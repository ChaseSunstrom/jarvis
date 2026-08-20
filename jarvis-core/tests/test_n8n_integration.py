"""The n8n integration: what the model may do, and at what price.

n8n is where Jarvis reaches other people's services — mail, spreadsheets,
invoices, payments — using credentials it is not allowed to see. So the tests
that matter are about the boundary rather than the plumbing:

* creating a workflow needs a human (Tier 3), and what arrives is switched OFF;
* Jarvis cannot switch one ON unless the operator opted in;
* no tool deletes anything, and no tool touches a credential;
* the API key is not in the listing, the tools, or an error message.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import n8n as n8n_integration  # noqa: E402
from jarvis.integrations.n8n import N8nConfig  # noqa: E402
from jarvis.integrations.n8n.client import N8nClient  # noqa: E402
from jarvis.llm.tools import TIER_APPROVAL, ToolRegistry  # noqa: E402

pytestmark = pytest.mark.asyncio

KEY = "n8n_api_0123456789abcdef"


async def make(tmp_path: Path, handler, **cfg: Any) -> tuple[Jarvis, ToolRegistry]:
    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    await n8n_integration.async_setup(
        jarvis, {"url": "http://n8n.lan:5678", "api_key": KEY, **cfg}
    )
    # The real client, with the instance faked underneath it.
    jarvis.data["n8n"]["client"] = N8nClient(
        "http://n8n.lan:5678", KEY, transport=httpx.MockTransport(handler)
    )
    return jarvis, registry


def nothing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": []})


WORKFLOW = {
    "name": "File the receipt",
    "nodes": [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "position": [0, 0]},
        {
            "name": "Gmail",
            "type": "n8n-nodes-base.gmail",
            "position": [200, 0],
            "credentials": {"gmailOAuth2": {"id": "5", "name": "guessed"}},
        },
    ],
    "connections": {
        "Webhook": {"main": [[{"node": "Gmail", "type": "main", "index": 0}]]}
    },
}


# ---------------------------------------------------------------------------
# the tiers
# ---------------------------------------------------------------------------
async def test_writing_a_workflow_needs_a_human(tmp_path: Path):
    """Not because creating is destructive — it is not. A workflow is a
    program that will run against somebody's email and somebody's money as
    soon as it is switched on."""
    _jarvis, registry = await make(tmp_path, nothing)
    tool = registry.get("create_n8n_workflow")
    assert tool is not None
    assert tool.tier >= TIER_APPROVAL
    assert registry.requires_approval(tool, {}) is True


async def test_reading_does_not(tmp_path: Path):
    """A listing that needed approval would make the tool useless — the model
    calls it to avoid duplicating a workflow that already exists."""
    _jarvis, registry = await make(tmp_path, nothing)
    for name in ("list_n8n_workflows", "read_n8n_workflow"):
        tool = registry.get(name)
        assert tool is not None, name
        assert registry.requires_approval(tool, {}) is False, name


async def test_switching_one_off_still_needs_a_human(tmp_path: Path):
    """The safe direction, but it still stops something the house may rely on."""
    _jarvis, registry = await make(tmp_path, nothing)
    tool = registry.get("deactivate_n8n_workflow")
    assert tool is not None and tool.tier >= TIER_APPROVAL


async def test_jarvis_cannot_switch_a_workflow_on_by_default(tmp_path: Path):
    """Activation is the moment a workflow becomes live, and one Jarvis wrote
    usually cannot work until a human has attached the credentials anyway."""
    _jarvis, registry = await make(tmp_path, nothing)
    assert registry.get("activate_n8n_workflow") is None


async def test_an_operator_can_opt_into_activation(tmp_path: Path):
    _jarvis, registry = await make(tmp_path, nothing, allow_activate=True)
    tool = registry.get("activate_n8n_workflow")
    assert tool is not None and tool.tier >= TIER_APPROVAL


async def test_activation_is_refused_in_the_service_layer_too(tmp_path: Path):
    """Withholding a tool is not a control: a model can name one it was never
    offered, and the recovery path in llm/toolcalls.py turns narrated calls
    into real ones without knowing what was withheld."""
    jarvis, _registry = await make(tmp_path, nothing)
    ok, note = await n8n_integration.async_set_active(jarvis, "7", True)
    assert ok is False
    assert "allow_activate" in note


async def test_there_is_no_tool_that_deletes_anything(tmp_path: Path):
    """Same rule as repositories: Jarvis does not delete somebody's work."""
    _jarvis, registry = await make(tmp_path, nothing, allow_activate=True)
    mine = [name for name in registry.names() if "n8n" in name]
    assert mine, "the n8n tools are not registered at all"
    assert not [n for n in mine if "delete" in n or "remove" in n], mine


