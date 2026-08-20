"""What this n8n can do, measured rather than assumed.

## The state most self-hosted users are actually in

n8n ships one binary and sells several products out of it. Its AI workflow
builder is gated by a signed licence certificate, checked by a middleware that
runs before the route handler. Two settings sound like they would turn it on
and only one of them is the user's:

    aiBuilder.setup     is a model wired up?      <- an env var, yours
    aiBuilder.enabled   is the feature licensed?  <- the certificate, not yours

Somebody who has pointed n8n's AI settings at their own local model has set
the first and not the second, and there is no reason they should know that.
The test below named `test_a_wired_up_model_on_an_unlicensed_instance...` is
that exact state, and the sentence it asserts is the whole reason this module
exists: never "the AI builder failed", always which of the four reasons.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.n8n.capabilities import (  # noqa: E402
    CACHE_SECONDS,
    N8nCapabilities,
)
from jarvis.integrations.n8n.client import N8nClient  # noqa: E402
from jarvis.integrations.n8n.session import COOKIE_NAME, N8nSession  # noqa: E402

URL = "http://n8n.lan:5678"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.a-real-looking-session-token.signature"

LICENCE_403 = {"status": "error", "message": "Plan lacks license for this feature"}


def caps(handler, *, login=("jarvis@example.com", "hunter2hunter2")) -> N8nCapabilities:
    transport = httpx.MockTransport(handler)
    client = N8nClient(URL, "n8n_api_key_value", transport=transport)
    session = N8nSession(URL, login[0], login[1], transport=transport) if login else None
    return N8nCapabilities(client=client, session=session)


def _router(*, settings=None, api=True, login=True, settings_status=200):
    """One handler covering all three layers, so a test only says what differs."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/v1/workflows"):
            if not api:
                return httpx.Response(401)
            return httpx.Response(200, json={"data": [], "nextCursor": None})
        if path.endswith("/rest/login"):
            if login is True:
                return httpx.Response(
                    200,
                    json={"data": {}},
                    headers={"Set-Cookie": f"{COOKIE_NAME}={TOKEN}; Path=/"},
                )
            return login
        if path.endswith("/rest/settings"):
            if settings_status != 200:
                return httpx.Response(settings_status)
            return httpx.Response(200, json={"data": settings or {}})
        return httpx.Response(404)

    return handler


# ---------------------------------------------------------------------------
# three layers, three separate answers
# ---------------------------------------------------------------------------
async def test_all_three_are_measured_separately():
    """A single "n8n: broken" sends people to the wrong half. The API key and
    the login fail independently and for different reasons."""
    box = await caps(_router(settings={"aiBuilder": {"enabled": True, "setup": True}})).refresh()
    assert box.api.available
    assert box.login.available
    assert box.builder.available
    assert "Public API" in box.summary()
    assert "AI builder" in box.summary()


async def test_a_broken_api_key_does_not_make_the_login_look_broken():
    box = await caps(
        _router(api=False, settings={"aiBuilder": {"enabled": True, "setup": True}})
    ).refresh()
    assert not box.api.available
    assert box.login.available
    assert box.builder.available


# ---------------------------------------------------------------------------
# the four reasons the builder is not there
# ---------------------------------------------------------------------------
async def test_a_wired_up_model_on_an_unlicensed_instance_gets_the_honest_sentence():
    """The state the user is probably in, and the one a shrug fails hardest."""
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": False, "setup": True}})
    ).refresh()
    assert not box.builder.available
    assert box.builder.reason == "licence"
    said = box.builder.detail
    assert "two separate switches" in said
    # And it says what happens instead, because something does.
    assert "write workflows itself" in said


async def test_neither_licensed_nor_wired_up_says_both():
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": False, "setup": False}})
    ).refresh()
    assert box.builder.reason == "not set up"
    assert "neither licensed nor wired up" in box.builder.detail


async def test_licensed_but_no_model_is_its_own_case():
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": True, "setup": False}})
    ).refresh()
    assert box.builder.available
    assert "no model is wired up" in box.builder.detail


async def test_an_n8n_too_old_to_have_the_setting_says_so():
    box = await caps(_router(settings={"versionCli": "1.20.0"})).refresh()
    assert box.builder.reason == "too old"


