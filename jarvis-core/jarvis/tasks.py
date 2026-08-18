"""Long work, tracked: the one job registry everything slow reports through.

## Why this exists

`run_background_task` was an empty seam. It minted an id, fired
``jarvis_background_task`` onto the bus, and told the model *"Accepted.
Acknowledge briefly now; the result arrives later."* Nothing ran it. Nothing
tracked it. No result ever arrived, and grepping the whole repo for that event
name returns the line that defines it and the line that fires it — and no
listener, on any surface, ever.

So the model was instructed to promise the user something the system could not
do. That is worse than not having the feature: "I'll see to it, Sir" followed
by silence is indistinguishable from a bug, and the user has no way to find out
which it was.

This is the missing half. It is deliberately not specific to any one kind of
work, because four separate things need exactly the same shape:

* a background task the model accepted, and the user later asks about;
* a research job that reads twelve pages and takes a minute;
* something scheduled for later, which must survive a restart to happen at all;
* a coding job, which is a long task with steps, output and a result.

Each of those wants: an id, a title a person can read, a status, *where it has
got to*, and a durable record that outlives the process. One registry, four
callers, one progress bar on every surface.

## Progress is steps, not a percentage

A percentage is a guess in almost every case here, and a wrong percentage is
worse than none — a bar that sits at 90% for four minutes teaches people to
ignore bars. So a task carries an ordered list of STEPS, each with its own
state, and the fraction is derived: done steps over total steps. When the total
is not known ahead of time (a crawl that discovers pages), steps are appended
as they are discovered and the bar is honest about being indeterminate rather
than inventing a denominator.

## What this module does NOT do

It does not run anything. It is a record and a notification, and the thing
doing the work drives it. That keeps it testable without a scheduler, and it
means a crashed worker leaves a task visibly stuck in `running` rather than
silently marked done — which is the honest failure and the one a user can act
on.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "EVENT_TASK_ADDED",
    "EVENT_TASK_UPDATED",
    "EVENT_TASK_REMOVED",
    "MAX_TASKS",
    "STATUS_BLOCKED",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_ERROR",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "Task",
    "TaskRegistry",
    "TaskStep",
]

STORE_KEY = "tasks"

EVENT_TASK_ADDED = "jarvis_task_added"
EVENT_TASK_UPDATED = "jarvis_task_updated"
EVENT_TASK_REMOVED = "jarvis_task_removed"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
#: Waiting on a person — an approval, an answer. Distinct from `running`
#: because "it is working" and "it is waiting for you" are different things to
#: show, and conflating them is how an approval prompt goes unnoticed.
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

TERMINAL = frozenset({STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED})
STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_CANCELLED,
)

#: Kept, oldest finished first out. Generous — a task is a few hundred bytes —
#: and bounded because the input is "however much the user asks for".
MAX_TASKS = 200

MAX_TITLE_CHARS = 200
MAX_DETAIL_CHARS = 2000
MAX_STEPS = 100
#: A result is a summary for a person, not a payload. Anything large belongs
#: behind a URL or in the store the task wrote to.
MAX_RESULT_CHARS = 8000


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split()) if "\n" not in text else text
    return text[:limit]


@dataclass
class TaskStep:
    """One unit of a task's progress, in the order it will be attempted."""

    title: str
    status: str = STATUS_QUEUED
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw: Any) -> "TaskStep | None":
        if not isinstance(raw, dict):
            return None
        title = _clip(raw.get("title"), MAX_TITLE_CHARS)
        if not title:
            return None
        status = str(raw.get("status") or STATUS_QUEUED)
        return cls(
            title=title,
            status=status if status in STATUSES else STATUS_QUEUED,
            detail=_clip(raw.get("detail"), MAX_DETAIL_CHARS),
        )


