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


@open_router.post("/api/pair/claim")
async def pair_claim(request: Request) -> dict[str, Any]:
    """Exchange a pairing code for a real token.

    The only unauthenticated write in this API, and it has to be: the phone has
    no credential yet, which is the entire problem being solved. What makes it
    safe is on the other side — see `api/pairing.py` — a 192-bit code that
    lives five minutes, is removed before the token is minted, is compared in
    constant time, and stops being answerable at all after ten failures.
    """
    from . import pairing

    # A browser may not claim. Browsers always send `Origin` on a cross-origin
    # POST and phones never do, so this costs the real client nothing and takes
    # the hostile-web-page attacker off the one unauthenticated write here.
    if request.headers.get("origin"):
        raise HTTPException(
            status_code=403,
            detail="Pairing codes are claimed by the app, not from a browser.",
        )
    body = await json_body(request)
    try:
        return await pairing.async_claim(
            get_jarvis(request),
            body,
            # Rate-limit bucket only. Spoofing it buys a fresh allowance and
            # nothing else — the code's entropy is what the security rests on.
            client=request.client.host if request.client else None,
        )
    except pairing.PairingError as err:
        # 403 rather than 404: the code was structurally a claim and it was
        # refused. A 404 would suggest the endpoint is not there and send
        # somebody debugging their reverse proxy.
        raise HTTPException(status_code=403, detail=str(err)) from err


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
            answer=body.get("answer"),
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


@api_router.get("/config/companion/list")
async def companion_list(request: Request) -> list[dict[str, Any]]:
    return common.companion_list_payload(get_jarvis(request))


@api_router.get("/config/tool/list")
async def tool_list(request: Request) -> list[dict[str, Any]]:
    return common.tool_list_payload(get_jarvis(request))


