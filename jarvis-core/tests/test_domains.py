"""Domain service layer + homeassistant compat tests.

Fake entities record every call they receive, so these tests assert the
whole path: service data -> target resolution -> entity method -> state.
"""

import gc
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.bus import Context  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.entity import Entity, EntityPlatform  # noqa: E402
from jarvis.integrations import domains as domains_integration  # noqa: E402
from jarvis.integrations import homeassistant_compat as compat_integration  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class Recorder(Entity):
    """Base fake: remembers (service, kwargs) for everything it's asked to do."""

    def __init__(self, name, uid, state="off", device_info=None):
        self._attr_name = name
        self._attr_unique_id = uid
        self._attr_state = state
        self._attr_device_info = device_info
        self._attr_extra_attributes = {}
        self.calls = []

    def _record(self, action, kwargs=None):
        self.calls.append((action, dict(kwargs or {})))

    @property
    def actions(self):
        return [action for action, _ in self.calls]


class FakeLight(Recorder):
    async def async_turn_on(self, **kwargs):
        self._record("turn_on", kwargs)
        self._attr_state = "on"
        self._attr_extra_attributes = {
            k: v for k, v in kwargs.items() if k != "transition"
        }

    async def async_turn_off(self, **kwargs):
        self._record("turn_off", kwargs)
        self._attr_state = "off"
        self._attr_extra_attributes = {}

    async def async_toggle(self):
        self._record("toggle")
        self._attr_state = "off" if self._attr_state == "on" else "on"


class WriteOnlyLight(Recorder):
    """Can be switched on but has no async_turn_off / async_toggle at all."""

    async def async_turn_on(self, **kwargs):
        self._record("turn_on", kwargs)
        self._attr_state = "on"


class ToggleOnlyLight(Recorder):
    """Only implements async_toggle — the toggle service must fall back to it."""

    async def async_toggle(self):
        self._record("toggle")
        self._attr_state = "off" if self._attr_state == "on" else "on"


class FakeSwitch(FakeLight):
    pass


class FakeCover(Recorder):
    async def async_open_cover(self):
        self._record("open_cover")
        self._attr_state = "open"

    async def async_close_cover(self):
        self._record("close_cover")
        self._attr_state = "closed"

    async def async_stop_cover(self):
        self._record("stop_cover")

    async def async_set_cover_position(self, position):
        self._record("set_cover_position", {"position": position})
        self._attr_state = "open" if position > 0 else "closed"
        self._attr_extra_attributes = {"current_position": position}


class FakeClimate(Recorder):
    async def async_set_temperature(self, temperature):
        self._record("set_temperature", {"temperature": temperature})
        self._attr_extra_attributes = {"temperature": temperature}

    async def async_set_hvac_mode(self, hvac_mode):
        self._record("set_hvac_mode", {"hvac_mode": hvac_mode})
        self._attr_state = hvac_mode

    async def async_set_fan_mode(self, fan_mode):
        self._record("set_fan_mode", {"fan_mode": fan_mode})
        self._attr_extra_attributes = {"fan_mode": fan_mode}


class FakeLock(Recorder):
    async def async_lock(self):
        self._record("lock")
        self._attr_state = "locked"

    async def async_unlock(self):
        self._record("unlock")
        self._attr_state = "unlocked"


class FakeMediaPlayer(Recorder):
    async def async_turn_on(self, **kwargs):
        self._record("turn_on", kwargs)
        self._attr_state = "idle"

    async def async_turn_off(self, **kwargs):
        self._record("turn_off", kwargs)
        self._attr_state = "off"

    async def async_media_play(self):
        self._record("media_play")
        self._attr_state = "playing"

    async def async_media_pause(self):
        self._record("media_pause")
        self._attr_state = "paused"

    async def async_media_stop(self):
        self._record("media_stop")
        self._attr_state = "idle"

    async def async_media_next_track(self):
        self._record("media_next_track")

    async def async_media_previous_track(self):
        self._record("media_previous_track")

    async def async_volume_set(self, volume_level):
        self._record("volume_set", {"volume_level": volume_level})
        self._attr_extra_attributes = {"volume_level": volume_level}

    async def async_play_media(self, media_type, media_id):
        self._record("play_media", {"media_type": media_type, "media_id": media_id})
        self._attr_state = "playing"


class FakeNumber(Recorder):
    async def async_set_value(self, value):
        self._record("set_value", {"value": value})
        self._attr_state = value


class FakeText(FakeNumber):
    pass


class FakeSelect(Recorder):
    async def async_select_option(self, option):
        self._record("select_option", {"option": option})
        self._attr_state = option


class FakeButton(Recorder):
    async def async_press(self):
        self._record("press")
        self._attr_state = "pressed"


class FakeVacuum(Recorder):
    async def async_start(self):
        self._record("start")
        self._attr_state = "cleaning"

    async def async_return_to_base(self):
        self._record("return_to_base")
        self._attr_state = "returning"


