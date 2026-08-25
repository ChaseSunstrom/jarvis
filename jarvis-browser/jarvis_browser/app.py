"""jarvis-browser — FastAPI service (port 8210).

Auth: EVERY route, ``/healthz`` included, requires
``Authorization: Bearer $JARVIS_BROWSER_TOKEN``. ``/approve`` ADDITIONALLY
requires ``X-Approval-Secret: $BROWSER_APPROVAL_SECRET`` — that header is
only ever sent by the human-facing approval path, never by the model, and
possession of the API token alone can never approve anything.

The read/write split is the shape of this API:

    /fetch /search /crawl /screenshot   read  — SSRF + read policy, output fenced
    /session/{id}/act                   write — act_allowlist + sensitive gate
    /approve                            the only door from "gated" to "executed"

Content returned by the read endpoints is wrapped in
``<untrusted_web_content>``. No endpoint accepts fetched content as an
instruction: /act takes a caller-authored step list, and a step carrying
fence markers is refused outright (the fetch->act chain is the exact attack
this service exists to prevent).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .browser import BrowserError, PlaywrightBackend
from .config import Settings, load_settings
from .crawl import CrawlConfigError, CrawlLimits, crawl
from .extract import extract
from .safety import (
    ApprovalGate,
    DomainPolicy,
    GateError,
    check_url,
    classify_steps,
    contains_fenced_content,
    fence,
    sanitize_request_url,
    sanitize_untrusted,
    strip_url_credentials,
)
from .search import (
    AgentSearchSearcher,
    SearchFailed,
    SearchNotConfigured,
    SearxngSearcher,
)
from .sessions import SessionError, SessionManager

log = logging.getLogger("jarvis.browser")
audit = logging.getLogger("jarvis.browser.audit")

PORT = 8210


# --------------------------------------------------------------------------
# Auth dependencies
# --------------------------------------------------------------------------

def require_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    token = request.app.state.settings.api_token
    expected = f"Bearer {token}"
    if not token or not authorization or not hmac.compare_digest(
        authorization.encode(), expected.encode()
    ):
        raise HTTPException(401, "bad or missing bearer token")


def require_approval_secret(
    request: Request, x_approval_secret: str | None = Header(default=None)
) -> None:
    secret = request.app.state.settings.approval_secret
    if not secret or not x_approval_secret or not hmac.compare_digest(
        x_approval_secret.encode(), secret.encode()
    ):
        raise HTTPException(403, "approval secret missing or wrong")


AUTH = [Depends(require_token)]
AUTH_AND_APPROVAL = [Depends(require_token), Depends(require_approval_secret)]


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------

class FetchBody(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    render: bool = True


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=50)


class CrawlBody(BaseModel):
    start_url: str = Field(min_length=1, max_length=4096)
    max_pages: int = Field(default=10, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=10)
    same_origin_only: bool = True
    url_include: list[str] = Field(default_factory=list, max_length=8)
    url_exclude: list[str] = Field(default_factory=list, max_length=8)


class ScreenshotBody(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    full_page: bool = False


class SessionBody(BaseModel):
    javascript: bool | None = None


class Step(BaseModel):
    action: Literal[
        "goto", "click", "type", "select", "scroll",
        "wait_for", "press", "extract", "upload",
    ]
    selector: str | None = Field(default=None, max_length=1000)
    text: str | None = Field(default=None, max_length=4000)
    value: str | None = Field(default=None, max_length=4000)
    url: str | None = Field(default=None, max_length=4096)
    amount: int | None = Field(default=None, ge=-20000, le=20000)


class ActBody(BaseModel):
    steps: list[Step] = Field(min_length=1, max_length=50)


class ApproveBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    approved: bool = False


# --------------------------------------------------------------------------
# App factory
# --------------------------------------------------------------------------

def create_app(
    settings: Settings | None = None,
    *,
    backend=None,
    searcher=None,
    robots_fetch=None,
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s: Settings = app.state.settings
        # Fail closed: an unauthenticated browser automation service on the
        # LAN is a remote-code-execution-shaped hole.
        if not s.api_token or not s.approval_secret:
            raise RuntimeError(
                "JARVIS_BROWSER_TOKEN and BROWSER_APPROVAL_SECRET must both "
                "be set (see .env.example / README)"
            )
        if app.state.backend is None:
            app.state.backend = PlaywrightBackend(s)
        app.state.sessions = SessionManager(
            app.state.backend,
            ttl=s.session_ttl,
            max_sessions=s.max_sessions,
            root=s.session_root or None,
        )
        app.state.gate = ApprovalGate(s.approval_secret, s.approval_ttl)
        app.state.policy = DomainPolicy(
            allowlist=s.allowlist,
            denylist=s.denylist,
            act_allowlist=s.act_allowlist,
        )
        if app.state.searcher is None:
            # AgentSearch wins when it is configured, because configuring it is
            # a deliberate act and it is strictly the richer path. It is not a
            # fallback chain: if it is set and down, /search fails saying so
            # rather than quietly reverting to the bare SearXNG behind it and
            # returning results that are missing everything AgentSearch was
            # chosen for.
            if s.agent_search_url:
                app.state.searcher = AgentSearchSearcher(
                    s.agent_search_url,
                    token=s.agent_search_token,
                    strategy=s.agent_search_strategy,
                )
                log.info(
                    "search: AgentSearch at %s%s",
                    s.agent_search_url,
                    f" (strategy {s.agent_search_strategy})" if s.agent_search_strategy else "",
                )
            else:
                app.state.searcher = SearxngSearcher(s.searxng_url)
        if app.state.robots_fetch is None:
            app.state.robots_fetch = _make_robots_fetch(s)
        # Nothing else ever reaps: SessionManager.reap ran only inside
        # create(), so an expired session kept its browser context and its
        # profile dir alive until someone happened to open a new one, and the
        # gate's request table grew for the life of the process.
        janitor = asyncio.create_task(_janitor(app, s.janitor_interval))
        app.state.janitor = janitor
        try:
            yield
        finally:
            janitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await janitor
            # Every session dir is wiped on the way out — no cookies survive
            # a restart.
            await app.state.sessions.close_all()
            try:
                await app.state.backend.close()
            except Exception:
                pass

    app = FastAPI(
        title="jarvis-browser",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,      # no interactive docs on a security boundary
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.backend = backend
    app.state.searcher = searcher
    app.state.robots_fetch = robots_fetch

    _register_handlers(app)
    _register_routes(app)
    return app


async def _janitor(app: FastAPI, interval: float) -> None:
    """Close expired sessions and drop finished approvals, forever."""
    while True:
        await asyncio.sleep(interval)
        try:
            closed = await app.state.sessions.reap()
            purged = app.state.gate.purge_expired()
            if closed or purged:
                log.info("janitor: %d sessions, %d approvals", closed, purged)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("janitor pass failed")


REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_ROBOTS_REDIRECTS = 3


async def get_with_checked_redirects(
    client, url: str, *, allowlist=(), max_redirects: int = MAX_ROBOTS_REDIRECTS
):
    """GET ``url``, re-running the SSRF check on every redirect hop.

    ``follow_redirects=True`` would hand the whole chain to httpx, which
    checks nothing: ``http://public.example/robots.txt`` -> 302 ->
    ``http://127.0.0.1:8123/`` is a blind SSRF straight at Home Assistant on
    the same host. Each hop is therefore validated before it is requested.

    Returns the final 2xx response, or ``None`` if any hop is refused, the
    chain is too long, or the status is not 200.
    """
    for _ in range(max_redirects + 1):
        reason = check_url(url, allowlist=allowlist)
        if reason:
            log.warning("refused robots hop %s: %s", url, reason)
            return None
        resp = await client.get(url)
        if resp.status_code in REDIRECT_STATUSES:
            location = resp.headers.get("location")
            if not location:
                return None
            url = urljoin(url, location)
            continue
        return resp if resp.status_code == 200 else None
    return None


def _make_robots_fetch(s: Settings):
    """Fetch robots.txt over plain HTTP — no browser, no JS, SSRF-checked."""

    async def _fetch(url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=False,   # every hop is re-checked by hand
                headers={"User-Agent": s.user_agent},
            ) as client:
                resp = await get_with_checked_redirects(
                    client, url, allowlist=s.lan_allowlist
                )
        except httpx.HTTPError:
            return None
        if resp is None:
            return None
        return resp.text[:512_000]

    return _fetch


def _register_handlers(app: FastAPI) -> None:
    @app.exception_handler(SessionError)
    async def _session_error(request: Request, exc: SessionError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(GateError)
    async def _gate_error(request: Request, exc: GateError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(BrowserError)
    async def _browser_error(request: Request, exc: BrowserError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(CrawlConfigError)
    async def _crawl_error(request: Request, exc: CrawlConfigError):
        return JSONResponse({"detail": str(exc)}, status_code=422)


# --------------------------------------------------------------------------
# Shared guards
# --------------------------------------------------------------------------

def _guard_read(app: FastAPI, url: str) -> str:
    """SSRF + read-policy check. Returns the credential-stripped URL."""
    s: Settings = app.state.settings
    url, reason = sanitize_request_url(url)
    if reason:
        audit.warning("blocked read reason=%s", reason)
        raise HTTPException(403, f"refused: {reason}")
    reason = check_url(url, allowlist=s.lan_allowlist)
    if reason:
        audit.warning("blocked read url=%s reason=%s", url, reason)
        raise HTTPException(403, f"refused: {reason}")
    host = _host_of(url)
    reason = app.state.policy.read_reason(host)
    if reason:
        audit.warning("blocked read url=%s reason=%s", url, reason)
        raise HTTPException(403, f"refused: {reason}")
    return url


def _guard_act(app: FastAPI, url: str) -> str:
    """SSRF + the stricter act policy. Approval can never bypass this."""
    s: Settings = app.state.settings
    url, reason = sanitize_request_url(url)
    if reason:
        audit.warning("blocked act reason=%s", reason)
        raise HTTPException(403, f"refused: {reason}")
    reason = check_url(url, allowlist=s.lan_allowlist)
    if reason:
        audit.warning("blocked act url=%s reason=%s", url, reason)
        raise HTTPException(403, f"refused: {reason}")
    reason = app.state.policy.act_reason(_host_of(url))
    if reason:
        audit.warning("blocked act url=%s reason=%s", url, reason)
        raise HTTPException(403, f"refused: {reason}")
    return url


def _guard_final_url(app: FastAPI, requested: str, final_url: str) -> str:
    """Re-apply the read policy to where a redirect actually landed.

    The initial URL is checked before we navigate, but a 302 is chosen by the
    site, not by us. Chromium's route filter still blocks private addresses,
    so this closes the other half: an allowlisted host must not be able to
    bounce us onto a host the operator never allowed.
    """
    s: Settings = app.state.settings
    final = strip_url_credentials((final_url or "").strip())
    if not final:
        return requested
    if _host_of(final) == _host_of(requested):
        return final
    reason = check_url(final, allowlist=s.lan_allowlist) or (
        app.state.policy.read_reason(_host_of(final))
    )
    if reason:
        audit.warning(
            "blocked redirect %s -> %s reason=%s", requested, final, reason
        )
        raise HTTPException(
            403, f"refused: redirect to {final[:200]!r} — {reason}"
        )
    return final


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _page_payload(page_html: str, final_url: str, s: Settings) -> dict:
    """Extract + fence. The single place read content becomes a response."""
    parsed = extract(
        page_html,
        base_url=final_url,
        max_chars=s.max_text_chars,
        max_links=s.max_links,
    )
    return {
        "final_url": final_url,
        "title": sanitize_untrusted(parsed.title),
        "content_is_untrusted": True,
        "text": fence(parsed.text, source=final_url),
        "links": [
            {"url": link.url, "text": sanitize_untrusted(link.text)}
            for link in parsed.links
        ],
        "meta": {
            k: sanitize_untrusted(v) for k, v in parsed.meta.items()
        },
        "truncated": parsed.truncated,
        "char_count": parsed.char_count,
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def _register_routes(app: FastAPI) -> None:

    @app.get("/healthz", dependencies=AUTH)
    async def healthz(probe: bool = True):
        """Alive, and — unless asked not to — able to open a page.

        `browser` is here because of what its absence allowed: an image whose
        chromium could not load its shared libraries answered this route 200
        for weeks while every /fetch returned 500. "Up" and "able to do the job"
        are different questions and this route now answers both.

        The probe launches the browser once and the result is cached, so the
        container's own healthcheck (every 30 s) costs nothing after the first.
        The status stays `ok` when it fails, deliberately: the security core —
        auth, the SSRF policy, the approval gate — is unaffected by a missing
        browser, and the Dockerfile documents building without one. What must
        not happen is that nobody can TELL.
        """
        s: Settings = app.state.settings
        browser = "unknown"
        if probe:
            if getattr(app.state, "browser_probe", None) is None:
                try:
                    await app.state.backend.start()
                    app.state.browser_probe = "ok"
                except Exception as exc:  # noqa: BLE001 - the answer is the message
                    app.state.browser_probe = str(exc).strip()[:300] or type(exc).__name__
            browser = app.state.browser_probe
        return {
            "status": "ok",
            "backend": type(app.state.backend).__name__,
            "browser": browser,
            "sessions": len(app.state.sessions),
            "searxng_configured": bool(s.searxng_url),
            "act_allowlist_size": len(s.act_allowlist),
        }

    # ---------------------------------------------------------------- read
    @app.post("/fetch", dependencies=AUTH)
    async def fetch(body: FetchBody):
        s: Settings = app.state.settings
        url = _guard_read(app, body.url)
        result = await app.state.backend.fetch(
            url, render=body.render, javascript=s.javascript_enabled
        )
        final = _guard_final_url(app, url, result.final_url or url)
        audit.info("fetch url=%s final=%s status=%s", url, final, result.status)
        payload = _page_payload(result.html, final, s)
        payload["status"] = result.status
        payload["requested_url"] = url
        return payload

    @app.post("/search", dependencies=AUTH)
    async def search(body: SearchBody):
        try:
            results = await app.state.searcher(body.query, body.limit)
        except SearchNotConfigured as exc:
            raise HTTPException(503, str(exc))
        except SearchFailed as exc:
            raise HTTPException(502, str(exc))
        blob = "\n\n".join(
            f"{r.title}\n{r.url}\n{r.snippet}" for r in results
        )
        return {
            "query": body.query,
            "count": len(results),
            "results": [r.as_dict() for r in results],
            "content_is_untrusted": True,
            "text": fence(blob, source="searxng"),
        }

    @app.post("/crawl", dependencies=AUTH)
    async def crawl_route(body: CrawlBody):
        s: Settings = app.state.settings
        start = _guard_read(app, body.start_url)

        limits = CrawlLimits(
            # The request may ask for less than the operator ceiling, never
            # more.
            max_pages=min(body.max_pages, s.max_pages_ceiling),
            max_depth=min(body.max_depth, s.max_depth_ceiling),
            same_origin_only=body.same_origin_only,
            url_include=tuple(body.url_include),
            url_exclude=tuple(body.url_exclude),
            max_total_bytes=s.crawl_total_bytes,
            budget_seconds=s.crawl_budget_seconds,
            per_domain_interval=s.per_domain_interval,
            respect_robots=s.respect_robots,
            user_agent=s.user_agent,
            max_chars_per_page=s.max_text_chars,
            max_links_per_page=s.max_links,
        )

        def url_ok(url: str) -> str | None:
            reason = check_url(url, allowlist=s.lan_allowlist)
            if reason:
                return reason
            return app.state.policy.read_reason(_host_of(url))

        async def do_fetch(url: str):
            return await app.state.backend.fetch(
                url, render=True, javascript=s.javascript_enabled
            )

        result = await crawl(
            start,
            limits,
            fetch=do_fetch,
            robots_fetch=app.state.robots_fetch,
            url_ok=url_ok,
        )
        audit.info(
            "crawl start=%s pages=%d reason=%s",
            start, len(result.pages), result.stopped_reason,
        )
        return {
            "start_url": result.start_url,
            "stopped_reason": result.stopped_reason,
            "fetched": result.fetched,
            "skipped": result.skipped,
            "total_bytes": result.total_bytes,
            "content_is_untrusted": True,
            "pages": [
                {
                    "url": p.url,
                    "final_url": p.final_url,
                    "depth": p.depth,
                    "status": p.status,
                    "title": sanitize_untrusted(p.title),
                    "text": fence(p.text, source=p.final_url),
                    "bytes": p.nbytes,
                    "links": p.links,
                }
                for p in result.pages
            ],
        }

    @app.post("/screenshot", dependencies=AUTH)
    async def screenshot(body: ScreenshotBody):
        s: Settings = app.state.settings
        url = _guard_read(app, body.url)
        png = await app.state.backend.screenshot(
            url, full_page=body.full_page, javascript=s.javascript_enabled
        )
        audit.info("screenshot url=%s bytes=%d", url, len(png))
        return Response(
            content=png,
            media_type="image/png",
            headers={"X-Jarvis-Source-Url": url[:1000]},
        )

    # ------------------------------------------------------------ sessions
    @app.post("/session", dependencies=AUTH)
    async def create_session(body: SessionBody | None = None):
        s: Settings = app.state.settings
        js = s.javascript_enabled
        if body is not None and body.javascript is not None:
            js = body.javascript
        session = await app.state.sessions.create(javascript=js)
        audit.info("session created id=%s js=%s", session.session_id, js)
        return {
            "session_id": session.session_id,
            "ttl_seconds": s.session_ttl,
            "javascript": js,
            "expires_in": s.session_ttl,
        }

    @app.delete("/session/{session_id}", dependencies=AUTH)
    async def delete_session(session_id: str):
        closed = await app.state.sessions.close(session_id)
        if not closed:
            raise HTTPException(404, "unknown session")
        audit.info("session closed id=%s", session_id)
        return {"session_id": session_id, "status": "closed"}

    # ----------------------------------------------------------- the write
    @app.post("/session/{session_id}/act", dependencies=AUTH)
    async def act(session_id: str, body: ActBody):
        s: Settings = app.state.settings
        session = app.state.sessions.get(session_id)
        steps = [st.model_dump(exclude_none=True) for st in body.steps]

        if len(steps) > s.max_act_steps:
            raise HTTPException(
                422, f"at most {s.max_act_steps} steps per call"
            )

        _reject_fenced(steps)
        _guard_act_targets(app, session, steps)

        reasons = classify_steps(
            steps,
            keywords=s.sensitive_keywords,
            selectors=s.sensitive_selectors,
            submit_keys=s.submit_keys,
        )
        if reasons:
            # NOTHING runs. Not the sensitive step, not the steps before it.
            req = app.state.gate.request(
                session_id, steps, reasons, page_url=session.current_url
            )
            audit.warning(
                "act gated session=%s request_id=%s page=%s reasons=%s",
                session_id, req.request_id, req.page_url, reasons,
            )
            return {
                "status": "approval_required",
                "request_id": req.request_id,
                "session_id": session_id,
                "reasons": reasons,
                # Verbatim, so the consent prompt shows the truth.
                "steps": req.steps,
                "page_url": req.page_url,
                "expires_in": s.approval_ttl,
                "executed": False,
            }

        return await _execute(app, session_id, steps, approved=False)

    @app.post("/approve", dependencies=AUTH_AND_APPROVAL)
    async def approve(body: ApproveBody):
        s: Settings = app.state.settings
        gate: ApprovalGate = app.state.gate

        if not body.approved:
            req = gate.deny(body.request_id, s.approval_secret)
            audit.warning("act DENIED request_id=%s", req.request_id)
            return {
                "request_id": req.request_id,
                "status": "denied",
                "executed": False,
            }

        req = gate.approve(body.request_id, s.approval_secret)
        try:
            # Re-validate: an approval is permission to run THESE steps, not
            # permission to skip the domain policy.
            session = app.state.sessions.get(req.session_id)
            # ...and it is permission to run them on the page the human was
            # shown. If an ungated /act navigated the session elsewhere in
            # the meantime, the consent no longer describes what would
            # happen, so it is void rather than retargeted.
            starts_with_goto = (
                bool(req.steps) and req.steps[0].get("action") == "goto"
            )
            if not starts_with_goto and session.current_url != req.page_url:
                audit.error(
                    "act APPROVAL VOID request_id=%s page moved %s -> %s",
                    req.request_id, req.page_url, session.current_url,
                )
                raise HTTPException(
                    409,
                    "the session has navigated since approval was requested "
                    f"({req.page_url!r} -> {session.current_url!r}); "
                    "re-request approval",
                )
            _guard_act_targets(app, session, req.steps)
            audit.warning(
                "act APPROVED request_id=%s session=%s steps=%s",
                req.request_id, req.session_id, req.steps,
            )
            return await _execute(
                app, req.session_id, req.steps, approved=True,
                request_id=req.request_id,
            )
        finally:
            # Consumed either way: a failed execution does not hand back a
            # re-usable approval.
            gate.mark_done(req.request_id)


def _reject_fenced(steps: list[dict]) -> None:
    """Tripwire against the fetch->act chain.

    If a step carries our ``<untrusted_web_content>`` markers, some caller
    piped page content straight into an automation step. That is the exact
    path the design forbids, so it is refused rather than sanitised.
    """
    for i, step in enumerate(steps):
        blob = " ".join(str(v) for v in step.values())
        if contains_fenced_content(blob):
            audit.error("act step %d carried fenced web content", i)
            raise HTTPException(
                422,
                f"step {i} contains fenced web content: page text may never "
                "be routed into an action without fresh human authorship",
            )


def _guard_act_targets(app: FastAPI, session, steps: list[dict]) -> None:
    """Every page these steps could touch must be on the act_allowlist.

    The guarded URL is written BACK into the step. The browser must navigate
    the exact string the policy approved, never the raw one: Python reads
    ``https://evil.net\\@allowed.com/`` as ``allowed.com`` while Chromium
    reads it as ``evil.net``, and userinfo we strip for the check would
    otherwise still be sent to the site. Doing this before the steps are
    classified and stored means the consent prompt shows what will run.
    """
    for step in steps:
        if step.get("action") == "goto":
            step["url"] = _guard_act(app, str(step.get("url") or ""))

    interacts_with_current = any(
        step.get("action") != "goto" for step in steps
    )
    first_is_goto = bool(steps) and steps[0].get("action") == "goto"
    if interacts_with_current and not first_is_goto:
        if not session.current_url:
            raise HTTPException(
                409,
                "session has no page loaded; the first step must be a goto",
            )
        _guard_act(app, session.current_url)


async def _execute(
    app: FastAPI,
    session_id: str,
    steps: list[dict],
    *,
    approved: bool,
    request_id: str | None = None,
) -> dict:
    """The ONLY path that hands steps to a browser."""
    s: Settings = app.state.settings
    if request_id is not None and not app.state.gate.is_executable(request_id):
        # Belt and braces: the gate — not the caller, and not the fact that
        # we got here — decides whether stored steps may run.
        audit.error("refused to execute non-approved request %s", request_id)
        raise HTTPException(409, "approval is not in an executable state")
    session = app.state.sessions.get(session_id)
    outcomes, page = await app.state.backend.act(session_id, steps)

    session.current_url = page.final_url or session.current_url
    session.steps_run += len(steps)
    app.state.sessions.touch(session)

    audit.info(
        "act executed session=%s approved=%s steps=%d request_id=%s",
        session_id, approved, len(steps), request_id,
    )

    payload = _page_payload(page.html, page.final_url, s)
    payload.update(
        {
            "status": "ok",
            "executed": True,
            "approved": approved,
            "session_id": session_id,
            "results": [o.as_dict() for o in outcomes],
        }
    )
    if request_id:
        payload["request_id"] = request_id
    return payload


# Module-level app for `uvicorn jarvis_browser.app:app`. Settings are read
# from the environment; the token check happens at startup (lifespan), so an
# import never fails but a misconfigured service never serves.
app = create_app()


def main() -> None:  # pragma: no cover - entrypoint
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":  # pragma: no cover
    main()
