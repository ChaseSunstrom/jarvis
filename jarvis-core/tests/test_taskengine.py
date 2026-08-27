"""The thing that actually runs the work.

`tasks.py` says in its own docstring that it "does not run anything", and for a
while nothing did: `run_background_task` minted an id and fired an event nobody
listened to, while Jarvis Code and research each grew their own unbounded
`ensure_future`. Three jobs at once meant three conversations against one model
server with one KV cache, and the symptom was not an error — it was everything
becoming four times slower at once.

What is pinned here: the queue is bounded and the cap is real, a failure is
retried with backoff rather than lost, cancellation is not a failure, and work
that was waiting when the process died is still waiting afterwards.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.taskengine import MAX_QUEUED, QueuedWork, TaskEngine
from jarvis.tasks import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_QUEUED,
    TaskRegistry,
)


class FakeBus:
    def fire(self, event, data):
        pass


class FakeJarvis:
    def __init__(self) -> None:
        self.bus = FakeBus()


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict = {}

    async def load(self):
        return self.data

    async def save(self, data):
        self.data = data


@pytest.fixture
def registry() -> TaskRegistry:
    jarvis = FakeJarvis()
    reg = TaskRegistry(jarvis, store=MemoryStore())
    jarvis.tasks = reg
    return reg


@pytest.fixture
async def engine(registry):
    """An engine, stopped at the end of the test.

    Without the teardown its pump outlives the test and the event loop it was
    started on, which is a hang rather than a failure — and a hang in a suite is
    a suite nobody runs.
    """
    made = TaskEngine(registry.jarvis, registry, max_concurrent=2)
    registry.jarvis.taskengine = made
    yield made
    await made.async_stop()


# --- the queue ---------------------------------------------------------------


async def test_work_submitted_is_work_run(registry, engine):
    task = await registry.async_add("do the thing")
    ran = asyncio.Event()

    async def worker(task_id: str) -> None:
        assert task_id == task.id
        ran.set()

    assert engine.submit(task.id, worker)
    assert await engine.async_drain(timeout=5)
    assert ran.is_set()


async def test_no_more_than_the_cap_run_at_once(registry, engine):
    """The whole reason the queue exists: every worker ends up talking to one
    model server, and the third concurrent conversation makes the other two slow
    enough to look broken."""
    live = 0
    peak = 0
    release = asyncio.Event()

    async def worker(_task_id: str) -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await release.wait()
        live -= 1

    for _ in range(6):
        task = await registry.async_add("job")
        engine.submit(task.id, worker)

    engine.start()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(engine.running) >= engine.max_concurrent:
            break
    assert peak <= engine.max_concurrent, f"{peak} ran at once, cap is {engine.max_concurrent}"
    release.set()
    assert await engine.async_drain(timeout=5)
    assert peak == engine.max_concurrent, "the cap was never actually reached"


async def test_a_full_queue_refuses_rather_than_growing_for_ever(registry, engine):
    async def worker(_task_id: str) -> None:
        await asyncio.sleep(10)

    for i in range(MAX_QUEUED):
        engine.queue.append(QueuedWork(task_id=f"filler-{i}", kind="background"))
    task = await registry.async_add("one too many")
    assert engine.submit(task.id, worker) is False


async def test_submitting_the_same_task_twice_is_not_an_error(registry, engine):
    task = await registry.async_add("job")

    async def worker(_task_id: str) -> None:
        pass

    assert engine.submit(task.id, worker)
    assert engine.submit(task.id, worker)
    assert len([item for item in engine.queue if item.task_id == task.id]) == 1


# --- failure, retry, backoff -------------------------------------------------


async def test_a_failure_is_retried_and_then_reported(registry, engine, monkeypatch):
    monkeypatch.setattr(TaskEngine, "backoff", staticmethod(lambda attempt: 0.01))
    attempts = 0

    async def worker(_task_id: str) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("the model server refused")

    task = await registry.async_add("flaky")
    engine.submit(task.id, worker, retries=2)
    assert await engine.async_drain(timeout=5)

    assert attempts == 3, "one attempt plus two retries"
    assert registry.get(task.id).status == STATUS_ERROR
    assert "refused" in registry.get(task.id).error


async def test_a_retry_that_works_leaves_the_task_alone(registry, engine, monkeypatch):
    monkeypatch.setattr(TaskEngine, "backoff", staticmethod(lambda attempt: 0.01))
    attempts = 0

    async def worker(task_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("not yet")
        await registry.async_update(task_id, status=STATUS_DONE)

    task = await registry.async_add("flaky")
    engine.submit(task.id, worker, retries=1)
    assert await engine.async_drain(timeout=5)
    assert registry.get(task.id).status == STATUS_DONE


def test_the_backoff_grows_and_is_capped_and_jittered():
    first = [TaskEngine.backoff(1) for _ in range(20)]
    third = [TaskEngine.backoff(3) for _ in range(20)]
    assert min(third) > max(first), "backoff does not grow"
    assert max(TaskEngine.backoff(20) for _ in range(20)) <= 200, "backoff is not capped"
    # Jitter: three jobs failing against the same dead server must not retry in
    # lockstep for ever.
    assert len(set(first)) > 1, "no jitter"


async def test_work_with_no_worker_and_no_factory_fails_loudly(registry, engine):
    task = await registry.async_add("orphan", kind="nonsense")
    engine.queue.append(QueuedWork(task_id=task.id, kind="nonsense"))
    engine.start()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if registry.get(task.id).status == STATUS_ERROR:
            break
    assert registry.get(task.id).status == STATUS_ERROR
    assert "no worker" in registry.get(task.id).error


# --- cancellation ------------------------------------------------------------


async def test_cancelling_is_not_a_failure(registry, engine):
    started = asyncio.Event()

    async def worker(task_id: str) -> None:
        started.set()
        for _ in range(200):
            registry.raise_if_cancelled(task_id)
            await asyncio.sleep(0.01)

    task = await registry.async_add("long")
    engine.submit(task.id, worker)
    engine.start()
    await asyncio.wait_for(started.wait(), timeout=5)
    await registry.async_update(task.id, status=STATUS_CANCELLED)
    assert await engine.async_drain(timeout=5)

    # Cancelled, not error: somebody asked it to stop and it did.
    assert registry.get(task.id).status == STATUS_CANCELLED


async def test_a_worker_that_ignores_cancellation_still_ends(registry, engine):
    """The registry cannot reach into a coroutine; `async_stop` can."""

    async def worker(_task_id: str) -> None:
        await asyncio.sleep(30)

    task = await registry.async_add("stubborn")
    engine.submit(task.id, worker)
    engine.start()
    await asyncio.sleep(0.05)
    await engine.async_stop()
    assert engine.running == {}


# --- surviving a restart -----------------------------------------------------


async def test_queued_work_is_still_queued_after_a_restart(registry, engine):
    task = await registry.async_add("waiting")

    async def worker(_task_id: str) -> None:
        pass

    # Read the queue before the pump can pick it up: what is being tested is
    # that a WAITING item survives, and a drained one is a different question.
    engine._stopping = True
    engine.submit(task.id, worker, kind="research")
    saved = engine.as_dict()
    assert saved["queue"], "nothing was queued to survive"

    second_registry = TaskRegistry(FakeJarvis(), store=registry.store)
    await second_registry.async_load()
    assert [t.id for t in second_registry.tasks] == [task.id], "the task did not survive"
    second = TaskEngine(second_registry.jarvis, second_registry)
    second.load(saved)
    assert [item.task_id for item in second.queue] == [task.id]
    assert [item.task_id for item in second.resumable()] == [task.id]


async def test_running_work_is_not_resumed_unless_it_said_it_was_idempotent(registry, engine):
    """Re-running half of something that already half-happened is worse than
    reporting that it stopped, and only the worker knows which it is."""
    risky = await registry.async_add("half-done")
    safe = await registry.async_add("re-runnable")
    await registry.async_update(risky.id, status="running")
    await registry.async_update(safe.id, status="running")

    engine.queue.append(QueuedWork(task_id=risky.id, kind="code", idempotent=False))
    engine.queue.append(QueuedWork(task_id=safe.id, kind="research", idempotent=True))

    resumable = [item.task_id for item in engine.resumable()]
    assert safe.id in resumable
    assert risky.id not in resumable


async def test_work_that_was_running_when_the_process_died_is_in_the_store_to_pick_up(registry, engine):
    """The shape the test above never had: the engine's OWN queue, with the job
    started. `_start_ready` popped a started item off the queue, and the store
    only ever held the queue — so a job running at the moment of a restart
    was the one job the store did not mention, and `load` had nothing to pick
    back up. On two houses task-survives-a-restart read "interrupted when
    Jarvis restarted" and the audit was started again by hand (27 Aug 2026).
    """
    from jarvis.tasks import RESTART_ERROR

    task = await registry.async_add("read every light", kind="background")
    hold = asyncio.Event()

    async def worker(task_id: str) -> None:
        await hold.wait()

    assert engine.submit(task.id, worker, kind="background", idempotent=True,
                         payload={"description": "read every light"})
    for _ in range(50):
        await asyncio.sleep(0.01)
        if task.id in engine.running:
            break
    assert task.id in engine.running and not engine.queue, "the job is running, the queue is empty"
    saved = engine.as_dict()
    assert [i["task_id"] for i in saved["queue"]] == [task.id], "the running job is in what the store holds"
    await registry.async_save()

    # The process dies with the job mid-flight, and comes back.
    engine._stopping = True
    second_registry = TaskRegistry(FakeJarvis(), store=registry.store)
    await second_registry.async_load()
    assert second_registry.get(task.id).error == RESTART_ERROR
    second = TaskEngine(second_registry.jarvis, second_registry, max_concurrent=2)
    second_registry.jarvis.taskengine = second
    ran: list[str] = []

    def factory(item: QueuedWork):
        async def again(task_id: str) -> None:
            ran.append(item.payload["description"])
            await second_registry.async_update(task_id, status=STATUS_DONE, result="done")
        return again

    second.register_kind("background", factory)
    second.load(saved)
    picked = second_registry.get(task.id)
    assert picked.status == "queued" and picked.resumed is True, picked.as_dict()
    second.start()
    try:
        assert await second.async_drain(timeout=5)
    finally:
        await second.async_stop()
    assert ran == ["read every light"]
    assert second_registry.get(task.id).status == STATUS_DONE
    hold.set()


async def test_a_resumable_job_is_picked_back_up_after_a_restart_and_says_so(registry, engine):
    """M85. Four background tasks on the house ended "interrupted when Jarvis
    restarted" on 27 Aug 2026: the registry errored them honestly, and the
    engine — with no factory registered and nothing idempotent — had nothing to
    pick up. A job whose worker said it was idempotent, and whose kind the
    engine can rebuild, goes back to queued, runs, and the task says it was
    picked back up. One that did not say so stays errored."""
    from jarvis.tasks import RESTART_ERROR

    safe = await registry.async_add("audit every sensor", kind="background")
    risky = await registry.async_add("half-done deploy", kind="code")
    await registry.async_update(safe.id, status="running", add_steps=["list the sensors", "read each one"])
    await registry.async_update(safe.id, step=0, step_status="done")
    await registry.async_update(safe.id, step=1, step_status="running")
    await registry.async_update(risky.id, status="running")
    engine._stopping = True
    engine.queue.append(QueuedWork(task_id=safe.id, kind="background", idempotent=True,
                                   payload={"description": "audit every sensor"}))
    engine.queue.append(QueuedWork(task_id=risky.id, kind="code", idempotent=False))
    await registry.async_save()
    saved = engine.as_dict()

    # The process dies and comes back.
    second_registry = TaskRegistry(FakeJarvis(), store=registry.store)
    await second_registry.async_load()
    restored = {t.id: t for t in second_registry.tasks}
    assert restored[safe.id].status == STATUS_ERROR and restored[safe.id].error == RESTART_ERROR
    assert restored[risky.id].status == STATUS_ERROR

    ran: list[str] = []
    second = TaskEngine(second_registry.jarvis, second_registry, max_concurrent=2)
    second_registry.jarvis.taskengine = second

    def factory(item: QueuedWork):
        async def worker(task_id: str) -> None:
            ran.append(item.payload["description"])
            await second_registry.async_update(task_id, status=STATUS_DONE, result="all fine")
        return worker

    second.register_kind("background", factory)
    second.load(saved)

    picked = second_registry.get(safe.id)
    assert picked.status == "queued" and picked.error == "" and picked.resumed is True
    assert picked.detail == "picked back up after a restart"
    assert [s.status for s in picked.steps] == ["done", "queued"], "the done step is kept, the dead one re-queued"
    assert second_registry.get(risky.id).status == STATUS_ERROR, "a non-idempotent job stays errored"
    assert second_registry.get(risky.id).resumed is False

    second.start()
    try:
        assert await second.async_drain(timeout=5)
    finally:
        await second.async_stop()
    assert ran == ["audit every sensor"], "the rebuilt worker did not run"
    assert second_registry.get(safe.id).status == STATUS_DONE
    assert second_registry.get(safe.id).as_dict()["resumed"] is True


async def test_a_queue_entry_for_a_task_that_is_gone_is_dropped(registry, engine):
    engine.load({"queue": [{"task_id": "vanished", "kind": "background"}]})
    assert engine.queue == []


async def test_retry_puts_a_finished_task_back(registry, engine):
    task = await registry.async_add("failed once", kind="research")

    async def worker(task_id: str) -> None:
        await registry.async_update(task_id, status=STATUS_DONE)

    engine.submit(task.id, worker)
    assert await engine.async_drain(timeout=5)
    await registry.async_update(task.id, status=STATUS_ERROR, error="boom")

    assert engine.retry(task.id) is True
    assert await engine.async_drain(timeout=5)
    assert registry.get(task.id).status == STATUS_DONE


async def test_retry_refuses_work_it_cannot_rebuild(registry, engine):
    task = await registry.async_add("unknown kind", kind="mystery")
    await registry.async_update(task.id, status=STATUS_ERROR)
    engine._workers.clear()
    assert engine.retry(task.id) is False


async def test_status_says_what_is_waiting(registry, engine):
    task = await registry.async_add("job")

    async def worker(_task_id: str) -> None:
        await asyncio.sleep(5)

    engine.submit(task.id, worker)
    status = engine.status()
    assert status["queued"] == 1
    assert status["max_concurrent"] == 2
    assert task.id in status["waiting"]
    assert registry.get(task.id).status == STATUS_QUEUED
