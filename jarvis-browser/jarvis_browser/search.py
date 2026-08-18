"""SearXNG proxy. The only search path this service has.

There is deliberately no fallback. If SEARXNG_URL is unset, /search fails
with a clear error rather than quietly reaching for Google — a private stack
that silently phones a cloud engine is worse than one that says "not
configured", because the operator never finds out.

Three things this module is careful about, all of them learned the hard way:

* **Timeouts are split.** A SearXNG that is *down* fails on connect in
  milliseconds; a SearXNG that is *thinking* (a slow upstream engine) needs
  seconds of read budget. One flat number has to be the larger of the two,
  which turns "the container is not running" into a fifteen-second hang on
  every voice query.
* **The limit is applied after filtering, not before.** Slicing the raw
  ``results`` list first and *then* dropping entries with no URL returns
  fewer results than asked for, and quietly gets worse as an engine starts
  emitting junk rows.
* **Every field is untrusted text.** Titles and snippets are attacker-chosen
  by construction — anyone can rank for a query. They are length-capped and
  run through :func:`sanitize_untrusted` here so they cannot close the fence
  the caller wraps them in.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .safety import sanitize_untrusted

log = logging.getLogger("jarvis.browser.search")

#: Schemes a result may point at. A ``javascript:`` or ``data:`` "result" is
#: not a web page, and handing one to the model as a fetchable URL is how a
#: search result becomes a payload.
RESULT_SCHEMES = frozenset({"http", "https"})

DEFAULT_TIMEOUT = 15.0
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_LANGUAGE = "en"
DEFAULT_SAFESEARCH = 1
#: Hard ceiling on how many results one call may return, whatever it asks for.
MAX_LIMIT = 50

MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 1000
MAX_URL_CHARS = 2048
MAX_ENGINE_CHARS = 120

_NOT_CONFIGURED = (
    "SEARXNG_URL is not configured. jarvis-browser will not fall back to a "
    "cloud search engine; point SEARXNG_URL at your local SearXNG instance "
    "(docker compose --profile search up -d)."
)

_JSON_HINT = (
    "enable the JSON format in searxng/settings.yml "
    "(search.formats: [html, json]) — it is off by default"
)


class SearchNotConfigured(Exception):
    """No SearXNG URL. Never a reason to try somebody else's engine."""


class SearchFailed(Exception):
    """SearXNG was configured but could not answer."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
        }


def _clean(value: object, limit: int) -> str:
    """Untrusted text -> a single bounded line, fence markers neutralised."""
    text = " ".join(str(value or "").split())
    return sanitize_untrusted(text)[:limit]


def _csv(values: Iterable[str] | str | None) -> str:
    """SearXNG takes ``engines``/``categories`` as a comma-separated string."""
    if values is None:
        return ""
    if isinstance(values, str):
        parts: Sequence[object] = values.split(",")
    else:
        parts = list(values)
    # `str(part)` on both sides. Filtering on the string form and then calling
    # .strip() on the original blows up with AttributeError the first time a
    # caller passes anything but strings — a tuple of ints out of a config
    # loader, say — and does it at construction time, which is startup.
    # `None` is dropped rather than stringified: a bare `- ` in YAML parses to
    # one, and "None" is not an engine.
    return ",".join(
        s for s in (str(part).strip() for part in parts if part is not None) if s
    )


def _result_url(value: object) -> str:
    """The result's URL, or "" if it is not a fetchable http(s) address."""
    url = str(value or "").strip()[:MAX_URL_CHARS]
    if not url:
        return ""
    try:
        scheme = (urlsplit(url).scheme or "").lower()
    except ValueError:
        return ""
    return url if scheme in RESULT_SCHEMES else ""


def _dedupe_key(url: str) -> str:
    """Same page reached twice (two engines, one trailing slash) -> one row."""
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - _result_url already parsed it
        return url.lower()
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}?{parts.query}"


def parse_results(payload: object, limit: int) -> list[SearchResult]:
    """Normalise a search JSON body. Every field in it is untrusted text.

    Used for SearXNG *and* AgentSearch. Not a coincidence and not luck:
    AgentSearch wraps SearXNG and answers `{"results": [...]}` with rows
    carrying `title`, `url` and `snippet`, and this reader already accepted
    `snippet` because a couple of SearXNG's own engines emit it. Two readers
    would be two places for a hostile field to be forgotten.

    Tolerant on the way in (engines disagree about which keys they fill, and
    a broken one emits rows with no URL at all), strict on the way out: what
    comes back is always a list of results with an http(s) URL, a title and a
    snippet, each capped, deduplicated, and no longer than ``limit``.
    """
    limit = max(0, min(int(limit or 0), MAX_LIMIT))
    if limit == 0 or not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []

    out: list[SearchResult] = []
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

        engines = item.get("engines")
        if isinstance(engines, (list, tuple, set)):
            engine = ", ".join(sorted(str(e) for e in engines))
        else:
            engine = str(engines or item.get("engine") or "")

        # Engines disagree: `content` is the common one, `description` comes
        # from the API-shaped engines, `snippet` from a couple of others.
        snippet = (
            item.get("content")
            or item.get("description")
            or item.get("snippet")
            or ""
        )
        out.append(
            SearchResult(
                # A result with no title is still a usable link; showing the
                # URL beats showing an empty string.
                title=_clean(item.get("title"), MAX_TITLE_CHARS) or url,
                url=url,
                snippet=_clean(snippet, MAX_SNIPPET_CHARS),
                engine=_clean(engine, MAX_ENGINE_CHARS),
            )
        )
    return out


