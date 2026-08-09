"""HTTP clients for the two things `web` talks to: SearXNG and jarvis-browser.

Both are on the LAN, both are operator-configured, and neither is allowed to
fail quietly. Every error surface here says what is wrong and what to set —
because the failure this module exists to prevent is not an exception, it is
a "helpful" fallback to a cloud engine that nobody notices for six months.

Nothing in this file interprets content. It fetches, checks the shape, and
hands the bytes on; :mod:`.fence` is what makes them safe to show a model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from .fence import sanitize_untrusted

_LOGGER = logging.getLogger(__name__)

DEFAULT_SEARXNG_URL = "http://127.0.0.1:8888"
DEFAULT_BROWSER_URL = "http://127.0.0.1:8210"
DEFAULT_TIMEOUT = 20.0
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_SAFE_SEARCH = 1
DEFAULT_LANGUAGE = "en"
DEFAULT_LIMIT = 8
MAX_LIMIT = 25

#: jarvis-browser's own BROWSER_APPROVAL_TTL default — how long it will hold a
#: gated step before releasing it.
BROWSER_APPROVAL_TTL_DEFAULT = 300.0

#: How long to hold a browse step waiting for the human to answer.
#:
#: This is NOT a free number. ``companion.ask`` escalates once, waiting the
#: full timeout on each device, so the wall-clock wait is *twice* this — and
#: if that overruns the browser's TTL above, the human says yes to a request
#: that has already been released and gets a bare HTTP 409 for their trouble.
#: 120 leaves four minutes of answering time inside a five-minute window.
DEFAULT_APPROVAL_TIMEOUT = 120.0

#: /approve statuses that mean "the held request is gone", not "you got the
#: secret wrong". Nothing ran in either case; only one of them is worth
#: re-asking about.
STALE_APPROVAL_STATUSES = frozenset({404, 409, 410})

RESULT_SCHEMES = frozenset({"http", "https"})

#: jarvis-browser mints session ids as ``uuid4().hex``. The id the model hands
#: back is interpolated straight into a request path, so it is checked against
#: that shape before it goes anywhere: a value carrying ``/``, ``?``, ``#`` or
#: a percent-escape is a caller trying to steer the request at a different
#: endpoint, and it should be refused here with a sentence rather than
#: bounced off the far end as an unexplained 404.
SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 600

SEARXNG_NOT_CONFIGURED = (
    "Web search is unavailable: no SearXNG instance is configured. Set "
    "SEARXNG_URL (or web: searxng_url: in configuration.yaml) and start one "
    "with `docker compose --profile search up -d`. Jarvis will NOT fall back "
    "to Google, Bing or any other cloud search engine — this stack is "
    "private by design."
)

BROWSER_NOT_CONFIGURED = (
    "The jarvis-browser service is not configured: set web: browser_url: and "
    "browser_token: (JARVIS_BROWSER_TOKEN) in configuration.yaml, and start "
    "it with `docker compose up -d jarvis-browser`."
)


class WebError(Exception):
    """Anything the `web` integration could not do. Carries a usable message."""


class SearchNotConfigured(WebError):
    pass


class SearchFailed(WebError):
    pass


class BrowserNotConfigured(WebError):
    pass


class BrowserFailed(WebError):
    """A call to jarvis-browser did not work. ``status`` is its HTTP code."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        return ()
    return tuple(str(p).strip().lower() for p in parts if str(p).strip())


