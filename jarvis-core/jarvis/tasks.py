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
    "EVENT_TASK_OUTPUT",
    "EVENT_TASK_TOOL_FINISHED",
    "EVENT_TASK_TOOL_STARTED",
    "MAX_LOG_ENTRIES",
    "MAX_TASKS",
    "STATUS_BLOCKED",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_ERROR",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "Task",
    "TaskCancelled",
    "TaskLogEntry",
    "TaskRegistry",
    "TaskStep",
]

STORE_KEY = "tasks"

EVENT_TASK_ADDED = "jarvis_task_added"
EVENT_TASK_UPDATED = "jarvis_task_updated"
EVENT_TASK_REMOVED = "jarvis_task_removed"
#: A worker called a tool, and that call returned. Any worker — not only a chat
#: turn, which is where tool events used to begin and end: a coding job called
#: nine tools and the console learned about them when the job was over.
EVENT_TASK_TOOL_STARTED = "jarvis_task_tool_started"
EVENT_TASK_TOOL_FINISHED = "jarvis_task_tool_finished"
#: Output worth watching while it happens: a check's stdout, a command's log.
#: The contract in `tests/contracts/task_events.json` is what both sides read.
EVENT_TASK_OUTPUT = "jarvis_task_output"

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
#: One task's replayable history. Bounded because a job that ran forty rounds
#: would otherwise carry forty rounds of text in the file every task is saved
#: to. Old entries fall off the front: the tail is what somebody arriving late
#: wants to see.
MAX_LOG_ENTRIES = 200
#: One slice of streamed output. A worker sends what it has; this is the cap on
#: what any single frame may carry.
MAX_CHUNK_CHARS = 4000


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split()) if "\n" not in text else text
    return text[:limit]


class TaskCancelled(Exception):
    """Raised inside a worker when its task was cancelled or forgotten.

    Cancelling a task marks the record; it cannot reach into a coroutine and
    stop it. A worker calls :meth:`TaskRegistry.raise_if_cancelled` at the
    points where stopping is safe, and this is what it gets. Not an error: the
    worker unwinds, tidies up, and the task stays `cancelled` rather than
    becoming `error`.
    """


