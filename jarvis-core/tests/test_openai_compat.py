"""Talking to vLLM — and to llama.cpp, LM Studio, TGI, SGLang and Ollama's `/v1`.

## Why this exists

jarvis-core spoke exactly one wire: Ollama's own `/api/chat`, newline-delimited
JSON, one whole message object per line. That made the inference server an
architectural decision rather than a deployment one — you could not put vLLM
behind it without rewriting the client.

`openai_compat.py` speaks `/v1/chat/completions` instead, which all of the above
serve. The interesting half is tool calls: Ollama sends a finished call object,
and OpenAI sends **fragments** — the name once, then `function.arguments` as a
run of string chunks keyed by `index`. A parser that treats a fragment as a call
produces a nameless tool call with no arguments, which `_run_rounds` reads as
the model ignoring it and silently drops. So that accumulation is what most of
this file is about.

Everything here runs against `httpx.MockTransport` serving bytes in the shape a
real server sends. No network, no model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.llm.ollama import OllamaError  # noqa: E402
from jarvis.llm.openai_compat import (  # noqa: E402
    OpenAICompatClient,
    normalise_base_url,
)


def _sse(*chunks: dict) -> bytes:
    """The bytes a real server puts on the wire, terminator and all."""
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


def _client(handler) -> OpenAICompatClient:
    return OpenAICompatClient(
        url="http://vllm:8000",
        model="qwen3:8b",
        transport=httpx.MockTransport(handler),
    )


def _delta(**delta) -> dict:
    return {"model": "qwen3:8b", "choices": [{"index": 0, "delta": delta}]}


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------
async def test_text_arrives_delta_by_delta():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        sent = json.loads(request.content)
        assert sent["stream"] is True
        assert sent["messages"] == [{"role": "user", "content": "hello"}]
        return httpx.Response(
            200,
            content=_sse(
                _delta(role="assistant", content="Good "),
                _delta(content="evening, "),
                _delta(content="Sir."),
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "hello"}])
    got = [delta async for delta in stream]

    assert got == ["Good ", "evening, ", "Sir."]
    assert stream.result.content == "Good evening, Sir."
    assert stream.result.done_reason == "stop"


async def test_keepalive_comments_and_blank_lines_are_not_content():
    """SSE allows `:` comments and blank lines; neither is a token."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            ": ping\n\n"
            + f"data: {json.dumps(_delta(content='Yes'))}\n\n"
            + "\n"
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode())

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    assert [d async for d in stream] == ["Yes"]


# ---------------------------------------------------------------------------
# tool calls — the fragments
# ---------------------------------------------------------------------------
async def test_a_tool_call_split_across_chunks_is_reassembled():
    """The whole reason this module needs its own parser.

    `{"entity` is not JSON and neither is `_id": "light.k`. Only the
    concatenation is, and only once the stream has ended.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                _delta(
                    role="assistant",
                    tool_calls=[
                        {"index": 0, "id": "call_abc", "function": {"name": "turn_on", "arguments": ""}}
                    ],
                ),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"nam'}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": 'e": "kitc'}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": 'hen lamp"}'}}]),
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "lamp on"}])
    async for _ in stream:
        pass

    calls = stream.result.tool_calls
    assert len(calls) == 1
    assert calls[0].name == "turn_on"
    assert calls[0].arguments == {"name": "kitchen lamp"}
    assert calls[0].id == "call_abc"


async def test_two_tool_calls_interleaved_stay_separate():
    """`index` is what tells them apart, and the fragments do interleave."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                _delta(
                    tool_calls=[
                        {"index": 0, "id": "a", "function": {"name": "turn_on", "arguments": ""}},
                        {"index": 1, "id": "b", "function": {"name": "get_state", "arguments": ""}},
                    ]
                ),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"name": "la'}}]),
                _delta(tool_calls=[{"index": 1, "function": {"arguments": '{"entity_id": "l'}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": 'mp"}'}}]),
                _delta(tool_calls=[{"index": 1, "function": {"arguments": 'ock.front"}'}}]),
            ),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    async for _ in stream:
        pass

    calls = stream.result.tool_calls
    assert [c.name for c in calls] == ["turn_on", "get_state"]
    assert calls[0].arguments == {"name": "lamp"}
    assert calls[1].arguments == {"entity_id": "lock.front"}


async def test_a_fragment_run_with_no_name_is_dropped_not_half_made():
    """A call nothing can look up must not reach `_execute_tool_calls`.

    It would resolve to "unknown tool ''" and burn one of five rounds. Dropping
    it keeps the failure where it can be read.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(_delta(tool_calls=[{"index": 0, "function": {"arguments": "{}"}}])),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    async for _ in stream:
        pass
    assert stream.result.tool_calls == []


async def test_a_server_that_sends_whole_arguments_still_works():
    """Not every server fragments. Ollama's own `/v1` often does not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                _delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "get_state", "arguments": {"entity_id": "light.a"}},
                        }
                    ]
                )
            ),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    async for _ in stream:
        pass
    assert stream.result.tool_calls[0].arguments == {"entity_id": "light.a"}


