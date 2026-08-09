"""The command channel: this agent's half of the jarvis-core device protocol.

::

    ->  {"type": "auth", "access_token": "..."}
    <-  {"type": "auth_ok"}
    ->  {"id": 1, "type": "jarvis/device/register",
         "device": {"id", "name", "platform": "desktop", "capabilities": [...],
                    "app_version": "..."}}
    <-  {"id": 1, "type": "result", "success": true, "result": {"ok": true}}

    <-  {"type": "device_command", "command_id": "c-123", "action": "...",
         "params": {...}, "tier": 1|2|3, "reason": "..."}
    ->  {"type": "device_result", "command_id": "c-123",
         "status": "ok"|"denied"|"error"|"unsupported", "result": {...}, "error": "..."}

    ->  {"type": "device_event", "event": "...", "data": {...}}

Parsing rule for everything inbound: **read the fields we know, ignore the rest,
and never let an unknown field change behaviour.** A server that adds
``"skip_confirmation": true`` or ``"policy": "allow"`` to a ``device_command`` is
describing a field this parser does not have and will not grow.

The transport is a seam (:class:`Transport`) so the whole protocol layer can be
exercised against a fake socket with no network at all — which is how
``tests/test_channel.py`` runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

from .actions.registry import ActionRegistry
from .config import Config
from .policy import ActionTier
from .ratelimit import Admission, Backoff, CommandGate, TokenBucket

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "Transport",
    "TransportClosed",
    "WebsocketTransport",
    "DeviceChannel",
    "PLATFORM",
    "handshake_host",
    "host_of_authority",
]

PLATFORM = "desktop"

TYPE_AUTH_REQUIRED = "auth_required"
TYPE_AUTH = "auth"
TYPE_AUTH_OK = "auth_ok"
TYPE_AUTH_INVALID = "auth_invalid"
TYPE_RESULT = "result"
TYPE_REGISTER = "jarvis/device/register"
TYPE_DEVICE_COMMAND = "device_command"
TYPE_DEVICE_RESULT = "device_result"
TYPE_DEVICE_EVENT = "device_event"
TYPE_PING = "ping"
TYPE_PONG = "pong"

STATUS_OK = "ok"
STATUS_DENIED = "denied"
STATUS_ERROR = "error"
STATUS_UNSUPPORTED = "unsupported"
VALID_STATUSES = (STATUS_OK, STATUS_DENIED, STATUS_ERROR, STATUS_UNSUPPORTED)

#: Longest inbound frame we will even parse. A command is a few hundred bytes;
#: a megabyte of JSON is not a command.
MAX_FRAME_BYTES = 512 * 1024


class TransportClosed(Exception):
    """The socket went away. The caller reconnects with backoff."""


def host_of_authority(value: str) -> str:
    """Hostname out of a ``Host`` header or an ``authority``, lowercased.

    ``jarvis.lan:8080`` -> ``jarvis.lan``; ``[::1]:8080`` -> ``::1``. Pure
    string work, so the redirect check below is testable without a socket.
    """
    text = value.strip()
    if text.startswith("["):
        end = text.find("]")
        return text[1:end].lower() if end > 0 else text.lower()
    head, sep, tail = text.rpartition(":")
    if sep and head and tail.isdigit():
        return head.lower()
    return text.lower()


def handshake_host(connection: Any) -> str | None:
    """The host the WebSocket handshake actually reached, or None if unknown.

    Read from the ``Host`` header of the request that was sent, which the client
    library rewrites when it follows a redirect. Returns None rather than
    guessing if the library ever stops exposing it — the caller then falls back
    to the pre-connect host check alone and logs nothing false.
    """
    headers = None
    request = getattr(connection, "request", None)
    if request is not None:
        headers = getattr(request, "headers", None)
    if headers is None:
        headers = getattr(connection, "request_headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Host")
    except Exception:  # noqa: BLE001 - a Headers-like object we do not know
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return host_of_authority(value)


class Transport(ABC):
    """Whatever moves text frames. One implementation, one fake."""

    @abstractmethod
    async def send(self, message: str) -> None: ...

    @abstractmethod
    async def recv(self) -> str:
        """Next inbound frame, or raise :class:`TransportClosed`."""

    @abstractmethod
    async def close(self) -> None: ...


class WebsocketTransport(Transport):
    """The real one, over the ``websockets`` library."""

    def __init__(self, connection: Any) -> None:
        self._ws = connection

    async def send(self, message: str) -> None:
        try:
            await self._ws.send(message)
        except Exception as exc:  # noqa: BLE001 - library-specific close errors
            raise TransportClosed(str(exc)) from exc

    async def recv(self) -> str:
        try:
            frame = await self._ws.recv()
        except Exception as exc:  # noqa: BLE001
            raise TransportClosed(str(exc)) from exc
        if isinstance(frame, bytes):
            return frame.decode("utf-8", errors="replace")
        return str(frame)

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    async def connect(url: str, open_timeout: float = 15.0) -> "WebsocketTransport":
        import websockets

        connection = await websockets.connect(
            url,
            open_timeout=open_timeout,
            max_size=MAX_FRAME_BYTES,
            ping_interval=20,
            ping_timeout=20,
        )
        # The `websockets` client follows HTTP 3xx redirects during the
        # handshake, cross-origin ones included, so "we connected" is not the
        # same as "we connected to the host we asked for". Nothing has been sent
        # yet — the token goes out later, in reply to auth_required — so this is
        # the last moment where a redirect can be caught for free.
        expected = (urlparse(url).hostname or "").lower()
        actual = handshake_host(connection)
        if expected and actual and actual != expected:
            try:
                await connection.close()
            except Exception:  # noqa: BLE001
                pass
            raise TransportClosed(
                f"refusing this session: the handshake ended at {actual!r}, not "
                f"{expected!r} — the server redirected us somewhere else"
            )
        return WebsocketTransport(connection)


class DeviceChannel:
    """Speaks the device protocol against one :class:`Transport` at a time."""

    def __init__(
        self,
        config: Config,
        registry: ActionRegistry,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.config = config
        self.registry = registry
        self._clock = clock
        self._rng = rng

        self._command_bucket = TokenBucket(
            config.command_rate_capacity, config.command_rate_per_second, clock()
        )
        self._event_bucket = TokenBucket(
            config.event_rate_capacity, config.event_rate_per_second, clock()
        )
        self._gate = CommandGate(max_concurrent=config.max_concurrent_commands)
        self._backoff = Backoff()

        self._transport: Transport | None = None
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._next_id = 1
        self.registered = False
        #: Set once a session has authenticated and registered; the trigger
        #: layer waits on it before emitting anything.
        self.ready = asyncio.Event()
        self._stopping = False

    # --- transport plumbing -------------------------------------------------

    async def _send(self, frame: Mapping[str, Any]) -> None:
        transport = self._transport
        if transport is None:
            raise TransportClosed("not connected")
        payload = json.dumps(frame, default=str)
        async with self._send_lock:
            await transport.send(payload)

    def _take_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    # --- session ------------------------------------------------------------

    async def run_session(self, transport: Transport) -> None:
        """Authenticate, register, then pump frames until the socket dies."""
        self._transport = transport
        self.registered = False
        self.ready.clear()
        self._gate.clear_in_flight()
        self._command_bucket.reset(self._clock())
        try:
            await self._handshake()
            await self._read_loop()
        finally:
            self.registered = False
            self.ready.clear()
            await self._cancel_tasks()
            self._transport = None

    async def _handshake(self) -> None:
        transport = self._transport
        assert transport is not None

        # jarvis-core greets with auth_required. The token is sent in reply to
        # that and to nothing else: a server that has not asked has no business
        # receiving it, and volunteering a credential to whatever answered the
        # socket is how tokens leak.
        first = await self._recv_json(transport)
        if first.get("type") == TYPE_AUTH_REQUIRED:
            await self._send({"type": TYPE_AUTH, "access_token": self.config.token})
            first = await self._recv_json(transport)

        if first.get("type") == TYPE_AUTH_INVALID:
            # Retrying a rejected token quickly accomplishes nothing except
            # hammering the server's auth path.
            self._backoff.penalise()
            raise TransportClosed(f"authentication rejected: {first.get('message', '')}")
        if first.get("type") != TYPE_AUTH_OK:
            raise TransportClosed(f"unexpected handshake frame: {first.get('type')!r}")

        register_id = self._take_id()
        await self._send(
            {
                "id": register_id,
                "type": TYPE_REGISTER,
                "device": {
                    "id": self.config.device_id,
                    "name": self.config.device_name,
                    "platform": PLATFORM,
                    "capabilities": self.registry.capabilities(),
                    "app_version": self.config.app_version,
                    # Additive: a server that does not know about `actions`
                    # ignores an extra key, whereas a second message type would
                    # come back as unknown_command.
                    "actions": self.registry.manifest(),
                },
            }
        )

        # The register result may be preceded by unrelated pushes.
        deadline = self._clock() + 30.0
        while self._clock() < deadline:
            frame = await self._recv_json(transport)
            if frame.get("type") == TYPE_RESULT and frame.get("id") == register_id:
                if not frame.get("success", False):
                    error = frame.get("error") or {}
                    raise TransportClosed(f"registration refused: {error}")
                self.registered = True
                self.ready.set()
                self._backoff.reset()
                _LOGGER.info(
                    "registered with jarvis-core as %s (%d actions, capabilities: %s)",
                    self.config.device_id,
                    len(self.registry),
                    ", ".join(self.registry.capabilities()),
                )
                return
            await self._handle_frame(frame)
        raise TransportClosed("server never answered the registration")

    async def _read_loop(self) -> None:
        transport = self._transport
        assert transport is not None
        while not self._stopping:
            frame = await self._recv_json(transport)
            await self._handle_frame(frame)

    async def _recv_json(self, transport: Transport) -> dict[str, Any]:
        raw = await transport.recv()
        if len(raw) > MAX_FRAME_BYTES:
            raise TransportClosed(f"inbound frame of {len(raw)} bytes is over the limit")
        try:
            frame = json.loads(raw)
        except ValueError:
            _LOGGER.warning("ignoring an unparseable frame (%d bytes)", len(raw))
            return {}
        if not isinstance(frame, dict):
            _LOGGER.warning("ignoring a non-object frame")
            return {}
        return frame

    # --- inbound frames -----------------------------------------------------

    async def _handle_frame(self, frame: Mapping[str, Any]) -> None:
        kind = frame.get("type")
        if kind == TYPE_DEVICE_COMMAND:
            await self.on_device_command(frame)
        elif kind == TYPE_PING:
            await self._send({"id": frame.get("id"), "type": TYPE_PONG})
        elif kind in (TYPE_RESULT, TYPE_PONG, "event", None, ""):
            return
        else:
            _LOGGER.debug("ignoring frame type %r", kind)

    async def on_device_command(self, frame: Mapping[str, Any]) -> None:
        """Admit, rate-limit, then run one command in its own task.

        The command runs off the read loop because a Tier-3 prompt can sit on
        screen for a minute, and a blocked read loop is a channel that cannot
        even receive a cancellation.
        """
        command_id = str(frame.get("command_id") or "")
        action_id = str(frame.get("action") or "")

        admission = self._gate.admit(command_id, action_id)
        if admission.kind == Admission.MALFORMED:
            _LOGGER.warning("dropping a malformed device_command: %s", admission.detail)
            if command_id:
                await self._reply(command_id, STATUS_ERROR, admission.detail)
            return
        if admission.kind == Admission.ALREADY_ANSWERED:
            # A redelivery. Replay the stored answer; execute nothing.
            _LOGGER.info("replaying the stored reply for %s", command_id)
            if admission.reply:
                await self._send(admission.reply)
            return
        if admission.kind == Admission.STILL_RUNNING:
            return  # it will answer when it finishes
        if admission.kind == Admission.ACTION_BUSY:
            await self._reply(
                command_id,
                STATUS_ERROR,
                f"another {admission.detail} is already running on this machine",
            )
            return
        if admission.kind == Admission.AT_CAPACITY:
            await self._reply(
                command_id,
                STATUS_ERROR,
                f"this machine is already running {admission.detail} actions; try again shortly",
            )
            return

        if not self._command_bucket.try_acquire(self._clock()):
            wait = self._command_bucket.wait_s(self._clock())
            _LOGGER.warning(
                "rate limit: refusing %s (%s); %.1fs until the next token",
                action_id,
                command_id,
                wait,
            )
            # Answered, never silently dropped — a silent drop leaves the server
            # waiting forever.
            reply = self._result_frame(
                command_id,
                STATUS_ERROR,
                f"rate limit: this machine is refusing commands for another {wait:.1f}s",
            )
            self._gate.complete(command_id, reply)
            await self._send(reply)
            return

        task = asyncio.create_task(self._run_command(frame, command_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_command(self, frame: Mapping[str, Any], command_id: str) -> None:
        try:
            # ActionRegistry.handle_command re-parses `tier` itself and folds it
            # in with max(); this layer never lowers anything and never passes a
            # policy hint of its own.
            reply = await self.registry.handle_command(frame)
            reply = self._sanitise_reply(command_id, reply)
            self._gate.complete(command_id, reply)
            await self._send(reply)
        except asyncio.CancelledError:
            # Shutdown mid-command: forget it so a redelivery after reconnect is
            # allowed to run, and say nothing (the server got no answer).
            self._gate.abandon(command_id)
            raise
        except TransportClosed:
            self._gate.abandon(command_id)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("command %s blew up", command_id, exc_info=True)
            reply = self._result_frame(command_id, STATUS_ERROR, f"{type(exc).__name__}: {exc}")
            self._gate.complete(command_id, reply)
            try:
                await self._send(reply)
            except TransportClosed:
                pass

    @staticmethod
    def _sanitise_reply(command_id: str, reply: Mapping[str, Any]) -> dict[str, Any]:
        """The executor does not get to invent wire vocabulary.

        An unrecognised status becomes ``error``: a garbled answer must never
        read as success.
        """
        status = str(reply.get("status", "")).lower()
        out: dict[str, Any] = {
            "type": TYPE_DEVICE_RESULT,
            "command_id": command_id,
            "status": status if status in VALID_STATUSES else STATUS_ERROR,
        }
        if isinstance(reply.get("result"), dict):
            out["result"] = reply["result"]
        error = reply.get("error")
        if error:
            out["error"] = str(error)
        elif status not in VALID_STATUSES:
            out["error"] = (
                "the device produced a result with no recognised status; "
                "treating it as an error"
            )
        return out

    @staticmethod
    def _result_frame(command_id: str, status: str, error: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": TYPE_DEVICE_RESULT,
            "command_id": command_id,
            "status": status if status in VALID_STATUSES else STATUS_ERROR,
        }
        if error:
            out["error"] = error
        return out

    async def _reply(self, command_id: str, status: str, error: str | None = None) -> None:
        frame = self._result_frame(command_id, status, error)
        self._gate.complete(command_id, frame)
        await self._send(frame)

    # --- outbound events ----------------------------------------------------

    async def emit_event(
        self, event: str, data: Mapping[str, Any] | None = None, untrusted: bool = False
    ) -> bool:
        """Push a ``device_event``. False when it was dropped by the rate limit.

        ``untrusted`` rides along for events whose payload came from somewhere
        this machine does not vouch for (a watched file's contents, a window
        title). Additive and ignorable, but worth sending: the server should
        know which of its inputs a stranger wrote before it feeds them to a
        model.
        """
        if not self.registered:
            return False
        if not self._event_bucket.try_acquire(self._clock()):
            _LOGGER.debug("rate limit: dropping device_event %s", event)
            return False
        frame: dict[str, Any] = {
            "type": TYPE_DEVICE_EVENT,
            "event": event,
            "data": dict(data or {}),
        }
        if untrusted:
            frame["trust"] = "untrusted"
        try:
            await self._send(frame)
        except TransportClosed:
            return False
        return True

    # --- effective tier, at the channel layer -------------------------------

    def effective_tier(self, action_id: str, incoming: object) -> ActionTier:
        """``max(local, incoming)`` — the same rule the registry enforces.

        Duplicated here on purpose: this layer decides what to *log* and what
        the reconnect logic sees, and the two gates can fail independently. An
        action the local table has never heard of is CONFIRM — not "unknown",
        not "ask the server" — so a typo or an injected action name cannot land
        in the auto-run bucket.

        There is deliberately no function here that lowers a tier, reads a
        "policy" field off the wire, or accepts an override flag.
        """
        action = self.registry.get(action_id)
        local = action.tier if action is not None else ActionTier.CONFIRM
        requested = ActionTier.from_wire(incoming)
        return ActionTier.max_of(local, requested or ActionTier.AUTO)

    # --- connect loop -------------------------------------------------------

    def check_host(self, url: str) -> str | None:
        """Refuse a URL that does not match the pin. Returns a reason or None.

        Host pinning means a rewritten config file or a redirect cannot quietly
        move the agent onto someone else's server while it still holds a valid
        token.
        """
        pinned = (self.config.pinned_host or "").strip().lower()
        if not pinned:
            return None
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return "malformed server url"
        if host != pinned:
            return f"server host {host!r} does not match the pinned host {pinned!r}"
        return None

    async def run_forever(
        self,
        connect: Callable[[str], Awaitable[Transport]] | None = None,
        max_sessions: int | None = None,
    ) -> None:
        """Connect, run a session, reconnect with backoff, forever.

        ``max_sessions`` bounds the loop for tests; production passes None.
        """
        connector = connect or (lambda url: WebsocketTransport.connect(url))
        sessions = 0
        while not self._stopping:
            if max_sessions is not None and sessions >= max_sessions:
                return
            sessions += 1
            url = self.config.server_url
            refusal = self.check_host(url)
            if refusal:
                _LOGGER.error("refusing to connect: %s", refusal)
                self._backoff.penalise()
            else:
                try:
                    transport = await connector(url)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("could not connect to %s: %s", url, exc)
                else:
                    try:
                        await self.run_session(transport)
                    except TransportClosed as exc:
                        _LOGGER.info("session ended: %s", exc)
                    except asyncio.CancelledError:
                        await transport.close()
                        raise
                    except Exception:  # noqa: BLE001
                        _LOGGER.warning("session failed", exc_info=True)
                    finally:
                        await transport.close()

            if self._stopping:
                return
            delay = self._backoff.next(self._rng())
            _LOGGER.info("reconnecting in %.1fs", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    async def stop(self) -> None:
        self._stopping = True
        self.ready.clear()
        await self._cancel_tasks()
        if self._transport is not None:
            await self._transport.close()

    async def _cancel_tasks(self) -> None:
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
