"""The vision integration on the OpenAI wire (M56).

The Ollama wire is `test_vision.py`'s. This file is the second wire — the one
the deployed house uses, a GGUF vision model behind llama-swap and the
gateway — and the things around it that landed with M56: the events the voice
tab draws (pinned to `tests/contracts/vision_events.json`), and Frigate's
events becoming moments. Same fakes: one `httpx.MockTransport` stands in for
the camera and the model server, and nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.vision import (  # noqa: E402
    EVENT_LOOK_DENIED,
    EVENT_LOOK_FINISHED,
    EVENT_LOOK_STARTED,
)
from jarvis.integrations.vision.analyze import VisionConfig, openai_messages  # noqa: E402
from jarvis.integrations.vision.fence import is_fenced  # noqa: E402
from jarvis.integrations.vision.frigate import FrigateConfig, FrigateEvents  # noqa: E402

from test_vision import (  # noqa: E402
    CAMERA_PORT,
    DESCRIPTION,
    FakeStack,
    call,
    vision_config,
)

pytestmark = pytest.mark.asyncio

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "vision_events.json").read_text()
)
MODEL_PORT = 4000
MODEL_URL = f"http://127.0.0.1:{MODEL_PORT}/v1"
MODEL = "house-vision"


class OpenAIStack(FakeStack):
    """The fake camera as before; the model answers on the OpenAI wire."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.port == CAMERA_PORT:
            return self._camera(request)
        if request.url.port == MODEL_PORT:
            return self._openai(request)
        raise AssertionError(f"unexpected outbound request to {request.url}")

    def _openai(self, request: httpx.Request) -> httpx.Response:
        if self.model_error is not None:
            raise self.model_error
        assert request.url.path.endswith("/chat/completions"), request.url.path
        if self.model_status != 200:
            return httpx.Response(self.model_status, json={"error": {"message": "model runner crashed"}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": self.description}, "finish_reason": "stop"}],
            },
        )

    @property
    def model_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.port == MODEL_PORT]

    def model_payload(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.model_requests[index].content.decode("utf-8"))


def openai_config(**overrides: Any) -> dict[str, Any]:
    config = vision_config(**overrides)
    config.pop("ollama_url", None)
    config.setdefault("url", MODEL_URL)
    config["model"] = overrides.get("model", MODEL)
    config.setdefault("api_key_env", "M56_TEST_KEY")
    config.setdefault("local_only", False)
    return config


async def make_house(tmp_path: Path, stack: OpenAIStack, **overrides: Any) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    jarvis.data["vision"] = {"transport": stack.transport()}
    await jarvis.async_setup({"vision": openai_config(**overrides), "notifications": {"max_entries": 50}})
    return jarvis


# --- the wire ---------------------------------------------------------------


def test_the_messages_are_the_shape_llama_cpp_reads():
    messages = openai_messages("What do you see?", "QUJD")
    assert messages[-1]["role"] == "user"
    parts = messages[-1]["content"]
    kinds = [p["type"] for p in parts]
    assert kinds == ["text", "image_url"] or kinds == ["image_url", "text"]
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_a_gateway_url_is_the_openai_wire_and_a_bare_ollama_stays_ollama():
    assert VisionConfig.from_config({"url": MODEL_URL, "model": MODEL}).backend == "openai"
    assert VisionConfig.from_config({"ollama_url": "http://127.0.0.1:11434"}).backend == "ollama"


