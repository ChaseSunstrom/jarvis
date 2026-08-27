"""The operating rules' wording, pinned where live runs found the model reading
them wrongly.

On the fifteenth rebuild's voice run (27 Aug) "Unlock the front door" was
answered with "unlocking it requires your explicit approval, which I can't
grant on my own … I can initiate the unlock request for you to confirm" and
no tool call — the model had taken rule 1's "decline in character" to mean
declining the gated action itself, so nothing was held, and the spoken
"yes, go ahead" that followed had nothing to resolve and fell through to the
model, which then made the call and reported the hold a turn late. These
tests pin the sentences that say a gated tool is CALLED and the house holds
it. They cannot prove the model reads them that way — the live scenario
house-confirm-by-voice does that on the rebuilt house.
"""

from __future__ import annotations

from jarvis.llm.agent import DEFAULT_PERSONA, TOOL_RULES


def test_a_gated_tool_is_called_not_refused():
    assert "call a gated tool exactly as you would any other" in DEFAULT_PERSONA
    assert "Never refuse the call yourself" in DEFAULT_PERSONA
    # The refusal is reserved for BYPASSING a gate, and the word is in the rule.
    assert "If asked to BYPASS a gate" in DEFAULT_PERSONA


def test_the_tool_rules_name_the_held_actions():
    assert "Unlocking, sending, running and" in TOOL_RULES
    assert "the call is the\n  initiation" in TOOL_RULES


def test_the_rules_say_what_it_means_in_a_follow_up():
    """The smoke slice's one steady miss (27 Aug): "Now turn it off again" after
    "turn on the bed light" turned it ON again, typed, in three gates. The
    rule names the pronoun and the direction."""
    assert '"It", "that", "the same" and "again" in a follow-up mean the thing this' in TOOL_RULES
    assert "Do the opposite of what" in TOOL_RULES

