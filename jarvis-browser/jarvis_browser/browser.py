"""Browser backends.

All browser access in this service goes through the :class:`BrowserBackend`
protocol. Two implementations ship:

* :class:`FakeBackend` — in-memory, records every interaction. The tests use
  it, and it is what lets the security tests assert "the browser was never
  touched".
* :class:`PlaywrightBackend` — the real thing. ``playwright`` is imported
  *lazily inside* ``start()``, so the service, the API and the whole test
  suite work in a container that has no playwright installed.

The Playwright launch is locked down: no GPU, no extensions, no dev-shm, no
downloads, no popups, TLS errors fatal, and a route handler that resolves
every request's host and aborts anything pointing at a private/loopback/
link-local/metadata address (SSRF defence, see safety.is_blocked_host).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .config import Settings
from .safety import DomainPolicy, act_target_violation, check_url

log = logging.getLogger("jarvis.browser.backend")


@dataclass
class FetchResult:
    html: str = ""
    final_url: str = ""
    status: int = 0
    nbytes: int = 0


@dataclass
class StepOutcome:
    index: int
    action: str
    status: str  # ok | error
    detail: str = ""
    value: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "index": self.index,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
        }
        if self.value is not None:
            d["value"] = self.value
        return d


class BrowserError(Exception):
    """Backend failure that should surface as a 502, not a crash."""


@runtime_checkable
class BrowserBackend(Protocol):
    """Everything the service is allowed to ask a browser to do."""

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def fetch(
        self, url: str, *, render: bool = True, javascript: bool = True
    ) -> FetchResult: ...

    async def screenshot(
        self, url: str, *, full_page: bool = False, javascript: bool = True
    ) -> bytes: ...

    async def open_session(
        self, session_id: str, *, javascript: bool, profile_dir: str
    ) -> None: ...

    async def close_session(self, session_id: str) -> None: ...

    async def act(
        self, session_id: str, steps: list[dict]
    ) -> tuple[list[StepOutcome], FetchResult]: ...


# --------------------------------------------------------------------------
# Fake backend (tests, and a working service without playwright)
# --------------------------------------------------------------------------

@dataclass
class FakeBackend:
    """Deterministic in-memory backend.

    ``interactions`` records every step the service asked it to perform. The
    security tests assert this list stays EMPTY when a batch is gated, which
    is the concrete meaning of "nothing executed".
    """

    pages: dict[str, str] = field(default_factory=dict)
    png: bytes = b"\x89PNG\r\n\x1a\n" + b"fake" * 4
    interactions: list[dict] = field(default_factory=list)
    sessions: dict[str, dict] = field(default_factory=dict)
    started: bool = False
    closed: bool = False
    fail_urls: set[str] = field(default_factory=set)

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True
        self.sessions.clear()

    def _page(self, url: str) -> FetchResult:
        if url in self.fail_urls:
            raise BrowserError(f"navigation failed for {url}")
        html = self.pages.get(url)
        if html is None:
            html = self.pages.get(url.rstrip("/"))
        if html is None:
            return FetchResult(html="", final_url=url, status=404, nbytes=0)
        return FetchResult(
            html=html,
            final_url=url,
            status=200,
            nbytes=len(html.encode("utf-8", "replace")),
        )

    async def fetch(
        self, url: str, *, render: bool = True, javascript: bool = True
    ) -> FetchResult:
        self.interactions.append(
            {"op": "fetch", "url": url, "render": render}
        )
        return self._page(url)

    async def screenshot(
        self, url: str, *, full_page: bool = False, javascript: bool = True
    ) -> bytes:
        self.interactions.append(
            {"op": "screenshot", "url": url, "full_page": full_page}
        )
        return self.png

    async def open_session(
        self, session_id: str, *, javascript: bool, profile_dir: str
    ) -> None:
        self.sessions[session_id] = {
            "javascript": javascript,
            "profile_dir": profile_dir,
            "url": "",
        }

    async def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    async def act(
        self, session_id: str, steps: list[dict]
    ) -> tuple[list[StepOutcome], FetchResult]:
        if session_id not in self.sessions:
            raise BrowserError("unknown session")
        outcomes: list[StepOutcome] = []
        state = self.sessions[session_id]
        for i, step in enumerate(steps):
            action = str(step.get("action", "")).lower()
            self.interactions.append(
                {"op": "step", "session": session_id, **step}
            )
            if action == "goto":
                state["url"] = str(step.get("url", ""))
                outcomes.append(
                    StepOutcome(i, action, "ok", f"navigated to {state['url']}")
                )
            elif action == "extract":
                page = self._page(state["url"])
                outcomes.append(
                    StepOutcome(i, action, "ok", "extracted", page.html)
                )
            else:
                outcomes.append(StepOutcome(i, action, "ok", "performed"))
        return outcomes, self._page(state["url"])


# --------------------------------------------------------------------------
# Real backend
# --------------------------------------------------------------------------

class PlaywrightBackend:
    """Chromium via Playwright, locked down.

    Import of ``playwright`` happens inside :meth:`start`, never at module
    import time, so this file is safe to import anywhere.
    """

    def __init__(self, settings: Settings):
        self.s = settings
        self._act_policy = DomainPolicy(
            allowlist=settings.allowlist,
            denylist=settings.denylist,
            act_allowlist=settings.act_allowlist,
        )
        self._pw = None
        self._browser = None
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
    def _launch_args(self) -> list[str]:
        args = [
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-plugins",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-client-side-phishing-detection",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-service-autorun",
            "--mute-audio",
            "--hide-scrollbars",
            "--metrics-recording-only",
            "--disable-features=Translate,BackForwardCache,"
            "AcceptCHFrame,MediaRouter,OptimizationHints",
        ]
        if self.s.chromium_no_sandbox:
            # Only when the operator opts in. Preferred posture is to run the
            # container as a non-root user and keep chromium's own sandbox.
            args += ["--no-sandbox", "--disable-setuid-sandbox"]
        return args

    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright  # lazy
        except ImportError as exc:  # pragma: no cover - env dependent
            raise BrowserError(
                "playwright is not installed in this container; install it "
                "(pip install playwright && playwright install chromium) or "
                "run the service with the FakeBackend"
            ) from exc

        self._pw = await async_playwright().start()
        kwargs: dict[str, Any] = {
            "headless": self.s.headless,
            "args": self._launch_args(),
            "chromium_sandbox": not self.s.chromium_no_sandbox,
        }
        # The Dockerfile symlinks /opt/pw-browsers/chromium at the installed
        # revision. If that symlink is missing (a best-effort image build
        # where `playwright install` could not reach the network), fall back
        # to Playwright's own resolution rather than failing on a bad path.
        if self.s.executable_path and os.path.exists(self.s.executable_path):
            kwargs["executable_path"] = self.s.executable_path
        elif self.s.executable_path:
            log.warning(
                "BROWSER_EXECUTABLE_PATH %s does not exist; falling back to "
                "the playwright-managed chromium",
                self.s.executable_path,
            )
        try:
            self._browser = await self._pw.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - every launch failure, named
            # Playwright raises its own error type, which nothing here handles,
            # so /fetch answered 500 with a stack trace for what is a broken
            # image. The commonest cause by far is the one this container hit:
            # the chromium binary downloaded and its shared libraries did not.
            detail = str(exc).strip().splitlines()
            raise BrowserError(
                "chromium would not start: "
                + (detail[-1] if detail else type(exc).__name__)
                + ". If that mentions a missing shared library, the image was "
                "built without chromium's system libraries — rebuild it "
                "(jarvis-browser/Dockerfile installs them by name)."
            ) from exc

    async def close(self) -> None:
        for sid in list(self._contexts):
            await self.close_session(sid)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    # -- context construction ---------------------------------------------
    async def _new_context(self, *, javascript: bool):
        """A fresh, isolated context.

        Cookies and storage live only in this in-memory context and die with
        it — nothing is written to a shared browser profile, and there is no
        persistent-context on-disk profile to leak. The session's temp dir
        (wiped on close) exists for any incidental on-disk artifact.
        """
        await self.start()
        ctx = await self._browser.new_context(
            user_agent=self.s.user_agent,
            java_script_enabled=javascript,
            ignore_https_errors=False,   # TLS failures are fatal
            accept_downloads=False,      # downloads disabled
            bypass_csp=False,
            service_workers="block",
            viewport={
                "width": self.s.viewport_width,
                "height": self.s.viewport_height,
            },
            device_scale_factor=1,
            locale="en-GB",
        )
        ctx.set_default_timeout(self.s.nav_timeout_ms)
        ctx.set_default_navigation_timeout(self.s.nav_timeout_ms)
        await self._install_guards(ctx)
        return ctx

    async def _install_guards(self, ctx) -> None:
        """SSRF route filter + popup/dialog handling for every page.

        The route handler is the second line of the SSRF defence: the initial
        URL is checked before we navigate, but redirects, iframes, XHR and
        subresources all pass through here too, so a public page cannot pull
        in ``http://127.0.0.1:8123/`` behind our back.
        """
        allowlist = self.s.lan_allowlist

        async def _route(route, request):
            # Resolution is blocking; keep it off the event loop.
            reason = await asyncio.to_thread(
                check_url, request.url, allowlist=allowlist
            )
            if reason:
                log.warning("blocked subresource %s: %s", request.url, reason)
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        await ctx.route("**/*", _route)
        ctx.on("page", self._harden_page)

    def _harden_page(self, page) -> None:
        """Popups are a navigation channel we do not want; dialogs block."""
        page.on("popup", lambda p: asyncio.ensure_future(p.close()))
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

    async def _settle(self, page) -> None:
        """Let a page that writes itself finish writing itself.

        `load` means the document's own resources arrived. A dashboard's
        numbers arrive after that — from a fetch, or from a timer — and taking
        `content()` at `load` captured the "Loading…" placeholder instead of
        the page. Two waits, because pages do it two ways: network idle for the
        one that asks a server, and a short settle for the one that does not.
        Both are bounded and both are allowed to time out; a slow page is still
        worth reading, and this must never turn a fetch into a failure.
        """
        if self.s.settle_ms <= 0:
            return
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=self.s.settle_ms * 5)
        with contextlib.suppress(Exception):
            await page.wait_for_timeout(self.s.settle_ms)

    # -- one-shot operations ----------------------------------------------
    async def fetch(
        self, url: str, *, render: bool = True, javascript: bool = True
    ) -> FetchResult:
        ctx = await self._new_context(javascript=javascript and render)
        try:
            page = await ctx.new_page()
            wait_until = "load" if render else "domcontentloaded"
            resp = await page.goto(url, wait_until=wait_until)
            if render:
                await self._settle(page)
            html = await page.content()
            data = html.encode("utf-8", "replace")
            if len(data) > self.s.max_page_bytes:
                html = data[: self.s.max_page_bytes].decode("utf-8", "ignore")
            return FetchResult(
                html=html,
                final_url=page.url,
                status=resp.status if resp else 0,
                nbytes=len(data),
            )
        except Exception as exc:
            raise BrowserError(f"fetch failed: {type(exc).__name__}") from exc
        finally:
            await ctx.close()

    async def screenshot(
        self, url: str, *, full_page: bool = False, javascript: bool = True
    ) -> bytes:
        ctx = await self._new_context(javascript=javascript)
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="load")
            png = await page.screenshot(type="png", full_page=full_page)
            if full_page and len(png) > self.s.max_screenshot_bytes:
                # Refuse to stream an unbounded full-page capture back: fall
                # back to the viewport, which is bounded by construction.
                png = await page.screenshot(type="png", full_page=False)
            return _downscale_png(png, self.s.max_screenshot_bytes)
        except Exception as exc:
            raise BrowserError(
                f"screenshot failed: {type(exc).__name__}"
            ) from exc
        finally:
            await ctx.close()

    # -- sessions ----------------------------------------------------------
    async def open_session(
        self, session_id: str, *, javascript: bool, profile_dir: str
    ) -> None:
        # profile_dir is part of the protocol and owned by the SessionManager,
        # which wipes it on close. This backend keeps cookies in the in-memory
        # context rather than on disk, so there is nothing to point at it.
        async with self._lock:
            if session_id in self._contexts:
                return
            ctx = await self._new_context(javascript=javascript)
            page = await ctx.new_page()
            self._contexts[session_id] = ctx
            self._pages[session_id] = page

    async def close_session(self, session_id: str) -> None:
        ctx = self._contexts.pop(session_id, None)
        self._pages.pop(session_id, None)
        if ctx is not None:
            try:
                await ctx.clear_cookies()
            except Exception:
                pass
            try:
                await ctx.close()
            except Exception:
                pass

    async def act(
        self, session_id: str, steps: list[dict]
    ) -> tuple[list[StepOutcome], FetchResult]:
        page = self._pages.get(session_id)
        if page is None:
            raise BrowserError("unknown session")
        outcomes: list[StepOutcome] = []
        for i, step in enumerate(steps):
            action = str(step.get("action", "")).lower()
            selector = step.get("selector") or ""
            value = str(step.get("value") or "")
            try:
                outcomes.append(
                    await self._one_step(page, i, action, selector, value, step)
                )
            except Exception as exc:
                outcomes.append(
                    StepOutcome(
                        i, action, "error", f"{type(exc).__name__}: {exc}"[:300]
                    )
                )
                break  # a failed step invalidates everything after it
            # Where we asked to go was checked before the batch ran; where we
            # ended up is the site's choice. A redirect, meta-refresh or
            # script navigation must not carry the remaining steps onto a
            # domain the act allowlist never covered.
            drift = act_target_violation(
                getattr(page, "url", ""), self._act_policy
            )
            if drift:
                log.warning("act batch left the act allowlist: %s", drift)
                outcomes.append(
                    StepOutcome(
                        i, action, "error",
                        f"navigation left the act allowlist: {drift}"[:300],
                    )
                )
                break
        html = ""
        try:
            html = await page.content()
        except Exception:
            pass
        return outcomes, FetchResult(
            html=html,
            final_url=getattr(page, "url", ""),
            status=200,
            nbytes=len(html.encode("utf-8", "replace")),
        )

    async def _one_step(
        self, page, i: int, action: str, selector: str, value: str, step: dict
    ) -> StepOutcome:
        timeout = self.s.nav_timeout_ms
        if action == "goto":
            await page.goto(str(step.get("url", "")), wait_until="load")
            return StepOutcome(i, action, "ok", f"at {page.url}")
        if action == "click":
            await page.click(selector, timeout=timeout)
            return StepOutcome(i, action, "ok", f"clicked {selector}")
        if action == "type":
            await page.fill(selector, value, timeout=timeout)
            return StepOutcome(i, action, "ok", f"typed into {selector}")
        if action == "select":
            await page.select_option(selector, value, timeout=timeout)
            return StepOutcome(i, action, "ok", f"selected {value!r}")
        if action == "press":
            if selector:
                await page.press(selector, value, timeout=timeout)
            else:
                await page.keyboard.press(value)
            return StepOutcome(i, action, "ok", f"pressed {value!r}")
        if action == "scroll":
            amount = int(step.get("amount", 1000) or 1000)
            await page.mouse.wheel(0, amount)
            return StepOutcome(i, action, "ok", f"scrolled {amount}")
        if action == "wait_for":
            if selector:
                await page.wait_for_selector(selector, timeout=timeout)
                return StepOutcome(i, action, "ok", f"saw {selector}")
            await page.wait_for_load_state("networkidle")
            return StepOutcome(i, action, "ok", "network idle")
        if action == "extract":
            if selector:
                el = await page.query_selector(selector)
                text = (await el.inner_text()) if el else ""
            else:
                text = await page.content()
            return StepOutcome(
                i, action, "ok", "extracted", text[: self.s.max_text_chars]
            )
        return StepOutcome(i, action, "error", f"unsupported action {action!r}")


def _downscale_png(png: bytes, max_bytes: int) -> bytes:
    """Shrink a PNG until it fits, if Pillow happens to be available.

    Pillow is optional; without it we return the capture unchanged and rely
    on the viewport cap, which already bounds the size.
    """
    if len(png) <= max_bytes:
        return png
    try:  # pragma: no cover - optional dependency
        import io

        from PIL import Image
    except ImportError:
        return png
    try:  # pragma: no cover
        img = Image.open(io.BytesIO(png))
        for scale in (0.75, 0.5, 0.35, 0.25):
            buf = io.BytesIO()
            resized = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
            )
            resized.save(buf, format="PNG", optimize=True)
            if buf.tell() <= max_bytes:
                return buf.getvalue()
        return buf.getvalue()
    except Exception:
        return png


def wipe_dir(path: str) -> None:
    """Remove a session profile directory and everything in it."""
    if path:
        shutil.rmtree(path, ignore_errors=True)
