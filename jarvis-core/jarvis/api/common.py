"""Everything the REST and websocket APIs both need.

Both surfaces expose the same operations; only the envelope differs. Keeping
the actual work here means ``call_service`` over the socket and
``POST /api/services/...`` can never drift apart.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..bus import Context
from ..const import EVENT_STATE_CHANGED, VERSION
from ..services import ServiceNotFound

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Reported as ``ha_version`` so clients written against Home Assistant's
#: websocket API recognise the handshake without a special case.
HA_VERSION = f"jarvis-{VERSION}"

DATA_TTS_CACHE = "tts_cache"
DATA_WEBHOOKS = "webhooks"
DATA_VOICE = "voice"
DATA_RECORDER = "recorder"

CONVERSATION_DOMAIN = "conversation"
LLM_DOMAIN = "llm"

DEFAULT_PIPELINE = {
    "id": "jarvis",
    "name": "Jarvis",
    "language": "en",
    "stt_engine": "wyoming",
    "stt_language": None,
    "tts_engine": "wyoming",
    "tts_voice": None,
    "tts_language": None,
    "wake_engine": "wyoming",
    "wake_word": "hey_jarvis",
    "conversation_engine": "ollama",
    "conversation_language": None,
}


class ApiError(Exception):
    """A failure with a machine-readable code and an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value: Any) -> Any:
    """Last-resort encoder for objects that wandered into an event payload."""
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def api_context(user_id: str | None = None, origin: str = "api") -> Context:
    return Context(user_id=user_id, origin=origin)


# --- read-only payloads -----------------------------------------------------
def state_payload(state: Any) -> dict[str, Any]:
    return state.as_dict() if hasattr(state, "as_dict") else dict(state)


def states_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    return [state.as_dict() for state in jarvis.states.all()]


def services_payload(jarvis: "Jarvis") -> dict[str, Any]:
    return jarvis.services.as_dict()


def events_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    listeners: dict[str, list[Any]] = getattr(jarvis.bus, "_listeners", {}) or {}
    return [
        {"event": event_type, "listener_count": len(callbacks)}
        for event_type, callbacks in sorted(listeners.items())
        if callbacks
    ]


def config_payload(jarvis: "Jarvis") -> dict[str, Any]:
    options = (jarvis.config or {}).get("jarvis") or {}
    if not isinstance(options, dict):
        options = {}
    components = sorted(set(jarvis.services.services) | jarvis.states.domains())
    return {
        "location_name": options.get("name") or "Jarvis",
        "latitude": options.get("latitude"),
        "longitude": options.get("longitude"),
        "elevation": options.get("elevation", 0),
        "time_zone": options.get("time_zone") or time.tzname[0],
        "unit_system": options.get("unit_system")
        or {
            "length": "km",
            "mass": "g",
            "temperature": "°C",
            "volume": "L",
            "pressure": "Pa",
        },
        "currency": options.get("currency"),
        "country": options.get("country"),
        "language": options.get("language") or "en",
        "components": components,
        "config_dir": str(jarvis.config_dir),
        "version": VERSION,
        "ha_version": HA_VERSION,
        "state": "RUNNING" if jarvis.is_running else "STARTING",
        "config_source": "yaml",
        "safe_mode": False,
        "areas": [area.as_dict() for area in jarvis.areas.areas.values()],
    }


def entity_registry_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    return [entry.as_dict() for entry in jarvis.entities.entities.values()]


def device_registry_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    return [entry.as_dict() for entry in jarvis.devices.devices.values()]


def area_registry_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    return [entry.as_dict() for entry in jarvis.areas.areas.values()]


def pipeline_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """``{"pipelines": [...], "preferred_pipeline": id}`` — HA's shape."""
    store = getattr(jarvis.data.get(DATA_VOICE), "pipelines", None)
    payload: dict[str, Any] | None = None
    if store is not None and hasattr(store, "as_dict"):
        try:
            candidate = store.as_dict()
        except Exception:  # pragma: no cover - a broken store is not fatal
            _LOGGER.exception("Could not read the voice pipeline store")
        else:
            if isinstance(candidate, dict) and candidate.get("pipelines"):
                payload = candidate
    if payload is None:
        payload = {
            "pipelines": [dict(DEFAULT_PIPELINE)],
            "preferred_pipeline": DEFAULT_PIPELINE["id"],
        }

    pipelines = []
    for pipeline in payload.get("pipelines") or []:
        item = dict(pipeline)
        # Aliases Home-Assistant clients look for.
        item.setdefault("wake_word_id", item.get("wake_word"))
        item.setdefault("wake_word_entity", item.get("wake_engine"))
        pipelines.append(item)
    return {
        "pipelines": pipelines,
        "preferred_pipeline": payload.get("preferred_pipeline"),
    }


