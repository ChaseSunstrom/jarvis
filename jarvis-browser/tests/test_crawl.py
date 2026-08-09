"""Crawler limits: pages, depth, origin, robots, bytes, wall clock, rates."""

from __future__ import annotations

import pytest

from jarvis_browser.browser import FetchResult
from jarvis_browser.crawl import (
    CrawlConfigError,
    CrawlLimits,
    RateLimiter,
    compile_patterns,
    crawl,
    normalise_url,
    robots_url_for,
    same_origin,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def page(*links: str, body: str = "hello") -> str:
    anchors = "".join(f"<a href='{u}'>l</a>" for u in links)
    return f"<html><head><title>T</title></head><body><p>{body}</p>{anchors}</body></html>"


class FakeSite:
    """A fake web. Records fetch order so tests can assert on it."""

    def __init__(self, pages: dict[str, str], robots: dict[str, str] | None = None):
        self.pages = pages
        self.robots = robots or {}
        self.fetched: list[str] = []
        self.robots_fetched: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.fetched.append(url)
        html = self.pages.get(url, "")
        return FetchResult(
            html=html,
            final_url=url,
            status=200 if html else 404,
            nbytes=len(html.encode()),
        )

    async def robots_fetch(self, url: str) -> str | None:
        self.robots_fetched.append(url)
        return self.robots.get(url)


# ------------------------------------------------------------------ helpers
def test_normalise_url():
    assert normalise_url("https://A.com/x#frag") == "https://a.com/x"
    assert normalise_url("https://a.com") == "https://a.com/"
    assert normalise_url("https://a.com:443/x") == "https://a.com/x"
    assert normalise_url("http://a.com:80/x") == "http://a.com/x"
    assert normalise_url("https://u:p@a.com/x") == "https://a.com/x"


def test_same_origin():
    assert same_origin("https://a.com/1", "https://a.com/2")
    assert not same_origin("https://a.com/1", "http://a.com/1")   # scheme
    assert not same_origin("https://a.com/1", "https://b.com/1")  # host
    assert not same_origin("https://a.com:1/1", "https://a.com/1")  # port


def test_robots_url_for():
    assert robots_url_for("https://a.com/deep/page?x=1") == "https://a.com/robots.txt"


def test_compile_patterns_bounds():
    with pytest.raises(CrawlConfigError):
        compile_patterns(["("])                       # invalid regex
    with pytest.raises(CrawlConfigError):
        compile_patterns(["x" * 300])                 # too long
    with pytest.raises(CrawlConfigError):
        compile_patterns([str(i) for i in range(20)])  # too many
    assert compile_patterns(["/docs/"])


# -------------------------------------------------------------------- caps
async def test_max_pages_is_respected():
    site = FakeSite({
        f"https://example.com/p{i}": page(*[f"/p{j}" for j in range(10)])
        for i in range(10)
    })
    result = await crawl(
        "https://example.com/p0",
        CrawlLimits(max_pages=3, max_depth=5, per_domain_interval=0),
        fetch=site.fetch,
    )
    assert len(result.pages) == 3
    assert len(site.fetched) == 3
    assert result.stopped_reason == "max_pages"


async def test_max_depth_is_respected():
    site = FakeSite({
        "https://example.com/a": page("/b"),
        "https://example.com/b": page("/c"),
        "https://example.com/c": page("/d"),
        "https://example.com/d": page(),
    })
    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=100, max_depth=1, per_domain_interval=0),
        fetch=site.fetch,
    )
    urls = [p.url for p in result.pages]
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert max(p.depth for p in result.pages) == 1


async def test_depth_zero_fetches_only_the_start_page():
    site = FakeSite({
        "https://example.com/a": page("/b"),
        "https://example.com/b": page(),
    })
    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_depth=0, per_domain_interval=0),
        fetch=site.fetch,
    )
    assert [p.url for p in result.pages] == ["https://example.com/a"]


