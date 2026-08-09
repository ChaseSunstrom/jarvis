"""Core contract tests: bus, state machine, services, registries, config."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.bus import Context, EventBus  # noqa: E402
from jarvis.config import ConfigError, load_config  # noqa: E402
from jarvis.const import EVENT_STATE_CHANGED  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.entity import Entity, EntityPlatform  # noqa: E402
from jarvis.services import ServiceCall, ServiceNotFound  # noqa: E402
from jarvis.state import StateMachine, slugify, valid_entity_id  # noqa: E402


# --- bus -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bus_sync_and_async_listeners():
    bus = EventBus()
    seen = []
    bus.listen("ping", lambda e: seen.append(("sync", e.data["n"])))

    async def async_listener(event):
        seen.append(("async", event.data["n"]))

    bus.listen("ping", async_listener)
    await bus.async_fire("ping", {"n": 1})
    assert ("sync", 1) in seen and ("async", 1) in seen


@pytest.mark.asyncio
async def test_bus_unsubscribe_and_once():
    bus = EventBus()
    hits = []
    unsub = bus.listen("x", lambda e: hits.append(1))
    await bus.async_fire("x")
    unsub()
    await bus.async_fire("x")
    assert len(hits) == 1

    once = []
    bus.listen_once("y", lambda e: once.append(1))
    await bus.async_fire("y")
    await bus.async_fire("y")
    assert len(once) == 1


@pytest.mark.asyncio
async def test_bus_listener_exception_does_not_break_others():
    bus = EventBus()
    ok = []

    def boom(event):
        raise RuntimeError("bad listener")

    bus.listen("e", boom)
    bus.listen("e", lambda e: ok.append(1))
    await bus.async_fire("e")
    assert ok == [1]


@pytest.mark.asyncio
async def test_bus_match_all():
    bus = EventBus()
    seen = []
    bus.listen("*", lambda e: seen.append(e.event_type))
    await bus.async_fire("a")
    await bus.async_fire("b")
    assert seen == ["a", "b"]


# --- state machine ---------------------------------------------------------
def test_entity_id_validation_and_slugify():
    assert valid_entity_id("light.kitchen")
    assert not valid_entity_id("Light.Kitchen")
    assert not valid_entity_id("nodomain")
    assert slugify("Kitchen Ceiling Light!") == "kitchen_ceiling_light"
    assert slugify("   ") == "unnamed"


@pytest.mark.asyncio
async def test_state_set_get_and_event():
    bus = EventBus()
    states = StateMachine(bus)
    changes = []
    bus.listen(EVENT_STATE_CHANGED, lambda e: changes.append(e.data))

    states.set("light.kitchen", "on", {"brightness": 128})
    await bus.async_block_till_done()
    state = states.get("light.kitchen")
    assert state.state == "on"
    assert state.attributes["brightness"] == 128
    assert state.domain == "light"
    assert len(changes) == 1
    assert changes[0]["old_state"] is None

    # identical set → no event
    states.set("light.kitchen", "on", {"brightness": 128})
    assert len(changes) == 1

    # attribute change → event, last_changed preserved
    first_changed = state.last_changed
    states.set("light.kitchen", "on", {"brightness": 255})
    assert len(changes) == 2
    assert states.get("light.kitchen").last_changed == first_changed


@pytest.mark.asyncio
async def test_state_force_update_and_remove():
    bus = EventBus()
    states = StateMachine(bus)
    events = []
    bus.listen(EVENT_STATE_CHANGED, lambda e: events.append(e))

    states.set("sensor.t", "20")
    states.set("sensor.t", "20", force_update=True)
    assert len(events) == 2

    assert states.remove("sensor.t") is True
    assert states.get("sensor.t") is None
    assert states.remove("sensor.t") is False
    assert events[-1].data["new_state"] is None


def test_state_rejects_bad_entity_id():
    states = StateMachine(EventBus())
    with pytest.raises(ValueError):
        states.set("bogus", "on")


def test_state_all_and_domains():
    states = StateMachine(EventBus())
    states.set("light.a", "on")
    states.set("light.b", "off")
    states.set("sensor.c", "5")
    assert len(states.all("light")) == 2
    assert states.domains() == {"light", "sensor"}
    assert sorted(states.entity_ids("light")) == ["light.a", "light.b"]


# --- services --------------------------------------------------------------
@pytest.mark.asyncio
async def test_service_register_call_and_response(tmp_path):
    jarvis = Jarvis(tmp_path)
    calls = []

    async def handler(call: ServiceCall):
        calls.append(call.data)
        return {"ok": True, "got": call.get("value")}

    jarvis.services.register("demo", "do", handler, supports_response=True)
    assert jarvis.services.has_service("demo", "do")

    result = await jarvis.async_call_service("demo", "do", {"value": 7})
    assert calls == [{"value": 7}]
    assert result == {"ok": True, "got": 7}

    with pytest.raises(ServiceNotFound):
        await jarvis.async_call_service("demo", "missing")


@pytest.mark.asyncio
async def test_service_sync_handler_and_context(tmp_path):
    jarvis = Jarvis(tmp_path)
    seen = {}
    jarvis.services.register(
        "demo", "sync", lambda call: seen.update(origin=call.context.origin)
    )
    await jarvis.async_call_service("demo", "sync", context=Context(origin="llm"))
    assert seen["origin"] == "llm"


# --- registries ------------------------------------------------------------
@pytest.mark.asyncio
async def test_registries_persist_and_dedupe(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.areas.load()
    await jarvis.devices.load()
    await jarvis.entities.load()

    kitchen = await jarvis.areas.create("Kitchen", ["cookery"])
    assert jarvis.areas.get_by_name("kitchen") is kitchen
    assert jarvis.areas.get_by_name("COOKERY") is kitchen

    device = await jarvis.devices.async_get_or_create(
        ["mac:aa:bb"], "Bulb", "demo", manufacturer="Acme"
    )
    again = await jarvis.devices.async_get_or_create(["mac:aa:bb"], "Bulb", "demo")
    assert device.id == again.id  # deduped by identifier

    entry = await jarvis.entities.async_get_or_create(
        "light", "demo", "uid-1", "Kitchen Light", device_id=device.id
    )
    assert entry.entity_id == "light.kitchen_light"
    dup = await jarvis.entities.async_get_or_create(
        "light", "demo", "uid-1", "Kitchen Light"
    )
    assert dup.entity_id == entry.entity_id  # unique_id dedupe

    other = await jarvis.entities.async_get_or_create(
        "light", "demo", "uid-2", "Kitchen Light"
    )
    assert other.entity_id == "light.kitchen_light_2"  # collision suffix

    # area resolution through the device
    await jarvis.devices.update(device.id, area_id=kitchen.id)
    assert jarvis.area_for_entity(entry.entity_id) == kitchen.id

    # reload from disk
    fresh = Jarvis(tmp_path)
    await fresh.areas.load()
    await fresh.devices.load()
    await fresh.entities.load()
    assert fresh.areas.get_by_name("Kitchen") is not None
    assert fresh.entities.get(entry.entity_id) is not None


# --- entity platform -------------------------------------------------------
class DemoLight(Entity):
    def __init__(self, name, uid):
        self._attr_name = name
        self._attr_unique_id = uid
        self._attr_state = "off"

    def turn_on(self):
        self._attr_state = "on"
        self.async_write_state()


@pytest.mark.asyncio
async def test_entity_platform_adds_and_writes_state(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.areas.load()
    await jarvis.devices.load()
    await jarvis.entities.load()

    platform = EntityPlatform(jarvis, "light", "demo")
    light = DemoLight("Lab Light", "lab-1")
    await platform.async_add_entities([light])

    assert light.entity_id == "light.lab_light"
    assert jarvis.states.get("light.lab_light").state == "off"
    assert jarvis.entity_object("light.lab_light") is light

    light.turn_on()
    assert jarvis.states.get("light.lab_light").state == "on"

    # unavailable entities report as such
    light._attr_available = False
    light.async_write_state()
    assert jarvis.states.get("light.lab_light").state == "unavailable"


# --- config ----------------------------------------------------------------
def _write(dirpath: Path, name: str, text: str) -> None:
    path = dirpath / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_config_include_secret_env_and_packages(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_HOST", "10.0.0.9")
    _write(tmp_path, "secrets.yaml", "mqtt_pass: hunter2\n")
    _write(tmp_path, "mqtt.yaml", "broker: 127.0.0.1\npassword: !secret mqtt_pass\n")
    _write(tmp_path, "packages/lights.yaml", "light:\n  - platform: demo\n")
    _write(tmp_path, "packages/more.yaml", "light:\n  - platform: other\n")
    _write(
        tmp_path,
        "configuration.yaml",
        "jarvis:\n"
        "  name: Jarvis\n"
        "  host: !env_var MY_HOST\n"
        "mqtt: !include mqtt.yaml\n"
        # packages use !include_dir_named: filename -> package contents
        "packages: !include_dir_named packages\n",
    )
    config = load_config(tmp_path)
    assert config["jarvis"]["host"] == "10.0.0.9"
    assert config["mqtt"]["password"] == "hunter2"
    # two packages each contributing a `light:` list -> concatenated
    assert len(config["light"]) == 2
    assert {entry["platform"] for entry in config["light"]} == {"demo", "other"}
    assert "packages" not in config  # folded away


def test_config_missing_secret_raises(tmp_path):
    _write(tmp_path, "configuration.yaml", "mqtt:\n  password: !secret nope\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_config_package_conflict_raises(tmp_path):
    _write(tmp_path, "configuration.yaml", "jarvis:\n  name: J\npackages:\n  p:\n    jarvis:\n      name: K\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


# --- lifecycle -------------------------------------------------------------
@pytest.mark.asyncio
async def test_jarvis_setup_start_stop(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"jarvis": {"areas": ["Kitchen", {"name": "Lab"}]}})
    assert jarvis.areas.get_by_name("Kitchen") is not None
    assert jarvis.areas.get_by_name("Lab") is not None

    started = []
    jarvis.bus.listen("jarvis_start", lambda e: started.append(1))
    await jarvis.async_start()
    assert started == [1]

    stopped = []
    jarvis.register_shutdown(lambda: stopped.append(1))
    await jarvis.async_stop()
    assert stopped == [1]
    assert jarvis.is_running is False


# --- an unreachable service is information, not a malfunction --------------
#
# On a first run with Ollama not yet started, `binary_sensor.ollama_up` and
# `sensor.ollama_loaded_model` each logged a twenty-frame httpx traceback at
# ERROR every poll. Those entities exist to report exactly that condition, and
# the noise buried the real startup log.

class _FlakyEntity(Entity):
    def __init__(self, exc):
        self._attr_name = "Probe"
        self._attr_unique_id = "probe"
        self._attr_state = "ok"
        self._exc = exc

    async def async_update(self):
        if self._exc is not None:
            raise self._exc


async def _poll(tmp_path, exc, caplog, times=1):
    import logging as _logging

    entity = _FlakyEntity(exc)
    entity.jarvis = Jarvis(tmp_path)
    entity.entity_id = "binary_sensor.probe"
    with caplog.at_level(_logging.DEBUG, logger="jarvis.entity"):
        for _ in range(times):
            await entity.async_update_state()
    return entity, caplog.records


async def test_an_unreachable_service_is_a_warning_without_a_traceback(tmp_path, caplog):
    entity, records = await _poll(tmp_path, ConnectionError("All connection attempts failed"), caplog)
    assert entity.available is False
    assert records, "the transition to unavailable must still be reported"
    assert all(r.exc_info is None for r in records), (
        "an unreachable service logged a traceback; that is the normal case"
    )
    assert any(r.levelname == "WARNING" for r in records)


async def test_it_does_not_re_warn_on_every_poll_while_still_down(tmp_path, caplog):
    _entity, records = await _poll(
        tmp_path, TimeoutError("timed out"), caplog, times=4
    )
    warnings = [r for r in records if r.levelname == "WARNING"]
    assert len(warnings) == 1, (
        f"warned {len(warnings)} times for one outage; only the transition "
        "should be at WARNING"
    )


async def test_a_real_bug_still_gets_its_traceback(tmp_path, caplog):
    """The distinction is fault: our code being wrong is still an exception."""
    _entity, records = await _poll(tmp_path, ValueError("bad parse"), caplog)
    assert any(r.exc_info is not None for r in records), (
        "a programming error must keep its traceback"
    )
    assert any(r.levelname == "ERROR" for r in records)


async def test_input_helper_keys_do_not_warn_about_a_missing_integration(
    tmp_path, caplog
):
    """`input_boolean:` and friends are features, not integration names.

    `automation._async_setup_input_helpers` bootstraps `input_helpers` whenever
    one is present, so the entities really are created — but the loader warned
    "No integration named 'input_boolean' (config key ignored)" for all five,
    each listing every available integration. The warning was false: the key is
    consumed, not ignored.
    """
    import logging as _logging

    from jarvis.integrations import async_setup_integrations

    jarvis = Jarvis(tmp_path)
    config = {
        "input_boolean": {"guest_mode": {"name": "Guest mode"}},
        "input_number": {"volume": {"min": 0, "max": 10}},
        "input_select": {"mode": {"options": ["home", "away"]}},
        "input_text": {"note": {}},
        "input_datetime": {"alarm": {"has_time": True}},
    }
    with caplog.at_level(_logging.WARNING, logger="jarvis.integrations"):
        await async_setup_integrations(jarvis, config)

    bogus = [r for r in caplog.records if "No integration named" in r.getMessage()]
    assert not bogus, (
        "warned about config keys that another integration consumes: "
        + "; ".join(r.getMessage().split(" (")[0] for r in bogus)
    )

    # A key that really is a typo must still be reported — the point is to stop
    # lying about the five, not to stop reporting anything.
    caplog.clear()
    with caplog.at_level(_logging.WARNING, logger="jarvis.integrations"):
        await async_setup_integrations(Jarvis(tmp_path), {"input_bolean": {}})
    assert any("No integration named" in r.getMessage() for r in caplog.records)
