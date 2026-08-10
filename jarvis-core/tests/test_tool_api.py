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
