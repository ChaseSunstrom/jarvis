"""The websocket API at ``/api/websocket``.

This is the socket every Jarvis client already speaks: the browser HUD, the
Android app and the ESP32 satellites were all written against Home Assistant's
websocket + ``assist_pipeline`` contract, so the framing here is deliberately
identical and is **not** a place to be creative.

    server  {"type": "auth_required", "ha_version": "jarvis-0.1.0"}
    client  {"type": "auth", "access_token": "..."}
    server  {"type": "auth_ok", "ha_version": "jarvis-0.1.0"}     (or auth_invalid)

    client  {"id": 1, "type": "get_states"}
    server  {"id": 1, "type": "result", "success": true, "result": [...]}

    client  {"id": 2, "type": "assist_pipeline/run", "start_stage": "stt", ...}
    server  {"id": 2, "type": "result", "success": true, "result": null}
    server  {"id": 2, "type": "event", "event": {"type": "run-start", "data": {
                "runner_data": {"stt_binary_handler_id": 1, "timeout": 300}}, ...}}
    client  <binary>  0x01 + Int16LE PCM        (audio for that run)
    client  <binary>  0x01                      (lone id byte = end of audio)
    server  {"id": 2, "type": "event", "event": {"type": "run-end", ...}}

Every outbound frame goes through one queue drained by a single writer task,
so results, subscribed events and pipeline events can never interleave
mid-frame — and bus listeners (which run synchronously inside ``bus.fire``)
can hand work to the socket without awaiting anything.

Inbound is the mirror image. The receive loop only reads and routes: binary
audio is handed to its run immediately, and text frames go onto a queue drained
by a single worker task. Commands therefore still execute strictly in the order
they were sent (two ``call_service`` frames can never overtake each other), but
a slow command no longer stops the socket being read — which it used to, and
which starved a concurrent voice run of the audio it was waiting for.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any, Callable

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from ..auth import AuthManager, extract_bearer_token, get_auth
from ..const import MATCH_ALL
from . import common
from .common import ApiError, HA_VERSION

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

TYPE_AUTH_REQUIRED = "auth_required"
TYPE_AUTH = "auth"
TYPE_AUTH_OK = "auth_ok"
TYPE_AUTH_INVALID = "auth_invalid"
TYPE_RESULT = "result"
TYPE_EVENT = "event"
TYPE_PONG = "pong"

ERR_INVALID_FORMAT = "invalid_format"
ERR_UNKNOWN_COMMAND = "unknown_command"
ERR_UNKNOWN_ERROR = "unknown_error"
ERR_NOT_FOUND = "not_found"
ERR_ID_REUSE = "id_reuse"

MAX_BINARY_HANDLERS = 255
AUTH_CLOSE_CODE = 1008

#: How long an accepted-but-unauthenticated socket may stay open. Without this
#: any peer that completes the HTTP upgrade holds a connection and a task for
#: as long as it likes, having proved nothing.
AUTH_TIMEOUT = 10.0

#: On disconnect the command already running is allowed to finish (a half-done
#: `call_service` is worse than a slow shutdown) — but not indefinitely.
DRAIN_TIMEOUT = 10.0

#: Returned by a handler that has already put its reply on the wire.
HANDLED = object()

#: Queued after the last command to stop the worker without cancelling it.
_STOP = object()


def _run_kwargs(msg: dict[str, Any]) -> dict[str, Any]:
    """Pipeline-run options taken off an ``assist_pipeline/run`` message."""
    payload = msg.get("input")
    payload = payload if isinstance(payload, dict) else {}
    kwargs: dict[str, Any] = {}
    if payload.get("sample_rate"):
        kwargs["sample_rate"] = int(payload["sample_rate"])
    if msg.get("timeout") is not None:
        kwargs["timeout"] = float(msg["timeout"])
    return kwargs


class WebSocketHandler:
    """One connected client."""

    def __init__(
        self,
        jarvis: "Jarvis",
        websocket: WebSocket,
        auth: AuthManager | None = None,
    ) -> None:
        self.jarvis = jarvis
        self.ws = websocket
        self.auth = auth if auth is not None else get_auth(jarvis)
        self.user_id: str | None = None

        self._out: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._writer: asyncio.Task | None = None
        self._work: asyncio.Queue[Any] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._subscriptions: dict[Any, Callable[[], None]] = {}
        self._runs: dict[Any, asyncio.Task] = {}
        self._binary_handlers: dict[int, asyncio.Queue] = {}
        self._closed = False

    # --- lifecycle --------------------------------------------------------
    async def run(self) -> None:
        await self.ws.accept()
        try:
            await self._send_now({"type": TYPE_AUTH_REQUIRED, "ha_version": HA_VERSION})
            if not await self._authenticate_with_timeout():
                return
            await self._send_now({"type": TYPE_AUTH_OK, "ha_version": HA_VERSION})
            self._writer = asyncio.create_task(self._writer_loop())
            self._worker = asyncio.create_task(self._worker_loop())
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except RuntimeError as err:  # receive-after-disconnect
            _LOGGER.debug("Websocket ended: %s", err)
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        self._closed = True
        await self._drain_worker()
        for unsub in list(self._subscriptions.values()):
            with contextlib.suppress(Exception):
                unsub()
        self._subscriptions.clear()
        for task in list(self._runs.values()):
            task.cancel()
        if self._runs:
            await asyncio.gather(*list(self._runs.values()), return_exceptions=True)
        self._runs.clear()
        self._binary_handlers.clear()
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
            self._writer = None

    async def _drain_worker(self) -> None:
        """Let the command in flight finish, then stop the worker.

        Cancelling outright would abort a half-executed ``call_service`` just
        because the client's socket went away mid-call — the sequential loop
        this replaced always ran a command to completion, and so does this.
        """
        worker, self._worker = self._worker, None
        if worker is None:
            return
        self._work.put_nowait(_STOP)
        try:
            await asyncio.wait_for(worker, DRAIN_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):  # wait_for already cancelled it
            _LOGGER.warning("Websocket command did not finish within %ss", DRAIN_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the worker swallows its own errors
            _LOGGER.debug("Websocket worker ended badly", exc_info=True)

    # --- transport --------------------------------------------------------
    async def _send_now(self, payload: dict[str, Any]) -> None:
        if WebSocketState.DISCONNECTED in (
            self.ws.client_state,
            self.ws.application_state,
        ):
            self._closed = True
            return
        await self.ws.send_text(json.dumps(payload, default=common.json_default))

    def send(self, payload: dict[str, Any]) -> None:
        """Queue a frame. Safe to call from a synchronous bus listener."""
        if not self._closed:
            self._out.put_nowait(payload)

    async def _writer_loop(self) -> None:
        while True:
            payload = await self._out.get()
            try:
                await self._send_now(payload)
            except (WebSocketDisconnect, RuntimeError):
                self._closed = True
                return
            except Exception:  # pragma: no cover - transport went strange
                _LOGGER.debug("Dropping websocket frame", exc_info=True)
                self._closed = True
                return

    def result(self, msg_id: Any, result: Any = None) -> None:
        self.send({"id": msg_id, "type": TYPE_RESULT, "success": True, "result": result})

    def error(self, msg_id: Any, code: str, message: str) -> None:
        self.send(
            {
                "id": msg_id,
                "type": TYPE_RESULT,
                "success": False,
                "error": {"code": code, "message": message},
            }
        )

    # --- auth -------------------------------------------------------------
    async def _authenticate_with_timeout(self) -> bool:
        """Authenticate, or hang up. An idle peer must not hold the slot."""
        try:
            return await self._authenticate()
        except (asyncio.TimeoutError, TimeoutError):
            _LOGGER.debug("Websocket did not authenticate within %ss", AUTH_TIMEOUT)
            with contextlib.suppress(Exception):
                await self._send_now(
                    {
                        "type": TYPE_AUTH_INVALID,
                        "message": f"no auth message within {AUTH_TIMEOUT:g}s",
                    }
                )
            await self._close(AUTH_CLOSE_CODE)
            return False

    async def _authenticate(self) -> bool:
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                return False
            raw = message.get("text")
            if raw is None:  # binary before auth: ignore, keep waiting
                continue
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                msg = None
            if not isinstance(msg, dict) or msg.get("type") != TYPE_AUTH:
                await self._send_now(
                    {
                        "type": TYPE_AUTH_INVALID,
                        "message": "expected an auth message with an access_token",
                    }
                )
                await self._close(AUTH_CLOSE_CODE)
                return False

            token = msg.get("access_token") or extract_bearer_token(msg.get("authorization"))
            info = self.auth.verify(token) if self.auth is not None else None
            if info is None:
                await self._send_now(
                    {"type": TYPE_AUTH_INVALID, "message": "invalid access token"}
                )
                await self._close(AUTH_CLOSE_CODE)
                return False
            self.user_id = info.id
            return True

    async def _close(self, code: int = 1000) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            await self.ws.close(code=code)

    # --- commands ---------------------------------------------------------
    async def _receive_loop(self) -> None:
        """Read frames and route them. Never executes a command itself.

        Binary audio is delivered straight to its run: it must not queue behind
        a command, or a voice run stalls for as long as the command takes.
        """
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            payload = message.get("bytes")
            if payload is not None:
                self._handle_binary(payload)
                continue
            raw = message.get("text")
            if raw is not None:
                self._work.put_nowait(raw)

    async def _worker_loop(self) -> None:
        """Execute queued text commands, one at a time, in the order sent."""
        while True:
            raw = await self._work.get()
            if raw is _STOP:
                return
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                self.error(None, ERR_INVALID_FORMAT, "message is not valid JSON")
                continue
            if not isinstance(msg, dict):
                self.error(None, ERR_INVALID_FORMAT, "message must be an object")
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            self.error(msg_id, ERR_INVALID_FORMAT, "message needs a 'type'")
            return
        handler = self._HANDLERS.get(msg_type)
        if handler is None:
            self.error(msg_id, ERR_UNKNOWN_COMMAND, f"unknown command {msg_type!r}")
            return
        try:
            result = await handler(self, msg)
        except ApiError as err:
            self.error(msg_id, err.code, err.message)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception("Error handling websocket command %s", msg_type)
            self.error(msg_id, ERR_UNKNOWN_ERROR, str(err) or type(err).__name__)
        else:
            if result is not HANDLED:
                self.result(msg_id, result)

    def _context(self) -> Any:
        return common.api_context(self.user_id)

    # --- binary audio -----------------------------------------------------
    def _handle_binary(self, payload: bytes) -> None:
        if not payload:
            return
        queue = self._binary_handlers.get(payload[0])
        if queue is None:
            _LOGGER.debug("Audio for unknown binary handler %d", payload[0])
            return
        # A lone handler-id byte is the client saying "that's all the audio".
        queue.put_nowait(bytes(payload[1:]) if len(payload) > 1 else None)

    def _reserve_id(self, msg_id: Any) -> None:
        """Guard commands that keep something alive under their message id.

        ``subscribe_events`` and ``assist_pipeline/run`` both file their state
        under ``msg["id"]``. Letting a second one reuse a live id overwrites the
        first's entry — the bus listener is never unsubscribed (it outlives the
        connection, forever), or the run task is never cancelled. Refuse
        instead, the way Home Assistant's ``id_reuse`` does.
        """
        if isinstance(msg_id, bool) or not isinstance(msg_id, (int, str)):
            raise ApiError(ERR_INVALID_FORMAT, "this command needs a unique 'id'")
        if msg_id in self._subscriptions or msg_id in self._runs:
            raise ApiError(ERR_ID_REUSE, f"id {msg_id!r} is already in use on this connection")

    def _claim_handler_id(self) -> int:
        for candidate in range(1, MAX_BINARY_HANDLERS + 1):
            if candidate not in self._binary_handlers:
                return candidate
        raise ApiError("too_many_runs", "no free binary handler id on this connection")

    # --- handlers ---------------------------------------------------------
    async def _cmd_ping(self, msg: dict[str, Any]) -> Any:
        self.send({"id": msg.get("id"), "type": TYPE_PONG})
        return HANDLED

    async def _cmd_get_states(self, msg: dict[str, Any]) -> Any:
        return common.states_payload(self.jarvis)

    async def _cmd_get_config(self, msg: dict[str, Any]) -> Any:
        return common.config_payload(self.jarvis)

    async def _cmd_get_services(self, msg: dict[str, Any]) -> Any:
        return common.services_payload(self.jarvis)

    async def _cmd_subscribe_events(self, msg: dict[str, Any]) -> Any:
        msg_id = msg.get("id")
        self._reserve_id(msg_id)
        event_type = msg.get("event_type") or MATCH_ALL

        def _forward(event: Any) -> None:
            self.send({"id": msg_id, "type": TYPE_EVENT, "event": event.as_dict()})

        self._subscriptions[msg_id] = self.jarvis.bus.listen(event_type, _forward)
        return None

    async def _cmd_unsubscribe_events(self, msg: dict[str, Any]) -> Any:
        subscription = msg.get("subscription")
        unsub = self._subscriptions.pop(subscription, None)
        if unsub is None:
            raise ApiError(ERR_NOT_FOUND, f"no subscription {subscription!r}")
        unsub()
        return None

    async def _cmd_fire_event(self, msg: dict[str, Any]) -> Any:
        event_type = msg.get("event_type")
        if not event_type:
            raise ApiError(ERR_INVALID_FORMAT, "fire_event needs an 'event_type'")
        data = msg.get("event_data")
        context = self._context()
        self.jarvis.bus.fire(
            str(event_type), data if isinstance(data, dict) else {}, context
        )
        return {"context": context.as_dict()}

    async def _cmd_call_service(self, msg: dict[str, Any]) -> Any:
        domain = msg.get("domain")
        service = msg.get("service")
        if not domain or not service:
            raise ApiError(ERR_INVALID_FORMAT, "call_service needs 'domain' and 'service'")
        return_response = bool(msg.get("return_response"))
        outcome = await common.async_call_service(
            self.jarvis,
            str(domain),
            str(service),
            msg.get("service_data"),
            msg.get("target"),
            context=self._context(),
            return_response=return_response,
        )
        result: dict[str, Any] = {"context": outcome.context.as_dict()}
        if return_response:
            result["response"] = outcome.response
        result["changed_states"] = outcome.changed_states
        return result

    async def _cmd_conversation_process(self, msg: dict[str, Any]) -> Any:
        return await common.async_conversation_process(
            self.jarvis,
            str(msg.get("text") or ""),
            msg.get("conversation_id"),
            msg.get("language"),
            msg.get("agent_id"),
            context=self._context(),
        )

    async def _cmd_approve(self, msg: dict[str, Any]) -> Any:
        return await common.async_approve(
            self.jarvis,
            str(msg.get("request_id") or ""),
            # Raw, not bool(): common.approval_flag fails closed on "false".
            msg.get("approved"),
            context=self._context(),
        )

    # registries
    async def _cmd_entity_list(self, msg: dict[str, Any]) -> Any:
        return common.entity_registry_payload(self.jarvis)

    async def _cmd_entity_update(self, msg: dict[str, Any]) -> Any:
        return await common.async_update_entity(self.jarvis, msg)

    async def _cmd_device_list(self, msg: dict[str, Any]) -> Any:
        return common.device_registry_payload(self.jarvis)

    async def _cmd_device_update(self, msg: dict[str, Any]) -> Any:
        return await common.async_update_device(self.jarvis, msg)

    async def _cmd_area_list(self, msg: dict[str, Any]) -> Any:
        return common.area_registry_payload(self.jarvis)

    async def _cmd_area_create(self, msg: dict[str, Any]) -> Any:
        return await common.async_create_area(self.jarvis, msg)

    async def _cmd_area_update(self, msg: dict[str, Any]) -> Any:
        return await common.async_update_area(self.jarvis, msg)

    async def _cmd_area_delete(self, msg: dict[str, Any]) -> Any:
        return await common.async_delete_area(self.jarvis, msg)

    # voice
    async def _cmd_pipeline_list(self, msg: dict[str, Any]) -> Any:
        return common.pipeline_list_payload(self.jarvis)

    async def _cmd_pipeline_run(self, msg: dict[str, Any]) -> Any:
        msg_id = msg.get("id")
        self._reserve_id(msg_id)
        start_stage = str(msg.get("start_stage") or "stt")
        end_stage = str(msg.get("end_stage") or "tts")
        payload = msg.get("input")
        payload = payload if isinstance(payload, dict) else {}
        text = payload.get("text")

        handler_id = self._claim_handler_id()
        try:
            run = create_pipeline_run(
                self.jarvis,
                pipeline=msg.get("pipeline"),
                start_stage=start_stage,
                end_stage=end_stage,
                conversation_id=msg.get("conversation_id"),
                binary_handler_id=handler_id,
                **_run_kwargs(msg),
            )
        except ApiError:
            raise
        except Exception as err:
            raise ApiError("pipeline_error", str(err) or type(err).__name__) from err

        queue: asyncio.Queue = asyncio.Queue()
        self._binary_handlers[handler_id] = queue
        # HA confirms the subscription first, then streams the run's events.
        self.result(msg_id, None)
        self._runs[msg_id] = asyncio.create_task(
            self._drive_run(msg_id, run, queue, handler_id, text)
        )
        return HANDLED

    async def _drive_run(
        self,
        msg_id: Any,
        run: Any,
        queue: asyncio.Queue,
        handler_id: int,
        text: str | None,
    ) -> None:
        def _event_cb(event_type: str, data: dict[str, Any]) -> None:
            self.send(
                {
                    "id": msg_id,
                    "type": TYPE_EVENT,
                    "event": {
                        "type": event_type,
                        "data": data,
                        "timestamp": common.now_iso(),
                    },
                }
            )

        try:
            if text:
                await run.execute(None, _event_cb, text=text)
            else:
                await run.execute(queue, _event_cb)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Voice pipeline run failed")
            _event_cb("error", {"code": "unknown", "message": "pipeline run failed"})
            _event_cb("run-end", {})
        finally:
            self._binary_handlers.pop(handler_id, None)
            self._runs.pop(msg_id, None)

    _HANDLERS: dict[str, Any] = {}


WebSocketHandler._HANDLERS = {
    "ping": WebSocketHandler._cmd_ping,
    "get_states": WebSocketHandler._cmd_get_states,
    "get_config": WebSocketHandler._cmd_get_config,
    "get_services": WebSocketHandler._cmd_get_services,
    "subscribe_events": WebSocketHandler._cmd_subscribe_events,
    "unsubscribe_events": WebSocketHandler._cmd_unsubscribe_events,
    "fire_event": WebSocketHandler._cmd_fire_event,
    "call_service": WebSocketHandler._cmd_call_service,
    "conversation/process": WebSocketHandler._cmd_conversation_process,
    "jarvis/approve": WebSocketHandler._cmd_approve,
    "config/entity_registry/list": WebSocketHandler._cmd_entity_list,
    "config/entity_registry/update": WebSocketHandler._cmd_entity_update,
    "config/device_registry/list": WebSocketHandler._cmd_device_list,
    "config/device_registry/update": WebSocketHandler._cmd_device_update,
    "config/area_registry/list": WebSocketHandler._cmd_area_list,
    "config/area_registry/create": WebSocketHandler._cmd_area_create,
    "config/area_registry/update": WebSocketHandler._cmd_area_update,
    "config/area_registry/delete": WebSocketHandler._cmd_area_delete,
    "assist_pipeline/pipeline/list": WebSocketHandler._cmd_pipeline_list,
    "assist_pipeline/run": WebSocketHandler._cmd_pipeline_run,
}


def create_pipeline_run(jarvis: "Jarvis", pipeline: Any = None, **kwargs: Any) -> Any:
    """Build a pipeline run, preferring whatever the voice integration set up.

    ``jarvis.data["voice"]`` is asked first (that is also how tests inject a
    fake), and only if nothing is there do we reach for the real runner — which
    is imported here rather than at module scope so the API layer still works
    in a build without the voice stack.
    """
    factory = getattr(jarvis.data.get(common.DATA_VOICE), "async_create_run", None)
    if callable(factory):
        return factory(pipeline, **kwargs)

    try:
        from ..voice.pipeline import PipelineRun
    except ImportError as err:  # pragma: no cover - voice stack absent
        raise ApiError(
            "pipeline_unavailable", f"the voice stack is not installed: {err}"
        ) from err
    return PipelineRun(jarvis, **kwargs)


async def websocket_endpoint(websocket: WebSocket) -> None:
    """ASGI entry point for ``/api/websocket``."""
    jarvis = websocket.scope["app"].state.jarvis
    await WebSocketHandler(jarvis, websocket).run()
