"""A website with known content, served on loopback.

Research is the one capability whose output cannot be checked by looking at the
house: the answer is prose, and prose that sounds right is exactly what a
language model produces when it has read nothing. So the rig serves its own
small web — four pages this repository wrote — and the scenarios assert on
facts that appear in them.

    python3 -m testing.live.fixture_site --port 8901

Deliberately dull HTML: the point is the text, and a fixture that needed
JavaScript to render would be testing the fetcher's browser rather than the
research loop.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGES = HERE / "fixtures" / "handbook"

#: The facts the scenarios assert on, and where each one lives. Kept here so a
#: scenario and the page it checks cannot drift apart silently: the rig's own
#: tests read this table and the pages, and fail if a fact has left the text.
FACTS = {
    "boiler_max_pressure": ("heating.html", "2.5 bar"),
    "flow_temperature": ("heating.html", "55 °C"),
    "cylinder_litres": ("heating.html", "210 litres"),
    "resting_watts": ("power.html", "412 watts"),
    "cheap_rate": ("power.html", "00:30 to 07:30"),
    "mains_pressure": ("water.html", "3.1 bar"),
    "stack_relined": ("water.html", "2021"),
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Site:
    """The handbook, on a port, for as long as the `with` block lasts."""

    def __init__(self, port: int | None = None, host: str = "127.0.0.1") -> None:
        self.host = host
        self.port = port or free_port()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "Site":
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(PAGES)
        )
        # Quieted on the class the partial builds, not on the partial: setting
        # it on `handler` above did nothing (a functools.partial has no
        # attribute the server ever consults) and every fetch still logged.
        handler.func.log_message = lambda *_a, **_k: None  # type: ignore[attr-defined]
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "Site":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    site = Site(args.port or None).start()
    print(site.url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    site.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