async def test_tools_are_offered_with_tool_choice_auto():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta(content="ok")))

    schema = [{"type": "function", "function": {"name": "turn_on", "parameters": {}}}]
    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}], tools=schema)
    async for _ in stream:
        pass

    assert seen["tools"] == schema
    assert seen["tool_choice"] == "auto"


# ---------------------------------------------------------------------------
# reasoning
# ---------------------------------------------------------------------------
async def test_reasoning_content_is_captured_and_not_spoken():
    """vLLM puts a reasoning model's thoughts in their own field.

    They must not come out as text deltas — those are what the HUD renders and
    the TTS speaks.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                _delta(reasoning_content="The user wants the lamp. "),
                _delta(reasoning_content="It is in the kitchen."),
                _delta(content="Done, Sir."),
            ),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    spoken = [d async for d in stream]

    assert spoken == ["Done, Sir."]
    assert stream.result.thinking == "The user wants the lamp. It is in the kitchen."


# ---------------------------------------------------------------------------
# options, format, errors
# ---------------------------------------------------------------------------
async def test_ollama_options_are_translated_not_forwarded_verbatim():
    """A config written for Ollama must keep meaning the same thing.

    `num_ctx` is deliberately dropped: on this wire the window is a property of
    how the server was started, and sending it as `max_tokens` would cap the
    *reply* at the size of the context.
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta(content="ok")))

    stream = _client(handler).chat(
        messages=[{"role": "user", "content": "x"}],
        options={"temperature": 0.6, "num_ctx": 8192, "num_predict": 256, "mirostat": 1},
    )
    async for _ in stream:
        pass

    assert seen["temperature"] == 0.6
    assert seen["max_tokens"] == 256
    assert "num_ctx" not in seen and "num_ctx" not in seen.get("extra_body", {})
    assert seen["extra_body"] == {"mirostat": 1}


async def test_a_json_schema_asks_for_guided_decoding():
    """The seam that makes tool arguments conform rather than be hoped at."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta(content="{}")))

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}], format=schema)
    async for _ in stream:
        pass

    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["schema"] == schema
    assert seen["extra_body"]["guided_json"] == schema


async def test_ollama_only_keywords_are_accepted_and_ignored():
    """`keep_alive` and `think` are Ollama's. A caller should not have to know
    which backend it got, and a TypeError from a keyword that used to work is
    the worst possible way to find out."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert "keep_alive" not in sent and "think" not in sent
        return httpx.Response(200, content=_sse(_delta(content="ok")))

    stream = _client(handler).chat(
        messages=[{"role": "user", "content": "x"}], keep_alive="5m", think=True
    )
    assert [d async for d in stream] == ["ok"]


async def test_an_http_error_says_which_server_and_why():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="model 'nope' does not exist")

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    with pytest.raises(OllamaError, match="does not exist"):
        async for _ in stream:
            pass


