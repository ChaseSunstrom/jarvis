"""Talking to an n8n instance, without one.

Every test here drives the real `N8nClient` through an httpx `MockTransport`,
so the URLs, headers, params and error handling are the production ones. What
is faked is the instance at the other end.

## The one thing this cannot check

Whether n8n's API really looks like this. The client was written against
documentation rather than a live instance, and n8n's public API has moved
between versions. That is what `probe()` is for — and why the tests below pin
what a WRONG guess looks like (a clear sentence naming the path and status)
rather than only what a right one does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.n8n.client import (  # noqa: E402
    KEY_HEADER,
    N8nClient,
    N8nError,
    redact,
)

pytestmark = pytest.mark.asyncio

KEY = "n8n_api_0123456789abcdef"


def client(handler, **kw) -> N8nClient:
    return N8nClient(
        "http://n8n.lan:5678", KEY, transport=httpx.MockTransport(handler), **kw
    )


def ok(payload) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---------------------------------------------------------------------------
# the wire
# ---------------------------------------------------------------------------
async def test_the_key_goes_in_the_header_and_not_the_url():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get(KEY_HEADER)
        return ok({"data": []})

    await client(handler).list_workflows()
    assert seen["key"] == KEY
    assert KEY not in seen["url"], "the API key ended up in a URL"


async def test_the_path_is_the_public_api_prefix():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return ok({"data": []})

    await client(handler).list_workflows()
    assert seen["path"] == "/api/v1/workflows"


async def test_a_page_is_asked_for_by_limit_and_returns_the_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "5"
        return ok({"data": [{"id": "1"}], "nextCursor": "abc"})

    rows, cursor = await client(handler).list_workflows(limit=5)
    assert [r["id"] for r in rows] == ["1"]
    assert cursor == "abc"


async def test_active_is_sent_as_a_lowercase_string():
    """`str(True)` is `True` with a capital T, which n8n does not parse."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["active"] = request.url.params.get("active")
        return ok({"data": []})

    await client(handler).list_workflows(active=True)
    assert seen["active"] == "true"


async def test_the_page_size_is_bounded():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["limit"] = int(request.url.params["limit"])
        return ok({"data": []})

    await client(handler).list_workflows(limit=100_000)
    assert seen["limit"] <= 100


async def test_creating_posts_the_payload_verbatim():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return ok({"id": "new-1", "name": "X"})

    payload = {"name": "X", "nodes": [], "connections": {}, "settings": {}}
    created = await client(handler).create_workflow(payload)
    assert seen["method"] == "POST"
    assert seen["body"] == payload
    assert created["id"] == "new-1"


async def test_activating_and_deactivating_are_different_paths():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return ok({})

    c = client(handler)
    await c.set_active("7", True)
    await c.set_active("7", False)
    assert seen == ["/api/v1/workflows/7/activate", "/api/v1/workflows/7/deactivate"]


# ---------------------------------------------------------------------------
# the failures, which are most of the value
# ---------------------------------------------------------------------------
async def test_a_bad_key_says_where_to_get_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    with pytest.raises(N8nError) as caught:
        await client(handler).list_workflows()
    assert "401" in str(caught.value)
    assert "Settings -> n8n API" in str(caught.value)


async def test_a_404_names_the_version_possibility():
    """The likeliest cause of a 404 here is an n8n too old for this API, and
    an operator who is not told that goes looking for a missing workflow."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    with pytest.raises(N8nError) as caught:
        await client(handler).get_workflow("7")
    assert "too old" in str(caught.value)


async def test_an_html_login_page_is_not_mistaken_for_an_answer():
    """Pointing this at a reverse proxy or the n8n UI itself is the ordinary
    mistake, and `<!doctype html>` parsed as nothing would look like an empty
    instance."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html><html>hello</html>")

    with pytest.raises(N8nError) as caught:
        await client(handler).list_workflows()
    assert "not JSON" in str(caught.value)


async def test_a_list_that_is_not_a_list_is_an_error_not_an_empty_instance():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"message": "something else entirely"})

    with pytest.raises(N8nError) as caught:
        await client(handler).list_workflows()
    assert "did not look like one" in str(caught.value)


async def test_an_unreachable_instance_says_so_without_a_traceback():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    with pytest.raises(N8nError) as caught:
        await client(handler).list_workflows()
    assert "could not reach n8n" in str(caught.value)


async def test_a_timeout_names_the_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(N8nError) as caught:
        await client(handler, timeout=9).list_workflows()
    assert "9s" in str(caught.value)


async def test_the_key_is_scrubbed_from_anything_quoted_back():
    """httpx quotes the request in its errors, the integration quotes the
    error into a tool result, and a tool result reaches the model and the
    console. Three hops from a header to a transcript."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"upstream said key={KEY} was fine")

    with pytest.raises(N8nError) as caught:
        await client(handler).list_workflows()
    assert KEY not in str(caught.value)
    assert "***" in str(caught.value)


def test_redact_leaves_short_or_absent_keys_alone():
    """A two-character "key" would turn every `ab` in a message into stars."""
    assert redact("all is ab well", "ab") == "all is ab well"
    assert redact("nothing to do", "") == "nothing to do"


# ---------------------------------------------------------------------------
# the id, which arrives from a model
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad", ["../../rest/settings", "7/../..", "a b", "", "  ", "id?x=1", "a#b"]
)
async def test_a_workflow_id_cannot_become_a_path(bad):
    """The ids come back from n8n, but they also arrive from a model and from
    a request body."""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"a request was made for {bad!r}")

    with pytest.raises(N8nError):
        await client(handler).get_workflow(bad)


async def test_an_ordinary_id_is_fine():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"id": "aB3-x_9"})

    assert (await client(handler).get_workflow("aB3-x_9"))["id"] == "aB3-x_9"


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------
async def test_the_probe_is_a_real_call_not_a_health_check():
    """A `/healthz` that answers says the process is up, which is not the
    question. The question is whether this url, this key and this API version
    can list a workflow."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return ok({"data": []})

    result = await client(handler).probe()
    assert result["ok"] is True
    assert seen == ["/api/v1/workflows"]


async def test_the_probe_reports_the_reason_rather_than_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    result = await client(handler).probe()
    assert result["ok"] is False
    assert "401" in result["detail"]


async def test_the_probe_says_when_there_is_no_key_at_all():
    c = N8nClient("http://n8n.lan:5678", "")
    result = await c.probe()
    assert result["ok"] is False
    assert "Settings -> n8n API" in result["detail"]


async def test_the_probe_says_when_there_is_no_url():
    result = await N8nClient("", KEY).probe()
    assert result["ok"] is False
    assert "No URL" in result["detail"]
