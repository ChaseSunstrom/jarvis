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
    # "Now do the same in the bedroom" after the ceiling lights: the bed light,
    # not "the bedroom has no ceiling lights" (resilience-core-restart, 27 Aug).
    assert "same in the bedroom" in TOOL_RULES and "not a\n  thing of the same name" in TOOL_RULES


def test_an_ambiguous_request_is_a_question_that_names_the_candidates():
    """"Turn on the light." on the sixteenth house (27 Aug): by voice the model
    turned on the ceiling lights and said so; typed, it asked "which light?"
    and named none. The rule says ask, name them, do nothing."""
    assert "When the words fit more than one thing" in TOOL_RULES
    assert "NAME the candidates" in TOOL_RULES
    assert "never pick\n  one, and never say it is done" in TOOL_RULES


def test_asking_after_a_job_never_starts_it_again():
    """"Is that finished yet?" after a restart started a second sensor audit on
    the sixteenth house (27 Aug): task_status was called, then
    run_background_task again. The rule says status, and only that."""
    assert '"Is that finished yet?", "how is it going?" about work already started is' in TOOL_RULES
    assert "never start the work again" in TOOL_RULES
