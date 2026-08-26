"""The models the servers actually serve, read without a network (M54).

Every server here is a `httpx.MockTransport` handler keyed on host name, so
the whole chain — LiteLLM in front, llama-swap behind it with vLLM under that,
two TEI containers beside — is exercised with the shapes the real ones
answered on the deployed stack (`docs/verification.md`, M54), and nothing in
this file can reach a port.

What is pinned, and why each one matters:

* the aliases are resolved and never listed as models — `house` is a name
  Jarvis uses, `qwen3.8-27b` is the model, and the settings page used to
  show the former and call it the latter;
* an unloaded model is never asked about through `/upstream/…`, because on
  llama-swap that is how you load it;
* a parameter count comes from the server when the server says and is
  otherwise read off the id and marked so — never made up;
* a server that is down is a row that says so, not a page that will not open.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import common  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm import catalogue  # noqa: E402
from jarvis.llm.catalogue import (  # noqa: E402
    ROLE_CHAT,
    ROLE_EMBEDDINGS,
    ROLE_FAST,
    ROLE_RERANK,
    ROLE_VISION,
    async_describe,
    describe_model_id,
    family_of,
)

GATEWAY = "http://gateway:4000"
SWAP = "http://swap:8080"
EMBED = "http://embed:7997"
RERANK = "http://rerank:7998"


# ---------------------------------------------------------------------------
# reading an id
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("model_id", "family", "parameters", "quant", "role"),
    [
        ("qwen3.8-27b", "Qwen 3.8", "27B", "", "unknown"),
        ("qwen3.6-35b", "Qwen 3.6", "35B", "", "unknown"),
        ("cyankiwi/Qwen3.8-27B-AWQ-INT4", "Qwen 3.8", "27B", "AWQ-INT4", "unknown"),
        ("gemma-3-27b-it-Q4_K_M.gguf", "Gemma 3", "27B", "Q4_K_M", "unknown"),
        ("qwen2.5vl:7b", "Qwen 2.5 VL", "7B", "", "vision"),
        ("Qwen2.5-VL-7B-Instruct-Q8_0", "Qwen 2.5 VL", "7B", "Q8_0", "vision"),
        ("BAAI/bge-small-en-v1.5", "BGE", "", "", "embeddings"),
        ("cross-encoder/ms-marco-MiniLM-L-6-v2", "MiniLM", "", "", "rerank"),
        ("nomic-embed-text", "Nomic", "", "", "embeddings"),
        ("qwen3:0.6b", "Qwen 3", "0.6B", "", "unknown"),
        ("mistral-small-3.1-24b-instruct-2503-iq4_xs", "Mistral", "24B", "IQ4_XS", "unknown"),
        ("llava:13b", "LLaVA", "13B", "", "vision"),
    ],
)
def test_an_id_is_read_for_what_it_says(model_id, family, parameters, quant, role) -> None:
    got = describe_model_id(model_id)
    assert got == {"family": family, "parameters": parameters, "quant": quant, "role": role}


def test_an_id_that_says_nothing_yields_nothing() -> None:
    """`house` carries no size, no quant, no role. Empty, not a guess."""
    got = describe_model_id("house")
    assert got["parameters"] == "" and got["quant"] == "" and got["role"] == "unknown"
    assert family_of("") == ""


# ---------------------------------------------------------------------------
# the fake stack
# ---------------------------------------------------------------------------
class FakeStack:
    """The deployed shape: LiteLLM → llama-swap → vLLM, plus two TEI containers.

    Every request is recorded so a test can assert what was *not* asked.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.gateway_key = "sk-test"
        self.loaded = {"qwen3.8-27b"}
        self.swap_up = True
        self.embed_up = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "gateway":
            if request.headers.get("authorization") != f"Bearer {self.gateway_key}":
                return httpx.Response(401, json={"error": {"message": "invalid key"}})
            if path == "/v1/models":
                return httpx.Response(200, json={"data": [
                    {"id": "house", "object": "model", "owned_by": "openai"},
                    {"id": "house-fast", "object": "model", "owned_by": "openai"},
                ], "object": "list"})
            if path == "/model/info":
                return httpx.Response(200, json={"data": [
                    {"model_name": "house",
                     "litellm_params": {"model": "openai/qwen3.8-27b", "api_base": f"{SWAP}/v1", "rpm": 60},
                     "model_info": {"locality": "local", "supports_vision": None}},
                    {"model_name": "house-fast",
                     "litellm_params": {"model": "openai/qwen3.6-35b", "api_base": f"{SWAP}/v1"},
                     "model_info": {"locality": "local"}},
                ]})
            return httpx.Response(404, json={"detail": "Not Found"})
        if host == "swap":
            if not self.swap_up:
                raise httpx.ConnectError("connection refused", request=request)
            if path == "/v1/models":
                return httpx.Response(200, json={"data": [
                    {"id": "qwen3.6-35b", "object": "model", "owned_by": "llama-swap",
                     "meta": {"llamaswap": {"type": "model"}},
                     "status": {"value": "loaded" if "qwen3.6-35b" in self.loaded else "unloaded"}},
                    {"id": "qwen3.8-27b", "object": "model", "owned_by": "llama-swap",
                     "meta": {"llamaswap": {"type": "model"}},
                     "status": {"value": "loaded" if "qwen3.8-27b" in self.loaded else "unloaded"}},
                ], "object": "list"})
            if path == "/running":
                return httpx.Response(200, json={"running": [
                    {"model": model, "state": "ready",
                     "cmd": "/bin/sh -c \"/opt/vllm/bin/vllm serve cyankiwi/Qwen3.8-27B-AWQ-INT4 "
                            "--port 5801 --max-model-len 256000 --served-model-name qwen3.8-27b\"",
                     "proxy": "http://127.0.0.1:5801", "ttl": 0, "name": "", "description": ""}
                    for model in sorted(self.loaded)
                ]})
            if path.startswith("/upstream/"):
                model = path.split("/")[2]
                if model not in self.loaded:
                    # llama-swap would START it here. The test asserts we never arrive.
                    self.loaded.add(model)
                return httpx.Response(200, json={"data": [
                    {"id": model, "object": "model", "owned_by": "vllm",
                     "root": "cyankiwi/Qwen3.8-27B-AWQ-INT4", "max_model_len": 256000},
                ]})
            return httpx.Response(404)
        if host == "embed":
            if not self.embed_up:
                raise httpx.ConnectError("connection refused", request=request)
            if path == "/info":
                return httpx.Response(200, json={
                    "model_id": "BAAI/bge-small-en-v1.5", "served_model_name": "BAAI/bge-small-en-v1.5",
                    "model_dtype": "float32", "model_type": {"embedding": {"pooling": "cls"}},
                    "max_input_length": 512, "version": "1.9.3",
                })
            return httpx.Response(404)
        if host == "rerank":
            if path == "/info":
                return httpx.Response(200, json={
                    "model_id": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                    "served_model_name": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                    "model_dtype": "float32", "model_type": {"reranker": {"id2label": {"0": "LABEL_0"}}},
                    "max_input_length": 512,
                })
            return httpx.Response(404)
        return httpx.Response(502, text=f"no such host {host}")

    def asked(self, fragment: str) -> list[str]:
        return [str(r.url) for r in self.requests if fragment in str(r.url)]