def _scalar(value: Any) -> str:
    """A YAML scalar as a usable string, or "" if it is only punctuation.

    ``!env_var NAME ""`` hands back the *two-character* string ``""`` when the
    variable is unset: the loader splits the tag's arguments on whitespace and
    keeps the second token verbatim, quotes and all. Left alone that is a
    truthy token, so an installation with no ``JARVIS_BROWSER_TOKEN`` would
    look configured, send ``Authorization: Bearer ""`` and get a 401 it has no
    explanation for. Same for anyone who pastes ``TOKEN="abc"`` into a .env.

    So: strip surrounding quotes, and treat a value that was nothing but
    quotes or whitespace as absent — which is the state it was describing.
    """
    text = str(value if value is not None else "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return "" if text in ('""', "''") else text


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class WebConfig:
    """The `web:` block, parsed. Every field has a working default."""

    searxng_url: str = ""
    browser_url: str = DEFAULT_BROWSER_URL
    browser_token: str = ""
    #: The SECOND secret. Approving a gated browser step needs this and it is
    #: never the same value as ``browser_token`` — holding the API token must
    #: not be enough to click "Pay". Empty means no approval can ever be
    #: granted, which is the safe direction.
    approval_secret: str = ""
    #: Domains where the agent may click/type. Advisory here — jarvis-browser
    #: enforces its own copy (BROWSER_ACT_ALLOWLIST) and is the authority.
    #: Empty means acting is refused everywhere.
    act_allowlist: tuple[str, ...] = ()
    safe_search: int = DEFAULT_SAFE_SEARCH
    language: str = DEFAULT_LANGUAGE
    engines: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    timeout: float = DEFAULT_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    default_limit: int = DEFAULT_LIMIT
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT

    @classmethod
    def from_config(cls, config: Any) -> "WebConfig":
        options: dict[str, Any]
        if isinstance(config, dict):
            options = config
        elif isinstance(config, list) and config and isinstance(config[0], dict):
            options = config[0]
        else:
            options = {}

        searxng = _scalar(options.get("searxng_url")).rstrip("/")
        browser = (_scalar(options.get("browser_url")) or DEFAULT_BROWSER_URL).rstrip("/")
        return cls(
            searxng_url=searxng,
            browser_url=browser,
            browser_token=_scalar(options.get("browser_token")),
            approval_secret=_scalar(
                options.get("browser_approval_secret")
                or options.get("approval_secret")
            ),
            act_allowlist=_as_tuple(options.get("act_allowlist")),
            safe_search=min(2, max(0, as_int(options.get("safe_search"), DEFAULT_SAFE_SEARCH))),
            language=str(options.get("language") or DEFAULT_LANGUAGE),
            engines=_as_tuple(options.get("engines")),
            categories=_as_tuple(options.get("categories")),
            timeout=_as_float(options.get("timeout"), DEFAULT_TIMEOUT),
            connect_timeout=_as_float(
                options.get("connect_timeout"), DEFAULT_CONNECT_TIMEOUT
            ),
            default_limit=max(1, min(MAX_LIMIT, as_int(options.get("limit"), DEFAULT_LIMIT))),
            approval_timeout=_as_float(
                options.get("approval_timeout"), DEFAULT_APPROVAL_TIMEOUT
            ),
        )

    @property
    def search_configured(self) -> bool:
        return bool(self.searxng_url)

    @property
    def browser_configured(self) -> bool:
        return bool(self.browser_url and self.browser_token)

    @property
    def can_approve(self) -> bool:
        return bool(self.approval_secret)

    def httpx_timeout(self) -> httpx.Timeout:
        # Split so a container that is *down* fails in milliseconds while a
        # slow upstream engine still gets the full read budget.
        return httpx.Timeout(self.timeout, connect=min(self.connect_timeout, self.timeout))


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------
def _clean(value: Any, limit: int) -> str:
    return sanitize_untrusted(" ".join(str(value or "").split()))[:limit]


def _result_url(value: Any) -> str:
    url = str(value or "").strip()[:2048]
    if not url:
        return ""
    try:
        scheme = (urlsplit(url).scheme or "").lower()
    except ValueError:
        return ""
    return url if scheme in RESULT_SCHEMES else ""


def _dedupe_key(url: str) -> str:
    """Same page reached twice (two engines, one trailing slash) -> one row.

    Host and scheme are case-insensitive; the path is NOT. Lowercasing the
    whole URL collapses ``/Downloads`` and ``/downloads`` into one row, and on
    the many servers where those are different pages that silently throws a
    result away — the one the user was looking for, half the time.
    """
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - _result_url already parsed it
        return url
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}?{parts.query}"


def parse_results(payload: Any, limit: int) -> list[dict[str, str]]:
    """SearXNG JSON -> ``[{title, url, snippet}]``, deduplicated and capped.

    Title/url/snippet only. The engine names, scores, thumbnails and parsed
    URLs SearXNG also returns are noise in a model's context window, and
    every extra field is another string an attacker gets to choose.
    """
    limit = max(0, min(as_int(limit, 0), MAX_LIMIT))
    if limit == 0 or not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue
        url = _result_url(item.get("url"))
        if not url:
            continue
        key = _dedupe_key(url)
        if key in seen:
            continue
        seen.add(key)
        snippet = item.get("content") or item.get("description") or item.get("snippet")
        out.append(
            {
                "title": _clean(item.get("title"), MAX_TITLE_CHARS) or url,
                "url": url,
                "snippet": _clean(snippet, MAX_SNIPPET_CHARS),
            }
        )
    return out