async def test_no_tool_touches_a_credential(tmp_path: Path):
    """Creating one would mean the model handling somebody's secret, and
    reading one would mean it leaving n8n at all."""
    _jarvis, registry = await make(tmp_path, nothing, allow_activate=True)
    mine = [name for name in registry.names() if "n8n" in name]
    assert not [n for n in mine if "credential" in n or "connection" in n], mine


# ---------------------------------------------------------------------------
# what actually gets sent
# ---------------------------------------------------------------------------
async def test_a_created_workflow_arrives_switched_off_and_uncredentialed(
    tmp_path: Path,
):
    """The two properties the whole design rests on, checked on the wire."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        if "/tags" in request.url.path:
            return httpx.Response(200, json={"data": [{"id": "t1", "name": "jarvis"}]})
        sent.update(_json.loads(request.content))
        return httpx.Response(200, json={"id": "wf-1", "name": sent.get("name")})

    jarvis, _registry = await make(tmp_path, handler)
    result, why = await n8n_integration.async_create(jarvis, WORKFLOW)

    assert result is not None, why
    assert "active" not in sent, "a workflow was sent with an `active` flag"
    assert all("credentials" not in node for node in sent["nodes"]), (
        "a guessed credential id was sent to n8n"
    )
    assert result["active"] is False


async def test_the_configured_tag_is_actually_applied(tmp_path: Path):
    """`tag:` used to be a sentence in the tool result asking the MODEL to ask
    the USER to tag it. That is not what the config option says it does, and
    "what did the assistant write" is only a filter in n8n if it is true.

    n8n takes tags in a separate call — they are read-only on the workflow —
    as a list of ids, so the tag has to be found or made first.
    """
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        path = request.url.path
        seen.append((request.method, path))
        if path.endswith("/api/v1/tags") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path.endswith("/api/v1/tags") and request.method == "POST":
            assert _json.loads(request.content)["name"] == "jarvis"
            return httpx.Response(200, json={"id": "t9", "name": "jarvis"})
        if path.endswith("/tags") and request.method == "PUT":
            assert _json.loads(request.content) == [{"id": "t9"}]
            return httpx.Response(200, json=[{"id": "t9", "name": "jarvis"}])
        return httpx.Response(200, json={"id": "wf-1", "name": "x"})

    jarvis, _registry = await make(tmp_path, handler)
    result, why = await n8n_integration.async_create(jarvis, WORKFLOW)

    assert result is not None, why
    assert result["tagged"] is True
    assert ("PUT", "/api/v1/workflows/wf-1/tags") in seen


async def test_a_workflow_is_still_reported_when_the_tag_will_not_stick(tmp_path: Path):
    """The workflow is already written. Refusing to report a successful create
    because a label did not apply would be the wrong trade."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/tags" in request.url.path:
            return httpx.Response(500, json={"message": "no"})
        return httpx.Response(200, json={"id": "wf-1", "name": "x"})

    jarvis, _registry = await make(tmp_path, handler)
    result, why = await n8n_integration.async_create(jarvis, WORKFLOW)
    assert result is not None, why
    assert result["tagged"] is False


