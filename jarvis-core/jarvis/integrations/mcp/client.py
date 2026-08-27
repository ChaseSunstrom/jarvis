"""A small MCP client: JSON-RPC over HTTP or over a subprocess's stdio.

## Why this is here rather than `pip install mcp`

The official SDK brings `httpx2` (alongside the `httpx` already in this image),
`pyjwt[crypto]`, `jsonschema`, OpenTelemetry and `sse-starlette`. `requirements.txt`
opens with the rule this would break: *"every one of these installs from a wheel
with no compiler, which is why the Dockerfile needs no build toolchain and the
image builds on a Pi as readily as on a desktop."*

Against that, what Jarvis actually needs from MCP is four messages:

    initialize                 -> the server's protocol version and capabilities
    notifications/initialized  -> a notification, no reply
    tools/list                 -> the tools it offers
    tools/call                 -> run one

That is a few hundred lines of JSON-RPC 2.0 and it is testable without a
network. Prompts, resources, sampling, completion and elicitation are NOT
implemented; a server offering them is used for its tools and the rest is
ignored, which is what the spec says an incapable client should do.

## The two transports, and why they are not equivalent

**http** — the spec's Streamable HTTP: every request is a POST to one endpoint,
and the reply is either `application/json` or an SSE stream whose first `message`
event carries the response. Both are handled; the stream is read only until the
response for the request id arrives, because this client makes no use of a
server-initiated stream.

**stdio** — newline-delimited JSON-RPC on a child process's pipes. This means
**Jarvis starts a program**, which is arbitrary code execution as the jarvis-core
user, and is gated in `__init__.py` behind an opt-in that only a file on disk can
set. Nothing here decides that; this class does as it is told.

## What a failure must never do

Take a turn down. Every method raises `MCPError`, which the integration turns
into a tool result the model can read out. A server that hangs is bounded by a
timeout; a server that floods is bounded by a read cap.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "MCPError",
    "MCPClient",
    "HttpTransport",
    "StdioTransport",
    "PROTOCOL_VERSION",
]

#: The revision this client implements. Sent in `initialize`; a server that
#: answers with a different one is used anyway — the four messages here have
#: been stable across revisions, and refusing to talk to a server over a version
#: string would be a worse failure than a field we do not read.
PROTOCOL_VERSION = "2025-06-18"

CLIENT_INFO = {"name": "jarvis-core", "version": "0.1.0"}

DEFAULT_TIMEOUT = 30.0
#: A tool result is text for a model to read. Anything larger is a server
#: misbehaving, and reading it costs the context window of every later turn.
MAX_RESULT_CHARS = 20_000
#: Bytes read from one stdio frame before giving up on the server.
MAX_LINE_BYTES = 4_000_000


class MCPError(RuntimeError):
    """Anything that went wrong talking to a server. Never leaves this module."""


# --- transports -----------------------------------------------------------------

class HttpTransport:
    """Streamable HTTP: one POST per request, JSON or SSE back."""

    def __init__(
        self,
        url: str,
        token: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._own = client is None
        self._http = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        #: Servers that keep state hand back a session id to quote back.
        self.session_id = ""

    def headers(self) -> dict[str, str]:
        out = {
            "Content-Type": "application/json",
            # Both, because a server may answer either and the spec says the
            # client must say it accepts both.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            out["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            out["Mcp-Session-Id"] = self.session_id
        return out

    async def send(self, message: dict[str, Any], *, expect_reply: bool) -> dict[str, Any] | None:
        try:
            response = await self._http.post(
                self.url, json=message, headers=self.headers(), timeout=self.timeout
            )
        except httpx.HTTPError as err:
            raise MCPError(f"could not reach {self.url}: {err}") from err

        session = response.headers.get("mcp-session-id")
        if session:
            self.session_id = session

        if response.status_code >= 400:
            raise MCPError(
                f"{self.url} answered {response.status_code}: "
                f"{response.text[:200].strip() or 'no detail'}"
            )
        if not expect_reply or response.status_code == 202:
            return None

        kind = (response.headers.get("content-type") or "").split(";")[0].strip()
        if kind == "text/event-stream":
            return _first_sse_message(response.text)
        try:
            return response.json()
        except ValueError as err:
            raise MCPError(f"{self.url} did not answer with JSON") from err

    async def aclose(self) -> None:
        if self._own and not self._http.is_closed:
            await self._http.aclose()


def _first_sse_message(body: str) -> dict[str, Any]:
    """The first JSON-RPC message out of an SSE body.

    Only the first: this client sends one request at a time and has no use for
    a server-initiated stream, so reading past the reply would be waiting for
    something nobody is going to send.
    """
    for block in body.split("\n\n"):
        data = "".join(
            line[5:].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        )
        if not data:
            continue
        try:
            parsed = json.loads(data)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise MCPError("the server's event stream carried no JSON-RPC message")


class StdioTransport:
    """Newline-delimited JSON-RPC on a child process's pipes.

    Starting the process is the caller's decision and a serious one — see the
    module docstring. This class only speaks to whatever it was given.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        import os

        environ = dict(os.environ)
        environ.update(self.env)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # Kept separate and drained nowhere: a server that logs to
                # stderr must not have its diagnostics parsed as protocol, and
                # must not fill a pipe nobody reads either.
                stderr=asyncio.subprocess.DEVNULL,
                env=environ,
            )
        except (OSError, ValueError) as err:
            raise MCPError(f"could not start {self.command!r}: {err}") from err

    async def send(self, message: dict[str, Any], *, expect_reply: bool) -> dict[str, Any] | None:
        await self.start()
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise MCPError(f"{self.command!r} is not running")

        # One request at a time. Newline-delimited JSON-RPC has no way to tell
        # two interleaved writes apart, and two coroutines calling tools on the
        # same server is the ordinary case here, not an exotic one.
        async with self._lock:
            line = json.dumps(message).encode() + b"\n"
            try:
                proc.stdin.write(line)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as err:
                raise MCPError(f"{self.command!r} closed its input: {err}") from err
            if not expect_reply:
                return None
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), self.timeout
                )
            except (asyncio.TimeoutError, TimeoutError) as err:
                raise MCPError(f"{self.command!r} did not answer in {self.timeout}s") from err
            except ValueError as err:  # readline's own limit
                raise MCPError(f"{self.command!r} sent an oversized frame") from err

        if not raw:
            raise MCPError(f"{self.command!r} closed its output")
        if len(raw) > MAX_LINE_BYTES:
            raise MCPError(f"{self.command!r} sent an oversized frame")
        try:
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as err:
            raise MCPError(f"{self.command!r} sent something that is not JSON") from err
        if not isinstance(parsed, dict):
            raise MCPError(f"{self.command!r} sent a JSON value that is not a message")
        return parsed

    async def aclose(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 5.0)
        except (asyncio.TimeoutError, TimeoutError):
            # A server that ignores SIGTERM is one this process would otherwise
            # carry to its own grave.
            with_kill = getattr(proc, "kill", None)
            if with_kill:
                with_kill()
        except (ProcessLookupError, OSError):
            pass