class SearxngClient:
    """The JSON API of a LAN SearXNG. No fallback, ever."""

    def __init__(self, config: WebConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client

    def params(self, query: str) -> dict[str, str]:
        cfg = self.config
        params = {
            "q": query,
            # Required. SearXNG serves HTML unless `json` is in
            # search.formats, and asking for a format it was not given is a
            # 403 — not a silent downgrade to HTML.
            "format": "json",
            "safesearch": str(cfg.safe_search),
            "language": cfg.language,
        }
        if cfg.categories:
            params["categories"] = ",".join(cfg.categories)
        if cfg.engines:
            params["engines"] = ",".join(cfg.engines)
        return params

    async def search(self, query: str, limit: int) -> list[dict[str, str]]:
        cfg = self.config
        if not cfg.search_configured:
            raise SearchNotConfigured(SEARXNG_NOT_CONFIGURED)
        query = (query or "").strip()
        if not query:
            raise SearchFailed("web.search needs a query")
        limit = max(0, min(as_int(limit, cfg.default_limit) or cfg.default_limit, MAX_LIMIT))
        if limit == 0:
            return []

        url = f"{cfg.searxng_url}/search"
        try:
            response = await self.client.get(
                url, params=self.params(query), timeout=cfg.httpx_timeout()
            )
        except httpx.TimeoutException as exc:
            raise SearchFailed(
                f"SearXNG at {cfg.searxng_url} timed out after {cfg.timeout:g}s "
                f"({type(exc).__name__}). No cloud search engine is used instead."
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchFailed(
                f"SearXNG is unreachable at {cfg.searxng_url} "
                f"({type(exc).__name__}). Start it with "
                "`docker compose --profile search up -d`, or point SEARXNG_URL "
                "at an existing instance. Jarvis will NOT fall back to a cloud "
                "search engine."
            ) from exc

        if response.status_code == 403:
            raise SearchFailed(
                "SearXNG returned HTTP 403 for a JSON search. Enable the JSON "
                "format in searxng/settings.yml (search.formats: [html, json]) "
                "— it is off by default."
            )
        if response.status_code == 429:
            raise SearchFailed(
                "SearXNG returned HTTP 429 (rate limited). Set "
                "server.limiter: false in searxng/settings.yml for LAN use."
            )
        if response.status_code != 200:
            raise SearchFailed(
                f"SearXNG returned HTTP {response.status_code} for {url}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchFailed(
                "SearXNG did not return JSON. Enable the JSON format in "
                "searxng/settings.yml (search.formats: [html, json])."
            ) from exc
        if not isinstance(payload, dict):
            raise SearchFailed(
                f"SearXNG returned {type(payload).__name__} at the top level; "
                "expected a JSON object."
            )
        return parse_results(payload, limit)


# ---------------------------------------------------------------------------
# jarvis-browser
# ---------------------------------------------------------------------------
@dataclass
class ActOutcome:
    """What ``POST /session/{id}/act`` (or ``/approve``) came back with."""

    executed: bool
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_approval(self) -> bool:
        return str(self.payload.get("status")) == "approval_required"

    @property
    def request_id(self) -> str:
        return str(self.payload.get("request_id") or "")

    @property
    def reasons(self) -> list[str]:
        value = self.payload.get("reasons")
        return [str(r) for r in value] if isinstance(value, list) else []

    @property
    def steps(self) -> list[dict[str, Any]]:
        value = self.payload.get("steps")
        return [s for s in value if isinstance(s, dict)] if isinstance(value, list) else []


class BrowserClient:
    """Thin, typed wrapper over the jarvis-browser HTTP API.

    Two headers matter and they are never sent together by accident:
    ``Authorization`` goes on every call, ``X-Approval-Secret`` goes ONLY on
    ``/approve``. :meth:`approve` is the only method that can produce the
    second one, so there is no code path where a routine call carries it.
    """

    def __init__(self, config: WebConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.browser_token}"}

    async def _post(self, path: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        cfg = self.config
        if not cfg.browser_configured:
            raise BrowserNotConfigured(BROWSER_NOT_CONFIGURED)
        url = f"{cfg.browser_url}{path}"
        try:
            response = await self.client.post(
                url,
                json=payload,
                headers={**self._headers(), **(headers or {})},
                timeout=cfg.httpx_timeout(),
            )
        except httpx.TimeoutException as exc:
            raise BrowserFailed(
                f"jarvis-browser timed out after {cfg.timeout:g}s on {path} "
                f"({type(exc).__name__})."
            ) from exc
        except httpx.HTTPError as exc:
            raise BrowserFailed(
                f"jarvis-browser is unreachable at {cfg.browser_url} "
                f"({type(exc).__name__}). Start it with "
                "`docker compose up -d jarvis-browser`."
            ) from exc

        if response.status_code == 401:
            raise BrowserFailed(
                "jarvis-browser rejected the bearer token (401). Check that "
                "web: browser_token: matches JARVIS_BROWSER_TOKEN.",
                401,
            )
        if response.status_code >= 400:
            raise BrowserFailed(
                f"jarvis-browser returned HTTP {response.status_code} on "
                f"{path}: {_detail(response)}",
                response.status_code,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise BrowserFailed(
                f"jarvis-browser returned a non-JSON body on {path}."
            ) from exc
        if not isinstance(body, dict):
            raise BrowserFailed(
                f"jarvis-browser returned {type(body).__name__} on {path}; "
                "expected a JSON object."
            )
        return body

    async def fetch(self, url: str, *, render: bool = True) -> dict[str, Any]:
        return await self._post("/fetch", {"url": url, "render": bool(render)})

    async def crawl(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/crawl", payload)

    async def create_session(self) -> str:
        body = await self._post("/session", {})
        session_id = str(body.get("session_id") or "")
        if not SESSION_ID_RE.match(session_id):
            raise BrowserFailed(
                f"jarvis-browser returned {session_id!r} as a session id; "
                "that is not one, and it is about to be pasted into a request "
                "path."
            )
        return session_id

    async def act(self, session_id: str, steps: list[dict[str, Any]]) -> ActOutcome:
        if not SESSION_ID_RE.match(session_id or ""):
            raise BrowserFailed(
                f"{session_id!r} is not a jarvis-browser session id. Session "
                "ids are opaque hex handed back by web.browse; omit the field "
                "to open a new session."
            )
        body = await self._post(f"/session/{session_id}/act", {"steps": steps})
        return ActOutcome(executed=bool(body.get("executed")), payload=body)

    async def approve(self, request_id: str, approved: bool) -> ActOutcome:
        """The ONLY method that sends the approval secret.

        Called with ``approved=True`` exactly once per human "yes", and never
        from a code path that decided on its own.
        """
        if not self.config.can_approve:
            raise BrowserFailed(
                "No browser approval secret is configured, so this step "
                "cannot be approved. Set web: browser_approval_secret: "
                "(BROWSER_APPROVAL_SECRET) — it must differ from the API "
                "token. Nothing was executed."
            )
        try:
            body = await self._post(
                "/approve",
                {"request_id": request_id, "approved": bool(approved)},
                headers={"X-Approval-Secret": self.config.approval_secret},
            )
        except BrowserFailed as exc:
            if exc.status in STALE_APPROVAL_STATUSES:
                # The held request aged out or was already consumed while the
                # question sat on somebody's lock screen. Nothing ran, and the
                # bare status code says none of that.
                raise BrowserFailed(
                    "That approval is no longer valid — jarvis-browser had "
                    f"already released it (HTTP {exc.status}). Nothing ran. "
                    "Ask again if you still want it; approvals expire after "
                    f"{BROWSER_APPROVAL_TTL_DEFAULT:g}s by default "
                    "(BROWSER_APPROVAL_TTL) and are single-use.",
                    exc.status,
                ) from exc
            raise
        return ActOutcome(executed=bool(body.get("executed")), payload=body)


def _detail(response: httpx.Response) -> str:
    """The far end's error message, if it left one, in one short line."""
    try:
        body = response.json()
    except ValueError:
        return _clean(response.text, 300) or "(no body)"
    if isinstance(body, dict):
        return _clean(body.get("detail") or body, 300)
    return _clean(body, 300)