def deployed_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "llm": {"url": f"{GATEWAY}/v1", "model": "house", "api_key": "sk-test", "backend": "openai"},
        "memory": {"embeddings": True, "embedding_model": "BAAI/bge-small-en-v1.5", "embedding_url": f"{EMBED}/v1"},
        "research": {"rerank_url": RERANK, "rerank_model": "cross-encoder/ms-marco-MiniLM-L-6-v2", "model": ""},
        "code": {"model": ""},
    }
    config.update(overrides)
    return config


@pytest.fixture
def stack() -> FakeStack:
    return FakeStack()


@pytest.fixture
def jarvis(tmp_path, stack):
    box = Jarvis(tmp_path)
    box.config = deployed_config()
    box.data["llm_client"] = httpx.AsyncClient(transport=httpx.MockTransport(stack.handler))
    return box


def by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in payload["models"]}


# ---------------------------------------------------------------------------
# the deployed shape
# ---------------------------------------------------------------------------
async def test_the_gateway_aliases_resolve_to_the_models_behind_them(jarvis, stack) -> None:
    """`house` is not a model. The row is `qwen3.8-27b`, and it knows its alias."""
    payload = await common.async_llm_models_payload(jarvis)
    rows = by_id(payload)

    assert "house" not in rows and "house-fast" not in rows, "an alias was listed as a model"
    assert set(rows) == {
        "qwen3.8-27b", "qwen3.6-35b", "BAAI/bge-small-en-v1.5", "cross-encoder/ms-marco-MiniLM-L-6-v2",
    }
    chat = rows["qwen3.8-27b"]
    assert chat["aliases"] == ["house"]
    assert chat["choice"] == "house", "choosing it for chat has to write the name LLM_URL knows"
    assert chat["loaded"] is True
    assert chat["role"] == ROLE_CHAT
    assert "conversation" in chat["in_use_for"]
    assert "research" in chat["in_use_for"], "research: model: '' means the conversation model"
    assert "coding" in chat["in_use_for"]
    assert payload["gateway"] == {"url": GATEWAY, "aliases": {"house": "qwen3.8-27b", "house-fast": "qwen3.6-35b"}}
    assert payload["roles"]["chat"] == {"setting": "llm.model", "value": "house", "model": "qwen3.8-27b"}


