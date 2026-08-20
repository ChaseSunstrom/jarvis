"""A background job asking a person something, and waiting for the answer.

## The gap this closes

`ask_user` works inside a model turn — sort of. The turn raises the question
and ends; the answer arrives minutes later and goes to whoever called the
approve API. That is fine for "which lamp did you mean", because the next
thing the user says carries the answer anyway.

It is useless for a background job. A coding job, a deep research run, a
workflow build talking to another AI — each of these can run for minutes with
nobody watching, and each of them hits a fork it cannot resolve. Today they
have two options: guess, or fail. `hold_question` is the third.

## What is actually being pinned here

Three things, and the second is the security boundary.

1. It rides the approval gate rather than inventing a channel, so the console,
   the phone bridge, the expiry clock and the single-use pop all apply with no
   change to any of them.
2. **The answer never touches the bus.** `EVENT_APPROVAL_RESOLVED` reaches
   every `subscribe_events` subscriber — through the console relay, anything
   that can open a socket. What a person types into a question box can be an
   address, a door code, a password. It goes into a private future and nowhere
   else.
3. It is invisible to the model. `as_openai_schema()` returns every registered
   tool and there is no hide mechanism, so this is a registry method — a model
   cannot call it with an invented ticket and put a consent card in front of
   somebody with nothing behind it.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    EVENT_APPROVAL_REQUIRED,
    EVENT_APPROVAL_RESOLVED,
    MAX_QUESTION_CHARS,
    TIER_APPROVAL,
    ToolRegistry,
    register_builtin_tools,
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
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, lambda event: seen.append(event.data))
    return seen


def _resolved(jarvis) -> list[dict]:
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_RESOLVED, lambda event: seen.append(event.data))
    return seen


async def _ask(registry, jarvis, question: str = "Which mailbox?", **kwargs):
    """Start a held question and wait until it has actually been raised.

    Returns `(task, request_id)`. Without the wait the test races the
    coroutine's first suspension point and reads an empty pending list.
    """
    before = {r["request_id"] for r in registry.pending_requests()}
    task = asyncio.ensure_future(registry.hold_question(question, **kwargs))
    for _ in range(50):
        await asyncio.sleep(0)
        fresh = [r for r in registry.pending_requests() if r["request_id"] not in before]
        if fresh:
            return task, fresh[0]["request_id"]
    task.cancel()
    raise AssertionError("the question was never raised")


# ---------------------------------------------------------------------------
# it looks exactly like an approval, because it is one
# ---------------------------------------------------------------------------
async def test_a_held_question_reaches_the_same_surfaces_an_approval_does(jarvis, registry):
    seen = _raised(jarvis)
    task, request_id = await _ask(registry, jarvis, "Which mailbox should it watch?")

    assert len(seen) == 1
    raised = seen[0]
    assert raised["request_id"] == request_id
    assert raised["arguments"]["question"] == "Which mailbox should it watch?"
    # `answerable` is what tells a console to draw an answer box and the phone
    # to accept typed text. Without it this is a yes/no card.
    assert raised["answerable"] == "answer"
    assert raised["tier"] == TIER_APPROVAL

    await registry.approve_request(request_id, True, "the work one")
    assert await task == "the work one"


async def test_choices_come_through_so_a_surface_can_draw_buttons(jarvis, registry):
    seen = _raised(jarvis)
    task, request_id = await _ask(registry, jarvis, choices=["Build it", "Change something"])

    assert seen[0]["choices"] == ["Build it", "Change something"]
    await registry.approve_request(request_id, True, "Build it")
    assert await task == "Build it"


async def test_a_question_from_a_known_stranger_is_marked_without_being_inferred(jarvis, registry):
    """`_is_tainted` reads the turn. A background job has no turn.

    The relay knows statically that its questions were composed by another AI,
    which is precisely the provenance the taint mark exists to show. So the
    caller can assert it rather than hoping it is inferred.
    """
    seen = _raised(jarvis)
    task, request_id = await _ask(registry, jarvis, tainted=True)

    assert seen[0]["tainted"] is True
    await registry.approve_request(request_id, False)
    assert await task is None


# ---------------------------------------------------------------------------
# the security boundary
# ---------------------------------------------------------------------------
async def test_the_answer_is_never_broadcast(jarvis, registry):
    """The one that matters.

    A resolved event goes to every subscriber. If the answer rode along, then
    "what is the code for the back gate?" would put the code on a socket that
    anything holding a console token can read.
    """
    resolved = _resolved(jarvis)
    task, request_id = await _ask(registry, jarvis, "What is the code for the back gate?")

    await registry.approve_request(request_id, True, "4417")
    assert await task == "4417"

    assert len(resolved) == 1
    assert resolved[0]["approved"] is True
    # Not under any key, not nested, not anywhere.
    assert "4417" not in repr(resolved[0])
    assert "answer" not in resolved[0]


async def test_the_future_is_not_on_the_wire_either(jarvis, registry):
    """`as_dict()` is what gets serialised to three surfaces. An asyncio
    Future in it would either crash the encoder or, worse, be quietly dropped
    by one encoder and not another."""
    task, request_id = await _ask(registry, jarvis)
    row = registry.pending_requests()[0]
    assert "future" not in row
    import json

    json.dumps(row)  # would raise on a Future
    await registry.approve_request(request_id, False)
    await task


async def test_the_model_cannot_raise_one(registry):
    """There is no hide-from-the-model mechanism, so this must not be a tool."""
    names = {t["function"]["name"] for t in registry.as_openai_schema()}
    assert "hold_question" not in names
    assert "relay_question" not in names
    assert not hasattr(registry, "_tools") or "hold_question" not in registry._tools


# ---------------------------------------------------------------------------
# every way it can end
# ---------------------------------------------------------------------------
async def test_denying_wakes_the_waiter_with_nothing(jarvis, registry):
    task, request_id = await _ask(registry, jarvis)
    got = await registry.approve_request(request_id, False)
    assert got["status"] == "denied"
    assert await task is None


async def test_approving_without_typing_anything_is_not_a_denial(jarvis, registry):
    """A person who presses yes on a question with no text has still answered.

    `None` is reserved for "there is no answer coming" — deny, expiry,
    shutdown — because a caller that cannot tell those apart will treat a
    shrug as permission to guess.
    """
    task, request_id = await _ask(registry, jarvis)
    await registry.approve_request(request_id, True, None)
    assert await task == ""


async def test_expiring_wakes_the_waiter_rather_than_parking_it_forever(jarvis, registry):
    task, request_id = await _ask(registry, jarvis, ttl=1.0)
    # The sweep another surface happens to run, before the waiter's own clock.
    assert registry.purge_expired(now=9e9) == 1
    assert await asyncio.wait_for(task, timeout=2) is None
    assert registry.pending_requests() == []


async def test_the_waiters_own_clock_works_when_nothing_sweeps(jarvis, registry):
    """Nothing in jarvis-core calls `purge_expired` on a timer. A question
    whose only clock was the sweep would hang until some unrelated surface
    happened to look at the pending list."""
    task, _request_id = await _ask(registry, jarvis, ttl=1.0)
    assert await asyncio.wait_for(task, timeout=5) is None
    assert registry.pending_requests() == []


async def test_cancelling_the_job_does_not_leave_the_question_up(jarvis, registry):
    task, _request_id = await _ask(registry, jarvis)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # A card on somebody's lock screen for a job that no longer exists is
    # worse than no card.
    assert registry.pending_requests() == []


async def test_answering_twice_is_refused_like_any_other_approval(jarvis, registry):
    task, request_id = await _ask(registry, jarvis)
    await registry.approve_request(request_id, True, "first")
    again = await registry.approve_request(request_id, True, "second")
    assert again["status"] == "error"
    assert "already-used" in again["error"]
    assert await task == "first"


async def test_two_questions_at_once_go_to_the_right_waiters(jarvis, registry):
    """A house can have two background jobs. The futures are per-request, and
    a mix-up here would answer one job with another's reply."""
    first, first_id = await _ask(registry, jarvis, "Which mailbox?")
    second, second_id = await _ask(registry, jarvis, "Which folder?")
    assert first_id != second_id

    await registry.approve_request(second_id, True, "Invoices")
    await registry.approve_request(first_id, True, "the work one")
    assert await first == "the work one"
    assert await second == "Invoices"


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------
async def test_a_long_question_is_clipped_before_it_reaches_a_lock_screen(jarvis, registry):
    seen = _raised(jarvis)
    task, request_id = await _ask(registry, jarvis, "why " * 400)
    assert len(seen[0]["arguments"]["question"]) <= MAX_QUESTION_CHARS
    await registry.approve_request(request_id, False)
    await task


async def test_an_empty_question_is_a_programming_error(registry):
    with pytest.raises(ValueError):
        await registry.hold_question("   ")


async def test_the_ordinary_approval_path_is_untouched(jarvis, registry):
    """`hold_question` added a branch to `approve_request`. This is the
    assertion that the branch is not taken for an ordinary held action."""
    resolved = _resolved(jarvis)
    held = await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert held["status"] == "approval_required"

    got = await registry.approve_request(held["request_id"], True, "the corner one")
    assert got["status"] == "executed"
    assert got["result"]["answer"] == "the corner one"
    assert resolved and resolved[0]["approved"] is True