# --- service calls ----------------------------------------------------------
TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")


@dataclass(slots=True)
class ServiceResult:
    changed_states: list[dict[str, Any]] = field(default_factory=list)
    response: Any = None
    context: Context = field(default_factory=Context)


def merge_target(
    service_data: dict[str, Any] | None, target: dict[str, Any] | None
) -> dict[str, Any]:
    """Fold a HA-style ``target`` block into flat service data."""
    data = dict(service_data or {})
    for key, value in (target or {}).items():
        if key in TARGET_KEYS and value not in (None, [], {}):
            data[key] = value
    return data


async def async_call_service(
    jarvis: "Jarvis",
    domain: str,
    service: str,
    service_data: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    context: Context | None = None,
    return_response: bool = False,
) -> ServiceResult:
    """Call a service and report which states moved while it ran."""
    data = merge_target(service_data, target)
    ctx = context or api_context()
    changed: list[dict[str, Any]] = []

    def _collect(event: Any) -> None:
        new_state = event.data.get("new_state")
        if new_state is not None:
            changed.append(state_payload(new_state))

    unsub = jarvis.bus.listen(EVENT_STATE_CHANGED, _collect)
    try:
        response = await jarvis.services.async_call(
            domain,
            service,
            data,
            blocking=True,
            context=ctx,
            return_response=return_response,
        )
    except ServiceNotFound as err:
        raise ApiError("service_not_found", str(err), 400) from err
    except ApiError:
        raise
    except Exception as err:
        raise ApiError(
            "service_failed", f"{domain}.{service} failed: {err}", 500
        ) from err
    finally:
        unsub()

    return ServiceResult(changed, response, ctx)


