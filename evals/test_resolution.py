"""How often "the kitchen lamp" is the kitchen lamp.

## Why this exists

`evals/` measured three things: the routing policy, the persona's tone, and the
orchestrator's decomposition. It measured nothing about **tool calling** — not
which tool is chosen, not whether the arguments are right, and not whether the
name in those arguments finds the device the user meant.

That last one is the layer between a sentence and a switch, and it is a
hand-tuned similarity blend: `NAME_MATCH_THRESHOLD = 0.46`, a `NAME_TIE_WINDOW`
of `0.03`, and **every candidate inside the window is returned**. Two failure
modes follow directly, and neither had a number attached:

  * a phrase that matches nothing in the house resolving anyway, to whatever
    scored highest — a confident wrong device, which is worse than an error the
    model can see and correct;
  * one utterance quietly actuating several devices, because the tie window
    kept them all.

This is deterministic and needs no model: `resolve_entities` is a pure function
of the phrase and the entity registry. So unlike `persona_eval` and
`decomposition_eval` — which need a GPU and are documented as unrun — it runs in
CI, on every commit, offline. Run it directly for the per-case report:

    python3 evals/test_resolution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "jarvis-core"))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.tools import Exposure, resolve_entities  # noqa: E402

CASES = yaml.safe_load((Path(__file__).parent / "resolution_cases.yaml").read_text())

#: The bar. Not 100%: a fuzzy matcher that never errs is a fuzzy matcher that
#: has been overfitted to its own test file, and the cases here include phrases
#: ("the coffee maker") that a token blend can legitimately miss. What must not
#: happen is a *silent* wrong answer, which is why the two checks below are
#: separated — a miss costs a clarifying question, a mis-resolve costs a device.
PASS_RATE = 0.85


async def _house(tmp_path) -> Jarvis:
    """The fixture house from `resolution_cases.yaml`, as real registry state.

    Registry entries rather than bare states, because `build_candidates` reads
    the entity registry for the area and the aliases — the very things a phrase
    like "the lamp in the kitchen" resolves through.
    """
    jarvis = Jarvis(tmp_path)
    for spec in CASES["house"]:
        area = await jarvis.areas.create(spec["area"])
        jarvis.states.set(spec["entity_id"], "off", {"friendly_name": spec["name"]})
        await jarvis.entities.update(spec["entity_id"], area_id=area.id)
    return jarvis


@pytest.fixture
def house(tmp_path):
    """Built with `asyncio.run`, deliberately, and handed back as plain state.

    `evals/` has no asyncio plugin configured — `test_routing.py` is a pure
    function test and never needed one — and adding pytest-asyncio here to
    build a fixture would be a dependency bought for setup rather than for the
    thing under test. Everything this measures (`resolve_entities`) is
    synchronous; only the registry writes that stand the house up are not.
    """
    import asyncio

    return asyncio.run(_house(tmp_path))


def _resolve(jarvis: Jarvis, case: dict) -> list[str]:
    """The entity ids one phrase reaches. `Resolution.entity_ids`, sorted."""
    found = resolve_entities(
        jarvis,
        Exposure(),
        name=case["say"],
        domain=case.get("domain"),
    )
    return sorted(getattr(found, "entity_ids", None) or [])


# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["say"])
def test_a_phrase_that_matches_nothing_resolves_to_nothing(house, case):
    """The half that matters most, asserted case by case.

    A miss is recoverable — the model is told what is close and asks. A wrong
    device is not: the user finds out when the wrong light comes on, and the
    model reported success.
    """
    if case["expect"]:
        pytest.skip("this case is about a phrase that should resolve")

    assert _resolve(house, case) == [], (
        f"{case['say']!r} is nothing in this house and resolved anyway"
    )


@pytest.mark.parametrize(
    "case",
    [c for c in CASES["cases"] if c["expect"] and not c.get("fans_out")],
    ids=lambda c: c["say"],
)
def test_an_unambiguous_phrase_hits_exactly_one_thing(house, case):
    """The tie window returns everything within 0.03 of the top score.

    For a phrase with one obvious referent that is a defect, not a nicety: it
    is how "turn off the lamp" reaches three lamps.
    """
    found = _resolve(house, case)
    if not found:
        pytest.skip("a miss is measured by the rate check, not here")

    assert len(found) == 1, f"{case['say']!r} fanned out to {found}"


def test_the_overall_hit_rate_clears_the_bar(house):
    """The aggregate, printed so a regression is legible rather than a count."""
    wanted = [c for c in CASES["cases"] if c["expect"]]
    hits, misses = 0, []
    for case in wanted:
        found = _resolve(house, case)
        if set(found) == set(case["expect"]):
            hits += 1
        else:
            misses.append((case["say"], case["expect"], found))

    rate = hits / len(wanted)
    report = "\n".join(f"    {s!r}: wanted {w}, got {g}" for s, w, g in misses)
    assert rate >= PASS_RATE, (
        f"entity resolution {rate:.0%} ({hits}/{len(wanted)}), "
        f"below the {PASS_RATE:.0%} bar:\n{report}"
    )


if __name__ == "__main__":  # a per-case report, for tuning the matcher
    import asyncio
    import tempfile

    jarvis = asyncio.run(_house(Path(tempfile.mkdtemp())))
    ok = 0
    for case in CASES["cases"]:
        found = _resolve(jarvis, case)
        good = set(found) == set(case["expect"])
        ok += good
        print(f"{'ok  ' if good else 'MISS'} {case['say']!r:34} -> {found or '(nothing)'}")
    print(f"\n{ok}/{len(CASES['cases'])} exact")
