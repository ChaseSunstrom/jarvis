"""The small web this repository owns.

Three loopback servers — two content sites, a search engine over them and a
browser endpoint — started for the whole of a run. A research answer can only
be checked for *correctness* against pages whose text is in this repository;
against today's internet the strongest assertion available is that the reply
was plausible, which is the assertion this suite exists to avoid.

Each site gets its own loopback ADDRESS rather than its own port, because the
per-domain source cap and the "corroborated by a second source" rule both key
on the host: two ports on 127.0.0.1 are one domain.
"""

from __future__ import annotations

from .fixture_browser import Browser
from .fixture_search import Search
from .fixture_site import SITES, Site, pages_for


class FixtureWeb:
    """Started with a ground and stopped with it."""

    def __init__(self) -> None:
        self.sites: list[Site] = []
        self.search: Search | None = None
        self.browser: Browser | None = None

    def start(self) -> dict[str, str]:
        """Start everything and return the two URLs jarvis-core is configured with."""
        self.sites = [
            Site(host=f"127.0.0.{index + 2}", pages=pages_for(name)).start()
            for index, name in enumerate(SITES)
        ]
        by_name = dict(zip(SITES, (site.url for site in self.sites)))
        self.search = Search(by_name).start()
        self.browser = Browser([site.url for site in self.sites]).start()
        # `handbook` is the site itself: the fixture camera lives there too.
        return {"search": self.search.url, "browser": self.browser.url, "handbook": by_name["handbook"]}

    def stop(self) -> None:
        for closing in (self.browser, self.search, *self.sites):
            if closing is not None:
                closing.stop()
        self.sites, self.search, self.browser = [], None, None