async def async_conversation_process(
    jarvis: "Jarvis",
    text: str,
    conversation_id: str | None = None,
    language: str | None = None,
    agent_id: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """Ask the conversation agent, returning HA's ``{response, conversation_id}``."""
    if not str(text or "").strip():
        raise ApiError("invalid_format", "conversation/process needs 'text'", 400)
    if not jarvis.services.has_service(CONVERSATION_DOMAIN, "process"):
        raise ApiError(
            "agent_not_found",
            "no conversation agent is configured (is the llm integration set up?)",
            501,
        )
    result = await jarvis.services.async_call(
        CONVERSATION_DOMAIN,
        "process",
        {
            "text": text,
            "conversation_id": conversation_id,
            "language": language,
            "agent_id": agent_id,
        },
        blocking=True,
        context=context or api_context(),
        return_response=True,
    )
    if isinstance(result, dict) and "response" in result:
        return result
    speech = result if isinstance(result, str) else str(result or "")
    return {
        "response": {
            "speech": {"plain": {"speech": speech, "extra_data": None}},
            "response_type": "action_done",
            "language": language or "en",
            "data": {},
        },
        "conversation_id": conversation_id,
    }


#: Values that mean "yes, run the held action". Anything else is a refusal.
APPROVAL_TRUTHY = frozenset({"true", "yes", "y", "1", "on", "approve", "approved", "ok"})


def approval_flag(value: Any) -> bool:
    """Interpret an ``approved`` flag from the wire, failing *closed*.

    ``bool("false")`` is ``True``. A plain cast therefore turns an explicit
    refusal — which is exactly how a phone's "Deny" button arrives once it has
    been through a form post, a query string or a JSON string field — into
    execution of the very Tier-3 action the safety gate was holding. Only an
    explicit affirmative counts here; anything unrecognised denies.

    Omitting the field entirely still means approve: ``POST /api/jarvis/approve``
    with just a ``request_id`` is how a client says yes.

    This mirrors ``jarvis.integrations.llm.parse_approved`` deliberately rather
    than importing it, so the API layer is safe on its own terms and does not
    drag the whole LLM stack into an import that must work without it.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in APPROVAL_TRUTHY
    return False


async def async_approve(
    jarvis: "Jarvis",
    request_id: str,
    approved: Any = True,
    context: Context | None = None,
) -> dict[str, Any]:
    """Resolve a Tier-3 request the LLM safety gate is holding.

    ``approved`` is taken raw from the client and normalised by
    :func:`approval_flag`; never pre-cast it with ``bool()``.
    """
    if not request_id:
        raise ApiError("invalid_format", "jarvis/approve needs 'request_id'", 400)
    if not jarvis.services.has_service(LLM_DOMAIN, "approve"):
        raise ApiError(
            "not_supported", "the llm integration is not set up, nothing to approve", 501
        )
    result = await jarvis.services.async_call(
        LLM_DOMAIN,
        "approve",
        {"request_id": request_id, "approved": approval_flag(approved)},
        blocking=True,
        context=context or api_context(),
        return_response=True,
    )
    return result if isinstance(result, dict) else {"result": result}


# --- registries -------------------------------------------------------------
ENTITY_UPDATE_FIELDS = (
    "name",
    "icon",
    "area_id",
    "device_id",
    "aliases",
    "disabled",
    "hidden",
    "exposed",
)
DEVICE_UPDATE_FIELDS = ("name", "area_id", "disabled", "manufacturer", "model")
AREA_UPDATE_FIELDS = ("name", "aliases")


def _changes(payload: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in allowed if key in payload}


async def async_update_entity(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(payload.get("entity_id") or "")
    if not entity_id:
        raise ApiError("invalid_format", "entity_id is required", 400)
    if payload.get("new_entity_id") and payload["new_entity_id"] != entity_id:
        raise ApiError(
            "not_supported",
            "renaming an entity_id is not supported yet; set 'name' instead",
            400,
        )
    entry = await jarvis.entities.update(entity_id, **_changes(payload, ENTITY_UPDATE_FIELDS))
    if entry is None:
        raise ApiError("not_found", f"unknown entity {entity_id}", 404)
    return {"entity_entry": entry.as_dict()}


async def async_update_device(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "")
    if not device_id:
        raise ApiError("invalid_format", "device_id is required", 400)
    entry = await jarvis.devices.update(device_id, **_changes(payload, DEVICE_UPDATE_FIELDS))
    if entry is None:
        raise ApiError("not_found", f"unknown device {device_id}", 404)
    return entry.as_dict()


async def async_create_area(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ApiError("invalid_format", "name is required", 400)
    area = await jarvis.areas.create(name, payload.get("aliases"))
    return area.as_dict()


async def async_update_area(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    area_id = str(payload.get("area_id") or "")
    if not area_id:
        raise ApiError("invalid_format", "area_id is required", 400)
    area = await jarvis.areas.update(area_id, **_changes(payload, AREA_UPDATE_FIELDS))
    if area is None:
        raise ApiError("not_found", f"unknown area {area_id}", 404)
    return area.as_dict()


async def async_delete_area(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    area_id = str(payload.get("area_id") or "")
    if not area_id:
        raise ApiError("invalid_format", "area_id is required", 400)
    if not await jarvis.areas.delete(area_id):
        raise ApiError("not_found", f"unknown area {area_id}", 404)
    return {"area_id": area_id, "deleted": True}


# --- settings ---------------------------------------------------------------
def settings_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """Every editable setting, with where its current value came from.

    The `choices` come from each spec's `choices_hook`, which asks the running
    system — the installed Ollama models, the loaded voices. A hook that throws
    (the backend is down, the integration is not configured) contributes no
    choices rather than failing the whole page: a settings screen that will not
    load because Ollama is unreachable is a settings screen you cannot use to
    fix the Ollama address.
    """
    from ..settings import SETTINGS_BY_KEY

    rows = jarvis.settings.describe(jarvis.raw_config, jarvis.package_provenance)
    for row in rows:
        spec = SETTINGS_BY_KEY.get(row["key"])
        hook = getattr(spec, "choices_hook", None)
        if hook is None:
            continue
        try:
            choices = hook(jarvis)
        except Exception:  # pragma: no cover - defensive, see the docstring
            _LOGGER.debug("choices for %s could not be listed", row["key"], exc_info=True)
            continue
        if choices:
            row["choices"] = list(choices)
    return {
        "settings": rows,
        "unapplied": [entry.as_dict() for entry in jarvis.settings.unapplied],
    }


def _apply_now(jarvis: "Jarvis", key: str, value: Any) -> bool:
    """Push a stored setting into whatever is already running.

    Storing a setting only changes what the *next* boot builds from. Every
    spec marked `live` carries an `apply_hook` for the running copy, and until
    this called them the console could report a model that nothing was using.
    Returns whether the value actually landed somewhere live.
    """
    from ..settings import SETTINGS_BY_KEY

    spec = SETTINGS_BY_KEY.get(key)
    if spec is None or spec.apply_hook is None:
        return False
    try:
        return bool(spec.apply_hook(jarvis, value))
    except Exception:
        _LOGGER.exception("Could not apply %s live", key)
        return False


def _setting_result(jarvis: "Jarvis", key: str, value: Any, applied: bool) -> dict[str, Any]:
    from ..settings import APPLY_LIVE, SETTINGS_BY_KEY

    spec = SETTINGS_BY_KEY[key]
    return {
        "key": key,
        "value": value,
        "applied": applied,
        "apply": spec.apply,
        # The honest answer to "do I need to restart", which is the question
        # anyone changing a setting is actually asking. `live` still counts as
        # needing one if the hook did not find its target.
        "restart_required": spec.apply != APPLY_LIVE or not applied,
        "settings": settings_payload(jarvis)["settings"],
    }


async def async_set_setting(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    from ..settings import SETTINGS_BY_KEY, SettingsError

    key = str(payload.get("key") or "").strip()
    if not key:
        raise ApiError("invalid_format", "key is required", 400)
    if "value" not in payload:
        raise ApiError("invalid_format", "value is required", 400)
    # Resolved before the write so an unknown key is a 404 rather than sharing
    # the 400 that a *bad value for a real key* gets. The console shows them
    # differently: one is a typo in the request, the other is a field to fix.
    if key not in SETTINGS_BY_KEY:
        raise ApiError("not_found", f"{key} is not an editable setting", 404)
    try:
        value = await jarvis.settings.async_set(key, payload["value"])
    except SettingsError as err:
        raise ApiError("invalid_format", str(err), 400) from err

    # Re-merge so `jarvis.config` — which everything reads — matches the store
    # immediately, rather than only after the next restart.
    await jarvis.async_install_config(jarvis.raw_config, jarvis.package_provenance)
    return _setting_result(jarvis, key, value, _apply_now(jarvis, key, value))


async def async_reset_setting(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    """Drop an override so the file's value shows through again."""
    from ..settings import SETTINGS_BY_KEY

    key = str(payload.get("key") or "").strip()
    if not key:
        raise ApiError("invalid_format", "key is required", 400)
    spec = SETTINGS_BY_KEY.get(key)
    if spec is None:
        raise ApiError("not_found", f"{key} is not an editable setting", 404)

    await jarvis.settings.async_reset(key)
    merged = await jarvis.async_install_config(jarvis.raw_config, jarvis.package_provenance)
    # Whatever the file (or a default) says now, pushed live the same way a set
    # would be — otherwise a reset appears to work and changes nothing.
    reverted = _dig_config(merged, spec.path)
    applied = _apply_now(jarvis, key, reverted) if reverted is not None else False
    return _setting_result(jarvis, key, reverted, applied)


def _dig_config(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = config
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# --- automations ------------------------------------------------------------
def automation_list_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    """Every automation the engine is running, editable or not.

    The console shows YAML automations alongside the ones it created, marked
    `editable: false`. Hiding them would be worse than useless: the user would
    see an empty list on a box that is visibly running automations, and the
    obvious conclusion — "it lost them" — would be wrong.
    """
    from ..automation.authored import get_authored

    manager = jarvis.data.get("automation")
    if manager is None:  # `automation:` not set up — no automations, not an error
        return []
    authored = get_authored(jarvis)
    rows: list[dict[str, Any]] = []
    for automation in manager.all():
        entry = authored.items.get(automation.automation_id)
        rows.append(
            {
                "id": automation.automation_id,
                "entity_id": automation.entity_id,
                "alias": automation.alias,
                "description": automation.description,
                "mode": automation.mode,
                "enabled": automation.enabled,
                # The engine's own copy, so what is shown is what is running
                # rather than what the store believes it saved.
                "trigger": automation.config.get("trigger") or [],
                "condition": automation.config.get("condition") or [],
                "action": automation.config.get("action") or [],
                "editable": entry is not None,
                "created_at": (entry or {}).get("created_at"),
                "updated_at": (entry or {}).get("updated_at"),
            }
        )
    rows.sort(key=lambda row: (not row["editable"], row["alias"].lower()))
    return rows


def _automation_config(payload: dict[str, Any]) -> dict[str, Any]:
    """The automation out of an API payload, without the transport's own keys.

    The websocket puts `id` and `type` on every message, and REST payloads that
    address an existing automation carry `automation_id`. None of those are
    fields of the automation, and `validate` refuses unknown fields — so
    passing the message through verbatim would reject every well-formed
    request.
    """
    config = payload.get("automation")
    if isinstance(config, dict):
        return dict(config)
    return {
        key: value
        for key, value in payload.items()
        if key not in ("id", "type", "automation_id")
    }


async def _async_reload_automations(jarvis: "Jarvis", context: Context | None = None) -> None:
    """Make the change live.

    Through the `automation.reload` service rather than by reaching into the
    manager: reload is the one path that already re-reads YAML, re-applies the
    settings overlay and re-loads the store together. A shortcut here would be
    a second, subtly different way to load automations, and the two would
    drift.
    """
    await jarvis.services.async_call(
        "automation", "reload", {}, blocking=True, context=context or api_context()
    )


async def async_create_automation(
    jarvis: "Jarvis", payload: dict[str, Any], context: Context | None = None
) -> dict[str, Any]:
    from ..automation.authored import AuthoredError, get_authored

    try:
        entry = await get_authored(jarvis).async_create(_automation_config(payload))
    except AuthoredError as err:
        raise ApiError("invalid_format", str(err), 400) from err
    await _async_reload_automations(jarvis, context)
    return {"automation": entry}


async def async_update_automation(
    jarvis: "Jarvis", payload: dict[str, Any], context: Context | None = None
) -> dict[str, Any]:
    from ..automation.authored import AuthoredError, get_authored

    automation_id = str(payload.get("automation_id") or "").strip()
    if not automation_id:
        raise ApiError("invalid_format", "automation_id is required", 400)
    try:
        entry = await get_authored(jarvis).async_update(
            automation_id, _automation_config(payload)
        )
    except AuthoredError as err:
        raise ApiError("invalid_format", str(err), 400) from err
    await _async_reload_automations(jarvis, context)
    return {"automation": entry}


async def async_delete_automation(
    jarvis: "Jarvis", payload: dict[str, Any], context: Context | None = None
) -> dict[str, Any]:
    from ..automation.authored import AuthoredError, get_authored

    automation_id = str(payload.get("automation_id") or "").strip()
    if not automation_id:
        raise ApiError("invalid_format", "automation_id is required", 400)
    try:
        deleted = await get_authored(jarvis).async_delete(automation_id)
    except AuthoredError as err:
        # Refusing to delete a YAML automation is the caller asking for
        # something this API will never do, not a malformed request.
        raise ApiError("not_supported", str(err), 400) from err
    if not deleted:
        raise ApiError("not_found", f"unknown automation {automation_id}", 404)
    await _async_reload_automations(jarvis, context)
    return {"automation_id": automation_id, "deleted": True}


# --- history / tts / webhooks ----------------------------------------------
async def async_history(
    jarvis: "Jarvis",
    entity_ids: list[str] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[Any]:
    """Hand off to a recorder if one is installed; otherwise nothing to show."""
    recorder = jarvis.data.get(DATA_RECORDER)
    if recorder is None:
        return []

    candidates: tuple[tuple[tuple[Any, ...], dict[str, Any]], ...] = (
        ((), {"entity_ids": entity_ids, "start_time": start_time, "end_time": end_time}),
        ((entity_ids, start_time, end_time), {}),
        ((entity_ids,), {}),
        ((), {}),
    )
    for name in ("async_history_period", "async_history", "async_get_history", "history"):
        method = getattr(recorder, name, None)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):  # builtins and C callables
            signature = None
        for args, kwargs in candidates:
            if signature is not None:
                # Match the signature *before* calling, so a TypeError raised
                # inside the recorder is never mistaken for a bad call shape.
                try:
                    signature.bind(*args, **kwargs)
                except TypeError:
                    continue
            result = method(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return list(result or [])
    _LOGGER.debug("A recorder is registered but exposes no usable history method")
    return []


def tts_audio(jarvis: "Jarvis", token: str) -> tuple[bytes, str] | None:
    """Cached synthesised speech for ``/api/tts_proxy/<token>.wav``."""
    if token.endswith(".wav"):
        token = token[: -len(".wav")]
    cache = jarvis.data.get(DATA_TTS_CACHE) or {}
    entry = cache.get(token)
    if entry is None:
        return None
    if isinstance(entry, (bytes, bytearray)):
        return bytes(entry), "audio/wav"
    audio, mime = entry
    return bytes(audio), str(mime or "audio/wav")


async def async_dispatch_webhook(
    jarvis: "Jarvis",
    webhook_id: str,
    data: Any = None,
    query: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    method: str = "POST",
) -> int:
    """Fan a webhook out to its registered handlers; returns how many ran."""
    handler = (jarvis.data.get(DATA_WEBHOOKS) or {}).get(webhook_id)
    if handler is None:
        raise ApiError("not_found", f"no webhook registered as {webhook_id!r}", 404)
    result = handler(data, query=query or {}, headers=headers or {}, method=method)
    if inspect.isawaitable(result):
        result = await result
    return int(result or 0)
