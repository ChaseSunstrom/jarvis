"""The thing that actually runs the work.

`tasks.py` is the record: what is happening, how far it has got, what it printed.
It says so in its own docstring — *"it does not run anything"* — and for a while
nothing did. `run_background_task` minted an id, fired an event nothing listened
to, and told the model to say the result would arrive later. Jarvis Code and
research each grew their own `ensure_future`, unbounded and unaware of each
other, so three jobs at once meant three concurrent model conversations against
one server with one KV cache.

This is the missing half: a queue, a small pool of workers, retries with
backoff, and work that survives a restart.

## The shape

    engine.submit(task_id, worker)          # queued, runs when a slot is free
    engine.submit(..., retries=2)           # and again on failure, with backoff
    engine.submit(..., idempotent=True)     # and again after a restart

A worker is `async def worker(task_id: str) -> None`. It reports through the
registry it was given, calls `raise_if_cancelled` where stopping is safe, and
raises to fail. That is the whole contract: the engine owns *when* work runs and
*how often it is retried*, and knows nothing about what the work is.

## Why a limit at all

Every worker here eventually talks to one model server. Two coding jobs and a
research run at the same time are three conversations sharing one KV cache, and
the symptom is not an error — it is everything becoming four times slower at
once, which reads as "Jarvis is broken today". `llm.max_concurrent` is the cap;
the queue is what makes exceeding it impossible rather than merely discouraged.

## What survives a restart

The queue is persisted with the task list, so work that was *waiting* is still
waiting after a restart. Work that was *running* is not resumed unless it said
it was idempotent: re-running half of something that already half-happened is a
worse failure than reporting it stopped, and only the worker knows which it is.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .tasks import (
    STATUS_ERROR,
    STATUS_QUEUED,
    RESTART_ERROR,
    STATUS_RUNNING,
    TaskCancelled,
    TaskRegistry,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["TaskEngine", "QueuedWork", "Worker", "DEFAULT_MAX_CONCURRENT"]

#: How many jobs may run at once. Two, because every one of them ends up talking
#: to the same model server, and the third concurrent conversation does not fail
#: — it makes the other two slow enough to look broken.
DEFAULT_MAX_CONCURRENT = 2

#: How long the queue may get before submissions are refused. A refusal a person
#: can read beats an unbounded queue that quietly grows for an hour.
MAX_QUEUED = 100

#: Retry backoff: 2s, 8s, 32s, capped. Jittered, so three jobs failing on the
#: same dead model server do not retry in lockstep for ever.
RETRY_BASE_SECONDS = 2.0
RETRY_FACTOR = 4.0
RETRY_CAP_SECONDS = 120.0

Worker = Callable[[str], Awaitable[None]]


@dataclass
class QueuedWork:
    """One piece of work waiting for a slot."""

    task_id: str
    #: What kind of work, so a restart can find the right worker again.
    kind: str
    #: How many more times this may be retried after a failure.
    retries: int = 0
    #: Safe to run again from the beginning. Only then is it resumed after a
    #: restart that killed it mid-flight.
    idempotent: bool = False
    #: Arguments a restart needs to rebuild the worker.
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    queued_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "retries": self.retries,
            "idempotent": self.idempotent,
            "payload": self.payload,
            "attempts": self.attempts,
            "queued_at": self.queued_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "QueuedWork | None":
        if not isinstance(raw, dict) or not raw.get("task_id"):
            return None
        return cls(
            task_id=str(raw["task_id"]),
            kind=str(raw.get("kind") or "background"),
            retries=int(raw.get("retries") or 0),
            idempotent=bool(raw.get("idempotent")),
            payload=raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
            attempts=int(raw.get("attempts") or 0),
            queued_at=float(raw.get("queued_at") or time.time()),
        )


class TaskEngine:
    """A bounded queue and a small pool of workers, over the task registry."""

    def __init__(
        self,
        jarvis: Any,
        registry: TaskRegistry,
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self.jarvis = jarvis
        self.registry = registry
        self.max_concurrent = max(1, int(max_concurrent))
        self.queue: list[QueuedWork] = []
        #: task_id -> the coroutine running it.
        self.running: dict[str, asyncio.Task] = {}
        #: kind -> how to rebuild a worker for it after a restart.
        self.factories: dict[str, Callable[[QueuedWork], Worker]] = {}
        self._workers: dict[str, Worker] = {}
        self._wake = asyncio.Event()
        self._pump: asyncio.Task | None = None
        self._stopping = False

    # --- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.ensure_future(self._run())

    async def async_stop(self) -> None:
        """Stop taking work and let what is running finish or be cancelled."""
        self._stopping = True
        self._wake.set()
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
        for task in list(self.running.values()):
            task.cancel()
        self.running.clear()

    def register_kind(self, kind: str, factory: Callable[[QueuedWork], Worker]) -> None:
        """Teach the engine how to rebuild a worker of `kind` after a restart.

        Without this a resumed item has a description and no way to do it, which
        is the state the whole registry used to be in.
        """
        self.factories[kind] = factory

    # --- submitting ----------------------------------------------------------
    def submit(
        self,
        task_id: str,
        worker: Worker | None = None,
        *,
        kind: str = "background",
        retries: int = 0,
        idempotent: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Queue one piece of work. False when the queue is full."""
        if len(self.queue) >= MAX_QUEUED:
            _LOGGER.error("task engine: the queue is full (%d); refusing %s", MAX_QUEUED, task_id)
            return False
        if any(item.task_id == task_id for item in self.queue) or task_id in self.running:
            return True  # already ours; submitting twice is not an error
        item = QueuedWork(
            task_id=task_id,
            kind=kind,
            retries=max(0, int(retries)),
            idempotent=idempotent,
            payload=payload or {},
        )
        if worker is not None:
            self._workers[task_id] = worker
        self.queue.append(item)
        self._wake.set()
        self.start()
        return True

    def retry(self, task_id: str) -> bool:
        """Put a finished task back on the queue, by hand.

        The button somebody presses after fixing whatever broke. Only for work
        whose kind can be rebuilt — otherwise there is nothing to run.
        """
        task = self.registry.get(task_id)
        if task is None or not task.finished:
            return False
        if task.kind not in self.factories and task_id not in self._workers:
            return False
        return self.submit(
            task_id, self._workers.get(task_id), kind=task.kind, retries=0, idempotent=True
        )

    # --- persistence ---------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return {"queue": [item.as_dict() for item in self.queue]}

    def load(self, raw: Any) -> None:
        """Restore the queue, and fail anything left waiting for nobody.

        Called after the registry has loaded its tasks. `Task.restored` leaves a
        queued task queued, because the engine may still have it — this is the
        only place that knows whether it does, so this is where a task that was
        waiting for a queue that no longer mentions it is failed. Without that
        step a task would sit at "queued" for ever, which is the exact failure
        the restart marking exists to prevent.
        """
        items = (raw or {}).get("queue") if isinstance(raw, dict) else None
        for entry in items or []:
            item = QueuedWork.from_dict(entry)
            if item is None:
                continue
            task = self.registry.get(item.task_id)
            if task is None:
                continue
            # Finished work is not re-queued — except the one finish a restart
            # wrote: a job the registry errored as interrupted, whose worker
            # said it was idempotent, is kept so the block below can pick it up.
            interrupted = task.status == STATUS_ERROR and task.error == RESTART_ERROR
            if task.finished and not (interrupted and item.idempotent):
                continue
            self.queue.append(item)

        # Work that was RUNNING when the process died is errored by
        # `Task.restored` — honest, since nothing is driving it. When the queue
        # still has it and the worker said it was idempotent, the engine can
        # drive it again, so it goes back to queued and says so: "picked back up
        # after a restart" (M85). Four tasks on the house ended "interrupted
        # when Jarvis restarted" on 27 Aug 2026 with nothing to pick them up.
        for item in self.queue:
            task = self.registry.get(item.task_id)
            if (
                task is not None
                and item.idempotent
                and task.status == STATUS_ERROR
                and task.error == RESTART_ERROR
            ):
                task.status = STATUS_QUEUED
                task.error = ""
                task.detail = "picked back up after a restart"
                task.resumed = True
                for step in task.steps:
                    if step.status in (STATUS_RUNNING, STATUS_ERROR):
                        step.status = STATUS_QUEUED
                _LOGGER.info("task engine: picking %s back up after the restart", task.id)

        known = {item.task_id for item in self.queue}
        for task in self.registry.tasks:
            if task.status == STATUS_QUEUED and task.id not in known:
                task.status = STATUS_ERROR
                task.error = task.error or (
                    "was waiting when Jarvis stopped, and nothing has it now"
                )

    def resumable(self) -> list[QueuedWork]:
        """What a restart may pick up again.

        Queued work always; running work only when it said it was idempotent.
        Re-running half of something that already half-happened is a worse
        failure than reporting that it stopped, and only the worker knows which
        it is.
        """
        out: list[QueuedWork] = []
        for item in self.queue:
            task = self.registry.get(item.task_id)
            if task is None:
                continue
            if task.status == STATUS_QUEUED or (task.status == STATUS_RUNNING and item.idempotent):
                out.append(item)
        return out

    # --- the pump ------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stopping:
            started = self._start_ready()
            if not started:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
            else:
                await asyncio.sleep(0)

    def _start_ready(self) -> int:
        started = 0
        while self.queue and len(self.running) < self.max_concurrent:
            item = self.queue.pop(0)
            worker = self._workers.get(item.task_id)
            if worker is None:
                factory = self.factories.get(item.kind)
                worker = factory(item) if factory else None
            if worker is None:
                _LOGGER.error(
                    "task engine: nothing knows how to run %s (%s); marking it failed",
                    item.task_id,
                    item.kind,
                )
                asyncio.ensure_future(
                    self.registry.async_update(
                        item.task_id,
                        status=STATUS_ERROR,
                        error=(
                            f"this server has no worker for {item.kind!r} work, so it was "
                            "never started"
                        ),
                    )
                )
                continue
            self.running[item.task_id] = asyncio.ensure_future(self._drive(item, worker))
            started += 1
        return started

    async def _drive(self, item: QueuedWork, worker: Worker) -> None:
        """Run one piece of work, and decide what its failure means."""
        item.attempts += 1
        try:
            await self.registry.async_update(item.task_id, status=STATUS_RUNNING)
            await worker(item.task_id)
        except TaskCancelled:
            # Not an error: somebody asked it to stop and it did.
            _LOGGER.info("task engine: %s was cancelled", item.task_id)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception as err:
            await self._failed(item, worker, err)
        finally:
            self.running.pop(item.task_id, None)
            self._wake.set()

    async def _failed(self, item: QueuedWork, worker: Worker, err: Exception) -> None:
        reason = f"{type(err).__name__}: {err}"[:400]
        if item.retries > 0:
            item.retries -= 1
            delay = self.backoff(item.attempts)
            _LOGGER.warning(
                "task engine: %s failed (%s); retrying in %.0fs", item.task_id, reason, delay
            )
            self.registry.output(item.task_id, f"failed: {reason}\nretrying in {delay:.0f}s", stream="note")
            await self.registry.async_update(item.task_id, status=STATUS_QUEUED, detail="waiting to retry")
            await asyncio.sleep(delay)
            self._workers[item.task_id] = worker
            self.queue.append(item)
            self._wake.set()
            return
        _LOGGER.exception("task engine: %s failed for the last time", item.task_id)
        await self.registry.async_update(item.task_id, status=STATUS_ERROR, error=reason)

    @staticmethod
    def backoff(attempt: int) -> float:
        """2s, 8s, 32s, capped — jittered so failures do not retry in lockstep."""
        raw = RETRY_BASE_SECONDS * (RETRY_FACTOR ** max(0, attempt - 1))
        capped = min(RETRY_CAP_SECONDS, raw)
        return capped * (0.75 + random.random() * 0.5)

    async def async_drain(self, timeout: float = 10.0) -> bool:
        """Wait until the queue is empty and nothing is running.

        For tests and for shutdown. Returns False if it gave up: a drain that
        hangs is worse than one that says it could not finish.
        """
        self.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.queue and not self.running:
                return True
            self._wake.set()
            await asyncio.sleep(0.01)
        return False

    # --- what a client sees --------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "queued": len(self.queue),
            "running": len(self.running),
            "max_concurrent": self.max_concurrent,
            "waiting": [item.task_id for item in self.queue],
        }
