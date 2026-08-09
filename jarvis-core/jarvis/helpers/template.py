"""Jinja2 templating bound to a Jarvis instance.

This is the shared rendering layer: REST/MQTT/command_line payloads,
template entities, automation service data and notification bodies all go
through :func:`render` / :func:`render_complex`.

The helper surface is deliberately Home Assistant compatible so templates
copied from an existing HA configuration keep working::

    {{ states('sensor.outside_temperature') | float(0) }}
    {% if is_state('binary_sensor.motion', 'on') %}...{% endif %}
    {{ state_attr('light.bed', 'brightness') }}
    {{ now().hour }}
    {{ value_json.main.temp }}

Public API (other integrations import these — keep the signatures stable)::

    render(jarvis, tpl: str, variables: dict | None = None) -> str
    render_complex(jarvis, value, variables=None) -> Any

`render` always returns a string. `render_complex` walks dicts/lists and
renders any string that looks like a template, converting the result to a
native Python type when it unambiguously is one ("12" -> 12, "[1, 2]" ->
[1, 2]) — the behaviour YAML-driven config expects.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable

from jinja2 import ChainableUndefined, Environment, Template, pass_context
from jinja2 import TemplateError as _JinjaTemplateError
from jinja2 import Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ..state import slugify as _slugify
from ..state import split_entity_id

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"

# Values that mean "on"/true when a template result is read as a boolean.
_TRUE_STRINGS = frozenset({"true", "yes", "on", "enable", "enabled", "1", "open", "home"})
_FALSE_STRINGS = frozenset(
    {"false", "no", "off", "disable", "disabled", "0", "closed", "not_home", "none", ""}
)

# `domain.object_id` occurrences inside a template (used for dependency hints).
_ENTITY_RE = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z0-9_]+)\b")

_TEMPLATE_MARKERS = ("{{", "{%", "{#")

# Compiled templates are cached by source text; the env is process-wide and
# jarvis-independent (the jarvis binding travels in the render variables).
_CACHE: dict[str, Template] = {}
_CACHE_MAX = 512

_SENTINEL = object()


class TemplateError(Exception):
    """Raised when a template fails to compile or render."""


# ---------------------------------------------------------------------------
# state wrappers
# ---------------------------------------------------------------------------
class TemplateState:
    """A read-only view of one entity, printable as its state string."""

    __slots__ = ("_state", "entity_id")

    def __init__(self, entity_id: str, state: Any) -> None:
        self.entity_id = entity_id
        self._state = state

    @property
    def state(self) -> str:
        return self._state.state if self._state is not None else STATE_UNKNOWN

    @property
    def attributes(self) -> dict[str, Any]:
        return dict(self._state.attributes) if self._state is not None else {}

    @property
    def domain(self) -> str:
        return split_entity_id(self.entity_id)[0]

    @property
    def object_id(self) -> str:
        return split_entity_id(self.entity_id)[1]

    @property
    def name(self) -> str:
        if self._state is not None:
            return self._state.name
        return self.object_id.replace("_", " ").title()

    @property
    def last_changed(self) -> datetime:
        stamp = getattr(self._state, "last_changed", 0.0)
        return datetime.fromtimestamp(stamp or 0.0, tz=timezone.utc)

    @property
    def last_updated(self) -> datetime:
        stamp = getattr(self._state, "last_updated", 0.0)
        return datetime.fromtimestamp(stamp or 0.0, tz=timezone.utc)

    @property
    def exists(self) -> bool:
        return self._state is not None

    def __str__(self) -> str:
        return self.state

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TemplateState {self.entity_id}={self.state}>"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, TemplateState):
            return self.entity_id == other.entity_id and self.state == other.state
        if isinstance(other, str):
            return self.state == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.entity_id)

    def __bool__(self) -> bool:
        return result_as_boolean(self.state)


class DomainStates:
    """`states.light` — attribute access and iteration over one domain."""

    __slots__ = ("_jarvis", "_domain")

    def __init__(self, jarvis: "Jarvis", domain: str) -> None:
        self._jarvis = jarvis
        self._domain = domain

    def __getattr__(self, object_id: str) -> TemplateState:
        if object_id.startswith("_"):
            raise AttributeError(object_id)
        entity_id = f"{self._domain}.{object_id}"
        return TemplateState(entity_id, self._jarvis.states.get(entity_id))

    __getitem__ = __getattr__

    def __iter__(self):
        for state in sorted(
            self._jarvis.states.all(self._domain), key=lambda s: s.entity_id
        ):
            yield TemplateState(state.entity_id, state)

    def __len__(self) -> int:
        return len(self._jarvis.states.all(self._domain))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DomainStates {self._domain}>"


class AllStates:
    """The `states` global: callable, iterable and attribute-addressable."""

    __slots__ = ("_jarvis",)

    def __init__(self, jarvis: "Jarvis") -> None:
        self._jarvis = jarvis

    def __call__(self, entity_id: Any = None, default: Any = STATE_UNKNOWN) -> Any:
        if entity_id is None or isinstance(entity_id, Undefined):
            return default
        state = self._jarvis.states.get(_entity_id_of(entity_id))
        return state.state if state is not None else default

    def __getattr__(self, domain: str) -> DomainStates:
        if domain.startswith("_"):
            raise AttributeError(domain)
        return DomainStates(self._jarvis, domain)

    __getitem__ = __getattr__

    def __iter__(self):
        for state in sorted(self._jarvis.states.all(), key=lambda s: s.entity_id):
            yield TemplateState(state.entity_id, state)

    def __len__(self) -> int:
        return len(self._jarvis.states.all())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<AllStates>"


def _entity_id_of(value: Any) -> str:
    if isinstance(value, TemplateState):
        return value.entity_id
    return str(value)


# ---------------------------------------------------------------------------
# value coercion
# ---------------------------------------------------------------------------
def result_as_boolean(value: Any) -> bool:
    """HA-compatible truthiness for a rendered template result."""
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, Undefined):
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
    except (TypeError, ValueError):
        return bool(text)


def parse_result(text: str) -> Any:
    """Turn a rendered string into a native value when it clearly is one."""
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if not stripped or len(stripped) > 10_000:
        return text
    # Leading zeros / plus signs must stay strings (zip codes, phone numbers).
    if re.fullmatch(r"[+-]?0\d+", stripped):
        return text
    lowered = stripped.lower()
    if lowered == "none":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if stripped[0] not in "-+0123456789[{(\"'.":
        return text
    try:
        value = ast.literal_eval(stripped)
    except (ValueError, SyntaxError, MemoryError, TypeError, RecursionError):
        return text
    if isinstance(value, (int, float, complex)) and not isinstance(value, bool):
        # NaN/inf round-trip poorly; keep the text.
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return text
    if isinstance(value, (int, float, bool, list, tuple, dict, set, str, complex)):
        return value
    return text


def is_template(value: Any) -> bool:
    """True when `value` is a string containing Jinja markers."""
    return isinstance(value, str) and any(m in value for m in _TEMPLATE_MARKERS)


def extract_entities(template: Any) -> set[str]:
    """Best-effort set of `domain.object_id` ids referenced by a template."""
    if not isinstance(template, str):
        return set()
    return {f"{domain}.{object_id}" for domain, object_id in _ENTITY_RE.findall(template)}


# ---------------------------------------------------------------------------
# filters / globals
# ---------------------------------------------------------------------------
def forgiving_float(value: Any, default: Any = _SENTINEL) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        if default is _SENTINEL:
            raise TemplateError(f"cannot convert {value!r} to float") from None
        return default


def forgiving_int(value: Any, default: Any = _SENTINEL, base: int = 10) -> Any:
    try:
        return int(value, base) if isinstance(value, str) else int(float(value))
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            if default is _SENTINEL:
                raise TemplateError(f"cannot convert {value!r} to int") from None
            return default


def forgiving_round(value: Any, precision: int = 0, default: Any = _SENTINEL) -> Any:
    try:
        result = round(float(value), precision)
    except (TypeError, ValueError):
        if default is _SENTINEL:
            raise TemplateError(f"cannot round {value!r}") from None
        return default
    return int(result) if precision == 0 else result


def now() -> datetime:
    """Current local time (timezone aware)."""
    return datetime.now().astimezone()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_datetime(value: Any, default: Any = None) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        text = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return default


def as_timestamp(value: Any, default: Any = None) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    parsed = as_datetime(value)
    if parsed is None:
        return default
    return parsed.timestamp()


def timestamp_custom(value: Any, fmt: str = "%Y-%m-%d %H:%M:%S", local: bool = True) -> Any:
    try:
        stamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return value
    if local:
        stamp = stamp.astimezone()
    return stamp.strftime(fmt)


def timestamp_local(value: Any) -> Any:
    return timestamp_custom(value, "%Y-%m-%dT%H:%M:%S%z", True)


def timestamp_utc(value: Any) -> Any:
    return timestamp_custom(value, "%Y-%m-%dT%H:%M:%S%z", False)


def strptime(text: Any, fmt: str, default: Any = None) -> Any:
    try:
        return datetime.strptime(str(text), fmt)
    except (TypeError, ValueError):
        return default


def relative_time(value: Any) -> str:
    """Rough human phrasing of how long ago `value` was."""
    stamp = as_datetime(value)
    if stamp is None:
        return str(value)
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    delta = utcnow() - stamp.astimezone(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "in the future"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _flatten(args: tuple[Any, ...]) -> list[Any]:
    if len(args) == 1 and isinstance(args[0], Iterable) and not isinstance(args[0], (str, bytes)):
        return list(args[0])
    return list(args)


def forgiving_min(*args: Any) -> Any:
    items = _flatten(args)
    return min(items) if items else None


def forgiving_max(*args: Any) -> Any:
    items = _flatten(args)
    return max(items) if items else None


def average(*args: Any) -> Any:
    items = [forgiving_float(v, None) for v in _flatten(args)]
    items = [v for v in items if v is not None]
    return sum(items) / len(items) if items else None


def iif(value: Any, if_true: Any = True, if_false: Any = False, if_none: Any = _SENTINEL) -> Any:
    if value is None or isinstance(value, Undefined):
        return if_false if if_none is _SENTINEL else if_none
    return if_true if result_as_boolean(value) else if_false


def to_json(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, indent=indent, default=str)


def from_json(value: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise TemplateError(f"invalid JSON: {value!r}") from exc


def regex_match(value: Any, find: str = "", ignorecase: bool = False) -> bool:
    flags = re.IGNORECASE if ignorecase else 0
    return re.match(find, str(value), flags) is not None


def regex_search(value: Any, find: str = "", ignorecase: bool = False) -> bool:
    flags = re.IGNORECASE if ignorecase else 0
    return re.search(find, str(value), flags) is not None


def regex_replace(value: Any, find: str = "", replace: str = "", ignorecase: bool = False) -> str:
    flags = re.IGNORECASE if ignorecase else 0
    return re.sub(find, replace, str(value), flags=flags)


def regex_findall(value: Any, find: str = "", ignorecase: bool = False) -> list[Any]:
    flags = re.IGNORECASE if ignorecase else 0
    return re.findall(find, str(value), flags)


def regex_findall_index(value: Any, find: str = "", index: int = 0, ignorecase: bool = False) -> Any:
    found = regex_findall(value, find, ignorecase)
    return found[index] if found else None


def ordinal(value: Any) -> str:
    number = forgiving_int(value, 0)
    suffix = "th"
    if number % 100 not in (11, 12, 13):
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def bool_filter(value: Any) -> bool:
    return result_as_boolean(value)


# --- jarvis-aware helpers (available as globals *and* filters) -------------
def _jarvis_from(ctx: Any) -> "Jarvis | None":
    jarvis = ctx.get("_jarvis") if ctx is not None else None
    return None if isinstance(jarvis, Undefined) else jarvis


@pass_context
def _filter_states(ctx: Any, entity_id: Any, default: Any = STATE_UNKNOWN) -> Any:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return default
    state = jarvis.states.get(_entity_id_of(entity_id))
    return state.state if state is not None else default


@pass_context
def _filter_state_attr(ctx: Any, entity_id: Any, attribute: str) -> Any:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return None
    state = jarvis.states.get(_entity_id_of(entity_id))
    return state.attributes.get(attribute) if state is not None else None


@pass_context
def _filter_is_state(ctx: Any, entity_id: Any, value: Any) -> bool:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return False
    state = jarvis.states.get(_entity_id_of(entity_id))
    if state is None:
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return state.state in {str(v) for v in value}
    return state.state == str(value)


@pass_context
def _filter_is_state_attr(ctx: Any, entity_id: Any, attribute: str, value: Any) -> bool:
    return _filter_state_attr(ctx, entity_id, attribute) == value


@pass_context
def _filter_has_value(ctx: Any, entity_id: Any) -> bool:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return False
    state = jarvis.states.get(_entity_id_of(entity_id))
    return state is not None and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)


@pass_context
def _filter_expand(ctx: Any, *args: Any) -> list[TemplateState]:
    """Flatten entity ids / lists into TemplateState objects."""
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return []
    found: dict[str, TemplateState] = {}

    def _add(item: Any) -> None:
        if item is None or isinstance(item, Undefined):
            return
        if isinstance(item, TemplateState):
            found[item.entity_id] = item
            return
        if isinstance(item, str):
            state = jarvis.states.get(item)
            if state is not None:
                found[item] = TemplateState(item, state)
            return
        if isinstance(item, Iterable):
            for sub in item:
                _add(sub)

    for arg in args:
        _add(arg)
    return [found[key] for key in sorted(found)]


@pass_context
def _filter_area_id(ctx: Any, entity_id: Any) -> Any:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return None
    return jarvis.area_for_entity(_entity_id_of(entity_id))


@pass_context
def _filter_area_name(ctx: Any, entity_id: Any) -> Any:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return None
    area_id = jarvis.area_for_entity(_entity_id_of(entity_id))
    if area_id is None:
        return None
    area = jarvis.areas.areas.get(area_id)
    return area.name if area else area_id


@pass_context
def _filter_area_entities(ctx: Any, area: Any) -> list[str]:
    jarvis = _jarvis_from(ctx)
    if jarvis is None:
        return []
    entry = jarvis.areas.areas.get(str(area)) or jarvis.areas.get_by_name(str(area))
    if entry is None:
        return []
    return sorted(
        e.entity_id for e in jarvis.entities.entities_in_area(entry.id, jarvis.devices)
    )


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------
def _build_environment() -> Environment:
    # SANDBOXED, not a plain Environment. A plain jinja2.Environment hands any
    # template author a Python interpreter — `{{ cycler.__init__.__globals__ }}`
    # reaches `os` and from there `popen`. Templates arrive from YAML today,
    # but they also flow in from automations, scripts and (indirectly) the LLM
    # tool layer, so the renderer must not be the thing standing between a
    # config string and arbitrary code execution.
    env = ImmutableSandboxedEnvironment(
        undefined=ChainableUndefined,
        autoescape=False,  # templates render config values, not HTML
        trim_blocks=False,
        keep_trailing_newline=False,
    )

    env.filters.update(
        {
            "float": forgiving_float,
            "int": forgiving_int,
            "round": forgiving_round,
            "bool": bool_filter,
            "multiply": lambda v, x: forgiving_float(v) * float(x),
            "add": lambda v, x: forgiving_float(v) + float(x),
            "log": lambda v, base=math.e: math.log(forgiving_float(v), float(base)),
            "sqrt": lambda v: math.sqrt(forgiving_float(v)),
            "average": lambda v: average(v),
            "min": forgiving_min,
            "max": forgiving_max,
            "as_timestamp": as_timestamp,
            "as_datetime": as_datetime,
            "timestamp_custom": timestamp_custom,
            "timestamp_local": timestamp_local,
            "timestamp_utc": timestamp_utc,
            "relative_time": relative_time,
            "to_json": to_json,
            "from_json": from_json,
            "regex_match": regex_match,
            "regex_search": regex_search,
            "regex_replace": regex_replace,
            "regex_findall": regex_findall,
            "regex_findall_index": regex_findall_index,
            "ordinal": ordinal,
            "slugify": lambda v: _slugify(str(v)),
            "iif": iif,
            "contains": lambda v, x: x in (v or []),
            "states": _filter_states,
            "state_attr": _filter_state_attr,
            "is_state": _filter_is_state,
            "is_state_attr": _filter_is_state_attr,
            "has_value": _filter_has_value,
            "expand": _filter_expand,
            "area_id": _filter_area_id,
            "area_name": _filter_area_name,
        }
    )

    env.tests.update(
        {
            "match": lambda v, find="", ignorecase=False: regex_match(v, find, ignorecase),
            "search": lambda v, find="", ignorecase=False: regex_search(v, find, ignorecase),
        }
    )

    env.globals.update(
        {
            "float": forgiving_float,
            "int": forgiving_int,
            "round": forgiving_round,
            "bool": bool_filter,
            "min": forgiving_min,
            "max": forgiving_max,
            "average": average,
            "now": now,
            "utcnow": utcnow,
            "as_datetime": as_datetime,
            "as_timestamp": as_timestamp,
            "strptime": strptime,
            "relative_time": relative_time,
            "timedelta": timedelta,
            "iif": iif,
            "to_json": to_json,
            "from_json": from_json,
            "slugify": lambda v: _slugify(str(v)),
            "log": lambda v, base=math.e: math.log(forgiving_float(v), float(base)),
            "sqrt": lambda v: math.sqrt(forgiving_float(v)),
            "pi": math.pi,
            "e": math.e,
            "expand": _filter_expand,
            "area_id": _filter_area_id,
            "area_name": _filter_area_name,
            "area_entities": _filter_area_entities,
            "has_value": _filter_has_value,
            "state_attr": _filter_state_attr,
            "is_state": _filter_is_state,
            "is_state_attr": _filter_is_state_attr,
        }
    )
    return env


_ENV: Environment = _build_environment()


def environment() -> Environment:
    """The shared Jinja environment (exposed for advanced integrations)."""
    return _ENV


def compile_template(tpl: str) -> Template:
    """Compile (and cache) one template string."""
    cached = _CACHE.get(tpl)
    if cached is not None:
        return cached
    try:
        compiled = _ENV.from_string(tpl)
    except _JinjaTemplateError as exc:
        raise TemplateError(f"invalid template {tpl!r}: {exc}") from exc
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[tpl] = compiled
    return compiled


def _render_variables(jarvis: "Jarvis", variables: dict[str, Any] | None) -> dict[str, Any]:
    """Build the per-render namespace (jarvis bindings + caller variables)."""
    variables = dict(variables or {})
    # `value_json` is offered automatically whenever a raw payload is present,
    # matching how REST/MQTT/command_line templates are written.
    if "value" in variables and "value_json" not in variables:
        raw = variables["value"]
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                variables["value_json"] = json.loads(raw)
            except (TypeError, ValueError):
                variables["value_json"] = None
        elif isinstance(raw, (dict, list)):
            variables["value_json"] = raw

    namespace: dict[str, Any] = {
        "_jarvis": jarvis,
        # `now` is in env.globals too, but that environment is module-global and
        # so cannot know which Jarvis is rendering. A per-render binding can, and
        # a variable shadows a global in Jinja — so `{{ now().hour }}` in a
        # condition reads the hour in `jarvis: time_zone:`, the same zone
        # `at: "07:00:00"` fires in. Two clocks disagreeing inside one automation
        # is worse than either being wrong.
        "now": _zoned_now(jarvis),
        "states": AllStates(jarvis) if jarvis is not None else None,
        "is_state": lambda entity_id, value: _is_state(jarvis, entity_id, value),
        "is_state_attr": lambda entity_id, attr, value: (
            _state_attr(jarvis, entity_id, attr) == value
        ),
        "state_attr": lambda entity_id, attr: _state_attr(jarvis, entity_id, attr),
        "has_value": lambda entity_id: _has_value(jarvis, entity_id),
    }
    namespace.update(variables)
    return namespace


def _zoned_now(jarvis: "Jarvis") -> Any:
    """`now` bound to this instance's clock, falling back to the plain one.

    Imported lazily: `jarvis.automation.util` imports helpers of its own, and a
    module-level import here closes the loop.
    """
    if jarvis is None:
        return now
    try:
        from ..automation.util import get_clock  # noqa: PLC0415 - cycle
    except ImportError:  # pragma: no cover - defensive
        return now
    clock = get_clock(jarvis)
    return clock.now


def _is_state(jarvis: "Jarvis", entity_id: Any, value: Any) -> bool:
    if jarvis is None:
        return False
    state = jarvis.states.get(_entity_id_of(entity_id))
    if state is None:
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return state.state in {str(v) for v in value}
    return state.state == str(value)


def _state_attr(jarvis: "Jarvis", entity_id: Any, attribute: str) -> Any:
    if jarvis is None:
        return None
    state = jarvis.states.get(_entity_id_of(entity_id))
    return state.attributes.get(attribute) if state is not None else None


def _has_value(jarvis: "Jarvis", entity_id: Any) -> bool:
    if jarvis is None:
        return False
    state = jarvis.states.get(_entity_id_of(entity_id))
    return state is not None and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def render(jarvis: "Jarvis", tpl: str, variables: dict[str, Any] | None = None) -> str:
    """Render `tpl` against Jarvis state and return the result as a string.

    Raises :class:`TemplateError` when the template is invalid or blows up
    at render time — callers usually turn that into an unavailable entity.
    """
    if not isinstance(tpl, str):
        tpl = str(tpl)
    compiled = compile_template(tpl)
    try:
        return compiled.render(_render_variables(jarvis, variables))
    except TemplateError:
        raise
    except _JinjaTemplateError as exc:
        raise TemplateError(f"error rendering {tpl!r}: {exc}") from exc
    except Exception as exc:  # a template must never take the caller down
        raise TemplateError(f"error rendering {tpl!r}: {exc}") from exc


def render_value(
    jarvis: "Jarvis", tpl: str, variables: dict[str, Any] | None = None
) -> Any:
    """Like :func:`render` but converts the result to a native type."""
    return parse_result(render(jarvis, tpl, variables))


def render_bool(
    jarvis: "Jarvis", tpl: str, variables: dict[str, Any] | None = None
) -> bool:
    """Render and interpret the result as a boolean (HA truthiness)."""
    return result_as_boolean(render(jarvis, tpl, variables))


def render_complex(
    jarvis: "Jarvis", value: Any, variables: dict[str, Any] | None = None
) -> Any:
    """Walk `value`, rendering every templated string it contains.

    Dicts and lists are rebuilt with rendered members; templated strings are
    rendered and converted to native types; everything else is returned
    unchanged.
    """
    if isinstance(value, str):
        if not is_template(value):
            return value
        return parse_result(render(jarvis, value, variables))
    if isinstance(value, dict):
        return {
            render_complex(jarvis, key, variables) if is_template(key) else key: (
                render_complex(jarvis, item, variables)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        rendered = [render_complex(jarvis, item, variables) for item in value]
        return type(value)(rendered) if isinstance(value, tuple) else rendered
    return value


def render_safe(
    jarvis: "Jarvis",
    tpl: str,
    variables: dict[str, Any] | None = None,
    default: Any = None,
) -> Any:
    """Render, returning `default` instead of raising on failure."""
    try:
        return render(jarvis, tpl, variables)
    except TemplateError as exc:
        _LOGGER.debug("Template failed (%s); using default", exc)
        return default