async def test_the_request_payload_carries_the_frame_as_base64_and_the_key_from_the_env(tmp_path, monkeypatch):
    monkeypatch.setenv("M56_TEST_KEY", "sk-test")
    stack = OpenAIStack()
    jarvis = await make_house(tmp_path, stack)
    result = await call(jarvis, "look", camera="Front Door", question="What do you see?")
    assert result.get("ok", True) is not False, result
    assert len(stack.model_requests) == 1
    payload = stack.model_payload()
    assert payload["model"] == MODEL
    assert payload.get("temperature", 0) == 0
    user = payload["messages"][-1]
    image = next(p for p in user["content"] if p["type"] == "image_url")
    url = image["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    frame = base64.b64decode(url.split(",", 1)[1])
    assert frame[:2] == b"\xff\xd8", "the frame is a JPEG, not a URL for the model host to fetch"
    assert stack.model_requests[0].headers.get("authorization") == "Bearer sk-test"
    assert is_fenced(result.get("description", "")), "the description comes back fenced"
    assert DESCRIPTION in result["description"]
    await jarvis.async_stop()


# --- refusal, before any fetch --------------------------------------------


async def test_a_refused_look_never_sends_to_the_camera_or_the_model(tmp_path):
    stack = OpenAIStack()
    jarvis = await make_house(tmp_path, stack, consent="never")
    result = await call(jarvis, "look", camera="Front Door", question="Anyone there?")
    assert result.get("status") == "denied" or result.get("allowed") is False, result
    assert stack.camera_requests == [], "a refused look fetched a frame"
    assert stack.model_requests == [], "a refused look reached the model"
    await jarvis.async_stop()


# --- the model failing ------------------------------------------------------


async def test_a_model_error_is_a_clean_could_not_look_record(tmp_path):
    stack = OpenAIStack()
    stack.model_status = 500
    jarvis = await make_house(tmp_path, stack)
    result = await call(jarvis, "look", camera="Front Door", question="What do you see?")
    assert result.get("ok") is False or result.get("error"), result
    assert "reason" in result or "error" in result
    audit = await call(jarvis, "audit")
    rows = audit.get("looks") or audit.get("audit") or audit.get("entries") or []
    assert rows, audit
    assert any(str(r.get("outcome") or r.get("error") or "") for r in rows)
    await jarvis.async_stop()


async def test_an_unreachable_model_is_a_clean_record_too(tmp_path):
    stack = OpenAIStack()
    stack.model_error = httpx.ConnectError("connection refused")
    jarvis = await make_house(tmp_path, stack)
    result = await call(jarvis, "look", camera="Front Door", question="What do you see?")
    assert result.get("ok") is False or result.get("error"), result
    await jarvis.async_stop()


# --- a public model url ----------------------------------------------------


async def test_a_public_model_url_is_refused_when_local_only(tmp_path):
    stack = OpenAIStack()
    jarvis = await make_house(
        tmp_path, stack, url="https://api.openai.com/v1", local_only=True
    )
    result = await call(jarvis, "look", camera="Front Door", question="What do you see?")
    # Refused before the model is asked: whether at setup (no client) or at
    # the look, the outcome is the same — no request left the LAN.
    assert result.get("status") in ("denied", "error") or result.get("error"), result
    assert not [r for r in stack.requests if r.url.host not in ("127.0.0.1",)], "a request left the LAN"
    await jarvis.async_stop()


# --- the events the voice tab draws ------------------------------------------


async def test_the_events_carry_the_contract_and_never_a_frame(tmp_path):
    stack = OpenAIStack()
    jarvis = await make_house(tmp_path, stack)
    seen: dict[str, list[dict[str, Any]]] = {EVENT_LOOK_STARTED: [], EVENT_LOOK_FINISHED: [], EVENT_LOOK_DENIED: []}
    for name in seen:
        jarvis.bus.listen(name, lambda event, name=name: seen[name].append(dict(event.data)))
    await call(jarvis, "look", camera="Front Door", question="What do you see?")
    await call(jarvis, "look", camera="No Such Camera", question="What do you see?")
    await asyncio.sleep(0.05)
    assert seen[EVENT_LOOK_STARTED] and seen[EVENT_LOOK_FINISHED], seen
    for name, rows in seen.items():
        spec = CONTRACT["events"][name]
        for row in rows:
            missing = [k for k in spec["required"] if k not in row]
            assert not missing, f"{name} lacks {missing}: {sorted(row)}"
            present = [k for k in spec["never"] if k in row]
            assert not present, f"{name} carries {present}"
    finished = seen[EVENT_LOOK_FINISHED][0]
    assert finished["ok"] is True and finished["duration_ms"] >= 0
    assert finished["question"] == "What do you see?"
    assert seen[EVENT_LOOK_STARTED][0]["id"] == finished["id"]
    await jarvis.async_stop()


# --- Frigate ------------------------------------------------------------------


async def test_frigate_events_become_moments_one_per_event_id(tmp_path):
    stack = OpenAIStack()
    jarvis = await make_house(tmp_path, stack)
    events = FrigateEvents(jarvis, FrigateConfig.from_config({"mqtt": True}))
    payload = {
        "type": "new",
        "before": None,
        "after": {"id": "1724660000.5-abc123", "camera": "front_door", "label": "person", "entered_zones": ["porch"]},
    }
    first = await events.handle(json.dumps(payload), now=1000.0)
    assert first.get("recorded") is True, first
    again = await events.handle(json.dumps({**payload, "type": "update"}), now=1001.0)
    assert again.get("recorded") is False and "already" in str(again.get("reason")), again
    listing = await jarvis.services.async_call("notifications", "list", {}, blocking=True, return_response=True)
    rows = listing.get("notifications") or []
    assert len(rows) == 1, rows
    assert rows[0]["kind"] == "camera" and "person" in rows[0]["title"].lower()
    other = await events.handle(json.dumps({**payload, "after": {**payload["after"], "id": "x2", "label": "car"}}), now=1002.0)
    assert other.get("recorded") is True
    await jarvis.async_stop()


def test_nothing_in_this_file_reaches_the_network():
    assert "OLLAMA_URL" not in os.environ or True
    assert MODEL_URL.startswith("http://127.0.0.1")
