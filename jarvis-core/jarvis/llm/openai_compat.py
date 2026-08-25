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
            async with http.stream(
                "POST", url, json=self._payload, headers=self._client.request_headers()
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise self._client._http_error(
                        response.status_code,
                        url,
                        body,
                        response.headers.get("retry-after"),
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
            response = await http.post(
                url, json=self._payload, headers=self._client.request_headers()
            )
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"could not reach {self._client.label} at {url}: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise self._client._http_error(
                response.status_code,
                url,
                response.text,
                response.headers.get("retry-after"),
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
            self._emit_thinking(reasoning)

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
            # Servers started without `--api-key` ignore it; vLLM and LiteLLM
            # behind one require it. Sending it unconditionally costs nothing.
            merged.setdefault("Authorization", f"Bearer {api_key}")
        #: Sent with every request this client makes, and **not** installed on
        #: the `AsyncClient`.
        #:
        #: This is the difference between `api_key` working and being silently
        #: ignored. The llm integration always injects a shared `AsyncClient`
        #: (one connection pool for the model server and every YAML/authored
        #: HTTP tool), so `client or httpx.AsyncClient(headers=...)` took the
        #: injected one and dropped the headers on the floor — a LiteLLM with a
        #: master key answered 401 and nothing said why.
        #:
        #: Per-request is also the only *correct* place for it. Putting a proxy
        #: key on the shared client would send it to every third-party endpoint
        #: an authored tool can reach, which is a credential leak the model
        #: chooses the destination of.
        self._headers: dict[str, str] = merged
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )
        #: Filled in by `list_models`. `settings.py` reads it to offer a model
        #: dropdown, and read an attribute that existed nowhere until now — so
        #: the dropdown was reliably empty.
        self.known_models: list[str] = []

    # --- plumbing ---------------------------------------------------------
    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    def endpoint(self, path: str) -> str:
        return f"{self.url}{path}"

    def request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str] | None:
        """Headers for one request to the model server. See `self._headers`."""
        merged = dict(self._headers)
        if extra:
            merged.update(extra)
        return merged or None

    def _http_error(self, status: int, url: str, body: str, retry_after: Any = None) -> OllamaError:
        """A failure the retry logic can reason about.

        A proxy answers with real HTTP semantics and the difference matters:
        429 with a `Retry-After` is "come back in nine seconds", 401 is "your
        key is wrong" and retrying it is pure latency, 502 is the upstream
        model flapping and is worth another go. Without the status on the
        error, `_Round.stream` treated all three identically.
        """
        error = OllamaError(f"{self.label} returned {status} for {url}: {body[:400]}")
        error.status = status
        try:
            error.retry_after = float(retry_after) if retry_after else 0.0
        except (TypeError, ValueError):
            # `Retry-After` may also be an HTTP date. Not worth parsing: the
            # fixed backoff is a fine answer and a wrong parse is worse.
            error.retry_after = 0.0
        return error

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
            # Merged, not `update`d. Both translators can produce `extra_body`,
            # and a plain update let the format's copy replace the options' one
            # — so asking for guided decoding silently dropped every passthrough
            # option the config had set.
            extra = _translate_format(format)
            body = extra.pop("extra_body", None)
            payload.update(extra)
            if body:
                payload.setdefault("extra_body", {}).update(body)
        _tag_privacy(payload)
        return OpenAICompatStream(self, payload)

    # --- the tool loop's wire shape ---------------------------------------
    def assistant_message(self, result: ChatResult) -> dict[str, Any]:
        """The model's own turn, replayed to continue a tool loop.

        Three differences from Ollama's shape, each of which a strict server
        rejects: `type: "function"` is required, `arguments` is a JSON *string*
        rather than an object, and every call needs an `id` that the tool
        result can point back at.

        This is the difference between multi-tool turns working and not through
        a proxy. vLLM and Ollama's own `/v1` are lenient about all three;
        LiteLLM translating to Anthropic or Bedrock is not, and rejects a
        `tool_use` block with no matching `tool_result` — so round two of every
        tool turn failed with a 400 that named none of this.
        """
        message: dict[str, Any] = {
            "role": result.role or "assistant",
            "content": result.content or "",
        }
        if not result.tool_calls:
            return message
        parts: list[dict[str, Any]] = []
        for index, call in enumerate(result.tool_calls):
            # Backfilled onto the call itself, so `tool_message` below names the
            # same id. A server that omitted ids would otherwise get a result
            # pointing at a call it never announced.
            if not call.id:
                call.id = f"call_{index}"
            parts.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments or {}, ensure_ascii=False),
                    },
                }
            )
        message["tool_calls"] = parts
        return message

    def tool_message(self, call: ToolCall, content: str) -> dict[str, Any]:
        """One tool's result, addressed to the call that asked for it."""
        return {
            "role": "tool",
            "tool_call_id": call.id or "",
            "name": call.name,
            "content": content,
        }

    async def list_models(self) -> list[str]:
        """Model ids the server is serving."""
        url = self.endpoint(MODELS_PATH)
        try:
            response = await self._http.get(url, headers=self.request_headers())
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach {self.label} at {url}: {exc}") from exc
        if response.status_code >= 400:
            # With the body: a LiteLLM budget or key failure says which in the
            # payload, and a bare "returned 401" is a support ticket.
            raise self._http_error(response.status_code, url, response.text)
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
        self.known_models = out
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
                url,
                json={"model": model or self.model, "input": wanted},
                headers=self.request_headers(),
            )
        except httpx.HTTPError as exc:
            raise OllamaError(f"could not reach {self.label} at {url}: {exc}") from exc
        if response.status_code >= 400:
            raise self._http_error(response.status_code, url, response.text)
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


def _tag_privacy(payload: dict[str, Any]) -> None:
    """Mark a request whose prompt carries private content (M40).

    The tag travels in `metadata` and is enforced by the gateway, which refuses
    to route a `local-only` request at a cloud provider. Both halves exist on
    purpose: this one knows WHAT is in the prompt, and the proxy is where the
    refusal binds anything that can reach the endpoint rather than only a
    well-behaved client.

    A prompt with nothing private in it is not tagged at all, so an install
    with no gateway sends exactly what it sent before.
    """
    try:
        from ..security.privacy import HEADER, classify

        tag, _why = classify(payload.get("messages"))
        if not tag:
            return
        metadata = payload.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["privacy"] = tag
        # And as a header, because a proxy that drops unknown body keys still
        # sees headers — and `drop_params: true` is a normal thing to configure.
        headers = payload.setdefault("extra_headers", {})
        if isinstance(headers, dict):
            headers[HEADER] = tag
    except Exception:  # pragma: no cover - tagging must never fail a turn
        pass


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

    Two shapes beyond the obvious one, because both are what people actually
    paste and both used to end in that same unexplained 404:

    * **A full endpoint** — `http://litellm:4000/v1/chat/completions`, copied
      out of a README. Appending nothing and then `/chat/completions` again
      gives `/v1/chat/completions/chat/completions`.
    * **A different case** — `/V1`. The tail check was case-sensitive, so this
      became `/V1/v1`.
    """
    text = str(url or DEFAULT_URL).rstrip("/")
    if not text:
        return DEFAULT_URL
    lowered = text.lower()
    for suffix in (CHAT_PATH, MODELS_PATH, EMBEDDINGS_PATH):
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].rstrip("/")
            lowered = text.lower()
            break
    tail = lowered.rsplit("/", 1)[-1]
    if tail in ("v1", "openai") or "/v1/" in f"{lowered}/":
        return text
    return f"{text}/v1"