async def test_the_fast_slot_is_configured_and_idle(jarvis) -> None:
    """The 35-B is `house-fast`, unloaded, and used by nothing. All three are said."""
    payload = await common.async_llm_models_payload(jarvis)
    fast = by_id(payload)["qwen3.6-35b"]
    assert fast["aliases"] == ["house-fast"]
    assert fast["loaded"] is False
    assert fast["role"] == ROLE_FAST
    assert fast["in_use_for"] == [], "nothing routes to the fast slot yet; saying so is the point"
    assert "idle" in fast["note"] and "house-fast" in fast["note"]
    assert payload["fast_available"] is True
    assert payload["roles"]["fast"] == {"setting": "llm.fast_model", "value": "", "model": "qwen3.6-35b", "source": "gateway"}


async def test_an_unloaded_model_is_never_asked_through_the_upstream_proxy(jarvis, stack) -> None:
    """`/upstream/<id>/…` is how llama-swap loads a model. Opening Settings must not."""
    await common.async_llm_models_payload(jarvis)
    assert stack.asked("/upstream/qwen3.8-27b/") , "the loaded model's own record was not read"
    assert not stack.asked("/upstream/qwen3.6-35b/"), "the settings page loaded the 35-B model"
    assert stack.loaded == {"qwen3.8-27b"}


async def test_what_the_server_says_beats_what_the_id_says(jarvis) -> None:
    """vLLM's `root` and `max_model_len` reach the row; the quant is read off the weights' name."""
    rows = by_id(await common.async_llm_models_payload(jarvis))
    chat = rows["qwen3.8-27b"]
    assert chat["context"] == 256000
    assert chat["description"] == "cyankiwi/Qwen3.8-27B-AWQ-INT4"
    assert chat["quant"] == "AWQ-INT4"
    assert chat["parameters"] == "27B"
    assert chat["family"] == "Qwen 3.8"
    assert chat["name"] == "Qwen 3.8 27B"
    # llama-swap gave an id and a status; the size came off the name. Say so.
    assert chat["described_by"] == "id"
    assert chat["note"] == "as named by the server"
    assert chat["kind"] == "llama-swap → vllm"


async def test_the_retrieval_services_are_rows_with_their_jobs(jarvis) -> None:
    rows = by_id(await common.async_llm_models_payload(jarvis))
    embed = rows["BAAI/bge-small-en-v1.5"]
    assert embed["role"] == ROLE_EMBEDDINGS and embed["loaded"] is True
    assert embed["in_use_for"] == ["embeddings"]
    assert embed["context"] == 512 and embed["kind"] == "tei"
    assert embed["choice"] is None, "an embedder is not something to run a conversation on"
    rerank = rows["cross-encoder/ms-marco-MiniLM-L-6-v2"]
    assert rerank["role"] == ROLE_RERANK and rerank["in_use_for"] == ["rerank"]
    assert rerank["family"] == "MiniLM"


