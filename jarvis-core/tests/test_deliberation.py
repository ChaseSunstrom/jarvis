"""Deciding, before the model is called, whether a turn needs working out.

## The two failures this sits between

Reasoning is off by default, and half this project's latency work exists to
make "turn the kitchen light off" answer immediately. Turning it on for
everything would undo all of that.

But a model with reasoning off, handed "check whether the back door is locked
and if it isn't, lock it and tell me who was last in the garage", starts
acting on the first clause and discovers the third halfway through. The
`think_it_through` tool lets it ask — and the turns that most need working out
are exactly the ones a model dives into instead of asking.

So the agent decides too, in code, before the first model call. No extra round
trip: the decision is a handful of regexes on the string the user just said,
and it flips a flag on the call that was going to happen anyway.

## What this file is really testing

Calibration. The scoring rules are easy; the hard part is that they are set
correctly relative to real sentences, in BOTH directions. So the two corpora
below are the point of the file — a list of turns that must stay instant, and
a list that must be thought about — and the individual rule tests exist to
explain a failure once a corpus row goes red.

The bias is deliberate and asymmetric: a false positive costs every trivial
request a reasoning block, a false negative costs one hard request the quality
it would have had, and the model can still escalate for itself. When in doubt
this file expects speed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.agent import THINK_TOOL_NAME, ConversationAgent  # noqa: E402
from jarvis.llm.deliberation import (  # noqa: E402
    DELIBERATE_AT,
    MAX_ASSESS_CHARS,
    STRONG,
    WEAK,
    assess,
)
from jarvis.llm.tools import Exposure, ToolRegistry  # noqa: E402


# ---------------------------------------------------------------------------
# the corpora — these are the test
# ---------------------------------------------------------------------------
STAY_INSTANT = [
    # The whole reason the default is off.
    "turn the kitchen light off",
    "turn on the lights in the kitchen and the hall",
    "lock the front door",
    "set the thermostat to 19",
    "dim the lounge lamps to 30%",
    "is the back door locked?",
    "what's the temperature in the bedroom",
    "play something",
    "stop",
    "hello",
    "thanks",
    "good morning",
    "yes",
    "never mind",
    "what time is it",
    "how many lights are on",
    "switch everything off",  # scope alone is not enough
    "run the goodnight scene",
    "tell me the forecast",
    "unlock the garage",
    "open the blinds in the bedroom",
    "remind me at seven",  # one schedule fact, one action
    "what did I ask you to remember about the boiler",
    "search for the opening hours of the hardware shop",
]

THINK_IT_THROUGH = [
    # Sequenced.
    "check the back door and then lock it and tell me who was in the garage",
    "first turn the heating down, then close the blinds, and finally set the alarm",
    # Conditional, with something to establish before acting.
    "if the washing machine has finished, turn the dryer on and let me know",
    # Short, and opens with a device verb, and still worth settling: "is it
    # warm" is a fact to check before the heating moves.
    "turn the heating down if it is warm in there",
    "unless someone is home already, turn the heating on when I leave work",
    # A judgement rather than an action.
    "should I switch the hall bulbs to the warm white ones or leave them",
    "compare the running cost of the heating on a timer versus a thermostat",
    "why does the landing light keep coming on at three in the morning",
    # Two capability families to join up.
    "email me a summary every morning of which doors were opened overnight",
    "when the back door opens, send a message to my phone and log it in a spreadsheet",
    # An enumerated list.
    "do these:\n1. check the boiler pressure\n2. book a service if it's low\n3. tell me",
    # Long and detailed.
    (
        "I want the house to behave differently when we have guests staying: "
        "the spare room heating should come on in the evening, the hall light "
        "should stay dim rather than off overnight, and the alarm should not "
        "arm itself at midnight the way it normally does, but only while "
        "somebody is actually in the spare room"
    ),
    # A constraint on top of a scope.
    "turn off all the lights except the landing one and the one in the porch, then arm the alarm",
]


@pytest.mark.parametrize("said", STAY_INSTANT)
def test_a_simple_turn_stays_instant(said):
    """Every one of these must cost nothing extra.

    A regression here is not a wrong answer — it is every light command in the
    house growing a reasoning block, which is the thing the default exists to
    prevent.
    """
    shape = assess(said)
    assert not shape.deliberate, f"{said!r} scored {shape.score}: {shape.why}"


@pytest.mark.parametrize("said", THINK_IT_THROUGH)
def test_a_complicated_turn_is_worked_out_first(said):
    shape = assess(said)
    assert shape.deliberate, f"{said!r} only scored {shape.score}"
    # And it can say why, because "why was that slow" deserves an answer.
    assert shape.why


# ---------------------------------------------------------------------------
# the individual rules, to explain a corpus failure
# ---------------------------------------------------------------------------
def test_sequencing_is_the_strongest_signal_there_is():
    """A sequenced request is multi-step by construction, whatever it is about."""
    assert assess("wash the car and then tell me").score >= 2


def test_a_condition_counts_because_something_must_be_settled_first():
    assert assess("if the door is open, close it for me please").score >= 2


def test_a_conjunction_alone_is_not_a_sequence():
    """"the kitchen and the hall" is one action with two targets. Treating
    every `and` as a step would slow down half the commands in the house."""
    assert not assess("turn on the lights in the kitchen and the hall").deliberate


def test_two_capability_families_score_because_the_join_is_the_hard_part():
    shape = assess("email me when the back door opens")
    assert "join up" in shape.why


def test_one_family_does_not():
    assert not assess("email me the invoices").deliberate
    assert not assess("turn the hall light off").deliberate


def test_a_scope_word_on_a_plain_command_is_not_enough():
    """"switch every light in the house off" is one call. Reading a scope word
    as complexity would slow down a whole class of ordinary requests."""
    for said in (
        "turn off all the lights if you would",
        "switch every light in the house off",
        "set all the radiators to 18 please",
    ):
        assert not assess(said).deliberate, said


def test_there_is_no_special_protection_for_commands():
    """There was a "starts with a device verb, so push it down" rule. It
    changed no outcome the politeness rule did not already handle, and it
    could change exactly one — a real condition on a short command. A guard
    that never helps and can only produce false negatives was removed."""
    import jarvis.llm.deliberation as module

    assert not hasattr(module, "SINGLE_COMMAND")
    assert assess("turn the heating down if it is warm in there").deliberate


def test_a_long_qualified_command_is_worked_out():
    long_one = (
        "turn off all the lights except the landing one and the porch one, "
        "then arm the alarm, but only if nobody is still up in the kitchen"
    )
    assert assess(long_one).deliberate


def test_a_greeting_short_circuits_before_any_pattern_runs():
    for said in ("hi", "thanks!", "good night", "ok"):
        assert assess(said).score == 0
        assert assess(said).reasons == ()


def test_a_very_short_turn_is_not_worth_reading_a_shape_from():
    assert assess("lights off").score == 0
    assert assess("").score == 0
    assert assess("   ").score == 0


def test_a_four_word_judgement_is_still_caught():
    """The cut-off is three words, not four, because four is where real
    questions start: "why is it dark" has a shape and "lights off" does not."""
    assert assess("why is it dark").deliberate


def test_the_cut_off_is_a_known_miss_and_not_a_free_lunch():
    """Three words can be a genuine judgement. The assessor only ever sees the
    current turn, so "compare these two" carries nothing to reason from — this
    costs one escalation the model can still make for itself, and that is the
    trade, stated rather than hidden."""
    assert not assess("compare these two").deliberate


def test_numbers_alone_are_not_arithmetic():
    """"set it to 19" has a number in it and nothing to work out."""
    assert not assess("set the thermostat to 19 in the evenings").deliberate


def test_a_pasted_wall_of_text_is_bounded_but_still_noticed():
    """Scanning stops at a cap so a paste cannot become a latency bug — and a
    wall of text is itself a signal, so nothing is lost by stopping early."""
    wall = "please sort this out. " * 4000
    assert len(wall) > MAX_ASSESS_CHARS
    shape = assess(wall)
    assert shape.deliberate
    assert "a lot to hold at once" in shape.why


def test_the_reasons_read_as_english():
    shape = assess("if the boiler is off, turn it on and then tell me the pressure")
    assert " and " in shape.why
    assert "," in shape.why or shape.why.count(" and ") == 1


def test_the_note_tells_it_to_resolve_ambiguity_before_acting_not_after():
    """The order is the point. An action already taken cannot be un-decided,
    so the check has to come before the first thing that changes something."""
    note = assess(THINK_IT_THROUGH[0]).note()
    assert "BEFORE the first action" in note


def test_the_note_tells_it_not_to_read_the_plan_out():
    """The failure this must not create: somebody asks for their lights off and
    gets a numbered plan."""
    assert "Do not read the plan out" in assess(THINK_IT_THROUGH[0]).note()


def test_a_turn_below_the_threshold_has_no_note():
    assert assess("turn the light off").note() == ""


def test_one_strong_signal_settles_it_and_one_weak_one_does_not():
    """The calibration, stated as an invariant rather than a number.

    A sequence, a condition, a judgement, a typed-out list or two capability
    families each mean the turn has real structure in it. A scope word or an
    exception on its own does not — "switch everything off" is one call.
    """
    assert STRONG >= DELIBERATE_AT
    assert WEAK < DELIBERATE_AT


def test_politeness_is_not_a_condition():
    """"if you would" is manners. Reading a condition there would slow down
    every polite request in the house, which is most of them."""
    assert not assess("turn the hall lights off if you could please").deliberate
    assert not assess("close the blinds if you don't mind").deliberate


def test_a_real_condition_alongside_the_manners_still_counts():
    """Stripping politeness must not strip the sentence's actual branch."""
    shape = assess("if you would, close the blinds if it is dark outside now")
    assert shape.deliberate


