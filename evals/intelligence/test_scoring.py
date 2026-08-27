"""The scorecard's arithmetic, offline.

The eval itself needs a model, a recogniser and twenty minutes. What can be
tested without any of that is the part that decides whether a run PASSED — the
floors, the ceilings, the counting — and that part must not be first exercised
at the end of a twenty-minute run.

    python3 -m pytest evals/intelligence -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "intelligence_run", Path(__file__).resolve().parent / "run.py"
)
run = importlib.util.module_from_spec(_spec)
sys.modules["intelligence_run"] = run
_spec.loader.exec_module(run)


def card(**overrides):
    """A scorecard that passes, so a test can break exactly one thing."""
    base = {
        section: {
            "passed": 10, "total": 10, "rate": 1.0,
            "floor": run.FLOORS[section], "cases": [],
        }
        for section in run.SECTIONS
    }
    base["latency"] = {
        condition: {
            "n": 4, "stt": 1.0, "ttft": 2.0, "tts_request": 3.0, "total": 4.0,
            "probes": [], "ceilings": ceilings,
        }
        for condition, ceilings in run.CEILINGS.items()
    }
    base["latency"]["idle"]["quiet"] = True
    base["latency"]["load"] = {"title": "a report", "kind": "research"}
    base["speech"] = {"wer_mean": 0.05, "wer_ceiling": run.WER_CEILING, "turns": 20}
    base["judge"] = []
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def test_a_clean_card_has_nothing_to_say():
    assert run.problems(card()) == []


def test_a_section_under_its_floor_is_named_with_its_numbers():
    low = card(routing={"passed": 4, "total": 10, "rate": 0.4})
    (why,) = run.problems(low)
    assert "routing" in why and "40%" in why and "4/10" in why


def test_a_section_that_never_ran_cannot_pass():
    """The failure mode this whole harness exists to refuse: a silent skip."""
    nothing = card(reasoning={"passed": 0, "total": 0, "rate": None})
    (why,) = run.problems(nothing)
    assert "nothing ran" in why


def test_the_load_pass_not_being_measurable_is_a_failure():
    """Measuring idle twice would otherwise be indistinguishable from a pass."""
    idle_only = card()
    idle_only["latency"]["under_load"] = {
        **idle_only["latency"]["under_load"], "n": 0,
        "stt": None, "ttft": None, "tts_request": None, "total": None,
    }
    problems = run.problems(idle_only)
    assert any("not measured" in why for why in problems)


def test_an_idle_pass_that_was_not_idle_is_reported_as_such():
    """The first run measured "idle" while a sensor audit was still running."""
    busy = card()
    busy["latency"]["idle"]["quiet"] = False
    (why,) = run.problems(busy)
    assert "not idle numbers" in why


def test_a_slow_box_under_load_is_over_its_ceiling():
    slow = card()
    slow["latency"]["under_load"]["total"] = run.CEILINGS["under_load"]["total"] + 1
    (why,) = run.problems(slow)
    assert "under_load" in why and "over the" in why


def test_a_reply_nobody_can_make_out_fails_on_its_own_axis():
    """Right answer, wrong loudspeaker. Not a section score — its own line."""
    mumbled = card(speech={"wer_mean": 0.5})
    (why,) = run.problems(mumbled)
    assert "WER" in why and "loudspeaker" in why


def test_sentences_are_counted_the_way_a_listener_counts_them():
    assert len(run.sentences("It is sixteen degrees.")) == 1
    assert len(run.sentences("It is sixteen degrees. Shall I close the window?")) == 2
    # An abbreviation is not a sentence boundary; counting it as one failed an
    # answer that had obeyed "one sentence only".
    assert len(run.sentences("Approx. sixteen degrees, Sir.")) == 1
    assert len(run.sentences("")) == 0


def test_words_ignores_the_whitespace_speech_leaves_behind():
    assert run.words("  yes  \n ") == ["yes"]


def test_the_markdown_names_a_failing_section_and_its_first_reason():
    broken = card(instructions={
        "passed": 1, "total": 2, "rate": 0.5,
        "cases": [
            {"name": "one-word", "ok": False, "failures": ["31 words, asked for at most 4"]},
            {"name": "units", "ok": True, "failures": []},
        ],
    })
    text = run.markdown(broken)
    assert "**under**" in text
    assert "instructions/one-word" in text
    assert "31 words" in text


def test_the_markdown_says_so_when_the_load_pass_never_happened():
    """A report that quietly omitted it would read as two clean passes."""
    text = run.markdown(card(latency={"load": None}))
    assert "NOT MEASURED" in text


def test_every_section_the_brief_names_has_a_floor():
    assert set(run.FLOORS) == set(run.SECTIONS)
