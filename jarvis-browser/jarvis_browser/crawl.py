"""BFS crawler. Pure logic — every side effect is injected.

The crawler never imports Playwright or opens a socket itself: it takes a
``fetch`` coroutine, a ``robots_fetch`` coroutine, a ``url_ok`` predicate and
a clock. That is what makes the limit tests (max_pages, max_depth,
same_origin, robots.txt, byte cap, wall-clock budget) run offline and
deterministically.

Five independent stop conditions, all enforced here rather than trusted to
the caller: page count, depth, total bytes, wall-clock budget, and robots.
"""

from __future__ import annotations

import asyncio
import re
import urllib.robotparser
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from .browser import FetchResult
from .extract import PageExtract, extract
from .safety import strip_url_credentials

MAX_PATTERN_LEN = 200
MAX_PATTERNS = 8


class CrawlConfigError(ValueError):
    """Bad user-supplied crawl parameters (regex, url, ...)."""


@dataclass(frozen=True)
class CrawlLimits:
    max_pages: int = 10
    max_depth: int = 2
    same_origin_only: bool = True
    url_include: tuple[str, ...] = ()
    url_exclude: tuple[str, ...] = ()
    max_total_bytes: int = 20_000_000
    budget_seconds: float = 120.0
    per_domain_interval: float = 1.0
    respect_robots: bool = True
    user_agent: str = "JarvisBrowser"
    max_chars_per_page: int = 40_000
    max_links_per_page: int = 200


@dataclass
class CrawlPage:
    url: str
    final_url: str
    depth: int
    status: int
    title: str
    text: str
    nbytes: int
    links: list[str] = field(default_factory=list)


@dataclass
class CrawlResult:
    start_url: str
    pages: list[CrawlPage] = field(default_factory=list)
    stopped_reason: str = "completed"
    fetched: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0

    def note_skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def compile_patterns(patterns: tuple[str, ...] | list[str]) -> list[re.Pattern]:
    """Compile user regexes with hard bounds.

    Length- and count-capped: an unbounded pattern from the caller is a cheap
    way to burn CPU, and the caller here is ultimately an LLM.
    """
    if len(patterns) > MAX_PATTERNS:
        raise CrawlConfigError(f"at most {MAX_PATTERNS} patterns allowed")
    out = []
    for p in patterns:
        if len(p) > MAX_PATTERN_LEN:
            raise CrawlConfigError(
                f"pattern longer than {MAX_PATTERN_LEN} chars"
            )
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            raise CrawlConfigError(f"bad regex {p!r}: {exc}") from exc
    return out


def normalise_url(url: str) -> str:
    """Canonical form for the visited set: no fragment, no credentials."""
    url = strip_url_credentials(url.strip())
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    # Drop the default port so http://a/ and http://a:80/ are one page.
    if netloc.endswith(":80") and parts.scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and parts.scheme == "https":
        netloc = netloc[:-4]
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def same_origin(a: str, b: str) -> bool:
    """True if both URLs share scheme, host and port.

    Unparseable is *not* same-origin. ``urlsplit`` defers port validation to
    attribute access, so a single ``<a href="https://x:99999/">`` on a
    crawled page used to raise ValueError out of here and turn /crawl into a
    500 — page content picking the response code is content acting on us.
    """
    try:
        pa, pb = urlsplit(a), urlsplit(b)
        return (
            pa.scheme.lower() == pb.scheme.lower()
            and (pa.hostname or "").lower() == (pb.hostname or "").lower()
            and pa.port == pb.port
        )
    except ValueError:
        return False


def robots_url_for(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


class RobotsCache:
    """Per-origin robots.txt, fetched at most once.

    A missing or unreadable robots.txt means "allowed" — that is what the
    standard says, and pretending otherwise would make the crawler useless
    against every site that returns a 404 there.
    """

    def __init__(
        self,
        fetcher: Callable[[str], Awaitable[str | None]],
        user_agent: str = "JarvisBrowser",
    ):
        self._fetch = fetcher
        self._ua = user_agent
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        robots = robots_url_for(url)
        if robots not in self._cache:
            try:
                body = await self._fetch(robots)
            except Exception:
                body = None
            if body is None:
                self._cache[robots] = None
            else:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.splitlines())
                self._cache[robots] = rp
        rp = self._cache[robots]
        if rp is None:
            return True
        try:
            return rp.can_fetch(self._ua, url)
        except Exception:
            return True