async def test_the_gateway_is_asked_with_its_key(jarvis, stack) -> None:
    """Without the Authorization header LiteLLM answers 401 and the page is empty."""
    payload = await common.async_llm_models_payload(jarvis)
    assert payload["servers"][0] == {"url": GATEWAY, "kind": "litellm", "role": "chat", "ok": True, "error": "", "models": 2}
    gateway_calls = [r for r in stack.requests if r.url.host == "gateway"]
    assert gateway_calls and all(r.headers.get("authorization") == "Bearer sk-test" for r in gateway_calls)


async def test_models_are_ordered_by_role_then_loaded(jarvis) -> None:
    ids = [row["id"] for row in (await common.async_llm_models_payload(jarvis))["models"]]
    assert ids == ["qwen3.8-27b", "qwen3.6-35b", "BAAI/bge-small-en-v1.5", "cross-encoder/ms-marco-MiniLM-L-6-v2"]


async def test_the_shared_client_is_used_and_the_answer_is_kept(jarvis, stack) -> None:
    await common.async_llm_models_payload(jarvis)
    assert stack.requests, "the injected transport saw nothing — a client of its own was built"
    assert jarvis.data[common.DATA_MODEL_CATALOGUE]["models"]


# ---------------------------------------------------------------------------
# what Jarvis is set to
# ---------------------------------------------------------------------------
async def test_a_chosen_fast_model_is_resolved_through_the_gateway(jarvis) -> None:
    jarvis.config["llm"]["fast_model"] = "house-fast"
    payload = await common.async_llm_models_payload(jarvis)
    assert payload["roles"]["fast"]["source"] == "setting"
    assert payload["roles"]["fast"]["model"] == "qwen3.6-35b"
    # Chosen means used (M60): spoken turns go to it, and the row says so
    # rather than calling it idle.
    row = by_id(payload)["qwen3.6-35b"]
    assert "fast path" in row["in_use_for"]
    assert "idle" not in (row["note"] or "")


async def test_the_gateways_own_fast_alias_is_idle_until_chosen(jarvis) -> None:
    jarvis.config["llm"].pop("fast_model", None)
    payload = await common.async_llm_models_payload(jarvis)
    if payload["roles"]["fast"]["source"] != "gateway":
        return  # this stack offers no fast alias; nothing to be idle
    row = by_id(payload)[payload["roles"]["fast"]["model"]]
    assert "fast path" not in row["in_use_for"]
    assert "idle" in (row["note"] or "")


async def test_a_configured_model_the_server_does_not_list_is_shown_as_missing(jarvis) -> None:
    """The M40 failure: an `llm.model` from before the gateway renamed everything."""
    jarvis.config["llm"]["model"] = "qwen3:8b"
    rows = by_id(await common.async_llm_models_payload(jarvis))
    ghost = rows["qwen3:8b"]
    assert ghost["missing"] is True
    assert ghost["in_use_for"] == ["conversation", "research", "coding"]
    assert "no server lists" in ghost["note"]
    assert ghost["loaded"] is None
    # And it sorts last: the real models first, the problem at the bottom.
    assert [r for r in rows][-1] == "qwen3:8b"


async def test_a_vision_model_is_a_role_of_its_own(jarvis, stack) -> None:
    jarvis.config["vision"] = {"model": "qwen2.5vl:7b", "ollama_url": "http://elsewhere:11434", "cameras": []}
    payload = await common.async_llm_models_payload(jarvis)
    vision = by_id(payload)["qwen2.5vl:7b"]
    assert vision["role"] == ROLE_VISION
    assert vision["in_use_for"] == ["vision"]
    assert vision["family"] == "Qwen 2.5 VL" and vision["parameters"] == "7B"
    assert payload["roles"]["vision"] == {"setting": "vision.model", "value": "qwen2.5vl:7b", "model": "qwen2.5vl:7b", "configured": True}


async def test_without_vision_configured_the_role_says_so(jarvis) -> None:
    payload = await common.async_llm_models_payload(jarvis)
    assert payload["roles"]["vision"]["configured"] is False
    assert payload["roles"]["vision"]["model"] is None


async def test_a_named_research_model_takes_that_job_off_the_conversation_model(jarvis) -> None:
    jarvis.config["research"]["model"] = "house-fast"
    rows = by_id(await common.async_llm_models_payload(jarvis))
    assert "research" in rows["qwen3.6-35b"]["in_use_for"]
    assert "research" not in rows["qwen3.8-27b"]["in_use_for"]


