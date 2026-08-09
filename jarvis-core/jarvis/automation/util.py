"""Shared plumbing for the automation engine.

Templates are rendered through ``jarvis.helpers.template`` when it is
available; a small self-contained fallback keeps the engine usable (and
importable) if that helper ever goes missing.

Also here: duration/time parsing (``5``, ``"00:00:05"``, ``{minutes: 2}``)
and the injectable :class:`Clock` used by the time triggers/conditions so
tests never have to wait for a wall clock.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time as dt_time, timedelta
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

_TEMPLATE_MARKERS = ("{{", "{%", "{#")
_TRUE_STRINGS = frozenset({"true", "yes", "on", "enable", "enabled", "1", "open", "home"})
_FALSE_STRINGS = frozenset(
    {"false", "no", "off", "disable", "disabled", "0", "closed", "not_home", "none", ""}
)

# --- template layer (imported defensively) ---------------------------------
try:  # pragma: no cover - exercised implicitly by every render
    from ..helpers.template import is_template as _is_template
    from ..helpers.template import render as _render
    from ..helpers.template import render_complex as _render_complex
    from ..helpers.template import result_as_boolean as _result_as_boolean

    HAS_TEMPLATE_HELPER = True
except ImportError:  # pragma: no cover - fallback path
    HAS_TEMPLATE_HELPER = False

    _SIMPLE_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")

    def _is_template(value: Any) -> bool:
        return isinstance(value, str) and any(m in value for m in _TEMPLATE_MARKERS)

    def _lookup(variables: dict[str, Any], path: str) -> Any:
        current: Any = variables
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return ""
        return current

    def _render(jarvis: Any, tpl: str, variables: dict[str, Any] | None = None) -> str:
        return _SIMPLE_VAR.sub(
            lambda m: str(_lookup(dict(variables or {}), m.group(1))), str(tpl)
        )

    def _render_complex(
        jarvis: Any, value: Any, variables: dict[str, Any] | None = None
    ) -> Any:
        if isinstance(value, str):
            return _render(jarvis, value, variables) if _is_template(value) else value
        if isinstance(value, dict):
            return {k: _render_complex(jarvis, v, variables) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_render_complex(jarvis, v, variables) for v in value]
        return value

    def _result_as_boolean(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
        try:
            return float(text) != 0
        except ValueError:
            return bool(text)


def is_template(value: Any) -> bool:
    """True when `value` is a string carrying Jinja markers."""
    return _is_template(value)


def result_as_boolean(value: Any) -> bool:
    """HA-compatible truthiness for a rendered result."""
    return _result_as_boolean(value)


def render_complex(
    jarvis: "Jarvis", value: Any, variables: dict[str, Any] | None = None
) -> Any:
    """Render every templated string inside `value`; never raises."""
    try:
        return _render_complex(jarvis, value, variables)
    except Exception as exc:  # a bad template must not kill the run
        _LOGGER.warning("Template error in %r: %s", value, exc)
        return value


def render_template(
    jarvis: "Jarvis", tpl: Any, variables: dict[str, Any] | None = None
) -> str:
    """Render one template string (returns the input on failure)."""
    if not isinstance(tpl, str):
        return str(tpl)
    try:
        return _render(jarvis, tpl, variables)
    except Exception as exc:
        _LOGGER.warning("Template error in %r: %s", tpl, exc)
        return tpl


def render_bool(
    jarvis: "Jarvis", tpl: Any, variables: dict[str, Any] | None = None
) -> bool:
    """Render `tpl` and read the result as a boolean."""
    if isinstance(tpl, bool):
        return tpl
    if isinstance(tpl, str) and is_template(tpl):
        return result_as_boolean(render_template(jarvis, tpl, variables))
    return result_as_boolean(tpl)


# --- small conversions ------------------------------------------------------
def as_list(value: Any) -> list[Any]:
    """Normalise scalar-or-list config values to a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def as_float(value: Any) -> float | None:
    """Best-effort float (None when it isn't numeric)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_duration(value: Any) -> float | None:
    """Seconds from ``5``, ``"00:01:30"``, ``"01:30"`` or ``{minutes: 2}``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        total = 0.0
        total += float(value.get("days", 0) or 0) * 86400
        total += float(value.get("hours", 0) or 0) * 3600
        total += float(value.get("minutes", 0) or 0) * 60
        total += float(value.get("seconds", 0) or 0)
        total += float(value.get("milliseconds", 0) or 0) / 1000.0
        return total
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            numbers = [float(p or 0) for p in parts]
        except ValueError:
            return None
        if len(numbers) == 2:  # HH:MM
            return numbers[0] * 3600 + numbers[1] * 60
        if len(numbers) >= 3:  # HH:MM:SS(.ms)
            return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_time(value: Any) -> dt_time | None:
    """Parse ``"07:00"`` / ``"07:00:00"`` / :class:`datetime.time`."""
    if value is None:
        return None
    if isinstance(value, dt_time):
        return value
    if isinstance(value, datetime):
        return value.time()
    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?", text)
    if not match:
        return None
    hour, minute, second = (int(match.group(i) or 0) for i in (1, 2, 3))
    if hour > 23 or minute > 59 or second > 59:
        return None
    return dt_time(hour, minute, second)


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# --- clock ------------------------------------------------------------------
DATA_CLOCK = "automation_clock"


