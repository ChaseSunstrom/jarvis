# Writing an integration

An integration is a Python package under `jarvis/integrations/<name>/` that
exposes three names:

```python
DOMAIN = "acme"                   # the configuration.yaml key
DEPENDENCIES: list[str] = []      # integrations to set up before this one

async def async_setup(jarvis, config) -> bool: ...
```

That is the whole contract. The loader walks `configuration.yaml`, matches each
top-level key against the package names on disk, resolves `DEPENDENCIES`, and
calls `async_setup` with that key's block. Presence of the key is what enables
an integration; there is no registration file and no manifest.

Returning `False` (or raising) logs the failure and skips that integration.
Everything else keeps running — one broken integration must never take the
house down with it.

## What `config` contains

Exactly the YAML under your key, already parsed by the loader with `!secret`,
`!include` and packages resolved. Shape is whatever the user wrote:

```yaml
acme:                    # → config = {"host": "10.0.0.5", "poll": 30}
  host: 10.0.0.5
  poll: 30

acme:                    # → config = [{"host": ...}, {"host": ...}]
  - host: 10.0.0.5
  - host: 10.0.0.6

acme:                    # → config = None
```

Handle all three. Every integration in the tree starts by normalising:

```python
def _as_list(config: Any) -> list[dict[str, Any]]:
    if config is None:
        return []
    if isinstance(config, dict):
        return [config]
    return [c for c in config if isinstance(c, dict)]
```

## Entities

An entity is one thing with a state. Subclass `Entity`, set the `_attr_*`
fields, and implement the methods its domain defines.

An entity does **not** register services. The `domains` integration owns
`light.turn_on` for every light in the house and dispatches it to whichever
object sits behind the entity_id. So you write `async_turn_on`, and every
caller — REST, websocket, an automation, a voice command, an LLM tool — reaches
it through the same door.

The methods per domain:

| Domain | Methods |
|---|---|
| `light` `switch` `fan` `siren` | `async_turn_on(**kwargs)` · `async_turn_off(**kwargs)` · `async_toggle()` |
| `cover` | `async_open_cover()` · `async_close_cover()` · `async_stop_cover()` · `async_set_cover_position(position: int)` |
| `climate` | `async_set_temperature(temperature: float)` · `async_set_hvac_mode(hvac_mode: str)` · `async_set_fan_mode(fan_mode)` |
| `lock` | `async_lock()` · `async_unlock()` |
| `media_player` | `async_media_play/pause/stop()` · `async_media_next_track()` · `async_media_previous_track()` · `async_volume_set(volume_level: float)` · `async_play_media(media_type, media_id)` |
| `number` `text` | `async_set_value(value)` |
| `select` | `async_select_option(option: str)` |
| `button` | `async_press()` |
| `vacuum` | `async_start()` · `async_return_to_base()` |

`light.turn_on` may arrive with `brightness` (0–255), `color_temp_kelvin`,
`rgb_color` (a tuple) and `transition`. Accept `**kwargs` and ignore what your
hardware cannot do.

**A device that cannot do something simply does not define the method.** Do not
write a stub that swallows the call — the domains layer detects the missing
method and returns a clear error, which is what the user (or the model) needs
to hear. A silent no-op is a bug report six months later.

### Attributes

| Field | Meaning |
|---|---|
| `_attr_name` | Friendly name. Also seeds the entity_id, so "Kitchen Lights" becomes `light.kitchen_lights`. |
| `_attr_unique_id` | Stable across restarts and renames. This is the key the entity registry uses to remember the entity_id, area and user's rename — get it from the hardware (MAC, serial, bridge id), never from the name. |
| `_attr_state` | The state. Written by `async_write_state()`. |
| `_attr_available` | `False` publishes `unavailable` regardless of state. |
| `_attr_extra_attributes` | Extra attributes dict, merged over the standard ones. |
| `_attr_device_class` `_attr_unit_of_measurement` `_attr_icon` `_attr_supported_features` | Standard attributes, omitted when unset. |
| `_attr_device_info` | `{identifiers, name, manufacturer, model, sw_version}`. Creates a device entry and links this entity to it, so several entities from one physical box group together and move areas together. |
| `_attr_should_poll` | `True` makes the platform call `async_update()` on a timer. Leave it `False` for anything push-based. |

Two lifecycle hooks: `async_added_to_jarvis()` runs once the entity has an
entity_id and is on the bus (subscribe to things here), and
`async_will_remove()` runs before removal.

Call `async_write_state()` whenever your state changes. It is synchronous and
cheap; the bus does the rest.

