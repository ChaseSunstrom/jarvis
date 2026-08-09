"""Condition evaluation.

``async_check(jarvis, config, variables) -> bool`` understands the Home
Assistant condition shapes::

    {condition: state, entity_id: ..., state: ..., for: ..., attribute: ...}
    {condition: numeric_state, entity_id: ..., above: ..., below: ...}
    {condition: template, value_template: "{{ ... }}"}
    {condition: time, after: "07:00", before: "22:00", weekday: [mon, tue]}
    {condition: time, after: sunset, before: "sunrise + 00:30"}
    {condition: and|or|not, conditions: [...]}
    {condition: trigger, id: "motion"}

A bare list is an implicit ``and``; a bare template string is a ``template``
condition; a plain bool passes straight through. Dicts without a
``condition:`` key are inferred from their contents, so ``{entity_id: x,
state: "on"}`` works as shorthand.
"""

from __future__ import annotations

import logging
import re
import time as time_module
from datetime import datetime, time as dt_time, timedelta
from typing import TYPE_CHECKING, Any

from .util import (
    WEEKDAYS,
    as_float,
    as_list,
    get_clock,
    is_template,
    parse_duration,
    parse_time,
    render_bool,
    render_template,
    result_as_boolean,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis
    from ..state import State

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# individual conditions
# ---------------------------------------------------------------------------
def _state_value(state: "State | None", attribute: str | None) -> Any:
    if state is None:
        return None
    if attribute:
        return state.attributes.get(attribute)
    return state.state


def _value_matches(value: Any, expected: Any) -> bool:
    if expected is None:
        return value is None
    if value == expected:
        return True
    return str(value) == str(expected)


def _check_state(jarvis: "Jarvis", config: dict[str, Any], variables: dict[str, Any]) -> bool:
    entity_ids = [str(e) for e in as_list(config.get("entity_id"))]
    if not entity_ids:
        _LOGGER.warning("state condition without entity_id")
        return False
    attribute = config.get("attribute")
    expected = config.get("state")
    if isinstance(expected, str) and is_template(expected):
        expected = render_template(jarvis, expected, variables)
    expected_values = as_list(expected) if isinstance(expected, (list, tuple)) else [expected]
    for_seconds = parse_duration(config.get("for"))
    match = str(config.get("match", "all")).lower()

    results: list[bool] = []
    for entity_id in entity_ids:
        state = jarvis.states.get(entity_id)
        if state is None:
            results.append(False)
            continue
        value = _state_value(state, attribute)
        ok = any(_value_matches(value, item) for item in expected_values)
        if ok and for_seconds:
            ok = (time_module.time() - state.last_changed) >= for_seconds
        results.append(ok)

    return any(results) if match == "any" else all(results)


def _numeric_value(
    jarvis: "Jarvis",
    state: "State | None",
    config: dict[str, Any],
    variables: dict[str, Any],
) -> float | None:
    value_template = config.get("value_template")
    if value_template:
        namespace = dict(variables)
        namespace.update(
            {
                "state": state,
                "value": state.state if state is not None else None,
                "entity_id": state.entity_id if state is not None else None,
            }
        )
        return as_float(render_template(jarvis, value_template, namespace))
    return as_float(_state_value(state, config.get("attribute")))


def _bound(jarvis: "Jarvis", raw: Any) -> float | None:
    """A bound may be a literal number or another entity's numeric state."""
    if isinstance(raw, str) and "." in raw and not raw.replace(".", "", 1).isdigit():
        other = jarvis.states.get(raw)
        return as_float(other.state) if other else None
    return as_float(raw)


def _check_numeric_state(
    jarvis: "Jarvis", config: dict[str, Any], variables: dict[str, Any]
) -> bool:
    entity_ids = [str(e) for e in as_list(config.get("entity_id"))]
    above = config.get("above")
    below = config.get("below")
    if not entity_ids:
        _LOGGER.warning("numeric_state condition without entity_id")
        return False

    for entity_id in entity_ids:
        state = jarvis.states.get(entity_id)
        value = _numeric_value(jarvis, state, config, variables)
        if value is None:
            return False
        if above is not None:
            limit = _bound(jarvis, above)
            if limit is None or value <= limit:
                return False
        if below is not None:
            limit = _bound(jarvis, below)
            if limit is None or value >= limit:
                return False
    return True


def _check_time(jarvis: "Jarvis", config: dict[str, Any], variables: dict[str, Any]) -> bool:
    now = get_clock(jarvis).now()

    weekdays = [str(day).strip().lower()[:3] for day in as_list(config.get("weekday"))]
    if weekdays and WEEKDAYS[now.weekday()] not in weekdays:
        return False

    bounds: dict[str, Any] = {}
    for key in ("after", "before"):
        if key not in config or config[key] is None:
            bounds[key] = None
            continue
        raw = _resolve_time(jarvis, config[key], variables)
        resolved = parse_time(raw) or _solar_time(jarvis, raw, now)
        if resolved is None:
            # Falling through with `None` used to mean "no bound", so an
            # unreadable window (`after: sunset` with no sun integration, a
            # typo, an input_datetime that has not been set) quietly passed at
            # every hour of the day. A window we cannot evaluate is not met.
            _LOGGER.warning(
                "time condition: cannot read %s: %r — treating the condition "
                "as not met",
                key,
                config[key],
            )
            return False
        bounds[key] = resolved

    after = bounds["after"]
    before = bounds["before"]
    current = now.time()

    if after is not None and before is not None:
        if after <= before:
            return after <= current < before
        return current >= after or current < before  # window spans midnight
    if after is not None:
        return current >= after
    if before is not None:
        return current < before
    return True


#: ``sunset``, ``sunrise + 00:30``, ``dusk-01:00`` …
_SOLAR_RE = re.compile(
    r"^(sunrise|sunset|dawn|dusk|noon|midnight)"
    r"(?:\s*([+-])\s*(\d{1,2}:\d{2}(?::\d{2})?))?$",
    re.IGNORECASE,
)


def _solar_time(jarvis: "Jarvis", value: Any, now: datetime) -> "dt_time | None":
    """Resolve a solar bound to a local time of day.

    Uses whatever the `sun` integration published at ``jarvis.data["sun"]``;
    returns None (and the caller fails closed) when sun is not set up.
    """
    match = _SOLAR_RE.match(str(value).strip())
    if not match:
        return None
    sun = (getattr(jarvis, "data", None) or {}).get("sun")
    if sun is None or not hasattr(sun, "next"):
        _LOGGER.warning("time condition wants %r but the sun integration is not set up", value)
        return None
    event, sign, offset_text = match.group(1).lower(), match.group(2), match.group(3)
    seconds = parse_duration(offset_text) or 0.0
    offset = timedelta(seconds=-seconds if sign == "-" else seconds)
    try:
        instant = sun.next(event, now, offset)
    except Exception:
        _LOGGER.exception("sun.next(%r) failed", event)
        return None
    if instant is None:
        return None
    # The next occurrence's time of day; solar times drift about a minute a
    # day, which is far below the resolution anyone writes these rules at.
    return instant.astimezone(now.tzinfo).time()


def _resolve_time(jarvis: "Jarvis", value: Any, variables: dict[str, Any]) -> Any:
    """Allow ``after: input_datetime.bedtime`` and templated times."""
    if isinstance(value, str) and is_template(value):
        return render_template(jarvis, value, variables)
    if isinstance(value, str) and "." in value and ":" not in value:
        state = jarvis.states.get(value)
        if state is not None:
            return state.state
    return value


def _check_trigger(config: dict[str, Any], variables: dict[str, Any]) -> bool:
    wanted = {str(i) for i in as_list(config.get("id"))}
    trigger = variables.get("trigger") or {}
    current = trigger.get("id") if isinstance(trigger, dict) else None
    return current is not None and str(current) in wanted


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
def _infer_condition(config: dict[str, Any]) -> str | None:
    if "value_template" in config or "template" in config:
        return "template"
    if "above" in config or "below" in config:
        return "numeric_state"
    if "state" in config or ("entity_id" in config and "attribute" in config):
        return "state"
    if "after" in config or "before" in config or "weekday" in config:
        return "time"
    if "conditions" in config:
        return "and"
    return None


async def async_check(
    jarvis: "Jarvis", config: Any, variables: dict[str, Any] | None = None
) -> bool:
    """Evaluate one condition config (see module docstring for the shapes)."""
    variables = variables or {}

    if config is None:
        return True
    if isinstance(config, bool):
        return config
    if isinstance(config, str):
        return render_bool(jarvis, config, variables)
    if isinstance(config, (list, tuple)):
        return await async_check_all(jarvis, config, variables)
    if not isinstance(config, dict):
        _LOGGER.warning("Unsupported condition config: %r", config)
        return False

    kind = config.get("condition")
    if isinstance(kind, (dict, list, tuple)):  # {condition: {...}} wrapper
        return await async_check(jarvis, kind, variables)
    if isinstance(kind, bool):
        return kind
    if kind is None:
        inferred = _infer_condition(config)
        if inferred is None:
            _LOGGER.warning("Condition without a `condition:` key: %r", config)
            return False
        kind = inferred
    kind = str(kind).strip().lower()

    try:
        if kind == "state":
            return _check_state(jarvis, config, variables)
        if kind == "numeric_state":
            return _check_numeric_state(jarvis, config, variables)
        if kind == "template":
            tpl = config.get("value_template", config.get("template"))
            return render_bool(jarvis, tpl, variables)
        if kind == "time":
            return _check_time(jarvis, config, variables)
        if kind == "trigger":
            return _check_trigger(config, variables)
        if kind == "and":
            return await async_check_all(jarvis, config.get("conditions"), variables)
        if kind == "or":
            for item in as_list(config.get("conditions")):
                if await async_check(jarvis, item, variables):
                    return True
            return False
        if kind == "not":
            for item in as_list(config.get("conditions")):
                if await async_check(jarvis, item, variables):
                    return False
            return True
        if kind in ("true", "always"):
            return True
        if kind in ("false", "never"):
            return False
    except Exception:
        _LOGGER.exception("Error evaluating %s condition: %r", kind, config)
        return False

    _LOGGER.warning("Unknown condition type %r", kind)
    return False


async def async_check_all(
    jarvis: "Jarvis", configs: Any, variables: dict[str, Any] | None = None
) -> bool:
    """Implicit AND over a list of conditions (empty list passes)."""
    for config in as_list(configs):
        if not await async_check(jarvis, config, variables):
            return False
    return True


__all__ = ["async_check", "async_check_all", "result_as_boolean"]