class Clock:
    """Wall clock + sleep. Swap it out via ``jarvis.data["automation_clock"]``."""

    def now(self) -> datetime:
        return datetime.now().astimezone()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, float(seconds)))


_DEFAULT_CLOCK = Clock()


def get_clock(jarvis: "Jarvis") -> Clock:
    """The clock this Jarvis instance should use (injectable for tests)."""
    data = getattr(jarvis, "data", None)
    clock = data.get(DATA_CLOCK) if isinstance(data, dict) else None
    return clock if clock is not None else _DEFAULT_CLOCK


# --- time-pattern maths -----------------------------------------------------
def pattern_matcher(spec: Any) -> Callable[[int], bool]:
    """Build a matcher for a `time_pattern` field (``5``, ``"/5"``, ``"*"``)."""
    if isinstance(spec, int) and not isinstance(spec, bool):
        return lambda value, n=spec: value == n
    text = str(spec).strip()
    if text in ("*", ""):
        return lambda value: True
    if text.startswith("/") or text.startswith("*/"):
        try:
            step = int(text.lstrip("*/") or 1)
        except ValueError:
            return lambda value: True
        step = max(1, step)
        return lambda value, s=step: value % s == 0
    try:
        exact = int(text)
    except ValueError:
        return lambda value: True
    return lambda value, n=exact: value == n


def next_time_pattern(
    now: datetime, hours: Any = None, minutes: Any = None, seconds: Any = None
) -> datetime | None:
    """Next datetime strictly after `now` matching an HA-style time pattern.

    Unspecified units *smaller* than the smallest specified one default to
    zero (``minutes: "/5"`` fires at second 0); larger ones are wildcards.
    """
    specs = (hours, minutes, seconds)
    given = [i for i, spec in enumerate(specs) if spec is not None]
    smallest = max(given) if given else 2

    matchers: list[Callable[[int], bool]] = []
    for index, spec in enumerate(specs):
        if spec is not None:
            matchers.append(pattern_matcher(spec))
        elif index > smallest:
            matchers.append(lambda value: value == 0)
        else:
            matchers.append(lambda value: True)

    match_hour, match_minute, match_second = matchers
    candidate = now.replace(microsecond=0) + timedelta(seconds=1)
    for _ in range(2 * 86400 + 1):
        if (
            match_hour(candidate.hour)
            and match_minute(candidate.minute)
            and match_second(candidate.second)
        ):
            return candidate
        candidate += timedelta(seconds=1)
    return None


def next_time_of_day(now: datetime, target: dt_time) -> datetime:
    """Next datetime strictly after `now` at `target` time of day."""
    candidate = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=target.second,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