async def test_same_origin_only_blocks_offsite_links():
    site = FakeSite({
        "https://example.com/a": page("/b", "https://evil.net/x",
                                      "http://example.com/insecure"),
        "https://example.com/b": page(),
        "https://evil.net/x": page(),
        "http://example.com/insecure": page(),
    })
    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=50, max_depth=3, same_origin_only=True,
                    per_domain_interval=0),
        fetch=site.fetch,
    )
    urls = {p.url for p in result.pages}
    assert urls == {"https://example.com/a", "https://example.com/b"}
    assert "https://evil.net/x" not in site.fetched
    assert result.skipped.get("cross_origin") == 2  # evil.net + scheme change


async def test_cross_origin_allowed_when_asked():
    site = FakeSite({
        "https://example.com/a": page("https://other.net/x"),
        "https://other.net/x": page(),
    })
    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=50, max_depth=2, same_origin_only=False,
                    per_domain_interval=0),
        fetch=site.fetch,
    )
    assert "https://other.net/x" in {p.url for p in result.pages}


async def test_include_and_exclude_patterns():
    site = FakeSite({
        "https://example.com/start": page("/docs/a", "/blog/b", "/docs/private"),
        "https://example.com/docs/a": page(),
        "https://example.com/blog/b": page(),
        "https://example.com/docs/private": page(),
    })
    result = await crawl(
        "https://example.com/start",
        CrawlLimits(max_pages=50, max_depth=2, per_domain_interval=0,
                    url_include=(r"/docs/",), url_exclude=(r"private",)),
        fetch=site.fetch,
    )
    urls = {p.url for p in result.pages}
    assert urls == {"https://example.com/start", "https://example.com/docs/a"}
    assert result.skipped.get("not_included") == 1
    assert result.skipped.get("excluded") == 1


async def test_visited_set_prevents_loops():
    site = FakeSite({
        "https://example.com/a": page("/b", "/a"),
        "https://example.com/b": page("/a", "/b"),
    })
    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=50, max_depth=5, per_domain_interval=0),
        fetch=site.fetch,
    )
    assert len(site.fetched) == 2
    assert len(result.pages) == 2


async def test_byte_cap_stops_the_crawl():
    big = page(*[f"/p{j}" for j in range(5)], body="x" * 5000)
    site = FakeSite({f"https://example.com/p{i}": big for i in range(5)})
    result = await crawl(
        "https://example.com/p0",
        CrawlLimits(max_pages=50, max_depth=5, per_domain_interval=0,
                    max_total_bytes=6000),
        fetch=site.fetch,
    )
    assert result.stopped_reason == "byte_cap"
    assert len(result.pages) < 5


async def test_wall_clock_budget_stops_the_crawl():
    """A fake clock that jumps 10s per read: the budget must bite."""
    ticks = iter([0.0, 0.0, 5.0, 5.0, 20.0, 20.0, 30.0, 30.0, 40.0])

    def clock():
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    site = FakeSite({
        f"https://example.com/p{i}": page(*[f"/p{j}" for j in range(6)])
        for i in range(6)
    })
    result = await crawl(
        "https://example.com/p0",
        CrawlLimits(max_pages=50, max_depth=5, per_domain_interval=0,
                    budget_seconds=15.0),
        fetch=site.fetch,
        clock=clock,
    )
    assert result.stopped_reason == "budget_exhausted"
    assert len(result.pages) < 6


# ------------------------------------------------------------------ robots
async def test_robots_disallow_is_honoured():
    site = FakeSite(
        pages={
            "https://example.com/ok": page("/private/secret", "/also-ok"),
            "https://example.com/private/secret": page(),
            "https://example.com/also-ok": page(),
        },
        robots={
            "https://example.com/robots.txt":
                "User-agent: *\nDisallow: /private/\n"
        },
    )
    result = await crawl(
        "https://example.com/ok",
        CrawlLimits(max_pages=50, max_depth=3, per_domain_interval=0,
                    respect_robots=True, user_agent="JarvisBrowser"),
        fetch=site.fetch,
        robots_fetch=site.robots_fetch,
    )
    urls = {p.url for p in result.pages}
    assert "https://example.com/private/secret" not in urls
    assert "https://example.com/private/secret" not in site.fetched
    assert urls == {"https://example.com/ok", "https://example.com/also-ok"}
    assert result.skipped.get("robots") == 1