## A complete integration

A ventilation unit on the LAN with a small JSON API. It has a fan you can set
to low/medium/high, a filter-life sensor, and it does not push — so we poll.

`jarvis/integrations/acme/__init__.py`:

```python
"""Acme ventilation units — fan speed and filter life over HTTP.

    acme:
      - host: 192.168.1.42
        name: Loft MVHR       # optional; the unit reports its own otherwise
        scan_interval: 30

Tests inject transport with:

    jarvis.data["acme"] = {"transport": httpx.MockTransport(handler)}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ...const import STATE_OFF, STATE_ON
from ...entity import Entity, EntityPlatform

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "acme"
DEPENDENCIES: list[str] = []

DEFAULT_SCAN_INTERVAL = 30.0
SPEEDS = {"off": 0, "low": 33, "medium": 66, "high": 100}


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    entries = [config] if isinstance(config, dict) else list(config or [])
    if not entries:
        return True

    # Transport injection: tests preset jarvis.data["acme"]["transport"] with an
    # httpx.MockTransport, so the whole integration is testable with no network.
    store = jarvis.data.setdefault(DOMAIN, {})
    client = httpx.AsyncClient(
        transport=store.get("transport"),
        timeout=httpx.Timeout(10.0),
    )
    store["client"] = client
    jarvis.register_shutdown(client.aclose)

    fans = EntityPlatform(jarvis, "fan", DOMAIN, scan_interval=DEFAULT_SCAN_INTERVAL)
    sensors = EntityPlatform(jarvis, "sensor", DOMAIN, scan_interval=DEFAULT_SCAN_INTERVAL)
    store["platforms"] = {"fan": fans, "sensor": sensors}

    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("host"):
            _LOGGER.warning("acme: skipping entry without a host: %r", entry)
            continue
        host = str(entry["host"])

        # Identity comes from the device, not the config: renaming the unit in
        # YAML must not orphan its history or its area assignment.
        try:
            info = (await client.get(f"http://{host}/api/info")).json()
        except Exception as err:
            _LOGGER.warning("acme: %s is not answering (%s); skipping", host, err)
            continue

        serial = str(info.get("serial") or host)
        name = str(entry.get("name") or info.get("name") or f"Acme {serial[-4:]}")
        device_info = {
            "identifiers": [f"acme:{serial}"],
            "name": name,
            "manufacturer": "Acme",
            "model": info.get("model"),
            "sw_version": info.get("firmware"),
        }

        # update_before_add=True so the first published state is real rather
        # than `unknown` flickering to a value a scan interval later.
        await fans.async_add_entities(
            [AcmeFan(client, host, serial, name, device_info)], update_before_add=True
        )
        await sensors.async_add_entities(
            [AcmeFilterSensor(client, host, serial, name, device_info)],
            update_before_add=True,
        )

    async def _shutdown() -> None:
        await fans.async_shutdown()
        await sensors.async_shutdown()

    jarvis.register_shutdown(_shutdown)
    return True


class AcmeEntity(Entity):
    """Shared plumbing: HTTP client, identity, availability."""

    _attr_should_poll = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        host: str,
        serial: str,
        name: str,
        device_info: dict[str, Any],
    ) -> None:
        self._client = client
        self._host = host
        self._serial = serial
        self._attr_name = name
        self._attr_device_info = device_info

    async def _get(self, path: str) -> dict[str, Any]:
        response = await self._client.get(f"http://{self._host}{path}")
        response.raise_for_status()
        return response.json()


class AcmeFan(AcmeEntity):
    _attr_state = STATE_OFF

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._attr_unique_id = f"acme_{self._serial}_fan"
        self._speed = "off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"speed": self._speed, "percentage": SPEEDS[self._speed]}

    async def async_update(self) -> None:
        # Raising here is correct: async_update_state catches it, marks the
        # entity unavailable and logs once. Do not swallow errors into a
        # plausible-looking state.
        data = await self._get("/api/state")
        self._speed = str(data.get("speed", "off"))
        self._attr_state = STATE_OFF if self._speed == "off" else STATE_ON

    async def _set_speed(self, speed: str) -> None:
        await self._client.post(f"http://{self._host}/api/speed", json={"speed": speed})
        self._speed = speed
        self._attr_state = STATE_OFF if speed == "off" else STATE_ON
        # Write immediately rather than waiting for the next poll — the user
        # just pressed a button and expects the UI to move now.
        self.async_write_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        percentage = kwargs.get("percentage")
        if percentage is None:
            speed = "medium"
        else:
            speed = min(SPEEDS, key=lambda s: abs(SPEEDS[s] - int(percentage)))
        await self._set_speed(speed)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_speed("off")

    async def async_toggle(self) -> None:
        if self._speed == "off":
            await self.async_turn_on()
        else:
            await self.async_turn_off()


class AcmeFilterSensor(AcmeEntity):
    _attr_unit_of_measurement = "%"
    _attr_icon = "mdi:air-filter"

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._attr_unique_id = f"acme_{self._serial}_filter"
        self._attr_name = f"{self._attr_name} Filter Life"

    async def async_update(self) -> None:
        data = await self._get("/api/state")
        self._attr_state = int(data.get("filter_remaining_pct", 0))
```

