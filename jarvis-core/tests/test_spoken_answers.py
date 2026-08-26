"""The spoken-answer rules, checked against the one table that states them.

`tests/contracts/spoken_answers.json` is the definition of when the next thing
said in a conversation resolves the request waiting on it. This is jarvis-core's
half of reading it: the word lists in `jarvis/llm/spoken_answers.py` must be
the table's, and every case in the table must decide as the table says.

A wrong match approves an action the person did not confirm, so the cases are
weighted towards the refusals — "yes and also…" is not a yes, two waiting and
a yes is nobody's, a tainted request is never anybody's by voice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.llm import spoken_answers
from jarvis.llm.spoken_answers import Decision, decide, normalise

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/spoken_answers.json").read_text()
)


def _request(spec: dict) -> dict:
    """A pending request as `pending_for_conversation` lists it, from the
    table's shorthand (a tool name and the fields that matter)."""
    return {
        "request_id": f"req-{spec['tool']}",
        "tool": spec["tool"],
        "arguments": {},
        "answerable": spec.get("answerable"),
        "choices": list(spec.get("choices") or []),
        "tainted": bool(spec.get("tainted")),
    }


def test_the_lists_are_the_tables():
    assert spoken_answers.AFFIRMATIONS == frozenset(CONTRACT["affirmations"])
    assert spoken_answers.DENIALS == frozenset(CONTRACT["denials"])
    assert spoken_answers.STOPWORDS == frozenset(CONTRACT["stopwords"])
    assert list(spoken_answers.EDGE_FILLERS) == CONTRACT["normalisation"]["edge_fillers"]


def test_no_word_is_both_a_yes_and_a_no():
    """One utterance, one meaning: a phrase in both lists would be decided by
    whichever check ran first, which is not a rule anybody wrote down."""
    both = {normalise(a) for a in spoken_answers.AFFIRMATIONS} & {
        normalise(d) for d in spoken_answers.DENIALS
    }
    assert not both, both


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=[c["name"] for c in CONTRACT["cases"]])
def test_every_case_in_the_table(case):
    pending = [_request(spec) for spec in case["pending"]]
    verdict = decide(pending, case["utterance"])
    expect = case["expect"]
    assert verdict.kind == expect["kind"], (case["name"], verdict)
    if "index" in expect:
        assert verdict.index == expect["index"], (case["name"], verdict)
    if "answer" in expect:
        assert verdict.answer == expect["answer"], (case["name"], verdict)
    if expect["kind"] in ("none", "ambiguous"):
        assert verdict.answer is None


def test_normalisation_is_what_the_table_says():
    assert normalise("Yes, GO ahead!") == "yes go ahead"
    assert normalise("Don't.") == "dont"
    # Fillers go from the edges only, however many there are.
    assert normalise("Jarvis, please, yes, thanks") == "yes"
    # ...and never from the middle.
    assert normalise("yes please turn it on") == "yes please turn it on"
    assert normalise("   ") == ""


def test_the_decision_says_whether_it_resolves_anything():
    assert Decision("approve", 0).resolves
    assert Decision("deny", 0).resolves
    assert Decision("answer", 0, "x").resolves
    assert not Decision("none").resolves
    assert not Decision("ambiguous").resolves
    assert not Decision("tainted", 0).resolves


def test_the_pending_list_is_not_touched():
    """`decide` reads; `approve_request` is the only thing that resolves."""
    pending = [_request({"tool": "lock_control"})]
    before = json.dumps(pending, sort_keys=True)
    decide(pending, "yes")
    assert json.dumps(pending, sort_keys=True) == before
