"""Tools created from the console.

A tool's identity is its name, because that is the word the model says to call
it. That makes the interesting cases different from automations': a console
tool must not be able to take a name a built-in already holds, and a delete
must not be able to reach anything this store did not create.

Everything here drives the real `ToolRegistry`, so a tool that is stored but
never registered — or deleted from the store but left callable — fails.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import common  # noqa: E402
from jarvis.api.common import ApiError  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.authored_tools import (  # noqa: E402
    AuthoredToolError,
    AuthoredToolStore,
    get_authored_tools,
    validate,
)
from jarvis.llm.tools import ToolRegistry, register_builtin_tools  # noqa: E402

GOOD = {
    "name": "paperless_search",
    "description": "Search Paperless-ngx documents by query text",
    "tier": 1,
    "service": {
        "method": "GET",
        "url": "http://paperless.lan/api/documents/?query={{ query }}",
        "fields": {"query": {"description": "search text", "required": True}},
    },
}


@pytest.fixture
def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    registry = ToolRegistry(box)
    register_builtin_tools(registry)
    box.data["llm_tools"] = registry
    box.data["llm_client"] = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    )
    return box


def test_validate_accepts_a_reasonable_tool():
    clean = validate(dict(GOOD))
    assert clean["name"] == "paperless_search"
    assert clean["service"]["method"] == "GET"
    assert clean["tier"] == 1


@pytest.mark.parametrize(
    "spec,message",
    [
        ({**GOOD, "name": ""}, "name"),
        ({**GOOD, "name": "Has Spaces"}, "lowercase"),
        ({**GOOD, "name": "ab"}, "3-48"),
        ({**GOOD, "description": ""}, "describe"),
        ({**GOOD, "tier": 9}, "Tier"),
        ({**GOOD, "service": {}}, "url"),
        # Only http(s): a `file://` tool would read the disk of the box.
        ({**GOOD, "service": {"url": "file:///etc/shadow"}}, "http"),
        ({**GOOD, "service": {"url": "http://x.test", "method": "TRACE"}}, "Method"),
        ({**GOOD, "service": {"url": "http://x.test", "timeout": 9999}}, "between"),
        # A newline in a header value is header injection — these are written
        # straight onto the wire.
        (
            {**GOOD, "service": {"url": "http://x.test", "headers": {"X": "a\r\nEvil: 1"}}},
            "line breaks",
        ),
        ({**GOOD, "webhook": "x"}, "Unknown field"),
        ("not a tool", "object"),
    ],
)
def test_validate_refuses_and_says_why(spec, message):
    with pytest.raises(AuthoredToolError) as err:
        validate(spec)
    assert message.lower() in str(err.value).lower()


def test_a_name_a_builtin_already_holds_is_refused():
    """Shadowing is the interesting attack, not a naming inconvenience.

    A console tool called `lock_control` would make the assistant call
    something else entirely while every log line still said `lock_control`.
    """
    with pytest.raises(AuthoredToolError) as err:
        validate({**GOOD, "name": "lock_control"}, {"lock_control", "turn_on"})
    assert "already a tool" in str(err.value)


async def test_create_registers_it_on_the_running_assistant(jarvis):
    result = await common.async_create_tool(jarvis, {"tool": dict(GOOD)})

    assert result["tool"]["name"] == "paperless_search"
    assert "paperless_search" in jarvis.data["llm_tools"].names(), (
        "stored but never registered — the model cannot call it until a restart"
    )


async def test_the_registered_tool_actually_runs(jarvis):
    await common.async_create_tool(jarvis, {"tool": dict(GOOD)})
    registry = jarvis.data["llm_tools"]

    out = await registry.call("paperless_search", {"query": "invoice"})

    assert out["status"] == "ok"
    # Fenced, like every other tool that returns somebody else's bytes.
    assert out["content_is_untrusted"] is True


async def test_delete_stops_the_model_being_able_to_call_it(jarvis):
    await common.async_create_tool(jarvis, {"tool": dict(GOOD)})

    result = await common.async_delete_tool(jarvis, {"name": "paperless_search"})

    assert result == {"name": "paperless_search", "deleted": True}
    assert "paperless_search" not in jarvis.data["llm_tools"].names()
    assert get_authored_tools(jarvis).items == {}


async def test_a_builtin_cannot_be_deleted_through_the_api(jarvis):
    """And is still callable afterwards — a refusal that had already
    unregistered it would look identical from the caller's side."""
    with pytest.raises(ApiError) as err:
        await common.async_delete_tool(jarvis, {"name": "lock_control"})

    assert err.value.code == "not_supported"
    assert "lock_control" in jarvis.data["llm_tools"].names()


async def test_creating_over_a_builtin_name_is_refused(jarvis):
    with pytest.raises(ApiError) as err:
        await common.async_create_tool(jarvis, {"tool": {**GOOD, "name": "lock_control"}})

    assert err.value.status == 400
    assert "already a tool" in err.value.message
    assert get_authored_tools(jarvis).items == {}


