"""Watch the web (M59): a page that changes, a feed with something new, a
question whose answer becomes yes, and a reader that works with nothing running.

No network: every fetch goes to an httpx MockTransport standing in for the
web, and the model is a stub. What is asserted is the moment — the change
lands in the notifications store and on the bus, never only in a log.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import watch as watch_integration  # noqa: E402
from jarvis.integrations.watch import (  # noqa: E402
    EVENT_CHANGED,
    MIN_INTERVAL,
    WatchManager,
    html_to_text,
    parse_feed,
    what_changed,
)
from jarvis.llm.tools import ToolRegistry  # noqa: E402

pytestmark = pytest.mark.asyncio

PAGE_V1 = "<html><head><title>Opening hours</title><script>var x=1;</script></head><body><h1>Pool</h1><p>Open 07:00–21:00.</p><p>Closed Mondays.</p></body></html>"
PAGE_V2 = "<html><head><title>Opening hours</title></head><body><h1>Pool</h1><p>Open 06:00–22:00.</p><p>Closed Mondays.</p></body></html>"
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Village news</title>
<item><title>Fete on Saturday</title><link>http://127.0.0.1:1/fete</link><guid>n1</guid><pubDate>Mon, 24 Aug 2026 10:00:00 GMT</pubDate><description>Cakes.</description></item>
</channel></rss>"""
RSS_MORE = RSS.replace("</channel>", '<item><title>Road closed</title><link>http://127.0.0.1:1/road</link><guid>n2</guid></item></channel>')
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title>
<entry><id>tag:r1</id><title>v1.0</title><link rel="alternate" href="http://127.0.0.1:1/v1"/><updated>2026-08-25T10:00:00Z</updated><summary>First.</summary></entry>
</feed>"""


class FakeWeb:
    """The web, as a dict of url → body that a test can change between checks."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}
        self.hits: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        if url not in self.pages:
            return httpx.Response(404, text="no such page")
        return httpx.Response(200, text=self.pages[url], headers={"content-type": "text/html"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


async def make_house(tmp_path: Path, web: FakeWeb, **options: Any) -> tuple[Jarvis, WatchManager, ToolRegistry]:
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"notifications": {"max_entries": 50}})
    registry = ToolRegistry(jarvis)
    jarvis.data["llm_tools"] = registry
    assert await watch_integration.async_setup(jarvis, {"interval": 60, "_transport": web.transport, **options}) is True
    manager = jarvis.data["watch"]
    return jarvis, manager, registry


async def moments(jarvis: Jarvis) -> list[dict[str, Any]]:
    listing = await jarvis.services.async_call("notifications", "list", {}, blocking=True, return_response=True)
    return [row for row in (listing.get("notifications") or []) if row.get("kind") == "watch"]


async def run(registry: ToolRegistry, name: str, **args: Any) -> Any:
    tool = registry.get(name)
    assert tool is not None, f"{name} is not registered"
    return await tool.handler(args, None)


# --- text and feeds -------------------------------------------------------------


def test_html_becomes_text_without_scripts_and_a_rewrap_is_not_a_change():
    title, text = html_to_text(PAGE_V1)
    assert title == "Opening hours"
    assert "var x" not in text and "Open 07:00–21:00." in text and "Closed Mondays." in text
    _, again = html_to_text(PAGE_V1.replace("<p>Open", "<p>\n   Open"))
    assert again == text


def test_what_changed_names_the_new_lines():
    assert what_changed("a\nb", "a\nc") == "c"
    assert what_changed("a\nb", "a").startswith("removed: b")


def test_rss_and_atom_parse_with_the_standard_library_and_junk_does_not():
    title, entries = parse_feed(RSS)
    assert title == "Village news" and [e.id for e in entries] == ["n1"]
    assert entries[0].link.endswith("/fete") and entries[0].summary == "Cakes."
    title, entries = parse_feed(ATOM)
    assert title == "Releases" and entries[0].id == "tag:r1" and entries[0].link.endswith("/v1")
    assert parse_feed("<html><body>not a feed</body></html>") == ("", [])
    assert parse_feed("<<<") == ("", [])


