"""A search engine over the fixture site, speaking SearXNG's JSON.

The brief asks for SearXNG pointed at a fixture website. SearXNG ships in this
repo's compose file behind `--profile search`, and on this host `jarvisdev`
cannot reach the Docker socket — so the container cannot be started, and
`BLOCKERS.md` says so.

What runs instead is this: the same `/search?q=…&format=json` contract, over
the four handbook pages, so Jarvis's *real* search client, its real fetcher and
its real reader all run unchanged. What is not proved by it is SearXNG itself —
its engines, its rate limits, its result ranking — and no scenario claims
otherwise.

    python3 -m testing.live.fixture_search --port 8902 --site http://127.0.0.1:8901
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import threading
import urllib.parse
import urllib.request

from .fixture_site import PAGES, free_port

_TAG = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    return " ".join(_TAG.sub(" ", html).split())


def _pages() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for path in sorted(PAGES.glob("*.html")):
        html = path.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        out.append(
            {
                "file": path.name,
                "title": (title.group(1) if title else path.stem).strip(),
                "text": _text(html),
            }
        )
    return out


class _Handler(http.server.BaseHTTPRequestHandler):
    site_url = "http://127.0.0.1:8901"
    pages: list[dict[str, str]] = []

    def log_message(self, *_args: object) -> None:  # noqa: D102 - quiet on purpose
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") not in ("/search", ""):
            self.send_error(404, "this fake speaks /search only")
            return
        query = urllib.parse.parse_qs(parsed.query)
        terms = [t.lower() for t in re.findall(r"[\w']+", " ".join(query.get("q") or []))]
        results = []
        for page in self.pages:
            haystack = f"{page['title']} {page['text']}".lower()
            score = sum(haystack.count(term) for term in terms if len(term) > 2)
            if score:
                # A snippet around the first hit, which is what a search engine
                # returns and what a reader has to decide to open.
                first = min(
                    (haystack.find(t) for t in terms if len(t) > 2 and t in haystack),
                    default=0,
                )
                results.append(
                    {
                        "url": f"{self.site_url}/{page['file']}",
                        "title": page["title"],
                        "content": page["text"][max(0, first - 80) : first + 240],
                        "engine": "fixture",
                        "score": float(score),
                        "category": "general",
                    }
                )
        results.sort(key=lambda row: row["score"], reverse=True)
        body = json.dumps(
            {
                "query": " ".join(query.get("q") or []),
                "number_of_results": len(results),
                "results": results,
                "answers": [],
                "corrections": [],
                "infoboxes": [],
                "suggestions": [],
                "unresponsive_engines": [],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Search:
    def __init__(self, site_url: str, port: int | None = None,
                 host: str = "127.0.0.1") -> None:
        self.host = host
        self.port = port or free_port()
        self.site_url = site_url.rstrip("/")
        self._server: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "Search":
        handler = functools.partial(_Handler)
        _Handler.site_url = self.site_url
        _Handler.pages = _pages()
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "Search":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--site", default="http://127.0.0.1:8901")
    args = parser.parse_args(argv)
    search = Search(args.site, args.port or None).start()
    print(search.url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    search.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
