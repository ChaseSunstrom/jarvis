"""What the house did, while a scenario was talking to it.

A scenario asserts on consequences — a light that is on, a service that was
called, a task that finished, a note that exists — and every one of those has
to be read from the running server rather than from what the reply claimed.
That distinction is the entire value of this suite: an assistant that says "the
hall light is on" while the hall light is off passes every text-similarity check
ever written.

The observer subscribes once and keeps everything, so a turn can be judged
after it finished rather than raced while it runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from testing.live import LiveError


@dataclass
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = 0.0

    @property
    def entity_ids(self) -> list[str]:
        target = self.data.get("entity_id") or (self.data.get("target") or {}).get("entity_id")
        if isinstance(target, str):
            return [target]
        if isinstance(target, (list, tuple)):
            return [str(item) for item in target]
        return []


class Observer:
    """Everything the server said happened, in order."""

    def __init__(self, client) -> None:
        self.client = client
        self.calls: list[ServiceCall] = []
        #: Every tool the model called, in order. Routing accuracy is measured
        #: from these rather than asked of the model: "which capability did it
        #: use" is a question about what happened, and a model reporting its
        #: own choice is a model grading its own homework.
        self.tools: list[str] = []
        #: Every action held for a human, in order. `{"request_id", "kind",
        #: "tool", "summary", "task_id"}` — enough to answer it and enough to
        #: assert on what was asked.
        self.approvals: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._streams: list[Any] = []
        self._pumps: list[asyncio.Task] = []

    async def start(self, event_types: tuple[str, ...] = (
        "call_service",
        "jarvis_task_added",
        "jarvis_task_started",
        "jarvis_task_completed",
        "jarvis_task_failed",
        "jarvis_task_cancelled",
        # `required`, not `requested`. The rig subscribed to an event nothing
        # fires for months: every approval assertion would have waited its full
        # timeout and then reported that nothing was ever held.
        "jarvis_approval_required",
        "jarvis_approval_resolved",
        "jarvis_tool_started",
    )) -> "Observer":
        for event_type in event_types:
            try:
                stream = await self.client.subscribe_events(event_type)
            except Exception:  # noqa: BLE001 - an event this build does not have yet
                continue
            self._streams.append(stream)
            self._pumps.append(asyncio.create_task(self._pump(stream)))
        return self

    async def _pump(self, stream) -> None:
        while True:
            try:
                event = await stream.next(timeout=3600)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the socket closing ends the pump
                return
            self.events.append(event)
            kind = event.get("event_type") or event.get("type")
            if kind == "jarvis_tool_started":
                name = str((event.get("data") or {}).get("name") or "")
                if name:
                    self.tools.append(name)
            if kind == "jarvis_approval_required":
                data = event.get("data") or {}
                self.approvals.append(
                    {
                        "request_id": str(data.get("request_id") or ""),
                        "kind": str(data.get("kind") or ""),
                        "tool": str(data.get("tool") or ""),
                        "summary": str(data.get("summary") or data.get("description") or ""),
                        "task_id": str(data.get("task_id") or ""),
                    }
                )
            if kind == "call_service":
                data = event.get("data") or {}
                self.calls.append(
                    ServiceCall(
                        domain=str(data.get("domain") or ""),
                        service=str(data.get("service") or ""),
                        data=dict(data.get("service_data") or data.get("data") or {}),
                        at=time.monotonic(),
                    )
                )

    async def stop(self) -> None:
        for pump in self._pumps:
            pump.cancel()
        for pump in self._pumps:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
        for stream in self._streams:
            with contextlib.suppress(Exception):
                await stream.unsubscribe()
        self._pumps.clear()
        self._streams.clear()

    # --- reading it back ---------------------------------------------------
    def mark(self) -> int:
        """A cursor, so a turn only ever asserts on its own calls."""
        return len(self.calls)

    def tool_mark(self) -> int:
        return len(self.tools)

    def tools_since(self, mark: int) -> list[str]:
        return self.tools[mark:]

    def calls_since(self, mark: int) -> list[ServiceCall]:
        return self.calls[mark:]

    def events_since(self, mark: int) -> list[dict[str, Any]]:
        return self.events[mark:]

    def event_mark(self) -> int:
        return len(self.events)

    def called(self, mark: int, domain: str, service: str = "",
               entity_id: str = "") -> ServiceCall | None:
        for call in self.calls_since(mark):
            if call.domain != domain:
                continue
            if service and call.service != service:
                continue
            if entity_id and entity_id not in call.entity_ids:
                continue
            return call
        return None

    async def state_of(self, entity_id: str) -> str:
        try:
            state = await self.client.state(entity_id)
        except Exception:  # noqa: BLE001 - a missing entity is a failed assertion
            return ""
        return str((state or {}).get("state") or "")

    async def wait_for_state(self, entity_id: str, want: str, timeout: float = 15.0) -> bool:
        """`want` is a state, or ``absent`` for an entity the house no longer has.

        "absent" is what a removal (M69) leaves behind: `state_of` answers ""
        for an entity the API cannot find, which is also what an entity with
        an empty state answers — so the scenario says the word and this maps
        it, rather than a scenario asserting on "" and reading as a typo.
        """
        want = "" if want == "absent" else want
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.state_of(entity_id) == want:
                return True
            await asyncio.sleep(0.2)
        return False

    def approval_mark(self) -> int:
        return len(self.approvals)

    async def wait_for_approval(
        self, mark: int = 0, kind: str = "", tool: str = "", timeout: float = 240.0
    ) -> dict[str, Any] | None:
        """The next held action after `mark`, or None if nobody asked."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for row in self.approvals[mark:]:
                if kind and row.get("kind") != kind:
                    continue
                if tool and tool not in row.get("tool", ""):
                    continue
                return row
            await asyncio.sleep(0.5)
        return None

    async def answer(self, request_id: str, approved: bool) -> bool:
        """Do what a person does in the console: say yes or no."""
        try:
            await self.client.command(
                "jarvis/approve", request_id=request_id, approved=approved
            )
            return True
        except Exception:  # noqa: BLE001 - an already-resolved request is not news
            return False

    # --- asking the house for a list ---------------------------------------
    def closed_reason(self) -> str | None:
        """Why the socket under this observer is unusable, or None while it works."""
        return getattr(self.client, "closed_reason", None)

    async def _list(self, command: str, key: str, **args: Any) -> list[Any]:
        """One listing command, or [] when this build lacks it — never [] for a dead socket.

        The distinction is the whole point. On 27 Aug 2026 the house closed the
        rig's sockets (uvicorn's 1012, "service restart") a third of the way
        through a report run, and every listing after that read as an empty
        house — "tasks were []", "had []" — across thirty scenarios, while the
        console beside them showed the tasks. A build without the command is
        the scenario's failed assertion; a socket the house closed is the run's,
        and it is named with the close code so whoever restarted the house can
        be found.
        """
        try:
            answer = await self.client.command(command, **args)
        except Exception as err:  # noqa: BLE001 - which of the two it is decides below
            dead = self.closed_reason()
            if dead:
                raise LiveError(
                    f"the socket to Jarvis is closed ({dead}); `{command}` cannot be asked"
                ) from err
            return []
        return list((answer or {}).get(key) or [])

    async def notes(self, query: str = "") -> list[dict[str, Any]]:
        """Every note the server holds, or the ones matching a query."""
        return await self._list("jarvis/notes/list", "notes", **({"query": query} if query else {}))

    async def note_body(self, note_id: str) -> str:
        try:
            answer = await self.client.command("jarvis/notes/get", note_id=note_id)
        except Exception:  # noqa: BLE001
            return ""
        return str(((answer or {}).get("note") or {}).get("body") or "")

    async def notifications(self) -> list[dict[str, Any]]:
        """Every proactive message the server has recorded."""
        return await self._list("jarvis/notifications/list", "notifications")

    async def wait_for_notification(self, title_contains: str = "", kind: str = "",
                                    timeout: float = 120.0, since: float = 0.0) -> dict[str, Any] | None:
        """The first notification matching, recorded at or after `since`.

        `since` is the scenario's own start: the record persists across runs
        and restarts, so without it a task notification from an earlier gate's
        run — a failed one, "interrupted when Jarvis restarted" — answered the
        proactive-moment scenario before its own task had finished (27 Aug
        2026, inside `make verify-all`).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for row in await self.notifications():
                if kind and row.get("kind") != kind:
                    continue
                if since and float(row.get("at") or 0.0) < since:
                    continue
                if title_contains and title_contains.lower() not in str(
                    row.get("title") or ""
                ).lower():
                    continue
                return row
            await asyncio.sleep(0.5)
        return None

    async def surface(self) -> list[dict[str, Any]]:
        """The voice screen's panels, as the console lists them (M83)."""
        return await self._list("jarvis/surface/list", "panels")

    async def wait_for_surface(self, entity: str = "", kind: str = "", count: int | None = None,
                               timeout: float = 20.0) -> list[dict[str, Any]] | None:
        """The panels once they match — an entity's panel present, a kind present,
        or exactly `count` panels — or None when they never do in time."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            panels = await self.surface()
            if count is not None and len(panels) == count and not entity and not kind:
                return panels
            hit = [p for p in panels
                   if (not entity or p.get("entity") == entity) and (not kind or p.get("kind") == kind)]
            if (entity or kind) and hit and (count is None or len(panels) == count):
                return panels
            await asyncio.sleep(0.5)
        return None

    async def memories(self, query: str = "") -> list[dict[str, Any]]:
        return await self._list("jarvis/memory/list", "entries", **({"query": query} if query else {}))

    async def extensions(self) -> list[dict[str, Any]]:
        """Everything installed, as the console's own page sees it."""
        return await self._list("jarvis/extensions/list", "extensions")

    async def set_extension(self, key: str, **patch: Any) -> dict[str, Any]:
        return await self.client.command("jarvis/extensions/set", key=key, **patch)

    async def offered_skills(self) -> list[str]:
        """The skills the MODEL is offered — the store, not the console's list."""
        rows = await self._list("jarvis/skills/list", "skills")
        return [str(row.get("name") or "") for row in rows]

    async def offered_tools(self) -> list[str]:
        """What the MODEL is offered, not what the console lists.

        The two are different questions and the second one is the claim worth
        testing: a plugin hidden from a page is one the model can still call.
        """
        rows = await self._list("jarvis/tools/list", "tools")
        return [str(row.get("name") or "") for row in rows]

    async def wait_for_note(self, contains: str = "", title_contains: str = "",
                            timeout: float = 60.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for note in await self.notes():
                if title_contains and title_contains.lower() not in str(
                    note.get("title") or ""
                ).lower():
                    continue
                if contains:
                    haystack = f"{note.get('title', '')} {await self.note_body(note['id'])}"
                    if contains.lower() not in haystack.lower():
                        continue
                return note
            await asyncio.sleep(0.5)
        return None

    async def tasks(self) -> list[dict[str, Any]]:
        return await self._list("jarvis/tasks/list", "tasks")

    async def schedules(self) -> list[dict[str, Any]]:
        """What is scheduled and not yet fired — reminders, timed jobs."""
        return await self._list("jarvis/schedule/list", "jobs")

    async def wait_for_schedule(
        self,
        *,
        title_contains: str = "",
        timeout: float = 60.0,
        since: float = 0.0,
    ) -> dict[str, Any] | None:
        """The first schedule entry matching, or None once `timeout` has passed.

        `since` is the same wall-clock floor `wait_for_task` has: an entry
        created before the scenario began is somebody else's reminder.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for job in await self.schedules():
                if since and float(job.get("created") or 0.0) < since:
                    continue
                if title_contains and title_contains.lower() not in str(
                    job.get("title") or ""
                ).lower():
                    continue
                return job
            await asyncio.sleep(0.5)
        return None

    async def wait_for_task(
        self,
        *,
        kind: str = "",
        status: str = "",
        title_contains: str = "",
        timeout: float = 120.0,
        since: float = 0.0,
    ) -> dict[str, Any] | None:
        """The first task matching, or None once `timeout` has passed.

        `since` is a wall-clock floor on `created`: without it, a turn that
        made no task at all was passed by whatever task was already in the
        list — four sensor audits interrupted by a restart hours earlier
        satisfied "a background task appeared within 30 s" for every scenario
        after them.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for task in await self.tasks():
                if since and float(task.get("created") or 0.0) < since:
                    continue
                if kind and task.get("kind") != kind:
                    continue
                if status and task.get("status") != status:
                    continue
                if title_contains and title_contains.lower() not in str(
                    task.get("title") or ""
                ).lower():
                    continue
                return task
            await asyncio.sleep(0.5)
        return None