async def test_an_error_object_mid_stream_is_raised():
    """A 200 that turns into an error partway through is still an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                _delta(content="thinking"),
                {"error": {"message": "ran out of memory"}},
            ),
        )

    stream = _client(handler).chat(messages=[{"role": "user", "content": "x"}])
    with pytest.raises(OllamaError, match="ran out of memory"):
        async for _ in stream:
            pass


# ---------------------------------------------------------------------------
# models, embeddings, urls
# ---------------------------------------------------------------------------
async def test_list_models_reads_the_openai_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": "qwen3:8b"}, {"id": "nomic-embed-text"}]}
        )

    assert await _client(handler).list_models() == ["qwen3:8b", "nomic-embed-text"]


async def test_is_available_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await _client(handler).is_available() is False


async def test_embeddings_come_back_in_the_order_they_were_asked_for():
    """A mis-ordered batch attaches every vector to the wrong note, silently.

    The API does not promise response order, so this sorts by `index`. The
    handler below answers backwards on purpose.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        sent = json.loads(request.content)
        assert sent["input"] == ["first", "second"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2, 0.2]},
                    {"index": 0, "embedding": [0.1, 0.1]},
                ]
            },
        )

    got = await _client(handler).embed(["first", "second"])
    assert got == [[0.1, 0.1], [0.2, 0.2]]


async def test_embedding_nothing_asks_nothing():
    """An empty batch must not become an HTTP round trip with an empty body."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have been called")

    assert await _client(handler).embed(["", "   "]) == []


@pytest.mark.parametrize(
    "given,expected",
    [
        ("http://vllm:8000", "http://vllm:8000/v1"),
        ("http://vllm:8000/", "http://vllm:8000/v1"),
        ("http://vllm:8000/v1", "http://vllm:8000/v1"),
        ("http://vllm:8000/v1/", "http://vllm:8000/v1"),
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
    ],
)
def test_the_base_url_is_normalised_once_rather_than_by_every_caller(given, expected):
    """Guessing wrong gives a 404 that reads exactly like the server being down."""
    assert normalise_base_url(given) == expected


# ---------------------------------------------------------------------------
# the wiring — which client an install actually gets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:11434", "ollama"),
        ("http://127.0.0.1:11434/", "ollama"),
        ("http://vllm:8000/v1", "openai"),
        ("http://vllm:8000/v1/", "openai"),
        ("http://box/openai", "openai"),
    ],
)
def test_the_backend_is_inferred_from_the_url_when_nobody_says(url, expected):
    """An install that never heard of `backend:` keeps what it had.

    A url ending in `/v1` is unambiguous: Ollama's native API has no such path
    and every OpenAI-compatible server serves exactly it.
    """
    from jarvis.integrations.llm import _detect_backend

    assert _detect_backend(url) == expected


def test_an_explicit_backend_wins_over_the_url():
    from jarvis.integrations.llm import _build_model_client

    client = _build_model_client(
        {"backend": "openai"}, "http://vllm:8000", "m", 30.0, None
    )
    assert isinstance(client, OpenAICompatClient)
    assert client.url == "http://vllm:8000/v1"


def test_an_unknown_backend_falls_back_rather_than_failing_to_boot():
    """A typo in one config key must not cost the whole assistant.

    It is logged. The alternative — refusing to start — turns a one-character
    mistake into a house with no voice.
    """
    from jarvis.integrations.llm import _build_model_client
    from jarvis.llm.ollama import OllamaClient

    client = _build_model_client(
        {"backend": "vllm-ish"}, "http://127.0.0.1:11434", "m", 30.0, None
    )
    assert isinstance(client, OllamaClient)


def test_both_clients_present_the_surface_the_agent_uses():
    """`ConversationAgent` must not be able to tell which one it got."""
    from jarvis.llm.ollama import OllamaClient

    for name in ("chat", "list_models", "is_available", "aclose", "endpoint"):
        assert callable(getattr(OllamaClient, name, None)), name
        assert callable(getattr(OpenAICompatClient, name, None)), name
    assert hasattr(OllamaClient, "http") and hasattr(OpenAICompatClient, "http")


# ---------------------------------------------------------------------------
# authentication — the part that was silently not happening
# ---------------------------------------------------------------------------
async def test_the_api_key_reaches_the_wire_through_an_injected_client():
    """The regression test for a bug that shipped.

    Headers were set on the `AsyncClient` the constructor builds — but the llm
    integration ALWAYS injects a shared one (a single connection pool for the
    model server and every YAML tool), so `client or httpx.AsyncClient(...)`
    took the injected client and the headers were dropped on the floor. Every
    request to a LiteLLM with a master key came back 401, and nothing anywhere
    said why.

    Injected, not default-constructed, because the default path was never the
    broken one.
    """
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, content=_sse(_delta(content="hi")))

    shared = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatClient(url="http://litellm:4000/v1", client=shared, api_key="sk-test")

    async for _ in client.chat(messages=[{"role": "user", "content": "x"}]):
        pass

    assert seen[0]["authorization"] == "Bearer sk-test"
    await shared.aclose()


async def test_the_key_is_not_installed_on_the_shared_client():
    """Per-request is not merely a fix, it is the only correct place.

    jarvis-core hands the same `AsyncClient` to every YAML- and console-authored
    HTTP tool. A proxy key set at client level would be sent to every
    third-party endpoint the model can reach — and which endpoints those are is
    a decision the MODEL makes.
    """
    shared = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    OpenAICompatClient(url="http://litellm:4000/v1", client=shared, api_key="sk-secret")

    assert shared.headers.get("authorization") is None
    await shared.aclose()


async def test_custom_headers_reach_every_endpoint():
    """`headers:` is for a router that wants more than a bearer token."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.path}:{request.headers.get('x-litellm-tags')}")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "house-model"}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})
        return httpx.Response(200, content=_sse(_delta(content="hi")))

    shared = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatClient(
        url="http://litellm:4000/v1", client=shared, headers={"x-litellm-tags": "house"}
    )

    async for _ in client.chat(messages=[{"role": "user", "content": "x"}]):
        pass
    await client.list_models()
    await client.embed(["a note"])

    assert seen == [
        "/v1/chat/completions:house",
        "/v1/models:house",
        "/v1/embeddings:house",
    ]
    await shared.aclose()


