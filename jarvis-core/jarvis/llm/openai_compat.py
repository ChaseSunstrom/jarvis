"""The OpenAI chat-completions wire, for vLLM and everything that speaks it.

`ollama.py` speaks Ollama's own `/api/chat`: newline-delimited JSON, one whole
message object per line. That is the only wire jarvis-core knew, so the choice
of inference server was not a choice.

This module speaks `/v1/chat/completions` instead, which is what **vLLM**,
llama.cpp's server, LM Studio, TGI, SGLang and Ollama itself all serve. One
adapter, and the model server becomes a deployment decision rather than an
architectural one.

## What is actually different

Not much, except in one place that matters.

* **Framing.** Server-sent events — `data: {...}` per line, `data: [DONE]` at
  the end — rather than bare NDJSON.
* **Deltas.** `choices[0].delta.content` rather than `message.content`.
* **Reasoning.** vLLM and friends put a reasoning model's thoughts in
  `delta.reasoning_content`. Ollama's native wire uses `message.thinking`, and
  qwen3 through *this* wire emits `<think>` inline instead — which
  `ThinkStripper` in `agent.py` already removes, so all three arrive handled.
* **Tool calls are streamed in PIECES, and this is the whole difficulty.**
  Ollama sends a finished call object. OpenAI sends fragments keyed by `index`:
  the name arrives once, then `function.arguments` turns up as a run of string
  chunks that have to be concatenated in order and parsed as JSON only at the
  end. A parser that treats each chunk as a call — or that JSON-parses a
  fragment — sees a tool call with an empty name and no arguments, which is
  precisely the "model ignored us" path in `_run_rounds`. `_ToolCallBuffer`
  below is that accumulation and nothing else.

## Options

Ollama's `options` block (`temperature`, `num_ctx`, ...) is not OpenAI's
vocabulary. `_translate_options` maps the ones with an equivalent and passes the
rest through as `extra_body`, which vLLM reads and a stricter server ignores —
so a config written for Ollama keeps working, and nothing silently changes
meaning. `num_ctx` is deliberately dropped: on the OpenAI wire the context
length is a property of how the server was started, not of a request, and
sending it as `max_tokens` would cap the *reply* at the size of the window.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from .ollama import ChatResult, ChatStream, OllamaError, ToolCall, parse_arguments

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 120.0

CHAT_PATH = "/chat/completions"
MODELS_PATH = "/models"
EMBEDDINGS_PATH = "/embeddings"

#: Ollama option -> OpenAI request field. Only the ones that mean the same
#: thing; see the module docstring for why `num_ctx` is not among them.
_OPTION_MAP = {
    "temperature": "temperature",
    "top_p": "top_p",
    "seed": "seed",
    "stop": "stop",
    "num_predict": "max_tokens",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}

#: Passed through to the server as-is when it is not in `_OPTION_MAP` and is not
#: something that would mean the wrong thing. vLLM reads `extra_body`; a server
#: that does not simply ignores the key.
_OPTIONS_DROPPED = frozenset({"num_ctx"})


def _translate_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Ollama's `options` block as OpenAI request fields."""
    if not options:
        return {}
    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in options.items():
        if key in _OPTIONS_DROPPED:
            continue
        mapped = _OPTION_MAP.get(key)
        if mapped:
            out[mapped] = value
        else:
            extra[key] = value
    if extra:
        out["extra_body"] = extra
    return out


class _ToolCallBuffer:
    """Accumulates OpenAI's streamed tool-call fragments into whole calls.

    A call arrives as several deltas sharing an `index`. The first usually
    carries `id` and `function.name`; the rest carry successive slices of
    `function.arguments` as raw text. Nothing is parseable until the stream
    says it is done, because `{"entity` is not JSON and `{"entity_id": "light.`
    is not either.
    """

    def __init__(self) -> None:
        self._by_index: dict[int, dict[str, Any]] = {}

    def absorb(self, deltas: Any) -> None:
        if not isinstance(deltas, list):
            return
        for position, raw in enumerate(deltas):
            if not isinstance(raw, dict):
                continue
            # `index` is what ties fragments together. Some servers omit it on
            # a single-call response, so fall back to position in the list.
            try:
                index = int(raw.get("index", position))
            except (TypeError, ValueError):
                index = position
            slot = self._by_index.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if raw.get("id"):
                slot["id"] = str(raw["id"])
            function = raw.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name"):
                slot["name"] = str(function["name"])
            fragment = function.get("arguments")
            if isinstance(fragment, str):
                slot["arguments"] += fragment
            elif isinstance(fragment, dict):
                # A server that sent the whole object rather than text. Keep it
                # verbatim; `parse_arguments` handles both.
                slot["arguments"] = json.dumps(fragment)

    def finish(self) -> list[ToolCall]:
        """The completed calls, in index order. Unnamed fragments are dropped."""
        out: list[ToolCall] = []
        for index in sorted(self._by_index):
            slot = self._by_index[index]
            if not slot["name"]:
                # A fragment run that never carried a name is not a call we can
                # make. `agent._execute_tool_calls` would have nothing to look
                # up, so dropping it here keeps the error where it belongs.
                _LOGGER.debug("Dropping a tool-call fragment with no name: %r", slot)
                continue
            out.append(
                ToolCall(
                    name=slot["name"],
                    arguments=parse_arguments(slot["arguments"]),
                    id=slot["id"] or f"call_{index}",
                )
            )
        return out