@api_router.post("/config/tool/create")
async def tool_create(request: Request) -> dict[str, Any]:
    try:
        return await common.async_create_tool(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/tool/update")
async def tool_update(request: Request) -> dict[str, Any]:
    try:
        return await common.async_update_tool(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/config/tool/delete")
async def tool_delete(request: Request) -> dict[str, Any]:
    try:
        return await common.async_delete_tool(get_jarvis(request), await json_body(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/conversation/list")
async def conversation_list(request: Request) -> dict[str, Any]:
    """Past conversations, most recent first. Summaries only."""
    return common.conversation_list_payload(get_jarvis(request))


@api_router.get("/conversation/{conversation_id}")
async def conversation_get(request: Request, conversation_id: str) -> dict[str, Any]:
    """One past conversation in full, with its reasoning and tool rows."""
    try:
        return common.conversation_get_payload(get_jarvis(request), conversation_id)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/conversation/delete")
async def conversation_delete(request: Request) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_delete_conversation(
            get_jarvis(request), str(body.get("conversation_id") or "")
        )
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/conversation/rename")
async def conversation_rename(request: Request) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_rename_conversation(
            get_jarvis(request),
            str(body.get("conversation_id") or ""),
            str(body.get("title") or ""),
        )
    except ApiError as err:
        raise _api_error(err) from err


# --- tasks -----------------------------------------------------------------
@api_router.get("/tasks")
async def task_list(request: Request) -> dict[str, Any]:
    """Every tracked job, newest first.

    `?kind=research` filters; `?active=1` hides finished ones, which is what a
    progress strip wants and a task page does not.
    """
    params = request.query_params
    active = str(params.get("active") or "").strip().lower() in ("1", "true", "yes")
    try:
        return common.task_list_payload(
            get_jarvis(request), kind=params.get("kind") or None, active_only=active
        )
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/tasks/{task_id}")
async def task_get(request: Request, task_id: str) -> dict[str, Any]:
    try:
        return common.task_get_payload(get_jarvis(request), task_id)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/tasks/{task_id}/cancel")
async def task_cancel(request: Request, task_id: str) -> dict[str, Any]:
    try:
        return await common.async_cancel_task(get_jarvis(request), task_id)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.delete("/tasks/{task_id}")
async def task_delete(request: Request, task_id: str) -> dict[str, Any]:
    try:
        return await common.async_delete_task(get_jarvis(request), task_id)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/tasks/clear_finished")
async def task_clear_finished(request: Request) -> dict[str, Any]:
    try:
        return await common.async_clear_finished_tasks(get_jarvis(request))
    except ApiError as err:
        raise _api_error(err) from err


# --- scheduled jobs ---------------------------------------------------------
@api_router.get("/schedule")
async def schedule_list(request: Request) -> dict[str, Any]:
    try:
        return common.schedule_list_payload(get_jarvis(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/schedule")
async def schedule_add(request: Request) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_add_scheduled(get_jarvis(request), body)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.delete("/schedule/{job_id}")
async def schedule_remove(request: Request, job_id: str) -> dict[str, Any]:
    try:
        return await common.async_remove_scheduled(get_jarvis(request), job_id)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/schedule/{job_id}/enabled")
async def schedule_enabled(request: Request, job_id: str) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_enable_scheduled(
            get_jarvis(request), job_id, bool(body.get("enabled", True))
        )
    except ApiError as err:
        raise _api_error(err) from err


# --- Jarvis Code ------------------------------------------------------------
@api_router.get("/code")
async def code_list(request: Request) -> dict[str, Any]:
    try:
        return common.code_list_payload(get_jarvis(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/code/jobs")
async def code_start(request: Request) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_start_code_job(get_jarvis(request), body)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.get("/code/jobs/{task_id}")
async def code_result(request: Request, task_id: str) -> dict[str, Any]:
    try:
        return common.code_result_payload(get_jarvis(request), task_id)
    except ApiError as err:
        raise _api_error(err) from err


# --- MCP servers ------------------------------------------------------------
@api_router.get("/mcp/servers")
async def mcp_servers(request: Request) -> dict[str, Any]:
    try:
        return common.mcp_list_payload(get_jarvis(request))
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/mcp/servers")
async def mcp_server_add(request: Request) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_add_mcp_server(get_jarvis(request), body)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.delete("/mcp/servers/{name}")
async def mcp_server_remove(request: Request, name: str) -> dict[str, Any]:
    try:
        return await common.async_remove_mcp_server(get_jarvis(request), name)
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/mcp/reconnect")
async def mcp_reconnect(request: Request) -> dict[str, Any]:
    body = await json_body(request)
    try:
        return await common.async_reconnect_mcp(
            get_jarvis(request), str(body.get("name") or "")
        )
    except ApiError as err:
        raise _api_error(err) from err


@api_router.post("/pair/new")
async def pair_new(request: Request) -> dict[str, Any]:
    """Mint a pairing code for the console to draw as a QR.

    Authenticated, because inviting a new device onto the house is something
    only somebody already inside may do. The code it returns is not a
    credential — see `api/pairing.py` for why the QR deliberately does not
    carry one.
    """
    from . import pairing

    body = await json_body(request)
    try:
        return await pairing.async_issue(get_jarvis(request), body)
    except pairing.PairingError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err


@api_router.get("/models/list")
async def models_list(request: Request) -> dict[str, Any]:
    """What the phone can run locally, and whether it is here yet."""
    from . import models as model_store

    return {"models": model_store.catalogue_payload(get_jarvis(request))}


@api_router.get("/models/{name}")
async def models_get(request: Request, name: str) -> Response:
    """Serve a model file to the app, fetching it once if this is the first ask.

    Authenticated like every other route on this router, which is the point:
    the phone reaches its own Jarvis with the token it already holds, and never
    talks to GitHub. See `api/models.py` for why that matters more than it
    might look.
    """
    from . import models as model_store

    jarvis = get_jarvis(request)
    try:
        path = await model_store.async_ensure(jarvis, name)
    except model_store.ModelError as err:
        # 404 for "no such model", 502 for "the mirror could not get it" — the
        # app retries the second and gives up on the first.
        status = 404 if "not a model" in str(err) else 502
        raise HTTPException(status_code=status, detail=str(err)) from err
    return Response(
        content=path.read_bytes(),
        media_type="application/octet-stream",
        headers={
            # The digest travels with the bytes so the phone can verify what it
            # received rather than trusting the transfer.
            "X-Jarvis-SHA256": model_store.CATALOGUE_BY_NAME[name].sha256,
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


# --- whose voice this Jarvis answers ----------------------------------------
@api_router.get("/voice/speaker")
async def speaker_status(request: Request) -> dict[str, Any]:
    from . import speaker as speaker_api

    try:
        return speaker_api.status(get_jarvis(request))
    except speaker_api.EnrolError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err


@api_router.post("/voice/speaker/enrol")
async def speaker_enrol(request: Request) -> dict[str, Any]:
    """Add one enrolment sample. Body is a WAV, or raw 16 kHz mono PCM.

    Raw PCM is accepted because the phone already has the samples in that shape
    and wrapping them in a container to send them back would be ceremony. The
    rate and width can be named in the query string for anything else.
    """
    from . import speaker as speaker_api

    try:
        return await speaker_api.async_enrol(
            get_jarvis(request),
            await request.body(),
            request.headers.get("content-type", ""),
            int(request.query_params.get("rate") or 16000),
            int(request.query_params.get("width") or 2),
        )
    except speaker_api.EnrolError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=f"bad rate/width: {err}") from err


@api_router.post("/voice/speaker/verify")
async def speaker_verify(request: Request) -> dict[str, Any]:
    """Score a sample without enrolling it — how you find your threshold."""
    from . import speaker as speaker_api

    try:
        return await speaker_api.async_verify(
            get_jarvis(request),
            await request.body(),
            request.headers.get("content-type", ""),
            int(request.query_params.get("rate") or 16000),
            int(request.query_params.get("width") or 2),
        )
    except speaker_api.EnrolError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=f"bad rate/width: {err}") from err


@api_router.delete("/voice/speaker")
async def speaker_forget(request: Request) -> dict[str, Any]:
    from . import speaker as speaker_api

    try:
        return await speaker_api.async_forget(get_jarvis(request))
    except speaker_api.EnrolError as err:
        raise HTTPException(status_code=err.status, detail=str(err)) from err


@api_router.get("/config/settings/list")
async def settings_list(request: Request) -> dict[str, Any]:
    jarvis = get_jarvis(request)
    # Opening the settings page is the moment to re-ask the voice services what
    # they serve: a Piper restarted with a different voice since boot would
    # otherwise be offered yesterday's list.
    await common.async_refresh_choices(jarvis)
    return common.settings_payload(jarvis)


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
    """Revoke a token, and hang up whatever is holding it open.

    The second half is not tidiness. A phone keeps its command socket open for
    days; without closing it, "revoked" would mean "revoked at the next
    reconnect", and a device you have just cut off would keep reading every
    state change and dispatching every service until something unrelated
    dropped the connection.
    """
    from .websocket import close_sockets_for_token

    jarvis = get_jarvis(request)
    auth = get_auth(jarvis)
    if auth is None or not await auth.revoke(token_id):
        raise HTTPException(404, f"unknown token {token_id}")
    closed = close_sockets_for_token(jarvis, token_id)
    return {"id": token_id, "revoked": True, "sockets_closed": closed}