# --- a page -------------------------------------------------------------------------


async def test_a_page_that_changes_lands_as_a_moment_and_a_bus_event(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/pool.html"] = PAGE_V1
    jarvis, manager, registry = await make_house(tmp_path, web)
    fired: list[dict[str, Any]] = []
    jarvis.bus.listen(EVENT_CHANGED, lambda e: fired.append(dict(e.data)))

    added = await run(registry, "watch_page", url="http://127.0.0.1:1/pool.html", interval=60)
    assert "spoken" in added and added["watch"]["title"] == "Opening hours"
    watch = manager.watches[added["watch"]["id"]]
    assert watch.checks == 1 and watch.changes == 0, "the first check is the baseline, not a change"

    # The same page again: nothing.
    assert (await manager.check(watch))["changed"] is False
    assert await moments(jarvis) == []

    web.pages["http://127.0.0.1:1/pool.html"] = PAGE_V2
    outcome = await manager.check(watch)
    assert outcome["changed"] is True and "06:00" in outcome["summary"]
    await asyncio.sleep(0.05)
    rows = await moments(jarvis)
    assert len(rows) == 1 and rows[0]["title"].startswith("Opening hours") and "06:00" in rows[0]["body"]
    assert rows[0]["link"] == "http://127.0.0.1:1/pool.html" and rows[0]["source"] == "watch"
    assert fired and fired[0]["id"] == watch.id and fired[0]["kind"] == "page"

    # Listed, then cancelled.
    listed = await run(registry, "list_watches")
    assert listed["count"] == 1 and "1 change" in listed["spoken"]
    gone = await run(registry, "cancel_watch", id=watch.id)
    assert "cancelled" in gone and manager.watches == {}
    await jarvis.async_stop()


async def test_a_watch_never_checks_faster_than_the_floor_and_a_bad_url_is_refused(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/a"] = PAGE_V1
    jarvis, manager, registry = await make_house(tmp_path, web)
    added = await run(registry, "watch_page", url="http://127.0.0.1:1/a", interval=1)
    assert added["watch"]["interval"] == MIN_INTERVAL
    refused = await run(registry, "watch_page", url="not a url")
    assert "error" in refused
    # Not due again straight away: the loop's `check_due` leaves it alone.
    assert await manager.check_due() == []
    await jarvis.async_stop()


async def test_a_page_that_cannot_be_fetched_is_a_recorded_error_not_a_change(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/a"] = PAGE_V1
    jarvis, manager, registry = await make_house(tmp_path, web)
    added = await run(registry, "watch_page", url="http://127.0.0.1:1/a")
    watch = manager.watches[added["watch"]["id"]]
    del web.pages["http://127.0.0.1:1/a"]
    outcome = await manager.check(watch)
    assert outcome["changed"] is False and "404" in outcome["error"]
    assert watch.last_error and watch.changes == 0
    assert await moments(jarvis) == []
    await jarvis.async_stop()


# --- a feed ---------------------------------------------------------------------------


async def test_a_feed_with_something_new_is_a_moment_naming_it(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/news.xml"] = RSS
    jarvis, manager, registry = await make_house(tmp_path, web)
    added = await run(registry, "watch_feed", url="http://127.0.0.1:1/news.xml")
    watch = manager.watches[added["watch"]["id"]]
    assert watch.title == "Village news" and watch.seen == ["n1"]
    assert (await manager.check(watch))["changed"] is False
    web.pages["http://127.0.0.1:1/news.xml"] = RSS_MORE
    outcome = await manager.check(watch)
    assert outcome["changed"] is True and outcome["summary"] == "Road closed"
    rows = await moments(jarvis)
    assert rows and "something new" in rows[0]["title"] and rows[0]["body"] == "Road closed"
    latest = await run(registry, "feed_latest", url="http://127.0.0.1:1/news.xml", limit=5)
    assert [e["title"] for e in latest["entries"]] == ["Fete on Saturday", "Road closed"]
    await jarvis.async_stop()


async def test_a_page_that_is_not_a_feed_is_refused_as_a_feed(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/pool.html"] = PAGE_V1
    jarvis, manager, registry = await make_house(tmp_path, web)
    refused = await run(registry, "feed_latest", url="http://127.0.0.1:1/pool.html")
    assert "error" in refused and "RSS" in refused["error"]
    await jarvis.async_stop()


# --- a question --------------------------------------------------------------------------


class StubAgent:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def ask_once(self, prompt: str, *, system: str = "") -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else '{"yes": false, "because": "nothing yet"}'


async def test_tell_me_when_asks_again_until_the_answer_is_yes(tmp_path):
    web = FakeWeb()
    jarvis, manager, registry = await make_house(tmp_path, web)
    agent = StubAgent(['{"yes": false, "because": "no announcement in the results"}', 'Sure: {"yes": true, "because": "the council page says the pool opens 1 September"}'])
    jarvis.data["llm"] = agent
    added = await run(registry, "watch_for", question="has the village pool reopened?", interval=60)
    watch = manager.watches[added["watch"]["id"]]
    assert "keep asking" in added["spoken"] and agent.prompts == [], "the baseline does not ask"
    first = await manager.check(watch)
    assert first["changed"] is False and "no announcement" in first["because"]
    assert "has the village pool reopened?" in agent.prompts[0]
    second = await manager.check(watch)
    assert second["changed"] is True and watch.done is True
    rows = await moments(jarvis)
    assert rows and "is now yes" in rows[0]["title"] and "1 September" in rows[0]["body"]
    assert await manager.check_due() == [], "a question answered yes is not asked again"
    await jarvis.async_stop()


# --- the reader ------------------------------------------------------------------------


async def test_read_page_reads_here_when_no_browser_is_configured(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/pool.html"] = PAGE_V1
    jarvis, manager, registry = await make_house(tmp_path, web)
    page = await run(registry, "read_page", url="http://127.0.0.1:1/pool.html")
    assert page["title"] == "Opening hours" and "Closed Mondays." in page["text"] and page["untrusted"] is True
    assert "error" in await run(registry, "read_page", url="ftp://x")
    await jarvis.async_stop()


async def test_read_page_goes_through_the_browser_when_it_is_there(tmp_path):
    """A page that draws itself with JavaScript is read by jarvis-browser."""
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/app.html"] = "<html><body><div id=app>Loading…</div><script>draw()</script></body></html>"
    jarvis, manager, registry = await make_house(tmp_path, web)
    seen: list[dict[str, Any]] = []

    async def fetch(call: Any) -> Any:
        seen.append(dict(call.data))
        return {"title": "The app", "text": "Rendered: 3 appliances, the oven the largest."}

    jarvis.data["web"] = {"configured": True}
    jarvis.services.register("web", "fetch", fetch, supports_response=True)
    page = await run(registry, "read_page", url="http://127.0.0.1:1/app.html")
    assert seen == [{"url": "http://127.0.0.1:1/app.html", "render": True}]
    assert page["text"].startswith("Rendered:") and "Loading" not in page["text"]
    assert web.hits == [], "the browser read it; this process did not fetch it as well"
    await jarvis.async_stop()


# --- persistence ---------------------------------------------------------------------------


async def test_watches_survive_a_restart(tmp_path):
    web = FakeWeb()
    web.pages["http://127.0.0.1:1/a"] = PAGE_V1
    jarvis, manager, registry = await make_house(tmp_path, web)
    added = await run(registry, "watch_page", url="http://127.0.0.1:1/a", title="the pool")
    await jarvis.async_stop()
    saved = json.loads((tmp_path / "watch" / "watches.json").read_text())
    assert saved["watches"][0]["id"] == added["watch"]["id"]

    again, manager2, _ = await make_house(tmp_path, web)
    assert list(manager2.watches) == [added["watch"]["id"]]
    assert manager2.watches[added["watch"]["id"]].digest == manager.watches[added["watch"]["id"]].digest
    await again.async_stop()
