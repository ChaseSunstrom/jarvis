"""Renaming an `entity_id`, which used to answer "not supported yet".

## Why it needs three subsystems and not one

An `entity_id` is not a label. It is the key in the registry, the key in the
state machine, and the string automations name their targets with. A rename
that moves only the registry entry leaves an entity whose state is still filed
under the old id — present twice, working neither way — and automations
pointing at an id nothing answers to, which is a house that silently stops
responding.

So this suite pins all three, and the two refusals that matter: an id that is
already taken, and a move between domains. The second is not fussiness — the
domain decides which services an entity accepts, so `light.x` renamed to
`switch.x` would promise `switch.turn_on` from a platform that does not
implement it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api.common import ApiError, async_update_entity  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402

pytestmark = pytest.mark.asyncio


async def _jarvis(tmp_path: Path) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    await jarvis.async_start()
    return jarvis


async def _entity(jarvis: Jarvis, object_id: str = "kitchen") -> str:
    entry = await jarvis.entities.async_get_or_create(
        "light", "demo", f"uid-{object_id}", object_id, name="Kitchen"
    )
    jarvis.states.set(entry.entity_id, "on", {"brightness": 200})
    return entry.entity_id


async def test_the_entity_gets_the_new_id(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)

    result = await async_update_entity(
        jarvis, {"entity_id": old, "new_entity_id": "light.cooking"}
    )

    assert result["entity_entry"]["entity_id"] == "light.cooking"
    assert result["renamed_from"] == old
    assert jarvis.entities.get("light.cooking") is not None
    assert jarvis.entities.get(old) is None


async def test_the_state_moves_with_it(tmp_path: Path):
    """A registry entry under the new id and a state under the old one is an
    entity that exists twice and works neither way."""
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)

    await async_update_entity(jarvis, {"entity_id": old, "new_entity_id": "light.cooking"})

    moved = jarvis.states.get("light.cooking")
    assert moved is not None, "the state was left behind"
    assert moved.state == "on"
    assert moved.attributes["brightness"] == 200
    assert jarvis.states.get(old) is None, "the old state is still there"


async def test_an_id_that_is_taken_is_refused(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis, "kitchen")
    await _entity(jarvis, "hallway")

    with pytest.raises(ApiError) as caught:
        await async_update_entity(
            jarvis, {"entity_id": old, "new_entity_id": "light.hallway"}
        )
    assert "already exists" in str(caught.value)
    assert jarvis.entities.get(old) is not None, "the entity was lost to a refusal"


async def test_an_entity_cannot_change_domain(tmp_path: Path):
    """The domain decides which services it accepts."""
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)

    with pytest.raises(ApiError) as caught:
        await async_update_entity(
            jarvis, {"entity_id": old, "new_entity_id": "switch.kitchen"}
        )
    assert "domains" in str(caught.value)


@pytest.mark.parametrize(
    "bad", ["kitchen", "light.", ".kitchen", "light.Kitchen Light", "light kitchen"]
)
async def test_a_malformed_id_is_refused(tmp_path: Path, bad: str):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)
    with pytest.raises(ApiError):
        await async_update_entity(jarvis, {"entity_id": old, "new_entity_id": bad})


async def test_renaming_to_the_same_id_is_a_no_op(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)
    result = await async_update_entity(jarvis, {"entity_id": old, "new_entity_id": old})
    assert result["entity_entry"]["entity_id"] == old
    assert "renamed_from" not in result


async def test_other_fields_still_apply_alongside_the_rename(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)
    result = await async_update_entity(
        jarvis,
        {"entity_id": old, "new_entity_id": "light.cooking", "name": "Cooking light"},
    )
    assert result["entity_entry"]["name"] == "Cooking light"
    assert result["entity_entry"]["entity_id"] == "light.cooking"


async def test_the_move_is_one_event_a_listener_can_follow(tmp_path: Path):
    """Not "one entity vanished and another appeared"."""
    from jarvis.const import EVENT_ENTITY_REGISTRY_UPDATED

    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_ENTITY_REGISTRY_UPDATED, lambda e: seen.append(e.data))

    await async_update_entity(jarvis, {"entity_id": old, "new_entity_id": "light.cooking"})

    moves = [d for d in seen if d.get("old_entity_id")]
    assert moves, f"no event named the old id: {seen}"
    assert moves[0]["old_entity_id"] == old
    assert moves[0]["entity_id"] == "light.cooking"


# ---------------------------------------------------------------------------
# the automations, which are the half that silently breaks a house
# ---------------------------------------------------------------------------
async def _automation(jarvis: Jarvis, entity_id: str) -> str:
    from jarvis.automation.authored import get_authored

    store = get_authored(jarvis)
    await store.async_load()
    entry = await store.async_create(
        {
            "alias": "Kitchen at dusk",
            "trigger": [{"platform": "state", "entity_id": entity_id, "to": "on"}],
            "action": [
                {"service": "light.turn_on", "target": {"entity_id": entity_id}}
            ],
        }
    )
    return str(entry["id"])


async def test_an_automation_follows_the_entity(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis)
    await _automation(jarvis, old)

    result = await async_update_entity(
        jarvis, {"entity_id": old, "new_entity_id": "light.cooking"}
    )

    assert result["automations_updated"] == ["Kitchen at dusk"]
    from jarvis.automation.authored import get_authored

    config = get_authored(jarvis).configs()[0]
    assert config["trigger"][0]["entity_id"] == "light.cooking"
    assert config["action"][0]["target"]["entity_id"] == "light.cooking"


async def test_a_similarly_named_entity_is_not_rewritten(tmp_path: Path):
    """`light.kitchen` must not rewrite `light.kitchen_counter` beside it.

    Whole strings, never substrings. A prefix match here would quietly retarget
    every neighbouring entity in the same automation.
    """
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis, "kitchen")
    await _entity(jarvis, "kitchen_counter")

    from jarvis.automation.authored import get_authored

    store = get_authored(jarvis)
    await store.async_load()
    await store.async_create(
        {
            "alias": "Both",
            "trigger": [{"platform": "state", "entity_id": "light.kitchen", "to": "on"}],
            "action": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": ["light.kitchen", "light.kitchen_counter"]},
                }
            ],
        }
    )

    await async_update_entity(jarvis, {"entity_id": old, "new_entity_id": "light.cooking"})

    config = get_authored(jarvis).configs()[0]
    assert config["action"][0]["target"]["entity_id"] == [
        "light.cooking",
        "light.kitchen_counter",
    ]


async def test_an_automation_that_never_mentioned_it_is_left_alone(tmp_path: Path):
    jarvis = await _jarvis(tmp_path)
    old = await _entity(jarvis, "kitchen")
    other = await _entity(jarvis, "hallway")
    await _automation(jarvis, other)

    result = await async_update_entity(
        jarvis, {"entity_id": old, "new_entity_id": "light.cooking"}
    )
    assert result["automations_updated"] == []


# ---------------------------------------------------------------------------
# the console's copy of the rules
# ---------------------------------------------------------------------------
async def test_the_console_and_the_server_agree_about_ids(tmp_path: Path):
    """One table, two implementations.

    `whyNotEntityId` in `jarvis-web/src/lib/entityAdmin.ts` copies these rules
    so the form can refuse without a round trip. The copy is for the message
    and never for the decision — but a copy that DRIFTS makes the form accept
    what the server rejects, and the reader blames the form. Neither side owns
    the answers: both read `tests/contracts/entity_id_rename.json`.
    """
    import json

    table = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "contracts"
        / "entity_id_rename.json"
    )
    assert table.is_file(), f"the shared table is missing: {table}"
    cases = json.loads(table.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 12, "the shared table lost most of its cases"

    for case in cases:
        jarvis = await _jarvis(tmp_path / f"c{cases.index(case)}")
        domain, object_id = case["from"].split(".", 1)
        entry = await jarvis.entities.async_get_or_create(
            domain, "demo", "uid-from", object_id
        )
        for taken in case.get("taken") or []:
            taken_domain, taken_object = taken.split(".", 1)
            await jarvis.entities.async_get_or_create(
                taken_domain, "demo", f"uid-{taken}", taken_object
            )
        try:
            await jarvis.entities.rename(entry.entity_id, case["to"])
            allowed = True
        except ValueError:
            allowed = False
        assert allowed is case["ok"], (
            f"{case['from']} -> {case['to']!r}: server said "
            f"{'allowed' if allowed else 'refused'}, table says {case['ok']}"
        )
