"""SearXNG proxy. The only search path this service has.

There is deliberately no fallback. If SEARXNG_URL is unset, /search fails
with a clear error rather than quietly reaching for Google — a private stack
that silently phones a cloud engine is worse than one that says "not
configured", because the operator never finds out.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .safety import sanitize_untrusted


class SearchNotConfigured(Exception):
    pass


class SearchFailed(Exception):
    pass


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


class SearxngSearcher:
    """Calls a LAN SearXNG instance's JSON API.

    The SearXNG URL is operator-configured, so it is exempt from the SSRF
    host block by design — it is normally a private address, and pointing
    Jarvis at it is a deliberate local decision.
    """

    def __init__(self, base_url: str, *, timeout: float = 15.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    async def __call__(self, query: str, limit: int) -> list[SearchResult]:
        if not self.base_url:
            raise SearchNotConfigured(
                "SEARXNG_URL is not configured. jarvis-browser will not fall "
                "back to a cloud search engine; point SEARXNG_URL at your "
                "local SearXNG instance."
            )
        params = {
            "q": query,
            "format": "json",
            "safesearch": "1",
            "language": "en",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=False
            ) as client:
                resp = await client.get(
                    f"{self.base_url}/search", params=params
                )
        except httpx.HTTPError as exc:
            raise SearchFailed(
                f"SearXNG unreachable at {self.base_url}: {type(exc).__name__}"
            ) from exc
        if resp.status_code != 200:
            raise SearchFailed(
                f"SearXNG returned HTTP {resp.status_code}. If this is 403, "
                "enable the JSON format in searxng settings.yml "
                "(search.formats: [html, json])."
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SearchFailed(
                "SearXNG did not return JSON — enable "
                "'json' in search.formats in settings.yml"
            ) from exc
        return parse_searxng(payload, limit)


def parse_searxng(payload: dict, limit: int) -> list[SearchResult]:
    """Normalise a SearXNG JSON body. Every field is untrusted text."""
    out: list[SearchResult] = []
    for item in (payload or {}).get("results", [])[: max(0, limit)]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))[:2048]
        if not url:
            continue
        engines = item.get("engines") or []
        out.append(
            SearchResult(
                title=sanitize_untrusted(str(item.get("title", "")))[:300],
                url=url,
                snippet=sanitize_untrusted(str(item.get("content", "")))[:1000],
                engine=sanitize_untrusted(
                    ", ".join(str(e) for e in engines)
                    if isinstance(engines, list)
                    else str(engines)
                )[:120],
            )
        )
    return out
