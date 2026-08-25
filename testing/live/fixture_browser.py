"""A stand-in for jarvis-browser's `/fetch`, over the fixture site.

`jarvis-browser` fetches pages with Playwright, and refuses loopback and RFC1918
addresses by design (`safety.is_blocked_host`) — which is right, and which means
the running one cannot read a fixture site on 127.0.0.1. Starting a second
instance is not an option here either: its Playwright browser is not installed
on this host.

So this serves the same two routes the `web` integration calls, over the pages
in `fixtures/`. What that means for a run using it:

* **Proved:** the research pipeline end to end — planning several queries,
  searching, ranking and de-duplicating sources, reading each page, taking
  notes on it, following a lead, cross-checking claims, writing the report,
  citing what was read, and the file and note it leaves behind.
* **Not proved:** jarvis-browser itself — its SSRF guards, its robots handling,
  its rendering of a page that needs JavaScript. Those have their own suite
  (`jarvis-browser/tests`), and `docs/verification.md` says which claims rest
  on it.

    python3 -m testing.live.fixture_browser --port 8903 --site http://127.0.0.1:8901
"""

from __future__ import annotations

import argparse
import http.server
import json
import re
import threading
import urllib.parse
import urllib.request

from .fixture_site import free_port

_TAG = re.compile(r"<[^>]+>")

#: Blocks whose CONTENT is not page text. Stripped before the tags are,
#: because `<[^>]+>` alone leaves the script's source behind — which is how a
#: stand-in that cannot run JavaScript came to "read" a page rendered by it,
#: and, in anything that reaches a model, how a page's <script> becomes text
#: somebody's assistant is reading. The real extractor drops the same set
#: (`jarvis-browser/jarvis_browser/extract.py`, DROP_TAGS).
_DEAD = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.S | re.I)



class _Handler(http.server.BaseHTTPRequestHandler):
    #: Every fixture site this fake will read. Anything else is refused, the
    #: same shape of refusal the real one gives for a host it will not read.
    site_urls: tuple[str, ...] = ()

    def log_message(self, *_args: object) -> None:  # noqa: D102 - quiet on purpose
        return

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/healthz", "/health"):
            self._json({"ok": True, "kind": "fixture-browser"})
            return
        self.send_error(404, "this fake serves /fetch and /healthz")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/fetch":
            self.send_error(404, "this fake serves /fetch only")
            return
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"detail": "invalid JSON"}, 400)
            return
        url = str(body.get("url") or "")
        if not any(url.startswith(base) for base in self.site_urls):
            # The same shape of refusal the real one gives for a host it will
            # not read, so a scenario that points somewhere else fails here
            # rather than silently fetching it.
            self._json({"detail": f"refused: {url} is not a fixture site"}, 400)
            return
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                html = response.read().decode("utf-8", "replace")
                status = response.status
        except Exception as err:  # noqa: BLE001
            self._json({"detail": f"fetch failed: {err}"}, 502)
            return
        text = " ".join(_TAG.sub(" ", _DEAD.sub(" ", html)).split())
        title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        self._json(
            {
                "url": url,
                "requested_url": url,
                "final_url": url,
                "status": status,
                "title": (title.group(1).strip() if title else ""),
                "text": text,
                "content_is_untrusted": True,
                "truncated": False,
            }
        )


class Browser:
    """`/fetch` over the fixture sites. `sites` is one URL or several."""

    def __init__(self, sites: "str | list[str] | tuple[str, ...]",
                 port: int | None = None, host: str = "127.0.0.1") -> None:
        self.host = host
        self.port = port or free_port()
        self.site_urls = (
            (sites.rstrip("/"),) if isinstance(sites, str)
            else tuple(url.rstrip("/") for url in sites)
        )
        self._server: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "Browser":
        _Handler.site_urls = self.site_urls
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "Browser":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--site", default="http://127.0.0.1:8901")
    args = parser.parse_args(argv)
    browser = Browser(args.site, args.port or None).start()
    print(browser.url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    browser.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