async def test_no_key_sends_no_authorization_header():
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, content=_sse(_delta(content="hi")))

    async for _ in _client(handler).chat(messages=[{"role": "user", "content": "x"}]):
        pass

    assert "authorization" not in seen[0]


# ---------------------------------------------------------------------------
# base urls people actually paste
# ---------------------------------------------------------------------------
def test_a_pasted_full_endpoint_is_trimmed_to_its_base():
    # Copied out of a LiteLLM README. Without this the client posts to
    # /v1/chat/completions/chat/completions and 404s.
    assert (
        normalise_base_url("http://litellm:4000/v1/chat/completions")
        == "http://litellm:4000/v1"
    )
    assert normalise_base_url("http://vllm:8000/v1/models") == "http://vllm:8000/v1"


def test_the_v1_check_is_case_insensitive():
    # `/V1` used to become `/V1/v1`.
    assert normalise_base_url("http://host:8000/V1") == "http://host:8000/V1"


def test_a_bare_host_gains_v1_and_a_real_one_is_left_alone():
    assert normalise_base_url("http://host:8000") == "http://host:8000/v1"
    assert normalise_base_url("http://host:8000/") == "http://host:8000/v1"
    assert normalise_base_url("http://host:8000/v1") == "http://host:8000/v1"
    assert normalise_base_url("http://gw/openai") == "http://gw/openai"


