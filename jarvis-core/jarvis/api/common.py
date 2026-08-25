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
    answer: Any = None,
) -> dict[str, Any]:
    """Resolve a Tier-3 request the LLM safety gate is holding.

    ``approved`` is taken raw from the client and normalised by
    :func:`approval_flag`; never pre-cast it with ``bool()``.

    ``answer`` is what a human typed or picked, for the one kind of held
    request that is a question rather than an action. It is passed straight
    through: the tool registry decides whether the held tool accepts one at
    all, and if so which single argument it may write.
    """
    if not request_id:
        raise ApiError("invalid_format", "jarvis/approve needs 'request_id'", 400)

    # A coding job's held edit or command first. Two gates share this command —
    # the model's (which ends a turn) and Jarvis Code's (which blocks a job
    # until somebody looks at a diff) — and a request id says nothing about
    # which one is holding it, so the one that recognises the id answers it.
    from ..integrations.code import resolve_approval as _resolve_code

    decided = approval_flag(approved)
    if _resolve_code(jarvis, request_id, decided):
        return {
            "status": "executed" if decided else "denied",
            "request_id": request_id,
            "tool": "code",
        }

    if not jarvis.services.has_service(LLM_DOMAIN, "approve"):
        raise ApiError(
            "not_supported", "the llm integration is not set up, nothing to approve", 501
        )
    result = await jarvis.services.async_call(
        LLM_DOMAIN,
        "approve",
        {
            "request_id": request_id,
            "approved": decided,
            "answer": answer,
        },
        blocking=True,
        context=context or api_context(),
        return_response=True,
    )
    return result if isinstance(result, dict) else {"result": result}


def token_list_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    """Every stored token, and whether anything is holding it open right now.

    Built from `auth.list_tokens()` rather than from any pairing record, and
    that is the load-bearing part. A token store that failed to load, or a
    partial restore, would leave paired full-privilege tokens live and
    invisible — and a list built from pairing records would render that as "no
    devices" rather than as something to revoke. Everything the auth manager
    knows about appears here, named or not.
    """
    from ..auth import get_auth
    from .websocket import DATA_WS_SESSIONS

    auth = get_auth(jarvis)
    if auth is None:
        return []
    live = jarvis.data.get(DATA_WS_SESSIONS)
    holders = live if isinstance(live, dict) else {}
    return [
        {**info.as_dict(), "connected": bool(holders.get(info.id))}
        for info in auth.list_tokens()
    ]