@dataclass
class Task:
    """A piece of work slow enough that somebody might ask about it."""

    id: str
    #: What sort of work: "background", "research", "code", "scheduled". Free
    #: text on purpose — a new kind of work must not need this file edited.
    kind: str
    title: str
    status: str = STATUS_QUEUED
    steps: list[TaskStep] = field(default_factory=list)
    detail: str = ""
    result: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    #: Where the work came from, so a surface can show "you asked for this" as
    #: distinct from "an automation did".
    source: str = ""
    #: True while more steps may still be appended, so a bar can say
    #: "indeterminate" instead of inventing a denominator.
    open_ended: bool = False

    # --- derived ----------------------------------------------------------
    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    @property
    def done_steps(self) -> int:
        return sum(1 for s in self.steps if s.status in TERMINAL)

    @property
    def fraction(self) -> float | None:
        """0..1, or None when a number would be a guess.

        None for a task with no steps and for one still discovering them: a bar
        that sits at 90% for four minutes teaches people to ignore bars.
        """
        if self.status == STATUS_DONE:
            return 1.0
        if not self.steps or self.open_ended:
            return None
        return self.done_steps / len(self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "steps": [s.as_dict() for s in self.steps],
            "detail": self.detail,
            "result": self.result,
            "error": self.error,
            "created": self.created,
            "updated": self.updated,
            "source": self.source,
            "open_ended": self.open_ended,
            # Derived, and sent rather than recomputed on four clients that
            # would each get the open-ended case subtly different.
            "fraction": self.fraction,
            "done_steps": self.done_steps,
            "total_steps": len(self.steps),
            "finished": self.finished,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Task | None":
        if not isinstance(raw, dict):
            return None
        task_id = _clip(raw.get("id"), 64)
        title = _clip(raw.get("title"), MAX_TITLE_CHARS)
        if not task_id or not title:
            return None
        status = str(raw.get("status") or STATUS_QUEUED)
        steps = [
            step
            for step in (TaskStep.from_dict(s) for s in raw.get("steps") or [])
            if step is not None
        ][:MAX_STEPS]
        task = cls(
            id=task_id,
            kind=_clip(raw.get("kind"), 40) or "background",
            title=title,
            status=status if status in STATUSES else STATUS_QUEUED,
            steps=steps,
            detail=_clip(raw.get("detail"), MAX_DETAIL_CHARS),
            result=_clip(raw.get("result"), MAX_RESULT_CHARS),
            error=_clip(raw.get("error"), MAX_DETAIL_CHARS),
            source=_clip(raw.get("source"), 80),
            open_ended=bool(raw.get("open_ended")),
        )
        for key in ("created", "updated"):
            try:
                setattr(task, key, float(raw.get(key) or time.time()))
            except (TypeError, ValueError):
                pass
        return task

    def restored(self) -> "Task":
        """What this task becomes when the process that was running it died.

        A task left `running` or `queued` in the store did NOT survive — the
        thing driving it is gone. Marking it errored on load is the honest
        answer and the actionable one: the alternative is a task that sits at
        "running" for ever and a user who cannot tell a slow job from a dead
        one. `blocked` is left alone: it was waiting on a person, and it still
        is.
        """
        if self.status in (STATUS_QUEUED, STATUS_RUNNING):
            self.status = STATUS_ERROR
            self.error = self.error or "interrupted when Jarvis restarted"
            for step in self.steps:
                if step.status == STATUS_RUNNING:
                    step.status = STATUS_ERROR
        return self


class TaskRegistry:
    """Every long job, and who to tell when one moves.

    Firing is best-effort and never raises into a caller: a surface that has
    gone away must not be able to fail the work it was watching.
    """

    def __init__(self, jarvis: Any = None, store: Any = None, max_tasks: int = MAX_TASKS):
        self.jarvis = jarvis
        self.store = store
        self.max_tasks = max(1, int(max_tasks))
        self.tasks: list[Task] = []

    # --- persistence ------------------------------------------------------
    async def async_load(self) -> None:
        if self.store is None:
            return
        data = await self.store.load()
        raw = (data or {}).get("tasks") if isinstance(data, dict) else None
        loaded = [t for t in (Task.from_dict(r) for r in raw or []) if t is not None]
        loaded.sort(key=lambda t: t.created)
        self.tasks = [t.restored() for t in loaded[-self.max_tasks :]]

    async def async_save(self) -> None:
        if self.store is None:
            return
        try:
            await self.store.save({"tasks": [t.as_dict() for t in self.tasks]})
        except Exception:  # pragma: no cover - a full disk is not a task failure
            _LOGGER.exception("Could not save the task list")

    # --- reading ----------------------------------------------------------
    def get(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def listing(self, *, kind: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
        """Newest first, which is the order every surface wants."""
        out = [
            task
            for task in reversed(self.tasks)
            if (kind is None or task.kind == kind) and (not active_only or not task.finished)
        ]
        return [t.as_dict() for t in out]

    @property
    def active(self) -> list[Task]:
        return [t for t in self.tasks if not t.finished]

    # --- writing ----------------------------------------------------------
    async def async_add(
        self,
        title: str,
        *,
        kind: str = "background",
        steps: Iterable[str] = (),
        source: str = "",
        detail: str = "",
        open_ended: bool = False,
        task_id: str | None = None,
    ) -> Task:
        task = Task(
            id=task_id or uuid.uuid4().hex[:12],
            kind=_clip(kind, 40) or "background",
            title=_clip(title, MAX_TITLE_CHARS) or "untitled task",
            steps=[TaskStep(title=_clip(s, MAX_TITLE_CHARS)) for s in steps][:MAX_STEPS],
            detail=_clip(detail, MAX_DETAIL_CHARS),
            source=_clip(source, 80),
            open_ended=bool(open_ended),
        )
        self.tasks.append(task)
        self._trim()
        await self.async_save()
        self._fire(EVENT_TASK_ADDED, task)
        return task

    async def async_update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail: str | None = None,
        result: str | None = None,
        error: str | None = None,
        step: int | None = None,
        step_status: str | None = None,
        step_detail: str | None = None,
        add_steps: Iterable[str] = (),
        open_ended: bool | None = None,
    ) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None

        for title in add_steps:
            if len(task.steps) >= MAX_STEPS:
                break
            task.steps.append(TaskStep(title=_clip(title, MAX_TITLE_CHARS)))

        if step is not None and 0 <= step < len(task.steps):
            if step_status is not None and step_status in STATUSES:
                task.steps[step].status = step_status
            if step_detail is not None:
                task.steps[step].detail = _clip(step_detail, MAX_DETAIL_CHARS)

        # A run that has finished discovering its work knows its own total, so
        # a bar that was honestly indeterminate can become an honest number.
        # The transition only runs one way in practice, but both are allowed:
        # a crawl that finds more work to do is entitled to say so.
        if open_ended is not None:
            task.open_ended = bool(open_ended)

        if status is not None and status in STATUSES:
            task.status = status
        if detail is not None:
            task.detail = _clip(detail, MAX_DETAIL_CHARS)
        if result is not None:
            task.result = _clip(result, MAX_RESULT_CHARS)
        if error is not None:
            task.error = _clip(error, MAX_DETAIL_CHARS)
            # An error is a finish. Leaving the status alone here produced a
            # task carrying a reason it failed while still claiming to run.
            if task.status not in TERMINAL:
                task.status = STATUS_ERROR

        # Finishing closes out the steps. A task that says `done` above a step
        # still marked `running` is a UI that contradicts itself.
        if task.status == STATUS_DONE:
            for pending in task.steps:
                if pending.status not in TERMINAL:
                    pending.status = STATUS_DONE

        task.updated = time.time()
        await self.async_save()
        self._fire(EVENT_TASK_UPDATED, task)
        return task

    async def async_remove(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        self.tasks.remove(task)
        await self.async_save()
        self._fire(EVENT_TASK_REMOVED, task)
        return True

    async def async_clear_finished(self) -> int:
        finished = [t for t in self.tasks if t.finished]
        for task in finished:
            self.tasks.remove(task)
        if finished:
            await self.async_save()
            for task in finished:
                self._fire(EVENT_TASK_REMOVED, task)
        return len(finished)

    # --- internals --------------------------------------------------------
    def _trim(self) -> None:
        """Oldest FINISHED first. A running task is never dropped for age.

        Trimming by age alone would delete the thing somebody is watching the
        moment the list filled up, which is precisely when they are watching.
        """
        while len(self.tasks) > self.max_tasks:
            victim = next((t for t in self.tasks if t.finished), None)
            if victim is None:
                # Everything is live. Refusing to trim is better than dropping
                # work in flight; the cap is a guard against unbounded growth,
                # not a promise to enforce a length at any cost.
                return
            self.tasks.remove(victim)

    def _fire(self, event: str, task: Task) -> None:
        bus = getattr(self.jarvis, "bus", None)
        if bus is None:
            return
        try:
            # `fire`, not `async_fire`: this is called from ordinary update
            # paths and must not make every one of them a coroutine that waits
            # on however slow the listeners are. The bus schedules coroutine
            # listeners itself and already swallows a listener that raises —
            # this guard is for the bus being absent or wedged, not for the
            # listeners, which it protects on its own.
            bus.fire(event, {"task": task.as_dict()})
        except Exception:  # pragma: no cover - a listener must not break work
            _LOGGER.exception("Could not fire %s", event)
