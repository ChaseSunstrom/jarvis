"""The job registry every slow thing reports through.

The bug it exists to close: `run_background_task` minted an id, fired an event
nothing listened to, and told the model to say "Accepted, the result arrives
later". Nothing ran, nothing tracked it, no result ever arrived. An assistant
that promises and then goes silent is indistinguishable from a broken one, and
the user cannot tell which.

So most of what is pinned here is honesty under failure: a task whose worker
died must not look like one that is merely slow, a percentage must not be
invented, and a finished task must not contradict its own steps.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.store import Store  # noqa: E402
from jarvis.tasks import (  # noqa: E402
    EVENT_TASK_ADDED,
    EVENT_TASK_REMOVED,
    EVENT_TASK_UPDATED,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Task,
    TaskRegistry,
)


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


def _registry(jarvis, **kw) -> TaskRegistry:
    return TaskRegistry(jarvis, store=Store(jarvis.config_dir, "tasks"), **kw)


def _events(jarvis) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    for name in (EVENT_TASK_ADDED, EVENT_TASK_UPDATED, EVENT_TASK_REMOVED):
        jarvis.bus.listen(name, lambda e, n=name: seen.append((n, e.data)))
    return seen


# --- the shape of a task ------------------------------------------------------

async def test_a_new_task_is_queued_and_announced(jarvis):
    registry = _registry(jarvis)
    seen = _events(jarvis)
    task = await registry.async_add("Reindex the notes", kind="background")
    assert task.status == STATUS_QUEUED
    assert registry.get(task.id) is task
    assert [name for name, _ in seen] == [EVENT_TASK_ADDED]
    assert seen[0][1]["task"]["title"] == "Reindex the notes"


async def test_progress_is_steps_done_over_steps_total(jarvis):
    registry = _registry(jarvis)
    task = await registry.async_add("Research", steps=["search", "read", "write"])
    assert task.fraction == 0
    await registry.async_update(task.id, step=0, step_status=STATUS_DONE)
    assert task.fraction == pytest.approx(1 / 3)
    await registry.async_update(task.id, step=1, step_status=STATUS_DONE)
    assert task.fraction == pytest.approx(2 / 3)


async def test_a_task_with_no_steps_reports_no_fraction_rather_than_zero(jarvis):
    """None means "do not draw a number", which is different from 0%."""
    registry = _registry(jarvis)
    task = await registry.async_add("Something opaque")
    assert task.fraction is None


async def test_an_open_ended_task_refuses_to_invent_a_denominator(jarvis):
    # A crawl that discovers pages as it goes has no total until it finishes.
    # A bar that sits at 90% for four minutes teaches people to ignore bars.
    registry = _registry(jarvis)
    task = await registry.async_add("Crawl", steps=["first page"], open_ended=True)
    await registry.async_update(task.id, step=0, step_status=STATUS_DONE)
    assert task.fraction is None
    await registry.async_update(task.id, add_steps=["second page"])
    assert len(task.steps) == 2
    assert task.fraction is None


async def test_finishing_reports_one_whole_even_when_open_ended(jarvis):
    registry = _registry(jarvis)
    task = await registry.async_add("Crawl", steps=["a"], open_ended=True)
    await registry.async_update(task.id, status=STATUS_DONE)
    assert task.fraction == 1.0


async def test_finishing_closes_out_every_step(jarvis):
    """A task saying `done` above a step still `running` contradicts itself."""
    registry = _registry(jarvis)
    task = await registry.async_add("Job", steps=["a", "b", "c"])
    await registry.async_update(task.id, step=0, step_status=STATUS_RUNNING)
    await registry.async_update(task.id, status=STATUS_DONE)
    assert [s.status for s in task.steps] == [STATUS_DONE] * 3


async def test_an_error_finishes_the_task_rather_than_annotating_it(jarvis):
    # Setting only `error` used to leave a task carrying the reason it failed
    # while still claiming to be running.
    registry = _registry(jarvis)
    task = await registry.async_add("Job")
    await registry.async_update(task.id, status=STATUS_RUNNING)
    await registry.async_update(task.id, error="the model server refused")
    assert task.status == STATUS_ERROR
    assert task.finished


async def test_blocked_is_not_running(jarvis):
    """"Working" and "waiting for you" are different things to show."""
    registry = _registry(jarvis)
    task = await registry.async_add("Needs approval")
    await registry.async_update(task.id, status=STATUS_BLOCKED)
    assert task.status == STATUS_BLOCKED
    assert not task.finished
    assert task in registry.active


# --- surviving a restart ------------------------------------------------------

async def test_tasks_survive_a_restart(jarvis):
    registry = _registry(jarvis)
    task = await registry.async_add("Scheduled thing", kind="scheduled")
    await registry.async_update(task.id, status=STATUS_BLOCKED)

    reborn = _registry(jarvis)
    await reborn.async_load()
    restored = reborn.get(task.id)
    assert restored is not None
    assert restored.kind == "scheduled"
    # It was waiting on a person before the restart, and it still is.
    assert restored.status == STATUS_BLOCKED


async def test_work_that_did_not_survive_says_so_instead_of_running_for_ever(jarvis):
    """The honest answer, and the actionable one.

    A task left `running` in the store did not survive — whatever was driving
    it is gone. Leaving it `running` gives a user no way to tell a slow job
    from a dead one, and nothing will ever move it again.
    """
    registry = _registry(jarvis)
    task = await registry.async_add("Long job", steps=["a", "b"])
    await registry.async_update(task.id, status=STATUS_RUNNING, step=0, step_status=STATUS_RUNNING)

    reborn = _registry(jarvis)
    await reborn.async_load()
    restored = reborn.get(task.id)
    assert restored.status == STATUS_ERROR
    assert "restart" in restored.error
    assert restored.steps[0].status == STATUS_ERROR
    # The step nobody had started stays untouched — it was never in flight.
    assert restored.steps[1].status == STATUS_QUEUED


async def test_a_queued_task_survives_because_the_queue_does(jarvis):
    """This used to assert the opposite, and the opposite used to be right.

    While nothing ran queued work, "queued" in the store meant "recorded, and
    nobody will ever pick this up", so erroring it on load was the honest
    answer. The task engine persists its queue alongside this list, so work that
    was WAITING really is still waiting — and `TaskEngine.load` is what fails a
    queued task the queue no longer mentions, because it is the only thing that
    knows.
    """
    registry = _registry(jarvis)
    task = await registry.async_add("Never started")
    reborn = _registry(jarvis)
    await reborn.async_load()
    assert reborn.get(task.id).status == STATUS_QUEUED


async def test_a_queued_task_nothing_has_any_more_is_failed_by_the_engine(jarvis):
    from jarvis.taskengine import TaskEngine

    registry = _registry(jarvis)
    task = await registry.async_add("orphaned")
    reborn = _registry(jarvis)
    await reborn.async_load()
    # No queue entry for it: whatever was going to run it is gone.
    TaskEngine(jarvis, reborn).load({"queue": []})
    assert reborn.get(task.id).status == STATUS_ERROR
    assert "nothing has it" in reborn.get(task.id).error


async def test_a_finished_task_is_restored_untouched(jarvis):
    registry = _registry(jarvis)
    task = await registry.async_add("Done thing")
    await registry.async_update(task.id, status=STATUS_DONE, result="42")
    reborn = _registry(jarvis)
    await reborn.async_load()
    restored = reborn.get(task.id)
    assert restored.status == STATUS_DONE
    assert restored.result == "42"


# --- bounds and hygiene -------------------------------------------------------

async def test_trimming_drops_finished_work_and_never_work_in_flight(jarvis):
    """The cap must not delete the thing somebody is watching.

    Trimming by age alone deletes the oldest task the moment the list fills —
    which is exactly when somebody is watching it.
    """
    registry = _registry(jarvis, max_tasks=3)
    live = await registry.async_add("still going")
    await registry.async_update(live.id, status=STATUS_RUNNING)
    for i in range(5):
        done = await registry.async_add(f"finished {i}")
        await registry.async_update(done.id, status=STATUS_DONE)

    assert registry.get(live.id) is not None, "a running task was trimmed for age"
    assert len(registry.tasks) <= 3


async def test_a_full_list_of_live_work_is_kept_rather_than_dropped(jarvis):
    registry = _registry(jarvis, max_tasks=2)
    for i in range(4):
        task = await registry.async_add(f"live {i}")
        await registry.async_update(task.id, status=STATUS_RUNNING)
    # The cap guards against unbounded growth; it is not a promise to enforce
    # a length by throwing away work in flight.
    assert len(registry.tasks) == 4
    assert all(not t.finished for t in registry.tasks)


async def test_listing_is_newest_first_and_filterable(jarvis):
    registry = _registry(jarvis)
    await registry.async_add("one", kind="research")
    two = await registry.async_add("two", kind="code")
    await registry.async_update(two.id, status=STATUS_DONE)
    await registry.async_add("three", kind="research")

    assert [t["title"] for t in registry.listing()] == ["three", "two", "one"]
    assert [t["title"] for t in registry.listing(kind="research")] == ["three", "one"]
    assert [t["title"] for t in registry.listing(active_only=True)] == ["three", "one"]


async def test_updating_a_task_that_is_not_there_is_not_an_error(jarvis):
    registry = _registry(jarvis)
    assert await registry.async_update("nope", status=STATUS_DONE) is None
    assert await registry.async_remove("nope") is False


async def test_clearing_finished_leaves_the_live_ones(jarvis):
    registry = _registry(jarvis)
    live = await registry.async_add("live")
    await registry.async_update(live.id, status=STATUS_RUNNING)
    gone = await registry.async_add("gone")
    await registry.async_update(gone.id, status=STATUS_DONE)

    assert await registry.async_clear_finished() == 1
    assert [t.id for t in registry.tasks] == [live.id]


async def test_steps_and_text_are_bounded(jarvis):
    registry = _registry(jarvis)
    task = await registry.async_add("x" * 5000, steps=[f"s{i}" for i in range(500)])
    assert len(task.title) <= 200
    assert len(task.steps) <= 100


async def test_a_listener_that_throws_cannot_break_the_work(jarvis):
    """A surface that has gone away must not fail the job it was watching.

    The bus guards listeners itself, so this is an integration assertion rather
    than a test of this module — which is the point: it pins that the registry
    goes THROUGH that guard rather than around it.
    """
    registry = _registry(jarvis)

    def explode(_event):
        raise RuntimeError("this listener is broken")

    jarvis.bus.listen(EVENT_TASK_UPDATED, explode)
    task = await registry.async_add("Job")
    updated = await registry.async_update(task.id, status=STATUS_DONE)
    assert updated.status == STATUS_DONE


async def test_a_registry_with_no_store_still_works(jarvis):
    """Used in tests and by anything that wants a purely in-memory registry."""
    registry = TaskRegistry(jarvis)
    task = await registry.async_add("Ephemeral")
    await registry.async_update(task.id, status=STATUS_DONE)
    await registry.async_load()  # a no-op rather than a crash
    assert registry.get(task.id).status == STATUS_DONE


def test_a_corrupt_record_is_skipped_rather_than_crashing_the_load():
    assert Task.from_dict(None) is None
    assert Task.from_dict({"id": "x"}) is None          # no title
    assert Task.from_dict({"title": "x"}) is None       # no id
    ok = Task.from_dict({"id": "a", "title": "t", "status": "nonsense"})
    assert ok is not None and ok.status == STATUS_QUEUED


async def test_a_task_can_stop_being_open_ended_once_it_knows_its_own_size(jarvis):
    """The transition a real worker makes, and the point of `open_ended`.

    A research run does not know how many pages it will read until it has
    searched. Until then a percentage is a guess; the moment the read list is
    settled it is a fact, and the bar should say so rather than staying vague
    for the whole run.
    """
    registry = _registry(jarvis)
    task = await registry.async_add("Research", steps=["plan"], open_ended=True)
    await registry.async_update(task.id, step=0, step_status=STATUS_DONE)
    assert task.fraction is None

    await registry.async_update(task.id, add_steps=["read a", "read b"], open_ended=False)
    assert task.fraction == pytest.approx(1 / 3)


async def test_a_task_that_finds_more_work_can_go_open_ended_again(jarvis):
    registry = _registry(jarvis)
    task = await registry.async_add("Crawl", steps=["a", "b"])
    assert task.fraction == 0
    await registry.async_update(task.id, open_ended=True)
    assert task.fraction is None