class OpenAICompatStream(ChatStream):
    """`ChatStream` over server-sent events instead of NDJSON.

    Everything a caller can do with an Ollama stream works here unchanged —
    `async for` the deltas, `await` the result, `aclose()` to hang up mid-turn —
    because only the two wire-facing methods are replaced.
    """

    def __init__(self, client: "OpenAICompatClient", payload: dict[str, Any]) -> None:
        super().__init__(client, payload)  # type: ignore[arg-type]
        self._tools = _ToolCallBuffer()

    async def _stream(self) -> AsyncIterator[str]:
        http = self._client.http
        url = self._client.endpoint(CHAT_PATH)
        try:
            async with http.stream("POST", url, json=self._payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise OllamaError(
                        f"{self._client.label} returned {response.status_code} "
                        f"for {url}: {body[:400]}"
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue  # keepalive comment
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        _LOGGER.debug("Skipping non-JSON SSE line: %r", line[:200])
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    delta = self._absorb(chunk)
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"could not reach {self._client.label} at {url}: {exc}"
            ) from exc
        finally:
            # Whole calls only, and only once the stream is over — see
            # `_ToolCallBuffer`. Done in a finally so a turn abandoned mid-way
            # still reports the calls it had completed rather than none.
            self._result.tool_calls.extend(self._tools.finish())

    async def _single(self) -> str:
        """The non-streaming path. Overridden because the base one posts to
        Ollama's `/api/chat`, which this server does not serve."""
        http = self._client.http
        url = self._client.endpoint(CHAT_PATH)
        try:
            response = await http.post(url, json=self._payload)
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"could not reach {self._client.label} at {url}: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise OllamaError(
                f"{self._client.label} returned {response.status_code} for {url}: "
                f"{response.text[:400]}"
            )
        try:
            chunk = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaError(
                f"{self._client.label} sent invalid JSON: {response.text[:200]!r}"
            ) from exc
        if not isinstance(chunk, dict):
            raise OllamaError(f"{self._client.label} sent an unexpected payload: {chunk!r}")
        try:
            return self._absorb(chunk)
        finally:
            self._result.tool_calls.extend(self._tools.finish())

    def _absorb(self, chunk: dict[str, Any]) -> str:
        """Fold one SSE chunk into the result; return its text delta."""
        error = chunk.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            raise OllamaError(str(message))

        self._result.raw = chunk
        if chunk.get("model"):
            self._result.model = str(chunk["model"])

        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""
        if choice.get("finish_reason"):
            self._result.done_reason = str(choice["finish_reason"])

        # `delta` while streaming; `message` when the caller asked for a single
        # non-streamed response. Same keys underneath.
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if not isinstance(delta, dict):
            return ""

        if delta.get("role"):
            self._result.role = str(delta["role"])

        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            self._result.thinking += reasoning

        self._tools.absorb(delta.get("tool_calls"))

        content = delta.get("content")
        if isinstance(content, str) and content:
            self._result.content += content
            return content
        return ""