# ---------------------------------------------------------------------------
# what the agent does with it
# ---------------------------------------------------------------------------
class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return _EmptyStream()


class _EmptyStream:
    result = type("R", (), {"tool_calls": [], "content": "", "thinking": ""})()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        return None


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


def _agent(jarvis: Jarvis, client: _RecordingClient, **kw) -> ConversationAgent:
    return ConversationAgent(
        jarvis, client, ToolRegistry(jarvis, exposure=Exposure()), **kw
    )


async def test_a_complicated_turn_gets_reasoning_with_no_extra_round_trip(jarvis):
    """The design constraint. A classifier CALL would answer the same question
    and cost a round trip on every turn — which is the cost this exists to
    avoid paying."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse(THINK_IT_THROUGH[0]):
        pass

    assert len(client.calls) == 1, "deliberating must not cost an extra call"
    assert client.calls[0]["think"] is True
    assert agent.last_result.deliberated


async def test_a_simple_turn_is_untouched(jarvis):
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse("turn the kitchen light off"):
        pass

    assert client.calls[0]["think"] is False
    assert agent.last_result.deliberated == ""


async def test_the_note_reaches_the_model_as_a_system_message(jarvis):
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse(THINK_IT_THROUGH[0]):
        pass

    systems = [m for m in client.calls[0]["messages"] if m["role"] == "system"]
    assert any("Work out what has to happen" in m["content"] for m in systems)
    # It goes AFTER the user's turn is known but as a system message, not as
    # words in the user's mouth — the user did not ask for a plan.
    users = [m for m in client.calls[0]["messages"] if m["role"] == "user"]
    assert all("Work out what has to happen" not in m["content"] for m in users)


async def test_the_escalation_tool_is_withdrawn_once_it_is_already_on(jarvis):
    """A tool that does nothing is a round the model spends discovering that."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse(THINK_IT_THROUGH[0]):
        pass

    names = {t["function"]["name"] for t in (client.calls[0].get("tools") or [])}
    assert THINK_TOOL_NAME not in names


