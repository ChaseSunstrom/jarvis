"""The REST API — the plain-HTTP half of the Jarvis contract.

Shaped after Home Assistant's ``/api/`` so existing scripts, curl one-liners
and the ESP32 satellites keep working:

    GET  /healthz                             (open)
    GET  /api/                                status
    GET  /api/states  /api/states/{id}
    POST /api/states/{id}
    POST /api/services/{domain}/{service}
    GET  /api/config  /api/events
    POST /api/events/{event_type}
    GET  /api/history/period[/{timestamp}]
    POST /api/conversation/process
    POST /api/jarvis/approve
    GET/POST the entity, device and area registries
    GET  /api/tts_proxy/{token}.wav           (open — the token is the secret)
    ANY  /api/webhook/{webhook_id}            (open — the id is the secret)

Everything else needs ``Authorization: Bearer <long-lived-token>``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ..auth import TokenInfo, extract_bearer_token, get_auth
from ..const import VERSION
from ..state import valid_entity_id
from . import common
from .common import ApiError

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_jarvis(request: Request) -> "Jarvis":
    jarvis = getattr(request.app.state, "jarvis", None)
    if jarvis is None:  # pragma: no cover - create_app always sets this
        raise HTTPException(503, "Jarvis is not attached to this app")
    return jarvis


async def require_token(request: Request) -> TokenInfo:
    """Bearer-token gate. Absent auth manager means locked, never open."""
    auth = get_auth(get_jarvis(request))
    token = extract_bearer_token(request.headers.get("authorization"))
    info = auth.verify(token) if auth is not None else None
    if info is None:
        raise HTTPException(401, "Unauthorized", headers=UNAUTHORIZED_HEADERS)
    return info


async def json_body(request: Request) -> dict[str, Any]:
    """Parse a JSON object body, tolerating an empty one."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = await request.json()
    except Exception as err:
        raise HTTPException(400, f"invalid JSON body: {err}") from err
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise HTTPException(400, "body must be a JSON object")
    return payload


def _api_error(err: ApiError) -> HTTPException:
    return HTTPException(err.status, {"code": err.code, "message": err.message})


def _context(token: TokenInfo | None = None) -> Any:
    """Trace anything this request causes back to the token that asked."""
    return common.api_context(token.id if token else None)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# open routes: liveness, TTS audio, webhooks
# ---------------------------------------------------------------------------
open_router = APIRouter()


@open_router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    jarvis = get_jarvis(request)
    return {
        "status": "ok",
        "version": VERSION,
        "running": bool(jarvis.is_running),
        "entities": len(jarvis.states.all()),
    }


@open_router.get("/api/tts_proxy/{filename}")
async def tts_proxy(request: Request, filename: str) -> Response:
    """Serve cached speech. Unauthenticated: audio players cannot send headers."""
    audio = common.tts_audio(get_jarvis(request), filename)
    if audio is None:
        raise HTTPException(404, "no cached audio for that token")
    data, mime = audio
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=300"},
    )


# One route per method rather than a single multi-method `api_route`: FastAPI
# derives a route's operation id from just one of its methods, so a
# multi-method route emits duplicate ids and warns during schema generation.
@open_router.get("/api/webhook/{webhook_id}")
@open_router.post("/api/webhook/{webhook_id}")
@open_router.put("/api/webhook/{webhook_id}")
# HEAD is a probe some senders make before they trust an endpoint; it is kept
# out of the schema so it needs no operation id of its own.
@open_router.head("/api/webhook/{webhook_id}", include_in_schema=False)
async def webhook(request: Request, webhook_id: str) -> dict[str, Any]:
    """Inbound webhook. The id is the secret, exactly as in Home Assistant."""
    jarvis = get_jarvis(request)
    if _truthy((jarvis.config.get("jarvis") or {}).get("webhook_require_auth")):
        await require_token(request)

    data: Any = None
    raw = await request.body()
    if raw:
        try:
            data = await request.json()
        except Exception:
            data = raw.decode("utf-8", "replace")
    try:
        delivered = await common.async_dispatch_webhook(
            jarvis,
            webhook_id,
            data,
            query=dict(request.query_params),
            headers=dict(request.headers),
            method=request.method,
        )
    except ApiError as err:
        raise _api_error(err) from err
    return {"webhook_id": webhook_id, "delivered": delivered}