class PollingSensor(Recorder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.updates = 0

    async def async_update(self):
        self.updates += 1
        self._attr_state = str(self.updates)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def make_jarvis(tmp_path) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    await jarvis.areas.load()
    await jarvis.devices.load()
    await jarvis.entities.load()
    await domains_integration.async_setup(jarvis, None)
    await compat_integration.async_setup(jarvis, None)
    return jarvis


async def add(jarvis: Jarvis, domain: str, *entities: Entity) -> EntityPlatform:
    platform = EntityPlatform(jarvis, domain, "fake")
    await platform.async_add_entities(list(entities))
    return platform


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
EXPECTED_SERVICES = [
    ("light", "turn_on"), ("light", "turn_off"), ("light", "toggle"),
    ("switch", "turn_on"), ("switch", "turn_off"), ("switch", "toggle"),
    ("fan", "turn_on"), ("fan", "turn_off"), ("fan", "toggle"),
    ("siren", "turn_on"), ("siren", "turn_off"), ("siren", "toggle"),
    ("cover", "open_cover"), ("cover", "close_cover"), ("cover", "stop_cover"),
    ("cover", "set_cover_position"), ("cover", "toggle"),
    ("cover", "turn_on"), ("cover", "turn_off"),
    ("media_player", "toggle"), ("vacuum", "toggle"),
    ("vacuum", "turn_on"), ("vacuum", "turn_off"),
    ("climate", "set_temperature"), ("climate", "set_hvac_mode"), ("climate", "set_fan_mode"),
    ("lock", "lock"), ("lock", "unlock"),
    ("media_player", "media_play"), ("media_player", "media_pause"),
    ("media_player", "media_stop"), ("media_player", "media_next_track"),
    ("media_player", "media_previous_track"), ("media_player", "volume_set"),
    ("media_player", "play_media"), ("media_player", "turn_on"), ("media_player", "turn_off"),
    ("number", "set_value"), ("text", "set_value"),
    ("select", "select_option"), ("button", "press"),
    ("vacuum", "start"), ("vacuum", "return_to_base"),
    ("homeassistant", "turn_on"), ("homeassistant", "turn_off"),
    ("homeassistant", "toggle"), ("homeassistant", "update_entity"),
    ("persistent_notification", "create"),
]


async def test_every_contract_service_is_registered(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    missing = [f"{d}.{s}" for d, s in EXPECTED_SERVICES if not jarvis.services.has_service(d, s)]
    assert missing == []


async def test_services_support_response(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    for domain, service in EXPECTED_SERVICES:
        svc = jarvis.services.services[domain][service]
        assert svc.supports_response is True, f"{domain}.{service}"


# ---------------------------------------------------------------------------
# light: kwargs plumbing
# ---------------------------------------------------------------------------
async def test_light_turn_on_passes_kwargs_and_writes_state(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Kitchen Lamp", "l1")
    await add(jarvis, "light", light)

    result = await jarvis.async_call_service(
        "light",
        "turn_on",
        {
            "entity_id": "light.kitchen_lamp",
            "brightness": 128,
            "rgb_color": [255, 0, 0],
            "color_temp_kelvin": 2700,
            "transition": 2,
        },
        return_response=True,
    )

    assert result == {"changed": ["light.kitchen_lamp"], "failed": {}}
    action, kwargs = light.calls[0]
    assert action == "turn_on"
    assert kwargs == {
        "brightness": 128,
        "rgb_color": (255, 0, 0),
        "color_temp_kelvin": 2700,
        "transition": 2.0,
    }
    state = jarvis.states.get("light.kitchen_lamp")
    assert state.state == "on"
    assert state.attributes["brightness"] == 128


async def test_light_brightness_pct_and_clamping(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1")
    await add(jarvis, "light", light)

    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.lamp", "brightness_pct": 50}
    )
    assert light.calls[-1][1]["brightness"] == 128

    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.lamp", "brightness": 900}
    )
    assert light.calls[-1][1]["brightness"] == 255


async def test_turn_off_takes_no_unrequested_kwargs(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1", state="on")
    await add(jarvis, "light", light)

    await jarvis.async_call_service(
        "light", "turn_off", {"entity_id": "light.lamp", "brightness": 10}
    )
    assert light.calls[-1] == ("turn_off", {})
    assert jarvis.states.get("light.lamp").state == "off"


# ---------------------------------------------------------------------------
# toggle
# ---------------------------------------------------------------------------
async def test_toggle_inspects_state(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    on_light = FakeLight("On Light", "l1", state="on")
    off_light = FakeLight("Off Light", "l2", state="off")
    await add(jarvis, "light", on_light, off_light)

    result = await jarvis.async_call_service(
        "light", "toggle", {"entity_id": ["light.on_light", "light.off_light"]},
        return_response=True,
    )
    assert result["failed"] == {}
    assert sorted(result["changed"]) == ["light.off_light", "light.on_light"]
    assert on_light.actions == ["turn_off"]
    assert off_light.actions == ["turn_on"]
    assert jarvis.states.get("light.on_light").state == "off"
    assert jarvis.states.get("light.off_light").state == "on"


async def test_toggle_falls_back_to_async_toggle(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = ToggleOnlyLight("Odd Light", "l1", state="off")
    await add(jarvis, "light", light)

    result = await jarvis.async_call_service(
        "light", "toggle", {"entity_id": "light.odd_light"}, return_response=True
    )
    assert result == {"changed": ["light.odd_light"], "failed": {}}
    assert light.actions == ["toggle"]
    assert jarvis.states.get("light.odd_light").state == "on"


async def test_cover_toggle_uses_open_close(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    shut = FakeCover("Blind", "c1", state="closed")
    await add(jarvis, "cover", shut)

    await jarvis.async_call_service("cover", "toggle", {"entity_id": "cover.blind"})
    assert shut.actions == ["open_cover"]
    await jarvis.async_call_service("cover", "toggle", {"entity_id": "cover.blind"})
    assert shut.actions == ["open_cover", "close_cover"]


# ---------------------------------------------------------------------------
# failures never raise
# ---------------------------------------------------------------------------
async def test_missing_method_is_a_failure_entry_not_an_exception(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    good = FakeLight("Good", "l1", state="on")
    bad = WriteOnlyLight("Bad", "l2", state="on")
    await add(jarvis, "light", good, bad)

    result = await jarvis.async_call_service(
        "light", "turn_off", {"entity_id": ["light.good", "light.bad"]}, return_response=True
    )
    assert result["changed"] == ["light.good"]
    assert "light.bad" in result["failed"]
    assert "light.turn_off" in result["failed"]["light.bad"]
    # the working entity still ran
    assert jarvis.states.get("light.good").state == "off"


async def test_entity_exception_is_captured_per_entity(tmp_path):
    jarvis = await make_jarvis(tmp_path)

    class Exploding(Recorder):
        async def async_turn_on(self, **kwargs):
            raise RuntimeError("bulb blew")

    ok = FakeLight("Fine", "l1")
    boom = Exploding("Boom", "l2")
    await add(jarvis, "light", ok, boom)

    result = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": ["light.fine", "light.boom"]}, return_response=True
    )
    assert result["changed"] == ["light.fine"]
    assert result["failed"]["light.boom"] == "RuntimeError: bulb blew"


async def test_unknown_and_wrong_domain_entities_fail_cleanly(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1")
    switch = FakeSwitch("Plug", "s1")
    await add(jarvis, "light", light)
    await add(jarvis, "switch", switch)

    result = await jarvis.async_call_service(
        "light",
        "turn_on",
        {"entity_id": ["light.lamp", "switch.plug", "light.nope"]},
        return_response=True,
    )
    assert result["changed"] == ["light.lamp"]
    assert "does not match service domain" in result["failed"]["switch.plug"]
    assert result["failed"]["light.nope"] == "unknown entity"
    assert switch.calls == []


async def test_missing_required_field_raises_value_error(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    await add(jarvis, "cover", FakeCover("Blind", "c1"))
    with pytest.raises(ValueError):
        await jarvis.async_call_service(
            "cover", "set_cover_position", {"entity_id": "cover.blind"}
        )


# ---------------------------------------------------------------------------
# targeting
# ---------------------------------------------------------------------------
async def test_entity_id_all_targets_only_that_domain(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    a = FakeLight("A", "l1")
    b = FakeLight("B", "l2")
    plug = FakeSwitch("Plug", "s1")
    await add(jarvis, "light", a, b)
    await add(jarvis, "switch", plug)

    result = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "all"}, return_response=True
    )
    assert sorted(result["changed"]) == ["light.a", "light.b"]
    assert result["failed"] == {}
    assert plug.calls == []


async def test_area_targeting_through_device(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    kitchen = await jarvis.areas.create("Kitchen")

    in_area = FakeLight(
        "Counter", "l1", device_info={"identifiers": ["dev-kitchen"], "name": "Counter Strip"}
    )
    elsewhere = FakeLight("Hall", "l2")
    kettle = FakeSwitch(
        "Kettle", "s1", device_info={"identifiers": ["dev-kitchen-2"], "name": "Kettle"}
    )
    await add(jarvis, "light", in_area, elsewhere)
    await add(jarvis, "switch", kettle)

    for identifier in ("dev-kitchen", "dev-kitchen-2"):
        device = jarvis.devices.get_by_identifier(identifier)
        await jarvis.devices.update(device.id, area_id=kitchen.id)

    result = await jarvis.async_call_service(
        "light", "turn_on", {"area_id": "kitchen"}, return_response=True
    )
    assert result == {"changed": ["light.counter"], "failed": {}}
    assert elsewhere.calls == []
    assert kettle.calls == []

    # areas are also addressable by their human name
    by_name = await jarvis.async_call_service(
        "switch", "turn_on", {"area_id": "Kitchen"}, return_response=True
    )
    assert by_name["changed"] == ["switch.kettle"]


async def test_area_targeting_via_entity_registry_area(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    lab = await jarvis.areas.create("Lab")
    light = FakeLight("Bench", "l1")
    await add(jarvis, "light", light)
    await jarvis.entities.update("light.bench", area_id=lab.id)

    result = await jarvis.async_call_service(
        "light", "turn_on", {"area_id": lab.id}, return_response=True
    )
    assert result["changed"] == ["light.bench"]


async def test_unknown_area_is_reported(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    result = await jarvis.async_call_service(
        "light", "turn_on", {"area_id": "atlantis"}, return_response=True
    )
    assert result["changed"] == []
    assert "unknown area" in result["failed"]["area_id:atlantis"]


async def test_device_targeting(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Desk", "l1", device_info={"identifiers": ["dev-1"], "name": "Desk Bulb"})
    other = FakeLight("Other", "l2", device_info={"identifiers": ["dev-2"], "name": "Other Bulb"})
    await add(jarvis, "light", light, other)

    device = jarvis.devices.get_by_identifier("dev-1")
    result = await jarvis.async_call_service(
        "light", "turn_on", {"device_id": device.id}, return_response=True
    )
    assert result == {"changed": ["light.desk"], "failed": {}}
    assert other.calls == []

    unknown = await jarvis.async_call_service(
        "light", "turn_on", {"device_id": "nope"}, return_response=True
    )
    assert "unknown device" in unknown["failed"]["device_id:nope"]


async def test_targets_are_deduplicated(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    kitchen = await jarvis.areas.create("Kitchen")
    light = FakeLight("Counter", "l1")
    await add(jarvis, "light", light)
    await jarvis.entities.update("light.counter", area_id=kitchen.id)

    result = await jarvis.async_call_service(
        "light",
        "turn_on",
        {"entity_id": "light.counter", "area_id": "kitchen"},
        return_response=True,
    )
    assert result["changed"] == ["light.counter"]
    assert len(light.calls) == 1


# ---------------------------------------------------------------------------
# virtual entities (state only, no live object)
# ---------------------------------------------------------------------------
async def test_virtual_entity_state_is_set_directly(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("light.ghost", "off", {"friendly_name": "Ghost"})

    result = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.ghost", "brightness": 200},
        return_response=True,
    )
    assert result == {"changed": ["light.ghost"], "failed": {}}
    state = jarvis.states.get("light.ghost")
    assert state.state == "on"
    assert state.attributes["brightness"] == 200
    assert state.attributes["friendly_name"] == "Ghost"  # existing attrs preserved

    await jarvis.async_call_service("light", "toggle", {"entity_id": "light.ghost"})
    assert jarvis.states.get("light.ghost").state == "off"


async def test_virtual_cover_position_and_climate(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("cover.ghost", "closed")
    jarvis.states.set("climate.ghost", "off")

    await jarvis.async_call_service(
        "cover", "set_cover_position", {"entity_id": "cover.ghost", "position": 60}
    )
    state = jarvis.states.get("cover.ghost")
    assert state.state == "open"
    assert state.attributes["current_position"] == 60

    await jarvis.async_call_service(
        "climate", "set_hvac_mode", {"entity_id": "climate.ghost", "hvac_mode": "heat"}
    )
    await jarvis.async_call_service(
        "climate", "set_temperature", {"entity_id": "climate.ghost", "temperature": 21.5}
    )
    climate = jarvis.states.get("climate.ghost")
    assert climate.state == "heat"
    assert climate.attributes["temperature"] == 21.5


# ---------------------------------------------------------------------------
# per-domain method dispatch
# ---------------------------------------------------------------------------
async def test_cover_services(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    cover = FakeCover("Blind", "c1", state="closed")
    await add(jarvis, "cover", cover)

    await jarvis.async_call_service("cover", "open_cover", {"entity_id": "cover.blind"})
    await jarvis.async_call_service("cover", "stop_cover", {"entity_id": "cover.blind"})
    await jarvis.async_call_service(
        "cover", "set_cover_position", {"entity_id": "cover.blind", "position": "42"}
    )
    await jarvis.async_call_service("cover", "close_cover", {"entity_id": "cover.blind"})

    assert cover.actions == ["open_cover", "stop_cover", "set_cover_position", "close_cover"]
    assert cover.calls[2][1] == {"position": 42}
    assert jarvis.states.get("cover.blind").state == "closed"


async def test_climate_services(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    climate = FakeClimate("Thermostat", "t1", state="off")
    await add(jarvis, "climate", climate)

    await jarvis.async_call_service(
        "climate", "set_temperature", {"entity_id": "climate.thermostat", "temperature": "20.5"}
    )
    await jarvis.async_call_service(
        "climate", "set_hvac_mode", {"entity_id": "climate.thermostat", "hvac_mode": "heat"}
    )
    await jarvis.async_call_service(
        "climate", "set_fan_mode", {"entity_id": "climate.thermostat", "fan_mode": "auto"}
    )
    assert climate.calls == [
        ("set_temperature", {"temperature": 20.5}),
        ("set_hvac_mode", {"hvac_mode": "heat"}),
        ("set_fan_mode", {"fan_mode": "auto"}),
    ]
    state = jarvis.states.get("climate.thermostat")
    assert state.state == "heat"
    assert state.attributes["fan_mode"] == "auto"


async def test_lock_services(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    lock = FakeLock("Front Door", "k1", state="unlocked")
    await add(jarvis, "lock", lock)

    await jarvis.async_call_service("lock", "lock", {"entity_id": "lock.front_door"})
    assert jarvis.states.get("lock.front_door").state == "locked"
    await jarvis.async_call_service("lock", "unlock", {"entity_id": "lock.front_door"})
    assert jarvis.states.get("lock.front_door").state == "unlocked"
    assert lock.actions == ["lock", "unlock"]


async def test_media_player_services(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    player = FakeMediaPlayer("Speaker", "m1", state="idle")
    await add(jarvis, "media_player", player)
    target = {"entity_id": "media_player.speaker"}

    await jarvis.async_call_service("media_player", "media_play", dict(target))
    await jarvis.async_call_service("media_player", "media_pause", dict(target))
    await jarvis.async_call_service("media_player", "media_next_track", dict(target))
    await jarvis.async_call_service("media_player", "media_previous_track", dict(target))
    await jarvis.async_call_service("media_player", "media_stop", dict(target))
    await jarvis.async_call_service(
        "media_player", "volume_set", {**target, "volume_level": 2.5}
    )
    await jarvis.async_call_service(
        "media_player",
        "play_media",
        {**target, "media_content_type": "music", "media_content_id": "spotify:x"},
    )

    assert player.actions == [
        "media_play", "media_pause", "media_next_track", "media_previous_track",
        "media_stop", "volume_set", "play_media",
    ]
    assert player.calls[5][1] == {"volume_level": 1.0}  # clamped
    assert player.calls[6][1] == {"media_type": "music", "media_id": "spotify:x"}
    assert jarvis.states.get("media_player.speaker").state == "playing"


async def test_number_text_select_button_vacuum(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    number = FakeNumber("Setpoint", "n1")
    text = FakeText("Label", "x1")
    select = FakeSelect("Mode", "s1")
    button = FakeButton("Doorbell", "b1")
    vacuum = FakeVacuum("Roomba", "v1")
    await add(jarvis, "number", number)
    await add(jarvis, "text", text)
    await add(jarvis, "select", select)
    await add(jarvis, "button", button)
    await add(jarvis, "vacuum", vacuum)

    await jarvis.async_call_service(
        "number", "set_value", {"entity_id": "number.setpoint", "value": "7"}
    )
    await jarvis.async_call_service(
        "text", "set_value", {"entity_id": "text.label", "value": "hello"}
    )
    await jarvis.async_call_service(
        "select", "select_option", {"entity_id": "select.mode", "option": "eco"}
    )
    await jarvis.async_call_service("button", "press", {"entity_id": "button.doorbell"})
    await jarvis.async_call_service("vacuum", "start", {"entity_id": "vacuum.roomba"})
    await jarvis.async_call_service(
        "vacuum", "return_to_base", {"entity_id": "vacuum.roomba"}
    )

    assert number.calls == [("set_value", {"value": 7})]  # cast off the YAML string
    assert text.calls == [("set_value", {"value": "hello"})]
    assert select.calls == [("select_option", {"option": "eco"})]
    assert button.actions == ["press"]
    assert vacuum.actions == ["start", "return_to_base"]
    assert jarvis.states.get("select.mode").state == "eco"
    assert jarvis.states.get("vacuum.roomba").state == "returning"


async def test_context_is_carried_onto_state_changes(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("light.ghost", "off")
    ctx = Context(origin="llm")
    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.ghost"}, context=ctx
    )
    assert jarvis.states.get("light.ghost").context.origin == "llm"


# ---------------------------------------------------------------------------
# homeassistant compat
# ---------------------------------------------------------------------------
async def test_homeassistant_turn_on_across_mixed_domains(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1")
    plug = FakeSwitch("Plug", "s1")
    cover = FakeCover("Blind", "c1", state="closed")
    lock = FakeLock("Door", "k1", state="locked")
    await add(jarvis, "light", light)
    await add(jarvis, "switch", plug)
    await add(jarvis, "cover", cover)
    await add(jarvis, "lock", lock)

    result = await jarvis.async_call_service(
        "homeassistant",
        "turn_on",
        {"entity_id": ["light.lamp", "switch.plug", "cover.blind", "lock.door"]},
        return_response=True,
    )
    assert sorted(result["changed"]) == ["cover.blind", "light.lamp", "switch.plug"]
    assert light.actions == ["turn_on"]
    assert plug.actions == ["turn_on"]
    assert cover.actions == ["open_cover"]  # mapped, not turn_on
    # locks are never implicitly opened
    assert lock.calls == []
    assert "lock.door" in result["failed"]


async def test_homeassistant_turn_off_and_data_passthrough(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1", state="off")
    cover = FakeCover("Blind", "c1", state="open")
    await add(jarvis, "light", light)
    await add(jarvis, "cover", cover)

    await jarvis.async_call_service(
        "homeassistant", "turn_on", {"entity_id": "light.lamp", "brightness": 64}
    )
    assert light.calls[-1][1]["brightness"] == 64

    result = await jarvis.async_call_service(
        "homeassistant",
        "turn_off",
        {"entity_id": ["light.lamp", "cover.blind"]},
        return_response=True,
    )
    assert sorted(result["changed"]) == ["cover.blind", "light.lamp"]
    assert cover.actions == ["close_cover"]


async def test_homeassistant_toggle_mixed(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1", state="on")
    player = FakeMediaPlayer("Speaker", "m1", state="playing")
    await add(jarvis, "light", light)
    await add(jarvis, "media_player", player)

    result = await jarvis.async_call_service(
        "homeassistant",
        "toggle",
        {"entity_id": ["light.lamp", "media_player.speaker"]},
        return_response=True,
    )
    assert result["failed"] == {}
    assert light.actions == ["turn_off"]          # via light.toggle
    assert player.actions == ["turn_off"]         # via state-based fallback
    assert jarvis.states.get("media_player.speaker").state == "off"


async def test_homeassistant_turn_on_by_area(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    den = await jarvis.areas.create("Den")
    light = FakeLight("Den Light", "l1")
    plug = FakeSwitch("Den Plug", "s1")
    sensor_only = FakeLight("Outside", "l2")
    await add(jarvis, "light", light, sensor_only)
    await add(jarvis, "switch", plug)
    await jarvis.entities.update("light.den_light", area_id=den.id)
    await jarvis.entities.update("switch.den_plug", area_id=den.id)

    result = await jarvis.async_call_service(
        "homeassistant", "turn_on", {"area_id": "Den"}, return_response=True
    )
    assert sorted(result["changed"]) == ["light.den_light", "switch.den_plug"]
    assert sensor_only.calls == []


async def test_homeassistant_all_skips_uncontrollable_domains_quietly(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1")
    await add(jarvis, "light", light)
    jarvis.states.set("sensor.temperature", "21")

    result = await jarvis.async_call_service(
        "homeassistant", "turn_on", {"entity_id": "all"}, return_response=True
    )
    assert result["changed"] == ["light.lamp"]
    assert result["failed"] == {}  # sensor wasn't named explicitly, so no noise


async def test_homeassistant_update_entity(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    sensor = PollingSensor("Probe", "p1")
    await add(jarvis, "sensor", sensor)

    result = await jarvis.async_call_service(
        "homeassistant", "update_entity", {"entity_id": "sensor.probe"}, return_response=True
    )
    assert result == {"changed": ["sensor.probe"], "failed": {}}
    assert sensor.updates == 1
    assert jarvis.states.get("sensor.probe").state == "1"

    jarvis.states.set("sensor.ghost", "5")
    virtual = await jarvis.async_call_service(
        "homeassistant", "update_entity", {"entity_id": "sensor.ghost"}, return_response=True
    )
    assert "sensor.ghost" in virtual["failed"]


async def test_persistent_notification_create_and_dismiss(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    events = []
    jarvis.bus.listen("persistent_notification", lambda event: events.append(event.data))

    created = await jarvis.async_call_service(
        "persistent_notification",
        "create",
        {"message": "Washer finished", "title": "Laundry", "notification_id": "laundry"},
        return_response=True,
    )
    assert created["notification_id"] == "laundry"
    assert jarvis.data["persistent_notifications"]["laundry"]["message"] == "Washer finished"
    assert events[0]["action"] == "create"
    assert events[0]["title"] == "Laundry"

    dismissed = await jarvis.async_call_service(
        "persistent_notification", "dismiss", {"notification_id": "laundry"},
        return_response=True,
    )
    assert dismissed["dismissed"] is True
    assert jarvis.data["persistent_notifications"] == {}
    assert events[1]["action"] == "dismiss"


async def test_notification_without_id_gets_one(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    created = await jarvis.async_call_service(
        "persistent_notification", "create", {"message": "hi"}, return_response=True
    )
    assert created["notification_id"] in jarvis.data["persistent_notifications"]


# ---------------------------------------------------------------------------
# loader integration
# ---------------------------------------------------------------------------
async def test_core_setup_registers_the_layer(tmp_path):
    """The loader treats both packages as core integrations."""
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"jarvis": {"name": "Test"}})
    assert jarvis.services.has_service("light", "turn_on")
    assert jarvis.services.has_service("homeassistant", "toggle")
    assert jarvis.services.has_service("persistent_notification", "create")


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------
async def test_toggle_forwards_the_fields_it_advertises(tmp_path):
    """`light.toggle` advertises brightness/colour — it must actually use them.

    The handler used to call the chosen branch with an empty kwargs dict, so
    every field in its own published schema was silently discarded.
    """
    jarvis = await make_jarvis(tmp_path)
    off_light = FakeLight("Off Light", "l1", state="off")
    on_light = FakeLight("On Light", "l2", state="on")
    await add(jarvis, "light", off_light, on_light)

    fields = jarvis.services.services["light"]["toggle"].fields
    assert "brightness" in fields  # the promise...

    await jarvis.async_call_service(
        "light",
        "toggle",
        {
            "entity_id": ["light.off_light", "light.on_light"],
            "brightness_pct": 40,
            "rgb_color": "0,255,0",
            "transition": 3,
        },
    )
    # ...and the delivery: the branch that turns on gets the full kwargs.
    assert off_light.calls == [
        ("turn_on", {"brightness": 102, "rgb_color": (0, 255, 0), "transition": 3.0}),
    ]
    # the branch that turns off only takes what turn_off accepts
    assert on_light.calls == [("turn_off", {"transition": 3.0})]
    assert jarvis.states.get("light.off_light").attributes["brightness"] == 102


async def test_toggle_rejects_bad_values_before_touching_anything(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    light = FakeLight("Lamp", "l1", state="off")
    await add(jarvis, "light", light)

    with pytest.raises(ValueError):
        await jarvis.async_call_service(
            "light", "toggle", {"entity_id": "light.lamp", "brightness": "very"}
        )
    assert light.calls == []


async def test_coroutine_write_state_is_awaited(tmp_path):
    """An entity may override async_write_state as a coroutine; dropping the
    returned coroutine would silently lose the state write."""
    jarvis = await make_jarvis(tmp_path)

    class AsyncWriteLight(FakeLight):
        wrote = False

        async def async_write_state(self):
            type(self).wrote = True
            self.jarvis.states.set(self.entity_id, self.state, {})

    # Registered by hand: EntityPlatform.async_add_entities also calls
    # async_write_state() synchronously, and that core path is out of scope
    # here (see the report note on jarvis/entity.py:192).
    light = AsyncWriteLight("Slow Lamp", "l1", state="off")
    light.jarvis = jarvis
    light.entity_id = "light.slow_lamp"
    jarvis.data.setdefault("entity_objects", {})["light.slow_lamp"] = light
    jarvis.states.set("light.slow_lamp", "off")
    AsyncWriteLight.wrote = False

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await jarvis.async_call_service(
            "light", "turn_on", {"entity_id": "light.slow_lamp"}
        )
        gc.collect()

    assert AsyncWriteLight.wrote is True
    never_awaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "never awaited" in str(w.message)
        and "domains" in str(w.filename)
    ]
    assert never_awaited == []


async def test_entity_id_all_skips_disabled_entities_with_a_stale_state(tmp_path):
    """`all` expansion walked the state machine first, so a disabled entity
    that still had a state got actuated anyway."""
    jarvis = await make_jarvis(tmp_path)
    live = FakeLight("Live", "l1")
    dead = FakeLight("Dead", "l2")
    await add(jarvis, "light", live, dead)
    await jarvis.entities.update("light.dead", disabled=True)
    assert jarvis.states.get("light.dead") is not None  # stale state still there

    result = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "all"}, return_response=True
    )
    assert result["changed"] == ["light.live"]
    assert dead.calls == []

    # naming it explicitly still works — the operator asked for it by name
    explicit = await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": "light.dead"}, return_response=True
    )
    assert explicit["changed"] == ["light.dead"]


async def test_repeat_calls_on_a_virtual_entity_do_not_spam_state_changed(tmp_path):
    """Virtual writes used force_update=True, so every no-op service call
    fired state_changed — waking bare state triggers, logbook and recorder."""
    jarvis = await make_jarvis(tmp_path)
    jarvis.states.set("light.ghost", "off")

    events = []
    jarvis.bus.listen("state_changed", lambda event: events.append(event.data["entity_id"]))

    for _ in range(3):
        await jarvis.async_call_service(
            "light", "turn_on", {"entity_id": "light.ghost", "brightness": 5}
        )
    assert events == ["light.ghost"]

    # a real change still fires
    await jarvis.async_call_service("light", "turn_off", {"entity_id": "light.ghost"})
    assert len(events) == 2


async def test_call_with_no_target_is_reported_not_silently_ignored(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    await add(jarvis, "light", FakeLight("Lamp", "l1"))

    result = await jarvis.async_call_service(
        "light", "turn_on", {}, return_response=True
    )
    assert result["changed"] == []
    assert "target" in result["failed"]

    # an empty area is a real (if fruitless) target: no bogus complaint
    await jarvis.areas.create("Empty Room")
    empty = await jarvis.async_call_service(
        "light", "turn_on", {"area_id": "empty_room"}, return_response=True
    )
    assert empty == {"changed": [], "failed": {}}


async def test_cover_and_vacuum_answer_the_generic_verbs(tmp_path):
    """Callers that dispatch uniformly (`<domain>.turn_on` for whatever the
    user named) must not hit ServiceNotFound on covers and vacuums."""
    jarvis = await make_jarvis(tmp_path)
    cover = FakeCover("Blind", "c1", state="closed")
    vacuum = FakeVacuum("Roomba", "v1", state="docked")
    await add(jarvis, "cover", cover)
    await add(jarvis, "vacuum", vacuum)

    await jarvis.async_call_service("cover", "turn_on", {"entity_id": "cover.blind"})
    await jarvis.async_call_service("cover", "turn_off", {"entity_id": "cover.blind"})
    await jarvis.async_call_service("vacuum", "turn_on", {"entity_id": "vacuum.roomba"})
    await jarvis.async_call_service("vacuum", "turn_off", {"entity_id": "vacuum.roomba"})

    assert cover.actions == ["open_cover", "close_cover"]
    assert vacuum.actions == ["start", "return_to_base"]


async def test_media_player_and_vacuum_toggle(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    player = FakeMediaPlayer("Speaker", "m1", state="playing")
    vacuum = FakeVacuum("Roomba", "v1", state="docked")
    await add(jarvis, "media_player", player)
    await add(jarvis, "vacuum", vacuum)

    await jarvis.async_call_service(
        "media_player", "toggle", {"entity_id": "media_player.speaker"}
    )
    await jarvis.async_call_service(
        "media_player", "toggle", {"entity_id": "media_player.speaker"}
    )
    assert player.actions == ["turn_off", "turn_on"]

    await jarvis.async_call_service("vacuum", "toggle", {"entity_id": "vacuum.roomba"})
    await jarvis.async_call_service("vacuum", "toggle", {"entity_id": "vacuum.roomba"})
    assert vacuum.actions == ["start", "return_to_base"]


async def test_compat_counts_services_that_return_no_report(tmp_path):
    """script/scene/automation register `turn_on` and return None. The compat
    layer used to drop that, reporting `changed: []` for an action that ran —
    which the LLM tool layer renders as an outright failure."""
    jarvis = await make_jarvis(tmp_path)
    ran = []

    async def script_turn_on(call):
        ran.append(call.data.get("entity_id"))

    jarvis.services.register("script", "turn_on", script_turn_on)
    jarvis.states.set("script.bedtime", "off")

    result = await jarvis.async_call_service(
        "homeassistant", "turn_on", {"entity_id": "script.bedtime"}, return_response=True
    )
    assert ran == [["script.bedtime"]]
    assert result == {"changed": ["script.bedtime"], "failed": {}}


async def test_compat_one_bad_domain_does_not_sink_the_rest(tmp_path):
    """A rejected value in one domain used to raise straight out of
    `homeassistant.turn_on`, discarding the report for domains that had
    already been actuated."""
    jarvis = await make_jarvis(tmp_path)
    plug = FakeSwitch("Plug", "s1", state="off")
    light = FakeLight("Lamp", "l1", state="off")
    await add(jarvis, "switch", plug)
    await add(jarvis, "light", light)

    result = await jarvis.async_call_service(
        "homeassistant",
        "turn_on",
        {"entity_id": ["switch.plug", "light.lamp"], "brightness": "nope"},
        return_response=True,
    )
    assert result["changed"] == ["switch.plug"]
    assert "light.lamp" in result["failed"]
    assert "brightness" in result["failed"]["light.lamp"]
    assert plug.actions == ["turn_on"]
    assert light.calls == []


async def test_compat_never_reaches_a_lock(tmp_path):
    """Locks are a gated (Tier-3) domain: no generic verb may operate one,
    including the blunt `entity_id: all` fan-out."""
    jarvis = await make_jarvis(tmp_path)
    lock = FakeLock("Front Door", "k1", state="locked")
    light = FakeLight("Lamp", "l1", state="off")
    await add(jarvis, "lock", lock)
    await add(jarvis, "light", light)

    for action in ("turn_on", "turn_off", "toggle"):
        await jarvis.async_call_service(
            "homeassistant", action, {"entity_id": "all"}, return_response=True
        )
        await jarvis.async_call_service(
            "homeassistant", action, {"entity_id": "lock.front_door"}, return_response=True
        )
    assert lock.calls == []
    assert jarvis.states.get("lock.front_door").state == "locked"
    # and no domain-level lock toggle exists to sneak through either
    assert not jarvis.services.has_service("lock", "toggle")
    assert not jarvis.services.has_service("lock", "turn_on")
    assert not jarvis.services.has_service("lock", "turn_off")


async def test_notification_requires_a_message(tmp_path):
    jarvis = await make_jarvis(tmp_path)
    with pytest.raises(ValueError):
        await jarvis.async_call_service("persistent_notification", "create", {})
    with pytest.raises(ValueError):
        await jarvis.async_call_service(
            "persistent_notification", "create", {"title": "Empty"}
        )
    assert jarvis.data["persistent_notifications"] == {}