#: The previous name. Kept so an out-of-tree importer does not break on an
#: upgrade whose whole content is "this also parses AgentSearch".
parse_searxng = parse_results


class SearxngSearcher:
    """Calls a LAN SearXNG instance's JSON API.

    The SearXNG URL is operator-configured, so it is exempt from the SSRF
    host block by design — it is normally a private address, and pointing
    Jarvis at it is a deliberate local decision.

    ``client`` exists for tests: pass an ``httpx.AsyncClient`` built on a
    ``MockTransport`` and no socket is ever opened. In production it is
    ``None`` and a client is created per call, which costs a connection setup
    to loopback and saves keeping a pool alive for a service that is idle
    most of the day.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        language: str = DEFAULT_LANGUAGE,
        safesearch: int = DEFAULT_SAFESEARCH,
        categories: Iterable[str] | str | None = None,
        engines: Iterable[str] | str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = float(timeout)
        # A dead container must fail fast; a slow upstream engine must not.
        self.connect_timeout = min(float(connect_timeout), self.timeout)
        self.language = language or DEFAULT_LANGUAGE
        # SearXNG takes 0 (off) / 1 (moderate) / 2 (strict). Anything else is
        # a 4xx from the far end, so clamp rather than forward it.
        self.safesearch = min(2, max(0, int(safesearch)))
        self.categories = _csv(categories)
        self.engines = _csv(engines)
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def params(self, query: str) -> dict[str, str]:
        """The query string sent to SearXNG. Public so tests can assert it."""
        params = {
            "q": query,
            # Not optional. SearXNG serves HTML unless `json` is in
            # search.formats, and asking for a format it was not given is a
            # 403 rather than a fallback to HTML.
            "format": "json",
            "safesearch": str(self.safesearch),
            "language": self.language,
        }
        if self.categories:
            params["categories"] = self.categories
        if self.engines:
            params["engines"] = self.engines
        return params

    async def __call__(self, query: str, limit: int) -> list[SearchResult]:
        if not self.configured:
            raise SearchNotConfigured(_NOT_CONFIGURED)
        query = (query or "").strip()
        if not query:
            raise SearchFailed("empty search query")
        limit = max(0, min(int(limit or 0), MAX_LIMIT))
        if limit == 0:
            return []

        timeout = httpx.Timeout(self.timeout, connect=self.connect_timeout)
        client = self._client
        owns = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        try:
            resp = await client.get(
                f"{self.base_url}/search",
                params=self.params(query),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise SearchFailed(
                f"SearXNG at {self.base_url} timed out after {self.timeout:g}s "
                f"({type(exc).__name__})"
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchFailed(
                f"SearXNG unreachable at {self.base_url}: {type(exc).__name__}. "
                "No cloud search engine is used as a fallback."
            ) from exc
        finally:
            if owns:
                await client.aclose()

        return self._parse_response(resp, limit)

    def _parse_response(self, resp: httpx.Response, limit: int) -> list[SearchResult]:
        if resp.status_code == 403:
            raise SearchFailed(
                f"SearXNG returned HTTP 403 for a JSON search — {_JSON_HINT}."
            )
        if resp.status_code == 429:
            raise SearchFailed(
                "SearXNG returned HTTP 429 (rate limited). Turn the limiter "
                "off for LAN use: server.limiter: false in settings.yml."
            )
        if resp.status_code != 200:
            raise SearchFailed(f"SearXNG returned HTTP {resp.status_code}.")

        content_type = (resp.headers.get("content-type") or "").lower()
        try:
            payload = resp.json()
        except ValueError as exc:
            detail = "HTML" if "html" in content_type else "a non-JSON body"
            raise SearchFailed(
                f"SearXNG returned {detail} instead of JSON — {_JSON_HINT}."
            ) from exc
        if not isinstance(payload, dict):
            raise SearchFailed(
                f"SearXNG returned {type(payload).__name__} at the top level; "
                "expected a JSON object."
            )

        unresponsive = payload.get("unresponsive_engines")
        if isinstance(unresponsive, list) and unresponsive:
            # Not an error: SearXNG answers with whatever the other engines
            # returned. Worth a log line — a permanently broken engine is
            # otherwise invisible from the outside.
            log.info("searxng: unresponsive engines %s", unresponsive[:10])

        return parse_results(payload, limit)


class AgentSearchSearcher:
    """Calls an AgentSearch instance's ``GET /search``.

    AgentSearch does not REPLACE SearXNG, it wraps it: the container still
    needs a reachable SearXNG behind it, and its own `SEARXNG_URL` is what
    points at one. What it adds in front is the part an agent otherwise has to
    do itself — cross-engine deduplication and scoring, content extraction,
    paywall detection and prompt-injection scrubbing — in one call.

    So this is a second SEARCH PROVIDER, not a migration. Pointing
    `AGENT_SEARCH_URL` at it makes it the one `/search` uses; leaving it unset
    keeps the direct SearXNG path exactly as it was. Both are LAN services the
    operator configured, so both are exempt from the SSRF host block for the
    same reason.

    ## The response shape, and why this reader is forgiving about it

    Upstream documents the endpoints but not the body: the README shows result
    rows being read as `title`, `url` and `snippet`, and names `results` and
    `meta.engine_attempts`, and stops there. Rather than pin a schema that is
    not published, this accepts what :func:`parse_results` already accepts —
    which covers `snippet` and `content` both — and additionally tolerates a
    bare top-level list, because an undocumented envelope is exactly the thing
    that changes between two releases.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        strategy: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        # One of AgentSearch's named modes (general, code, academic, news,
        # private, reference, community). Empty means plain `/search`.
        self.strategy = (strategy or "").strip().lower()
        self.timeout = float(timeout)
        self.connect_timeout = min(float(connect_timeout), self.timeout)
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @property
    def path(self) -> str:
        return "/search/strategy" if self.strategy else "/search"

    def params(self, query: str, limit: int) -> dict[str, str]:
        """The query string sent to AgentSearch. Public so tests can assert it."""
        params = {"q": query, "count": str(limit)}
        if self.strategy:
            params["strategy"] = self.strategy
        return params

    def headers(self) -> dict[str, str]:
        # Only when there is one. AgentSearch's bearer token is optional, and
        # sending `Authorization: Bearer ` to an instance that has none
        # configured is a header it has to decide about for no reason.
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def __call__(self, query: str, limit: int) -> list[SearchResult]:
        if not self.configured:
            raise SearchNotConfigured(
                "AGENT_SEARCH_URL is not configured. jarvis-browser will not "
                "fall back to a cloud search engine; point AGENT_SEARCH_URL at "
                "your AgentSearch instance (default port 3939), or unset it and "
                "use SEARXNG_URL directly."
            )
        query = (query or "").strip()
        if not query:
            raise SearchFailed("empty search query")
        limit = max(0, min(int(limit or 0), MAX_LIMIT))
        if limit == 0:
            return []

        timeout = httpx.Timeout(self.timeout, connect=self.connect_timeout)
        client = self._client
        owns = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        try:
            resp = await client.get(
                f"{self.base_url}{self.path}",
                params=self.params(query, limit),
                headers=self.headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            # AgentSearch fetches and reads the pages it finds, so a slow call
            # is expected behaviour rather than a sick service — say which
            # timeout was hit so the fix is "raise it", not "restart it".
            raise SearchFailed(
                f"AgentSearch at {self.base_url} timed out after {self.timeout:g}s "
                f"({type(exc).__name__}). It fetches and extracts page content, "
                "so it is slower than a bare SearXNG query; raise "
                "BROWSER_SEARCH_TIMEOUT if this is a deep search."
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchFailed(
                f"AgentSearch unreachable at {self.base_url}: {type(exc).__name__}. "
                "No cloud search engine is used as a fallback."
            ) from exc
        finally:
            if owns:
                await client.aclose()

        return self._parse_response(resp, limit)

    def _parse_response(self, resp: httpx.Response, limit: int) -> list[SearchResult]:
        if resp.status_code in (401, 403):
            raise SearchFailed(
                f"AgentSearch returned HTTP {resp.status_code}. It is running "
                "with AGENT_SEARCH_TOKEN set; put the same value in "
                "AGENT_SEARCH_TOKEN for jarvis-browser."
            )
        if resp.status_code == 429:
            raise SearchFailed(
                "AgentSearch returned HTTP 429 (rate limited). Its own "
                "RATE_LIMIT defaults to 60 requests a minute."
            )
        if resp.status_code == 502:
            # The one failure whose cause is behind AgentSearch rather than in
            # it, and the one an operator will otherwise chase in the wrong
            # container.
            raise SearchFailed(
                "AgentSearch returned HTTP 502 — it could not reach the SearXNG "
                "behind it. AgentSearch wraps SearXNG rather than replacing it, "
                "so that instance still has to be up with JSON output enabled."
            )
        if resp.status_code != 200:
            raise SearchFailed(f"AgentSearch returned HTTP {resp.status_code}.")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise SearchFailed(
                "AgentSearch returned a non-JSON body."
            ) from exc

        # A bare list is tolerated because the envelope is not documented.
        if isinstance(payload, list):
            payload = {"results": payload}
        if not isinstance(payload, dict):
            raise SearchFailed(
                f"AgentSearch returned {type(payload).__name__} at the top level; "
                "expected a JSON object."
            )

        attempts = (payload.get("meta") or {}).get("engine_attempts") if isinstance(
            payload.get("meta"), dict
        ) else None
        if isinstance(attempts, list) and attempts:
            failed = [a for a in attempts if isinstance(a, dict) and a.get("error")]
            if failed:
                log.info("agent-search: %d engine attempt(s) errored", len(failed))

        return parse_results(payload, limit)
