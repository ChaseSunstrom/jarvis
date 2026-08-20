"""The optional n8n login, and the four ways it quietly goes wrong.

## Why a login at all

n8n has two HTTP surfaces that do not overlap. `/api/v1` is opened by the API
key Jarvis already has. `/rest` — which is what n8n's own UI talks to — is
opened by a session cookie and by nothing else, and three things Jarvis wants
live only there: the instance settings, the node type catalogue, and the AI
builder.

## What is pinned here

The four failure modes that all look like "wrong password" if you are not
looking for them, plus the one that looks like success:

- a `Secure` cookie arriving over plain http, which httpx's jar would drop
- a `browser-id` header, which permanently arms a check that then fails
- a mid-session token rotation, which n8n does on its own
- 401 code 998 (two-factor) told apart from 401 code 401 (wrong password)
- a 204 with an empty body, which is n8n's bot filter and parses as "no data"

And the one thing that must never happen: a password or a session cookie in a
string Jarvis quotes back to somebody.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.n8n.session import (  # noqa: E402
    COOKIE_NAME,
    USER_AGENT,
    N8nSession,
    SessionError,
)

URL = "http://n8n.lan:5678"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.a-real-looking-session-token.signature"


def session(handler, **kwargs) -> N8nSession:
    return N8nSession(
        URL,
        kwargs.pop("email", "jarvis@example.com"),
        kwargs.pop("password", "correct-horse-battery"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _login_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"id": "1", "email": "jarvis@example.com"}},
        headers={"Set-Cookie": f"{COOKIE_NAME}={TOKEN}; Path=/; HttpOnly; SameSite=lax"},
    )


# ---------------------------------------------------------------------------
# logging in
# ---------------------------------------------------------------------------
async def test_the_login_field_is_the_one_n8n_actually_reads():
    """n8n's DTO calls it `emailOrLdapLoginId`. A body with `email` is dropped
    by validation and comes back as a refused login, which sends somebody to
    check a password that was right all along."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return _login_ok(request)

    await session(handler).login()
    assert seen[0]["emailOrLdapLoginId"] == "jarvis@example.com"
    assert "email" not in seen[0]


async def test_a_secure_cookie_over_plain_http_is_still_read():
    """The bug this prevents is the nastiest one in the module.

    `N8N_SECURE_COOKIE` defaults to true, so an n8n on http:// still stamps
    `Secure` on its Set-Cookie. A standards-respecting cookie jar then refuses
    to send it back over http, and every request 401s — which looks exactly
    like a wrong password and is not.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200,
                json={"data": {}},
                headers={
                    "Set-Cookie": f"{COOKIE_NAME}={TOKEN}; Path=/; HttpOnly; Secure; SameSite=lax"
                },
            )
        return httpx.Response(200, json={"data": {"ok": True}})

    box = session(handler)
    assert URL.startswith("http://")
    assert await box.login() == TOKEN
    response = await box.request("GET", "settings")
    assert response.status_code == 200


async def test_no_browser_id_is_ever_sent():
    """n8n hashes that header into the session at login and checks it on every
    later request. Never sending it leaves the check a permanent no-op;
    sending it once makes every subsequent byte of it load-bearing."""
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        if request.url.path.endswith("/login"):
            return _login_ok(request)
        return httpx.Response(200, json={})

    box = session(handler)
    await box.request("GET", "settings")
    assert len(headers) >= 2
    for sent in headers:
        assert "browser-id" not in sent


async def test_the_user_agent_never_contains_bot():
    """n8n answers 204 with an empty body to any User-Agent containing `bot` —
    a silently empty success, which parses as "no data" everywhere downstream
    and is impossible to diagnose from the result."""
    assert "bot" not in USER_AGENT.lower()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        return _login_ok(request)

    await session(handler).login()
    assert seen and "bot" not in seen[0].lower()


async def test_a_bot_filtered_login_is_named_rather_than_shrugged_at():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with pytest.raises(SessionError) as err:
        await session(handler).login()
    assert "bot filter" in str(err.value)


# ---------------------------------------------------------------------------
# the four refusals
# ---------------------------------------------------------------------------
async def test_two_factor_is_told_apart_from_a_wrong_password():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 998, "message": "MFA Error"})

    with pytest.raises(SessionError) as err:
        await session(handler).login()
    said = str(err.value)
    assert "two-factor" in said
    assert "mfa_code" in said


async def test_a_wrong_password_says_so_and_says_it_will_not_retry():
    """n8n allows five login attempts per minute per address. A retry loop
    locks Jarvis out of the instance it is trying to reach."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"code": 401, "message": "Wrong username or password"})

    with pytest.raises(SessionError) as err:
        await session(handler).login()
    assert "five login attempts" in str(err.value)
    assert len(calls) == 1