async def async_revoke_token(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    """Revoke a token and hang up whatever is holding it open."""
    from ..auth import get_auth
    from .websocket import close_sockets_for_token

    token_id = str(payload.get("token_id") or payload.get("id") or "")
    if not token_id:
        raise ApiError("invalid_format", "a token_id is required", 400)
    auth = get_auth(jarvis)
    if auth is None or not await auth.revoke(token_id):
        raise ApiError("not_found", f"unknown token {token_id}", 404)
    return {
        "id": token_id,
        "revoked": True,
        "sockets_closed": close_sockets_for_token(jarvis, token_id),
    }


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


def _rewrite_entity_id(value: Any, old: str, new: str) -> tuple[Any, int]:
    """`old` becomes `new` anywhere inside a nested config. Returns the count.

    Whole strings only, never substrings: `light.kitchen` must not rewrite the
    `light.kitchen_counter` sitting next to it in the same list.
    """
    if isinstance(value, str):
        return (new, 1) if value == old else (value, 0)
    if isinstance(value, list):
        changed = 0
        out = []
        for item in value:
            rewritten, count = _rewrite_entity_id(item, old, new)
            out.append(rewritten)
            changed += count
        return out, changed
    if isinstance(value, dict):
        changed = 0
        out = {}
        for key, item in value.items():
            rewritten, count = _rewrite_entity_id(item, old, new)
            out[key] = rewritten
            changed += count
        return out, changed
    return value, 0


async def _follow_rename_into_automations(
    jarvis: "Jarvis", old: str, new: str
) -> list[str]:
    """Point authored automations at the entity's new id. Returns their names.

    Renaming an entity and leaving the automations that drive it pointing at
    an id nothing answers to is a rename that silently breaks the house. These
    are configs Jarvis owns and stores, so it can follow them; anything in
    `configuration.yaml` is the operator's file and is reported instead.
    """
    from ..automation.authored import get_authored

    try:
        store = get_authored(jarvis)
    except Exception:  # noqa: BLE001 - automations are optional
        return []
    touched: list[str] = []
    for entry in list(store.entries()):
        entry_id = str(entry.get("id") or "")
        config = {k: v for k, v in entry.items() if k not in ("created_at", "updated_at")}
        rewritten, count = _rewrite_entity_id(config, old, new)
        if not count:
            continue
        try:
            await store.async_update(entry_id, rewritten)
        except Exception as err:  # noqa: BLE001 - one bad config must not stop the rest
            _LOGGER.warning(
                "could not follow the rename of %s into automation %s: %s",
                old,
                entry_id,
                err,
            )
            continue
        touched.append(str(entry.get("alias") or entry_id))
    # Reload so the running engine picks up the rewritten configs; without it
    # the file is right and the behaviour is still wrong until a restart. Only
    # if the integration is loaded at all — automations are optional.
    if touched and jarvis.services.has_service("automation", "reload"):
        await jarvis.services.async_call("automation", "reload", {}, blocking=True)
    return touched


async def async_update_entity(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(payload.get("entity_id") or "")
    if not entity_id:
        raise ApiError("invalid_format", "entity_id is required", 400)
    wanted = str(payload.get("new_entity_id") or "").strip().lower()

    entry = await jarvis.entities.update(entity_id, **_changes(payload, ENTITY_UPDATE_FIELDS))
    if entry is None:
        raise ApiError("not_found", f"unknown entity {entity_id}", 404)

    result: dict[str, Any] = {}
    if wanted and wanted != entity_id:
        # After the other fields, so a request that both renames and renames-
        # the-label does not half-apply when the id turns out to be taken.
        state = jarvis.states.get(entity_id)
        try:
            entry = await jarvis.entities.rename(entity_id, wanted)
        except ValueError as err:
            raise ApiError("invalid_format", str(err), 400) from err
        if entry is None:  # pragma: no cover - update() already found it
            raise ApiError("not_found", f"unknown entity {entity_id}", 404)
        if state is not None:
            # Carried, not recreated: an entity that came back as `unknown`
            # after a rename would look broken to everything watching it.
            jarvis.states.set(wanted, state.state, state.attributes)
            jarvis.states.remove(entity_id)
        result["renamed_from"] = entity_id
        result["automations_updated"] = await _follow_rename_into_automations(
            jarvis, entity_id, wanted
        )
    return {"entity_entry": entry.as_dict(), **result}


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


# --- companion devices --------------------------------------------------------
def companion_list_payload(jarvis: "Jarvis", include_actions: bool = True) -> list[dict[str, Any]]:
    """The phones, desktops and satellites currently registered on the socket.

    Distinct from `device_registry_payload`, which is the registry of *things in
    the house* — a Hue bridge, a thermostat. These are the machines running
    Jarvis clients, each advertising the actions it will accept, and until now
    nothing could show them: you could grant your phone forty-odd capabilities
    and have no way to see that it had connected, let alone what it offered.
    """
    from .devices import get_devices

    hub = get_devices(jarvis)
    try:
        return hub.as_dict(include_actions)
    except Exception:  # pragma: no cover - a broken link must not blank the page
        _LOGGER.exception("Could not describe the connected devices")
        return []


# --- conversation history -----------------------------------------------------
#: Where the `llm` integration leaves its conversation archive. Read by name
#: rather than imported, so the API layer still works in a build with no
#: assistant configured — the same way `llm_tools` is reached above.
DATA_HISTORY = "llm_history"


def _conversation_archive(jarvis: "Jarvis") -> Any:
    archive = jarvis.data.get(DATA_HISTORY)
    if archive is None:
        raise ApiError(
            "not_found",
            "the assistant is not configured, so it has kept no conversations",
            404,
        )
    return archive


def conversation_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """Past conversations as summary rows, most recent first.

    Summaries only — no message bodies. The chat console lists a hundred of
    these on load and opens one, and shipping every transcript to draw a
    sidebar would put megabytes on the socket to render a list of titles.
    """
    archive = jarvis.data.get(DATA_HISTORY)
    if archive is None:
        return {"conversations": []}
    return {"conversations": archive.listing()}


def conversation_get_payload(jarvis: "Jarvis", conversation_id: str) -> dict[str, Any]:
    """One conversation in full: every turn, with reasoning and tool rows."""
    conversation = _conversation_archive(jarvis).get(str(conversation_id or ""))
    if conversation is None:
        raise ApiError("not_found", f"no conversation {conversation_id!r}", 404)
    return {"conversation": conversation.as_dict()}


async def async_delete_conversation(
    jarvis: "Jarvis", conversation_id: str
) -> dict[str, Any]:
    """Forget one conversation, in both stores.

    Through the service rather than the archive directly, so the live
    conversation the model may still be holding goes with it — see
    `llm.clear_conversation`. A delete that left the model's copy would mean
    the next message on that id resumed a conversation the user had just
    removed from their history.
    """
    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        raise ApiError("invalid_format", "which conversation?", 400)
    if not jarvis.services.has_service(LLM_DOMAIN, "clear_conversation"):
        raise ApiError("not_found", "the assistant is not configured", 404)
    result = await jarvis.services.async_call(
        LLM_DOMAIN,
        "clear_conversation",
        {"conversation_id": conversation_id},
        blocking=True,
        return_response=True,
    )
    return {"deleted": bool((result or {}).get("cleared"))}


async def async_rename_conversation(
    jarvis: "Jarvis", conversation_id: str, title: str
) -> dict[str, Any]:
    """Give a conversation a name instead of its first sentence."""
    conversation_id = str(conversation_id or "").strip()
    if not conversation_id:
        raise ApiError("invalid_format", "which conversation?", 400)
    if not str(title or "").strip():
        raise ApiError("invalid_format", "a conversation needs a non-empty title", 400)
    if not _conversation_archive(jarvis).rename(conversation_id, str(title)):
        raise ApiError("not_found", f"no conversation {conversation_id!r}", 404)
    return {"renamed": True, "conversation_id": conversation_id, "title": str(title)}


# --- tools --------------------------------------------------------------------
def _tool_registry(jarvis: "Jarvis") -> Any:
    registry = jarvis.data.get("llm_tools")
    if registry is None:
        raise ApiError(
            "not_found",
            "the assistant is not configured, so it has no tools to manage",
            404,
        )
    return registry


def tool_list_payload(jarvis: "Jarvis") -> list[dict[str, Any]]:
    """Every registered tool, marked with whether the console may edit it.

    Built-ins and tools from the user's YAML are listed read-only, for the same
    reason YAML automations are: an empty list on an assistant that visibly has
    tools would be a lie.
    """
    from ..llm.authored_tools import get_authored_tools

    registry = jarvis.data.get("llm_tools")
    if registry is None:
        return []
    authored = get_authored_tools(jarvis)
    rows: list[dict[str, Any]] = []
    for name in registry.names():
        tool = registry.get(name)
        entry = authored.items.get(name)
        rows.append(
            {
                "name": name,
                "description": getattr(tool, "description", ""),
                "tier": getattr(tool, "tier", 1),
                "domain": getattr(tool, "domain", None),
                "parameters": getattr(tool, "parameters", None),
                "editable": entry is not None,
                "service": (entry or {}).get("service"),
                "created_at": (entry or {}).get("created_at"),
                "updated_at": (entry or {}).get("updated_at"),
            }
        )
    rows.sort(key=lambda row: (not row["editable"], row["name"]))
    return rows


def tools_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """What the MODEL sees — not what this console can edit.

    Deliberately a different command from `config/tool/list`, which answers
    "what may be edited here" and marks each row `editable`. The console shows
    the union of the two, because a backend can answer one and not the other,
    and because a tool the model can call but nobody can edit still has to
    appear. This one is the model's toolbox: `jarvis/llm/agent.py` builds its
    schema from `as_openai_schema()` over this same registry with no filtering,
    so "listed here" and "offered to the model" are the same set by
    construction rather than by agreement.

    That equivalence is the point. The reason this command had to exist is that
    "is the tool the model is failing to call actually registered?" was not a
    question any surface could answer.

    `needs_approval` and `may_escalate` are computed HERE, from the registry's
    own rule, rather than re-derived in TypeScript from `tier`. The tier is not
    the whole rule — a tool in a gated domain is held at any tier, and a tool
    with a `gate` is held depending on its arguments — and a console that
    reimplemented that would be a second copy of the security decision.
    """
    from ..const import GATED_DOMAINS
    from ..llm.tools import TIER_APPROVAL

    registry = _tool_registry(jarvis)
    tools: list[dict[str, Any]] = []
    for name in registry.names():
        tool = registry.get(name)
        if tool is None:  # pragma: no cover - names() came from the same dict
            continue
        entry = tool.as_dict()
        entry["needs_approval"] = bool(
            tool.tier >= TIER_APPROVAL or (tool.domain and tool.domain in GATED_DOMAINS)
        )
        # A gate decides per call, so the listing cannot say yes or no — only
        # that this one is capable of being held. Saying "no" here for a tool
        # that will in fact ask would be the worse of the two errors.
        entry["may_escalate"] = tool.gate is not None
        tools.append(entry)
    return {"tools": tools, "count": len(tools)}


async def async_call_tool(
    jarvis: "Jarvis",
    name: str,
    arguments: Any = None,
    *,
    context: Any = None,
) -> dict[str, Any]:
    """Run one tool the way the model would, and answer with what it got.

    Straight through `ToolRegistry.call`, which is the whole design: argument
    coercion against the declared schema, the unknown-tool message with the
    list of real names, the approval gate, the `jarvis_tool_called` event. A
    console test-run that reimplemented any of that would be testing a second
    code path and reporting it as the first.

    ## This is not a way round the gate

    `registry.call` holds a Tier-3 tool here exactly as it holds it mid-turn:
    the reply is the approval-required payload and an approval card is raised.
    That is stricter than the console's existing reach, not looser —
    `call_service` has always been able to call any service directly and is
    not tiered at all. A test runner that skipped the gate would have made this
    page the easiest Tier-3 bypass in the product.
    """
    registry = _tool_registry(jarvis)
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ApiError("invalid_format", "a tool call needs a name", 400)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ApiError(
            "invalid_format",
            "tool arguments must be an object, not "
            f"{type(arguments).__name__}",
            400,
        )

    result = await registry.call(tool_name, arguments, context)
    # `call` answers `{"status": "error", "error": "unknown tool ..."}` rather
    # than raising, because that shape is what a model reads and acts on. A
    # REQUEST deserves the HTTP-shaped answer instead, so the console can tell
    # "no such tool" from "the tool ran and said no".
    if isinstance(result, dict) and str(result.get("error", "")).startswith("unknown tool"):
        raise ApiError("not_found", str(result.get("error")), 404)
    return {"tool": tool_name, "result": result}


def _taken_names(jarvis: "Jarvis") -> set[str]:
    """Names held by something that is not the authored store."""
    from ..llm.authored_tools import get_authored_tools

    registry = jarvis.data.get("llm_tools")
    if registry is None:
        return set()
    ours = set(get_authored_tools(jarvis).items)
    return {name for name in registry.names() if name not in ours}


def _register_authored(jarvis: "Jarvis", spec: dict[str, Any]) -> None:
    """Build one authored tool onto the running registry.

    Registered live rather than by rebuilding the agent: the registry has
    `register`/`remove`, and rebuilding would drop every in-flight approval
    request along with it.
    """
    from ..llm.tools import build_yaml_tools

    registry = _tool_registry(jarvis)
    client = jarvis.data.get("llm_client")
    factory = (lambda: client) if client is not None else None
    build_yaml_tools(registry, [spec], client_factory=factory)


async def async_create_tool(
    jarvis: "Jarvis", payload: dict[str, Any], *, allow_local_targets: bool = True
) -> dict[str, Any]:
    """Store and register a new authored tool.

    `allow_local_targets` is the console/model split. The console is the
    OPERATOR naming a service on their own box — photon, SearXNG, anything on
    loopback — which is the ordinary case and stays allowed. The `create_tool`
    LLM tool passes False, because a url the MODEL chose pointing at
    `127.0.0.1:8080` is jarvis-core's own API and reaches around every gate in
    `llm/tools.py`. See `helpers/ssrf.py`.
    """
    from ..llm.authored_tools import AuthoredToolError, get_authored_tools

    _tool_registry(jarvis)  # refuse early rather than storing an unusable tool
    try:
        entry = await get_authored_tools(jarvis).async_create(
            _tool_spec(payload),
            _taken_names(jarvis),
            allow_local_targets=allow_local_targets,
        )
    except AuthoredToolError as err:
        raise ApiError("invalid_format", str(err), 400) from err
    _register_authored(jarvis, {k: v for k, v in entry.items() if k not in ("created_at", "updated_at")})
    return {"tool": entry}


async def async_update_tool(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    from ..llm.authored_tools import AuthoredToolError, get_authored_tools

    registry = _tool_registry(jarvis)
    name = str(payload.get("name") or "").strip().lower()
    if not name:
        raise ApiError("invalid_format", "name is required", 400)
    try:
        entry = await get_authored_tools(jarvis).async_update(
            name, _tool_spec(payload), _taken_names(jarvis)
        )
    except AuthoredToolError as err:
        raise ApiError("invalid_format", str(err), 400) from err
    # Remove before rebuilding: `register` replaces by name, but going through
    # remove makes the "it is gone if the rebuild throws" case honest.
    registry.remove(name)
    _register_authored(jarvis, {k: v for k, v in entry.items() if k not in ("created_at", "updated_at")})
    return {"tool": entry}


async def async_delete_tool(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    from ..llm.authored_tools import get_authored_tools

    registry = _tool_registry(jarvis)
    name = str(payload.get("name") or "").strip().lower()
    if not name:
        raise ApiError("invalid_format", "name is required", 400)
    store = get_authored_tools(jarvis)
    if name not in store.items:
        # Refuses rather than silently doing nothing: the caller is asking to
        # delete a built-in or a YAML tool, and a quiet no-op looks like it
        # worked until the model calls the tool again.
        raise ApiError(
            "not_supported",
            f"{name} is not a tool this console created — it is built in or "
            "comes from your YAML, so it cannot be deleted here.",
            400,
        )
    await store.async_delete(name)
    registry.remove(name)
    return {"name": name, "deleted": True}


def _tool_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """The tool out of an API payload, without the transport's own keys."""
    spec = payload.get("tool")
    if isinstance(spec, dict):
        return dict(spec)
    return {key: value for key, value in payload.items() if key not in ("id", "type")}


# --- settings ---------------------------------------------------------------
#: How long the voice catalogue (Piper's voices, openWakeWord's models) is
#: reused before the services are asked again. See `async_refresh_choices`.
CATALOGUE_TTL = 60.0
_CATALOGUE_STAMP = "voice_catalogue_refreshed_at"

async def async_refresh_choices(jarvis: "Jarvis") -> None:
    """Re-ask the services what they offer, before the settings page is built.

    `choices_hook` is synchronous — it has to be, because it runs once per row
    while the payload is assembled — so anything that needs a network round trip
    has to have been fetched already. Voice is the case: Piper's voice list and
    openWakeWord's model list come from a Wyoming `describe`, and a container
    restarted since boot serves a different set.

    Best effort and never awaited by anything that matters. A probe that fails
    leaves the previous answer in place, and an empty answer degrades the field
    to the text box it used to be rather than failing the page.
    """
    voice = jarvis.data.get("voice")
    refresh = getattr(voice, "async_refresh_catalogue", None)
    if refresh is None:
        return
    # At most once a minute. Piper answers `describe` with every voice it can
    # serve — 83 KB on this install — and a settings page that polls asked for
    # all of it on every render. Worse, a probe abandoned mid-read (the box is
    # busy recognising speech; the read times out) makes Piper log a
    # twenty-frame ConnectionResetError, so a settings screen left open turned
    # into a stream of ERROR lines from a service that was perfectly fine.
    #
    # A minute is chosen against what the answer is FOR: the voice list changes
    # when someone restarts a container with different models, and being a
    # minute out of date about that has never mattered to anyone.
    now = time.monotonic()
    last = float(jarvis.data.get(_CATALOGUE_STAMP) or 0.0)
    if last and now - last < CATALOGUE_TTL:
        return
    jarvis.data[_CATALOGUE_STAMP] = now
    try:
        await refresh()
    except Exception:  # pragma: no cover - the page must still render
        _LOGGER.debug("could not refresh the voice catalogue", exc_info=True)


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
    from ..automation.reach import actions_of, describe_reach, needs_approval

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
                "action": actions_of(automation.config) or [],
                "editable": entry is not None,
                # Whether RUNNING this one has to go past a human, and why.
                # Shown in the console because "this automation can unlock the
                # front door" is worth knowing before you press the button, and
                # because it explains the approval prompt when it appears.
                "needs_approval": needs_approval(actions_of(automation.config)),
                "reach": describe_reach(automation.config.get("action")),
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


# --- the task list ----------------------------------------------------------
#
# Read-only plus two destructive verbs, and no "create" on purpose. A task is
# created by whatever is going to do the work — the assistant accepting a job,
# a research run, a schedule firing — because a task nothing is driving is the
# empty seam this registry was built to close. Letting a client mint one would
# put that hole back, one level out.


def _task_registry(jarvis: "Jarvis") -> Any:
    registry = getattr(jarvis, "tasks", None)
    if registry is None:
        raise ApiError("unavailable", "this server has no task list", 503)
    return registry


def task_list_payload(
    jarvis: "Jarvis", kind: str | None = None, active_only: bool = False
) -> dict[str, Any]:
    """Every tracked job, newest first.

    Whole tasks rather than summaries, unlike the conversation list: a task is
    a few hundred bytes and its steps ARE the interesting part — a list that
    made you fetch each row to draw its progress bar would be one request per
    visible task, on every update.
    """
    registry = _task_registry(jarvis)
    return {"tasks": registry.listing(kind=kind, active_only=active_only)}


def task_get_payload(jarvis: "Jarvis", task_id: str) -> dict[str, Any]:
    task = _task_registry(jarvis).get(str(task_id or ""))
    if task is None:
        raise ApiError("not_found", f"no task {task_id!r}", 404)
    return {"task": task.as_dict()}


async def async_retry_task(jarvis: Any, task_id: str) -> dict[str, Any]:
    """Put a finished task back on the queue.

    The button somebody presses after fixing whatever broke — a model server
    that was down, a repository that was dirty. Only work whose kind the engine
    can rebuild is retryable; anything else would be a button that does nothing,
    so it says so instead.
    """
    engine = getattr(jarvis, "taskengine", None)
    if engine is None:
        raise ApiError("unsupported", "this backend has no task engine", 501)
    task = _task_registry(jarvis).get(str(task_id or ""))
    if task is None:
        raise ApiError("not_found", f"no task {task_id!r}", 404)
    if not task.finished:
        raise ApiError("invalid_format", "that task has not finished", 400)
    if not engine.retry(task.id):
        raise ApiError(
            "unsupported",
            f"nothing on this server knows how to run {task.kind!r} work again",
            400,
        )
    return {"task": task.as_dict(), "queued": True}


async def async_delete_task(jarvis: "Jarvis", task_id: str) -> dict[str, Any]:
    """Forget one task. Does not stop it — see the note on cancel below."""
    registry = _task_registry(jarvis)
    if not await registry.async_remove(str(task_id or "")):
        raise ApiError("not_found", f"no task {task_id!r}", 404)
    return {"removed": task_id}


async def async_clear_finished_tasks(jarvis: "Jarvis") -> dict[str, Any]:
    return {"removed": await _task_registry(jarvis).async_clear_finished()}


async def async_cancel_task(jarvis: "Jarvis", task_id: str) -> dict[str, Any]:
    """Ask for a task to stop, and be honest that asking is all this does.

    The registry is a record, not a scheduler: nothing here can reach into the
    coroutine doing the work. Marking a task `cancelled` is a REQUEST that the
    worker is expected to notice, and a worker that ignores it will keep going
    and keep reporting. Saying "cancelled" while the work continues would be
    the same class of lie as the seam this registry replaced, so the payload
    says which of the two happened.
    """
    registry = _task_registry(jarvis)
    task = registry.get(str(task_id or ""))
    if task is None:
        raise ApiError("not_found", f"no task {task_id!r}", 404)
    if task.finished:
        return {"task": task.as_dict(), "cancelled": False, "reason": "already finished"}
    from ..tasks import STATUS_CANCELLED

    updated = await registry.async_update(
        task.id, status=STATUS_CANCELLED, detail="cancelled from a client"
    )
    return {
        "task": updated.as_dict() if updated else task.as_dict(),
        "cancelled": True,
        "note": (
            "marked cancelled; a worker that does not check for this may still "
            "be running"
        ),
    }


# --- MCP servers -------------------------------------------------------------
#
# Read, add, remove, reconnect. What is deliberately NOT here is a way to turn
# `allow_stdio` on: that is the line between "Jarvis fetches a URL" and "Jarvis
# starts a program", and it is set in configuration.yaml precisely so that no
# request — from a browser, a phone, a model or a forged one — can cross it.



def task_log_payload(jarvis: Any, task_id: str, limit: int = 200) -> dict[str, Any]:
    """One task's replayable history.

    The watching events (tool calls, output) are fire-and-forget: a client that
    opens a task's page two minutes into a job has missed every one of them.
    This is how it catches up, and it is a separate command rather than part of
    the task payload because the task payload is sent on every single update.
    """
    registry = getattr(jarvis, "tasks", None)
    if registry is None:
        return {"task_id": task_id, "log": []}
    return {"task_id": task_id, "log": registry.log_entries(task_id, limit=limit)}


# ---------------------------------------------------------------------------
# dashboards + metrics
# ---------------------------------------------------------------------------


def _dashboard_store(jarvis: Any) -> Any:
    store = getattr(jarvis, "data", {}).get("dashboards")
    if store is None:
        raise ApiError("unsupported", "this backend has no dashboards integration", 501)
    return store


def dashboards_list_payload(jarvis: Any, owner: str) -> dict[str, Any]:
    """This token's dashboards, plus the shared ones. Never somebody else's."""
    store = _dashboard_store(jarvis)
    return {"dashboards": store.visible_to(owner), "owner": owner}


async def async_dashboard_save(jarvis: Any, raw: Any, owner: str) -> dict[str, Any]:
    store = _dashboard_store(jarvis)
    try:
        board = await store.async_put(raw, owner)
    except ValueError as err:
        raise ApiError("invalid_format", str(err), 400) from err
    return {"dashboard": board}


async def async_dashboard_delete(jarvis: Any, dashboard_id: str, owner: str) -> dict[str, Any]:
    store = _dashboard_store(jarvis)
    if not await store.async_delete(dashboard_id, owner):
        raise ApiError("not_found", f"no dashboard {dashboard_id!r} you own", 404)
    return {"deleted": dashboard_id}


async def async_metrics_sources(jarvis: Any) -> dict[str, Any]:
    from ..integrations.dashboards import async_sources

    return {"sources": await async_sources(jarvis)}


async def async_metrics_query(jarvis: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """One widget's numbers.

    A source that is down answers with an error per series rather than raising:
    a dashboard with six widgets and one dead source should draw five graphs and
    one honest "cannot reach it", not a page-wide failure.
    """
    from ..integrations.dashboards import async_query, window_for

    keys = [str(k) for k in (payload.get("series") or []) if str(k).strip()][:20]
    if not keys:
        raise ApiError("invalid_format", "name at least one series", 400)
    window = window_for(payload)
    series = await async_query(
        jarvis,
        str(payload.get("source") or "internal"),
        keys,
        window,
        str(payload.get("aggregate") or ""),
    )
    return {
        "series": series,
        "start": window.start,
        "end": window.end,
        "step": window.resolved_step(),
    }

def _mcp(jarvis: "Jarvis") -> Any:
    from ..integrations.mcp import get_manager

    manager = get_manager(jarvis)
    if manager is None:
        raise ApiError("unavailable", "this server has no MCP integration", 503)
    return manager


def _briefing(jarvis: "Jarvis") -> Any:
    manager = jarvis.data.get("briefing")
    if manager is None:
        raise ApiError("not_configured", "the briefing integration is not set up", 400)
    return manager


def briefing_settings_payload(jarvis: "Jarvis") -> dict[str, Any]:
    return {"briefing": _briefing(jarvis).settings()}


def briefing_configure(jarvis: "Jarvis", changes: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"briefing": _briefing(jarvis).configure(changes)}
    except ValueError as err:
        raise ApiError("invalid_format", str(err), 400) from err


def _notifications(jarvis: "Jarvis") -> Any:
    store = jarvis.data.get("notifications")
    if store is None:
        raise ApiError("not_configured", "the notifications integration is not set up", 400)
    return store


def notifications_payload(jarvis: "Jarvis", unread_only: bool = False,
                          limit: int = 100) -> dict[str, Any]:
    store = _notifications(jarvis)
    return {
        "notifications": store.listing(unread_only=unread_only, limit=limit),
        "unread": store.unread,
    }


async def async_notification_read(jarvis: "Jarvis", entry_id: str = "",
                                  everything: bool = False) -> dict[str, Any]:
    return await _notifications(jarvis).async_mark_read(
        entry_id=entry_id, everything=everything
    )


async def async_notification_dismiss(jarvis: "Jarvis", entry_id: str = "",
                                     everything: bool = False) -> dict[str, Any]:
    return await _notifications(jarvis).async_dismiss(
        entry_id=entry_id, everything=everything
    )


def conversation_search_payload(jarvis: "Jarvis", query: str,
                                limit: int = 20) -> dict[str, Any]:
    """Threads containing `query`, with the line that matched.

    The match is what makes it useful: a person searching for "blue tin" wants
    the sentence, and the conversation it belongs to is what they click.
    """
    agent = jarvis.data.get("llm")
    archive = getattr(agent, "archive", None)
    if archive is None:
        raise ApiError("not_configured", "there is no conversation archive", 400)
    return {"query": query, "results": archive.search(query, limit=limit)}


def _notes(jarvis: "Jarvis") -> Any:
    store = jarvis.data.get("notes")
    if store is None:
        raise ApiError("not_configured", "the notes integration is not set up", 400)
    return store


def notes_list_payload(jarvis: "Jarvis", tag: str = "", query: str = "",
                       limit: int = 200) -> dict[str, Any]:
    store = _notes(jarvis)
    rows = store.search(query, tag, limit) if query else store.listing(tag=tag, limit=limit)
    return {"notes": rows, "total": len(store.notes), "query": query, "tag": tag}


def note_payload(jarvis: "Jarvis", key: str) -> dict[str, Any]:
    note = _notes(jarvis).get(str(key or ""))
    if note is None:
        raise ApiError("not_found", f"no note {key!r}", 404)
    return {"note": note.as_dict(body=True)}


async def async_note_create(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    store = _notes(jarvis)
    result = store.create(
        title=str(data.get("title") or ""),
        body=str(data.get("body") or data.get("text") or ""),
        tags=data.get("tags"),
        overwrite=bool(data.get("overwrite")),
    )
    if not result.get("created"):
        raise ApiError("invalid_format", str(result.get("error") or "could not create"), 400)
    return result


async def async_note_update(jarvis: "Jarvis", key: str, data: dict[str, Any]) -> dict[str, Any]:
    result = _notes(jarvis).update(
        key,
        body=data.get("body"),
        title=data.get("title"),
        tags=data.get("tags"),
    )
    if not result.get("updated"):
        raise ApiError("not_found", str(result.get("error") or "no such note"), 404)
    return result


async def async_note_append(jarvis: "Jarvis", key: str, text: str) -> dict[str, Any]:
    result = _notes(jarvis).append(key, text)
    if not result.get("appended"):
        raise ApiError("not_found", str(result.get("error") or "no such note"), 404)
    return result


async def async_note_delete(jarvis: "Jarvis", key: str) -> dict[str, Any]:
    result = _notes(jarvis).delete(key)
    if not result.get("deleted"):
        raise ApiError("not_found", str(result.get("error") or "no such note"), 404)
    return result


def _memory(jarvis: "Jarvis") -> Any:
    store = jarvis.data.get("memory")
    if store is None:
        raise ApiError("not_configured", "the memory integration is not set up", 400)
    return store


def memory_list_payload(jarvis: "Jarvis", tag: str = "", query: str = "",
                        limit: int = 200) -> dict[str, Any]:
    """Everything Jarvis remembers, newest first — or the matches for a query.

    The whole store, not a page of it: the point of this route is that a person
    can read what is held about them, and a listing that made that take three
    clicks would be a promise kept badly.
    """
    store = _memory(jarvis)
    entries = (
        store.search(query=query, tags=tag or None, limit=limit)
        if query
        else store.all(tag=tag or None, limit=limit)
    )
    return {
        "entries": [entry.as_dict() for entry in entries],
        "total": len(store.entries),
        "query": query,
        "tag": tag,
    }


async def async_memory_list_payload(
    jarvis: "Jarvis", tag: str = "", query: str = "", limit: int = 200
) -> dict[str, Any]:
    """The same listing, with semantic recall and the reranker in front of it.

    The synchronous version above is the fallback for callers that cannot
    await, and it is keyword-only by construction — an embedding call and a
    cross-encoder are both HTTP. Every route that CAN await uses this one, so
    what a person sees in the console is what the assistant sees.
    """
    store = _memory(jarvis)
    if not query:
        return memory_list_payload(jarvis, tag=tag, query=query, limit=limit)
    entries = await store.async_search(query=query, tags=tag or None, limit=limit)
    return {
        "entries": [entry.as_dict() for entry in entries],
        "total": len(store.entries),
        "query": query,
        "tag": tag,
    }


def _recorder(jarvis: "Jarvis") -> Any:
    return jarvis.data.get("observability")


def traces_payload(jarvis: "Jarvis", limit: int = 50, kind: str = "") -> dict[str, Any]:
    """Recent traces, newest first. An install with no recorder answers `[]`.

    Not an error: `observability:` is optional configuration, and a console
    that showed a red banner because tracing is off would be describing a
    choice as a fault.
    """
    recorder = _recorder(jarvis)
    if recorder is None:
        return {"traces": [], "recording": False}
    return {"traces": recorder.listing(limit=limit, kind=kind), "recording": True}


def trace_payload(jarvis: "Jarvis", trace_id: str = "", task_id: str = "") -> dict[str, Any]:
    """One trace, by its id or by the task it covers.

    `task_id` is what the UI's "view trace" link has: a task knows its own id
    and nothing about contexts, so the lookup belongs here rather than in the
    console.
    """
    recorder = _recorder(jarvis)
    if recorder is None:
        return {"trace": None, "recording": False}
    if not trace_id and task_id:
        trace_id = recorder.for_task(task_id)
    return {"trace": recorder.get(trace_id) if trace_id else None, "recording": True}


def memory_export_payload(jarvis: "Jarvis", fmt: str = "json") -> dict[str, Any]:
    return _memory(jarvis).export(fmt)


async def async_memory_forget(jarvis: "Jarvis", entry_id: str = "", query: str = "",
                              everything: bool = False) -> dict[str, Any]:
    store = _memory(jarvis)
    if everything:
        return await store.async_wipe()
    if not entry_id and not query:
        raise ApiError("invalid_format", "say which note: id or query", 400)
    return await store.async_forget(entry_id=entry_id, query=query)


async def async_memory_add(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    store = _memory(jarvis)
    return await store.async_add(
        text=str(data.get("text") or ""),
        tags=data.get("tags"),
        source=str(data.get("source") or "user"),
        pinned=bool(data.get("pinned")),
        # The console is the user typing, so it may store what the model may
        # not: this is the "yes, I know where it came from" switch.
        allow_untrusted=bool(data.get("allow_untrusted")),
    )


async def async_memory_pin(jarvis: "Jarvis", entry_id: str, pinned: bool) -> dict[str, Any]:
    store = _memory(jarvis)
    entry = store.get(str(entry_id or ""))
    if entry is None:
        raise ApiError("not_found", f"no note {entry_id!r}", 404)
    entry.pinned = bool(pinned)
    await store.async_save()
    return {"entry": entry.as_dict()}


def skills_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """Every loaded skill, and every one that could not be loaded.

    The errors are half the value: a skill somebody dropped in and mistyped is
    otherwise simply absent, and "it does not appear" is the least diagnosable
    failure a folder-based feature can have.
    """
    store = jarvis.data.get("skills")
    if store is None:
        return {"skills": [], "errors": [], "enabled": False}
    return {
        "skills": store.listing(),
        "errors": list(store.errors),
        "enabled": True,
        "path": str(store.root),
    }


def skill_payload(jarvis: "Jarvis", name: str) -> dict[str, Any]:
    """One skill, body included. What the console shows when you open one."""
    store = jarvis.data.get("skills")
    skill = store.get(name) if store is not None else None
    if skill is None:
        raise ApiError("not_found", f"no skill named {name!r}", 404)
    return {"skill": skill.as_dict(body=True)}


async def async_reload_skills(jarvis: "Jarvis") -> dict[str, Any]:
    store = jarvis.data.get("skills")
    if store is None:
        raise ApiError("not_configured", "the skills integration is not set up", 400)
    return {"loaded": store.load(), "errors": list(store.errors)}


async def extensions_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """Everything extensible, with health, in one call.

    Health is included rather than left to a second round trip: the page is a
    list of things that are either working or not, and a list that paints
    without that is a list that changes under the reader a moment later.
    """
    registry = jarvis.data.get("extensions")
    if registry is None:
        return {"extensions": [], "errors": [], "enabled": False}
    await registry.health()
    state = jarvis.data.get("extensions_state")
    rows = registry.listing()
    if state is not None:
        for row in rows:
            row["last_used"] = state.last_used.get(row["key"])
    return {
        "extensions": rows,
        "errors": list(registry.errors),
        "enabled": True,
        "permissions": list(_extension_permissions()),
        "counts": {
            kind: len([r for r in rows if r["kind"] == kind])
            for kind in ("skill", "mcp", "plugin")
        },
    }


def _extension_permissions() -> tuple[str, ...]:
    from ..integrations.extensions.manifest import PERMISSIONS

    return PERMISSIONS


async def async_set_extension(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    """Enable/disable, or narrow the permission scope. Applied before it returns."""
    if jarvis.data.get("extensions") is None:
        raise ApiError("not_configured", "the extension registry is not set up", 400)
    result = await jarvis.services.async_call(
        "extensions", "set", dict(data), blocking=True, return_response=True
    )
    if isinstance(result, dict) and result.get("error"):
        raise ApiError("invalid", str(result["error"]), 400)
    return result or {}


async def async_scaffold_skill(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    if jarvis.data.get("extensions") is None:
        raise ApiError("not_configured", "the extension registry is not set up", 400)
    result = await jarvis.services.async_call(
        "extensions", "scaffold", dict(data), blocking=True, return_response=True
    )
    if isinstance(result, dict) and result.get("error"):
        raise ApiError("invalid", str(result["error"]), 400)
    return result or {}


async def extensions_browse_payload(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    return await jarvis.services.async_call(
        "extensions",
        "browse",
        {"query": msg.get("query") or "", "kind": msg.get("kind") or ""},
        blocking=True,
        return_response=True,
    ) or {}


def _entry_id(msg: dict[str, Any]) -> str:
    """The catalog entry's id, from `entry` and never from `id`.

    `id` is the WEBSOCKET ENVELOPE's message id. Reading the entry out of it
    worked in every test that called the service directly and failed the moment
    a browser sent a real frame, because by then `id` was the integer the
    protocol uses to match a reply to a request.
    """
    return str(msg.get("entry") or msg.get("entry_id") or "")


async def extensions_plan_payload(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    return await jarvis.services.async_call(
        "extensions",
        "plan",
        {
            "source": msg.get("source") or "",
            "id": _entry_id(msg),
            "sha256": msg.get("sha256") or "",
            "refs": msg.get("refs") or [],
        },
        blocking=True,
        return_response=True,
    ) or {}


async def async_install_extension(jarvis: "Jarvis", msg: dict[str, Any]) -> dict[str, Any]:
    result = await jarvis.services.async_call(
        "extensions",
        "install",
        {
            "source": msg.get("source") or "",
            "id": _entry_id(msg),
            "approved": msg.get("approved"),
        },
        blocking=True,
        return_response=True,
    )
    if isinstance(result, dict) and result.get("error"):
        raise ApiError("invalid", str(result["error"]), 400)
    return result or {}


def mcp_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    manager = _mcp(jarvis)
    return {
        "servers": manager.listing(),
        # So the console can say WHY the stdio fields are disabled rather than
        # merely refusing the form after it is filled in.
        "allow_stdio": manager.allow_stdio,
        "default_tier": manager.default_tier,
    }


def mcp_inspect_payload(jarvis: "Jarvis", name: str) -> dict[str, Any]:
    """One server in full: its schemas, its protocol version, its last error."""
    manager = _mcp(jarvis)
    try:
        return {"server": manager.inspect(str(name or ""))}
    except KeyError as err:
        raise ApiError("not_found", f"no MCP server named {name!r}", 404) from err


async def async_add_mcp_server(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    from ..integrations.mcp import async_add_server

    result = await async_add_server(_mcp(jarvis), data or {})
    if result.get("status") == "error":
        raise ApiError("invalid_format", str(result.get("error") or "could not add it"), 400)
    return result


async def async_remove_mcp_server(jarvis: "Jarvis", name: str) -> dict[str, Any]:
    from ..integrations.mcp import async_remove_server

    result = await async_remove_server(_mcp(jarvis), str(name or ""))
    if result.get("status") == "error":
        raise ApiError("not_found", str(result.get("error") or "no such server"), 404)
    return result


async def async_reconnect_mcp(jarvis: "Jarvis", name: str = "") -> dict[str, Any]:
    """Bring a server back, or all of them, and refresh what they offer.

    The refresh is the point as much as the reconnect: a server that gained a
    tool since Jarvis started is invisible until something asks it again.
    """
    manager = _mcp(jarvis)
    from ..integrations.mcp.catalog import safe_server_name

    key = safe_server_name(name)
    if not key:
        await manager.async_connect_all()
        return {"reconnected": "all", **mcp_list_payload(jarvis)}
    spec = manager.servers.get(key)
    if spec is None:
        raise ApiError("not_found", f"no MCP server called {name!r}", 404)
    connected = await manager.async_connect(spec)
    return {"reconnected": key, "connected": connected, **mcp_list_payload(jarvis)}


# --- Jarvis Code -------------------------------------------------------------


def _code(jarvis: "Jarvis") -> Any:
    from ..integrations.code import get_config

    cfg = get_config(jarvis)
    if cfg is None:
        raise ApiError("unavailable", "this server has no code integration", 503)
    return cfg


def code_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """Repositories, jobs, and whether checks run behind a wrapper."""
    from ..integrations.code import listing_payload

    _code(jarvis)
    return listing_payload(jarvis)


def code_result_payload(jarvis: "Jarvis", task_id: str) -> dict[str, Any]:
    from ..integrations.code import result_payload

    _code(jarvis)
    found = result_payload(jarvis, str(task_id or ""))
    if found is None:
        raise ApiError("not_found", "no finished coding job with that id", 404)
    return found


async def async_create_code_repository(
    jarvis: "Jarvis", data: dict[str, Any]
) -> dict[str, Any]:
    """Make a repository from the console.

    Same authority as starting a job and the same asymmetry with the model:
    this request carried a bearer token. Unlike the model's `create_repository`
    tool it is not tiered at all — but it also cannot reach outside the
    workspace root, because the confinement is in `RepoStore`, not in the
    caller.
    """
    from ..integrations.code import async_create_repository

    _code(jarvis)
    payload = data or {}
    entry, why = await async_create_repository(
        jarvis,
        str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        environment=str(payload.get("environment") or ""),
    )
    if entry is None:
        raise ApiError("invalid_format", why, 400)
    return {"repository": entry.as_dict(), **code_list_payload(jarvis)}


async def async_forget_code_repository(jarvis: "Jarvis", name: str) -> dict[str, Any]:
    """Drop it from the registry. The files stay — Jarvis does not delete."""
    from ..integrations.code import get_repos

    cfg = _code(jarvis)
    repos = get_repos(jarvis)
    if repos is None:
        raise ApiError("unavailable", "this server has no code integration", 503)
    gone, note = await repos.async_forget(str(name or ""))
    if not gone:
        raise ApiError("not_found", note, 404)
    cfg.repositories.pop(str(name), None)
    return {"forgotten": name, "note": note, **code_list_payload(jarvis)}


async def async_clone_code_repository(
    jarvis: "Jarvis", data: dict[str, Any]
) -> dict[str, Any]:
    """Clone a permitted repository from a forge.

    The allow-list is enforced in the integration, not here — the console has
    exactly the same reach as the model on this one, because the constraint is
    the operator's configuration rather than who is asking.
    """
    from ..integrations.code import async_clone_repository

    _code(jarvis)
    payload = data or {}
    entry, why = await async_clone_repository(
        jarvis,
        str(payload.get("forge") or ""),
        str(payload.get("project") or ""),
        name=str(payload.get("name") or ""),
        environment=str(payload.get("environment") or ""),
    )
    if entry is None:
        raise ApiError("invalid_format", why, 400)
    return {"repository": entry.as_dict(), **code_list_payload(jarvis)}


async def async_push_code_branch(
    jarvis: "Jarvis", data: dict[str, Any]
) -> dict[str, Any]:
    """Push a `jarvis/…` branch back to its forge."""
    from ..integrations.code import async_push_branch

    _code(jarvis)
    payload = data or {}
    ok, note = await async_push_branch(
        jarvis, str(payload.get("repo") or ""), str(payload.get("branch") or "")
    )
    if not ok:
        raise ApiError("invalid_format", note, 400)
    return {"pushed": True, "note": note}


async def async_start_code_job(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    """Start a coding job from an authenticated caller.

    Not approval-gated, unlike the model's `start_coding_job` tool. Same asymmetry and
    same reason as `async_add_scheduled`: a request that reached here carried a
    bearer token, whereas a tool call may have been shaped by a page the model
    read.
    """
    from ..integrations.code import async_start

    _code(jarvis)
    payload = data or {}
    started = await async_start(
        jarvis,
        str(payload.get("repo") or ""),
        str(payload.get("instruction") or ""),
        source=str(payload.get("source") or "console"),
        # Per task, from a caller holding a bearer token — a person in the
        # console choosing how much this particular job may do without asking.
        # Never from the model: `start_coding_job` does not forward it, for the
        # same reason the model does not pick its own environment.
        mode=str(payload.get("mode") or ""),
    )
    if isinstance(started, str):
        raise ApiError("invalid_format", started, 400)
    return {"task_id": started.id, "title": started.title, "task": started.as_dict()}


# --- scheduled jobs ----------------------------------------------------------


def _schedule(jarvis: "Jarvis") -> Any:
    from ..integrations.schedule import get_manager

    manager = get_manager(jarvis)
    if manager is None:
        raise ApiError("unavailable", "this server has no scheduler", 503)
    return manager


def schedule_list_payload(jarvis: "Jarvis") -> dict[str, Any]:
    return {"jobs": _schedule(jarvis).listing()}


async def async_add_scheduled(jarvis: "Jarvis", data: dict[str, Any]) -> dict[str, Any]:
    """Add a job from an authenticated caller.

    `allow_service=True`, unlike the model's tool. A request that reached here
    carried a bearer token; a tool call may have been shaped by a web page the
    model read, which is why the two doors are different widths.
    """
    result = await _schedule(jarvis).async_add(data or {}, allow_service=True)
    if result.get("status") == "error":
        raise ApiError("invalid_format", str(result.get("error") or "could not add it"), 400)
    return result


async def async_remove_scheduled(jarvis: "Jarvis", job_id: str) -> dict[str, Any]:
    result = await _schedule(jarvis).async_remove(str(job_id or ""))
    if result.get("status") == "error":
        raise ApiError("not_found", str(result.get("error") or "no such job"), 404)
    return result


async def async_enable_scheduled(
    jarvis: "Jarvis", job_id: str, enabled: bool
) -> dict[str, Any]:
    result = await _schedule(jarvis).async_set_enabled(str(job_id or ""), enabled)
    if result.get("status") == "error":
        raise ApiError("not_found", str(result.get("error") or "no such job"), 404)
    return result