async def test_update_replaces_what_the_model_calls(jarvis):
    await common.async_create_tool(jarvis, {"tool": dict(GOOD)})

    await common.async_update_tool(
        jarvis,
        {
            "name": "paperless_search",
            "tool": {**GOOD, "description": "Search the document archive"},
        },
    )

    registry = jarvis.data["llm_tools"]
    assert registry.get("paperless_search").description == "Search the document archive"
    assert registry.names().count("paperless_search") == 1


async def test_a_tool_cannot_be_renamed(jarvis):
    """Renaming would silently break every place the old name was referred to."""
    await common.async_create_tool(jarvis, {"tool": dict(GOOD)})

    with pytest.raises(ApiError) as err:
        await common.async_update_tool(
            jarvis, {"name": "paperless_search", "tool": {**GOOD, "name": "something_else"}}
        )
    assert "cannot be changed" in err.value.message


async def test_the_list_marks_which_tools_the_console_owns(jarvis):
    await common.async_create_tool(jarvis, {"tool": dict(GOOD)})

    rows = {row["name"]: row for row in common.tool_list_payload(jarvis)}

    assert rows["paperless_search"]["editable"] is True
    assert rows["lock_control"]["editable"] is False
    assert rows["paperless_search"]["service"]["url"].startswith("http://paperless.lan")
    # Editable first, so what the user can act on is not buried.
    assert common.tool_list_payload(jarvis)[0]["editable"] is True


async def test_without_an_assistant_the_api_says_so_rather_than_storing(tmp_path):
    box = Jarvis(tmp_path)
    with pytest.raises(ApiError) as err:
        await common.async_create_tool(box, {"tool": dict(GOOD)})
    assert err.value.status == 404
    assert common.tool_list_payload(box) == []


async def test_a_stored_tool_survives_a_restart(tmp_path):
    store = AuthoredToolStore(tmp_path)
    await store.async_create(dict(GOOD))

    fresh = AuthoredToolStore(tmp_path)
    specs = await fresh.async_load()

    assert [spec["name"] for spec in specs] == ["paperless_search"]
    assert "created_at" not in specs[0]


async def test_a_corrupt_stored_tool_is_dropped_not_fatal(tmp_path, caplog):
    store = AuthoredToolStore(tmp_path)
    await store.async_create(dict(GOOD))
    store.items["broken"] = {"name": "broken"}  # no description, no service
    await store._async_save()

    fresh = AuthoredToolStore(tmp_path)
    with caplog.at_level("WARNING"):
        specs = await fresh.async_load()

    assert [spec["name"] for spec in specs] == ["paperless_search"]
    assert "broken" in caplog.text


def test_every_tool_route_is_wired_to_the_api():
    from jarvis.api import rest, websocket

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    for verb in ("list", "create", "update", "delete"):
        assert f"/api/config/tool/{verb}" in paths, verb
        assert f"config/tool/{verb}" in websocket.WebSocketHandler._HANDLERS, verb


# ---------------------------------------------------------------------------
# `jarvis/tools/list` and `jarvis/tools/call` — the model's own toolbox
# ---------------------------------------------------------------------------
#
# These two existed in `jarvis-web/src/lib/jarvisClient.ts` — with a documented
# graceful-degradation path, unit tests for that path, and a Tools page built
# on top of it — for the whole life of the product, and jarvis-core implemented
# NEITHER. The console's "Test run" button answered
#
#     unknown command 'jarvis/tools/call'
#
# and the page quietly relabelled itself to the service catalogue. The e2e
# suite was green throughout because the mock backend deliberately did not know
# the command either, so the only thing ever tested was the fallback.
#
# That is the client-server seam this repo keeps finding: a contract written on
# one side only. The tests below are on the SERVER side on purpose.


async def test_the_toolbox_command_answers_at_all(jarvis):
    """The regression. `unknown command` was the entire bug."""
    from jarvis.api import websocket

    assert "jarvis/tools/list" in websocket.WebSocketHandler._HANDLERS
    assert "jarvis/tools/call" in websocket.WebSocketHandler._HANDLERS

    payload = common.tools_list_payload(jarvis)
    assert payload["count"] > 10, "an empty toolbox would make this vacuous"
    assert payload["count"] == len(payload["tools"])


async def test_the_listing_is_exactly_what_the_model_is_offered(jarvis):
    """Not "roughly the same". The same set, by construction.

    `agent.py` builds the model's schema from `as_openai_schema()` over this
    registry with no filtering, so if these two ever diverge the page is
    lying about the thing it exists to answer — "is the tool the model is
    failing to call actually registered?"
    """
    registry = jarvis.data["llm_tools"]
    listed = {t["name"] for t in common.tools_list_payload(jarvis)["tools"]}
    offered = {t["function"]["name"] for t in registry.as_openai_schema()}
    assert listed == offered


