"""The queue in front of the model.

Subagents run at once; the model server does not. What is pinned here is that
the limit holds, that waiting is fair, that the context budget is applied
before the call rather than hoped for, and — the one that decides whether
"parallel" is a true word — that overlap is measured rather than assumed.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.llm.pool import DEFAULT_MAX_CONCURRENT, ModelPool, budgeted

pytestmark = pytest.mark.asyncio


async def test_only_max_concurrent_calls_run_at_once():
    pool = ModelPool(max_concurrent=2)
    live = 0
    peak = 0

    async def call(index: int) -> None:
        nonlocal live, peak
        async with pool.slot(f"agent-{index}"):
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.05)
            live -= 1

    await asyncio.gather(*(call(i) for i in range(6)))
    assert peak == 2, peak
    assert pool.stats.in_flight_peak == 2
    assert pool.stats.finished == 6


async def test_the_queue_is_first_come_first_served():
    """Otherwise the first subagent to ask can be the last to run, forever."""
    pool = ModelPool(max_concurrent=1)
    order: list[int] = []
    started = asyncio.Event()

    async def hog() -> None:
        async with pool.slot("hog"):
            started.set()
            await asyncio.sleep(0.1)

    async def call(index: int) -> None:
        async with pool.slot(f"agent-{index}"):
            order.append(index)

    first = asyncio.ensure_future(hog())
    await started.wait()
    waiters = []
    for index in range(4):
        waiters.append(asyncio.ensure_future(call(index)))
        # A moment between each, so "arrived in this order" is a real fact
        # rather than whatever the loop happened to schedule.
        await asyncio.sleep(0.01)
    await asyncio.gather(first, *waiters)
    assert order == [0, 1, 2, 3], order


async def test_a_slot_is_released_even_when_the_call_fails():
    """A model that times out must not take a slot with it."""
    pool = ModelPool(max_concurrent=1)

    with pytest.raises(RuntimeError):
        async with pool.slot("doomed"):
            raise RuntimeError("the model server went away")

    async with pool.slot("after"):
        pass
    assert pool.stats.finished == 2


async def test_overlap_is_measured_not_assumed():
    """The number that decides whether "runs them in parallel" is true.

    A fan-out that spawned two subagents and ran them one after the other looks
    identical in every other record: two children, two results, one roll-up.
    """
    pool = ModelPool(max_concurrent=2)

    async def call(label: str) -> None:
        async with pool.slot(label):
            await asyncio.sleep(0.08)

    await asyncio.gather(call("a"), call("b"))
    assert pool.stats.overlap() > 0.04, pool.stats.as_dict()

    serial = ModelPool(max_concurrent=1)

    async def one(label: str) -> None:
        async with serial.slot(label):
            await asyncio.sleep(0.05)

    await one("a")
    await one("b")
    assert serial.stats.overlap() == 0.0


async def test_waiting_is_counted():
    pool = ModelPool(max_concurrent=1)

    async def call(label: str) -> None:
        async with pool.slot(label):
            await asyncio.sleep(0.05)

    await asyncio.gather(call("a"), call("b"))
    # The second call waited for the first: ~50 ms, and the assertion is loose
    # because a loaded CI box schedules when it feels like it.
    assert pool.stats.waited_seconds > 0.02, pool.stats.as_dict()
    # `queued_peak` counts callers waiting for a slot at the same moment; with
    # one slot and two callers that is 2 — but only if both asked before either
    # was served, which `gather` does not promise. What is certain is that
    # somebody queued.
    assert pool.stats.queued_peak >= 1


# --- the context budget ------------------------------------------------------


def test_a_prompt_within_budget_is_untouched():
    assert budgeted("short", 1000) == "short"


def test_and_one_over_it_is_cut_and_says_so():
    """Silently truncating is how a subagent answers about the wrong half."""
    cut = budgeted("x" * 5000, 1000)
    assert len(cut) <= 1000
    assert "truncated" in cut


def test_the_budget_has_a_floor():
    """A definition asking for 12 characters gets something usable instead."""
    cut = budgeted("y" * 1000, 5)
    assert len(cut) >= 200


def test_the_default_limit_is_two():
    """Documented as measured on this hardware, so it is worth pinning."""
    assert DEFAULT_MAX_CONCURRENT == 2
    assert ModelPool().max_concurrent == 2
