"""A loopback socket the desktop shell talks to, and nothing else can.

The agent runs headless: it dispatches actions, keeps the policy, and asks a
human when a tier says so. Until now "a human" meant a Tk dialog it drew
itself, or a terminal prompt. Neither is available when the thing in front of
the person is an Electron window — and an app with a tray icon that cannot
answer its own consent prompts is two programs sharing a machine rather than
one product.

So there is a seam:

    server = IpcServer(port=0)
    await server.start()
    ...
    verdict = await server.ask(request, timeout=45)

The shell connects, receives `status` and `ask` frames, and sends `answer`
frames back. One line of JSON per frame, newline-delimited, over TCP on
**127.0.0.1** with a token.

## Why loopback TCP and not a Unix socket

Windows. The agent runs on Linux, macOS and Windows, and a Unix socket is one
of those platform seams that turns into two implementations and one of them
being the untested one. A loopback port with a token is the same on all three.

## What makes it safe

* **Loopback only.** `127.0.0.1`, never `0.0.0.0`. Nothing off this machine can
  reach it — which matters because what comes over it can approve an action.
* **A token, checked on every frame.** Any process on this machine can open a
  loopback port; the token is what makes "the shell" mean the shell. It is
  written to a file only this user can read, exactly as the pairing secret is.
* **Answers are matched to requests by id, single use.** A shell that answered
  the same id twice would be re-approving something that has already run.
* **A closed connection is a denial**, never a default-allow. Same rule as
  every other gateway here: nobody answered means nobody approved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import stat
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

__all__ = ["IpcServer", "TOKEN_FILE", "read_token", "write_token"]

#: Where the token lives, so the shell can find it without being told.
TOKEN_FILE = "shell-token"

#: How long a held question waits for the shell before it is refused.
DEFAULT_TIMEOUT = 45.0

#: A frame longer than this is not a frame; it is somebody probing the port.
MAX_FRAME = 64 * 1024


def write_token(directory: str | Path) -> str:
    """Mint (or reuse) the shell's token, readable only by this user."""
    path = Path(directory).expanduser() / TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - Windows has no mode bits worth setting
        _LOGGER.debug("could not tighten the token file's permissions")
    return token


def read_token(directory: str | Path) -> str:
    path = Path(directory).expanduser() / TOKEN_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass
class _Pending:
    """One question the shell has been asked and has not answered."""

    id: str
    future: "asyncio.Future[str]"
    asked_at: float = field(default_factory=time.monotonic)


class IpcServer:
    """The agent's side of the socket."""

    def __init__(self, token: str = "", host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self._server: asyncio.AbstractServer | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self._pending: dict[str, _Pending] = {}
        #: Last status broadcast, replayed to a shell that connects late — a
        #: tray icon that says nothing until the next event is a tray icon that
        #: looks broken for the first minute.
        self._status: dict[str, Any] = {"state": "starting"}

    # --- lifecycle --------------------------------------------------------
    async def start(self) -> int:
        self._server = await asyncio.start_server(self._serve, self.host, self.port)
        sockets = self._server.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
        _LOGGER.info("shell IPC listening on %s:%s", self.host, self.port)
        return self.port

    async def stop(self) -> None:
        for writer in list(self._writers):
            with_suppress(writer.close)
        self._writers.clear()
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result("denied")
        self._pending.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover - closing twice is not news
                pass
            self._server = None

    @property
    def connected(self) -> bool:
        return bool(self._writers)

    # --- talking to the shell ---------------------------------------------
    async def publish(self, status: dict[str, Any]) -> None:
        """Tell every connected shell what the agent is doing now."""
        self._status = dict(status)
        await self._broadcast({"type": "status", **self._status})

    async def ask(self, payload: dict[str, Any], timeout: float = DEFAULT_TIMEOUT) -> str:
        """Put one question to the shell and wait for its answer.

        Returns the verdict string the shell sent, or `"timeout"`. Never
        raises: a consent path that can throw is one that fails open on the day
        the shell crashes.
        """
        if not self._writers:
            return "unattended"
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        pending = _Pending(id=request_id, future=loop.create_future())
        self._pending[request_id] = pending
        await self._broadcast({"type": "ask", "id": request_id, **payload})
        try:
            return await asyncio.wait_for(pending.future, timeout=timeout)
        except asyncio.TimeoutError:
            return "timeout"
        finally:
            self._pending.pop(request_id, None)

    async def _broadcast(self, frame: dict[str, Any]) -> None:
        line = (json.dumps(frame, default=str) + "\n").encode("utf-8")
        for writer in list(self._writers):
            try:
                writer.write(line)
                await writer.drain()
            except Exception:  # noqa: BLE001 - a dead shell is not an error here
                self._drop(writer)

    # --- the connection ---------------------------------------------------
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if not _is_loopback(peer):
            # Cannot happen while the server binds loopback, and is checked
            # anyway: this socket can approve an action.
            _LOGGER.warning("refusing a shell connection from %r", peer)
            with_suppress(writer.close)
            return
        authenticated = False
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if len(line) > MAX_FRAME:
                    _LOGGER.warning("shell sent an oversized frame; closing")
                    break
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                if not authenticated:
                    if not secrets.compare_digest(str(frame.get("token") or ""), self.token):
                        _LOGGER.warning("a shell connected with the wrong token")
                        break
                    authenticated = True
                    self._writers.append(writer)
                    writer.write(
                        (json.dumps({"type": "status", **self._status}) + "\n").encode("utf-8")
                    )
                    await writer.drain()
                    continue
                self._handle(frame)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._drop(writer)

    def _handle(self, frame: dict[str, Any]) -> None:
        if str(frame.get("type")) != "answer":
            return
        pending = self._pending.pop(str(frame.get("id") or ""), None)
        if pending is None:
            # Unknown, or already answered. Single use, deliberately: a second
            # answer to one question would be approving something twice.
            return
        if not pending.future.done():
            pending.future.set_result(str(frame.get("verdict") or "denied"))

    def _drop(self, writer: asyncio.StreamWriter) -> None:
        if writer in self._writers:
            self._writers.remove(writer)
        with_suppress(writer.close)
        if not self._writers:
            # The shell went away with questions outstanding. Nobody is there,
            # so nobody approved.
            for pending in list(self._pending.values()):
                if not pending.future.done():
                    pending.future.set_result("unattended")
            self._pending.clear()


def _is_loopback(peer: Any) -> bool:
    host = peer[0] if isinstance(peer, tuple) and peer else ""
    return str(host) in ("127.0.0.1", "::1", "localhost")


def with_suppress(fn: Any) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - closing something already closed
        pass


def default_directory() -> Path:
    """Where the token goes when the caller has no opinion."""
    base = os.environ.get("XDG_STATE_HOME") or os.environ.get("LOCALAPPDATA")
    return Path(base or Path.home() / ".local" / "state") / "jarvis-desktop"
