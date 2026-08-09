"""Regressions from the adversarial review pass.

Every test here is tied to a defect that was live in the service. Each one
fails against the code as it stood before the fix; the comment above each
block says which invariant it pins down.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from jarvis_browser.app import (
    create_app,
    get_with_checked_redirects,
)
from jarvis_browser.browser import FakeBackend, FetchResult
from jarvis_browser.config import Settings
from jarvis_browser.crawl import CrawlLimits, crawl, same_origin
from jarvis_browser.extract import MAX_LINK_URL_CHARS, extract
from jarvis_browser.safety import (
    ApprovalGate,
    DomainPolicy,
    act_target_violation,
    check_url,
    classify_steps,
    key_tokens,
    raw_authority,
    sanitize_request_url,
)

from conftest import APPROVAL, AUTH, fake_resolver


# ==========================================================================
# 1. URL smuggling: Python and Chromium disagreeing about the host
# ==========================================================================

# `urlsplit` ends the authority at / ? #; the WHATWG parser Chromium uses
# also ends it at a backslash. So `https://evil.net\@good.com/` is good.com
# to the policy check and evil.net to the browser.

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://evil.net\\@example.com/", "evil.net\\@example.com"),
        ("https://example.com/a\\b", "example.com"),
        ("https://example.com", "example.com"),
        ("https://user:pw@example.com/x", "user:pw@example.com"),
        ("https://example.com?q=a\\b", "example.com"),
        ("not a url", ""),
    ],
)
def test_raw_authority_sees_the_unparsed_authority(url, expected):
    assert raw_authority(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.net\\@example.com/",
        "https://127.0.0.1\\@example.com/",
        "http://evil.net\\.example.com/",
    ],
)
def test_check_url_refuses_a_backslash_authority(url):
    reason = check_url(url, resolver=fake_resolver)
    assert reason and "backslash" in reason


def test_sanitize_request_url_refuses_rather_than_silently_rewriting():
    clean, reason = sanitize_request_url("https://evil.net\\@example.com/")
    assert clean == ""
    assert reason and "backslash" in reason


def test_sanitize_request_url_passes_a_normal_url_through():
    clean, reason = sanitize_request_url("  https://user:pw@example.com/x  ")
    assert reason is None
    assert clean == "https://example.com/x"


def test_act_refuses_a_backslash_smuggled_goto(client, backend):
    """DEFECT: the act allowlist saw example.com, Chromium would see evil.net."""
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    backend.interactions.clear()
    r = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://evil.net\\@example.com/"},
            {"action": "scroll", "amount": 100},
        ]},
    )
    assert r.status_code == 403
    assert "backslash" in r.json()["detail"]
    assert [i for i in backend.interactions if i["op"] == "step"] == []


def test_fetch_refuses_a_backslash_smuggled_url(client, backend):
    backend.interactions.clear()
    r = client.post(
        "/fetch", headers=AUTH,
        json={"url": "https://evil.net\\@example.com/"},
    )
    assert r.status_code == 403
    assert backend.interactions == []


# The guard used to validate a normalised copy and then hand the RAW step to
# the browser, so credentials the check stripped were still sent to the site.
def test_act_navigates_the_url_the_policy_approved_not_the_raw_one(
    client, backend
):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    backend.interactions.clear()
    r = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://user:hunter2@example.com/"},
        ]},
    )
    assert r.status_code == 200
    gotos = [i for i in backend.interactions if i.get("action") == "goto"]
    assert [g["url"] for g in gotos] == ["https://example.com/"]
    assert "hunter2" not in repr(backend.interactions)


def test_gated_steps_are_stored_already_normalised(client, backend):
    """The consent prompt must show the URL that will actually be visited."""
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    r = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://user:pw@example.com/login"},
            {"action": "click", "selector": "#submit-btn"},
        ]},
    )
    body = r.json()
    assert body["status"] == "approval_required"
    assert body["steps"][0]["url"] == "https://example.com/login"


# ==========================================================================
# 2. Modifier chords bypassing the "this can submit a form" gate
# ==========================================================================

@pytest.mark.parametrize(
    "value",
    ["Enter", "enter", "NumpadEnter", "Control+Enter", "Shift+Enter",
     "Meta+Enter", "Control+Shift+Enter", "Alt+NumpadEnter", " Control+Enter "],
)
def test_key_chords_containing_a_submit_key_are_gated(value):
    """DEFECT: Ctrl+Enter is 'send' in Gmail/Slack/GitHub and ran unattended."""
    reasons = classify_steps(
        [{"action": "press", "selector": "#msg", "value": value}],
        keywords=(), selectors=(),
    )
    assert reasons, f"{value!r} should need approval"


@pytest.mark.parametrize("value", ["Tab", "ArrowDown", "Control+A", "Escape"])
def test_harmless_keys_are_still_not_gated(value):
    assert classify_steps(
        [{"action": "press", "selector": "#msg", "value": value}],
        keywords=(), selectors=(),
    ) == []


def test_key_tokens_splits_a_chord():
    assert key_tokens("Control+Shift+Enter") == {
        "control+shift+enter", "control", "shift", "enter",
    }
    assert key_tokens("") == set()


def test_control_enter_is_gated_end_to_end(client, backend):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    backend.interactions.clear()
    r = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://example.com/"},
            {"action": "press", "selector": "#msg", "value": "Control+Enter"},
        ]},
    )
    body = r.json()
    assert body["status"] == "approval_required"
    assert body["executed"] is False
    assert [i for i in backend.interactions if i["op"] == "step"] == []


# ==========================================================================
# 3. robots.txt redirect chain — blind SSRF at LAN services
# ==========================================================================

class _Resp:
    def __init__(self, status_code=200, text="", location=None):
        self.status_code = status_code
        self.text = text
        self.headers = {"location": location} if location else {}


class _FakeClient:
    """Records every URL it is asked for. follow_redirects is OUR job."""

    def __init__(self, script: dict[str, _Resp]):
        self.script = script
        self.requested: list[str] = []

    async def get(self, url):
        self.requested.append(url)
        return self.script.get(url, _Resp(404))


def _run(coro):
    return asyncio.run(coro)


def test_robots_redirect_to_loopback_is_refused():
    """DEFECT: follow_redirects=True let a public robots.txt aim at HA."""
    client = _FakeClient({
        "https://example.com/robots.txt": _Resp(
            302, location="http://127.0.0.1:8123/api/states"
        ),
        "http://127.0.0.1:8123/api/states": _Resp(200, "secrets"),
    })
    resp = _run(get_with_checked_redirects(
        client, "https://example.com/robots.txt", allowlist=()
    ))
    assert resp is None
    assert client.requested == ["https://example.com/robots.txt"]


def test_robots_redirect_to_metadata_is_refused():
    client = _FakeClient({
        "https://example.com/robots.txt": _Resp(
            301, location="http://169.254.169.254/latest/meta-data/"
        ),
    })
    assert _run(get_with_checked_redirects(
        client, "https://example.com/robots.txt", allowlist=()
    )) is None
    assert "169.254.169.254" not in "".join(client.requested)


def test_robots_follows_a_public_redirect():
    client = _FakeClient({
        "https://example.com/robots.txt": _Resp(
            308, location="https://other.net/robots.txt"
        ),
        "https://other.net/robots.txt": _Resp(200, "User-agent: *"),
    })
    resp = _run(get_with_checked_redirects(
        client, "https://example.com/robots.txt", allowlist=()
    ))
    assert resp is not None and resp.text == "User-agent: *"
    assert len(client.requested) == 2


def test_robots_redirect_chain_is_bounded():
    loop = {
        "https://example.com/robots.txt": _Resp(
            302, location="https://example.com/robots.txt"
        ),
    }
    client = _FakeClient(loop)
    assert _run(get_with_checked_redirects(
        client, "https://example.com/robots.txt", allowlist=(), max_redirects=2
    )) is None
    assert len(client.requested) == 3


# ==========================================================================
# 4. Redirects walking a read off the domain policy
# ==========================================================================

class _RedirectingBackend(FakeBackend):
    """Every fetch lands somewhere else, as a 302 chain would."""

    lands_at: str = "https://evil.net/"

    async def fetch(self, url, *, render=True, javascript=True):
        self.interactions.append({"op": "fetch", "url": url})
        return FetchResult(
            html="<html><title>Evil</title><body>pwned</body></html>",
            final_url=self.lands_at,
            status=200,
            nbytes=42,
        )


def test_fetch_refuses_a_redirect_off_the_read_allowlist(settings):
    """DEFECT: the site chose the redirect, so the allowlist must re-apply."""
    s = settings.with_overrides(allowlist=("example.com",))
    backend = _RedirectingBackend()
    app = create_app(s, backend=backend, searcher=None, robots_fetch=None)
    with TestClient(app) as c:
        r = c.post("/fetch", headers=AUTH, json={"url": "https://example.com/"})
    assert r.status_code == 403
    assert "redirect" in r.json()["detail"]


def test_fetch_allows_a_same_host_redirect(settings):
    s = settings.with_overrides(allowlist=("example.com",))
    backend = _RedirectingBackend()
    backend.lands_at = "https://example.com/landing"
    app = create_app(s, backend=backend, searcher=None, robots_fetch=None)
    with TestClient(app) as c:
        r = c.post("/fetch", headers=AUTH, json={"url": "https://example.com/"})
    assert r.status_code == 200
    assert r.json()["final_url"] == "https://example.com/landing"


@pytest.mark.asyncio
async def test_crawl_skips_a_page_that_redirected_somewhere_blocked():
    async def fetch(url):
        return FetchResult(
            html="<html><body>x</body></html>",
            final_url="http://127.0.0.1:8123/",
            status=200,
            nbytes=10,
        )

    def url_ok(url):
        return check_url(url, resolver=fake_resolver)

    result = await crawl(
        "https://example.com/",
        CrawlLimits(max_pages=3, max_depth=1, per_domain_interval=0.0,
                    respect_robots=False),
        fetch=fetch,
        url_ok=url_ok,
    )
    assert result.pages == []
    assert result.skipped.get("blocked_redirect") == 1


# ==========================================================================
# 5. Hostile page content crashing the crawler
# ==========================================================================

def test_same_origin_survives_an_out_of_range_port():
    """DEFECT: `.port` raises on access, which used to 500 /crawl."""
    assert same_origin("https://example.com:99999/x", "https://example.com/") \
        is False
    assert same_origin("https://example.com/x", "https://example.com/y") is True


def test_check_url_refuses_an_out_of_range_port():
    reason = check_url("https://example.com:99999/", resolver=fake_resolver)
    assert reason and "port" in reason


def test_crawl_does_not_500_on_a_hostile_link(client):
    client.app_backend.pages["https://example.com/"] = (
        "<html><body>"
        "<a href='https://example.com:99999/x'>bad port</a>"
        "<a href='http://[::1'>bad ipv6</a>"
        "</body></html>"
    )
    r = client.post(
        "/crawl", headers=AUTH,
        json={"start_url": "https://example.com/", "max_pages": 3,
              "max_depth": 1},
    )
    assert r.status_code == 200
    assert len(r.json()["pages"]) == 1


def test_extract_drops_absurdly_long_links():
    long_href = "https://example.com/" + "a" * (MAX_LINK_URL_CHARS + 10)
    html = (
        f"<html><body><a href='{long_href}'>x</a>"
        "<a href='https://example.com/ok'>ok</a></body></html>"
    )
    links = extract(html, base_url="https://example.com/").links
    assert [link.url for link in links] == ["https://example.com/ok"]


# ==========================================================================
# 6. The executor must consult the gate, not its caller
# ==========================================================================

def test_execute_refuses_a_request_the_gate_has_not_approved(client, backend):
    """safety.is_executable claimed to be consulted; it was not."""
    from jarvis_browser import app as app_module

    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    gated = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://example.com/"},
            {"action": "click", "selector": "input[type=submit]"},
        ]},
    ).json()
    rid = gated["request_id"]
    backend.interactions.clear()

    # Somebody calls the executor directly with a request the gate never
    # approved. It must refuse, not run.
    with pytest.raises(Exception) as exc:
        asyncio.run(
            app_module._execute(
                client.app, sid, gated["steps"],
                approved=True, request_id=rid,
            )
        )
    assert "executable" in str(exc.value)
    assert [i for i in backend.interactions if i["op"] == "step"] == []


def test_approve_still_executes_normally(client, backend):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    gated = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://example.com/"},
            {"action": "click", "selector": "input[type=submit]"},
        ]},
    ).json()
    r = client.post(
        "/approve", headers={**AUTH, **APPROVAL},
        json={"request_id": gated["request_id"], "approved": True},
    )
    assert r.status_code == 200
    assert r.json()["executed"] is True


# ==========================================================================
# 7. Leaks: the approval table and expired sessions were never reaped
# ==========================================================================

def test_purge_expired_actually_drops_finished_requests():
    """DEFECT: the `state in (done, denied, expired)` arm was dead code."""
    gate = ApprovalGate("s", ttl_seconds=300.0)
    done = gate.request("sess", [{"action": "click"}], ["r"])
    gate.approve(done.request_id, "s")
    gate.mark_done(done.request_id)
    denied = gate.request("sess", [{"action": "click"}], ["r"])
    gate.deny(denied.request_id, "s")
    live = gate.request("sess", [{"action": "click"}], ["r"])

    assert gate.purge_expired() == 2
    assert gate.get(done.request_id) is None
    assert gate.get(denied.request_id) is None
    assert gate.get(live.request_id) is not None


def test_purge_expired_expires_stale_requests():
    gate = ApprovalGate("s", ttl_seconds=-1.0)  # already past its TTL
    req = gate.request("sess", [{"action": "click"}], ["r"])
    assert gate.purge_expired() == 1
    assert gate.get(req.request_id) is None


def test_purged_request_cannot_be_approved(client, backend):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    gated = client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://example.com/"},
            {"action": "click", "selector": "input[type=submit]"},
        ]},
    ).json()
    client.app.state.gate.purge_expired()
    client.app.state.gate._ttl = -1.0
    client.app.state.gate.purge_expired()
    backend.interactions.clear()
    r = client.post(
        "/approve", headers={**AUTH, **APPROVAL},
        json={"request_id": gated["request_id"], "approved": True},
    )
    assert r.status_code == 404
    assert [i for i in backend.interactions if i["op"] == "step"] == []


@pytest.mark.asyncio
async def test_janitor_closes_an_expired_session():
    from jarvis_browser.app import _janitor
    from jarvis_browser.sessions import SessionManager

    clock = {"t": 0.0}
    wiped: list[str] = []
    backend = FakeBackend()
    mgr = SessionManager(
        backend, ttl=10.0, clock=lambda: clock["t"],
        wipe=wiped.append, mkdir=lambda: "/tmp/nope",
    )
    session = await mgr.create()
    assert len(mgr) == 1

    class _App:
        class state:
            sessions = mgr
            gate = ApprovalGate("s")

    clock["t"] = 100.0
    task = asyncio.create_task(_janitor(_App, 0.001))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(mgr) == 0
    assert wiped == [session.profile_dir]
    assert session.session_id not in backend.sessions


# ==========================================================================
# 8. A mid-batch navigation must not carry steps off the act allowlist
# ==========================================================================

POLICY = DomainPolicy(act_allowlist=("example.com",))


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://example.com/x", True),
        ("https://sub.example.com/x", True),
        ("about:blank", True),
        ("", True),
        ("https://evil.net/x", False),
        ("http://evil.net/", False),
        ("data:text/html,<h1>hi", False),
        ("javascript:alert(1)", False),
        ("file:///etc/passwd", False),
    ],
)
def test_act_target_violation(url, ok):
    """DEFECT: a 302 mid-batch left the remaining steps on any domain."""
    assert (act_target_violation(url, POLICY) is None) is ok


def test_act_target_violation_honours_the_denylist():
    policy = DomainPolicy(
        denylist=("bank.example.com",), act_allowlist=("example.com",)
    )
    assert act_target_violation("https://bank.example.com/", policy)


@pytest.mark.asyncio
async def test_playwright_backend_stops_when_a_page_drifts_offsite():
    """The real backend re-checks page.url after every step."""
    from jarvis_browser.browser import PlaywrightBackend

    class _Page:
        url = "https://example.com/"

        async def goto(self, url, **kw):
            # The site 302s us somewhere the act allowlist never covered.
            self.url = "https://evil.net/landing"

        async def click(self, selector, **kw):
            raise AssertionError("must not click after drifting offsite")

        async def content(self):
            return "<html></html>"

    settings = Settings(act_allowlist=("example.com",))
    be = PlaywrightBackend(settings)
    page = _Page()
    be._pages["s1"] = page

    outcomes, _ = await be.act("s1", [
        {"action": "goto", "url": "https://example.com/"},
        {"action": "click", "selector": "#buy"},
    ])
    assert outcomes[-1].status == "error"
    assert "act allowlist" in outcomes[-1].detail
    assert len(outcomes) == 2  # goto ok, then the drift error; no click


# ==========================================================================
# 9. An approval is consent for THESE steps on THAT page
# ==========================================================================

def _gate_a_click(client, sid):
    return client.post(
        f"/session/{sid}/act",
        headers=AUTH,
        json={"steps": [{"action": "click", "selector": "#checkout"}]},
    ).json()


def test_gated_response_names_the_page_the_steps_will_run_on(client):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    client.post(
        f"/session/{sid}/act", headers=AUTH,
        json={"steps": [{"action": "goto", "url": "https://example.com/"}]},
    )
    body = _gate_a_click(client, sid)
    assert body["status"] == "approval_required"
    assert body["page_url"] == "https://example.com/"


def test_approval_is_void_if_the_session_navigated_since(client, backend):
    """Confused deputy: approve 'click #checkout', then retarget the page."""
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    client.post(
        f"/session/{sid}/act", headers=AUTH,
        json={"steps": [{"action": "goto", "url": "https://example.com/"}]},
    )
    gated = _gate_a_click(client, sid)

    # An ungated batch moves the session while the human is deciding.
    client.post(
        f"/session/{sid}/act", headers=AUTH,
        json={"steps": [{"action": "goto", "url": "https://shop.example.com/"}]},
    )
    backend.interactions.clear()

    r = client.post(
        "/approve", headers={**AUTH, **APPROVAL},
        json={"request_id": gated["request_id"], "approved": True},
    )
    assert r.status_code == 409
    assert "navigated" in r.json()["detail"]
    assert [i for i in backend.interactions if i["op"] == "step"] == []

    # And it was consumed: no second bite once the page is put back.
    client.post(
        f"/session/{sid}/act", headers=AUTH,
        json={"steps": [{"action": "goto", "url": "https://example.com/"}]},
    )
    backend.interactions.clear()
    again = client.post(
        "/approve", headers={**AUTH, **APPROVAL},
        json={"request_id": gated["request_id"], "approved": True},
    )
    assert again.status_code == 409
    assert [i for i in backend.interactions if i["op"] == "step"] == []


def test_approval_on_an_unmoved_session_still_runs(client, backend):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    client.post(
        f"/session/{sid}/act", headers=AUTH,
        json={"steps": [{"action": "goto", "url": "https://example.com/"}]},
    )
    gated = _gate_a_click(client, sid)
    r = client.post(
        "/approve", headers={**AUTH, **APPROVAL},
        json={"request_id": gated["request_id"], "approved": True},
    )
    assert r.status_code == 200
    assert r.json()["executed"] is True


# ==========================================================================
# 10. The fetch->act tripwire also catches a stripped-tag paste
# ==========================================================================

def test_act_rejects_a_step_carrying_the_fence_notice_without_the_tags(
    client, backend
):
    sid = client.post("/session", headers=AUTH, json={}).json()["session_id"]
    backend.interactions.clear()
    fetched = client.post(
        "/fetch", headers=AUTH, json={"url": "https://example.com/"}
    ).json()["text"]
    # A caller strips our markers but pastes the rest of what it fetched.
    body_only = fetched.replace("<untrusted_web_content>", "").replace(
        "</untrusted_web_content>", ""
    )
    backend.interactions.clear()
    r = client.post(
        f"/session/{sid}/act", headers=AUTH,
        json={"steps": [
            {"action": "goto", "url": "https://example.com/"},
            {"action": "type", "selector": "#q", "value": body_only[:3000]},
        ]},
    )
    assert r.status_code == 422
    assert "fenced web content" in r.json()["detail"]
    assert [i for i in backend.interactions if i["op"] == "step"] == []
