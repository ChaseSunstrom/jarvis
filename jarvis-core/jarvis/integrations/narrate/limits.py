"""The part that stops this feature being turned off after a week.

A house full of sensors is a house full of *flapping* sensors. One PIR with
a loose connection, one contact sensor on a door in a draught, and a system
that narrates state changes will read them out four hundred times before
breakfast. That is not a nuisance, it is the failure mode: people don't
tune it, they disable it, and then they miss the one message that mattered.

So delivery passes four independent gates, and each has to say yes:

1. **debounce** — the same rule, about the same entity, at most once every
   ``min_interval`` seconds;
2. **per-rule ceiling** — a rule may narrate at most ``max_per_hour`` times,
   however many entities it covers;
3. **burst ceiling** — at most ``max_burst`` narrations in any
   ``burst_window`` seconds, across everything;
4. **global ceiling** — at most ``max_per_hour`` narrations in any hour,
   across everything.

Three and four are the ones that hold when a rule is misconfigured. A sensor
flapping a hundred times a second cannot produce more than ``max_burst``
notifications, whatever the rules say, because the cap is counted on
delivery rather than on matching.

Everything here is pure: pass the time in, get a decision out.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Iterable

HOUR = 3600.0

DEFAULT_MIN_INTERVAL = 300.0
DEFAULT_MAX_PER_HOUR = 20
DEFAULT_MAX_BURST = 5
DEFAULT_BURST_WINDOW = 60.0

#: How many ``(rule, entity)`` debounce stamps to hold before sweeping the
#: expired ones. A rule that selects a whole domain keeps one stamp per entity
#: it has ever narrated about, and entities outlive the sensors that made them
#: (an MQTT broker can mint them, and `sensors.forget` does not un-narrate the
#: past), so without a sweep this dict only ever grows.
DEFAULT_MAX_TRACKED = 4096

#: The sweep walks the whole table, so it is rate-limited: an oversized table
#: is scanned at most once a minute, however many state changes arrive.
SWEEP_INTERVAL = 60.0

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


# ---------------------------------------------------------------------------
# quiet hours
# ---------------------------------------------------------------------------
def parse_hhmm(value: Any) -> int | None:
    """``"23:00"`` -> minutes since midnight; ``None`` when unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minutes = int(value) * 60 if value <= 24 else int(value)
        return minutes % (24 * 60)
    match = _HHMM_RE.match(str(value).strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 24 or minute > 59:
        return None
    return (hour * 60 + minute) % (24 * 60)


def parse_window(value: Any) -> tuple[int, int] | None:
    """``["23:00", "07:00"]`` or ``"23:00-07:00"`` -> ``(1380, 420)``."""
    if value in (None, False, ""):
        return None
    if isinstance(value, str):
        parts = [p for p in re.split(r"\s*(?:-|to)\s*", value.strip()) if p]
    elif isinstance(value, Iterable):
        parts = [str(p) for p in value]
    else:
        return None
    if len(parts) != 2:
        return None
    start, end = parse_hhmm(parts[0]), parse_hhmm(parts[1])
    if start is None or end is None or start == end:
        return None
    return start, end


def in_window(minutes: int, window: tuple[int, int] | None) -> bool:
    """Is ``minutes`` inside the window? Handles windows crossing midnight."""
    if window is None:
        return False
    start, end = window
    minutes %= 24 * 60
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def local_minutes(now: float, localtime: Any = None) -> int:
    """Minutes since local midnight for a POSIX timestamp."""
    import time as _time

    stamp = (localtime or _time.localtime)(now)
    return int(stamp.tm_hour) * 60 + int(stamp.tm_min)


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


@dataclass
class NarrationLimiter:
    """Every ceiling in one object. ``allow()`` decides *and* records."""

    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    max_burst: int = DEFAULT_MAX_BURST
    burst_window: float = DEFAULT_BURST_WINDOW
    max_tracked: int = DEFAULT_MAX_TRACKED

    _global: Deque[float] = field(default_factory=deque, repr=False)
    _per_rule: dict[str, Deque[float]] = field(default_factory=dict, repr=False)
    _last: dict[tuple[str, str], float] = field(default_factory=dict, repr=False)
    _longest_interval: float = field(default=0.0, repr=False)
    _last_sweep: float = field(default=0.0, repr=False)

    # --- reads ------------------------------------------------------------
    def _prune(self, now: float) -> None:
        while self._global and now - self._global[0] > HOUR:
            self._global.popleft()
        for key, stamps in list(self._per_rule.items()):
            while stamps and now - stamps[0] > HOUR:
                stamps.popleft()
            if not stamps:
                del self._per_rule[key]  # a rule that has gone quiet
        self._sweep_debounce(now)

    def _sweep_debounce(self, now: float) -> None:
        """Drop debounce stamps too old to hold anything back any more.

        Kept off the hot path twice over: nothing is scanned while the table is
        within its bound, and an oversized table is scanned at most once every
        :data:`SWEEP_INTERVAL`.
        """
        if len(self._last) <= self.max_tracked:
            return
        if self._last_sweep and now - self._last_sweep < SWEEP_INTERVAL:
            return
        self._last_sweep = now
        # An entry only matters while it can still debounce something, so the
        # horizon is the longest `min_interval` this limiter has been asked
        # about — never less than the hour the other ceilings use.
        horizon = max(HOUR, self._longest_interval)
        self._last = {
            key: stamp for key, stamp in self._last.items() if now - stamp <= horizon
        }

    def check(
        self,
        rule_key: str,
        entity_id: str,
        now: float,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        rule_max_per_hour: int | None = None,
    ) -> Decision:
        """Would this be delivered? Records nothing."""
        if min_interval > self._longest_interval:
            self._longest_interval = min_interval
        self._prune(now)

        last = self._last.get((rule_key, entity_id))
        if last is not None and min_interval > 0 and now - last < min_interval:
            return Decision(False, f"debounced ({min_interval:g}s)")

        if rule_max_per_hour is not None and rule_max_per_hour >= 0:
            used = len(self._per_rule.get(rule_key, ()))
            if used >= rule_max_per_hour:
                return Decision(False, f"rule cap ({rule_max_per_hour}/hour)")

        if self.max_burst >= 0:
            recent = sum(1 for t in self._global if now - t <= self.burst_window)
            if recent >= self.max_burst:
                return Decision(
                    False, f"burst cap ({self.max_burst} per {self.burst_window:g}s)"
                )

        if self.max_per_hour >= 0 and len(self._global) >= self.max_per_hour:
            return Decision(False, f"global cap ({self.max_per_hour}/hour)")

        return Decision(True, "ok")

    # --- writes -----------------------------------------------------------
    def record(self, rule_key: str, entity_id: str, now: float) -> None:
        self._last[(rule_key, entity_id)] = now
        self._global.append(now)
        self._per_rule.setdefault(rule_key, deque()).append(now)

    def allow(
        self,
        rule_key: str,
        entity_id: str,
        now: float,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        rule_max_per_hour: int | None = None,
    ) -> Decision:
        """Check and, if it passes, spend the budget. The normal entry point."""
        decision = self.check(rule_key, entity_id, now, min_interval, rule_max_per_hour)
        if decision.allowed:
            self.record(rule_key, entity_id, now)
        return decision

    # --- introspection ----------------------------------------------------
    def delivered_in_last(self, seconds: float, now: float) -> int:
        return sum(1 for t in self._global if now - t <= seconds)

    def tracked(self) -> int:
        """How many debounce stamps are being held (for tests and status)."""
        return len(self._last)

    def reset(self) -> None:
        self._global.clear()
        self._per_rule.clear()
        self._last.clear()
        self._longest_interval = 0.0
        self._last_sweep = 0.0


__all__ = [
    "DEFAULT_BURST_WINDOW",
    "DEFAULT_MAX_BURST",
    "DEFAULT_MAX_PER_HOUR",
    "DEFAULT_MAX_TRACKED",
    "DEFAULT_MIN_INTERVAL",
    "Decision",
    "NarrationLimiter",
    "in_window",
    "local_minutes",
    "parse_hhmm",
    "parse_window",
]
