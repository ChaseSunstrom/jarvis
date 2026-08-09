"""Logbook — the house's plain-English activity feed.

Turns raw state changes and service calls into lines a person (or an LLM
summarising "what happened while I was out") can read:

    Kitchen Light turned on
    Front Door was opened
    Chris is at home
    Bedtime routine started

Recent entries live in a bounded in-memory ring buffer, so the logbook
works with no database at all. When ``recorder`` is present, older entries
are reconstructed on demand from recorded state rows, and explicit
``logbook.log`` calls are persisted as ``logbook_entry`` events — the
buffer and the database are merged and de-duplicated at query time.

Configuration::

    logbook:
      max_entries: 5000
      log_service_calls: true
      log_unavailable: false
      exclude:
        domains: [sensor]
        entities: [binary_sensor.chatty]
      include:
        domains: [light, lock, person]

Services
    ``logbook.log`` (name, message, entity_id, domain)
    ``logbook.get`` (start, end, entity_id, limit) → ``{"entries": [...]}``
    ``logbook.clear`` — empties the in-memory buffer.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Iterable

from ...const import (
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
    STATE_CLOSED,
    STATE_HOME,
    STATE_LOCKED,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    STATE_UNLOCKED,
)
from ...services import ServiceCall
from ...state import split_entity_id
from ..recorder import EntityFilter, as_iso, as_timestamp, get_recorder

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "logbook"

EVENT_LOGBOOK_ENTRY = "logbook_entry"

DEFAULT_MAX_ENTRIES = 5000

# Plumbing domains whose service calls are noise in an activity feed.
DEFAULT_EXCLUDED_SERVICE_DOMAINS = frozenset(
    {
        DOMAIN,
        "recorder",
        "history",
        "homeassistant_compat",
        "persistent_notification",
        "system_log",
        "template",
        "voice",
        "llm",
        "conversation",
    }
)

ON_OFF_DOMAINS = frozenset(
    {"light", "switch", "fan", "siren", "input_boolean", "humidifier", "remote"}
)

# binary_sensor device classes read better with their own verbs.
BINARY_SENSOR_PHRASES: dict[str, tuple[str, str]] = {
    "motion": ("detected motion", "stopped detecting motion"),
    "occupancy": ("detected occupancy", "cleared"),
    "presence": ("is home", "is away"),
    "door": ("was opened", "was closed"),
    "garage_door": ("was opened", "was closed"),
    "window": ("was opened", "was closed"),
    "opening": ("was opened", "was closed"),
    "smoke": ("detected smoke", "cleared"),
    "gas": ("detected gas", "cleared"),
    "moisture": ("detected moisture", "dried"),
    "problem": ("reported a problem", "cleared"),
    "sound": ("detected sound", "stopped detecting sound"),
    "connectivity": ("connected", "disconnected"),
    "battery": ("reported low battery", "battery is normal"),
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _state_of(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get("state")
    return getattr(obj, "state", None)


def _attrs_of(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj.get("attributes") or {}
    return getattr(obj, "attributes", {}) or {}


def _friendly_name(entity_id: str, attributes: dict[str, Any]) -> str:
    name = attributes.get("friendly_name")
    if name:
        return str(name)
    return split_entity_id(entity_id)[1].replace("_", " ").title()


def describe_state_change(entity_id: str, old_state: Any, new_state: Any) -> str | None:
    """One sentence fragment for a transition, or None if not worth logging."""
    new = _state_of(new_state)
    old = _state_of(old_state)
    if new is None or new == old:
        return None

    domain = split_entity_id(entity_id)[0]
    attributes = _attrs_of(new_state)

    if new == STATE_UNAVAILABLE:
        return "became unavailable"
    if new == STATE_UNKNOWN:
        return None
    if old == STATE_UNAVAILABLE:
        return f"came back as {new}"

    if domain in ON_OFF_DOMAINS:
        if new == STATE_ON:
            return "turned on"
        if new == STATE_OFF:
            return "turned off"
    if domain == "binary_sensor":
        device_class = str(attributes.get("device_class") or "")
        on_phrase, off_phrase = BINARY_SENSOR_PHRASES.get(
            device_class, ("turned on", "turned off")
        )
        if new == STATE_ON:
            return on_phrase
        if new == STATE_OFF:
            return off_phrase
    if domain == "cover":
        if new == STATE_OPEN:
            return "was opened"
        if new == STATE_CLOSED:
            return "was closed"
        return f"is {new}"
    if domain == "lock":
        if new == STATE_LOCKED:
            return "was locked"
        if new == STATE_UNLOCKED:
            return "was unlocked"
    if domain in ("person", "device_tracker"):
        if new == STATE_HOME:
            return "is at home"
        if new == STATE_NOT_HOME:
            return "is away"
        return f"is at {new}"
    if domain == "media_player":
        title = attributes.get("media_title")
        if new == STATE_PLAYING:
            return f"is playing {title}" if title else "started playing"
        if new == STATE_PAUSED:
            return "was paused"
        if new == STATE_OFF:
            return "turned off"
        if new == STATE_ON:
            return "turned on"
        return f"is {new}"
    if domain == "automation":
        return "was turned on" if new == STATE_ON else "was turned off"
    if domain == "script":
        return "started" if new == STATE_ON else "finished"
    if domain == "climate":
        return f"was set to {new}"

    unit = attributes.get("unit_of_measurement")
    if unit:
        return f"changed to {new} {unit}"
    return f"changed to {new}"


def _entry(
    when: float,
    name: str,
    message: str,
    entity_id: str | None = None,
    domain: str | None = None,
    state: str | None = None,
    context_id: str | None = None,
    source: str = "state",
) -> dict[str, Any]:
    return {
        "when": when,
        "when_iso": as_iso(when),
        "name": name,
        "message": message,
        "entity_id": entity_id,
        "domain": domain,
        "state": state,
        "context_id": context_id,
        "source": source,
    }


def _dedupe_key(entry: dict[str, Any]) -> tuple:
    """Identity of a logbook entry, for merging the buffer with the database.

    The timestamp is used at full precision on purpose. Both sources derive
    it from the same float (``State.last_updated``, which round-trips
    exactly through SQLite's REAL), so rounding buys nothing — and rounding
    to 10 ms silently merged genuinely distinct changes whenever an entity
    moved more than once inside the same bucket, which is exactly what a
    scene or a script does.
    """
    return (
        float(entry.get("when") or 0.0),
        entry.get("entity_id"),
        entry.get("name"),
        entry.get("message"),
        entry.get("state"),
    )


class Logbook:
    """Ring buffer of readable entries + the query/merge logic."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.jarvis = jarvis
        self.config = config
        self.max_entries = int(config.get("max_entries", DEFAULT_MAX_ENTRIES))
        self.log_service_calls = bool(config.get("log_service_calls", True))
        self.log_unavailable = bool(config.get("log_unavailable", False))
        self.filter = EntityFilter(config.get("include"), config.get("exclude"))
        self.excluded_service_domains = set(
            DEFAULT_EXCLUDED_SERVICE_DOMAINS
        ) | set(_as_list((config.get("exclude") or {}).get("service_domains")))
        self.entries: deque[dict[str, Any]] = deque(maxlen=self.max_entries)
        self._unsubs: list[Any] = []

    # --- lifecycle ----------------------------------------------------
    async def async_setup(self) -> None:
        self._unsubs.append(
            self.jarvis.bus.listen(EVENT_STATE_CHANGED, self._handle_state_changed)
        )
        if self.log_service_calls:
            self._unsubs.append(
                self.jarvis.bus.listen(EVENT_CALL_SERVICE, self._handle_service_call)
            )
        self.jarvis.register_shutdown(self.async_shutdown)

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # --- capture ------------------------------------------------------
    def _handle_state_changed(self, event: Any) -> None:
        try:
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            entity_id = getattr(new_state, "entity_id", None) or event.data.get(
                "entity_id"
            )
            if not entity_id:
                return
            entry = self._entry_for_state(
                entity_id, event.data.get("old_state"), new_state
            )
            if entry is not None:
                self.entries.append(entry)
        except Exception:
            _LOGGER.exception("Logbook failed on state change")

    def _entry_for_state(
        self, entity_id: str, old_state: Any, new_state: Any
    ) -> dict[str, Any] | None:
        if not self.filter(entity_id):
            return None
        attributes = _attrs_of(new_state)
        # Continuous sensors would drown everything else out.
        if split_entity_id(entity_id)[0] == "sensor" and attributes.get(
            "unit_of_measurement"
        ):
            if entity_id.lower() not in self.filter.include_entities:
                return None
        state = _state_of(new_state)
        if state == STATE_UNAVAILABLE and not self.log_unavailable:
            return None
        message = describe_state_change(entity_id, old_state, new_state)
        if message is None:
            return None
        when = float(
            getattr(new_state, "last_updated", None)
            or (new_state.get("last_updated") if isinstance(new_state, dict) else None)
            or time.time()
        )
        context = getattr(new_state, "context", None)
        return _entry(
            when=when,
            name=_friendly_name(entity_id, attributes),
            message=message,
            entity_id=entity_id,
            domain=split_entity_id(entity_id)[0],
            state=state,
            context_id=getattr(context, "id", None),
            source="state",
        )

    def _handle_service_call(self, event: Any) -> None:
        try:
            domain = event.data.get("domain")
            service = event.data.get("service")
            if not domain or domain in self.excluded_service_domains:
                return
            data = event.data.get("service_data") or {}
            requested = _as_list(data.get("entity_id"))
            targets = [t for t in requested if self.filter(t)]
            if requested and not targets:
                # Every target of this call is filtered out of the feed, so
                # the call itself has nothing to say here either.
                return
            context = getattr(event, "context", None)
            origin = getattr(context, "origin", "internal")
            name = {"llm": "Assistant", "automation": "Automation", "api": "API"}.get(
                origin, "Jarvis"
            )
            target_text = f" ({', '.join(targets)})" if targets else ""
            self.entries.append(
                _entry(
                    when=float(event.time_fired),
                    name=name,
                    message=f"called {domain}.{service}{target_text}",
                    entity_id=targets[0] if len(targets) == 1 else None,
                    domain=domain,
                    context_id=getattr(context, "id", None),
                    source="service",
                )
            )
        except Exception:
            _LOGGER.exception("Logbook failed on service call")

    # --- writing ------------------------------------------------------
    async def async_log(
        self,
        message: str,
        name: str = "Jarvis",
        entity_id: str | None = None,
        domain: str | None = None,
        context: Any = None,
    ) -> dict[str, Any]:
        """Add a manual entry (also persisted via the recorder, if any)."""
        entry = _entry(
            when=time.time(),
            name=name,
            message=message,
            entity_id=entity_id,
            domain=domain or (split_entity_id(entity_id)[0] if entity_id else None),
            context_id=getattr(context, "id", None),
            source="user",
        )
        self.entries.append(entry)
        # Recorded as an event so it survives a restart.
        self.jarvis.bus.fire(
            EVENT_LOGBOOK_ENTRY,
            {
                "name": entry["name"],
                "message": entry["message"],
                "entity_id": entry["entity_id"],
                "domain": entry["domain"],
                "when": entry["when"],
            },
            context,
        )
        return entry

    # --- reading ------------------------------------------------------
    async def async_get(
        self,
        start: Any = None,
        end: Any = None,
        entity_ids: Iterable[str] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Merged entries in ``[start, end]``, oldest first."""
        end_ts = as_timestamp(end, time.time()) or time.time()
        start_ts = as_timestamp(start, end_ts - 86400) or 0.0
        wanted = {e.lower() for e in _as_list(entity_ids)}

        collected: dict[tuple, dict[str, Any]] = {}

        def add(entry: dict[str, Any]) -> None:
            when = float(entry.get("when") or 0.0)
            if when < start_ts or when > end_ts:
                return
            if wanted and (entry.get("entity_id") or "").lower() not in wanted:
                return
            collected.setdefault(_dedupe_key(entry), entry)

        for entry in self.entries:
            add(entry)

        recorder = get_recorder(self.jarvis)
        if recorder is not None:
            try:
                for entry in await self._from_recorder(
                    recorder, start_ts, end_ts, sorted(wanted)
                ):
                    add(entry)
            except Exception:
                _LOGGER.exception("Logbook could not read recorded history")

        entries = sorted(collected.values(), key=lambda e: e["when"])
        if limit:
            entries = entries[-int(limit) :]
        return entries

    async def _from_recorder(
        self,
        recorder: Any,
        start_ts: float,
        end_ts: float,
        entity_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Rebuild entries from recorded rows (pairs consecutive states)."""
        out: list[dict[str, Any]] = []
        for row in await recorder.events_between(
            start_ts, end_ts, [EVENT_LOGBOOK_ENTRY]
        ):
            data = row.get("data") or {}
            out.append(
                _entry(
                    when=float(data.get("when") or row["time_fired"]),
                    name=str(data.get("name") or "Jarvis"),
                    message=str(data.get("message") or ""),
                    entity_id=data.get("entity_id"),
                    domain=data.get("domain"),
                    context_id=row.get("context_id"),
                    source="user",
                )
            )

        rows = await recorder.states_between(entity_ids or None, start_ts, end_ts)
        previous: dict[str, dict[str, Any]] = {}
        if entity_ids:
            previous = await recorder.last_state_before(entity_ids, start_ts)
        for row in rows:
            entity_id = row["entity_id"]
            entry = self._entry_for_state(entity_id, previous.get(entity_id), row)
            previous[entity_id] = row
            if entry is not None:
                out.append(entry)
        return out


def get_logbook(jarvis: "Jarvis") -> Logbook | None:
    return jarvis.data.get(DOMAIN)


async def async_log(
    jarvis: "Jarvis",
    message: str,
    name: str = "Jarvis",
    entity_id: str | None = None,
) -> dict[str, Any] | None:
    """Convenience for other integrations (no-op if logbook is disabled)."""
    logbook = get_logbook(jarvis)
    if logbook is None:
        return None
    return await logbook.async_log(message, name=name, entity_id=entity_id)


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if not isinstance(config, dict):
        config = {}

    logbook = Logbook(jarvis, config)
    await logbook.async_setup()
    jarvis.data[DOMAIN] = logbook

    async def handle_log(call: ServiceCall) -> dict[str, Any]:
        message = str(call.get("message", ""))
        if not message:
            return {"logged": False, "error": "message is required"}
        entry = await logbook.async_log(
            message,
            name=str(call.get("name", "Jarvis")),
            entity_id=call.get("entity_id"),
            domain=call.get("domain"),
            context=call.context,
        )
        return {"logged": True, "entry": entry}

    async def handle_get(call: ServiceCall) -> dict[str, Any]:
        entries = await logbook.async_get(
            start=call.get("start"),
            end=call.get("end"),
            entity_ids=call.get("entity_id"),
            limit=call.get("limit"),
        )
        return {"entries": entries, "count": len(entries)}

    async def handle_clear(call: ServiceCall) -> dict[str, Any]:
        removed = len(logbook.entries)
        logbook.entries.clear()
        return {"cleared": removed}

    jarvis.services.register(
        DOMAIN,
        "log",
        handle_log,
        description="Add a custom entry to the logbook.",
        fields={
            "message": {"description": "What happened.", "example": "Dishwasher finished"},
            "name": {"description": "Who/what it is about.", "example": "Dishwasher"},
            "entity_id": {"description": "Optional entity this relates to."},
            "domain": {"description": "Optional domain label."},
        },
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN,
        "get",
        handle_get,
        description="Read logbook entries for a time window.",
        fields={
            "start": {"description": "Window start (ISO 8601 or epoch seconds)."},
            "end": {"description": "Window end (ISO 8601 or epoch seconds)."},
            "entity_id": {"description": "Limit to one or more entities."},
            "limit": {"description": "Return at most this many (most recent) entries."},
        },
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN,
        "clear",
        handle_clear,
        description="Empty the in-memory logbook buffer.",
        supports_response=True,
    )
    return True
