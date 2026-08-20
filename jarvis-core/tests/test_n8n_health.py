"""Whether a workflow Jarvis wrote is actually working.

## What was missing

The story used to end at "created". Jarvis writes a workflow, says "connect
Gmail and switch it on", and then cannot answer a single question about it
ever again — even though n8n has been recording every execution the whole
time. Ask "did that expense thing run?" and the honest answer was that Jarvis
did not know.

## The cases worth having

The obvious two are "not connected" and "it failed". The one that justifies
the module is the third: **connected, switched on, and it has never run.**
That state is invisible from everywhere else, it is what a schedule in the
wrong timezone looks like, and it is indistinguishable from working unless
somebody joins three separate reads.

## And the thing it must never do

Never `includeData=true`. That parameter returns the body of everything that
went through the workflow — the user's actual emails and invoices. There is a
test below whose only job is to assert it is not on the wire.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import n8n as n8n_integration  # noqa: E402
from jarvis.integrations.n8n.health import assess  # noqa: E402

URL = "http://n8n.lan:5678"


def workflow(*, active=True, attached=True, **over):
    credential = {"id": "5", "name": "Gmail account"} if attached else {}
    base = {
        "id": "wf-1",
        "name": "File the receipt",
        "active": active,
        "nodes": [
            {"name": "Cron", "type": "n8n-nodes-base.cron", "typeVersion": 1},
            {
                "name": "Gmail",
                "type": "n8n-nodes-base.gmail",
                "typeVersion": 2.1,
                "credentials": {"gmailOAuth2": credential},
            },
        ],
        "connections": {},
    }
    base.update(over)
    return base


def run(status="success", started="2026-02-01T02:00:00Z"):
    return {"id": "r1", "workflowId": "wf-1", "status": status, "startedAt": started}


# ---------------------------------------------------------------------------
# the four states
# ---------------------------------------------------------------------------
def test_unattached_credentials_are_reported_before_anything_else():
    """A workflow that is switched on with nothing attached errors every time
    it fires, so "it failed" is the symptom and this is the cause."""
    got = assess(workflow(attached=False), [run("error")])
    assert got.healthy is False
    assert "Not connected yet" in got.summary
    assert "gmailOAuth2" in got.summary
    assert "Credentials -> New" in got.next_step


def test_switched_off_with_everything_attached_says_exactly_that():
    got = assess(workflow(active=False), [])
    assert got.healthy is False
    assert "switched off" in got.summary
    assert "switch it on" in got.next_step


def test_unattached_and_off_mentions_both():
    """Two problems, one visit to n8n. Reporting one and then the other on the
    next question wastes a trip."""
    got = assess(workflow(active=False, attached=False), [])
    assert "Not connected yet" in got.summary
    assert "also switched off" in got.summary


def test_failures_are_counted_and_the_status_words_quoted():
    got = assess(workflow(), [run("error"), run("success"), run("crashed")])
    assert got.healthy is False
    assert got.failures == 2
    assert "2 of the last 3" in got.summary
    assert "error" in got.summary and "crashed" in got.summary
    # Never what the failure contained — only that it failed.
    assert "which node threw" in got.next_step


def test_connected_and_on_and_never_run_is_the_interesting_case():
    """This is what a schedule in the wrong timezone looks like, and it is
    indistinguishable from working unless somebody joins the three reads."""
    got = assess(workflow(), [])
    assert got.healthy is False
    assert "never run" in got.summary
    assert "timezone" in got.next_step
    assert "production URL" in got.next_step


def test_working_says_so_plainly():
    got = assess(workflow(), [run(), run()])
    assert got.healthy is True
    assert "Working" in got.summary


# ---------------------------------------------------------------------------
# the quirk that would produce a wrong answer
# ---------------------------------------------------------------------------
def test_a_workflow_running_right_now_is_not_reported_as_never_run():
    """n8n excludes running executions from `GET /executions` unless you ask
    for them by status. A health check that made only the first call would
    report "never run" about a workflow that is running as you read it."""
    got = assess(workflow(), [], [run("running")])
    assert got.healthy is True
    assert "never run" not in got.summary
    assert got.running_now == 1


def test_a_finished_history_plus_a_current_run_mentions_both():
    got = assess(workflow(), [run()], [run("running")])
    assert got.healthy is True
    assert "1 running right now" in got.summary


# ---------------------------------------------------------------------------
# over the wire
# ---------------------------------------------------------------------------
@pytest.fixture
async def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({})
    yield box
    await box.async_stop()


async def make(jarvis, handler):
    await n8n_integration.async_setup(jarvis, {"url": URL, "api_key": "n8n_key_value"})
    n8n_integration.get_client(jarvis)._transport = httpx.MockTransport(handler)


async def test_execution_contents_are_never_requested(jarvis):
    """The security test. `includeData=true` returns the body of every email
    and invoice that went through the workflow."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "/executions" in request.url.path:
            return httpx.Response(200, json={"data": [run()]})
        return httpx.Response(200, json=workflow())

    await make(jarvis, handler)
    await n8n_integration.async_health(jarvis, "wf-1")

    assert seen, "it made requests"
    for url in seen:
        assert "includeData" not in url
        assert "includedata" not in url.lower()


async def test_the_running_filter_is_asked_for_separately(jarvis):
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/executions" in request.url.path:
            asked.append(request.url.params.get("status", ""))
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json=workflow())

    await make(jarvis, handler)
    await n8n_integration.async_health(jarvis, "wf-1")
    assert asked == ["", "running"]


async def test_an_instance_that_rejects_the_running_filter_still_answers(jarvis):
    """A missing "running" count is a smaller lie than a failed health check."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/executions" in request.url.path:
            if request.url.params.get("status"):
                return httpx.Response(400, json={"message": "unknown filter"})
            return httpx.Response(200, json={"data": [run()]})
        return httpx.Response(200, json=workflow())

    await make(jarvis, handler)
    got = await n8n_integration.async_health(jarvis, "wf-1")
    assert got["healthy"] is True
    assert got["running_now"] == 0


async def test_the_tool_is_tier_one_and_reads_no_parameters(jarvis):
    """Reading structure and run metadata is not an action, and requiring an
    approval to answer "did that work?" would make the answer useless."""
    from jarvis.llm.tools import TIER_DIRECT, ToolRegistry

    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    await n8n_integration.async_setup(jarvis, {"url": URL, "api_key": "n8n_key_value"})
    tool = registry.get("check_n8n_health")
    assert tool is not None
    assert tool.tier == TIER_DIRECT


async def test_an_unreachable_instance_is_an_error_not_a_verdict(jarvis):
    """"Cannot reach n8n" is not "the workflow is unhealthy"."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    await make(jarvis, handler)
    from jarvis.integrations.n8n.client import N8nError

    with pytest.raises(N8nError):
        await n8n_integration.async_health(jarvis, "wf-1")