Add `acme:` to `configuration.yaml` and restart. `fan.loft_mvhr` and
`sensor.loft_mvhr_filter_life` exist, `fan.turn_on` works, they are grouped
under one device, and history is recorded — with no service registration and no
API code of your own.

## Registering services

Entities cover devices. A service covers a verb that is not tied to one entity
— "run this query", "recalculate that". Register in `async_setup`:

```python
from ...services import ServiceCall

async def handle_recalibrate(call: ServiceCall) -> dict[str, Any]:
    target = call.get("entity_id")
    ...
    return {"calibrated": target, "offset": offset}

jarvis.services.register(
    DOMAIN,
    "recalibrate",
    handle_recalibrate,
    description="Re-run the sensor calibration routine.",
    fields={
        "entity_id": {"required": True, "example": "sensor.loft_mvhr_filter_life"},
        "reference": {"description": "Known-good value.", "example": 100},
    },
    supports_response=True,
)
```

`description` and `fields` are not decoration. They are what the API publishes
and what the LLM tool layer reads to decide whether it can call this and with
what arguments. `supports_response=True` means the return value comes back to
the caller, so a model can use the result instead of guessing.

## The bus

```python
unsub = jarvis.bus.listen("state_changed", callback)      # sync or async
jarvis.bus.listen_once("jarvis_start", callback)
jarvis.bus.fire("acme_filter_due", {"entity_id": ...}, context)
await jarvis.bus.async_fire("acme_filter_due", {...})     # awaits listeners
```

Listeners registered with `listen` run synchronously inside `fire`, so keep
them cheap — queue the real work with `jarvis.async_create_task()`. Pass the
`context` you were given through to whatever you fire or call, so a chain of
"phone → service → automation → light" stays traceable to the token that
started it.

`register_shutdown(callback)` runs your teardown on stop, in reverse
registration order. Close sockets and clients there; the process may be stopped
by `docker compose stop` at any moment.

## Testing

Tests run in CI with no network, no broker, no Ollama and no hardware. Inject
the transport rather than reaching out — the pattern every integration in the
tree uses:

```python
import httpx
from jarvis.core import Jarvis
from jarvis.integrations.acme import async_setup


async def test_fan_turns_on(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/info":
            return httpx.Response(200, json={"serial": "AC-1", "name": "Loft MVHR"})
        if request.url.path == "/api/state":
            return httpx.Response(200, json={"speed": "off", "filter_remaining_pct": 82})
        return httpx.Response(200, json={"ok": True})

    jarvis = Jarvis(tmp_path)
    jarvis.data["acme"] = {"transport": httpx.MockTransport(handler)}
    await jarvis.async_setup({"acme": [{"host": "10.0.0.9"}]})

    assert jarvis.states.get("fan.loft_mvhr").state == "off"
    await jarvis.async_call_service("fan", "turn_on", {"entity_id": "fan.loft_mvhr"})
    assert jarvis.states.get("fan.loft_mvhr").state == "on"
    await jarvis.async_stop()
```

`pytest.ini` sets `asyncio_mode = auto`, so `async def test_x()` needs no
decorator. Other injection points already wired up: `jarvis.data["mqtt"]` for a
fake MQTT client, `jarvis.data["voice_stt_client"]` / `["voice_tts_client"]` /
`["voice_wake_client"]` for Wyoming, and `jarvis.data["llm_transport"]` for
Ollama.

## Before you write Python

Check that you need to. A device with an HTTP API is a `rest:` block; one that
speaks MQTT is a `mqtt:` block or nothing at all if it publishes HA-format
discovery; one you can poke from a shell is `command_line:`; a value derived
from other entities is `template:`. See
[configuration.md](configuration.md). Python earns its place when there is a
real protocol, a push connection, or state to keep between calls.
