"""`sensors` — attach any sensor to Jarvis without writing code for it.

There are three ways a reading gets in, and none of them need a Python file:

1. **MQTT discovery** — already handled by the `mqtt` integration, which
   speaks the Home Assistant discovery protocol that Zigbee2MQTT, Tasmota,
   ESPHome and Shelly all publish. Nothing here is needed for those.

2. **HTTP** — ``POST /api/sensor/<sensor_id>`` with ``{"state": ...}``,
   ``{"value": ...}`` or a bare value. An id nobody has ever heard of
   *auto-registers*: :mod:`.infer` reads the id and the payload, decides
   whether it is a ``sensor`` or a ``binary_sensor``, gives it a device
   class, a unit, a friendly name and an area, and the entity exists from
   then on. A new ESP32 needs zero server-side configuration.

3. **YAML** — for the sensors you want named and typed up front::

       sensors:
         token: !secret sensor_ingest_token   # optional shared ingest token
         allow_auto_register: true            # default
         expire_after: 0                      # default for every sensor
         sensors:
           - id: front_door_motion
             name: Front Door Motion
             domain: binary_sensor
             device_class: motion
             area: Front Porch
             narrate: "Motion detected at the front door"
             expire_after: 120
             token: !secret front_door_token  # this sensor only

   A bare list (``sensors: [ ... ]``) is accepted as shorthand for the
   ``sensors:`` key.

``expire_after`` is the honesty setting: a sensor that stops reporting goes
``unavailable`` rather than sitting there showing yesterday's number.

Services
    ``sensors.list``   every sensor, its entity, when it was last heard from
    ``sensors.set``    push a reading from an automation or script
    ``sensors.forget`` drop a sensor so a changed device can re-register

Security
    An ingest POST must carry a valid credential — the long-lived bearer
    token, the shared ``sensors.token``, or that sensor's own ``token``.
    There is no unauthenticated path: with nothing configured, everything is
    refused. Inference can only ever produce ``sensor`` or ``binary_sensor``,
    so a device cannot talk its way into becoming something the assistant is
    then able to *operate*, and auto-registration is capped so a broken or
    hostile poster cannot fill the registry.

Wiring the route
    This integration owns no HTTP routes — the API layer does. It publishes
    :func:`handle_sensor_post` at ``jarvis.data["sensor_ingest"]`` for that
    layer to mount at ``/api/sensor/{sensor_id}``. Until it does, the same
    handler is reachable through the existing webhook door:
    ``POST /api/webhook/sensor?sensor_id=<id>`` (auth is still enforced by
    the handler, not by the route). That door is a
    :class:`~jarvis.automation.triggers.WebhookHandler`, so an automation
    using the same webhook id joins it instead of replacing it.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from ...auth import extract_bearer_token, get_auth
from ...automation.triggers import WebhookHandler
from ...const import STATE_UNAVAILABLE, STATE_UNKNOWN
from ...entity import Entity, EntityPlatform
from ...services import ServiceCall
from .infer import (
    ALLOWED_DOMAINS,
    DROPPED_KEYS,
    AreaIndex,
    SensorSpec,
    humanize,
    infer,
    normalize_id,
    normalize_state,
    parse_payload,
    slug,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "sensors"

#: Where the API layer finds the ingest handler.
DATA_INGEST = "sensor_ingest"
DATA_WEBHOOKS = "webhooks"
#: Shared with `narrate`: entity_id -> {"message": str | None, ...}.
DATA_NARRATION_OVERRIDES = "narration_overrides"

#: The webhook id the fallback ingest door listens on.
FALLBACK_WEBHOOK_ID = "sensor"

SENSOR_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")

#: What an id posted over HTTP may look like *before* normalising. A URL
#: segment is attacker-chosen, and quietly slugifying ``../../etc/passwd``
#: into ``etc_passwd`` would register a sensor nobody asked for under a name
#: nobody typed. Config and service calls stay lenient; this door does not.
RAW_SENSOR_ID_RE = re.compile(r"^[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?$")

DEFAULT_MAX_SENSORS = 500
DEFAULT_EXPIRE_CHECK_INTERVAL = 10.0
MAX_ATTRIBUTES = 32
MAX_ATTRIBUTE_CHARS = 256
MAX_PAYLOAD_KEYS = 64


def _as_options(config: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split the YAML block into ``(options, declared sensors)``."""
    if config is None:
        return {}, []
    if isinstance(config, Mapping):
        options = {k: v for k, v in config.items() if k != "sensors"}
        declared = config.get("sensors")
        if isinstance(declared, Mapping):
            declared = [
                {**value, "id": value.get("id", key)}
                for key, value in declared.items()
                if isinstance(value, Mapping)
            ]
        return options, [d for d in (declared or []) if isinstance(d, Mapping)]
    if isinstance(config, (list, tuple)):
        return {}, [d for d in config if isinstance(d, Mapping)]
    return {}, []