class RateLimiter:
    """Minimum interval between requests to the same host."""

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self.interval <= 0:
            return
        now = self._clock()
        last = self._last.get(host)
        if last is not None:
            delay = self.interval - (now - last)
            if delay > 0:
                await self._sleep(delay)
                now = self._clock()
        self._last[host] = now


async def crawl(
    start_url: str,
    limits: CrawlLimits,
    *,
    fetch: Callable[[str], Awaitable[FetchResult]],
    robots_fetch: Callable[[str], Awaitable[str | None]] | None = None,
    url_ok: Callable[[str], str | None] | None = None,
    clock: Callable[[], float] = monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CrawlResult:
    """Breadth-first crawl from ``start_url`` under ``limits``.

    ``url_ok`` returns a refusal reason or None — the service passes the SSRF
    + domain policy check in here so a link on page 1 cannot walk the crawler
    onto ``127.0.0.1``.
    """
    include = compile_patterns(limits.url_include)
    exclude = compile_patterns(limits.url_exclude)

    result = CrawlResult(start_url=start_url)
    started = clock()
    robots = (
        RobotsCache(robots_fetch, limits.user_agent)
        if (limits.respect_robots and robots_fetch is not None)
        else None
    )
    limiter = RateLimiter(
        limits.per_domain_interval, clock=clock, sleep=sleep
    )

    try:
        start_norm = normalise_url(start_url)
    except ValueError as exc:
        raise CrawlConfigError(f"unparseable start_url: {exc}") from exc

    queue: deque[tuple[str, int]] = deque([(start_norm, 0)])
    seen: set[str] = {start_norm}

    while queue:
        if len(result.pages) >= limits.max_pages:
            result.stopped_reason = "max_pages"
            break
        if clock() - started >= limits.budget_seconds:
            result.stopped_reason = "budget_exhausted"
            break
        if result.total_bytes >= limits.max_total_bytes:
            result.stopped_reason = "byte_cap"
            break

        url, depth = queue.popleft()

        if url_ok is not None:
            reason = url_ok(url)
            if reason:
                result.note_skip("blocked")
                continue

        if robots is not None and not await robots.allowed(url):
            result.note_skip("robots")
            continue

        host = (urlsplit(url).hostname or "").lower()
        await limiter.wait(host)

        try:
            page: FetchResult = await fetch(url)
        except Exception:
            result.note_skip("fetch_error")
            continue

        final_url = page.final_url or url
        # A redirect is chosen by the site, not by us: re-check where we
        # actually landed before the body counts as fetched content.
        if url_ok is not None and final_url != url:
            try:
                redirect_blocked = url_ok(normalise_url(final_url))
            except ValueError:
                redirect_blocked = "unparseable redirect target"
            if redirect_blocked:
                result.note_skip("blocked_redirect")
                continue

        result.fetched += 1
        nbytes = page.nbytes or len(page.html.encode("utf-8", "replace"))
        result.total_bytes += nbytes

        parsed: PageExtract = extract(
            page.html,
            base_url=final_url,
            max_chars=limits.max_chars_per_page,
            max_links=limits.max_links_per_page,
        )
        child_urls = [link.url for link in parsed.links]
        result.pages.append(
            CrawlPage(
                url=url,
                final_url=page.final_url or url,
                depth=depth,
                status=page.status,
                title=parsed.title,
                text=parsed.text,
                nbytes=nbytes,
                links=child_urls,
            )
        )

        if depth >= limits.max_depth:
            continue

        for child in child_urls:
            try:
                norm = normalise_url(child)
            except ValueError:
                # e.g. `http://[::1` — a malformed href must not be able to
                # end the crawl with a traceback.
                result.note_skip("unparseable_link")
                continue
            if norm in seen:
                continue
            if limits.same_origin_only and not same_origin(norm, start_url):
                result.note_skip("cross_origin")
                seen.add(norm)
                continue
            if include and not any(p.search(norm) for p in include):
                result.note_skip("not_included")
                seen.add(norm)
                continue
            if exclude and any(p.search(norm) for p in exclude):
                result.note_skip("excluded")
                seen.add(norm)
                continue
            seen.add(norm)
            queue.append((norm, depth + 1))

    # stopped_reason defaults to "completed"; the loop body overwrites it on
    # every early break (max_pages / budget_exhausted / byte_cap).
    return result