# --- the client -----------------------------------------------------------------

class MCPClient:
    """`initialize`, `tools/list`, `tools/call`. Nothing else."""

    def __init__(self, transport: Any, name: str = "") -> None:
        self.transport = transport
        self.name = name or "mcp"
        self._next_id = 1
        self.ready = False
        #: What the server said it is, for the console to show.
        self.server_info: dict[str, Any] = {}
        self.protocol_version = ""

    async def async_initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                # Honest: this client implements no roots, no sampling and no
                # elicitation, and claiming otherwise would have a server wait
                # for a reply that never comes.
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.server_info = result.get("serverInfo") if isinstance(result, dict) else {}
        if not isinstance(self.server_info, dict):
            self.server_info = {}
        self.protocol_version = str((result or {}).get("protocolVersion") or "")
        # A notification, so no id and no reply. Skipping it leaves some
        # servers refusing every later request as "not initialised".
        await self._notify("notifications/initialized")
        self.ready = True
        return result or {}

    async def async_list_tools(self) -> list[dict[str, Any]]:
        """Every tool, following `nextCursor` until the server stops.

        Bounded: a server that returns a cursor pointing at itself would
        otherwise be an infinite loop inside setup.
        """
        tools: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            page = result.get("tools") if isinstance(result, dict) else None
            for tool in page or []:
                if isinstance(tool, dict) and tool.get("name"):
                    tools.append(tool)
            cursor = str((result or {}).get("nextCursor") or "")
            if not cursor:
                break
        return tools

    async def async_call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool and flatten its content to text.

        `isError` is part of the result rather than a JSON-RPC error — a tool
        that failed is a normal reply — so it is read and passed on, because a
        model told "ok" about a tool that failed will build on it.
        """
        result = await self._request(
            "tools/call", {"name": name, "arguments": dict(arguments or {})}
        )
        text = flatten_content(result.get("content") if isinstance(result, dict) else None)
        return {
            "ok": not bool((result or {}).get("isError")),
            "text": text[:MAX_RESULT_CHARS],
            "truncated": len(text) > MAX_RESULT_CHARS,
            "structured": (result or {}).get("structuredContent"),
        }

    async def aclose(self) -> None:
        self.ready = False
        close = getattr(self.transport, "aclose", None)
        if close is not None:
            await close()

    # --- JSON-RPC ---------------------------------------------------------
    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params:
            message["params"] = params
        reply = await self.transport.send(message, expect_reply=True)
        if not isinstance(reply, dict):
            raise MCPError(f"{self.name}: no reply to {method}")
        error = reply.get("error")
        if isinstance(error, dict):
            raise MCPError(
                f"{self.name}: {method} failed "
                f"({error.get('code')}) {str(error.get('message') or '')[:200]}"
            )
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        await self.transport.send(message, expect_reply=False)


MAX_PAGES = 20


def flatten_content(content: Any) -> str:
    """MCP's content blocks -> one string a model can read.

    Text blocks are used as they are. An image or audio block is named rather
    than inlined: this goes into a chat completion as a tool result, base64 in
    that position is thousands of tokens of nothing, and the model cannot see it
    anyway. A resource block is named and linked.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} returned, {block.get('mimeType') or 'unknown type'}]")
        elif kind == "resource":
            resource = block.get("resource")
            if isinstance(resource, dict):
                if resource.get("text"):
                    parts.append(str(resource["text"]))
                else:
                    parts.append(f"[resource {resource.get('uri') or 'with no uri'}]")
        elif kind == "resource_link":
            parts.append(f"[link {block.get('uri') or ''}]".strip())
    return "\n".join(p for p in parts if p).strip()
