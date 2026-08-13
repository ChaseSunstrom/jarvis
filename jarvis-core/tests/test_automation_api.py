"""Creating, editing and deleting automations from the console.

The store has its own tests. What these cover is the part that makes the store
worth having: that a create actually reaches the running engine, that an edit
changes what runs, that a delete stops it — and that none of it can be turned
into a way to touch an automation the user wrote by hand.

Everything here drives the real `Jarvis`, the real `AutomationManager` and the
real `automation.reload` service, and asserts against the state machine. A test
that only checked the store would pass just as happily if the reload were
missing, which is the failure most likely to happen.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import common  # noqa: E402
from jarvis.api.common import ApiError  # noqa: E402
from jarvis.automation.authored import get_authored  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402

YAML_AUTOMATION = {
    "id": "hallway_motion",
    "alias": "Hallway motion",
    "trigger": [{"platform": "state", "entity_id": "binary_sensor.hall", "to": "on"}],
    "action": [{"service": "light.turn_on"}],
}

NEW = {
    "alias": "Porch light at dusk",
    "trigger": [{"platform": "time", "at": "21:00:00"}],
    "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
}


@pytest.fixture
async def jarvis(tmp_path):
    """A running box with one YAML automation.

    `configuration.yaml` is written for real because `automation.reload` reads
    it from disk — a config dir without one makes reload bail out and every
    assertion here would be testing the store alone.
    """
    (tmp_path / "configuration.yaml").write_text(
        "jarvis:\n  name: Test\nautomation:\n"
        "  - id: hallway_motion\n"
        "    alias: Hallway motion\n"
        "    trigger:\n"
        "      - platform: state\n"
        "        entity_id: binary_sensor.hall\n"
        "        to: 'on'\n"
        "    action:\n"
        "      - service: light.turn_on\n",
        encoding="utf-8",
    )
    box = Jarvis(tmp_path)
    await box.async_setup({"automation": [YAML_AUTOMATION]})
    yield box
    await box.async_stop()


def _aliases(jarvis) -> set[str]:
    return {
        state.attributes.get("friendly_name")
        for state in jarvis.states.all("automation")
    }


async def test_create_reaches_the_running_engine(jarvis):
    """A created automation is a live entity, not just a stored record."""
    result = await common.async_create_automation(jarvis, {"automation": dict(NEW)})
    entry = result["automation"]

    assert entry["id"].startswith("ui_")
    assert "Porch light at dusk" in _aliases(jarvis), (
        "the automation was stored but never reloaded, so nothing will run it "
        "until the next restart"
    )


async def test_update_changes_what_is_running(jarvis):
    created = (await common.async_create_automation(jarvis, {"automation": dict(NEW)}))[
        "automation"
    ]

    await common.async_update_automation(
        jarvis,
        {"automation_id": created["id"], "automation": {**NEW, "alias": "Porch light"}},
    )

    aliases = _aliases(jarvis)
    assert "Porch light" in aliases
    assert "Porch light at dusk" not in aliases, "the old version is still running"


async def test_delete_stops_it_running(jarvis):
    created = (await common.async_create_automation(jarvis, {"automation": dict(NEW)}))[
        "automation"
    ]
    assert "Porch light at dusk" in _aliases(jarvis)

    result = await common.async_delete_automation(
        jarvis, {"automation_id": created["id"]}
    )

    assert result == {"automation_id": created["id"], "deleted": True}
    assert "Porch light at dusk" not in _aliases(jarvis)
    # The YAML one is untouched by a delete of something else.
    assert "Hallway motion" in _aliases(jarvis)


async def test_a_yaml_automation_cannot_be_deleted_through_the_api(jarvis):
    """The console must not become a way to remove a file the user wrote.

    It is not enough that the call fails: the automation has to still be
    running afterwards, because a refusal that had already unloaded it would
    look identical from the caller's side.
    """
    with pytest.raises(ApiError) as err:
        await common.async_delete_automation(jarvis, {"automation_id": "hallway_motion"})

    assert err.value.code == "not_supported"
    assert "automations.yaml" in err.value.message
    assert "Hallway motion" in _aliases(jarvis)


async def test_a_yaml_automation_cannot_be_edited_through_the_api(jarvis):
    with pytest.raises(ApiError) as err:
        await common.async_update_automation(
            jarvis,
            {"automation_id": "hallway_motion", "automation": {**NEW, "alias": "Hijacked"}},
        )

    assert err.value.status == 400
    assert "Hijacked" not in _aliases(jarvis)
    assert "Hallway motion" in _aliases(jarvis)


async def test_the_list_shows_both_kinds_and_says_which_is_editable(jarvis):
    """Hiding the YAML ones would show an empty list on a box running them."""
    created = (await common.async_create_automation(jarvis, {"automation": dict(NEW)}))[
        "automation"
    ]

    rows = {row["id"]: row for row in common.automation_list_payload(jarvis)}

    assert rows["hallway_motion"]["editable"] is False
    assert rows[created["id"]]["editable"] is True
    assert rows[created["id"]]["trigger"] == NEW["trigger"]
    assert rows[created["id"]]["entity_id"].startswith("automation.")
    # Editable first, so what the user can act on is not buried under what they
    # cannot.
    assert [row["editable"] for row in common.automation_list_payload(jarvis)] == [
        True,
        False,
    ]


async def test_a_refused_automation_says_why_and_stores_nothing(jarvis):
    # `device` rather than `sun`: the engine grew a sun trigger, so it is no
    # longer an example of something it cannot do.
    with pytest.raises(ApiError) as err:
        await common.async_create_automation(
            jarvis, {"automation": {**NEW, "trigger": [{"platform": "device"}]}}
        )

    assert err.value.status == 400
    assert "device" in err.value.message
    assert get_authored(jarvis).items == {}


async def test_a_sun_automation_can_be_authored(jarvis):
    """The rule everybody writes first, from the console and from the model.

    The *condition* side has understood `"sunset - 00:30"` since the beginning;
    there was simply no trigger platform, so the archetypal home automation
    could not be expressed at all. It is authorable now, which means the web
    editor and `create_automation` can both write it.
    """
    stored = await common.async_create_automation(
        jarvis,
        {
            "automation": {
                **NEW,
                "trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:30"}],
            }
        },
    )

    assert stored["automation"]["trigger"][0]["platform"] == "sun"
    assert get_authored(jarvis).items


async def test_the_websocket_envelope_is_not_mistaken_for_the_automation():
    """`id` and `type` are the transport's, not the automation's.

    Every websocket frame carries them, and `validate` refuses unknown fields —
    so passing a frame through verbatim would reject every well-formed request
    over the socket while REST worked fine.
    """
    config = common._automation_config(
        {"id": 7, "type": "config/automation/create", **NEW}
    )
    assert config == NEW

    # An explicit `automation:` object wins, and is not polluted by the envelope.
    nested = common._automation_config(
        {"id": 7, "type": "config/automation/update", "automation_id": "ui_x",
         "automation": dict(NEW)}
    )
    assert nested == NEW


async def test_missing_automation_id_is_a_clear_refusal(jarvis):
    for call in (common.async_update_automation, common.async_delete_automation):
        with pytest.raises(ApiError) as err:
            await call(jarvis, {"automation": dict(NEW)})
        assert "automation_id" in err.value.message


async def test_deleting_one_that_is_already_gone_is_a_404(jarvis):
    with pytest.raises(ApiError) as err:
        await common.async_delete_automation(jarvis, {"automation_id": "ui_nosuch"})
    assert err.value.status == 404


def test_every_automation_route_is_wired_to_the_api():
    """The handlers exist; prove nothing is an orphan.

    Both transports are checked because the console uses REST and the app uses
    the websocket, and a command registered in one table and not the other is
    the kind of gap that only shows up on the client nobody tested with.
    """
    from jarvis.api import rest, websocket

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    for verb in ("list", "create", "update", "delete"):
        assert f"/api/config/automation/{verb}" in paths, verb
        assert f"config/automation/{verb}" in websocket.WebSocketHandler._HANDLERS, verb


async def test_the_companion_list_is_wired_and_empty_when_nothing_registered(tmp_path):
    """Phones and desktops, as opposed to the house's own entity registry.

    Empty is the honest answer on a box nothing has connected to yet — the
    console hides the panel rather than showing a heading over nothing.
    """
    box = Jarvis(tmp_path)
    assert common.companion_list_payload(box) == []


def test_the_companion_route_is_wired_to_both_transports():
    from jarvis.api import rest, websocket

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    assert "/api/config/companion/list" in paths
    assert "config/companion/list" in websocket.WebSocketHandler._HANDLERS


# ===========================================================================
# The assistant building the house
# ===========================================================================
# "Jarvis should be able to create them as well." The interesting half is not
# that it can, but what each creation is WORTH — and the three answers are
# deliberately different.


async def test_the_assistant_can_make_a_room(jarvis):
    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    tool = registry.get("create_area")
    assert tool is not None and tool.tier == 1, (
        "an area is a label; creating one cannot do anything to anything"
    )

    result = await tool.handler({"name": "Study", "aliases": ["office"]}, None)
    assert result["status"] == "ok"
    assert jarvis.areas.get_by_name("office") is not None, "the alias did not stick"


async def test_creating_an_automation_costs_what_it_will_eventually_do(jarvis):
    """The tier is a property of the actions, not of the act of writing them.

    An automation is a standing instruction that runs later, unattended. So
    `create_automation` is gated by exactly the function that decides whether
    RUNNING one needs a human — anything else would be a way to schedule a
    door unlock without ever being asked about a door unlock.
    """
    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    tool = registry.get("create_automation")
    assert tool is not None
    assert tool.gate is not None, "creating an automation is not gated at all"

    harmless = {"action": [{"service": "light.turn_on", "target": {"entity_id": "light.x"}}]}
    assert tool.gate(harmless) is False

    for dangerous in (
        {"action": [{"service": "lock.unlock", "target": {"entity_id": "lock.front"}}]},
        # A script hides whatever is inside it, so its reach is unknowable from
        # here and the answer has to be "ask".
        {"action": [{"service": "script.mystery"}]},
        # Templated service names are the same problem wearing a disguise.
        {"action": [{"service": "{{ whatever }}"}]},
        # Buried inside a choose, which is where a naive walk stops looking.
        {"action": [{"choose": [{"sequence": [{"service": "lock.unlock"}]}]}]},
    ):
        assert tool.gate(dangerous) is True, dangerous


async def test_writing_a_new_tool_always_needs_a_human(jarvis):
    """Not a gate — a tier, and unconditionally.

    A YAML tool can name an endpoint to call. A model that can write its own
    tools can write itself a way out of every constraint in the registry, so
    there must be no argument that makes this happen unattended.
    """
    from jarvis.llm.tools import TIER_APPROVAL, ToolRegistry, register_builtin_tools

    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    tool = registry.get("create_tool")
    assert tool is not None
    assert tool.tier == TIER_APPROVAL
    assert tool.gate is None, (
        "a gate can be argued with by choosing arguments; this must not be"
    )


async def test_there_is_no_way_to_invent_a_device(jarvis):
    """Devices come from integrations, and pretending otherwise is a lie.

    A bulb exists because a bridge said so. A tool that made a `light.foo` with
    nothing behind it would produce an entity that controls nothing and cannot
    be told apart from one that is merely offline.
    """
    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    assert registry.get("create_device") is None
    assert registry.get("create_entity") is None
