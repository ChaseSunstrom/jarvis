"""Automations created from the console: validation, storage, and the engine.

The store is what makes "add an automation" mean anything, so most of what is
worth testing is what it refuses and what it cannot be talked into doing to the
user's own YAML.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.automation.authored import (  # noqa: E402
    ID_PREFIX,
    AuthoredError,
    AuthoredStore,
    validate,
)
from jarvis.core import Jarvis  # noqa: E402

GOOD = {
    "alias": "Porch light at dusk",
    "trigger": [{"platform": "time", "at": "21:00:00"}],
    "action": [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
}


def test_validate_accepts_a_reasonable_automation():
    clean = validate(dict(GOOD))
    assert clean["alias"] == "Porch light at dusk"
    assert clean["mode"] == "single"  # defaulted, not required of the caller
    assert clean["trigger"] and clean["action"]


@pytest.mark.parametrize(
    "config,message",
    [
        ({}, "name"),
        ({"alias": "  "}, "name"),
        ({"alias": "x", "action": GOOD["action"]}, "trigger"),
        ({"alias": "x", "trigger": GOOD["trigger"]}, "action"),
        # A trigger with no platform never fires, and an automation that never
        # fires is indistinguishable from a broken one at three in the morning.
        ({"alias": "x", "trigger": [{}], "action": GOOD["action"]}, "platform"),
        # `sun` is a Home Assistant trigger this engine does not have. Saving it
        # would produce an automation that lists, looks right and never fires,
        # which is worse than refusing it.
        (
            {**GOOD, "trigger": [{"platform": "sun", "event": "sunset"}]},
            "no `sun` trigger",
        ),
        ({**GOOD, "mode": "sideways"}, "Mode"),
        ({**GOOD, "max": "lots"}, "whole number"),
        ({**GOOD, "alias": "x" * 200}, "under"),
        # Unknown fields are refused rather than dropped: the engine ignores
        # them, so accepting one is a setting that appears to work and does not.
        ({**GOOD, "webhook_id": "abc"}, "Unknown field"),
        ("not an automation", "object"),
    ],
)
def test_validate_refuses_and_says_why(config, message):
    with pytest.raises(AuthoredError) as err:
        validate(config)
    assert message.lower() in str(err.value).lower()


async def test_create_update_delete_round_trip(tmp_path):
    store = AuthoredStore(tmp_path)
    created = await store.async_create(dict(GOOD))

    assert created["id"].startswith(ID_PREFIX)
    assert created["created_at"] and created["updated_at"]

    updated = await store.async_update(created["id"], {**GOOD, "alias": "Porch light"})
    assert updated["alias"] == "Porch light"
    assert updated["created_at"] == created["created_at"]  # not reset by an edit

    reloaded = AuthoredStore(tmp_path)
    await reloaded.async_load()
    assert list(reloaded.items) == [created["id"]]

    assert await reloaded.async_delete(created["id"]) is True
    # Deleting what is already gone achieved what the caller wanted.
    assert await reloaded.async_delete(created["id"]) is False

    again = AuthoredStore(tmp_path)
    assert await again.async_load() == []


async def test_ids_are_namespaced_so_yaml_automations_cannot_be_touched(tmp_path):
    """The console must not be able to delete a file the user wrote.

    Ids are the only handle the API has, so if an authored id could collide
    with a YAML one, "delete this automation" would be a way to remove
    something the store never created and cannot put back.
    """
    store = AuthoredStore(tmp_path)
    created = await store.async_create(dict(GOOD))
    assert created["id"].startswith(ID_PREFIX)

    with pytest.raises(AuthoredError) as err:
        await store.async_delete("hallway_motion")  # an id from automations.yaml
    assert "automations.yaml" in str(err.value)

    with pytest.raises(AuthoredError):
        await store.async_update("hallway_motion", dict(GOOD))


async def test_a_corrupt_entry_is_dropped_rather_than_stopping_startup(tmp_path, caplog):
    """One bad record must not stop every other automation from loading."""
    store = AuthoredStore(tmp_path)
    good = await store.async_create(dict(GOOD))
    # Plant what an older format, or a hand-edit, might leave behind.
    store.items["ui_broken"] = {"id": "ui_broken", "alias": "no trigger"}
    store.items["hallway_motion"] = {"id": "hallway_motion", **GOOD}
    await store._async_save()

    fresh = AuthoredStore(tmp_path)
    with caplog.at_level("WARNING"):
        configs = await fresh.async_load()

    assert [c["id"] for c in configs] == [good["id"]]
    assert "ui_broken" in caplog.text
    assert "hallway_motion" in caplog.text


async def test_configs_are_what_the_engine_wants(tmp_path):
    """Bookkeeping stays in the store; the engine gets an automation."""
    store = AuthoredStore(tmp_path)
    await store.async_create(dict(GOOD))

    config = store.configs()[0]
    assert "created_at" not in config and "updated_at" not in config
    assert config["id"].startswith(ID_PREFIX)
    assert config["trigger"] and config["action"]


async def test_an_authored_automation_becomes_a_live_entity(tmp_path):
    """The point of the whole thing: created here, running there.

    Asserts against the state machine rather than the store, because a store
    that persists beautifully and never reaches the engine is exactly the
    failure this is written to catch.
    """
    store = AuthoredStore(tmp_path)
    await store.async_create({**GOOD, "alias": "Console made this"})

    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"automation": []})

    entities = [
        state for state in jarvis.states.all("automation")
        if state.attributes.get("friendly_name") == "Console made this"
    ]
    assert len(entities) == 1, [s.entity_id for s in jarvis.states.all("automation")]

    await jarvis.async_stop()


async def test_yaml_and_authored_automations_coexist(tmp_path):
    store = AuthoredStore(tmp_path)
    await store.async_create({**GOOD, "alias": "From the console"})

    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup(
        {
            "automation": [
                {
                    "id": "from_yaml",
                    "alias": "From the file",
                    "trigger": [{"platform": "time", "at": "06:00:00"}],
                    "action": [{"service": "light.turn_off"}],
                }
            ]
        }
    )

    names = {s.attributes.get("friendly_name") for s in jarvis.states.all("automation")}
    assert {"From the console", "From the file"} <= names

    await jarvis.async_stop()
