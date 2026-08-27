"""Configuration — plain dataclass, built from an env mapping.

No Playwright, no FastAPI, no I/O beyond reading a mapping: the whole thing is
constructible in a test with keyword arguments.

Design note on the sensitive lists: ``BROWSER_SENSITIVE_KEYWORDS`` and
``BROWSER_SENSITIVE_SELECTORS`` only ever *extend* the built-in defaults. An
operator cannot shrink the gated set by setting an env var, because a
half-typed env var must not be able to silently un-gate ``checkout`` or
``password``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace

# Steps touching any of these are Tier-3-equivalent: they log in, pay, send,
# or destroy. Substring match, case-insensitive, over selector/text/value/url.
DEFAULT_SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "password", "passwd", "passphrase", "secret", "otp", "2fa", "mfa",
    "totp", "verification code",
    "login", "log in", "log-in", "signin", "sign in", "sign-in", "logon",
    "authenticate", "credential",
    "checkout", "pay", "payment", "purchase", "buy", "order now",
    "place order", "subscribe", "billing", "card number", "cvv", "iban",
    "transfer", "send money", "wire", "withdraw", "deposit",
    "delete", "remove", "destroy", "wipe", "erase", "deactivate",
    "cancel account", "close account", "unsubscribe",
    "submit", "confirm", "apply now", "send message", "post comment",
    "upload", "attach file",
)

# Selector fragments that mean "this is a form control that submits or takes
# a credential", independent of the words on the page.
DEFAULT_SENSITIVE_SELECTORS: tuple[str, ...] = (
    "input[type=password]", 'input[type="password"]',
    "input[type=submit]", 'input[type="submit"]',
    "button[type=submit]", 'button[type="submit"]',
    "input[type=file]", 'input[type="file"]',
    "form",
)

# Keys that submit a focused form.
DEFAULT_SUBMIT_KEYS: tuple[str, ...] = ("enter", "numpadenter", "return")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 JarvisBrowser/0.1"
)


def _split(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(
        part.strip().lower() for part in value.split(",") if part.strip()
    )


class _Env:
    """Tiny typed reader over an env mapping."""

    def __init__(self, env: Mapping[str, str]):
        self._env = env

    def str(self, name: str, default: str = "") -> str:
        return self._env.get(name, "") or default

    def list(self, name: str) -> tuple[str, ...]:
        return _split(self._env.get(name))

    def int(self, name: str, default: int) -> int:
        try:
            return int(self._env.get(name, "") or default)
        except ValueError:
            return default

    def float(self, name: str, default: float) -> float:
        try:
            return float(self._env.get(name, "") or default)
        except ValueError:
            return default

    def bool(self, name: str, default: bool) -> bool:
        raw = self._env.get(name)
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- auth -------------------------------------------------------------
    api_token: str = ""
    approval_secret: str = ""

    # --- private search ---------------------------------------------------
    # Empty means "not configured". /search then fails loudly. It NEVER falls
    # back to a cloud engine: this stack is private by design.
    searxng_url: str = ""

    # AgentSearch, which WRAPS SearXNG rather than replacing it: it still needs
    # a reachable SearXNG behind it, and adds deduplication, scoring, content
    # extraction and prompt-injection scrubbing in front. Set this and it
    # becomes the search path; leave it empty and the direct SearXNG one is
    # used exactly as before. Default port 3939.
    agent_search_url: str = ""
    agent_search_token: str = ""
    # One of AgentSearch's named modes (general, code, academic, news, private,
    # reference, community). Empty uses its plain /search.
    agent_search_strategy: str = ""

    # --- domain policy ----------------------------------------------------
    # allowlist empty => any non-blocked public host may be READ.
    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()
    # act_allowlist is never implicitly open. Empty => /act is refused for
    # every domain. Reading a page is one thing; clicking on it is another.
    act_allowlist: tuple[str, ...] = ()
    # Operator-declared LAN/loopback hosts that are exempt from the SSRF
    # block. Exact hostname match only.
    lan_allowlist: tuple[str, ...] = ()

    # --- gating -----------------------------------------------------------
    sensitive_keywords: tuple[str, ...] = DEFAULT_SENSITIVE_KEYWORDS
    sensitive_selectors: tuple[str, ...] = DEFAULT_SENSITIVE_SELECTORS
    submit_keys: tuple[str, ...] = DEFAULT_SUBMIT_KEYS
    approval_ttl: float = 300.0

    # --- extraction caps --------------------------------------------------
    max_text_chars: int = 40_000
    max_links: int = 200
    max_page_bytes: int = 4_000_000

    # --- crawl caps (hard ceilings; a request may ask for less, never more)
    max_pages_ceiling: int = 100
    max_depth_ceiling: int = 5
    crawl_total_bytes: int = 20_000_000
    crawl_budget_seconds: float = 120.0
    per_domain_interval: float = 1.0
    respect_robots: bool = True

    # --- browser ----------------------------------------------------------
    user_agent: str = DEFAULT_USER_AGENT
    javascript_enabled: bool = True
    #: Text first (M75). A page is fetched over plain HTTP — the same SSRF
    #: checks on every hop — and extracted; only when that text is shorter
    #: than `plain_min_chars` (a JavaScript-only page) is a browser started
    #: for it. On 26 Aug 2026 every read in two research runs hit the
    #: browser's twenty-second navigation timeout on news sites that answer
    #: plain HTTP in under a second.
    plain_fetch: bool = True
    plain_timeout_s: float = 8.0
    plain_min_chars: int = 500
    nav_timeout_ms: int = 20_000
    #: How long a rendered fetch waits AFTER `load` for the page to finish
    #: writing itself. `load` fires when the document's own resources are in,
    #: which on a page whose figures arrive from a script is before there is
    #: anything to read: this service returned "Loading the appliance
    #: register…" for a page whose register was 120 ms away. Network idle
    #: covers a page that fetches its data; the settle covers one that does not.
    #: Set to 0 to turn both off and pay nothing.
    settle_ms: int = 400
    viewport_width: int = 1280
    viewport_height: int = 800
    max_screenshot_bytes: int = 3_000_000
    max_full_page_height: int = 6_000
    headless: bool = True
    chromium_no_sandbox: bool = False
    executable_path: str = ""

    # --- sessions ---------------------------------------------------------
    session_ttl: float = 600.0
    max_sessions: int = 8
    max_act_steps: int = 25
    session_root: str = ""
    # How often the background janitor closes expired sessions and drops
    # finished approval requests.
    janitor_interval: float = 30.0

    def with_overrides(self, **kw) -> "Settings":
        return replace(self, **kw)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env`` (defaults to the process environment)."""
    e = _Env(os.environ if env is None else env)

    exe = e.str("BROWSER_EXECUTABLE_PATH")
    if not exe and e.str("PLAYWRIGHT_BROWSERS_PATH"):
        # The container ships a pre-installed chromium at a fixed path.
        exe = "/opt/pw-browsers/chromium"

    return Settings(
        api_token=e.str("JARVIS_BROWSER_TOKEN"),
        approval_secret=e.str("BROWSER_APPROVAL_SECRET"),
        searxng_url=e.str("SEARXNG_URL").rstrip("/"),
        agent_search_url=e.str("AGENT_SEARCH_URL").rstrip("/"),
        agent_search_token=e.str("AGENT_SEARCH_TOKEN"),
        agent_search_strategy=e.str("AGENT_SEARCH_STRATEGY"),
        allowlist=e.list("BROWSER_ALLOWLIST"),
        denylist=e.list("BROWSER_DENYLIST"),
        act_allowlist=e.list("BROWSER_ACT_ALLOWLIST"),
        lan_allowlist=e.list("BROWSER_LAN_ALLOWLIST"),
        sensitive_keywords=DEFAULT_SENSITIVE_KEYWORDS
        + e.list("BROWSER_SENSITIVE_KEYWORDS"),
        sensitive_selectors=DEFAULT_SENSITIVE_SELECTORS
        + e.list("BROWSER_SENSITIVE_SELECTORS"),
        approval_ttl=e.float("BROWSER_APPROVAL_TTL", 300.0),
        max_text_chars=e.int("BROWSER_MAX_TEXT_CHARS", 40_000),
        max_links=e.int("BROWSER_MAX_LINKS", 200),
        max_page_bytes=e.int("BROWSER_MAX_PAGE_BYTES", 4_000_000),
        max_pages_ceiling=e.int("BROWSER_MAX_PAGES", 100),
        max_depth_ceiling=e.int("BROWSER_MAX_DEPTH", 5),
        crawl_total_bytes=e.int("BROWSER_CRAWL_TOTAL_BYTES", 20_000_000),
        crawl_budget_seconds=e.float("BROWSER_CRAWL_BUDGET", 120.0),
        per_domain_interval=e.float("BROWSER_PER_DOMAIN_INTERVAL", 1.0),
        respect_robots=e.bool("BROWSER_RESPECT_ROBOTS", True),
        user_agent=e.str("BROWSER_USER_AGENT", DEFAULT_USER_AGENT),
        javascript_enabled=e.bool("BROWSER_JAVASCRIPT", True),
        plain_fetch=e.bool("BROWSER_PLAIN_FETCH", True),
        plain_timeout_s=e.float("BROWSER_PLAIN_TIMEOUT_S", 8.0),
        plain_min_chars=e.int("BROWSER_PLAIN_MIN_CHARS", 500),
        nav_timeout_ms=e.int("BROWSER_NAV_TIMEOUT_MS", 20_000),
        settle_ms=e.int("BROWSER_SETTLE_MS", 400),
        viewport_width=e.int("BROWSER_VIEWPORT_WIDTH", 1280),
        viewport_height=e.int("BROWSER_VIEWPORT_HEIGHT", 800),
        max_screenshot_bytes=e.int("BROWSER_MAX_SCREENSHOT_BYTES", 3_000_000),
        max_full_page_height=e.int("BROWSER_MAX_FULL_PAGE_HEIGHT", 6_000),
        headless=e.bool("BROWSER_HEADLESS", True),
        chromium_no_sandbox=e.bool("BROWSER_CHROMIUM_NO_SANDBOX", False),
        executable_path=exe,
        session_ttl=e.float("BROWSER_SESSION_TTL", 600.0),
        max_sessions=e.int("BROWSER_MAX_SESSIONS", 8),
        max_act_steps=e.int("BROWSER_MAX_ACT_STEPS", 25),
        session_root=e.str("BROWSER_SESSION_ROOT"),
        janitor_interval=e.float("BROWSER_JANITOR_INTERVAL", 30.0),
    )
