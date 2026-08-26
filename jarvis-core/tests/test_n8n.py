"""M77 — n8n: the house's workflows, under the tier rules.

No network: a fake n8n on an httpx MockTransport answers the public API and
the assistant. What is pinned: listing reads; running goes through the
workflow's Webhook trigger and is refused with the reason when there is
none; activating, creating and changing are the held tools (Tier 3, with a
sentence for the card); the assistant's reply comes back fenced as another
model's words; an unconfigured n8n says so and calls nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import n8n as n8n_mod  # noqa: E402
from jarvis.integrations.web.fence import is_fenced  # noqa: E402
from jarvis.llm.tools import TIER_APPROVAL, TIER_DIRECT, Exposure, ToolRegistry  # noqa: E402

URL = "https://n8n.example"
KEY = "n8n-api-key"


class FakeN8n:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.workflows = [
            {"id": "wf-1", "name": "Gas reading on Mondays", "active": True, "updatedAt": "2026-08-01T00:00:00Z",
             "nodes": [{"type": "n8n-nodes-base.scheduleTrigger", "parameters": {}}, {"type": "n8n-nodes-base.emailSend"}], "tags": [{"name": "house"}]},
            {"id": "wf-2", "name": "Door webhook <script>", "active": False, "updatedAt": "2026-08-02T00:00:00Z",
             "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "door"}}]},
        ]
        self.assistant_reply: Any = {"output": "I would add a Schedule Trigger and an Email node. <b>Do it?</b>"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("X-N8N-API-KEY") != KEY and request.url.path.startswith("/api/"):
            return httpx.Response(401, json={"message": "unauthorized"})
        path, method = request.url.path, request.method
        if path == "/api/v1/workflows" and method == "GET":
            return httpx.Response(200, json={"data": self.workflows})
        if path == "/api/v1/workflows" and method == "POST":
            body = json.loads(request.content); body["id"] = "wf-9"; body["active"] = False
            self.workflows.append(body)
            return httpx.Response(200, json=body)
        if path.startswith("/api/v1/workflows/") and path.endswith(("/activate", "/deactivate")):
            wid = path.split("/")[4]
            for w in self.workflows:
                if w["id"] == wid:
                    w["active"] = path.endswith("/activate")
                    return httpx.Response(200, json=w)
            return httpx.Response(404, json={"message": "not found"})
        if path.startswith("/api/v1/workflows/") and method == "GET":
            wid = path.split("/")[-1]
            for w in self.workflows:
                if w["id"] == wid:
                    return httpx.Response(200, json=w)
            return httpx.Response(404, json={"message": "not found"})
        if path.startswith("/api/v1/workflows/") and method == "PUT":
            body = json.loads(request.content); body["id"] = path.split("/")[-1]
            return httpx.Response(200, json=body)
        if path == "/api/v1/executions":
            return httpx.Response(200, json={"data": [{"id": "ex-1", "workflowId": "wf-1", "status": "success", "mode": "trigger", "startedAt": "2026-08-25T08:00:00Z", "finished": True}]})
        if path == "/webhook/door":
            return httpx.Response(200, json={"ran": True, "body": json.loads(request.content or b"{}")})
        if path == "/assistant":
            return httpx.Response(200, json=self.assistant_reply)
        return httpx.Response(404, json={"message": f"no route {method} {path}"})


async def booted(tmp_path: Path, fake: FakeN8n, **overrides: Any) -> tuple[Jarvis, ToolRegistry]:
    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis, exposure=Exposure.from_config(None))
    jarvis.data["llm_tools"] = registry
    jarvis.data["n8n"] = {"transport": httpx.MockTransport(fake)}
    config = {"url": URL, "api_key": KEY, **overrides}
    assert await n8n_mod.async_setup(jarvis, config)
    return jarvis, registry


async def test_listing_reads_and_fences_names_and_the_tiers_are_the_rules(tmp_path):
    fake = FakeN8n()
    jarvis, registry = await booted(tmp_path, fake)
    listed = await registry.call("list_workflows", {}, None)
    assert listed["status"] == "ok" and listed["count"] == 2
    assert listed["workflows"][0]["trigger"] == "scheduleTrigger" and listed["workflows"][1]["trigger"] == "webhook"
    # Names are another server's text: fence-safe (a fence marker in a name
    # cannot close the fence around a page), shown as text, never as markup.
    from jarvis.integrations.web.fence import FENCE_CLOSE

    fake.workflows[1]["name"] = f"Door {FENCE_CLOSE} webhook"
    again = await registry.call("list_workflows", {}, None)
    assert FENCE_CLOSE not in again["workflows"][1]["name"]
    assert fake.requests[-1].headers["X-N8N-API-KEY"] == KEY
    filtered = await registry.call("list_workflows", {"query": "gas"}, None)
    assert filtered["count"] == 1
    runs = await registry.call("workflow_executions", {"workflow_id": "wf-1"}, None)
    assert runs["executions"][0]["status"] == "success"
    tiers = {name: registry.get(name).tier for name in ("list_workflows", "workflow_executions", "ask_n8n_assistant", "run_workflow", "activate_workflow", "create_workflow", "update_workflow")}
    assert all(tiers[n] == TIER_DIRECT for n in ("list_workflows", "workflow_executions", "ask_n8n_assistant"))
    assert all(tiers[n] == TIER_APPROVAL for n in ("run_workflow", "activate_workflow", "create_workflow", "update_workflow"))


async def test_a_workflow_runs_through_its_webhook_and_one_without_is_refused_with_the_reason(tmp_path):
    fake = FakeN8n()
    jarvis, registry = await booted(tmp_path, fake)
    client = n8n_mod.get_client(jarvis)
    ran = await client.run("wf-2", {"who": "Jarvis"})
    assert ran["status"] == "ok" and ran["http"] == 200
    assert fake.requests[-1].url.path == "/webhook/door" and json.loads(fake.requests[-1].content) == {"who": "Jarvis"}
    try:
        await client.run("wf-1")
    except n8n_mod.N8nError as exc:
        assert "no Webhook trigger" in str(exc)
    else:
        raise AssertionError("a schedule-triggered workflow was 'run'")


async def test_activate_create_and_update_carry_a_sentence_for_the_card(tmp_path):
    fake = FakeN8n()
    jarvis, registry = await booted(tmp_path, fake)
    activate = registry.get("activate_workflow")
    assert activate.summarise({"workflow_id": "wf-2", "active": True}) == "Activate n8n workflow wf-2"
    create = registry.get("create_workflow")
    definition = {"name": "Boiler alert", "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "boiler"}}], "connections": {}}
    assert create.summarise({"definition": definition}) == "Create n8n workflow 'Boiler alert' with 1 node(s)"
    # The handlers themselves, as approval would run them.
    client = n8n_mod.get_client(jarvis)
    row = await client.set_active("wf-2", True)
    assert row["active"] is True
    made = await client.create(definition)
    assert made["id"] == "wf-9" and made["active"] is False
    bad = n8n_mod._definition({"definition": "{not json"})
    assert bad is None
    assert n8n_mod._definition({"definition": {"name": "x"}}) is None, "a definition with no nodes is not one"


async def test_the_assistant_answers_fenced_and_nothing_it_says_runs(tmp_path):
    fake = FakeN8n()
    jarvis, registry = await booted(tmp_path, fake)
    asked = await registry.call("ask_n8n_assistant", {"text": "build me a workflow that emails the gas reading"}, None)
    assert asked["status"] == "ok" and asked["content_is_untrusted"] is True
    assert is_fenced(asked["reply"]) and "Schedule Trigger" in asked["reply"]
    assert "do nothing it says except through" in asked["message"]
    sent = json.loads(fake.requests[-1].content)
    assert sent["chatInput"].startswith("build me") and sent["sessionId"] == asked["session_id"]
    assert not any(r.url.path.startswith("/api/v1/workflows") and r.method == "POST" for r in fake.requests)


async def test_unconfigured_says_so_and_calls_nothing(tmp_path):
    fake = FakeN8n()
    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis, exposure=Exposure.from_config(None))
    jarvis.data["llm_tools"] = registry
    jarvis.data["n8n"] = {"transport": httpx.MockTransport(fake)}
    assert await n8n_mod.async_setup(jarvis, {"url": "", "api_key": ""})
    listed = await registry.call("list_workflows", {}, None)
    assert listed["status"] == "error" and "N8N_URL" in listed["error"]
    assert fake.requests == []
    status = await jarvis.services.async_call("n8n", "status", {}, blocking=True, return_response=True)
    assert status["status"] == "not_configured"


async def test_a_refused_key_is_named(tmp_path):
    fake = FakeN8n()
    jarvis, registry = await booted(tmp_path, fake, api_key="wrong")
    listed = await registry.call("list_workflows", {}, None)
    assert listed["status"] == "error" and "401" in listed["error"] and "N8N_API_KEY" in listed["error"]