# ---------------------------------------------------------------------------
# the tool loop's wire shape
# ---------------------------------------------------------------------------
def test_an_assistant_message_is_openai_shaped():
    """Three differences from Ollama's shape, each of which a strict server
    rejects: `type: "function"`, `arguments` as a JSON string, and an id."""
    from jarvis.llm.ollama import ChatResult, ToolCall

    client = OpenAICompatClient(url="http://x/v1")
    result = ChatResult(
        content="",
        tool_calls=[ToolCall(name="turn_on", arguments={"name": "lab"}, id="call_abc")],
    )

    message = client.assistant_message(result)

    call = message["tool_calls"][0]
    assert call["type"] == "function"
    assert call["id"] == "call_abc"
    assert call["function"]["arguments"] == '{"name": "lab"}'
    assert isinstance(call["function"]["arguments"], str)


def test_a_tool_result_names_the_call_it_answers():
    """LiteLLM in front of Anthropic or Bedrock rejects a `tool_result` with no
    matching `tool_use` id, so without this the SECOND round of every
    multi-tool turn 400s."""
    from jarvis.llm.ollama import ToolCall

    client = OpenAICompatClient(url="http://x/v1")
    message = client.tool_message(ToolCall(name="turn_on", id="call_abc"), '{"status": "ok"}')

    assert message["tool_call_id"] == "call_abc"
    assert message["role"] == "tool"


def test_ids_are_backfilled_so_the_result_can_point_at_the_call():
    """A server that omitted ids would otherwise get a result addressed to a
    call it never announced."""
    from jarvis.llm.ollama import ChatResult, ToolCall

    client = OpenAICompatClient(url="http://x/v1")
    call = ToolCall(name="turn_on", arguments={}, id="")
    message = client.assistant_message(ChatResult(tool_calls=[call]))

    assert message["tool_calls"][0]["id"] == "call_0"
    # ...and the same value reaches the result, which is the point.
    assert client.tool_message(call, "{}")["tool_call_id"] == "call_0"


def test_the_ollama_client_keeps_its_own_shape():
    """The two wires disagree, and the agent must not have to know."""
    from jarvis.llm.ollama import OllamaClient, ToolCall

    message = OllamaClient().tool_message(ToolCall(name="turn_on", id="call_0"), "{}")

    assert "tool_call_id" not in message
    assert message["tool_name"] == "turn_on"


# ---------------------------------------------------------------------------
# request body
# ---------------------------------------------------------------------------
async def test_guided_decoding_does_not_discard_the_passthrough_options():
    """`_translate_format` used to replace the whole `extra_body` key that
    `_translate_options` had just written, so asking for guided decoding
    silently dropped every option the config had set."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=_sse(_delta(content="{}")))

    stream = _client(handler).chat(
        messages=[{"role": "user", "content": "x"}],
        options={"temperature": 0.6, "repetition_penalty": 1.1},
        format={"type": "object"},
    )
    async for _ in stream:
        pass

    body = sent[0]
    assert body["temperature"] == 0.6
    assert body["extra_body"]["repetition_penalty"] == 1.1
    assert body["extra_body"]["guided_json"] == {"type": "object"}
    assert body["response_format"]["type"] == "json_schema"


# ---------------------------------------------------------------------------
# failures a proxy actually produces
# ---------------------------------------------------------------------------
async def test_a_429_carries_its_status_and_retry_after():
    """The retry logic cannot tell "come back in nine seconds" from "your key is
    wrong" without these."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "9"}, text="budget exceeded")

    with pytest.raises(OllamaError) as raised:
        async for _ in _client(handler).chat(messages=[{"role": "user", "content": "x"}]):
            pass

    assert raised.value.status == 429
    assert raised.value.retry_after == 9.0
    assert "budget exceeded" in str(raised.value)


async def test_an_http_date_retry_after_is_ignored_rather_than_guessed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, text="slow down"
        )

    with pytest.raises(OllamaError) as raised:
        async for _ in _client(handler).chat(messages=[{"role": "user", "content": "x"}]):
            pass

    assert raised.value.retry_after == 0.0


