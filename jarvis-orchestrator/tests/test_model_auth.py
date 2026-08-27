"""The orchestrator talks to the house's model server the way jarvis-core does.

Until 27 Aug 2026 the compose file handed it OLLAMA_URL and nothing else: the
container pointed at 127.0.0.1:11434 (nothing there), sent no key, and every
delegation failed while /healthz said ok. These pin the wire: a bearer when a
key is set, none when it is not, and a health payload that names the target.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.fanout import fan_out  # noqa: E402


def _server(seen: list[dict[str, str]]):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})
    return handle


def test_a_key_becomes_a_bearer_on_every_model_call(monkeypatch):
    seen: list[dict[str, str]] = []
    transport = httpx.MockTransport(_server(seen))
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    result = asyncio.run(fan_out(["one", "two"], "http://gateway/v1", "house", api_key="k-1"))
    assert result
    assert seen, "no request reached the model"
    assert {h.get("authorization") for h in seen} == {"Bearer k-1"}


def test_no_key_means_no_header(monkeypatch):
    seen: list[dict[str, str]] = []
    transport = httpx.MockTransport(_server(seen))
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, transport=transport, **k))
    asyncio.run(fan_out(["one"], "http://gateway/v1", "house"))
    assert seen and all("authorization" not in h for h in seen)


def test_healthz_names_the_model_server_and_never_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TOKEN", "t")
    monkeypatch.setenv("APPROVAL_SECRET", "s")
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    monkeypatch.setenv("LLM_URL", "http://gateway:4000/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    monkeypatch.setenv("PLANNER_MODEL", "house")
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as c:
        body = c.get("/healthz").json()
    assert body["llm_url"] == "http://gateway:4000/v1"
    assert body["planner_model"] == "house"
    assert body["llm_key_set"] is True
    assert "secret-key" not in str(body)
