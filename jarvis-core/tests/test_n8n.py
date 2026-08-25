"""The n8n bridge: three refusals, and one worked example.

The whole point of this integration is what it will NOT do — run before it is
switched on, run something nobody listed, or run anything at Tier 1 by
accident. So that is most of what is tested, and the happy path is one case at
the end.
"""

from __future__ import annotations

import pytest

from jarvis.integrations.n8n import N8nError, Workflow, build


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text="") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.sent: list[dict] = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "json": json, "headers": headers or {}})
        return self.answers.pop(0) if self.answers else FakeResponse({"ok": True})


CONFIG = {
    "enabled": True,
    "url": "http://n8n.tail:5678",
    "api_key": "k3y",
    "workflows": [
        {"name": "bins", "webhook": "bins-out", "description": "put the bins out"},
        {"name": "meter", "webhook": "meter", "tier": 1},
        {"name": "unreachable"},
    ],
}


def test_the_default_is_off_and_off_means_off():
    """An install that never touches this file gets no bridge at all."""
    bridge = build({})
    assert bridge.enabled is False
    assert bridge.workflows == []


@pytest.mark.asyncio
async def test_a_workflow_cannot_run_while_the_bridge_is_off():
    bridge = build({**CONFIG, "enabled": False})
    bridge.client = FakeHttp()
    with pytest.raises(N8nError) as err:
        await bridge.run("bins")
    assert "enabled: true" in str(err.value)
    assert bridge.client.sent == [], "it reached n8n while switched off"


@pytest.mark.asyncio
async def test_something_nobody_listed_is_refused_and_the_list_is_named():
    """Adding a workflow to n8n must never add a capability to Jarvis."""
    bridge = build(CONFIG)
    bridge.client = FakeHttp()
    with pytest.raises(N8nError) as err:
        await bridge.run("delete-everything")
    assert "not in the n8n allow-list" in str(err.value)
    assert "bins" in str(err.value)
    assert bridge.client.sent == []


@pytest.mark.asyncio
async def test_an_empty_allow_list_is_valid_and_useless():
    bridge = build({"enabled": True, "url": "http://n8n", "workflows": []})
    with pytest.raises(N8nError):
        await bridge.run("anything")


def test_a_workflow_is_tier_three_unless_deliberately_lowered():
    """Running somebody's automation has effects this process cannot see."""
    bridge = build(CONFIG)
    assert bridge.find("bins").tier == 3
    assert bridge.find("meter").tier == 1, "an explicit tier: 1 must be honoured"
    # A nonsense tier is the safe one, not a crash at startup.
    assert build({"workflows": [{"name": "x", "tier": "banana"}]}).workflows[0].tier == 3
    assert build({"workflows": [{"name": "x", "tier": 9}]}).workflows[0].tier == 3


def test_a_workflow_with_no_webhook_says_which_node_it_needs():
    """n8n's API cannot start an arbitrary workflow; only a webhook trigger can."""
    bridge = build(CONFIG)
    entry = bridge.find("unreachable")
    assert entry.runnable is False
    assert "webhook" in entry.as_dict()["why_not"]


@pytest.mark.asyncio
async def test_running_one_with_no_webhook_refuses_rather_than_guessing_a_url():
    bridge = build(CONFIG)
    bridge.client = FakeHttp()
    with pytest.raises(N8nError) as err:
        await bridge.run("unreachable")
    assert "no supported way to start it" in str(err.value)
    assert bridge.client.sent == []


def test_the_tool_name_survives_being_an_identifier():
    assert Workflow(name="Put the BINS out!").tool_name == "n8n_put_the_bins_out"
    assert Workflow(name="meter").tool_name == "n8n_meter"


@pytest.mark.asyncio
async def test_the_worked_example_end_to_end():
    """Flag on, workflow listed, webhook present: it runs and reports."""
    bridge = build(CONFIG)
    bridge.client = FakeHttp(FakeResponse({"queued": True, "run": 17}))
    answer = await bridge.run("bins", {"when": "tuesday"})
    assert answer == {"status": "ok", "workflow": "bins", "result": {"queued": True, "run": 17}}
    (sent,) = bridge.client.sent
    assert sent["url"] == "http://n8n.tail:5678/webhook/bins-out"
    assert sent["json"] == {"when": "tuesday"}
    assert sent["headers"]["X-N8N-API-KEY"] == "k3y", "the key did not travel"


@pytest.mark.asyncio
async def test_the_key_is_a_header_and_never_a_url():
    """A key in a URL ends up in n8n's access log and in shell history."""
    bridge = build(CONFIG)
    bridge.client = FakeHttp()
    await bridge.run("bins")
    assert "k3y" not in bridge.client.sent[0]["url"]


@pytest.mark.asyncio
async def test_an_error_from_n8n_is_reported_rather_than_swallowed():
    bridge = build(CONFIG)
    bridge.client = FakeHttp(FakeResponse(status_code=500))
    with pytest.raises(N8nError) as err:
        await bridge.run("bins")
    assert "500" in str(err.value)


@pytest.mark.asyncio
async def test_a_workflow_that_answers_with_nothing_is_still_a_success():
    """A webhook node with no `respond` returns an empty body, not a failure."""
    bridge = build(CONFIG)
    bridge.client = FakeHttp(FakeResponse(payload=None, text="OK"))
    answer = await bridge.run("bins")
    assert answer["status"] == "ok"


@pytest.mark.asyncio
async def test_an_unreachable_n8n_names_the_url_it_tried():
    class Refusing:
        async def post(self, *_a, **_k):
            raise ConnectionError("no route to host")

    bridge = build(CONFIG)
    bridge.client = Refusing()
    with pytest.raises(N8nError) as err:
        await bridge.run("bins")
    assert "n8n.tail:5678" in str(err.value)


def test_a_nameless_entry_is_dropped_rather_than_half_registered():
    bridge = build({"enabled": True, "workflows": [{"webhook": "x"}, {"name": "ok"}]})
    assert [w.name for w in bridge.workflows] == ["ok"]