async def test_the_result_says_what_to_connect_and_where(tmp_path: Path):
    """This is "ask for connections": Jarvis says what it needs, a person
    attaches it in n8n, where the secrets already live."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "wf-1"})

    jarvis, registry = await make(tmp_path, handler)
    tool = registry.get("create_n8n_workflow")
    result = await tool.handler({"workflow": WORKFLOW})

    assert result["status"] == "ok"
    assert result["connections_needed"] == [
        {"node": "Gmail", "credential_type": "gmailOAuth2"}
    ]
    assert "switched OFF" in result["message"]
    assert "gmailOAuth2" in result["message"] and "Gmail" in result["message"]


async def test_a_workflow_with_nothing_to_connect_says_so_plainly(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "wf-2"})

    jarvis, registry = await make(tmp_path, handler)
    plain = {
        "name": "Just a webhook",
        "nodes": [{"name": "Webhook", "type": "n8n-nodes-base.webhook", "position": [0, 0]}],
        "connections": {},
    }
    result = await registry.get("create_n8n_workflow").handler({"workflow": plain})
    assert result["connections_needed"] == []
    assert "activate it there" in result["message"]


async def test_a_broken_workflow_comes_back_as_a_sentence_not_a_crash(tmp_path: Path):
    """The model has to be able to read the refusal and fix it."""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a broken workflow reached the instance")

    jarvis, registry = await make(tmp_path, handler)
    result = await registry.get("create_n8n_workflow").handler(
        {"workflow": {"name": "x", "nodes": [{"name": "a", "type": "t"}, {"name": "a", "type": "t"}]}}
    )
    assert result["status"] == "error"
    assert "unique" in result["error"]


# ---------------------------------------------------------------------------
# the key, and the unconfigured case
# ---------------------------------------------------------------------------
async def test_the_listing_never_carries_the_key(tmp_path: Path):
    jarvis, _registry = await make(tmp_path, nothing)
    payload = n8n_integration.listing_payload(jarvis)
    assert KEY not in str(payload)
    assert payload["instance"]["has_key"] is True
    assert payload["instance"]["url"] == "http://n8n.lan:5678"


async def test_an_unconfigured_server_says_which_key_to_set(tmp_path: Path):
    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    await n8n_integration.async_setup(jarvis, {})

    result = await registry.get("list_n8n_workflows").handler({})
    assert result["status"] == "error"
    assert "n8n: url:" in result["error"]


async def test_the_tools_exist_even_when_nothing_is_configured(tmp_path: Path):
    """So the model answers "point me at an n8n" rather than "I cannot do
    that", which is the difference between a setup step and a dead end."""
    jarvis = Jarvis(tmp_path)
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    await n8n_integration.async_setup(jarvis, {})
    assert registry.get("create_n8n_workflow") is not None


def test_the_config_reads_a_url_and_a_key():
    cfg = N8nConfig.from_config(
        {"url": "http://x:5678/", "api_key": "k", "allow_activate": True}
    )
    assert cfg.url == "http://x:5678"  # trailing slash trimmed
    assert cfg.api_key == "k"
    assert cfg.allow_activate is True
    assert cfg.configured is True


def test_an_empty_config_is_not_configured():
    assert N8nConfig.from_config({}).configured is False
    assert N8nConfig.from_config(None).configured is False


# ---------------------------------------------------------------------------
# the API surface the console talks to
# ---------------------------------------------------------------------------
async def test_the_console_can_list_workflows(tmp_path: Path):
    from jarvis.api import common

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": "1", "name": "Nightly", "active": True, "nodes": []}]}
        )

    jarvis, _registry = await make(tmp_path, handler)
    payload = await common.async_n8n_workflows(jarvis, {})
    assert payload["workflows"][0]["name"] == "Nightly"
    assert payload["instance"]["has_key"] is True
    assert KEY not in str(payload)


