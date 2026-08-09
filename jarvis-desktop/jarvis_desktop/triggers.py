"""Triggers: things this machine notices, pushed to jarvis-core as ``device_event``.

Four kinds, all optional and all configured locally:

* ``schedule`` — a 5-field cron expression, evaluated locally.
* ``file`` — a watched path, by polling. No inotify/FSEvents dependency, and
  polling is honest about what it can miss.
* ``idle`` — the session going idle or active again, best effort per OS.
* ``manual`` — fired by the CLI or by another part of the agent.

**A trigger fires an event, never an action.** This is structural, not a
convention: :class:`TriggerManager` is constructed with an ``emit`` callback that
reaches the channel and nothing else. There is no reference to the action
registry anywhere in this module, so a file whose contents change cannot cause
anything to run on this machine — the server decides what, if anything, to do
with the event, and whatever it decides comes back through
``device_command`` and gets the full policy treatment.

The schedule arithmetic is pure and lives in :class:`CronSchedule`, so it is
tested without waiting for a clock to move.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "CronSchedule",
    "CronError",
    "Trigger",
    "ScheduleTrigger",
    "FileWatchTrigger",
    "IdleTrigger",
    "ManualTrigger",
    "TriggerManager",
    "build_triggers",
]

EmitFn = Callable[[str, dict], Awaitable[bool]]


# --- schedule arithmetic (pure) ---------------------------------------------


class CronError(ValueError):
    """A cron expression that will not parse."""


_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

_MONTHS = {
    name: index
    for index, name in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}
_DOWS = {name: index for index, name in enumerate("sun mon tue wed thu fri sat".split())}


@dataclass(frozen=True)
class CronSchedule:
    """A 5-field cron expression: ``minute hour day-of-month month day-of-week``.

    Supports ``*``, ``a``, ``a-b``, ``a-b/n``, ``*/n`` and comma lists, plus the
    usual ``@daily``-style aliases and three-letter month/day names. Day-of-week
    accepts 0-7 with both 0 and 7 meaning Sunday.

    Matches Vixie cron's day semantics: when *both* day-of-month and day-of-week
    are restricted, a day matches if *either* does. That surprises people, so it
    is worth stating — ``0 0 1 * mon`` fires on the 1st and on every Monday.
    """

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    expression: str = ""

    @staticmethod
    def parse(expression: str) -> "CronSchedule":
        text = (expression or "").strip().lower()
        if not text:
            raise CronError("empty cron expression")
        text = _ALIASES.get(text, text)
        fields = text.split()
        if len(fields) != 5:
            raise CronError(
                f"a cron expression needs 5 fields (minute hour dom month dow), got {len(fields)}"
            )
        minute, hour, dom, month, dow = fields
        return CronSchedule(
            minutes=_parse_field(minute, 0, 59, {}),
            hours=_parse_field(hour, 0, 23, {}),
            days=_parse_field(dom, 1, 31, {}),
            months=_parse_field(month, 1, 12, _MONTHS),
            weekdays=frozenset(
                0 if value == 7 else value for value in _parse_field(dow, 0, 7, _DOWS)
            ),
            expression=text,
        )

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes:
            return False
        if moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        # Python: Monday=0..Sunday=6. Cron: Sunday=0..Saturday=6.
        weekday = (moment.weekday() + 1) % 7
        dom_restricted = len(self.days) < 31
        dow_restricted = len(self.weekdays) < 7
        if dom_restricted and dow_restricted:
            return moment.day in self.days or weekday in self.weekdays
        if dom_restricted:
            return moment.day in self.days
        if dow_restricted:
            return weekday in self.weekdays
        return True

    def next_after(self, moment: datetime, horizon_days: int = 1900) -> datetime | None:
        """The first minute strictly after ``moment`` that matches.

        Minute granularity, seconds zeroed. Returns None when nothing matches
        inside the horizon (``0 0 30 2 *`` — February 30th — never matches).

        The horizon is a bit over five years rather than one, because
        ``0 12 29 2 *`` is a perfectly sensible schedule whose next occurrence
        can be nearly four years out. The walk skips whole months and whole days
        when they cannot match, so the worst case is a few thousand iterations,
        not a few million.
        """
        candidate = (moment.replace(second=0, microsecond=0)) + timedelta(minutes=1)
        limit = moment + timedelta(days=horizon_days)
        while candidate <= limit:
            if candidate.month not in self.months:
                # Skip to the 1st of the next month rather than minute by minute.
                candidate = _start_of_next_month(candidate)
                continue
            if not self._day_matches(candidate):
                candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if candidate.hour not in self.hours:
                candidate = candidate.replace(minute=0) + timedelta(hours=1)
                continue
            if candidate.minute not in self.minutes:
                candidate = candidate + timedelta(minutes=1)
                continue
            return candidate
        return None

    def _day_matches(self, moment: datetime) -> bool:
        weekday = (moment.weekday() + 1) % 7
        dom_restricted = len(self.days) < 31
        dow_restricted = len(self.weekdays) < 7
        if dom_restricted and dow_restricted:
            return moment.day in self.days or weekday in self.weekdays
        if dom_restricted:
            return moment.day in self.days
        if dow_restricted:
            return weekday in self.weekdays
        return True


def _start_of_next_month(moment: datetime) -> datetime:
    if moment.month == 12:
        return moment.replace(year=moment.year + 1, month=1, day=1, hour=0, minute=0)
    return moment.replace(month=moment.month + 1, day=1, hour=0, minute=0)


def _parse_field(
    field_text: str, low: int, high: int, names: Mapping[str, int]
) -> frozenset[int]:
    values: set[int] = set()
    for part in field_text.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty element in {field_text!r}")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"bad step in {field_text!r}")
            step = int(step_text)
            if not part:
                part = "*"
        if part == "*":
            start, end = low, high
        elif "-" in part[1:] or (part.startswith("-") is False and "-" in part):
            start_text, _, end_text = part.partition("-")
            start = _named(start_text, names, low, high, field_text)
            end = _named(end_text, names, low, high, field_text)
        else:
            start = end = _named(part, names, low, high, field_text)
        if start > end:
            raise CronError(f"range {start}-{end} is backwards in {field_text!r}")
        values.update(range(start, end + 1, step))
    if not values:
        raise CronError(f"{field_text!r} matches nothing")
    return frozenset(values)


def _named(text: str, names: Mapping[str, int], low: int, high: int, whole: str) -> int:
    token = text.strip()
    if token in names:
        return names[token]
    if not re.fullmatch(r"\d+", token):
        raise CronError(f"{token!r} is not a number or a known name in {whole!r}")
    value = int(token)
    if not (low <= value <= high):
        raise CronError(f"{value} is outside {low}-{high} in {whole!r}")
    return value


# --- triggers ---------------------------------------------------------------


@dataclass
class Trigger:
    """Base: an id, an enable flag, and one coroutine that runs forever."""

    id: str
    enabled: bool = True

    async def run(self, emit: EmitFn, stop: asyncio.Event) -> None:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "kind": type(self).__name__, "enabled": self.enabled}


@dataclass
class ScheduleTrigger(Trigger):
    """Fires ``schedule`` on a cron expression."""

    expression: str = "0 * * * *"
    payload: dict = field(default_factory=dict)
    schedule: CronSchedule | None = None
    #: Injected so tests do not wait for a real minute to pass.
    now: Callable[[], datetime] = datetime.now
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    def __post_init__(self) -> None:
        if self.schedule is None:
            self.schedule = CronSchedule.parse(self.expression)

    async def run(self, emit: EmitFn, stop: asyncio.Event) -> None:
        assert self.schedule is not None
        while not stop.is_set():
            moment = self.now()
            upcoming = self.schedule.next_after(moment)
            if upcoming is None:
                _LOGGER.warning(
                    "schedule %s (%s) will never fire; stopping it", self.id, self.expression
                )
                return
            delay = max(0.0, (upcoming - moment).total_seconds())
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return  # stop was set
            except asyncio.TimeoutError:
                pass
            await emit(
                "schedule",
                {
                    "trigger": self.id,
                    "expression": self.expression,
                    "fired_at": upcoming.isoformat(timespec="seconds"),
                    **self.payload,
                },
            )

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out["expression"] = self.expression
        return out


@dataclass
class FileWatchTrigger(Trigger):
    """Fires ``file_changed`` when a watched path appears, changes or vanishes.

    Polling, on purpose: no third-party watcher, works the same on every OS, and
    the failure mode (missing a change that was reverted between polls) is
    obvious rather than subtle.

    Only *metadata* is reported — path, size, mtime, what happened. The file's
    contents are never read here. Anything that wants them has to ask for
    ``read_file``, which is an action, which means policy sees it.
    """

    path: str = ""
    interval_s: float = 5.0
    recursive: bool = False
    _state: dict[str, tuple[int, float]] = field(default_factory=dict, repr=False)

    async def run(self, emit: EmitFn, stop: asyncio.Event) -> None:
        target = Path(os.path.expanduser(self.path))
        self._state = self._scan(target)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.5, self.interval_s))
                return
            except asyncio.TimeoutError:
                pass
            current = self._scan(target)
            for change, name, stamp in _diff(self._state, current):
                await emit(
                    "file_changed",
                    {
                        "trigger": self.id,
                        "path": name,
                        "change": change,
                        "size_bytes": stamp[0] if stamp else None,
                        "modified": stamp[1] if stamp else None,
                    },
                )
            self._state = current

    def _scan(self, target: Path) -> dict[str, tuple[int, float]]:
        out: dict[str, tuple[int, float]] = {}
        try:
            if target.is_dir():
                walker = target.rglob("*") if self.recursive else target.glob("*")
                for entry in walker:
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    out[str(entry)] = (stat.st_size, stat.st_mtime)
            elif target.exists():
                stat = target.stat()
                out[str(target)] = (stat.st_size, stat.st_mtime)
        except OSError as exc:
            _LOGGER.debug("file watch %s: %s", self.id, exc)
        return out

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out.update({"path": self.path, "interval_s": self.interval_s})
        return out


def _diff(
    before: Mapping[str, tuple[int, float]], after: Mapping[str, tuple[int, float]]
) -> list[tuple[str, str, tuple[int, float] | None]]:
    changes: list[tuple[str, str, tuple[int, float] | None]] = []
    for name, stamp in after.items():
        if name not in before:
            changes.append(("created", name, stamp))
        elif before[name] != stamp:
            changes.append(("modified", name, stamp))
    for name in before:
        if name not in after:
            changes.append(("deleted", name, None))
    return changes


@dataclass
class IdleTrigger(Trigger):
    """Fires ``idle`` and ``active`` around the session going quiet.

    Best effort by design. ``xprintidle`` on X11, ``ioreg`` on macOS,
    ``GetLastInputInfo`` via PowerShell on Windows; a Wayland session usually
    exposes nothing, and this reports itself unavailable rather than guessing.
    """

    threshold_s: float = 300.0
    interval_s: float = 30.0
    probe: Callable[[], float | None] | None = None
    _idle: bool = False

    async def run(self, emit: EmitFn, stop: asyncio.Event) -> None:
        probe = self.probe or system_idle_seconds
        if probe() is None:
            _LOGGER.info(
                "idle detection is not available on this machine; trigger %s is inert", self.id
            )
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(1.0, self.interval_s))
                return
            except asyncio.TimeoutError:
                pass
            idle_for = probe()
            if idle_for is None:
                continue
            if not self._idle and idle_for >= self.threshold_s:
                self._idle = True
                await emit("idle", {"trigger": self.id, "idle_s": round(idle_for, 1)})
            elif self._idle and idle_for < self.threshold_s:
                self._idle = False
                await emit("active", {"trigger": self.id, "idle_s": round(idle_for, 1)})

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        out.update({"threshold_s": self.threshold_s})
        return out


def system_idle_seconds() -> float | None:
    """Seconds since the last input, or None when this machine cannot say."""
    import shutil

    if shutil.which("xprintidle"):
        try:
            proc = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=5, check=False
            )
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                return int(proc.stdout.strip()) / 1000.0
        except (OSError, subprocess.TimeoutExpired):
            pass
    if shutil.which("ioreg"):
        try:
            proc = subprocess.run(
                ["ioreg", "-c", "IOHIDSystem"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', proc.stdout or "")
            if match:
                return int(match.group(1)) / 1_000_000_000.0
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.name == "nt":
        try:
            import ctypes

            class _Info(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _Info()
            info.cbSize = ctypes.sizeof(_Info)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
                tick = ctypes.windll.kernel32.GetTickCount()  # type: ignore[attr-defined]
                return max(0.0, (tick - info.dwTime) / 1000.0)
        except Exception:  # noqa: BLE001
            pass
    return None


@dataclass
class ManualTrigger(Trigger):
    """A queue the CLI or another component can push into.

    Used for "tell the server I just plugged in" style events and as the seam
    the tests fire through.
    """

    queue: asyncio.Queue | None = None

    def __post_init__(self) -> None:
        if self.queue is None:
            self.queue = asyncio.Queue(maxsize=64)

    async def fire(self, event: str, data: Mapping[str, Any] | None = None) -> None:
        assert self.queue is not None
        await self.queue.put((event, dict(data or {})))

    async def run(self, emit: EmitFn, stop: asyncio.Event) -> None:
        assert self.queue is not None
        while not stop.is_set():
            getter = asyncio.ensure_future(self.queue.get())
            stopper = asyncio.ensure_future(stop.wait())
            done, pending = await asyncio.wait(
                {getter, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if getter in done:
                event, data = getter.result()
                await emit(event, {"trigger": self.id, **data})
            else:
                return


class TriggerManager:
    """Owns the trigger tasks and the one path they have to the outside.

    ``emit`` is the *only* thing a trigger is handed. It reaches the channel and
    nothing else — no registry, no dispatcher, no policy store. That is what
    makes "a trigger cannot cause an action" a property of the wiring rather
    than a rule someone has to remember.
    """

    def __init__(self, emit: EmitFn, triggers: Sequence[Trigger] = ()) -> None:
        self._emit = emit
        self.triggers: list[Trigger] = [t for t in triggers if t.enabled]
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    def add(self, trigger: Trigger) -> None:
        if trigger.enabled:
            self.triggers.append(trigger)

    async def start(self) -> None:
        self._stop.clear()
        for trigger in self.triggers:
            task = asyncio.create_task(
                self._guard(trigger), name=f"trigger:{trigger.id}"
            )
            self._tasks.append(task)

    async def _guard(self, trigger: Trigger) -> None:
        try:
            await trigger.run(self._emit, self._stop)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one broken trigger must not stop the rest
            _LOGGER.warning("trigger %s failed", trigger.id, exc_info=True)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def describe(self) -> list[dict[str, Any]]:
        return [t.describe() for t in self.triggers]


def build_triggers(specs: Iterable[Mapping[str, Any]]) -> list[Trigger]:
    """Turn the ``triggers`` list from the config file into objects.

    A spec that will not parse is logged and skipped: one bad cron line should
    not stop the agent from starting.
    """
    out: list[Trigger] = []
    for index, spec in enumerate(specs):
        kind = str(spec.get("type", "")).lower()
        trigger_id = str(spec.get("id") or f"{kind or 'trigger'}-{index}")
        enabled = spec.get("enabled", True) is not False
        try:
            if kind in ("schedule", "cron"):
                out.append(
                    ScheduleTrigger(
                        id=trigger_id,
                        enabled=enabled,
                        expression=str(spec.get("cron") or spec.get("expression") or "0 * * * *"),
                        payload=dict(spec.get("payload") or {}),
                    )
                )
            elif kind in ("file", "file_watch", "watch"):
                path = str(spec.get("path") or "")
                if not path:
                    raise ValueError("file trigger needs a path")
                out.append(
                    FileWatchTrigger(
                        id=trigger_id,
                        enabled=enabled,
                        path=path,
                        interval_s=float(spec.get("interval_s", 5.0)),
                        recursive=bool(spec.get("recursive", False)),
                    )
                )
            elif kind == "idle":
                out.append(
                    IdleTrigger(
                        id=trigger_id,
                        enabled=enabled,
                        threshold_s=float(spec.get("threshold_s", 300.0)),
                        interval_s=float(spec.get("interval_s", 30.0)),
                    )
                )
            elif kind in ("manual", "server"):
                out.append(ManualTrigger(id=trigger_id, enabled=enabled))
            else:
                raise ValueError(f"unknown trigger type {kind!r}")
        except (ValueError, CronError) as exc:
            _LOGGER.error("skipping trigger %s: %s", trigger_id, exc)
    return out


def next_fire_times(expression: str, count: int = 5, start: datetime | None = None) -> list[str]:
    """Preview a cron expression, for ``python -m jarvis_desktop cron``."""
    schedule = CronSchedule.parse(expression)
    moment = start or datetime.now()
    out: list[str] = []
    for _ in range(count):
        upcoming = schedule.next_after(moment)
        if upcoming is None:
            break
        out.append(upcoming.isoformat(timespec="minutes"))
        moment = upcoming
    return out


def uptime_hint() -> float:
    return time.monotonic()