def _clean_attributes(source: Mapping[str, Any] | None) -> dict[str, Any]:
    """Trim a posted attribute bag down to something safe to store."""
    out: dict[str, Any] = {}
    for key, value in list((source or {}).items())[:MAX_ATTRIBUTES]:
        name = slug(key)[:48]
        # `sensors.set` hands an attribute bag straight through from a service
        # call, so the credential filter has to bite here too and not only in
        # `parse_payload`. A secret must never reach the state machine.
        if not name or name in DROPPED_KEYS:
            continue
        if isinstance(value, str):
            value = "".join(ch for ch in value if ch.isprintable())[:MAX_ATTRIBUTE_CHARS]
        elif isinstance(value, (list, tuple)):
            value = [str(v)[:MAX_ATTRIBUTE_CHARS] for v in list(value)[:MAX_ATTRIBUTES]]
        elif isinstance(value, Mapping):
            value = {
                str(k)[:48]: str(v)[:MAX_ATTRIBUTE_CHARS]
                for k, v in list(value.items())[:MAX_ATTRIBUTES]
            }
        elif not isinstance(value, (int, float, bool)) and value is not None:
            value = str(value)[:MAX_ATTRIBUTE_CHARS]
        out[name] = value
    return out


# ---------------------------------------------------------------------------
# the entity
# ---------------------------------------------------------------------------
class IngestedSensor(Entity):
    """One sensor, however it arrived: YAML, HTTP or a service call."""

    def __init__(self, sensor_id: str, spec: SensorSpec, source: str = "yaml") -> None:
        self.sensor_id = sensor_id
        self.spec = spec
        self.source = source
        self._attr_unique_id = f"{DOMAIN}_{sensor_id}"
        self._attr_name = spec.name or humanize(sensor_id)
        self._attr_device_class = spec.device_class
        self._attr_unit_of_measurement = spec.unit
        self._attr_icon = spec.icon
        # Deliberately not `spec.state`: a sensor is born knowing nothing, and
        # its first reading is then a real transition. Writing the reading as
        # the entity's *initial* state would make it look like a startup write,
        # and narration (rightly) says nothing about those — so the first
        # motion a brand-new sensor ever reports would be silent.
        self._attr_state = STATE_UNKNOWN
        self._attr_available = True
        self._attr_should_poll = False
        self._base_attributes: dict[str, Any] = {"sensor_id": sensor_id, "source": source}
        self._attr_extra_attributes = dict(self._base_attributes)

    @property
    def domain(self) -> str:
        return self.spec.domain

    def apply(self, value: Any, attributes: Mapping[str, Any] | None = None) -> str:
        """Write a new reading. Returns the state that was stored."""
        state = normalize_state(value, self.spec.domain)
        self._attr_state = state
        self._attr_available = True
        self._attr_extra_attributes = {
            **self._base_attributes,
            **_clean_attributes(attributes),
        }
        self.async_write_state()
        return state

    def mark_unavailable(self) -> bool:
        """Stop pretending we know. Returns True if this changed anything."""
        if not self._attr_available:
            return False
        self._attr_available = False
        self.async_write_state()
        return True


