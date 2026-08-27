"""Domain service layer — turns `light.turn_on` into a method call on an entity.

This integration owns the *verbs* of Jarvis. Every controllable domain
(light, switch, fan, siren, cover, climate, lock, media_player, number,
text, select, button, vacuum) gets its services registered here, and each
handler does the same four things:

1. resolve targets from ``entity_id`` / ``area_id`` / ``device_id``
   (``entity_id: all`` expands to every entity in the domain),
2. look up the live object via ``jarvis.entity_object(entity_id)``,
3. call the matching ``async_*`` method with the relevant kwargs and write
   the new state,
4. return ``{"changed": [...], "failed": {entity_id: reason}}`` so the LLM
   tool layer can say precisely what happened instead of guessing.

Entities that don't implement an action simply don't define the method —
they land in ``failed`` with a readable reason and the rest of the targets
still run. Entities with no live object at all ("virtual" entities that
only exist in the state machine) get their state set directly where that
is meaningful.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from ...const import (
    STATE_CLOSED,
    STATE_IDLE,
    STATE_LOCKED,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_UNKNOWN,
    STATE_UNLOCKED,
)
from ...services import ServiceCall
from ...state import slugify, split_entity_id

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...state import State

_LOGGER = logging.getLogger(__name__)

DOMAIN = "domains"

# Domains this layer serves (a few have no const entry yet — keep them local).
DOMAIN_LIGHT = "light"
DOMAIN_SWITCH = "switch"
DOMAIN_FAN = "fan"
DOMAIN_SIREN = "siren"
DOMAIN_COVER = "cover"
DOMAIN_CLIMATE = "climate"
DOMAIN_LOCK = "lock"
DOMAIN_MEDIA_PLAYER = "media_player"
DOMAIN_NUMBER = "number"
DOMAIN_TEXT = "text"
DOMAIN_SELECT = "select"
DOMAIN_BUTTON = "button"
DOMAIN_VACUUM = "vacuum"

# Target keys understood by every handler (never forwarded as method kwargs).
TARGET_KEYS = frozenset({"entity_id", "entity", "area_id", "area", "device_id", "device"})

ALL_TARGETS = frozenset({"all", "*"})

# Domain-specific states that have no const entry yet.
STATE_CLEANING = "cleaning"
STATE_RETURNING = "returning"

# States that count as "currently on" when deciding what `toggle` should do.
ON_STATES: dict[str, frozenset[str]] = {
    DOMAIN_COVER: frozenset({STATE_OPEN, "opening"}),
    DOMAIN_MEDIA_PLAYER: frozenset(
        {STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_ON, "buffering"}
    ),
    DOMAIN_VACUUM: frozenset({STATE_CLEANING, STATE_RETURNING, STATE_ON}),
}
DEFAULT_ON_STATES = frozenset({STATE_ON})


# ---------------------------------------------------------------------------
# value casting
# ---------------------------------------------------------------------------
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _cast_brightness(value: Any) -> int:
    return int(_clamp(round(float(value)), 0, 255))


def _cast_percent(value: Any) -> int:
    return int(_clamp(round(float(value)), 0, 100))


def _cast_kelvin(value: Any) -> int:
    return int(round(float(value)))


def _cast_float(value: Any) -> float:
    return float(value)


def _cast_volume(value: Any) -> float:
    return float(_clamp(float(value), 0.0, 1.0))


def _cast_rgb(value: Any) -> tuple[int, int, int]:
    if isinstance(value, str):
        parts = [p for p in value.replace("(", "").replace(")", "").split(",") if p.strip()]
    else:
        parts = list(value)
    if len(parts) < 3:
        raise ValueError(f"rgb_color needs 3 components, got {value!r}")
    return tuple(int(_clamp(round(float(p)), 0, 255)) for p in parts[:3])  # type: ignore[return-value]


def _cast_number(value: Any) -> Any:
    """Numbers arrive as strings from YAML/voice often enough to be worth it."""
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return value
    return int(as_float) if as_float.is_integer() else as_float


# ---------------------------------------------------------------------------
# spec model
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Param:
    """One piece of service data forwarded to the entity method."""

    key: str
    kwarg: str | None = None
    required: bool = False
    cast: Callable[[Any], Any] | None = None
    aliases: tuple[str, ...] = ()
    description: str = ""

    @property
    def name(self) -> str:
        return self.kwarg or self.key


# (current state | None, kwargs) -> (new state | None keeps current, extra attrs)
VirtualFn = Callable[["State | None", dict[str, Any]], tuple[str | None, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    domain: str
    service: str
    method: str
    params: tuple[Param, ...] = ()
    virtual: VirtualFn | None = None
    description: str = ""
    prepare: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    @property
    def key(self) -> str:
        return f"{self.domain}.{self.service}"


@dataclass(frozen=True, slots=True)
class ToggleSpec:
    domain: str
    on: ServiceSpec
    off: ServiceSpec
    on_states: frozenset[str] = DEFAULT_ON_STATES
    description: str = ""


@dataclass(slots=True)
class TargetSet:
    """Entities a service call applies to, plus targets that never resolved."""

    entity_ids: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    explicit: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# virtual-entity state fallbacks
# ---------------------------------------------------------------------------
_NON_ATTR_KWARGS = frozenset({"transition"})


def _v_on(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    return STATE_ON, {k: v for k, v in kwargs.items() if k not in _NON_ATTR_KWARGS}


def _v_off(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    return STATE_OFF, {}


def _v_keep(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    return None, {}


def _v_const(state: str) -> VirtualFn:
    def _inner(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        return state, {}

    return _inner


def _v_attrs(*names: str) -> VirtualFn:
    def _inner(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        return None, {n: kwargs[n] for n in names if n in kwargs}

    return _inner


def _v_position(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    position = int(kwargs.get("position", 0))
    return (STATE_OPEN if position > 0 else STATE_CLOSED), {"current_position": position}


def _v_state_from(name: str) -> VirtualFn:
    def _inner(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        value = kwargs.get(name)
        return (None if value is None else str(value)), {}

    return _inner


def _v_play_media(
    current: "State | None", kwargs: dict[str, Any]
) -> tuple[str | None, dict[str, Any]]:
    return STATE_PLAYING, {
        "media_content_type": kwargs.get("media_type"),
        "media_content_id": kwargs.get("media_id"),
    }


def _v_press(current: "State | None", kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    return datetime.now().astimezone().isoformat(timespec="seconds"), {}


# ---------------------------------------------------------------------------
# the service table
# ---------------------------------------------------------------------------
def _prepare_light(data: dict[str, Any]) -> dict[str, Any]:
    """Accept `brightness_pct` the way HA-flavoured YAML tends to write it."""
    if data.get("brightness") is None and data.get("brightness_pct") is not None:
        data = dict(data)
        data["brightness"] = _clamp(round(float(data["brightness_pct"]) * 255 / 100), 0, 255)
    return data


_LIGHT_ON_PARAMS = (
    Param("brightness", cast=_cast_brightness, description="Brightness 0-255."),
    Param(
        "color_temp_kelvin",
        cast=_cast_kelvin,
        aliases=("kelvin",),
        description="Colour temperature in kelvin.",
    ),
    Param("rgb_color", cast=_cast_rgb, description="[r, g, b] 0-255 each."),
    Param("transition", cast=_cast_float, description="Fade time in seconds."),
)
_LIGHT_OFF_PARAMS = (Param("transition", cast=_cast_float, description="Fade time in seconds."),)
_FAN_ON_PARAMS = (
    Param("percentage", cast=_cast_percent, description="Fan speed percentage."),
    Param("preset_mode", description="Named fan preset."),
)


def _on_off(
    domain: str,
    on_params: tuple[Param, ...] = (),
    off_params: tuple[Param, ...] = (),
    prepare: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[ServiceSpec, ServiceSpec]:
    return (
        ServiceSpec(
            domain, "turn_on", "async_turn_on", on_params, _v_on,
            f"Turn on one or more {domain} entities.", prepare,
        ),
        ServiceSpec(
            domain, "turn_off", "async_turn_off", off_params, _v_off,
            f"Turn off one or more {domain} entities.", prepare,
        ),
    )


def build_specs() -> tuple[list[ServiceSpec], list[ToggleSpec]]:
    """The full domain/service table. Pure data — safe to call repeatedly."""
    specs: list[ServiceSpec] = []
    toggles: list[ToggleSpec] = []

    light_on, light_off = _on_off(DOMAIN_LIGHT, _LIGHT_ON_PARAMS, _LIGHT_OFF_PARAMS, _prepare_light)
    switch_on, switch_off = _on_off(DOMAIN_SWITCH)
    fan_on, fan_off = _on_off(DOMAIN_FAN, _FAN_ON_PARAMS)
    siren_on, siren_off = _on_off(DOMAIN_SIREN)
    specs += [light_on, light_off, switch_on, switch_off, fan_on, fan_off, siren_on, siren_off]
    for domain, on, off in (
        (DOMAIN_LIGHT, light_on, light_off),
        (DOMAIN_SWITCH, switch_on, switch_off),
        (DOMAIN_FAN, fan_on, fan_off),
        (DOMAIN_SIREN, siren_on, siren_off),
    ):
        # Said as the flip it is: "turn on the coffee machine" reached
        # switch.toggle on the live house (27 Aug), which is right once and
        # wrong the next time — the named services are the ones to reach for.
        toggles.append(ToggleSpec(
            domain, on, off, DEFAULT_ON_STATES,
            f"Flip {domain} entities to their other state. Only when asked to toggle or flip; "
            f"'turn on' and 'turn off' are {domain}.turn_on and {domain}.turn_off.",
        ))

    # --- cover ------------------------------------------------------------
    cover_open = ServiceSpec(
        DOMAIN_COVER, "open_cover", "async_open_cover", (), _v_const(STATE_OPEN), "Open a cover."
    )
    cover_close = ServiceSpec(
        DOMAIN_COVER, "close_cover", "async_close_cover", (), _v_const(STATE_CLOSED), "Close a cover."
    )
    specs += [
        cover_open,
        cover_close,
        ServiceSpec(
            DOMAIN_COVER, "stop_cover", "async_stop_cover", (), _v_keep, "Stop a moving cover."
        ),
        ServiceSpec(
            DOMAIN_COVER,
            "set_cover_position",
            "async_set_cover_position",
            (
                Param(
                    "position",
                    required=True,
                    cast=_cast_percent,
                    aliases=("current_position",),
                    description="Target position 0 (closed) - 100 (open).",
                ),
            ),
            _v_position,
            # "Close the living room window" reached this with position 0 on
            # the seventeenth house (27 Aug 2026): true, and not what a person
            # would call it. The named services are the ones to reach for.
            "Move a cover part-way, to a position between open and closed. "
            "To open or close it fully use open_cover and close_cover.",
        ),
    ]
    # Generic verbs for covers. Callers that dispatch uniformly (`<domain>.turn_on`
    # for whatever the user named) must not fall off a cliff on covers.
    specs += [
        ServiceSpec(
            DOMAIN_COVER, "turn_on", "async_open_cover", (), _v_const(STATE_OPEN),
            "Open a cover (alias of open_cover).",
        ),
        ServiceSpec(
            DOMAIN_COVER, "turn_off", "async_close_cover", (), _v_const(STATE_CLOSED),
            "Close a cover (alias of close_cover).",
        ),
    ]
    toggles.append(
        ToggleSpec(
            DOMAIN_COVER, cover_open, cover_close, ON_STATES[DOMAIN_COVER],
            "Flip a cover to its other state: open if closed, closed if open. Only when asked to "
            "toggle; 'open' and 'close' are cover.open_cover and cover.close_cover.",
        )
    )

    # --- climate ----------------------------------------------------------
    specs += [
        ServiceSpec(
            DOMAIN_CLIMATE,
            "set_temperature",
            "async_set_temperature",
            (
                Param(
                    "temperature",
                    required=True,
                    cast=_cast_float,
                    description="Target temperature.",
                ),
            ),
            _v_attrs("temperature"),
            "Set a thermostat's target temperature.",
        ),
        ServiceSpec(
            DOMAIN_CLIMATE,
            "set_hvac_mode",
            "async_set_hvac_mode",
            (Param("hvac_mode", required=True, description="heat / cool / auto / off ..."),),
            _v_state_from("hvac_mode"),
            "Set a thermostat's HVAC mode.",
        ),
        ServiceSpec(
            DOMAIN_CLIMATE,
            "set_fan_mode",
            "async_set_fan_mode",
            (Param("fan_mode", required=True, description="Named fan mode."),),
            _v_attrs("fan_mode"),
            "Set a thermostat's fan mode.",
        ),
    ]

    # --- lock -------------------------------------------------------------
    specs += [
        ServiceSpec(DOMAIN_LOCK, "lock", "async_lock", (), _v_const(STATE_LOCKED), "Lock a lock."),
        ServiceSpec(
            DOMAIN_LOCK, "unlock", "async_unlock", (), _v_const(STATE_UNLOCKED), "Unlock a lock."
        ),
    ]

    # --- media_player -----------------------------------------------------
    mp_on, mp_off = _on_off(DOMAIN_MEDIA_PLAYER)
    specs += [
        mp_on,
        mp_off,
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER, "media_play", "async_media_play", (),
            _v_const(STATE_PLAYING), "Resume playback.",
        ),
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER, "media_pause", "async_media_pause", (),
            _v_const(STATE_PAUSED), "Pause playback.",
        ),
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER, "media_stop", "async_media_stop", (),
            _v_const(STATE_IDLE), "Stop playback.",
        ),
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER, "media_next_track", "async_media_next_track", (),
            _v_keep, "Skip to the next track.",
        ),
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER, "media_previous_track", "async_media_previous_track", (),
            _v_keep, "Go back to the previous track.",
        ),
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER,
            "volume_set",
            "async_volume_set",
            (
                Param(
                    "volume_level",
                    required=True,
                    cast=_cast_volume,
                    aliases=("volume",),
                    description="Volume 0.0 - 1.0.",
                ),
            ),
            _v_attrs("volume_level"),
            "Set playback volume.",
        ),
        ServiceSpec(
            DOMAIN_MEDIA_PLAYER,
            "play_media",
            "async_play_media",
            (
                Param(
                    "media_type",
                    required=True,
                    aliases=("media_content_type",),
                    description="music / tts / playlist ...",
                ),
                Param(
                    "media_id",
                    required=True,
                    aliases=("media_content_id",),
                    description="URL or provider id to play.",
                ),
            ),
            _v_play_media,
            "Play a specific piece of media.",
        ),
    ]
    toggles.append(
        ToggleSpec(
            DOMAIN_MEDIA_PLAYER,
            mp_on,
            mp_off,
            ON_STATES[DOMAIN_MEDIA_PLAYER],
            "Turn a media player on or off.",
        )
    )

    # --- number / text / select / button / vacuum -------------------------
    specs += [
        ServiceSpec(
            DOMAIN_NUMBER,
            "set_value",
            "async_set_value",
            (Param("value", required=True, cast=_cast_number, description="New numeric value."),),
            _v_state_from("value"),
            "Set a number entity's value.",
        ),
        ServiceSpec(
            DOMAIN_TEXT,
            "set_value",
            "async_set_value",
            (Param("value", required=True, description="New text value."),),
            _v_state_from("value"),
            "Set a text entity's value.",
        ),
        ServiceSpec(
            DOMAIN_SELECT,
            "select_option",
            "async_select_option",
            (Param("option", required=True, description="Option to select."),),
            _v_state_from("option"),
            "Choose an option on a select entity.",
        ),
        ServiceSpec(DOMAIN_BUTTON, "press", "async_press", (), _v_press, "Press a button."),
    ]

    # --- vacuum -----------------------------------------------------------
    vacuum_start = ServiceSpec(
        DOMAIN_VACUUM, "start", "async_start", (), _v_const(STATE_CLEANING), "Start cleaning."
    )
    vacuum_dock = ServiceSpec(
        DOMAIN_VACUUM,
        "return_to_base",
        "async_return_to_base",
        (),
        _v_const(STATE_RETURNING),
        "Send the vacuum back to its dock.",
    )
    specs += [
        vacuum_start,
        vacuum_dock,
        ServiceSpec(
            DOMAIN_VACUUM, "turn_on", "async_start", (), _v_const(STATE_CLEANING),
            "Start cleaning (alias of start).",
        ),
        ServiceSpec(
            DOMAIN_VACUUM, "turn_off", "async_return_to_base", (), _v_const(STATE_RETURNING),
            "Send the vacuum back to its dock (alias of return_to_base).",
        ),
    ]
    toggles.append(
        ToggleSpec(
            DOMAIN_VACUUM,
            vacuum_start,
            vacuum_dock,
            ON_STATES[DOMAIN_VACUUM],
            "Start the vacuum, or send it home if it is already out.",
        )
    )

    return specs, toggles


# ---------------------------------------------------------------------------
# target resolution
# ---------------------------------------------------------------------------
def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        out: list[str] = []
        for item in value:
            out.extend(_as_list(item))
        return out
    return [str(value)]


def _is_disabled(jarvis: "Jarvis", entity_id: str) -> bool:
    entry = jarvis.entities.get(entity_id)
    return entry is not None and entry.disabled


def _known_entity_ids(jarvis: "Jarvis", domain: str | None) -> list[str]:
    """Every entity we know about, states first (they're definitely live).

    A disabled registry entry is skipped even when it still has a state —
    a stale state must not make `entity_id: all` reach a disabled entity.
    """
    seen: dict[str, None] = {}
    for state in jarvis.states.all(domain):
        if _is_disabled(jarvis, state.entity_id):
            continue
        seen[state.entity_id] = None
    for entity_id, entry in jarvis.entities.entities.items():
        if entry.disabled:
            continue
        if domain and split_entity_id(entity_id)[0] != domain:
            continue
        seen[entity_id] = None
    return list(seen)


def _resolve_area_id(jarvis: "Jarvis", value: str) -> str | None:
    if value in jarvis.areas.areas:
        return value
    area = jarvis.areas.get_by_name(value)
    if area is not None:
        return area.id
    slug = slugify(value)
    return slug if slug in jarvis.areas.areas else None


def _entity_exists(jarvis: "Jarvis", entity_id: str) -> bool:
    return (
        jarvis.states.get(entity_id) is not None
        or jarvis.entities.get(entity_id) is not None
        or jarvis.entity_object(entity_id) is not None
    )


def has_target_keys(data: dict[str, Any]) -> bool:
    """True when the caller actually named something to act on."""
    return any(_as_list(data.get(key)) for key in TARGET_KEYS)


def resolve_targets(
    jarvis: "Jarvis", data: dict[str, Any], domain: str | None = None
) -> TargetSet:
    """Expand `entity_id` / `area_id` / `device_id` into concrete entity ids.

    `domain` filters area/device/"all" expansion (a `light.turn_on` on an
    area only touches lights). Explicitly named entities from another
    domain are reported in `failed` rather than silently dropped.
    """
    result = TargetSet()
    seen: set[str] = set()

    def add(entity_id: str, explicit: bool = False) -> None:
        if entity_id not in seen:
            seen.add(entity_id)
            result.entity_ids.append(entity_id)
        if explicit:
            result.explicit.add(entity_id)

    raw = _as_list(data.get("entity_id")) + _as_list(data.get("entity"))
    if any(item.lower() in ALL_TARGETS for item in raw):
        for entity_id in _known_entity_ids(jarvis, domain):
            add(entity_id)

    for item in raw:
        if item.lower() in ALL_TARGETS:
            continue
        entity_id = item.lower()
        target_domain, object_id = split_entity_id(entity_id)
        if not object_id:
            result.failed[item] = f"malformed entity_id {item!r}"
            continue
        if domain and target_domain != domain:
            result.failed[entity_id] = (
                f"entity domain {target_domain!r} does not match service domain {domain!r}"
            )
            continue
        if not _entity_exists(jarvis, entity_id):
            result.failed[entity_id] = "unknown entity"
            continue
        add(entity_id, explicit=True)

    for item in _as_list(data.get("area_id")) + _as_list(data.get("area")):
        area_id = _resolve_area_id(jarvis, item)
        if area_id is None:
            result.failed[f"area_id:{item}"] = f"unknown area {item!r}"
            continue
        for entry in jarvis.entities.entities_in_area(area_id, jarvis.devices):
            if entry.disabled:
                continue
            if domain and split_entity_id(entry.entity_id)[0] != domain:
                continue
            add(entry.entity_id)

    for item in _as_list(data.get("device_id")) + _as_list(data.get("device")):
        matched = False
        for entry in jarvis.entities.entities.values():
            if entry.device_id != item:
                continue
            matched = True
            if entry.disabled:
                continue
            if domain and split_entity_id(entry.entity_id)[0] != domain:
                continue
            add(entry.entity_id)
        if not matched and item not in jarvis.devices.devices:
            result.failed[f"device_id:{item}"] = f"unknown device {item!r}"

    # A call with no targeting keys at all is a caller mistake, not a no-op:
    # say so instead of returning an empty, indistinguishable success.
    if not result.entity_ids and not result.failed and not has_target_keys(data):
        result.failed["target"] = "no entity_id, area_id or device_id given"

    return result


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def _extract_kwargs(spec: ServiceSpec, data: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for param in spec.params:
        raw = None
        for key in (param.key, *param.aliases):
            if data.get(key) is not None:
                raw = data[key]
                break
        if raw is None:
            if param.required:
                raise ValueError(f"{spec.key}: missing required field {param.key!r}")
            continue
        try:
            kwargs[param.name] = param.cast(raw) if param.cast else raw
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{spec.key}: invalid value for {param.key!r}: {exc}") from exc
    return kwargs


def _apply_virtual(
    jarvis: "Jarvis", entity_id: str, spec: ServiceSpec, kwargs: dict[str, Any], context: Any
) -> str | None:
    """No live object — set the state directly when that makes sense."""
    if spec.virtual is None:
        return f"{entity_id} has no live entity object"
    current = jarvis.states.get(entity_id)
    new_state, extra = spec.virtual(current, kwargs)
    if new_state is None:
        new_state = current.state if current is not None else STATE_UNKNOWN
    attributes = dict(current.attributes) if current is not None else {}
    attributes.update({k: v for k, v in extra.items() if v is not None})
    # NOT force_update: re-sending the state a virtual entity already has must
    # not fire state_changed, or every no-op call spams bare state triggers,
    # the logbook and the recorder.
    jarvis.states.set(entity_id, new_state, attributes, context=context)
    return None


async def _apply_to_entity(
    jarvis: "Jarvis",
    spec: ServiceSpec,
    entity_id: str,
    kwargs: dict[str, Any],
    context: Any,
    toggle_fallback: bool = False,
) -> str | None:
    """Run one service on one entity. Returns None on success, else a reason."""
    entity = jarvis.entity_object(entity_id)
    if entity is None:
        return _apply_virtual(jarvis, entity_id, spec, kwargs, context)

    method = getattr(entity, spec.method, None)
    if not callable(method) and toggle_fallback:
        method = getattr(entity, "async_toggle", None)
        kwargs = {}
    if not callable(method):
        _LOGGER.warning(
            "%s does not support %s (no %s method on %s)",
            entity_id, spec.key, spec.method, type(entity).__name__,
        )
        return f"{entity_id} does not support {spec.key}"

    result = method(**kwargs)
    if inspect.isawaitable(result):
        await result
    write = getattr(entity, "async_write_state", None)
    if callable(write):
        written = write()
        # The base Entity.async_write_state is sync, but an integration may
        # have overridden it with a coroutine; dropping it on the floor would
        # silently lose the state write.
        if inspect.isawaitable(written):
            await written
    return None


async def _run(
    jarvis: "Jarvis",
    spec: ServiceSpec,
    entity_ids: list[str],
    kwargs: dict[str, Any],
    context: Any,
    toggle_fallback: bool = False,
) -> tuple[list[str], dict[str, str]]:
    changed: list[str] = []
    failed: dict[str, str] = {}
    for entity_id in entity_ids:
        try:
            reason = await _apply_to_entity(
                jarvis, spec, entity_id, dict(kwargs), context, toggle_fallback
            )
        except Exception as exc:  # one bad entity must not sink the batch
            _LOGGER.exception("Error running %s on %s", spec.key, entity_id)
            reason = f"{type(exc).__name__}: {exc}"
        if reason is None:
            changed.append(entity_id)
        else:
            failed[entity_id] = reason
    return changed, failed


def _service_fields(spec: ServiceSpec) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "entity_id": {"description": "Entity id, list of ids, or 'all'.", "required": False},
        "area_id": {"description": "Area id or area name.", "required": False},
        "device_id": {"description": "Device id.", "required": False},
    }
    for param in spec.params:
        fields[param.key] = {"description": param.description, "required": param.required}
    return fields


def make_handler(jarvis: "Jarvis", spec: ServiceSpec) -> Callable[[ServiceCall], Any]:
    async def handler(call: ServiceCall) -> dict[str, Any]:
        data = spec.prepare(call.data) if spec.prepare else call.data
        kwargs = _extract_kwargs(spec, data)
        targets = resolve_targets(jarvis, data, spec.domain)
        changed, failed = await _run(
            jarvis, spec, targets.entity_ids, kwargs, call.context
        )
        return {"changed": changed, "failed": {**targets.failed, **failed}}

    handler.__name__ = f"handle_{spec.domain}_{spec.service}"
    return handler


def make_toggle_handler(jarvis: "Jarvis", spec: ToggleSpec) -> Callable[[ServiceCall], Any]:
    async def handler(call: ServiceCall) -> dict[str, Any]:
        # `light.toggle` advertises the same fields as `light.turn_on`
        # (brightness, colour, transition), so it has to forward them.
        # Both sides are extracted up front: an invalid value must raise
        # before any entity is touched, exactly like the plain services.
        kwargs_for: dict[str, dict[str, Any]] = {}
        for branch in (spec.on, spec.off):
            data = branch.prepare(call.data) if branch.prepare else call.data
            kwargs_for[branch.key] = _extract_kwargs(branch, data)

        targets = resolve_targets(jarvis, call.data, spec.domain)
        changed: list[str] = []
        failed: dict[str, str] = dict(targets.failed)
        for entity_id in targets.entity_ids:
            state = jarvis.states.get(entity_id)
            currently_on = state is not None and state.state in spec.on_states
            chosen = spec.off if currently_on else spec.on
            part_changed, part_failed = await _run(
                jarvis,
                chosen,
                [entity_id],
                kwargs_for[chosen.key],
                call.context,
                toggle_fallback=True,
            )
            changed.extend(part_changed)
            failed.update(part_failed)
        return {"changed": changed, "failed": failed}

    handler.__name__ = f"handle_{spec.domain}_toggle"
    return handler


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    specs, toggles = build_specs()
    registry: dict[str, ServiceSpec] = {}

    for spec in specs:
        registry[spec.key] = spec
        jarvis.services.register(
            spec.domain,
            spec.service,
            make_handler(jarvis, spec),
            description=spec.description,
            fields=_service_fields(spec),
            supports_response=True,
        )

    for toggle in toggles:
        jarvis.services.register(
            toggle.domain,
            "toggle",
            make_toggle_handler(jarvis, toggle),
            description=toggle.description,
            fields=_service_fields(toggle.on),
            supports_response=True,
        )

    jarvis.data[DOMAIN] = {"specs": registry, "toggles": {t.domain: t for t in toggles}}
    _LOGGER.info(
        "Domain service layer ready: %d services across %d domains",
        len(specs) + len(toggles),
        len({s.domain for s in specs}),
    )
    return True
