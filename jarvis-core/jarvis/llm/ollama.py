"""Async client for a local Ollama server (``/api/chat`` + ``/api/tags``).

Ollama streams NDJSON: one JSON object per line, each carrying a slice of
``message.content``, with the last one flagged ``done``. Tool calls arrive as
``message.tool_calls[].function.{name, arguments}`` — usually all at once, on
the final chunk, and ``arguments`` is sometimes a JSON *string* rather than an
object depending on the model. Both shapes are handled here.

Usage::

    client = OllamaClient("http://127.0.0.1:11434")

    stream = client.chat("qwen3:8b", messages, tools=schema)
    async for delta in stream:          # streamed text deltas
        print(delta, end="")
    result = stream.result              # ChatResult: content + tool_calls

    result = await client.chat("qwen3:8b", messages, stream=False)

Tests inject an ``httpx.MockTransport`` (or a whole ``httpx.AsyncClient``) so
none of this needs a running Ollama.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 120.0

CHAT_PATH = "/api/chat"
TAGS_PATH = "/api/tags"
GENERATE_PATH = "/api/generate"


class OllamaError(RuntimeError):
    """The model server returned an error, or spoke something that wasn't JSON.

    Carries the HTTP status when there was one, so a caller can tell apart the
    three failures that look identical in the message: a 401 that will fail the
    same way forever, a 429 that is asking to be retried later, and a 502 from
    an upstream model that is merely flapping. `retry_after` is the server's own
    `Retry-After`, in seconds, or 0 when it did not say.

    Both default to 0, which reads as "no HTTP status" — a connection that never
    got that far — and keeps every existing `raise OllamaError(...)` valid.
    """

    #: HTTP status, or 0 for a transport-level failure.
    status: int = 0
    #: Seconds the server asked us to wait, or 0.0.
    retry_after: float = 0.0


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ToolCall:
    """One function call the model wants us to make."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}

    def as_message_part(self) -> dict[str, Any]:
        """The shape Ollama expects back inside an assistant message."""
        part: dict[str, Any] = {
            "function": {"name": self.name, "arguments": dict(self.arguments)}
        }
        if self.id:
            part["id"] = self.id
        return part


@dataclass(slots=True)
class ChatResult:
    """Everything one ``/api/chat`` exchange produced."""

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    done_reason: str = ""
    role: str = "assistant"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def as_assistant_message(self) -> dict[str, Any]:
        """Replay this turn back to the model when continuing a tool loop."""
        message: dict[str, Any] = {"role": self.role or "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [c.as_message_part() for c in self.tool_calls]
        return message

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "thinking": self.thinking,
            "tool_calls": [c.as_dict() for c in self.tool_calls],
            "model": self.model,
            "done_reason": self.done_reason,
        }


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Coerce whatever a model put in ``arguments`` into a dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"input": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"input": parsed}
    if raw is None:
        return {}
    return {"input": raw}


def parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """Pull ``message.tool_calls`` out in Ollama's (OpenAI-ish) format."""
    out: list[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") if isinstance(raw.get("function"), dict) else raw
        name = function.get("name")
        if not name:
            continue
        out.append(
            ToolCall(
                name=str(name),
                arguments=parse_arguments(function.get("arguments")),
                id=str(raw.get("id") or f"call_{index}"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# streaming handle
# ---------------------------------------------------------------------------
class ChatStream:
    """Awaitable *and* async-iterable handle on one chat exchange.

    ``async for delta in stream`` yields text deltas as they arrive;
    ``await stream`` runs it to completion and hands back the
    :class:`ChatResult`. Either way ``stream.result`` is populated afterwards.
    """

    def __init__(self, client: "OllamaClient", payload: dict[str, Any]) -> None:
        self._client = client
        self._payload = payload
        self._result = ChatResult(model=str(payload.get("model") or ""))
        self._done = False
        self._started = False
        self._gen: AsyncIterator[str] | None = None
        #: Called with each slice of the model's reasoning as it arrives.
        #:
        #: Reasoning cannot be delivered by iterating the stream, because the
        #: iterator only ticks on a *content* delta and a model that is thinking
        #: is producing no content at all — the chunks land in
        #: `ChatResult.thinking` and the consumer's `async for` never runs.
        #: A surface that wants to show reasoning while it happens (rather than
        #: after the turn) has to be pushed to. Set it after construction; the
        #: default is nobody listening, which costs one `is None`.
        self.on_thinking: Callable[[str], None] | None = None

    # --- interface --------------------------------------------------------
    def __aiter__(self) -> AsyncIterator[str]:
        if self._started:
            raise RuntimeError("ChatStream can only be consumed once")
        self._started = True
        self._gen = self._run()
        return self._gen

    def __await__(self):  # type: ignore[no-untyped-def]
        return self._collect().__await__()

    @property
    def result(self) -> ChatResult:
        return self._result

    @property
    def done(self) -> bool:
        return self._done

    async def aclose(self) -> None:
        """Release the upstream HTTP response.

        A consumer that stops reading part-way — voice barge-in, a cancelled
        request — otherwise leaves the ``/api/chat`` response suspended inside
        the generator, holding a pooled connection until the loop gets round to
        finalising orphaned async generators. Safe to call more than once, and
        safe to call on a stream that was never started.
        """
        gen, self._gen = self._gen, None
        self._started = True
        if gen is None:
            return
        aclose = getattr(gen, "aclose", None)
        if aclose is not None:
            await aclose()

    async def _collect(self) -> ChatResult:
        if not self._started:
            async for _ in self:
                pass
        return self._result

    def _emit_thinking(self, delta: str) -> None:
        """Push one slice of reasoning at whoever asked for it.

        Exception-safe: a surface that throws while drawing a thought must not
        take down the turn it was watching.
        """
        listener = self.on_thinking
        if listener is None or not delta:
            return
        try:
            listener(delta)
        except Exception:  # pragma: no cover - a listener is never load-bearing
            _LOGGER.debug("A reasoning listener raised; ignoring", exc_info=True)

    # --- transport --------------------------------------------------------
    async def _run(self) -> AsyncIterator[str]:
        if self._payload.get("stream"):
            # aclosing, not a bare `async for`: closing _run must tear the
            # inner generator down now, so the `async with http.stream(...)`
            # inside it actually exits.
            async with aclosing(self._stream()) as chunks:
                async for delta in chunks:
                    yield delta
        else:
            delta = await self._single()
            if delta:
                yield delta
        self._done = True

    async def _single(self) -> str:
        http = self._client.http
        url = self._client.endpoint(CHAT_PATH)
        try:
            response = await http.post(url, json=self._payload)
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach Ollama at {url}: {exc}") from exc
        if response.status_code >= 400:
            raise OllamaError(
                f"Ollama returned {response.status_code} for {url}: "
                f"{response.text[:400]}"
            )
        try:
            chunk = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaError(f"Ollama sent invalid JSON: {response.text[:200]!r}") from exc
        if not isinstance(chunk, dict):
            raise OllamaError(f"Ollama sent an unexpected payload: {chunk!r}")
        return self._absorb(chunk)

    async def _stream(self) -> AsyncIterator[str]:
        http = self._client.http
        url = self._client.endpoint(CHAT_PATH)
        try:
            async with http.stream("POST", url, json=self._payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise OllamaError(
                        f"Ollama returned {response.status_code} for {url}: {body[:400]}"
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        _LOGGER.debug("Skipping non-JSON line from Ollama: %r", line[:200])
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    delta = self._absorb(chunk)
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach Ollama at {url}: {exc}") from exc

    # --- accumulation -----------------------------------------------------
    def _absorb(self, chunk: dict[str, Any]) -> str:
        """Fold one NDJSON chunk into the result; return its text delta."""
        error = chunk.get("error")
        if error:
            raise OllamaError(str(error))

        self._result.raw = chunk
        if chunk.get("model"):
            self._result.model = str(chunk["model"])
        if chunk.get("done_reason"):
            self._result.done_reason = str(chunk["done_reason"])

        message = chunk.get("message")
        if not isinstance(message, dict):
            return ""
        if message.get("role"):
            self._result.role = str(message["role"])

        thinking = message.get("thinking")
        if isinstance(thinking, str) and thinking:
            self._result.thinking += thinking
            self._emit_thinking(thinking)

        for call in parse_tool_calls(message):
            self._result.tool_calls.append(call)

        content = message.get("content")
        if isinstance(content, str) and content:
            self._result.content += content
            return content
        return ""


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------
class OllamaClient:
    """Thin async wrapper over the Ollama HTTP API."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        keep_alive: str | float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = str(url or DEFAULT_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout),
            headers=headers or None,
            follow_redirects=True,
        )

        #: Filled in by `list_models`; read by `settings.py` to offer a model
        #: dropdown. It read this attribute before either client defined it, so
        #: the dropdown was reliably empty.
        self.known_models: list[str] = []

    # --- plumbing ---------------------------------------------------------
    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    def endpoint(self, path: str) -> str:
        return f"{self.url}{path}"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # --- the tool loop's wire shape ---------------------------------------
    def assistant_message(self, result: ChatResult) -> dict[str, Any]:
        """The model's own turn, replayed to continue a tool loop.

        Paired with `tool_message` so the agent never has to know which wire it
        is on — the OpenAI client overrides both with the shape a strict server
        (or a proxy translating to Anthropic) demands.
        """
        return result.as_assistant_message()

    def tool_message(self, call: ToolCall, content: str) -> dict[str, Any]:
        """One tool's result. Ollama matches on `name`, not on a call id."""
        return {
            "role": "tool",
            "name": call.name,
            "tool_name": call.name,
            "content": content,
        }

    # --- api --------------------------------------------------------------
    def chat(
        self,
        model: str | None = None,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        stream: bool = True,
        options: dict[str, Any] | None = None,
        keep_alive: str | float | None = None,
        think: bool | None = None,
        format: str | dict[str, Any] | None = None,
    ) -> ChatStream:
        """Start a chat exchange.

        Returns a :class:`ChatStream`: iterate it for deltas, await it for the
        finished :class:`ChatResult`. Nothing is sent until you do one or the
        other.
        """
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [dict(m) for m in (messages or [])],
            "stream": bool(stream),
        }
        if tools:
            payload["tools"] = list(tools)
        if options:
            payload["options"] = dict(options)
        keep = keep_alive if keep_alive is not None else self.keep_alive
        if keep is not None:
            payload["keep_alive"] = keep
        if think is not None:
            payload["think"] = think
        if format is not None:
            payload["format"] = format
        return ChatStream(self, payload)

    async def list_models(self) -> list[str]:
        """Model names the local Ollama has pulled."""
        url = self.endpoint(TAGS_PATH)
        try:
            response = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach Ollama at {url}: {exc}") from exc
        if response.status_code >= 400:
            raise OllamaError(f"Ollama returned {response.status_code} for {url}")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaError("Ollama sent invalid JSON for /api/tags") from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        out: list[str] = []
        for entry in models or []:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("model")
                if name:
                    out.append(str(name))
            elif isinstance(entry, str):
                out.append(entry)
        self.known_models = out
        return out

    async def is_available(self) -> bool:
        """Cheap reachability probe — never raises."""
        try:
            await self.list_models()
        except Exception:
            # Not only OllamaError: a malformed url raises httpx.InvalidURL and
            # an already-closed client raises RuntimeError, neither of which is
            # an HTTPError, and both would escape a probe documented as safe.
            return False
        return True