@dataclass
class SensorRecord:
    """Bookkeeping for one sensor: identity, freshness, and its credential."""

    sensor_id: str
    entity_id: str
    entity: IngestedSensor
    spec: SensorSpec
    declared: bool = False
    source: str = "http"
    token: str | None = None
    expire_after: float = 0.0
    last_seen: float = 0.0
    created: float = field(default_factory=time.time)
    updates: int = 0

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        moment = time.time() if now is None else now
        return {
            "sensor_id": self.sensor_id,
            "entity_id": self.entity_id,
            "name": self.entity.name,
            "domain": self.spec.domain,
            "device_class": self.spec.device_class,
            "unit_of_measurement": self.spec.unit,
            "area_id": self.spec.area_id,
            "state": self.entity.state if self.entity.available else STATE_UNAVAILABLE,
            "available": self.entity.available,
            "declared": self.declared,
            "source": self.source,
            "has_token": bool(self.token),
            "expire_after": self.expire_after,
            "last_seen": self.last_seen or None,
            "seconds_since_seen": round(moment - self.last_seen, 3) if self.last_seen else None,
            "updates": self.updates,
            "inference": self.spec.reason,
        }


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------
class SensorManager:
    """Owns every ingested sensor: registration, readings, expiry, auth."""

    def __init__(self, jarvis: "Jarvis", options: Mapping[str, Any] | None = None) -> None:
        self.jarvis = jarvis
        options = dict(options or {})
        self.shared_token: str | None = _text_or_none(options.get("token"))
        self.allow_auto_register = options.get("allow_auto_register", True) is not False
        self.default_expire_after = _number(options.get("expire_after"), 0.0)
        self.max_sensors = int(_number(options.get("max_sensors"), DEFAULT_MAX_SENSORS))
        self.expire_check_interval = _number(
            options.get("expire_check_interval"), DEFAULT_EXPIRE_CHECK_INTERVAL
        )
        clock = options.get("clock")
        self.clock = clock if callable(clock) else time.time
        self.sensors: dict[str, SensorRecord] = {}
        self._platforms: dict[str, EntityPlatform] = {}
        self._expire_task: Any = None
        # Creating an entity awaits (registry writes, area assignment), and
        # HTTP posts arrive concurrently, so without this two posts for the
        # same unknown id both see "not registered" and both create one — and
        # `max_sensors` stops being a cap at all. Held only across creation.
        self._register_lock = asyncio.Lock()

    # --- helpers ----------------------------------------------------------
    def _platform(self, domain: str) -> EntityPlatform:
        platform = self._platforms.get(domain)
        if platform is None:
            platform = EntityPlatform(self.jarvis, domain, DOMAIN)
            self._platforms[domain] = platform
        return platform

    def area_index(self) -> AreaIndex:
        return AreaIndex.from_registry(self.jarvis.areas)

    def get(self, sensor_id: Any) -> SensorRecord | None:
        return self.sensors.get(normalize_id(sensor_id)[1])

    def list(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [record.as_dict(now) for record in sorted(
            self.sensors.values(), key=lambda r: r.entity_id
        )]

    # --- authorisation ----------------------------------------------------
    def authorize(self, sensor_id: str, token: Any) -> tuple[bool, str]:
        """Is this post allowed? Fails closed: no credential, no ingest."""
        secret = _text_or_none(token)
        if not secret:
            return False, "no credential presented"

        auth = get_auth(self.jarvis)
        if auth is not None and auth.verify(secret) is not None:
            return True, "bearer token"

        if self.shared_token and hmac.compare_digest(self.shared_token, secret):
            return True, "shared sensor token"

        record = self.sensors.get(sensor_id)
        if record is not None and record.token and hmac.compare_digest(record.token, secret):
            return True, "per-sensor token"

        return False, "credential not recognised"

    # --- registration -----------------------------------------------------
    async def async_declare(self, raw: Mapping[str, Any]) -> SensorRecord | None:
        """Create a sensor from its YAML declaration."""
        sensor_id = normalize_id(raw.get("id") or raw.get("sensor_id") or raw.get("name"))[1]
        if not SENSOR_ID_RE.match(sensor_id or ""):
            _LOGGER.warning("sensors: ignoring declaration with unusable id %r", raw.get("id"))
            return None

        hints = {
            key: raw[key]
            for key in ("name", "domain", "device_class", "unit", "unit_of_measurement", "area")
            if raw.get(key) not in (None, "")
        }
        area_name = raw.get("area")
        if area_name and self.jarvis.areas.get_by_name(str(area_name)) is None:
            # A declared area is the user asking for it, so make it.
            await self.jarvis.areas.create(str(area_name))

        spec = infer(sensor_id, None, hints, self.area_index())
        record = await self._async_create(spec, declared=True, source="yaml")
        record.token = _text_or_none(raw.get("token"))
        record.expire_after = _number(raw.get("expire_after"), self.default_expire_after)

        narrate = raw.get("narrate")
        if narrate not in (None, False):
            overrides = self.jarvis.data.setdefault(DATA_NARRATION_OVERRIDES, {})
            overrides[record.entity_id] = {
                "message": None if narrate is True else str(narrate),
                "importance": raw.get("importance"),
                # "Motion detected at the front door" is a sentence about the
                # motion starting, so a bare `narrate:` on a binary sensor
                # means the on-transition, not both edges.
                "on_state": raw.get("narrate_on") or (
                    "on" if spec.domain == "binary_sensor" else None
                ),
            }
        return record

    async def _async_create(
        self, spec: SensorSpec, declared: bool, source: str
    ) -> SensorRecord:
        # The same invariant as in `infer`, checked again at the only place an
        # entity is actually created. Ingest is the one door where an outside
        # device chooses a name and a payload; it must never be a door to an
        # actuator, and one guard for a property that important is one too few.
        if spec.domain not in ALLOWED_DOMAINS:
            raise ValueError(
                f"refusing to create a {spec.domain!r} entity from a sensor payload"
            )
        entity = IngestedSensor(spec.sensor_id, spec, source=source)
        platform = self._platform(spec.domain)
        await platform.async_add_entities([entity])
        if spec.area_id:
            await self.jarvis.entities.update(entity.entity_id, area_id=spec.area_id)
        record = SensorRecord(
            sensor_id=spec.sensor_id,
            entity_id=entity.entity_id,
            entity=entity,
            spec=spec,
            declared=declared,
            source=source,
            expire_after=self.default_expire_after,
        )
        self.sensors[spec.sensor_id] = record
        _LOGGER.info(
            "sensors: %s -> %s (%s)", spec.sensor_id, entity.entity_id, spec.reason
        )
        return record

    async def async_forget(self, sensor_id: Any) -> bool:
        record = self.get(sensor_id)
        if record is None:
            return False
        self.sensors.pop(record.sensor_id, None)
        await self._platform(record.spec.domain).async_remove_entity(record.entity_id)
        self.jarvis.data.get(DATA_NARRATION_OVERRIDES, {}).pop(record.entity_id, None)
        return True

    # --- ingest -----------------------------------------------------------
    async def async_ingest(
        self, sensor_id: Any, payload: Any = None, source: str = "http"
    ) -> dict[str, Any]:
        """Apply a reading, registering the sensor if it is new."""
        clean = normalize_id(sensor_id)[1]
        if not SENSOR_ID_RE.match(clean or ""):
            return _error(400, "bad_sensor_id", f"unusable sensor id {sensor_id!r}")
        if isinstance(payload, Mapping) and len(payload) > MAX_PAYLOAD_KEYS:
            return _error(400, "payload_too_large", "too many keys in the payload")

        parsed = parse_payload(payload, clean)
        record = self.sensors.get(clean)
        created = False

        if record is None:
            if not self.allow_auto_register:
                return _error(404, "unknown_sensor", f"no sensor called {clean!r}")
            async with self._register_lock:
                # Re-read under the lock: another post may have registered this
                # id while we were waiting for it.
                record = self.sensors.get(clean)
                if record is None:
                    if len(self.sensors) >= self.max_sensors:
                        return _error(
                            429, "too_many_sensors",
                            f"the auto-registration cap of {self.max_sensors} "
                            "sensors is full",
                        )
                    # The raw id, not the cleaned one: `binary_sensor.x` carries
                    # a domain hint the cleaned form has already thrown away.
                    spec = infer(sensor_id, parsed, None, self.area_index())
                    record = await self._async_create(
                        spec, declared=False, source=source
                    )
                    record.expire_after = self.default_expire_after
                    created = True

        state = record.entity.apply(parsed.value, parsed.attributes)
        record.last_seen = self.clock()
        record.updates += 1

        if record.expire_after > 0:
            self.ensure_expiry_loop()

        return {
            "ok": True,
            "status": 201 if created else 200,
            "sensor_id": record.sensor_id,
            "entity_id": record.entity_id,
            "state": state,
            "created": created,
            "domain": record.spec.domain,
            "device_class": record.spec.device_class,
            "unit_of_measurement": record.spec.unit,
            "area_id": record.spec.area_id,
            "last_seen": record.last_seen,
        }

    # --- expiry -----------------------------------------------------------
    def check_expired(self, now: float | None = None) -> list[str]:
        """Mark every silent sensor unavailable. Returns the ones that changed."""
        moment = self.clock() if now is None else now
        expired: list[str] = []
        for record in self.sensors.values():
            if record.expire_after <= 0 or not record.last_seen:
                continue
            if moment - record.last_seen <= record.expire_after:
                continue
            if record.entity.mark_unavailable():
                expired.append(record.entity_id)
                _LOGGER.info(
                    "sensors: %s has not reported for %.0fs; marking unavailable",
                    record.entity_id, moment - record.last_seen,
                )
        return expired

    def ensure_expiry_loop(self) -> None:
        if self._expire_task is not None or self.expire_check_interval <= 0:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(self.expire_check_interval)
                try:
                    self.check_expired()
                except Exception:  # pragma: no cover - never kill the loop
                    _LOGGER.exception("sensors: expiry sweep failed")

        try:
            self._expire_task = self.jarvis.async_create_task(_loop())
        except RuntimeError:  # pragma: no cover - no loop (sync test harness)
            self._expire_task = None

    def stop(self) -> None:
        if self._expire_task is not None:
            self._expire_task.cancel()
            self._expire_task = None


def _text_or_none(value: Any) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text or None


def _number(value: Any, default: float) -> float:
    """A YAML number, or the default. A typo must not fail the integration."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        _LOGGER.warning("sensors: %r is not a number; using %s", value, default)
        return default


def _error(status: int, code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "status": status, "error": code, "message": message}


# ---------------------------------------------------------------------------
# the ingest door
# ---------------------------------------------------------------------------
async def handle_sensor_post(
    jarvis: "Jarvis", sensor_id: Any, payload: Any = None, token: Any = None
) -> dict[str, Any]:
    """Handle ``POST /api/sensor/<sensor_id>``.

    Never raises for an expected failure. The returned dict always carries
    ``ok`` and ``status`` (an HTTP status code), so an API layer can do::

        result = await jarvis.data["sensor_ingest"](sensor_id, body, token)
        return JSONResponse(result, status_code=result["status"])

    ``token`` may be the raw secret or a whole ``Authorization`` header.
    """
    manager = jarvis.data.get(DOMAIN)
    if not isinstance(manager, SensorManager):
        return _error(503, "not_configured", "the sensors integration is not set up")

    raw = str(sensor_id or "").strip().lower()
    clean = normalize_id(raw)[1]
    if not RAW_SENSOR_ID_RE.match(raw) or not SENSOR_ID_RE.match(clean or ""):
        return _error(400, "bad_sensor_id", f"unusable sensor id {sensor_id!r}")

    secret = _text_or_none(token)
    if secret and secret.lower().startswith("bearer "):
        secret = extract_bearer_token(secret)

    allowed, why = manager.authorize(clean, secret)
    if not allowed:
        _LOGGER.warning("sensors: refused a post to %r (%s)", clean, why)
        return _error(401, "unauthorized", why)

    return await manager.async_ingest(raw, payload, source="http")


class SensorIngest:
    """The callable published at ``jarvis.data["sensor_ingest"]``.

    Call it — ``await ingest(sensor_id, payload, token)`` — or read ``path``
    and ``methods`` off it to mount a route.
    """

    path = "/api/sensor/{sensor_id}"
    methods = ("POST", "PUT")
    auth = "bearer token, shared sensors token, or the sensor's own token"

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.handle_sensor_post = handle_sensor_post

    async def __call__(
        self, sensor_id: Any, payload: Any = None, token: Any = None
    ) -> dict[str, Any]:
        return await handle_sensor_post(self.jarvis, sensor_id, payload, token)

    async def webhook(
        self,
        data: Any = None,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, Any] | None = None,
        method: str = "POST",
    ) -> int:
        """Fallback door: ``POST /api/webhook/sensor?sensor_id=<id>``.

        The webhook route is open by design (its id is the secret), so the
        credential check still happens here — an unauthenticated post writes
        nothing and reports zero deliveries.
        """
        query = dict(query or {})
        headers = {str(k).lower(): v for k, v in dict(headers or {}).items()}
        body = data if isinstance(data, Mapping) else {}

        sensor_id = (
            query.get("sensor_id")
            or query.get("id")
            or body.get("sensor_id")
            or body.get("id")
        )
        token = (
            extract_bearer_token(str(headers.get("authorization") or "") or None)
            or headers.get("x-sensor-token")
            or query.get("token")
            or body.get("token")
        )
        result = await handle_sensor_post(self.jarvis, sensor_id, data, token)
        return 1 if result.get("ok") else 0


class SensorWebhook(WebhookHandler):
    """The fallback ingest door, shaped so it can *share* its webhook id.

    ``jarvis.data["webhooks"]`` is not this integration's namespace — the
    automation layer writes there too, and
    :func:`jarvis.automation.triggers.async_attach_webhook` replaces anything
    it finds under an id that is not a :class:`WebhookHandler`. Registering a
    bare function meant an automation with ``webhook_id: sensor`` silently
    killed HTTP ingest, with no warning on either side.

    Being a ``WebhookHandler`` fixes both directions: an automation attaching
    to the same id joins this door instead of replacing it, and its detach
    cannot evict us, because our own callback keeps ``callbacks`` non-empty.
    """

    def __init__(self, ingest: "SensorIngest") -> None:
        super().__init__(FALLBACK_WEBHOOK_ID)
        self.ingest = ingest
        # Bound once and kept, because `self.method` makes a *new* object on
        # every attribute access and `__call__` has to recognise this entry by
        # identity. A real entry rather than a sentinel: it is what the
        # automation layer's detach counts when it decides whether to evict.
        self._slot = self._ingest_callback
        self.add(self._slot)

    async def _ingest_callback(
        self,
        data: Any = None,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, Any] | None = None,
        method: str = "POST",
    ) -> int:
        return await self.ingest.webhook(data, query, headers, method)

    async def __call__(
        self,
        data: Any = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        method: str = "POST",
    ) -> int:
        """Ingest, then fan out to anything else attached to the same id.

        The count reports the ingest's own verdict rather than "a callback
        ran", so a post with no valid credential still reads as zero.
        """
        delivered = 0
        for callback in list(self.callbacks):
            result = await callback(data, query, headers, method)
            if callback is self._slot:
                delivered += int(result or 0)
            else:
                delivered += result if isinstance(result, int) else 1
        return delivered


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options, declared = _as_options(config)
    manager = SensorManager(jarvis, options)
    jarvis.data[DOMAIN] = manager

    for raw in declared:
        try:
            await manager.async_declare(raw)
        except Exception:  # one bad declaration must not lose the rest
            _LOGGER.exception("sensors: could not declare %r", raw.get("id"))

    ingest = SensorIngest(jarvis)
    jarvis.data[DATA_INGEST] = ingest
    _register_tools(jarvis)

    webhooks = jarvis.data.setdefault(DATA_WEBHOOKS, {})
    existing = webhooks.get(FALLBACK_WEBHOOK_ID)
    if isinstance(existing, SensorWebhook):
        existing.ingest = ingest  # a reload keeps whatever else joined the id
    elif isinstance(existing, WebhookHandler):
        # An automation got here first. Take the id over rather than lose the
        # ingest door, and keep the automation's handler as one of ours — it
        # is callable with the same signature, and leaving it intact means its
        # own attach/detach bookkeeping still works.
        door = SensorWebhook(ingest)
        door.add(existing)
        webhooks[FALLBACK_WEBHOOK_ID] = door
        _LOGGER.info(
            "sensors: sharing webhook id %r with an automation trigger",
            FALLBACK_WEBHOOK_ID,
        )
    elif existing is not None:
        _LOGGER.warning(
            "sensors: webhook id %r is already taken; the fallback ingest door is off",
            FALLBACK_WEBHOOK_ID,
        )
    else:
        webhooks[FALLBACK_WEBHOOK_ID] = SensorWebhook(ingest)

    _register_services(jarvis, manager)
    jarvis.register_shutdown(manager.stop)

    if any(r.expire_after > 0 for r in manager.sensors.values()):
        manager.ensure_expiry_loop()

    _LOGGER.info(
        "sensors ready: %d declared, auto-registration %s",
        len(manager.sensors),
        "on" if manager.allow_auto_register else "off",
    )
    return True


def _register_services(jarvis: "Jarvis", manager: SensorManager) -> None:
    async def handle_list(call: ServiceCall) -> dict[str, Any]:
        sensors = manager.list()
        sensor_id = call.get("sensor_id")
        if sensor_id:
            clean = normalize_id(sensor_id)[1]
            sensors = [s for s in sensors if s["sensor_id"] == clean]
        return {"count": len(sensors), "sensors": sensors}

    jarvis.services.register(
        DOMAIN, "list", handle_list, supports_response=True,
        description="Every sensor Jarvis is ingesting, with freshness and inference.",
        fields={"sensor_id": {"description": "Limit to one sensor.", "required": False}},
    )

    async def handle_set(call: ServiceCall) -> dict[str, Any]:
        sensor_id = call.get("sensor_id") or call.get("id") or ""
        payload: dict[str, Any] = {}
        if "state" in call.data or "value" in call.data:
            payload["state"] = call.get("state", call.get("value"))
        for key in ("attributes", "name", "domain", "device_class", "unit", "area"):
            if call.get(key) is not None:
                payload[key] = call.get(key)
        return await manager.async_ingest(sensor_id, payload, source="service")

    jarvis.services.register(
        DOMAIN, "set", handle_set, supports_response=True,
        description="Push a sensor reading (auto-registers an unknown id).",
        fields={
            "sensor_id": {"description": "The sensor's id.", "required": True},
            "state": {"description": "The reading.", "required": False},
            "attributes": {"description": "Extra attributes.", "required": False},
        },
    )

    async def handle_forget(call: ServiceCall) -> dict[str, Any]:
        sensor_id = call.get("sensor_id") or call.get("id") or ""
        return {"sensor_id": str(sensor_id), "forgotten": await manager.async_forget(sensor_id)}

    jarvis.services.register(
        DOMAIN, "forget", handle_forget, supports_response=True,
        description="Drop a sensor so a changed device can register again.",
        fields={"sensor_id": {"description": "The sensor's id.", "required": True}},
    )

    async def handle_check_expired(call: ServiceCall) -> dict[str, Any]:
        expired = manager.check_expired()
        return {"expired": expired, "count": len(expired)}

    jarvis.services.register(
        DOMAIN, "check_expired", handle_check_expired, supports_response=True,
        description="Run the staleness sweep now (the loop does this for you).",
    )


__all__ = [
    "DATA_INGEST",
    "DOMAIN",
    "IngestedSensor",
    "SensorIngest",
    "SensorManager",
    "SensorRecord",
    "SensorWebhook",
    "async_setup",
    "handle_sensor_post",
]


# --- what the model can ask (M57) -------------------------------------------

#: Domains whose states are readings.
_READING_DOMAINS = ("sensor", "binary_sensor")


def _area_name(jarvis: "Jarvis", entity_id: str, attributes: dict[str, Any]) -> str:
    """The room a reading is in, from the registry or the attributes, or ''."""
    if attributes.get("area"):
        return str(attributes["area"])
    try:
        entry = jarvis.entities.get(entity_id)
        area_id = getattr(entry, "area_id", None) if entry is not None else None
        if area_id:
            area = jarvis.areas.get(area_id)
            return str(getattr(area, "name", "") or "")
    except Exception:  # noqa: BLE001 - a registry that is not there is not an error here
        pass
    return ""


def _readings(jarvis: "Jarvis") -> list[dict[str, Any]]:
    """Every reading the house has, as rows with a unit, a class, an age and a room."""
    now = time.time()
    rows: list[dict[str, Any]] = []
    for domain in _READING_DOMAINS:
        for state in jarvis.states.all(domain):
            attrs = dict(state.attributes or {})
            value: Any = state.state
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                pass
            rows.append({
                "entity_id": state.entity_id,
                "name": str(attrs.get("friendly_name") or state.entity_id),
                "value": value,
                "unit": attrs.get("unit_of_measurement") or "",
                "device_class": attrs.get("device_class") or "",
                "area": _area_name(jarvis, state.entity_id, attrs),
                "age_s": max(0, int(now - float(state.last_updated or now))),
                "available": state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
            })
    return rows


def _fmt(row: dict[str, Any]) -> str:
    unit = f" {row['unit']}" if row["unit"] else ""
    where = f" in the {row['area']}" if row["area"] else ""
    return f"{row['name']}{where}: {row['value']}{unit}"


def _window_seconds(window: Any, default: float = 24 * 3600) -> float:
    """'24h', '7d', '30m', 90 (seconds) → seconds."""
    if window is None or window == "":
        return default
    text = str(window).strip().lower()
    try:
        return float(text)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhdw])", text)
    if not m:
        return default
    n, unit = float(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}[unit]


def _register_tools(jarvis: "Jarvis") -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        return
    from ...llm.tools import TIER_DIRECT, schema_object

    async def tool_readings(args: dict[str, Any], context: Any = None) -> Any:
        rows = _readings(jarvis)
        area = str(args.get("area") or "").strip().lower()
        klass = str(args.get("device_class") or "").strip().lower()
        query = str(args.get("query") or "").strip().lower()
        if area:
            rows = [r for r in rows if area in r["area"].lower()]
        if klass:
            rows = [r for r in rows if r["device_class"].lower() == klass]
        if query:
            rows = [r for r in rows if query in r["name"].lower() or query in r["entity_id"].lower()]
        rows = [r for r in rows if r["available"]]
        limit = int(args.get("limit") or 25)
        shown = rows[:limit]
        spoken = (
            "; ".join(_fmt(r) for r in shown[:6]) if shown else "no readings match"
        )
        return {"count": len(rows), "readings": shown, "spoken": spoken}

    async def tool_compare(args: dict[str, Any], context: Any = None) -> Any:
        """Coldest / warmest / highest — across rooms, for one device class."""
        klass = str(args.get("metric") or args.get("device_class") or "temperature").strip().lower()
        rows = [r for r in _readings(jarvis) if r["device_class"].lower() == klass and isinstance(r["value"], float) and r["available"]]
        if not rows:
            return {"metric": klass, "rows": [], "spoken": f"no {klass} readings to compare"}
        rows.sort(key=lambda r: r["value"])
        lowest, highest = rows[0], rows[-1]
        return {
            "metric": klass,
            "lowest": lowest,
            "highest": highest,
            "rows": rows,
            "spoken": f"lowest {klass}: {_fmt(lowest)}; highest: {_fmt(highest)}",
        }

    async def tool_history(args: dict[str, Any], context: Any = None) -> Any:
        from ..history import get_stats

        entity_id = str(args.get("entity_id") or "").strip()
        if not entity_id:
            return {"error": "say which entity_id"}
        seconds = _window_seconds(args.get("window"), 24 * 3600)
        end = time.time()
        stats = await get_stats(jarvis, [entity_id], start=end - seconds, end=end)
        summary = stats.get(entity_id) or {"count": 0}
        state = jarvis.states.get(entity_id)
        unit = (state.attributes.get("unit_of_measurement") if state else "") or ""
        if not summary.get("count"):
            spoken = f"no history for {entity_id} in that window"
        else:
            spoken = (
                f"{entity_id} over the window: min {summary.get('min')}{unit}, "
                f"max {summary.get('max')}{unit}, mean {summary.get('mean')}{unit}, "
                f"now {summary.get('last', state.state if state else '?')}{unit}"
            )
        return {"entity_id": entity_id, "window_s": seconds, "stats": summary, "spoken": spoken}

    async def tool_summary(args: dict[str, Any], context: Any = None) -> Any:
        rows = [r for r in _readings(jarvis) if r["available"]]
        by_class: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_class.setdefault(r["device_class"] or "other", []).append(r)
        parts = []
        for klass, group in sorted(by_class.items()):
            nums = [g for g in group if isinstance(g["value"], float)]
            if klass == "temperature" and nums:
                nums.sort(key=lambda g: g["value"])
                parts.append(f"temperature {nums[0]['value']}–{nums[-1]['value']} {nums[0]['unit']} across {len(nums)} rooms")
            elif klass == "power" and nums:
                parts.append(f"power draw {round(sum(g['value'] for g in nums), 1)} W over {len(nums)} readings")
            else:
                parts.append(f"{len(group)} {klass} reading(s)")
        return {"count": len(rows), "by_class": {k: len(v) for k, v in by_class.items()}, "spoken": "; ".join(parts) or "no readings"}

    registry.register(
        name="sensor_readings",
        description=(
            "Current readings from every sensor in the house — temperature, humidity, power, "
            "air quality, whatever is attached — with units, the room and how old each is. "
            "Filter by area, device_class (temperature, humidity, power, energy, …) or a word "
            "from the name. Read this before answering a question about a reading."
        ),
        parameters=schema_object({
            "area": {"type": "string", "description": "a room name, or part of one"},
            "device_class": {"type": "string", "description": "temperature | humidity | power | energy | pressure | illuminance | battery | …"},
            "query": {"type": "string", "description": "a word from the sensor's name"},
            "limit": {"type": "integer", "description": "at most this many rows (default 25)"},
        }),
        handler=tool_readings,
        tier=TIER_DIRECT,
        domain=DOMAIN,
    )
    registry.register(
        name="sensor_compare",
        description="Which room is coldest, warmest, drawing the most power: the lowest and highest reading of one device class across the house.",
        parameters=schema_object({
            "metric": {"type": "string", "description": "the device class to compare: temperature, humidity, power, …"},
        }),
        handler=tool_compare,
        tier=TIER_DIRECT,
        domain=DOMAIN,
    )
    registry.register(
        name="sensor_history",
        description="One sensor over a window: min, max, mean, first and last. Window like '24h', '7d', '30m'.",
        parameters=schema_object({
            "entity_id": {"type": "string", "description": "the sensor, e.g. sensor.garage_temperature"},
            "window": {"type": "string", "description": "how far back: 1h, 24h, 7d (default 24h)"},
        }, required=["entity_id"]),
        handler=tool_history,
        tier=TIER_DIRECT,
        domain=DOMAIN,
    )
    registry.register(
        name="sensor_summary",
        description="The house at a glance: how many readings of each kind, the temperature spread across rooms, the total power draw.",
        parameters=schema_object({}),
        handler=tool_summary,
        tier=TIER_DIRECT,
        domain=DOMAIN,
    )