async def test_no_login_means_the_builder_is_unreachable_by_definition():
    """`/rest` is the only place the builder lives, and an API key cannot open
    it. Said as a fact about the surface, not as a failure."""
    box = await caps(_router(), login=None).refresh()
    assert not box.login.available
    assert box.login.reason == "unconfigured"
    assert box.builder.reason == "unconfigured"
    assert "needs a login" in box.builder.detail


async def test_two_factor_carries_through_to_the_builder_line():
    box = await caps(
        _router(login=httpx.Response(401, json={"code": 998, "message": "MFA Error"}))
    ).refresh()
    assert box.login.reason == "mfa"
    assert box.builder.reason == "mfa"


async def test_a_bot_filtered_settings_call_is_named():
    """A 204 with an empty body is n8n's bot filter. Parsed as JSON it is
    "no settings", which would read as "too old"."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rest/settings"):
            return httpx.Response(204)
        return _router()(request)

    box = await caps(handler).refresh()
    assert box.builder.reason == "bot filter"


async def test_settings_that_are_not_json_blame_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/rest/settings"):
            return httpx.Response(200, text="<html>hello</html>")
        return _router()(request)

    box = await caps(handler).refresh()
    assert "really an n8n" in box.builder.detail


# ---------------------------------------------------------------------------
# the answer that cannot be stale
# ---------------------------------------------------------------------------
async def test_a_real_403_overrules_a_settings_page_that_claimed_otherwise():
    """The probe reads the instance's own claim. The licence middleware's
    verdict is the only thing that decides, and it is only visible on a real
    call."""
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": True, "setup": True}})
    ).refresh()
    assert box.builder.available

    box.note_refusal(403, str(LICENCE_403))
    assert not box.builder.available
    assert box.builder.reason == "licence"
    assert "403" in box.builder.detail


async def test_a_dead_builder_is_not_resurrected_by_a_cache_expiry():
    """Re-asking costs a login, and a licence that was absent when the call
    was made will not have appeared without a restart."""
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": True, "setup": True}})
    ).refresh()
    box.note_refusal(403, "Plan lacks license for this feature")
    box._checked_at = 0.0  # as if the cache had expired
    await box.refresh()
    assert box.builder.reason == "licence"


async def test_a_404_on_the_builder_route_means_too_old_and_is_also_final():
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": True, "setup": True}})
    ).refresh()
    box.note_refusal(404, "")
    assert box.builder.reason == "too old"


async def test_a_403_that_is_not_about_the_licence_is_not_treated_as_final():
    """Not every 403 is the licence middleware. One that is not stays
    re-checkable, because it may be a permissions problem somebody fixes."""
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": True, "setup": True}})
    ).refresh()
    box.note_refusal(403, "forbidden by the reverse proxy")
    assert box.builder.reason == "credentials"
    assert box._builder_is_dead is False


# ---------------------------------------------------------------------------
# the sentence the model gets
# ---------------------------------------------------------------------------
async def test_the_model_is_told_what_to_do_instead_rather_than_lied_to():
    """Writing the workflow itself is a turn of Jarvis's own model and cannot
    happen inside a tool call. A tool that pretended to fall back would be
    reporting work it did not do."""
    box = await caps(
        _router(settings={"aiBuilder": {"enabled": False, "setup": True}})
    ).refresh()
    said = box.instead()
    assert said["status"] == "error"
    assert "create_n8n_workflow" in said["instead"]
    assert "list_n8n_node_types" in said["instead"]
    assert "two separate switches" in said["error"]


# ---------------------------------------------------------------------------
# not asking more often than it is worth
# ---------------------------------------------------------------------------
async def test_a_fresh_result_is_not_re_measured():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _router(settings={"aiBuilder": {"enabled": True, "setup": True}})(request)

    box = caps(handler)
    await box.refresh()
    first = len(calls)
    await box.refresh()
    assert len(calls) == first, "a cached probe should make no requests"

    await box.refresh(force=True)
    assert len(calls) > first, "and CHECK should always really check"


@pytest.mark.parametrize("age,expected", [(1.0, True), (CACHE_SECONDS + 1, False)])
async def test_freshness_is_a_window_not_a_latch(age, expected):
    import time

    box = caps(_router(settings={"aiBuilder": {"enabled": True, "setup": True}}))
    await box.refresh()
    box._checked_at = time.time() - age
    assert box.fresh is expected