async def test_robots_disallow_all_stops_everything():
    site = FakeSite(
        pages={"https://example.com/x": page()},
        robots={"https://example.com/robots.txt":
                "User-agent: *\nDisallow: /\n"},
    )
    result = await crawl(
        "https://example.com/x",
        CrawlLimits(per_domain_interval=0, respect_robots=True),
        fetch=site.fetch,
        robots_fetch=site.robots_fetch,
    )
    assert result.pages == []
    assert site.fetched == []


async def test_robots_is_fetched_once_per_origin():
    site = FakeSite(
        pages={
            "https://example.com/a": page("/b", "/c"),
            "https://example.com/b": page(),
            "https://example.com/c": page(),
        },
        robots={"https://example.com/robots.txt": "User-agent: *\nAllow: /\n"},
    )
    await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=10, max_depth=2, per_domain_interval=0,
                    respect_robots=True),
        fetch=site.fetch,
        robots_fetch=site.robots_fetch,
    )
    assert site.robots_fetched == ["https://example.com/robots.txt"]


async def test_missing_robots_means_allowed():
    site = FakeSite(pages={"https://example.com/x": page()}, robots={})
    result = await crawl(
        "https://example.com/x",
        CrawlLimits(per_domain_interval=0, respect_robots=True),
        fetch=site.fetch,
        robots_fetch=site.robots_fetch,
    )
    assert len(result.pages) == 1


async def test_respect_robots_false_skips_the_check():
    site = FakeSite(
        pages={"https://example.com/x": page()},
        robots={"https://example.com/robots.txt":
                "User-agent: *\nDisallow: /\n"},
    )
    result = await crawl(
        "https://example.com/x",
        CrawlLimits(per_domain_interval=0, respect_robots=False),
        fetch=site.fetch,
        robots_fetch=site.robots_fetch,
    )
    assert len(result.pages) == 1
    assert site.robots_fetched == []


# ------------------------------------------------------------ url_ok / SSRF
async def test_url_ok_blocks_links_that_point_inward():
    """A link on a public page must not walk the crawler onto the LAN."""
    site = FakeSite({
        "https://example.com/a": page("http://127.0.0.1:8123/api", "/b"),
        "http://127.0.0.1:8123/api": page(body="HA SECRETS"),
        "https://example.com/b": page(),
    })

    def url_ok(url: str):
        return "blocked" if "127.0.0.1" in url else None

    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=10, max_depth=2, same_origin_only=False,
                    per_domain_interval=0),
        fetch=site.fetch,
        url_ok=url_ok,
    )
    assert "http://127.0.0.1:8123/api" not in site.fetched
    assert result.skipped.get("blocked") == 1
    assert not any("HA SECRETS" in p.text for p in result.pages)


async def test_fetch_errors_are_counted_not_fatal():
    async def boom(url: str):
        if url.endswith("/bad"):
            raise RuntimeError("nope")
        return FetchResult(html=page("/bad"), final_url=url, status=200,
                           nbytes=100)

    result = await crawl(
        "https://example.com/a",
        CrawlLimits(max_pages=10, max_depth=2, per_domain_interval=0),
        fetch=boom,
    )
    assert result.skipped.get("fetch_error") == 1
    assert len(result.pages) == 1


# ------------------------------------------------------------- rate limiter
async def test_rate_limiter_waits_per_domain():
    slept: list[float] = []
    now = [100.0]

    async def sleep(d):
        slept.append(d)
        now[0] += d

    limiter = RateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
    await limiter.wait("a.com")     # first hit, no wait
    assert slept == []
    await limiter.wait("b.com")     # different host, no wait
    assert slept == []
    await limiter.wait("a.com")     # same host, immediately => waits
    assert slept == [2.0]


async def test_rate_limiter_disabled_at_zero():
    async def sleep(d):
        raise AssertionError("must not sleep")

    limiter = RateLimiter(0.0, clock=lambda: 0.0, sleep=sleep)
    await limiter.wait("a.com")
    await limiter.wait("a.com")


async def test_crawl_extracts_title_and_text():
    site = FakeSite({"https://example.com/a": page(body="the body text")})
    result = await crawl(
        "https://example.com/a",
        CrawlLimits(per_domain_interval=0),
        fetch=site.fetch,
    )
    assert result.pages[0].title == "T"
    assert "the body text" in result.pages[0].text
