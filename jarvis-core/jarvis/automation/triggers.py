"""Trigger platforms.

Every platform is ``async def async_attach(jarvis, config, fire) -> unsub``
where `fire` is called with the ``trigger`` variable dict (and optionally the
originating :class:`~jarvis.bus.Context`). The returned callable detaches the
trigger and cancels anything it scheduled.

Supported ``platform:`` (``trigger:`` is accepted as an alias, matching newer
Home Assistant YAML)::

    state | numeric_state | time | time_pattern | event | mqtt | webhook
    template | jarvis_start (aliases: homeassistant_start, start, jarvis,
    homeassistant with `event: start|shutdown`)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..const import EVENT_JARVIS_START, EVENT_JARVIS_STOP, EVENT_STATE_CHANGED
from .util import (
    as_float,
    as_list,
    get_clock,
    next_time_of_day,
    next_time_pattern,
    parse_duration,
    parse_time,
    render_bool,
    render_template,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..bus import Context, Event
    from ..core import Jarvis
    from ..state import State

_LOGGER = logging.getLogger(__name__)

DATA_WEBHOOKS = "webhooks"
DATA_MQTT = "mqtt"

_UNSET: Any = object()

FireTrigger = Callable[..., Awaitable[None] | None]
Unsub = Callable[[], None]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _wrap_fire(fire: FireTrigger) -> Callable[..., Awaitable[None]]:
    """Normalise a `fire` callback to ``await fire(trigger, context)``."""
    accepts_context = False
    try:
        params = list(inspect.signature(fire).parameters.values())
        positional = [
            p
            for p in params
            if p.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        accepts_context = len(positional) >= 2 or any(
            p.kind is inspect.Parameter.VAR_POSITIONAL for p in params
        )
    except (TypeError, ValueError):  # builtins / C callables
        accepts_context = False

    async def _call(trigger: dict[str, Any], context: "Context | None" = None) -> None:
        try:
            result = fire(trigger, context) if accepts_context else fire(trigger)
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error handling %s trigger", trigger.get("platform"))

    return _call


def _base_trigger(config: dict[str, Any], platform: str) -> dict[str, Any]:
    trigger: dict[str, Any] = {"platform": platform}
    if config.get("id") is not None:
        trigger["id"] = str(config["id"])
    if config.get("alias"):
        trigger["alias"] = config["alias"]
    return trigger


def _matches(value: Any, expected: Any) -> bool:
    """Compare a state/attribute value against a config matcher."""
    if expected is _UNSET:
        return True
    if isinstance(expected, (list, tuple, set, frozenset)):
        return any(_matches_one(value, item) for item in expected)
    return _matches_one(value, expected)


def _matches_one(value: Any, expected: Any) -> bool:
    if expected is None:
        return value is None
    if value is None:
        return False
    if value == expected:
        return True
    return str(value) == str(expected)


def _state_value(state: "State | None", attribute: str | None) -> Any:
    if state is None:
        return None
    if attribute:
        return state.attributes.get(attribute)
    return state.state


class _Delayed:
    """Per-entity `for:` timers, cancelled when the match stops holding."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule(
        self, key: str, jarvis: "Jarvis", coro_factory: Callable[[], Awaitable[None]]
    ) -> None:
        self.cancel(key)
        holder: dict[str, asyncio.Task] = {}

        async def _runner() -> None:
            try:
                await coro_factory()
            finally:
                # Only retire *this* task. A cancelled predecessor unwinds one
                # loop pass later, by which time `key` already points at its
                # replacement — popping blindly would orphan that replacement
                # (it would then survive `cancel`/`cancel_all` and still fire
                # after the trigger was detached).
                if self._tasks.get(key) is holder.get("task"):
                    self._tasks.pop(key, None)

        task = jarvis.async_create_task(_runner())
        holder["task"] = task
        self._tasks[key] = task

    def __contains__(self, key: object) -> bool:
        task = self._tasks.get(str(key))
        return task is not None and not task.done()

    def cancel(self, key: str) -> None:
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()

    def cancel_all(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
async def async_attach_state(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    entity_ids = {str(e) for e in as_list(config.get("entity_id"))}
    attribute = config.get("attribute")
    to_match = config.get("to", _UNSET)
    from_match = config["from"] if "from" in config else config.get("from_", _UNSET)
    not_to = config.get("not_to", _UNSET)
    not_from = config.get("not_from", _UNSET)
    for_seconds = parse_duration(config.get("for"))
    filtered = to_match is not _UNSET or from_match is not _UNSET or attribute is not None

    emit = _wrap_fire(fire)
    delayed = _Delayed()

    async def _listener(event: "Event") -> None:
        entity_id = event.data.get("entity_id")
        if entity_ids and entity_id not in entity_ids:
            return
        old_state: "State | None" = event.data.get("old_state")
        new_state: "State | None" = event.data.get("new_state")
        old_value = _state_value(old_state, attribute)
        new_value = _state_value(new_state, attribute)

        # Does the *new* value still sit on the target the `for:` timer is
        # counting down against? Kept separate from `ok` so that a repeat /
        # attribute-only write ("still off, battery now 80%") does not cancel
        # a timer that is legitimately still running.
        holds = _matches(new_value, to_match) and not (
            not_to is not _UNSET and _matches(new_value, not_to)
        )
        ok = (
            holds
            and _matches(old_value, from_match)
            and not (not_from is not _UNSET and _matches(old_value, not_from))
        )
        # With to/from/attribute filters the watched value must actually move.
        repeat = old_value == new_value
        if ok and filtered and repeat:
            ok = False

        if not ok:
            if not holds:
                delayed.cancel(str(entity_id))
            return
        if for_seconds and repeat and str(entity_id) in delayed:
            return  # timer already running for this value; let it finish

        trigger = _base_trigger(config, "state")
        trigger.update(
            {
                "entity_id": entity_id,
                "from_state": old_state,
                "to_state": new_state,
                "attribute": attribute,
                "description": f"state of {entity_id}",
            }
        )

        if not for_seconds:
            delayed.cancel(str(entity_id))
            await emit(trigger, event.context)
            return

        trigger["for"] = for_seconds

        async def _after_delay() -> None:
            await asyncio.sleep(for_seconds)
            current = jarvis.states.get(str(entity_id))
            if _state_value(current, attribute) != new_value:
                return
            await emit(trigger, event.context)

        delayed.schedule(str(entity_id), jarvis, _after_delay)

    unsub = jarvis.bus.listen(EVENT_STATE_CHANGED, _listener)

    def _detach() -> None:
        unsub()
        delayed.cancel_all()

    return _detach


# ---------------------------------------------------------------------------
# numeric_state
# ---------------------------------------------------------------------------
async def async_attach_numeric_state(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    entity_ids = {str(e) for e in as_list(config.get("entity_id"))}
    attribute = config.get("attribute")
    value_template = config.get("value_template")
    above = config.get("above")
    below = config.get("below")
    for_seconds = parse_duration(config.get("for"))

    emit = _wrap_fire(fire)
    delayed = _Delayed()

    def _bound(raw: Any) -> float | None:
        """A bound may be a number or another entity's (numeric) state."""
        if isinstance(raw, str) and "." in raw and not raw.replace(".", "", 1).isdigit():
            other = jarvis.states.get(raw)
            return as_float(other.state) if other else None
        return as_float(raw)

    def _value_of(state: "State | None", entity_id: str) -> float | None:
        if state is None:
            return None
        if value_template:
            rendered = render_template(
                jarvis,
                value_template,
                {"state": state, "entity_id": entity_id, "value": state.state},
            )
            return as_float(rendered)
        return as_float(_state_value(state, attribute))

    def _in_range(value: float | None) -> bool:
        if value is None:
            return False
        if above is not None:
            limit = _bound(above)
            if limit is None or value <= limit:
                return False
        if below is not None:
            limit = _bound(below)
            if limit is None or value >= limit:
                return False
        return above is not None or below is not None

    async def _listener(event: "Event") -> None:
        entity_id = str(event.data.get("entity_id"))
        if entity_ids and entity_id not in entity_ids:
            return
        old_state: "State | None" = event.data.get("old_state")
        new_state: "State | None" = event.data.get("new_state")
        new_value = _value_of(new_state, entity_id)

        if not _in_range(new_value):
            delayed.cancel(entity_id)
            return
        if _in_range(_value_of(old_state, entity_id)):
            return  # already inside the window; not a crossing

        trigger = _base_trigger(config, "numeric_state")
        trigger.update(
            {
                "entity_id": entity_id,
                "from_state": old_state,
                "to_state": new_state,
                "above": above,
                "below": below,
                "attribute": attribute,
                "description": f"numeric state of {entity_id}",
            }
        )

        if not for_seconds:
            await emit(trigger, event.context)
            return

        trigger["for"] = for_seconds

        async def _after_delay() -> None:
            await asyncio.sleep(for_seconds)
            if _in_range(_value_of(jarvis.states.get(entity_id), entity_id)):
                await emit(trigger, event.context)

        delayed.schedule(entity_id, jarvis, _after_delay)

    unsub = jarvis.bus.listen(EVENT_STATE_CHANGED, _listener)

    def _detach() -> None:
        unsub()
        delayed.cancel_all()

    return _detach


# ---------------------------------------------------------------------------
# time / time_pattern
# ---------------------------------------------------------------------------
async def async_attach_time(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    raw_times = as_list(config.get("at") if config.get("at") is not None else config.get("time"))
    targets = [t for t in (parse_time(value) for value in raw_times) if t is not None]
    if not targets:
        _LOGGER.warning("time trigger without a usable `at:` (%r)", raw_times)
        return lambda: None

    emit = _wrap_fire(fire)
    clock = get_clock(jarvis)

    async def _loop() -> None:
        while True:
            now = clock.now()
            nxt = min(next_time_of_day(now, target) for target in targets)
            await clock.sleep((nxt - now).total_seconds())
            await asyncio.sleep(0)  # stay cancellable even with a fake clock
            trigger = _base_trigger(config, "time")
            trigger.update({"now": clock.now(), "description": "time"})
            await emit(trigger)

    task = jarvis.async_create_task(_loop())
    return task.cancel


async def async_attach_time_pattern(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    hours = config.get("hours")
    minutes = config.get("minutes")
    seconds = config.get("seconds")
    if hours is None and minutes is None and seconds is None:
        _LOGGER.warning("time_pattern trigger with no hours/minutes/seconds")
        return lambda: None

    emit = _wrap_fire(fire)
    clock = get_clock(jarvis)

    async def _loop() -> None:
        while True:
            now = clock.now()
            nxt = next_time_pattern(now, hours, minutes, seconds)
            if nxt is None:
                _LOGGER.warning("time_pattern never matches: %r", config)
                return
            await clock.sleep((nxt - now).total_seconds())
            await asyncio.sleep(0)
            trigger = _base_trigger(config, "time_pattern")
            trigger.update({"now": clock.now(), "description": "time pattern"})
            await emit(trigger)

    task = jarvis.async_create_task(_loop())
    return task.cancel


# ---------------------------------------------------------------------------
# event
# ---------------------------------------------------------------------------
async def async_attach_event(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    event_types = [str(e) for e in as_list(config.get("event_type"))]
    if not event_types:
        _LOGGER.warning("event trigger without event_type")
        return lambda: None
    wanted = config.get("event_data") or {}
    emit = _wrap_fire(fire)

    async def _listener(event: "Event") -> None:
        for key, value in wanted.items():
            if not _matches_one(event.data.get(key), value):
                return
        trigger = _base_trigger(config, "event")
        trigger.update(
            {
                "event_type": event.event_type,
                "event": event,
                "event_data": event.data,
                "description": f"event {event.event_type}",
            }
        )
        await emit(trigger, event.context)

    unsubs = [jarvis.bus.listen(event_type, _listener) for event_type in event_types]

    def _detach() -> None:
        for unsub in unsubs:
            unsub()

    return _detach


# ---------------------------------------------------------------------------
# mqtt
# ---------------------------------------------------------------------------
async def async_attach_mqtt(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    topic = config.get("topic")
    if not topic:
        _LOGGER.warning("mqtt trigger without topic")
        return lambda: None
    client = jarvis.data.get(DATA_MQTT)
    if client is None or not hasattr(client, "async_subscribe"):
        _LOGGER.warning("mqtt not available; mqtt trigger on %s is inert", topic)
        return lambda: None

    expected = config.get("payload", _UNSET)
    value_template = config.get("value_template")
    emit = _wrap_fire(fire)

    async def _on_message(message: Any) -> None:
        payload = getattr(message, "payload", message)
        if value_template:
            payload = render_template(
                jarvis, value_template, {"value": payload, "payload": payload}
            )
        if expected is not _UNSET and not _matches_one(payload, expected):
            return
        trigger = _base_trigger(config, "mqtt")
        trigger.update(
            {
                "topic": getattr(message, "topic", topic),
                "payload": payload,
                "description": f"mqtt topic {topic}",
            }
        )
        payload_json = getattr(message, "json", None)
        if callable(payload_json):
            try:
                trigger["payload_json"] = payload_json()
            except Exception:
                trigger["payload_json"] = None
        await emit(trigger)

    unsub = await client.async_subscribe(topic, _on_message, int(config.get("qos", 0) or 0))
    return unsub if callable(unsub) else (lambda: None)


# ---------------------------------------------------------------------------
# webhook
# ---------------------------------------------------------------------------
class WebhookHandler:
    """Fan-out target stored at ``jarvis.data["webhooks"][webhook_id]``.

    The API layer just awaits it: ``await handler(payload, query=..., ...)``.
    """

    def __init__(self, webhook_id: str) -> None:
        self.webhook_id = webhook_id
        self.callbacks: list[Callable[..., Awaitable[None]]] = []

    def add(self, callback: Callable[..., Awaitable[None]]) -> Callable[[], None]:
        self.callbacks.append(callback)

        def _remove() -> None:
            try:
                self.callbacks.remove(callback)
            except ValueError:
                pass

        return _remove

    async def __call__(
        self,
        data: Any = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        method: str = "POST",
    ) -> int:
        for callback in list(self.callbacks):
            await callback(data, query, headers, method)
        return len(self.callbacks)


async def async_attach_webhook(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    webhook_id = config.get("webhook_id")
    if not webhook_id:
        _LOGGER.warning("webhook trigger without webhook_id")
        return lambda: None

    webhooks = jarvis.data.setdefault(DATA_WEBHOOKS, {})
    handler = webhooks.get(webhook_id)
    if not isinstance(handler, WebhookHandler):
        handler = WebhookHandler(str(webhook_id))
        webhooks[webhook_id] = handler

    emit = _wrap_fire(fire)

    async def _callback(
        data: Any, query: Any = None, headers: Any = None, method: str = "POST"
    ) -> None:
        trigger = _base_trigger(config, "webhook")
        trigger.update(
            {
                "webhook_id": webhook_id,
                "data": data,
                "json": data if isinstance(data, (dict, list)) else None,
                "query": query or {},
                "headers": headers or {},
                "method": method,
                "description": f"webhook {webhook_id}",
            }
        )
        await emit(trigger)

    remove = handler.add(_callback)

    def _detach() -> None:
        remove()
        if not handler.callbacks and webhooks.get(webhook_id) is handler:
            webhooks.pop(webhook_id, None)

    return _detach


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------
async def async_attach_template(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    value_template = config.get("value_template") or config.get("template")
    if not value_template:
        _LOGGER.warning("template trigger without value_template")
        return lambda: None

    for_seconds = parse_duration(config.get("for"))
    emit = _wrap_fire(fire)
    state = {"last": render_bool(jarvis, value_template, {})}
    delayed = _Delayed()

    async def _listener(event: "Event") -> None:
        result = render_bool(jarvis, value_template, {})
        previous = state["last"]
        state["last"] = result
        if not result:
            delayed.cancel("template")
            return
        if previous:
            return

        trigger = _base_trigger(config, "template")
        trigger.update(
            {
                "entity_id": event.data.get("entity_id"),
                "from_state": event.data.get("old_state"),
                "to_state": event.data.get("new_state"),
                "description": "template",
            }
        )
        if not for_seconds:
            await emit(trigger, event.context)
            return

        trigger["for"] = for_seconds

        async def _after_delay() -> None:
            await asyncio.sleep(for_seconds)
            if render_bool(jarvis, value_template, {}):
                await emit(trigger, event.context)

        delayed.schedule("template", jarvis, _after_delay)

    unsub = jarvis.bus.listen(EVENT_STATE_CHANGED, _listener)

    def _detach() -> None:
        unsub()
        delayed.cancel_all()

    return _detach


# ---------------------------------------------------------------------------
# jarvis lifecycle
# ---------------------------------------------------------------------------
async def async_attach_start(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    which = str(config.get("event", "start")).lower()
    event_type = EVENT_JARVIS_STOP if which in ("shutdown", "stop") else EVENT_JARVIS_START
    emit = _wrap_fire(fire)

    async def _listener(event: "Event") -> None:
        trigger = _base_trigger(config, "jarvis_start" if which == "start" else "jarvis_stop")
        trigger.update({"event": which, "description": f"jarvis {which}"})
        await emit(trigger, event.context)

    unsub = jarvis.bus.listen(event_type, _listener)

    # Attached after startup (e.g. an automation reload): fire once, soon.
    if event_type == EVENT_JARVIS_START and getattr(jarvis, "is_running", False):

        async def _late() -> None:
            trigger = _base_trigger(config, "jarvis_start")
            trigger.update({"event": "start", "description": "jarvis start (late attach)"})
            await emit(trigger)

        jarvis.async_create_task(_late())

    return unsub


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
TRIGGER_PLATFORMS: dict[str, Callable[..., Awaitable[Unsub]]] = {
    "state": async_attach_state,
    "numeric_state": async_attach_numeric_state,
    "time": async_attach_time,
    "time_pattern": async_attach_time_pattern,
    "event": async_attach_event,
    "mqtt": async_attach_mqtt,
    "webhook": async_attach_webhook,
    "template": async_attach_template,
    "jarvis_start": async_attach_start,
    "jarvis": async_attach_start,
    "homeassistant_start": async_attach_start,
    "home_assistant_start": async_attach_start,
    "homeassistant": async_attach_start,
    "start": async_attach_start,
    "shutdown": async_attach_start,
}


def _platform_of(config: dict[str, Any]) -> str:
    platform = config.get("platform") or config.get("trigger") or ""
    return str(platform).strip().lower()


async def async_attach_trigger(
    jarvis: "Jarvis", config: dict[str, Any], fire: FireTrigger
) -> Unsub:
    """Attach one trigger config. Returns an unsubscribe callable."""
    if not isinstance(config, dict):
        _LOGGER.warning("Trigger config must be a mapping, got %r", config)
        return lambda: None
    platform = _platform_of(config)
    attach = TRIGGER_PLATFORMS.get(platform)
    if attach is None:
        _LOGGER.warning(
            "Unknown trigger platform %r (known: %s)",
            platform,
            ", ".join(sorted(TRIGGER_PLATFORMS)),
        )
        return lambda: None
    if platform == "shutdown":
        config = {**config, "event": "shutdown"}
    try:
        return await attach(jarvis, config, fire)
    except Exception:
        _LOGGER.exception("Failed attaching %s trigger", platform)
        return lambda: None


async def async_attach_triggers(
    jarvis: "Jarvis", configs: Any, fire: FireTrigger
) -> Unsub:
    """Attach a list of triggers; returns one callable that detaches them all."""
    unsubs = [
        await async_attach_trigger(jarvis, config, fire) for config in as_list(configs)
    ]

    def _detach() -> None:
        for unsub in unsubs:
            try:
                unsub()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Error detaching trigger")

    return _detach


__all__ = [
    "TRIGGER_PLATFORMS",
    "WebhookHandler",
    "async_attach_trigger",
    "async_attach_triggers",
]
