"""Shared fixtures. No network, no playwright, no filesystem outside tmp."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from jarvis_browser.app import create_app  # noqa: E402
from jarvis_browser.browser import FakeBackend  # noqa: E402
from jarvis_browser.config import Settings  # noqa: E402

TOKEN = "test-api-token"
SECRET = "test-approval-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
APPROVAL = {"X-Approval-Secret": SECRET}


PUBLIC_IPS = {
    "example.com": ["93.184.216.34"],
    "www.example.com": ["93.184.216.34"],
    "shop.example.com": ["93.184.216.34"],
    "other.net": ["8.8.8.8"],
    "evil.net": ["1.1.1.1"],
}


def fake_resolver(host: str) -> list[str]:
    """Deterministic DNS. Anything unknown resolves nowhere (=> blocked)."""
    return PUBLIC_IPS.get(host.lower(), [])


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """Nothing in the suite is allowed to touch the real resolver."""
    monkeypatch.setattr("jarvis_browser.safety.resolve_host", fake_resolver)


@pytest.fixture(autouse=True)
def no_outbound_http(monkeypatch):
    """Prove the suite makes no outbound calls.

    Any code path that reaches for httpx.AsyncClient (robots.txt, SearXNG)
    fails the test loudly instead of quietly hitting the network.
    """

    class Forbidden:
        def __init__(self, *a, **kw):
            raise AssertionError(
                "the test suite must not make outbound HTTP calls"
            )

    monkeypatch.setattr("jarvis_browser.app.httpx.AsyncClient", Forbidden)
    monkeypatch.setattr("jarvis_browser.search.httpx.AsyncClient", Forbidden)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        api_token=TOKEN,
        approval_secret=SECRET,
        allowlist=(),
        denylist=(),
        act_allowlist=("example.com",),
        lan_allowlist=(),
        session_root=str(tmp_path),
        per_domain_interval=0.0,
        respect_robots=False,
        session_ttl=300.0,
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(
        pages={
            "https://example.com/": (
                "<html><head><title>Example</title></head>"
                "<body><h1>Hi</h1><p>Body text</p>"
                "<a href='/next'>next</a></body></html>"
            ),
            "https://example.com/next": (
                "<html><head><title>Next</title></head>"
                "<body><p>Second page</p></body></html>"
            ),
        }
    )


@pytest.fixture
def client(settings, backend):
    app = create_app(settings, backend=backend, searcher=_no_search,
                     robots_fetch=_no_robots)
    with TestClient(app) as c:
        c.app_backend = backend  # convenience handle for assertions
        yield c


async def _no_search(query: str, limit: int):
    from jarvis_browser.search import SearchNotConfigured

    raise SearchNotConfigured("SEARXNG_URL is not configured")


async def _no_robots(url: str) -> str | None:
    return None