async def test_the_tool_is_still_offered_on_a_simple_turn(jarvis):
    """The model remains the better judge of a turn whose shape says nothing —
    "sort out the thing" is complicated and scores zero."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse("sort out the thing we discussed"):
        pass

    names = {t["function"]["name"] for t in (client.calls[0].get("tools") or [])}
    assert THINK_TOOL_NAME in names


async def test_an_operator_can_turn_it_off(jarvis):
    """`llm: deliberate: false`, for anyone who would rather latency be flat
    than the hard turns be better."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False, deliberate=False)

    async for _ in agent.converse(THINK_IT_THROUGH[0]):
        pass

    assert client.calls[0]["think"] is False
    assert agent.last_result.deliberated == ""


async def test_it_does_not_fight_an_install_that_already_thinks(jarvis):
    """With `llm: think: true` there is nothing to raise, and stamping
    `deliberated` would make the console claim a decision nobody made."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=True)

    async for _ in agent.converse(THINK_IT_THROUGH[0]):
        pass

    assert client.calls[0]["think"] is True
    assert agent.last_result.deliberated == ""


async def test_why_it_was_slow_is_answerable_afterwards(jarvis):
    """It rides on the result, so the console and the archive can both say
    which turns were thought about and what looked hard about them."""
    client = _RecordingClient()
    agent = _agent(jarvis, client, think=False)

    async for _ in agent.converse(THINK_IT_THROUGH[2]):
        pass

    row = agent.last_result.as_dict()
    assert row["escalated"] is True
    assert row["deliberated"]
