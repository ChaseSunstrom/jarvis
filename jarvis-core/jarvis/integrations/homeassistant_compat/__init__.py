"""Domain-agnostic services so HA-flavoured YAML keeps working.

Jarvis replaces Home Assistant, but an enormous amount of automation YAML
in the wild calls `homeassistant.turn_on` / `turn_off` / `toggle` with a
mixed bag of targets. This integration accepts those calls and fans them
out to the real per-domain services registered by `domains`, so pasted
config keeps doing what its author meant.

Also here: `homeassistant.update_entity` (force a poll) and a minimal
`persistent_notification.create` / `.dismiss` pair.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from ...services import ServiceCall
from ...state import split_entity_id
from ..domains import TARGET_KEYS, resolve_targets

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "homeassistant_compat"
DEPENDENCIES = ["domains"]

HA_DOMAIN = "homeassistant"
NOTIFY_DOMAIN = "persistent_notification"
NOTIFICATIONS_KEY = "persistent_notifications"
EVENT_PERSISTENT_NOTIFICATION = "persistent_notification"

ACTION_TURN_ON = "turn_on"
ACTION_TURN_OFF = "turn_off"
ACTION_TOGGLE = "toggle"

# Domains whose "on"/"off" verbs aren't literally turn_on/turn_off.
# `lock` is deliberately absent: unlocking a door is never an implicit
# side effect of "turn on the house".
SERVICE_MAP: dict[str, dict[str, str]] = {
    "cover": {ACTION_TURN_ON: "open_cover", ACTION_TURN_OFF: "close_cover"},
    "vacuum": {ACTION_TURN_ON: "start", ACTION_TURN_OFF: "return_to_base"},
}

# What counts as "currently on" per domain when toggling by state.
ON_STATES: dict[str, frozenset[str]] = {
    "cover": frozenset({"open", "opening"}),
    "media_player": frozenset({"playing", "paused", "idle", "on", "buffering"}),
    "vacuum": frozenset({"cleaning", "returning", "on"}),
}
DEFAULT_ON_STATES = frozenset({"on"})


def _service_for(domain: str, action: str) -> str:
    return SERVICE_MAP.get(domain, {}).get(action, action)


def _passthrough(data: dict[str, Any]) -> dict[str, Any]:
    """Everything except targeting keys is forwarded (brightness, etc.)."""
    return {k: v for k, v in data.items() if k not in TARGET_KEYS}


def _merge(into: dict[str, Any], result: Any) -> None:
    if isinstance(result, dict):
        into["changed"].extend(result.get("changed", []))
        into["failed"].update(result.get("failed", {}))


async def _call_domain(
    jarvis: "Jarvis",
    domain: str,
    service: str,
    entity_ids: list[str],
    extra: dict[str, Any],
    call: ServiceCall,
    out: dict[str, Any],
    explicit: set[str],
) -> None:
    if not entity_ids:
        return
    if not jarvis.services.has_service(domain, service):
        _LOGGER.warning("No %s.%s service for compat dispatch", domain, service)
        for entity_id in entity_ids:
            if entity_id in explicit:
                out["failed"][entity_id] = f"domain {domain!r} does not support {service}"
        return
    result = await jarvis.services.async_call(
        domain,
        service,
        {**extra, "entity_id": entity_ids},
        blocking=True,
        context=call.context,
        return_response=True,
    )
    _merge(out, result)


async def _dispatch(jarvis: "Jarvis", call: ServiceCall, action: str) -> dict[str, Any]:
    targets = resolve_targets(jarvis, call.data, None)
    out: dict[str, Any] = {"changed": [], "failed": dict(targets.failed)}
    extra = _passthrough(call.data)

    by_domain: dict[str, list[str]] = {}
    for entity_id in targets.entity_ids:
        by_domain.setdefault(split_entity_id(entity_id)[0], []).append(entity_id)

    for domain, entity_ids in by_domain.items():
        if action != ACTION_TOGGLE:
            await _call_domain(
                jarvis, domain, _service_for(domain, action), entity_ids, extra,
                call, out, targets.explicit,
            )
            continue

        # Prefer a real domain-level toggle; otherwise decide per entity.
        if jarvis.services.has_service(domain, ACTION_TOGGLE):
            await _call_domain(
                jarvis, domain, ACTION_TOGGLE, entity_ids, extra, call, out, targets.explicit
            )
            continue

        on_states = ON_STATES.get(domain, DEFAULT_ON_STATES)
        to_off, to_on = [], []
        for entity_id in entity_ids:
            state = jarvis.states.get(entity_id)
            (to_off if state is not None and state.state in on_states else to_on).append(entity_id)
        await _call_domain(
            jarvis, domain, _service_for(domain, ACTION_TURN_OFF), to_off, extra,
            call, out, targets.explicit,
        )
        await _call_domain(
            jarvis, domain, _service_for(domain, ACTION_TURN_ON), to_on, extra,
            call, out, targets.explicit,
        )

    return out


async def _update_entity(jarvis: "Jarvis", call: ServiceCall) -> dict[str, Any]:
    targets = resolve_targets(jarvis, call.data, None)
    out: dict[str, Any] = {"changed": [], "failed": dict(targets.failed)}
    for entity_id in targets.entity_ids:
        entity = jarvis.entity_object(entity_id)
        if entity is None:
            out["failed"][entity_id] = f"{entity_id} has no live entity object"
            continue
        try:
            await entity.async_update_state()
        except Exception as exc:
            _LOGGER.exception("Error updating %s", entity_id)
            out["failed"][entity_id] = f"{type(exc).__name__}: {exc}"
            continue
        out["changed"].append(entity_id)
    return out


_TARGET_FIELDS = {
    "entity_id": {"description": "Entity id, list of ids, or 'all'.", "required": False},
    "area_id": {"description": "Area id or area name.", "required": False},
    "device_id": {"description": "Device id.", "required": False},
}


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    jarvis.data.setdefault(NOTIFICATIONS_KEY, {})

    for action, description in (
        (ACTION_TURN_ON, "Turn on any entity, whatever its domain."),
        (ACTION_TURN_OFF, "Turn off any entity, whatever its domain."),
        (ACTION_TOGGLE, "Toggle any entity, whatever its domain."),
    ):

        def _make(action: str = action):
            async def handler(call: ServiceCall) -> dict[str, Any]:
                return await _dispatch(jarvis, call, action)

            handler.__name__ = f"handle_homeassistant_{action}"
            return handler

        jarvis.services.register(
            HA_DOMAIN, action, _make(), description=description,
            fields=dict(_TARGET_FIELDS), supports_response=True,
        )

    async def handle_update_entity(call: ServiceCall) -> dict[str, Any]:
        return await _update_entity(jarvis, call)

    jarvis.services.register(
        HA_DOMAIN,
        "update_entity",
        handle_update_entity,
        description="Force an immediate refresh of one or more entities.",
        fields=dict(_TARGET_FIELDS),
        supports_response=True,
    )

    async def handle_notification_create(call: ServiceCall) -> dict[str, Any]:
        store: dict[str, Any] = jarvis.data.setdefault(NOTIFICATIONS_KEY, {})
        notification_id = str(call.get("notification_id") or uuid.uuid4().hex[:12])
        notification = {
            "notification_id": notification_id,
            "title": call.get("title"),
            "message": str(call.get("message") or ""),
            "created_at": time.time(),
        }
        store[notification_id] = notification
        jarvis.bus.fire(
            EVENT_PERSISTENT_NOTIFICATION,
            {"action": "create", **notification},
            call.context,
        )
        return dict(notification)

    jarvis.services.register(
        NOTIFY_DOMAIN,
        "create",
        handle_notification_create,
        description="Store a notification for the UI / voice assistant to read out.",
        fields={
            "message": {"description": "Notification body.", "required": True},
            "title": {"description": "Optional title.", "required": False},
            "notification_id": {"description": "Stable id (replaces an existing one).",
                                "required": False},
        },
        supports_response=True,
    )

    async def handle_notification_dismiss(call: ServiceCall) -> dict[str, Any]:
        store: dict[str, Any] = jarvis.data.setdefault(NOTIFICATIONS_KEY, {})
        notification_id = str(call.get("notification_id") or "")
        removed = store.pop(notification_id, None)
        jarvis.bus.fire(
            EVENT_PERSISTENT_NOTIFICATION,
            {"action": "dismiss", "notification_id": notification_id},
            call.context,
        )
        return {"dismissed": removed is not None, "notification_id": notification_id}

    jarvis.services.register(
        NOTIFY_DOMAIN,
        "dismiss",
        handle_notification_dismiss,
        description="Remove a stored notification.",
        fields={"notification_id": {"description": "Id to dismiss.", "required": True}},
        supports_response=True,
    )

    return True