async def test_the_listing_carries_the_approval_rule_not_just_the_tier(jarvis):
    """`tier` is not the whole rule, so the console must not re-derive it.

    `lock_control` is Tier 3. `write_file`-shaped tools are Tier 3. But a tool
    can also be held for its DOMAIN at any tier, and a tool with a `gate` is
    held depending on its arguments. A TypeScript reimplementation of that
    would be a second copy of a security decision.
    """
    from jarvis.llm.tools import TIER_APPROVAL

    tools = {t["name"]: t for t in common.tools_list_payload(jarvis)["tools"]}

    held = [t for t in tools.values() if t["needs_approval"]]
    assert held, "no tool reports needing approval; the field is not being set"
    for entry in held:
        from jarvis.const import GATED_DOMAINS

        assert entry["tier"] >= TIER_APPROVAL or entry["domain"] in GATED_DOMAINS

    # And a gated-domain tool is reported held even though its tier alone
    # would not say so.
    lock = tools.get("lock_control")
    if lock is not None:
        assert lock["needs_approval"] is True


async def test_a_test_run_actually_runs_the_tool(jarvis):
    jarvis.states.set("light.lab", "off", {"friendly_name": "Lab"})
    answer = await common.async_call_tool(
        jarvis, "get_state", {"name": "Lab"}
    )
    assert answer["tool"] == "get_state"
    assert answer["result"], answer


async def test_an_unknown_tool_is_a_clean_404_not_a_model_shaped_answer(jarvis):
    """`registry.call` answers unknown tools with a dict a MODEL can act on.

    A request wants the HTTP-shaped answer instead, so the console can tell
    "no such tool" from "the tool ran and refused".
    """
    with pytest.raises(ApiError) as caught:
        await common.async_call_tool(jarvis, "no_such_tool_at_all", {})
    assert caught.value.code == "not_found"


async def test_a_nameless_call_is_refused(jarvis):
    with pytest.raises(ApiError) as caught:
        await common.async_call_tool(jarvis, "   ", {})
    assert caught.value.code == "invalid_format"


async def test_arguments_must_be_an_object(jarvis):
    """A list or a string here would reach `registry.call` as `{"input": ...}`.

    Silently reshaping a caller's mistake into a different call is how a test
    run reports success for something nobody asked for.
    """
    with pytest.raises(ApiError) as caught:
        await common.async_call_tool(jarvis, "get_state", ["Lab"])
    assert caught.value.code == "invalid_format"
    assert "object" in caught.value.message


async def test_a_test_run_does_not_bypass_the_approval_gate(jarvis):
    """THE test on this command.

    A console test runner that skipped the tier gate would be the easiest
    Tier-3 bypass in the product: every approval-held verb — `execute_command`,
    `apply_code_task`, `write_file`, `start_coding_job`, anything in a gated
    domain — reachable with one button and no human. It goes through
    `ToolRegistry.call`, which holds it exactly as it holds a model turn.
    """
    from jarvis.llm.tools import TIER_APPROVAL, schema_object

    ran = []

    async def _boom(args, context=None):
        ran.append(args)
        return {"status": "ok", "did": "the dangerous thing"}

    jarvis.data["llm_tools"].register(
        name="pretend_dangerous_verb",
        description="stands in for execute_command",
        parameters=schema_object({"x": {"type": "string"}}, []),
        handler=_boom,
        tier=TIER_APPROVAL,
    )

    answer = await common.async_call_tool(
        jarvis, "pretend_dangerous_verb", {"x": "1"}
    )
    result = answer["result"]
    assert not ran, "a Tier-3 tool RAN from the console test runner"
    assert result.get("status") == "approval_required", result
    assert result.get("request_id"), "no approval request was raised to answer"


async def test_the_held_request_is_the_one_a_human_can_answer(jarvis):
    """A held test run has to reach the same approvals queue as any other.

    Raising a request nothing surfaces would be worse than refusing outright:
    the button would appear to hang.
    """
    from jarvis.llm.tools import TIER_APPROVAL, schema_object

    async def _noop(args, context=None):
        return {"status": "ok"}

    jarvis.data["llm_tools"].register(
        name="pretend_gated",
        description="x",
        parameters=schema_object({}, []),
        handler=_noop,
        tier=TIER_APPROVAL,
    )
    await common.async_call_tool(jarvis, "pretend_gated", {})
    pending = jarvis.data["llm_tools"].pending_requests()
    assert [p for p in pending if p["tool"] == "pretend_gated"], pending


async def test_bad_arguments_come_back_naming_the_key(jarvis):
    """Coercion is `registry.call`'s job, and the console gets its answer.

    Reimplementing validation in the API layer would give the console a
    different verdict from the one the model gets for the same call.
    """
    answer = await common.async_call_tool(jarvis, "get_state", {"nonsense": 1})
    result = answer["result"]
    assert result.get("status") == "error"
    assert "expected" in result or "error" in result


async def test_without_an_assistant_the_toolbox_says_so(tmp_path):
    box = Jarvis(tmp_path)
    with pytest.raises(ApiError):
        common.tools_list_payload(box)
    with pytest.raises(ApiError):
        await common.async_call_tool(box, "get_state", {})


def test_the_toolbox_is_reachable_over_rest_as_well():
    from jarvis.api import rest

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    assert "/api/tools" in paths
    assert "/api/tools/call" in paths