# ---------------------------------------------------------------------------
# guarded routes
# ---------------------------------------------------------------------------
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


@api_router.get("/")
async def api_root(request: Request) -> dict[str, Any]:
    jarvis = get_jarvis(request)
    return {
        "message": "API running.",
        "version": VERSION,
        "ha_version": common.HA_VERSION,
        "state": "RUNNING" if jarvis.is_running else "STARTING",
    }


# --- states ---------------------------------------------------------------
@api_router.get("/states")
async def get_states(request: Request) -> list[dict[str, Any]]:
    return common.states_payload(get_jarvis(request))


@api_router.get("/states/{entity_id}")
async def get_state(request: Request, entity_id: str) -> dict[str, Any]:
    state = get_jarvis(request).states.get(entity_id)
    if state is None:
        raise HTTPException(404, f"unknown entity {entity_id}")
    return state.as_dict()


@api_router.post("/states/{entity_id}")
async def set_state(
    request: Request, entity_id: str, token: TokenInfo = Depends(require_token)
) -> Response:
    jarvis = get_jarvis(request)
    if not valid_entity_id(entity_id.lower()):
        raise HTTPException(400, f"malformed entity_id {entity_id!r}")
    body = await json_body(request)
    if "state" not in body:
        raise HTTPException(400, "body needs a 'state'")
    attributes = body.get("attributes")
    if attributes is not None and not isinstance(attributes, dict):
        # StateMachine.set does dict(attributes), which raises ValueError deep
        # in the state machine for a string or a list — a 500 for a bad request.
        raise HTTPException(400, "'attributes' must be a JSON object")
    existed = jarvis.states.get(entity_id) is not None
    state = jarvis.states.set(
        entity_id,
        body["state"],
        attributes,
        force_update=bool(body.get("force_update")),
        context=_context(token),
    )
    return JSONResponse(state.as_dict(), status_code=200 if existed else 201)


@api_router.delete("/states/{entity_id}")
async def delete_state(
    request: Request, entity_id: str, token: TokenInfo = Depends(require_token)
) -> dict[str, Any]:
    removed = get_jarvis(request).states.remove(entity_id, _context(token))
    if not removed:
        raise HTTPException(404, f"unknown entity {entity_id}")
    return {"entity_id": entity_id, "removed": True}


# --- services -------------------------------------------------------------
@api_router.get("/services")
async def get_services(request: Request) -> dict[str, Any]:
    return common.services_payload(get_jarvis(request))


@api_router.post("/services/{domain}/{service}")
async def call_service(
    request: Request,
    domain: str,
    service: str,
    token: TokenInfo = Depends(require_token),
) -> Any:
    body = await json_body(request)
    target = body.pop("target", None)
    return_response = _truthy(
        request.query_params.get("return_response", body.pop("return_response", False))
    )
    try:
        outcome = await common.async_call_service(
            get_jarvis(request),
            domain,
            service,
            body,
            target if isinstance(target, dict) else None,
            context=_context(token),
            return_response=return_response,
        )
    except ApiError as err:
        raise _api_error(err) from err
    if return_response:
        return {
            "changed_states": outcome.changed_states,
            "service_response": outcome.response,
        }
    return outcome.changed_states


# --- config / events ------------------------------------------------------
@api_router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    return common.config_payload(get_jarvis(request))


@api_router.get("/events")
async def get_events(request: Request) -> list[dict[str, Any]]:
    return common.events_payload(get_jarvis(request))


@api_router.post("/events/{event_type}")
async def fire_event(
    request: Request, event_type: str, token: TokenInfo = Depends(require_token)
) -> dict[str, Any]:
    body = await json_body(request)
    context = _context(token)
    get_jarvis(request).bus.fire(event_type, body, context)
    return {
        "message": f"Event {event_type} fired.",
        "context": context.as_dict(),
    }


# --- history --------------------------------------------------------------
@api_router.get("/history/period")
@api_router.get("/history/period/{timestamp}")
async def history_period(request: Request, timestamp: str | None = None) -> list[Any]:
    raw = request.query_params.get("filter_entity_id")
    entity_ids = [item for item in (raw or "").split(",") if item.strip()] or None
    return await common.async_history(
        get_jarvis(request),
        entity_ids,
        timestamp or request.query_params.get("start_time"),
        request.query_params.get("end_time"),
    )