async def test_an_instance_with_no_rest_login_says_which_setting_moved():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(SessionError) as err:
        await session(handler).login()
    assert "rest_path" in str(err.value)


async def test_a_login_that_sets_no_cookie_blames_the_proxy_not_the_password():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    with pytest.raises(SessionError) as err:
        await session(handler).login()
    assert "Set-Cookie" in str(err.value)


async def test_no_login_configured_makes_zero_requests():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200)

    box = N8nSession(URL, "", "", transport=httpx.MockTransport(handler))
    assert not box.configured
    assert "only an API key" in box.why_not
    with pytest.raises(SessionError):
        await box.request("GET", "settings")
    assert calls == []


# ---------------------------------------------------------------------------
# staying logged in
# ---------------------------------------------------------------------------
async def test_a_rotated_cookie_is_picked_up_from_an_ordinary_response():
    """n8n rotates the JWT once inside the refresh window, on whatever request
    happens to be in flight. A client that only reads Set-Cookie on the login
    keeps using a token that is about to stop working."""
    rotated = TOKEN + "-second"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return _login_ok(request)
        return httpx.Response(
            200,
            json={"data": {}},
            headers={"Set-Cookie": f"{COOKIE_NAME}={rotated}; Path=/"},
        )

    box = session(handler)
    await box.request("GET", "settings")
    assert box._token == rotated


async def test_a_dead_session_is_re_minted_exactly_once():
    """One retry, not a loop: the 5/min login limit is the reason, and a
    second 401 means the credentials are the problem, not the cookie."""
    logins: list[int] = []
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            logins.append(1)
            return _login_ok(request)
        calls.append(1)
        return httpx.Response(401, json={"status": "error", "message": "Unauthorized"})

    box = session(handler)
    response = await box.request("GET", "settings")
    assert response.status_code == 401
    assert len(logins) == 2, "one initial login, one re-login"
    assert len(calls) == 2, "and then it stopped"


async def test_the_cookie_goes_out_on_every_request():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return _login_ok(request)
        seen.append(request.headers.get("cookie", ""))
        return httpx.Response(200, json={})

    box = session(handler)
    await box.request("GET", "settings")
    await box.request("GET", "types/nodes.json")
    assert seen == [f"{COOKIE_NAME}={TOKEN}"] * 2


async def test_a_moved_rest_prefix_is_honoured():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _login_ok(request)

    await session(handler, rest_path="/n8n-rest").login()
    assert seen == ["/n8n-rest/login"]


# ---------------------------------------------------------------------------
# the secrets
# ---------------------------------------------------------------------------
async def test_neither_the_password_nor_the_cookie_survives_into_an_error():
    """httpx quotes the request in its exceptions, the integration quotes the
    exception in a tool result, and a tool result is read by the model and
    drawn in the console. A cookie leaked that way is a bearer credential for
    the whole instance — including the endpoint that mints API keys."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return _login_ok(request)
        raise httpx.ConnectError(
            f"connection failed while sending Cookie: {COOKIE_NAME}={TOKEN}"
        )

    box = session(handler, password="correct-horse-battery-staple")
    with pytest.raises(SessionError) as err:
        await box.request("GET", "settings")
    said = str(err.value)
    assert TOKEN not in said
    assert "correct-horse-battery-staple" not in said
    assert "***" in said


def test_scrub_leaves_short_secrets_alone():
    """A two-character password would turn every occurrence of those letters
    into asterisks, and an unreadable error is its own kind of failure."""
    box = N8nSession(URL, "a@b.c", "ab")
    assert box.scrub("all is ab well") == "all is ab well"
