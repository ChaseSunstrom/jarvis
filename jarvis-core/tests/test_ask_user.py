"""Jarvis asking the user a question, and getting a real answer back.

The assistant regularly needs a fact only the user has — the address of a
service on their network, which of three lamps "the corner one" is, whether to
go ahead. Without a way to ask, it guesses, and a guess about an address is a
request sent to the wrong host.

The design decision worth pinning is that a question is **not** a new channel.
It is a Tier-3 approval request, so it inherits every property that makes an
approval mean something: single use, expiring, resolvable only by a human,
raised on the same event and shown on the same surfaces. The one addition is
`Tool.answerable`, which names the single argument a human's reply may write.

That last part is the security boundary and most of what is tested here. If an
answer could write any argument, then approving "turn on the kitchen lamp"
while supplying an answer would let the answer re-target it at the front door —
undoing the pin that makes the consent screen's text true.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    EVENT_APPROVAL_REQUIRED,
    MAX_CHOICES,
    TIER_APPROVAL,
    TIER_DIRECT,
    ToolRegistry,
    register_builtin_tools,
    schema_object,
)


@pytest.fixture
async def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    yield box
    await box.async_stop()


@pytest.fixture
def registry(jarvis):
    reg = ToolRegistry(jarvis)
    register_builtin_tools(reg)
    return reg


def _raised(jarvis) -> list[dict]:
    """Collect every approval request fired while a test runs."""
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, lambda event: seen.append(event.data))
    return seen


async def test_asking_holds_the_question_instead_of_answering_it(jarvis, registry):
    seen = _raised(jarvis)

    held = await registry.call("ask_user", {"question": "Which lamp did you mean?"}, None)

    assert held["status"] == "approval_required"
    assert seen and seen[0]["tool"] == "ask_user"
    # The question reaches the surface that will draw it, verbatim.
    assert seen[0]["arguments"]["question"] == "Which lamp did you mean?"
    # ...and the surface is told it may take an answer, without having to hold
    # a tool registry to find out. The phone does not have one.
    assert seen[0]["answerable"] == "answer"


async def test_the_answer_comes_back_as_the_tool_result(jarvis, registry):
    seen = _raised(jarvis)
    await registry.call("ask_user", {"question": "What is the printer's URL?"}, None)
    request_id = seen[0]["request_id"]

    resolved = await registry.approve_request(request_id, True, "http://printer.lan")

    assert resolved["status"] == "executed"
    # This is what puts the reply back into the conversation the model is having.
    assert resolved["result"]["answer"] == "http://printer.lan"
    assert resolved["result"]["question"] == "What is the printer's URL?"


async def test_choices_travel_with_the_question(jarvis, registry):
    seen = _raised(jarvis)
    await registry.call(
        "ask_user",
        {"question": "Which one?", "choices": ["Desk", "Corner", "Ceiling"]},
        None,
    )
    assert seen[0]["choices"] == ["Desk", "Corner", "Ceiling"]


async def test_a_question_with_no_choices_is_free_text(jarvis, registry):
    seen = _raised(jarvis)
    await registry.call("ask_user", {"question": "What is the URL?"}, None)
    assert seen[0]["choices"] == []


async def test_choices_are_bounded_and_cleaned(jarvis, registry):
    """The model does not get to decide how much of a consent screen it takes."""
    seen = _raised(jarvis)
    await registry.call(
        "ask_user",
        {
            "question": "Pick one",
            "choices": ["a", "a", "", "  b  ", None, {"x": 1}, True, *[str(i) for i in range(30)]],
        },
        None,
    )
    choices = seen[0]["choices"]
    assert len(choices) <= MAX_CHOICES
    assert "a" in choices and "b" in choices
    # Deduplicated, stripped, and nothing a surface cannot draw.
    assert choices.count("a") == 1
    assert all(isinstance(c, str) and c for c in choices)


async def test_denying_a_question_answers_nothing(jarvis, registry):
    seen = _raised(jarvis)
    await registry.call("ask_user", {"question": "Shall I?"}, None)

    resolved = await registry.approve_request(seen[0]["request_id"], False, "yes go on")

    assert resolved["status"] == "denied"
    # And the request is spent either way — a denial is not a retry.
    again = await registry.approve_request(seen[0]["request_id"], True, "yes")
    assert again["status"] == "error"


async def test_an_answer_cannot_rewrite_a_held_action(jarvis, registry):
    """The boundary. An answer writes ONE argument, named by the tool.

    Without this, resolving a held `lock_control` while passing an answer would
    let the answer re-target the action — so the human would agree to the text
    on their screen and something else would run.
    """
    ran: list[dict] = []

    async def _handler(args, context):
        ran.append(args)
        return {"status": "ok"}

    registry.register(
        name="unlock_thing",
        description="Unlock a named thing.",
        parameters=schema_object({"target": {"type": "string"}}, required=["target"]),
        handler=_handler,
        tier=TIER_APPROVAL,
        # NOTE: no `answerable`. This is the ordinary case.
    )
    seen = _raised(jarvis)
    await registry.call("unlock_thing", {"target": "lock.side_gate"}, None)

    resolved = await registry.approve_request(
        seen[0]["request_id"], True, {"target": "lock.front_door"}
    )

    assert resolved["status"] == "executed"
    assert ran == [{"target": "lock.side_gate"}], "the answer rewrote a pinned argument"


async def test_an_answer_cannot_reach_a_second_argument(jarvis, registry):
    """Even on a tool that DOES take an answer, only the named key moves."""
    ran: list[dict] = []

    async def _handler(args, context):
        ran.append(args)
        return {"status": "ok"}

    registry.register(
        name="confirm_thing",
        description="Do a thing to a target, once confirmed.",
        parameters=schema_object({"target": {"type": "string"}}, required=["target"]),
        handler=_handler,
        tier=TIER_APPROVAL,
        answerable="reply",
    )
    seen = _raised(jarvis)
    await registry.call("confirm_thing", {"target": "lock.side_gate"}, None)

    await registry.approve_request(seen[0]["request_id"], True, "lock.front_door")

    assert ran == [{"target": "lock.side_gate", "reply": "lock.front_door"}]


async def test_ask_user_is_tier_three_and_stays_there(registry):
    """A question that could run without a human is not a question."""
    tool = registry.get("ask_user")
    assert tool is not None
    assert tool.tier == TIER_APPROVAL
    assert tool.tier != TIER_DIRECT
    assert tool.answerable == "answer"


async def test_a_question_nobody_answered_says_so(jarvis, registry):
    """The gate is what supplies the answer; without it, fail loudly.

    Only reachable if the tier check is bypassed. Returning a plausible empty
    string here would put "" into the conversation as if the user had said it.
    """
    tool = registry.get("ask_user")
    out = await tool.handler({"question": "Which one?"}, None)
    assert out["status"] == "error"
    assert "answer" in out["error"]


async def test_a_question_also_goes_to_the_phone(jarvis, tmp_path):
    """And whichever surface answers first wins, safely.

    The console banner is right when somebody is at a screen. Most of the time
    they are not — they asked out loud and walked off — so a question also goes
    to `companion.ask`, which already knows how to reach the device the user is
    actually at, draw options as buttons and take a spoken answer.

    Only questions. A tier-3 ACTION stays on the surfaces that already show it:
    this is a route for supplying a fact, not a quieter way to consent to
    unlocking a door.
    """
    import asyncio

    from jarvis.integrations.llm import _bridge_questions_to_the_phone

    asked: list[dict] = []

    async def _ask(call):
        asked.append(dict(call.data))
        return {"answer": "http://printer.lan"}

    jarvis.services.register("companion", "ask", _ask, supports_response=True)

    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    held = await registry.call("ask_user", {"question": "Printer URL?"}, None)
    # The bridge runs as a task, so let it.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if asked:
            break

    assert asked and asked[0]["question"] == "Printer URL?"
    # The phone answered, so the request is spent — the console's copy of the
    # same question can no longer be answered, which is the race resolving
    # itself with no coordination at all.
    again = await registry.approve_request(held["request_id"], True, "something else")
    assert again["status"] == "error"


async def test_an_action_is_not_pushed_to_the_phone(jarvis):
    """The bridge is for questions. An unlock is not a question."""
    import asyncio

    from jarvis.integrations.llm import _bridge_questions_to_the_phone

    asked: list[dict] = []

    async def _ask(call):
        asked.append(dict(call.data))
        return {"answer": "yes"}

    jarvis.services.register("companion", "ask", _ask, supports_response=True)

    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    async def _handler(args, context):
        return {"status": "ok"}

    registry.register(
        name="unlock_gate",
        description="Unlock the gate.",
        parameters=schema_object({"target": {"type": "string"}}),
        handler=_handler,
        tier=TIER_APPROVAL,
    )
    await registry.call("unlock_gate", {"target": "lock.gate"}, None)
    for _ in range(10):
        await asyncio.sleep(0.01)

    assert asked == [], "a tier-3 action was pushed to the phone as a question"


async def test_a_question_from_a_tainted_turn_says_so(jarvis, registry):
    """The one path that shows the model's own words to a person.

    The tier system answers "may this run without a human". It cannot answer
    "should the human believe the words on the screen". For an ACTION the two
    coincide — what is shown is pinned entity ids, which injected text cannot
    forge. For a QUESTION they do not: `ask_user` renders the model's sentence
    on a consent surface, so a turn that has read a hostile page can put "What
    is your bank password?" in front of somebody in Jarvis's voice.

    Nothing refuses — a turn that read a page and needs to ask which of three
    results was meant is exactly the legitimate case. The surface is told, so
    it can say where the words came from.
    """
    from jarvis.api.devices import mark_untrusted
    from jarvis.core import Context

    seen = _raised(jarvis)
    context = Context()
    mark_untrusted(jarvis, context)

    await registry.call("ask_user", {"question": "Confirm your password?"}, context)

    assert seen[0]["tainted"] is True


async def test_an_ordinary_question_is_not_marked(jarvis, registry):
    seen = _raised(jarvis)
    await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert seen[0]["tainted"] is False


async def test_a_held_action_carries_the_same_mark(jarvis, registry):
    """Not only questions. A tier-3 action raised by a tainted turn earns it."""
    from jarvis.api.devices import mark_untrusted
    from jarvis.core import Context

    async def _handler(args, context):
        return {"status": "ok"}

    registry.register(
        name="open_gate",
        description="Open the gate.",
        parameters=schema_object({"target": {"type": "string"}}),
        handler=_handler,
        tier=TIER_APPROVAL,
    )
    seen = _raised(jarvis)
    context = Context()
    mark_untrusted(jarvis, context)
    await registry.call("open_gate", {"target": "cover.gate"}, context)
    assert seen[0]["tainted"] is True


async def test_the_phone_is_told_where_a_tainted_question_came_from(jarvis):
    """A lock screen has no room for a provenance field, so it goes in the words."""
    import asyncio

    from jarvis.api.devices import mark_untrusted
    from jarvis.core import Context
    from jarvis.integrations.llm import UNTRUSTED_PREFIX, _bridge_questions_to_the_phone

    asked: list[dict] = []

    async def _ask(call):
        asked.append(dict(call.data))
        return {"answer": ""}

    jarvis.services.register("companion", "ask", _ask, supports_response=True)
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    context = Context()
    mark_untrusted(jarvis, context)
    await registry.call("ask_user", {"question": "Confirm your password"}, context)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if asked:
            break

    assert asked and asked[0]["question"].startswith(UNTRUSTED_PREFIX)


async def test_an_ordinary_question_reaches_the_phone_unadorned(jarvis):
    import asyncio

    from jarvis.integrations.llm import UNTRUSTED_PREFIX, _bridge_questions_to_the_phone

    asked: list[dict] = []

    async def _ask(call):
        asked.append(dict(call.data))
        return {"answer": ""}

    jarvis.services.register("companion", "ask", _ask, supports_response=True)
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    await registry.call("ask_user", {"question": "Which lamp?"}, None)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if asked:
            break

    assert asked and asked[0]["question"] == "Which lamp?"
    assert UNTRUSTED_PREFIX not in asked[0]["question"]