@dataclass
class TaskLogEntry:
    """One line of a task's replayable history.

    The events below are fire-and-forget: a client watching from the start sees
    every tool call and every line of output, and a client that opens the task
    detail page two minutes in sees nothing at all. So every event is also
    appended here, and `jarvis/tasks/log` replays it.
    """

    at: float
    #: "status" | "step" | "tool" | "output" | "note"
    kind: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at, "kind": self.kind, "text": self.text}

    @classmethod
    def from_dict(cls, raw: Any) -> "TaskLogEntry | None":
        if not isinstance(raw, dict):
            return None
        kind = _clip(raw.get("kind"), 16) or "note"
        text = _clip(raw.get("text"), MAX_CHUNK_CHARS)
        try:
            at = float(raw.get("at") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        return cls(at=at, kind=kind, text=text)


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
    #: Everything that happened, oldest first, capped at [MAX_LOG_ENTRIES].
    log: list[TaskLogEntry] = field(default_factory=list)
    #: Monotonic per task, so a client can tell a dropped frame from a
    #: reordered one. Not persisted: a restart ends the run anyway.
    seq: int = 0

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
            # The log is deliberately NOT here: the lifecycle events carry the
            # whole task on every update, and a 200-entry log on every frame is
            # a websocket carrying the same kilobytes forty times a minute.
            # `jarvis/tasks/log` fetches it once.
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
            log=[
                entry
                for entry in (TaskLogEntry.from_dict(e) for e in raw.get("log") or [])
                if entry is not None
            ][-MAX_LOG_ENTRIES:],
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
            await self.store.save(
                {"tasks": [{**t.as_dict(), "log": [e.as_dict() for e in t.log]} for t in self.tasks]}
            )
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

        if status is not None and status in STATUSES:
            self._log(task, "status", status)
        if step is not None and 0 <= step < len(task.steps) and step_status is not None:
            self._log(task, "step", f"{task.steps[step].title}: {step_status}")

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

    # --- watching one run ---------------------------------------------------
    #
    # Everything below is what a worker calls while it works, and what the task
    # detail page renders. Each fires an event AND appends to the task's own log,
    # because a client that opens the page two minutes in has missed every event
    # and must still be able to see what happened.

    def _log(self, task: Task, kind: str, text: str) -> None:
        task.log.append(TaskLogEntry(at=time.time(), kind=kind, text=_clip(text, MAX_CHUNK_CHARS)))
        if len(task.log) > MAX_LOG_ENTRIES:
            del task.log[: len(task.log) - MAX_LOG_ENTRIES]

    def raise_if_cancelled(self, task_id: str) -> None:
        """Stop, if somebody has cancelled this task or forgotten it.

        Cancelling marks the record — `api/common.py` says so plainly, and adds
        that "a worker that does not check may still be running". This is the
        check. Call it wherever unwinding is safe: between rounds, before a
        tool call, between pages. A task that has been removed outright counts
        as cancelled, because there is nothing left to report to.
        """
        task = self.get(task_id)
        if task is None:
            raise TaskCancelled(f"task {task_id} was forgotten")
        if task.status == STATUS_CANCELLED:
            raise TaskCancelled(f"task {task_id} was cancelled")

    def cancelled(self, task_id: str) -> bool:
        """The same question, without the exception, for a polling loop."""
        task = self.get(task_id)
        return task is None or task.status == STATUS_CANCELLED

    def tool_started(
        self,
        task_id: str,
        *,
        name: str,
        arguments: Any = None,
        call_id: str = "",
        index: int = 0,
        total: int = 0,
    ) -> str:
        """A worker is calling a tool. Returns the call id to finish it with."""
        task = self.get(task_id)
        if task is None:
            return ""
        task.seq += 1
        call = call_id or f"{task_id}-{task.seq}"
        self._log(task, "tool", f"{name} {_clip(arguments, 200)}".strip())
        self._fire_raw(
            EVENT_TASK_TOOL_STARTED,
            {
                "task_id": task_id,
                "call_id": call,
                "name": _clip(name, 80),
                "arguments": arguments if isinstance(arguments, (dict, list, str)) else {},
                "index": int(index),
                "total": int(total),
            },
        )
        return call

    def tool_finished(
        self,
        task_id: str,
        *,
        name: str,
        call_id: str = "",
        ok: bool = True,
        status: str = "",
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        task = self.get(task_id)
        if task is None:
            return
        self._log(task, "tool", f"{name} {'ok' if ok else 'failed'} {duration_ms} ms {error}".strip())
        self._fire_raw(
            EVENT_TASK_TOOL_FINISHED,
            {
                "task_id": task_id,
                "call_id": call_id,
                "name": _clip(name, 80),
                "ok": bool(ok),
                "status": _clip(status, 40) or ("ok" if ok else "error"),
                "error": _clip(error, MAX_DETAIL_CHARS),
                "duration_ms": int(duration_ms),
            },
        )

    def output(self, task_id: str, chunk: str, *, stream: str = "stdout") -> None:
        """A line (or several) of output, while it happens.

        This is the channel a check's stdout goes down. It is deliberately not
        the task's `detail`: detail is one line saying where the work has got
        to, and using it as a log made the last line the only line anybody saw.
        """
        task = self.get(task_id)
        if task is None or not chunk:
            return
        task.seq += 1
        text = _clip(chunk, MAX_CHUNK_CHARS)
        self._log(task, "output", text)
        self._fire_raw(
            EVENT_TASK_OUTPUT,
            {
                "task_id": task_id,
                "stream": stream if stream in ("stdout", "stderr", "note") else "stdout",
                "chunk": text,
                "seq": task.seq,
            },
        )

    def log_entries(self, task_id: str, *, limit: int = MAX_LOG_ENTRIES) -> list[dict[str, Any]]:
        """The task's replayable history, oldest first."""
        task = self.get(task_id)
        if task is None:
            return []
        return [entry.as_dict() for entry in task.log[-max(1, limit) :]]

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

    def _fire_raw(self, event: str, data: dict[str, Any]) -> None:
        """Fire an event whose payload is not the whole task.

        The lifecycle events carry `{"task": ...}` so a client can recover from
        any single frame. The watching events carry only what changed, because
        they arrive many times a second and the task is already on screen.
        """
        bus = getattr(self.jarvis, "bus", None)
        if bus is None:
            return
        try:
            bus.fire(event, data)
        except Exception:  # pragma: no cover - a listener must not break work
            _LOGGER.exception("Could not fire %s", event)