async def test_an_unreachable_instance_is_a_502_and_not_a_traceback(tmp_path: Path):
    from jarvis.api import common
    from jarvis.api.common import ApiError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    jarvis, _registry = await make(tmp_path, handler)
    with pytest.raises(ApiError) as caught:
        await common.async_n8n_workflows(jarvis, {})
    assert caught.value.status == 502


async def test_the_console_may_activate_even_when_the_model_may_not(tmp_path: Path):
    """`allow_activate` is about what Jarvis does on its own. A person pressing
    a button in the console IS the human that flag exists to insist on."""
    from jarvis.api import common

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    jarvis, _registry = await make(tmp_path, handler, allow_activate=False)
    result = await common.async_n8n_set_active(jarvis, {"id": "7", "active": True})
    assert result == {"id": "7", "active": True}
    assert seen == ["/api/v1/workflows/7/activate"]


async def test_the_check_command_reports_rather_than_raises(tmp_path: Path):
    from jarvis.api import common

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    jarvis, _registry = await make(tmp_path, handler)
    result = await common.async_n8n_check(jarvis)
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_every_n8n_command_is_reachable_over_the_socket():
    """A command implemented in `common` and not routed is a feature the
    console cannot use — the exact gap `jarvis/tools/call` had."""
    from jarvis.api.websocket import WebSocketHandler

    for name in (
        "jarvis/n8n/list",
        "jarvis/n8n/workflow",
        "jarvis/n8n/check",
        "jarvis/n8n/set_active",
        "jarvis/n8n/executions",
    ):
        assert name in WebSocketHandler._HANDLERS, name


async def test_the_workflow_id_is_not_called_id_on_the_wire(tmp_path: Path):
    """The trap the console client already carried a comment about.

    `command()` in jarvis-web stamps the RPC id onto the frame LAST, so a
    payload key called `id` is overwritten and the server is handed a sequence
    number instead of a workflow id. It cost a page that expanded a row and
    loaded for ever; the field is `workflow_id` on all three commands.
    """
    from jarvis.api import common

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"id": "wf-1", "nodes": [], "connections": {}})

    jarvis, _registry = await make(tmp_path, handler)

    # What the websocket layer actually passes through.
    await common.async_n8n_workflow(jarvis, "wf-1")
    assert seen == ["/api/v1/workflows/wf-1"]

    seen.clear()
    await common.async_n8n_set_active(jarvis, {"workflow_id": "wf-1", "active": False})
    assert seen == ["/api/v1/workflows/wf-1/deactivate"]


def test_the_socket_handler_reads_workflow_id_and_not_id():
    """Read out of the source, because the failure is silent: `msg["id"]` is
    always present and always wrong."""
    import inspect

    from jarvis.api.websocket import WebSocketHandler

    source = inspect.getsource(WebSocketHandler._cmd_n8n_workflow)
    assert 'msg.get("workflow_id")' in source
    assert 'msg.get("id")' not in source


async def test_an_automation_cannot_write_a_workflow_unattended(tmp_path: Path):
    """The hole a tier on the TOOL does not close.

    `create_n8n_workflow` is Tier 3, but the same verb exists as the service
    `n8n.create`, and an automation calling a service does not go through the
    tool layer at all. `GATED_SERVICES` is what holds it — checked here
    against the real constant rather than against the table that documents it.
    """
    from jarvis.const import GATED_SERVICES

    assert "n8n.create" in GATED_SERVICES
    assert "n8n.set_active" in GATED_SERVICES


async def test_reading_n8n_is_not_gated(tmp_path: Path):
    """A gate on the read services would hold an automation every time it
    asked which workflows exist, which is a confirmation nobody can act on."""
    from jarvis.const import GATED_SERVICES

    for service in ("n8n.list", "n8n.get", "n8n.executions", "n8n.check"):
        assert service not in GATED_SERVICES, service