async def test_a_401_from_the_model_list_says_what_the_server_said():
    """A bare "returned 401" is a support ticket; LiteLLM puts the reason in
    the body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid proxy key"}})

    with pytest.raises(OllamaError) as raised:
        await _client(handler).list_models()

    assert raised.value.status == 401
    assert "Invalid proxy key" in str(raised.value)


async def test_list_models_fills_the_dropdown_the_console_reads():
    """`settings.py` has read `client.known_models` since it was written, and
    no client defined it — so the console's model dropdown was always empty."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "house-model"}, {"id": "big"}]})

    client = _client(handler)
    assert client.known_models == []

    assert await client.list_models() == ["house-model", "big"]
    assert client.known_models == ["house-model", "big"]


@pytest.mark.parametrize(
    "url,expected",
    [
        # `/v1` anywhere, not only at the end — this used to look at the final
        # segment alone, so a pasted full endpoint was normalised as OpenAI and
        # then dispatched to Ollama's /api/chat.
        ("http://litellm:4000/v1/chat/completions", "openai"),
        ("http://litellm:4000/V1", "openai"),
        # A bare host:port stays Ollama. Deliberate: every existing install
        # writes its Ollama url exactly that way, and changing this default
        # would break all of them on upgrade.
        ("http://litellm:4000", "ollama"),
    ],
)
def test_backend_detection_matches_how_the_url_is_normalised(url, expected):
    from jarvis.integrations.llm import _detect_backend

    assert _detect_backend(url) == expected


def test_an_unset_env_var_default_is_not_sent_as_a_literal_bearer_quote():
    """`!env_var LLM_API_KEY ""` with nothing in the environment.

    `config.py` keeps the default token verbatim, so an empty-string default
    arrives as the two CHARACTERS `""`. Passed through, that is
    `Authorization: Bearer ""` and a 401 against a config file that looks
    entirely correct.
    """
    from jarvis.integrations.llm import _build_model_client

    client = _build_model_client(
        {"backend": "openai", "api_key": '""'}, "http://litellm:4000/v1", "m", 30.0, None
    )

    assert client.request_headers() is None


def test_configured_headers_reach_the_client():
    from jarvis.integrations.llm import _build_model_client

    client = _build_model_client(
        {
            "backend": "openai",
            "api_key": "sk-1",
            "headers": {"x-litellm-tags": "house", "x-empty": '""'},
        },
        "http://litellm:4000/v1",
        "m",
        30.0,
        None,
    )

    headers = client.request_headers()
    assert headers["Authorization"] == "Bearer sk-1"
    assert headers["x-litellm-tags"] == "house"
    # A header whose env var is unset is dropped rather than sent empty.
    assert "x-empty" not in headers


def test_the_label_appears_in_the_error_so_it_names_the_right_server():
    """"could not reach the model server" is unhelpful when there are two."""
    from jarvis.integrations.llm import _build_model_client

    client = _build_model_client(
        {"backend": "openai", "backend_name": "LiteLLM"},
        "http://litellm:4000/v1",
        "m",
        30.0,
        None,
    )
    assert client.label == "LiteLLM"


def test_the_diagnostic_script_still_finds_what_it_imports():
    """`scripts/check-model-server.py` is what somebody runs when Jarvis says it
    cannot reach the language model, so it must not be the second thing that is
    broken. It reaches into private helpers — `_detect_backend`, `_scalar` — and
    a rename here would leave it failing on import at exactly the wrong moment.

    Checked by name rather than by running it: the script needs a live server.
    """
    import ast
    import importlib
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "check-model-server.py"
    assert script.is_file(), "the diagnostic script has moved or been deleted"

    tree = ast.parse(script.read_text(encoding="utf-8"))
    wanted: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("jarvis"):
            wanted.setdefault(node.module, set()).update(a.name for a in node.names)
    assert wanted, "the script no longer imports jarvis-core at all"

    for module_name, names in wanted.items():
        module = importlib.import_module(module_name)
        missing = sorted(n for n in names if not hasattr(module, n))
        assert not missing, f"{module_name} no longer provides {missing}"
