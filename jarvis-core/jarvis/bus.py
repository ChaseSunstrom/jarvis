"""Async event bus.

Every state change, service call and integration signal flows through here.
Listeners may be coroutine functions or plain callables; plain callables run
inline (keep them fast), coroutines are scheduled on the loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from .const import MATCH_ALL

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Context:
    """Traces what caused an event (user, automation, LLM tool call)."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str | None = None
    parent_id: str | None = None
    origin: str = "internal"  # internal | user | automation | llm | api

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "parent_id": self.parent_id,
            "origin": self.origin,
        }


@dataclass(slots=True)
class Event:
    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    time_fired: float = field(default_factory=time.time)
    context: Context = field(default_factory=Context)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "data": _jsonable(self.data),
            "time_fired": self.time_fired,
            "context": self.context.as_dict(),
        }


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of event payloads for JSON transport."""
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


Listener = Callable[[Event], Coroutine[Any, Any, None] | None]


class EventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._listeners: dict[str, list[Listener]] = {}
        self._loop = loop
        self._pending: set[asyncio.Task] = set()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    def listen(self, event_type: str, listener: Listener) -> Callable[[], None]:
        """Subscribe. Returns an unsubscribe callable."""
        self._listeners.setdefault(event_type, []).append(listener)

        def _unsub() -> None:
            try:
                self._listeners.get(event_type, []).remove(listener)
            except ValueError:
                pass

        return _unsub

    def listen_once(self, event_type: str, listener: Listener) -> Callable[[], None]:
        unsub: Callable[[], None]

        async def _once(event: Event) -> None:
            unsub()
            result = listener(event)
            if asyncio.iscoroutine(result):
                await result

        unsub = self.listen(event_type, _once)
        return unsub

    def fire(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> Event:
        """Fire an event. Coroutine listeners are scheduled, not awaited."""
        event = Event(event_type, data or {}, context=context or Context())
        for listener in (
            *self._listeners.get(event_type, ()),
            *self._listeners.get(MATCH_ALL, ()),
        ):
            try:
                result = listener(event)
            except Exception:  # a bad listener must not break the bus
                _LOGGER.exception("Error in listener for %s", event_type)
                continue
            if asyncio.iscoroutine(result):
                task = self.loop.create_task(_guard(result, event_type))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
        return event

    async def async_fire(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> Event:
        """Fire and await every listener (used by tests and ordered flows)."""
        event = Event(event_type, data or {}, context=context or Context())
        for listener in (
            *list(self._listeners.get(event_type, ())),
            *list(self._listeners.get(MATCH_ALL, ())),
        ):
            try:
                result = listener(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _LOGGER.exception("Error in listener for %s", event_type)
        return event

    async def async_block_till_done(self) -> None:
        """Wait for scheduled listener tasks (test helper)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)


async def _guard(coro: Coroutine[Any, Any, None], event_type: str) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception("Error in async listener for %s", event_type)