# ---------------------------------------------------------------------------
# half-up stacks
# ---------------------------------------------------------------------------
async def test_a_server_that_is_down_is_a_reason_not_an_exception(jarvis, stack) -> None:
    stack.swap_up = False
    stack.embed_up = False
    payload = await common.async_llm_models_payload(jarvis)
    swap = next(s for s in payload["servers"] if s["url"] == SWAP)
    assert swap["ok"] is False and "ConnectError" in swap["error"]
    rows = by_id(payload)
    # The embedder is listed from the config, honest about not being reachable.
    embed = rows["BAAI/bge-small-en-v1.5"]
    assert embed["loaded"] is None and "did not answer" in embed["note"]
    # And the chat model is "missing" only in the sense that nothing listed it.
    assert rows["qwen3.8-27b"]["missing"] is True


async def test_the_wrong_gateway_key_is_a_reason_on_the_server_row(jarvis, stack) -> None:
    stack.gateway_key = "something-else"
    payload = await common.async_llm_models_payload(jarvis)
    front = payload["servers"][0]
    assert front["ok"] is False and "401" in front["error"]
    assert payload["gateway"] is None


async def test_a_plain_llama_cpp_server_reports_its_own_numbers(tmp_path) -> None:
    """No gateway, no swap: llama.cpp's `/v1/models` carries the GGUF's own meta."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{
                "id": "/models/gemma-3-27b-it-Q4_K_M.gguf", "object": "model", "owned_by": "llamacpp",
                "meta": {"vocab_type": 2, "n_vocab": 262144, "n_ctx_train": 131072, "n_embd": 5376,
                         "n_params": 27432406640, "size": 16546000000},
            }]})
        return httpx.Response(404)

    box = Jarvis(tmp_path)
    box.config = {"llm": {"url": "http://cpp:8081/v1", "model": "/models/gemma-3-27b-it-Q4_K_M.gguf"}}
    box.data["llm_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = await common.async_llm_models_payload(box)
    row = payload["models"][0]
    assert row["parameters"] == "27B" and row["described_by"] == "server"
    assert row["context"] == 131072 and row["size_bytes"] == 16546000000
    assert row["quant"] == "Q4_K_M" and row["family"] == "Gemma 3"
    assert row["loaded"] is True and row["kind"] == "llama.cpp"
    assert row["choice"] == row["id"], "no gateway: the id itself is what llm.model takes"
    assert row["in_use_for"] == ["conversation"]
    assert row["note"] == "", "the server described it; nothing was read off the name"
    assert payload["gateway"] is None and payload["fast_available"] is False


async def test_the_ollama_wire_is_read_from_tags_and_ps(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3:8b", "size": 5225000000,
                 "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"}},
                {"name": "qwen2.5vl:7b", "details": {"family": "qwen25vl", "parameter_size": "7.6B", "quantization_level": "Q4_K_M"}},
            ]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        return httpx.Response(404)

    box = Jarvis(tmp_path)
    box.config = {"llm": {"url": "http://ollama:11434", "model": "qwen3:8b"}, "vision": {"model": "qwen2.5vl:7b"}}
    box.data["llm_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rows = by_id(await common.async_llm_models_payload(box))
    chat = rows["qwen3:8b"]
    assert chat["loaded"] is True and chat["parameters"] == "8.2B" and chat["quant"] == "Q4_K_M"
    assert chat["family"] == "qwen3" and chat["described_by"] == "server"
    assert chat["kind"] == "ollama" and chat["choice"] == "qwen3:8b"
    vision = rows["qwen2.5vl:7b"]
    assert vision["loaded"] is False and vision["role"] == ROLE_VISION
    assert vision["in_use_for"] == ["vision"]


async def test_a_bare_openai_list_gives_bare_rows(tmp_path) -> None:
    """A server that lists ids and nothing else: no loaded flag, no numbers, no guesses."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "house-model", "object": "model"}]})
        return httpx.Response(404)

    box = Jarvis(tmp_path)
    box.config = {"llm": {"url": "http://server:8000/v1", "model": "house-model"}}
    box.data["llm_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = await common.async_llm_models_payload(box)
    row = payload["models"][0]
    assert row["loaded"] is None
    assert row["parameters"] == "" and row["quant"] == ""
    assert row["role"] == ROLE_CHAT, "a chat server's model with no other hint is a chat model"
    assert row["kind"] == "openai"


async def test_a_cloud_alias_behind_the_gateway_is_listed_as_not_local(jarvis, stack) -> None:
    original = stack.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gateway" and request.url.path == "/model/info":
            body = json.loads(original(request).content)
            body["data"].append({
                "model_name": "cloud-big",
                "litellm_params": {"model": "anthropic/claude-sonnet-4-5"},
                "model_info": {"locality": "cloud"},
            })
            return httpx.Response(200, json=body)
        return original(request)

    jarvis.data["llm_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = await common.async_llm_models_payload(jarvis)
    cloud = by_id(payload)["claude-sonnet-4-5"]
    assert cloud["kind"] == "litellm → anthropic"
    assert "not on this network" in cloud["note"]
    assert cloud["loaded"] is None
    assert not [r for r in stack.requests if r.url.host not in ("gateway", "swap", "embed", "rerank")], (
        "a cloud provider was asked something"
    )


async def test_without_a_shared_client_it_builds_and_closes_its_own(tmp_path, stack, monkeypatch) -> None:
    """The REST route can be hit before the llm integration has built a pool."""
    built: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def fake_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(stack.handler)
        client = real(*args, **kwargs)
        built.append(client)
        return client

    monkeypatch.setattr(catalogue.httpx, "AsyncClient", fake_client)
    box = Jarvis(tmp_path)
    box.config = deployed_config()
    payload = await async_describe(box)
    assert by_id(payload)["qwen3.8-27b"]["aliases"] == ["house"]
    assert len(built) == 1 and built[0].is_closed


# ---------------------------------------------------------------------------
# the settings that choose a role
# ---------------------------------------------------------------------------
class FakeAgent:
    def __init__(self) -> None:
        self.model = "house"
        self.fast_model = ""
        self.client = type("C", (), {"model": "house"})()


async def test_the_fast_model_setting_lands_on_the_running_agent(tmp_path) -> None:
    box = Jarvis(tmp_path)
    box.raw_config = {"llm": {"model": "house"}}
    agent = FakeAgent()
    box.data["llm"] = agent
    result = await common.async_set_setting(box, {"key": "llm.fast_model", "value": "house-fast"})
    assert agent.fast_model == "house-fast"
    assert agent.model == "house", "the fast slot must not move the conversation"
    assert result["applied"] is True
    # Empty means "the conversation model" and is a legal value.
    result = await common.async_set_setting(box, {"key": "llm.fast_model", "value": ""})
    assert result["value"] == "" and agent.fast_model == ""


async def test_the_vision_model_setting_reaches_the_analyser(tmp_path) -> None:
    import dataclasses

    @dataclasses.dataclass(frozen=True)
    class Cfg:
        model: str
        ollama_url: str = "http://127.0.0.1:11434"

    class Analyser:
        def __init__(self) -> None:
            self.config = Cfg(model="old:7b")
            self.ollama = type("O", (), {"model": "old:7b"})()

    class Manager:
        model = Analyser()

    box = Jarvis(tmp_path)
    box.raw_config = {"vision": {"model": "old:7b"}}
    box.data["vision"] = {"manager": Manager()}
    result = await common.async_set_setting(box, {"key": "vision.model", "value": "qwen2.5vl:7b"})
    assert Manager.model.config.model == "qwen2.5vl:7b"
    assert Manager.model.ollama.model == "qwen2.5vl:7b"
    assert result["applied"] is True


async def test_the_vision_model_setting_without_vision_says_it_needs_a_restart(tmp_path) -> None:
    box = Jarvis(tmp_path)
    box.raw_config = {}
    result = await common.async_set_setting(box, {"key": "vision.model", "value": "qwen2.5vl:7b"})
    assert result["applied"] is False and result["restart_required"] is True


def test_every_role_setting_the_catalogue_names_is_editable() -> None:
    """The console writes what the payload's `roles` names; a key not in the allowlist is a 404."""
    from jarvis.settings import SETTINGS_BY_KEY

    for key in catalogue.ROLE_SETTINGS.values():
        assert key in SETTINGS_BY_KEY, f"{key} is not an editable setting"
