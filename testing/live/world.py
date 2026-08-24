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
        "jarvis_approval_requested",
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.state_of(entity_id) == want:
                return True
            await asyncio.sleep(0.2)
        return False

    async def notes(self, query: str = "") -> list[dict[str, Any]]:
        """Every note the server holds, or the ones matching a query."""
        try:
            answer = await self.client.command(
                "jarvis/notes/list", **({"query": query} if query else {})
            )
        except Exception:  # noqa: BLE001 - a build without notes fails the assertion
            return []
        return list((answer or {}).get("notes") or [])

    async def note_body(self, note_id: str) -> str:
        try:
            answer = await self.client.command("jarvis/notes/get", note_id=note_id)
        except Exception:  # noqa: BLE001
            return ""
        return str(((answer or {}).get("note") or {}).get("body") or "")

    async def notifications(self) -> list[dict[str, Any]]:
        """Every proactive message the server has recorded."""
        try:
            answer = await self.client.command("jarvis/notifications/list")
        except Exception:  # noqa: BLE001 - a build without them fails the assertion
            return []
        return list((answer or {}).get("notifications") or [])

    async def wait_for_notification(self, title_contains: str = "", kind: str = "",
                                    timeout: float = 120.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for row in await self.notifications():
                if kind and row.get("kind") != kind:
                    continue
                if title_contains and title_contains.lower() not in str(
                    row.get("title") or ""
                ).lower():
                    continue
                return row
            await asyncio.sleep(0.5)
        return None

    async def memories(self, query: str = "") -> list[dict[str, Any]]:
        try:
            answer = await self.client.command(
                "jarvis/memory/list", **({"query": query} if query else {})
            )
        except Exception:  # noqa: BLE001
            return []
        return list((answer or {}).get("entries") or [])

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
        try:
            answer = await self.client.command("jarvis/tasks/list")
        except Exception:  # noqa: BLE001 - a build without tasks fails the assertion
            return []
        return list((answer or {}).get("tasks") or [])

    async def wait_for_task(
        self,
        *,
        kind: str = "",
        status: str = "",
        title_contains: str = "",
        timeout: float = 120.0,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for task in await self.tasks():
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
