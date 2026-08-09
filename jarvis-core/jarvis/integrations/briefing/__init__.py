"""briefing — the summary Jarvis volunteers, rather than waits to be asked for.

Twice a day (or on demand) this reads the live house and produces something
short enough to listen to: what the weather is doing, what is on today, what
needs attention, and what quietly stopped working while you were not looking.

It is built to be *skippable*. Every section can come back empty, and an empty
section is not mentioned — no "you have no calendar events, no tasks and no
unavailable entities", which is the failure mode that makes people turn these
things off. If every section is empty there is no briefing at all and nothing
is delivered.

Delivery goes through ``companion.notify``, so the presence layer decides
where it lands: spoken if you are up and at a device, a quiet notification if
you are not, queued if you are unreachable. The briefing never picks a device
itself.

Configuration::

    briefing:
      morning: "07:00"
      evening: "22:00"
      include: [calendar, weather, tasks, house, unavailable_entities]
      max_items: 4        # per section
      max_chars: 700      # whole briefing
      importance: low

Services
    ``briefing.generate`` (kind, include) → ``{"text": ..., "sections": [...]}``
    ``briefing.deliver``  (kind, include, device_id) → the companion result

LLM tool: ``get_briefing``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Iterable

from ...automation.util import get_clock, next_time_of_day, parse_time
from ...bus import Context
from ...const import (
    EVENT_JARVIS_START,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from ...services import ServiceCall
from ...state import split_entity_id

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...state import State

_LOGGER = logging.getLogger(__name__)

DOMAIN = "briefing"
DEPENDENCIES = ["llm"]

EVENT_BRIEFING_READY = "briefing_ready"

DEFAULT_SECTIONS = ("weather", "calendar", "tasks", "house", "unavailable_entities")
DEFAULT_MAX_ITEMS = 4
DEFAULT_MAX_CHARS = 700
DEFAULT_MORNING = "07:00"
DEFAULT_EVENING = "22:00"

#: How far back "overnight" reaches for the morning briefing.
OVERNIGHT_HOURS = 10.0

OPENING_STATES = frozenset({"open", STATE_ON})
DOOR_CLASSES = frozenset({"door", "garage_door", "window", "opening"})


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def friendly_name(state: "State") -> str:
    name = state.attributes.get("friendly_name")
    if name:
        return str(name)
    return split_entity_id(state.entity_id)[1].replace("_", " ").title()


def join_names(names: list[str], limit: int) -> str:
    """``a, b and 3 more`` — never a wall of entity ids."""
    shown = names[:limit]
    extra = len(names) - len(shown)
    if not shown:
        return ""
    text = shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"
    if extra > 0:
        text = f"{text} and {extra} more"
    return text


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00"), text.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _clock_time(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Section:
    key: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(line.strip() for line in self.lines if line.strip())

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "lines": list(self.lines), "text": self.text}


# ---------------------------------------------------------------------------
# the builder
# ---------------------------------------------------------------------------
class BriefingBuilder:
    """Turns live state into a digest. No I/O; every input is the state machine."""

    def __init__(
        self,
        jarvis: "Jarvis",
        include: Iterable[str] = DEFAULT_SECTIONS,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.jarvis = jarvis
        self.include = [str(s).strip().lower() for s in include if str(s).strip()]
        self.max_items = max(1, int(max_items or DEFAULT_MAX_ITEMS))
        self.max_chars = max(80, int(max_chars or DEFAULT_MAX_CHARS))
        self.last_delivered: dict[str, float] = {}

    # --- entry point ------------------------------------------------------
    def build(self, kind: str = "now", include: Iterable[str] | None = None) -> dict[str, Any]:
        kind = str(kind or "now").strip().lower()
        wanted = [str(s).strip().lower() for s in include] if include else list(self.include)
        now = get_clock(self.jarvis).now()

        builders = {
            "weather": self._weather,
            "calendar": self._calendar,
            "tasks": self._tasks,
            "house": self._house,
            "unavailable_entities": self._unavailable,
        }

        sections: list[Section] = []
        for key in wanted:
            builder = builders.get(key)
            if builder is None:
                _LOGGER.warning("briefing: unknown section %r (ignored)", key)
                continue
            try:
                section = builder(kind, now)
            except Exception:  # pragma: no cover - one bad section must not kill the rest
                _LOGGER.exception("briefing: section %s failed", key)
                continue
            # Empty sections are dropped here, not mentioned as empty.
            if section is not None and section.lines:
                sections.append(section)

        text, dropped = self._compose(kind, now, sections)
        return {
            "kind": kind,
            "text": text,
            "empty": not sections,
            "sections": [s.as_dict() for s in sections],
            "dropped_sections": dropped,
            "generated": time.time(),
        }

    # --- composition ------------------------------------------------------
    def _greeting(self, kind: str, now: datetime) -> str:
        if kind == "morning":
            return "Good morning, Sir."
        if kind == "evening":
            return "Before you turn in, Sir."
        return "Here is where things stand, Sir."

    def _compose(
        self, kind: str, now: datetime, sections: list[Section]
    ) -> tuple[str, list[str]]:
        if not sections:
            return "", []
        opening = self._greeting(kind, now)
        parts = [opening]
        used = len(opening)
        dropped: list[str] = []
        for section in sections:
            text = section.text
            if not text:
                continue
            if used + len(text) + 1 > self.max_chars:
                # Trim whole sections off the end rather than cutting a
                # sentence in half mid-word.
                dropped.append(section.key)
                continue
            parts.append(text)
            used += len(text) + 1
        if dropped:
            parts.append(f"There is more, but I'll spare you: {', '.join(dropped)}.")
        return " ".join(parts), dropped

    # --- sections ---------------------------------------------------------
    def _weather(self, kind: str, now: datetime) -> Section | None:
        section = Section("weather")
        for state in sorted(self.jarvis.states.all("weather"), key=lambda s: s.entity_id):
            if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
                continue
            condition = str(state.state).replace("-", " ").replace("_", " ")
            unit = str(state.attributes.get("temperature_unit") or "°")
            temperature = _number(state.attributes.get("temperature"))
            bits = [condition]
            if temperature is not None:
                bits.append(f"{round(temperature)}{unit}")

            forecast = state.attributes.get("forecast")
            if isinstance(forecast, (list, tuple)) and forecast:
                entry = forecast[0] if isinstance(forecast[0], dict) else {}
                high = _number(entry.get("temperature") or entry.get("native_temperature"))
                low = _number(entry.get("templow") or entry.get("native_templow"))
                if high is not None and low is not None:
                    bits.append(f"between {round(low)} and {round(high)}{unit} today")
                elif high is not None:
                    bits.append(f"up to {round(high)}{unit} today")
            section.lines.append(f"{', '.join(bits)}.".capitalize())
            break  # one weather entity is a briefing; five is a forecast service
        return section

    def _calendar(self, kind: str, now: datetime) -> Section | None:
        target: date = (now + timedelta(days=1)).date() if kind == "evening" else now.date()
        events: list[tuple[datetime | None, str]] = []

        for state in sorted(self.jarvis.states.all("calendar"), key=lambda s: s.entity_id):
            for event in self._events_of(state):
                summary = str(
                    event.get("summary") or event.get("message") or event.get("title") or ""
                ).strip()
                if not summary:
                    continue
                start = _as_datetime(event.get("start") or event.get("start_time"))
                if start is not None and start.date() != target:
                    continue
                if start is None and not event.get("all_day"):
                    continue
                events.append((start, summary))

        if not events:
            return None
        events.sort(key=lambda item: (item[0] is None, item[0] or now))

        section = Section("calendar")
        when = "Tomorrow" if kind == "evening" else "Today"
        shown = events[: self.max_items]
        described = [
            f"{summary} at {_clock_time(start)}" if start is not None else summary
            for start, summary in shown
        ]
        extra = len(events) - len(shown)
        line = f"{when}: {'; '.join(described)}"
        if extra > 0:
            line += f", plus {extra} more"
        section.lines.append(f"{line}.")
        return section

    def _events_of(self, state: "State") -> list[dict[str, Any]]:
        """Both shapes in the wild: an `events` list, or one flattened event."""
        events = state.attributes.get("events")
        if isinstance(events, (list, tuple)):
            return [e for e in events if isinstance(e, dict)]
        if state.attributes.get("message") or state.attributes.get("summary"):
            return [dict(state.attributes)]
        return []

    def _tasks(self, kind: str, now: datetime) -> Section | None:
        section = Section("tasks")
        outstanding: list[str] = []
        total = 0
        for state in sorted(self.jarvis.states.all("todo"), key=lambda s: s.entity_id):
            items = state.attributes.get("items")
            names: list[str] = []
            if isinstance(items, (list, tuple)):
                for item in items:
                    if isinstance(item, dict):
                        if str(item.get("status") or "needs_action") == "completed":
                            continue
                        summary = str(item.get("summary") or item.get("name") or "").strip()
                    else:
                        summary = str(item).strip()
                    if summary:
                        names.append(summary)
                count = len(names)
            else:
                count = int(_number(state.state) or 0)
            if count <= 0:
                continue
            total += count
            label = friendly_name(state)
            if names:
                outstanding.append(f"{label}: {join_names(names, self.max_items)}")
            else:
                outstanding.append(f"{count} on {label}")

        if not outstanding:
            return None
        section.lines.append(f"{'; '.join(outstanding[: self.max_items])}.")
        return section

    def _house(self, kind: str, now: datetime) -> Section | None:
        section = Section("house")
        cutoff = time.time() - OVERNIGHT_HOURS * 3600

        unlocked = [
            friendly_name(s)
            for s in self.jarvis.states.all("lock")
            if s.state == "unlocked"
        ]
        if unlocked:
            section.lines.append(
                f"{join_names(unlocked, self.max_items)} "
                f"{'is' if len(unlocked) == 1 else 'are'} unlocked."
            )

        open_things = [
            friendly_name(s)
            for s in self.jarvis.states.all("cover")
            if s.state == "open"
        ] + [
            friendly_name(s)
            for s in self.jarvis.states.all("binary_sensor")
            if s.state == STATE_ON
            and str(s.attributes.get("device_class") or "") in DOOR_CLASSES
        ]
        if open_things:
            section.lines.append(f"{join_names(open_things, self.max_items)} still open.")

        if kind == "evening":
            lights_on = [
                friendly_name(s) for s in self.jarvis.states.all("light") if s.state == STATE_ON
            ]
            if lights_on:
                section.lines.append(
                    f"{len(lights_on)} light{'s' if len(lights_on) != 1 else ''} still on"
                    f" ({join_names(lights_on, 3)})."
                )

        low_batteries = []
        for state in self.jarvis.states.all("sensor"):
            if str(state.attributes.get("device_class") or "") != "battery":
                continue
            level = _number(state.state)
            if level is not None and level <= 20:
                low_batteries.append(f"{friendly_name(state)} at {round(level)}%")
        if low_batteries:
            section.lines.append(f"Low battery: {join_names(low_batteries, self.max_items)}.")

        if kind == "morning":
            overnight = [
                friendly_name(s)
                for s in self.jarvis.states.all("binary_sensor")
                if s.last_changed >= cutoff
                and str(s.attributes.get("device_class") or "") in DOOR_CLASSES
                and s.state == STATE_OFF
            ]
            if overnight:
                section.lines.append(
                    f"Opened and closed again overnight: {join_names(overnight, self.max_items)}."
                )

        return section

    def _unavailable(self, kind: str, now: datetime) -> Section | None:
        broken = [
            friendly_name(s)
            for s in self.jarvis.states.all()
            if s.state == STATE_UNAVAILABLE
        ]
        if not broken:
            return None
        section = Section("unavailable_entities")
        section.lines.append(
            f"{len(broken)} thing{'s' if len(broken) != 1 else ''} "
            f"{'are' if len(broken) != 1 else 'is'} unavailable: "
            f"{join_names(sorted(broken), self.max_items)}."
        )
        return section


# ---------------------------------------------------------------------------
# delivery + schedule
# ---------------------------------------------------------------------------
class BriefingManager:
    """Builds, delivers, and (optionally) does it twice a day on its own."""

    def __init__(self, jarvis: "Jarvis", options: dict[str, Any] | None = None) -> None:
        options = options or {}
        self.jarvis = jarvis
        self.builder = BriefingBuilder(
            jarvis,
            include=options.get("include") or DEFAULT_SECTIONS,
            max_items=int(options.get("max_items") or DEFAULT_MAX_ITEMS),
            max_chars=int(options.get("max_chars") or DEFAULT_MAX_CHARS),
        )
        self.importance = str(options.get("importance") or "low")
        self.schedule: dict[str, Any] = {}
        for kind, default in (("morning", DEFAULT_MORNING), ("evening", DEFAULT_EVENING)):
            raw = options.get(kind, default)
            if raw in (None, False, ""):
                continue
            parsed = parse_time(raw)
            if parsed is None:
                _LOGGER.warning("briefing: unparsable %s time %r; ignoring", kind, raw)
                continue
            self.schedule[kind] = parsed
        self.last: dict[str, Any] = {}
        self._task: asyncio.Task | None = None

    # --- generate / deliver ----------------------------------------------
    def generate(self, kind: str = "now", include: Iterable[str] | None = None) -> dict[str, Any]:
        result = self.builder.build(kind, include)
        self.last = result
        return result

    async def deliver(
        self,
        kind: str = "now",
        include: Iterable[str] | None = None,
        device_id: str | None = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        briefing = self.generate(kind, include)
        if briefing["empty"] or not briefing["text"]:
            # Nothing to say beats saying nothing at length.
            return {"status": "skipped", "reason": "nothing worth reporting", **briefing}

        self.jarvis.bus.fire(EVENT_BRIEFING_READY, dict(briefing), context)

        if not self.jarvis.services.has_service("companion", "notify"):
            _LOGGER.info("briefing: companion is not set up; briefing not delivered")
            return {"status": "undelivered", "reason": "companion.notify is not available",
                    **briefing}

        data: dict[str, Any] = {
            "message": briefing["text"],
            # `notify` lets the presence layer decide: spoken when the user is
            # actually at a device, a quiet notification otherwise.
            "kind": "notify",
            "importance": self.importance,
        }
        if device_id:
            data["device_id"] = device_id
        delivery = await self.jarvis.services.async_call(
            "companion", "notify", data, blocking=True,
            context=context or Context(origin="internal"), return_response=True,
        )
        self.builder.last_delivered[kind] = time.time()
        return {"status": "delivered", "delivery": delivery, **briefing}

    # --- schedule ---------------------------------------------------------
    def start(self) -> None:
        if self._task is not None or not self.schedule:
            return
        self._task = self.jarvis.async_create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown path
                pass

    def next_due(self, now: datetime) -> tuple[str, datetime] | None:
        upcoming = [
            (kind, next_time_of_day(now, at)) for kind, at in self.schedule.items()
        ]
        if not upcoming:
            return None
        upcoming.sort(key=lambda item: item[1])
        return upcoming[0]

    async def _run(self) -> None:
        clock = get_clock(self.jarvis)
        while True:
            due = self.next_due(clock.now())
            if due is None:
                return
            kind, when = due
            delay = (when - clock.now()).total_seconds()
            await clock.sleep(max(1.0, delay))
            try:
                await self.deliver(kind)
            except asyncio.CancelledError:
                raise
            except Exception:  # a failed briefing must not kill the schedule
                _LOGGER.exception("briefing: %s briefing failed", kind)


def get_briefing(jarvis: "Jarvis") -> BriefingManager | None:
    manager = jarvis.data.get(DOMAIN)
    return manager if isinstance(manager, BriefingManager) else None


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    manager = BriefingManager(jarvis, options)
    jarvis.data[DOMAIN] = manager

    async def handle_generate(call: ServiceCall) -> dict[str, Any]:
        return manager.generate(
            kind=str(call.get("kind") or "now"),
            include=call.get("include"),
        )

    async def handle_deliver(call: ServiceCall) -> dict[str, Any]:
        return await manager.deliver(
            kind=str(call.get("kind") or "now"),
            include=call.get("include"),
            device_id=call.get("device_id"),
            context=call.context,
        )

    jarvis.services.register(
        DOMAIN, "generate", handle_generate, supports_response=True,
        description="Build the digest without sending it anywhere.",
        fields={
            "kind": {"description": "morning | evening | now."},
            "include": {"description": "Override the configured section list."},
        },
    )
    jarvis.services.register(
        DOMAIN, "deliver", handle_deliver, supports_response=True,
        description="Build the digest and send it wherever the user actually is.",
        fields={
            "kind": {"description": "morning | evening | now."},
            "include": {"description": "Override the configured section list."},
            "device_id": {"description": "Force a specific device."},
        },
    )

    def _start(event: Any = None) -> None:
        manager.start()

    if jarvis.is_running:
        manager.start()
    else:
        jarvis.bus.listen(EVENT_JARVIS_START, _start)
    jarvis.register_shutdown(manager.stop)

    _register_tools(jarvis, manager)
    _LOGGER.info(
        "briefing ready: %s",
        ", ".join(f"{k} at {v}" for k, v in manager.schedule.items()) or "on demand only",
    )
    return True


def _register_tools(jarvis: "Jarvis", manager: BriefingManager) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("briefing: no LLM tool registry; services registered without tools")
        return

    from ...llm.tools import schema_object

    async def tool_get_briefing(args: dict[str, Any], context: Any = None) -> Any:
        briefing = manager.generate(
            kind=str(args.get("kind") or "now"), include=args.get("include")
        )
        if briefing["empty"]:
            return {
                "status": "ok",
                "empty": True,
                "text": "",
                "note": "Nothing needs reporting. Say so in one short sentence.",
            }
        return {
            "status": "ok",
            "empty": False,
            "kind": briefing["kind"],
            "text": briefing["text"],
            "sections": {s["key"]: s["text"] for s in briefing["sections"]},
        }

    registry.register(
        name="get_briefing",
        description=(
            "A short digest of the house right now: weather, what is on today, "
            "outstanding tasks, anything left open or unlocked, and anything that "
            "has gone offline. Use it for \"what's happening today\", \"anything I "
            "should know\", or before saying goodnight."
        ),
        parameters=schema_object(
            {
                "kind": {
                    "type": "string",
                    "description": "morning, evening, or now (default).",
                },
            }
        ),
        handler=tool_get_briefing,
        domain=DOMAIN,
    )


__all__ = [
    "DOMAIN",
    "BriefingBuilder",
    "BriefingManager",
    "Section",
    "async_setup",
    "get_briefing",
]