# --- conversation / approvals ---------------------------------------------
@api_router.post("/conversation/process")
async def conversation_process(
    request: Request, token: TokenInfo = Depends(require_token)
) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_conversation_process(
            get_jarvis(request),
            str(body.get("text") or ""),
            body.get("conversation_id"),
            body.get("language"),
            body.get("agent_id"),
            context=_context(token),
        )
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/jarvis/approve")
async def approve(
    request: Request, token: TokenInfo = Depends(require_token)
) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_approve(
            get_jarvis(request),
            str(body.get("request_id") or ""),
            # Raw, not bool(): common.approval_flag fails closed on "false".
            body.get("approved"),
            context=_context(token),
        )
    except ApiError as err:
        raise _api_error(err) from err


# --- voice ----------------------------------------------------------------
@api_router.get("/assist_pipeline/pipelines")
async def list_pipelines(request: Request) -> dict[str, Any]:
    return common.pipeline_list_payload(get_jarvis(request))


# --- registries -----------------------------------------------------------
@api_router.get("/config/entity_registry/list")
async def entity_registry_list(request: Request) -> list[dict[str, Any]]:
    return common.entity_registry_payload(get_jarvis(request))


@api_router.post("/config/entity_registry/update")
async def entity_registry_update(request: Request) -> dict[str, Any]:
    try:
        return await common.async_update_entity(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/config/device_registry/list")
async def device_registry_list(request: Request) -> list[dict[str, Any]]:
    return common.device_registry_payload(get_jarvis(request))


@api_router.post("/config/device_registry/update")
async def device_registry_update(request: Request) -> dict[str, Any]:
    try:
        return await common.async_update_device(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/config/area_registry/list")
async def area_registry_list(request: Request) -> list[dict[str, Any]]:
    return common.area_registry_payload(get_jarvis(request))


@api_router.post("/config/area_registry/create")
async def area_registry_create(request: Request) -> dict[str, Any]:
    try:
        return await common.async_create_area(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/area_registry/update")
async def area_registry_update(request: Request) -> dict[str, Any]:
    try:
        return await common.async_update_area(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/area_registry/delete")
async def area_registry_delete(request: Request) -> dict[str, Any]:
    try:
        return await common.async_delete_area(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/config/settings/list")
async def settings_list(request: Request) -> dict[str, Any]:
    return common.settings_payload(get_jarvis(request))


@api_router.post("/config/settings/set")
async def settings_set(request: Request) -> dict[str, Any]:
    try:
        return await common.async_set_setting(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/settings/reset")
async def settings_reset(request: Request) -> dict[str, Any]:
    try:
        return await common.async_reset_setting(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/config/automation/list")
async def automation_list(request: Request) -> list[dict[str, Any]]:
    return common.automation_list_payload(get_jarvis(request))


@api_router.post("/config/automation/create")
async def automation_create(request: Request) -> dict[str, Any]:
    try:
        return await common.async_create_automation(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/automation/update")
async def automation_update(request: Request) -> dict[str, Any]:
    try:
        return await common.async_update_automation(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/automation/delete")
async def automation_delete(request: Request) -> dict[str, Any]:
    try:
        return await common.async_delete_automation(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


# --- tokens ---------------------------------------------------------------
@api_router.get("/auth/tokens")
async def list_tokens(request: Request) -> list[dict[str, Any]]:
    auth = get_auth(get_jarvis(request))
    return [info.as_dict() for info in auth.list_tokens()] if auth else []


@api_router.post("/auth/tokens")
async def create_token(request: Request) -> dict[str, Any]:
    auth = get_auth(get_jarvis(request))
    if auth is None:
        raise HTTPException(503, "auth is not set up")
    body = await json_body(request)
    info, secret = await auth.create_token(str(body.get("name") or "api"))
    # The only time the secret is ever transmitted.
    return {**info.as_dict(), "access_token": secret}


@api_router.delete("/auth/tokens/{token_id}")
async def revoke_token(request: Request, token_id: str) -> dict[str, Any]:
    auth = get_auth(get_jarvis(request))
    if auth is None or not await auth.revoke(token_id):
        raise HTTPException(404, f"unknown token {token_id}")
    return {"id": token_id, "revoked": True}