class OpenAICompatClient:
    """Async client for any server speaking the OpenAI chat-completions API.

    Deliberately the same public surface as :class:`~jarvis.llm.ollama.OllamaClient`
    — `chat`, `list_models`, `is_available`, `aclose`, `http`, `endpoint` — so
    `ConversationAgent` and the vision analyser take either without knowing
    which they have.
    """

    #: For error messages, so "could not reach vLLM" says vLLM.
    label = "the model server"

    def __init__(
        self,
        url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        label: str | None = None,
    ) -> None:
        self.url = normalise_base_url(url)
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        if label:
            self.label = label
        merged = dict(headers or {})
        if api_key:
            # Servers started without `--api-key` ignore it; vLLM behind one
            # requires it. Sending it unconditionally costs nothing.
            merged.setdefault("Authorization", f"Bearer {api_key}")
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout),
            headers=merged or None,
            follow_redirects=True,
        )

    # --- plumbing ---------------------------------------------------------
    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    def endpoint(self, path: str) -> str:
        return f"{self.url}{path}"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

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
        """Start a chat exchange. Same contract as the Ollama client's.

        `keep_alive` and `think` are accepted and ignored: they are Ollama's
        own, and a caller that sets one should not have to know which backend
        it ended up talking to. Accepting-and-ignoring is the honest option —
        the alternative is a TypeError from a keyword that used to work.
        """
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [dict(m) for m in (messages or [])],
            "stream": bool(stream),
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        payload.update(_translate_options(options))
        if format is not None:
            payload.update(_translate_format(format))
        return OpenAICompatStream(self, payload)

    async def list_models(self) -> list[str]:
        """Model ids the server is serving."""
        url = self.endpoint(MODELS_PATH)
        try:
            response = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach {self.label} at {url}: {exc}") from exc
        if response.status_code >= 400:
            raise OllamaError(f"{self.label} returned {response.status_code} for {url}")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaError(f"{self.label} sent invalid JSON for {MODELS_PATH}") from exc
        entries = payload.get("data") if isinstance(payload, dict) else None
        out: list[str] = []
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("id"):
                out.append(str(entry["id"]))
            elif isinstance(entry, str):
                out.append(entry)
        return out

    async def is_available(self) -> bool:
        """Cheap reachability probe — never raises."""
        try:
            await self.list_models()
        except Exception:
            return False
        return True

    async def embed(self, texts: Sequence[str], model: str | None = None) -> list[list[float]]:
        """Embeddings for a batch of strings, from the same server.

        This is the seam the memory work needs, and the reason it can exist
        without a new dependency: the vectors come over HTTP from the model
        server that is already running, so jarvis-core stays pure-Python
        wheels — no numpy, no onnxruntime, and the image still builds on a Pi.
        """
        wanted = [str(t) for t in texts if str(t).strip()]
        if not wanted:
            return []
        url = self.endpoint(EMBEDDINGS_PATH)
        try:
            response = await self._http.post(
                url, json={"model": model or self.model, "input": wanted}
            )
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach {self.label} at {url}: {exc}") from exc
        if response.status_code >= 400:
            body = response.text[:400]
            raise OllamaError(
                f"{self.label} returned {response.status_code} for {url}: {body}"
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaError(f"{self.label} sent invalid JSON for {EMBEDDINGS_PATH}") from exc

        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise OllamaError(f"{self.label} sent no embeddings")
        # Ordered by `index`, because the server is not required to answer in
        # the order it was asked and a mis-ordered batch silently attaches every
        # vector to the wrong note.
        ordered = sorted(
            (r for r in rows if isinstance(r, dict)),
            key=lambda r: int(r.get("index", 0)),
        )
        return [[float(x) for x in (r.get("embedding") or [])] for r in ordered]


def _translate_format(format: str | dict[str, Any]) -> dict[str, Any]:
    """Ollama's `format` as OpenAI structured output.

    `format="json"` is `response_format={"type": "json_object"}` everywhere. A
    JSON *schema* is `json_schema` on OpenAI and vLLM, and vLLM additionally
    accepts `guided_json`; sending both is harmless and means one payload works
    on either. This is the seam that makes tool arguments conform rather than
    be hoped at — `OllamaClient.chat` has always plumbed `format` and no caller
    has ever passed one.
    """
    if format == "json":
        return {"response_format": {"type": "json_object"}}
    if isinstance(format, dict):
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": format},
            },
            "extra_body": {"guided_json": format},
        }
    return {}


def normalise_base_url(url: str) -> str:
    """`http://host:8000` -> `http://host:8000/v1`, and leave a real one alone.

    Every server here serves the API under `/v1`, and every one of them is
    also commonly written without it in a config file. Guessing wrong produces
    a 404 that reads like the server being down, so the guess is made once,
    here, rather than by each caller.
    """
    text = str(url or DEFAULT_URL).rstrip("/")
    if not text:
        return DEFAULT_URL
    tail = text.rsplit("/", 1)[-1]
    if tail in ("v1", "openai") or "/v1/" in text:
        return text
    return f"{text}/v1"
