#!/usr/bin/env python3
"""A cloud provider that is not one: an OpenAI-compatible server on loopback.

M40 has to prove four things about the gateway, and three of them need a
provider that is NOT local:

    an override reaches it
    an error from it falls back
    a request tagged local-only NEVER reaches it

No test may touch a real provider — there is no key, and there would be no
point: what is under test is the routing and the guard, not somebody else's
model. So this answers `/v1/chat/completions` like OpenAI, records every
request it was given, and can be told to fail on demand.

    python3 testing/fixtures/mock_cloud.py --port 8899

`GET /requests` returns what it has been asked, which is how "the guard refused"
is proven: not by reading a log, but by the provider having heard nothing.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    #: Every request this fake has been given, newest last.
    seen: list[dict[str, Any]] = []
    #: Set to a status code to make the next call fail — the fallback probe.
    fail_with: int = 0

    def log_message(self, *_args: object) -> None:  # noqa: D102 - quiet
        return

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/requests"):
            self._json({"requests": list(_Handler.seen)})
            return
        if self.path.startswith("/reset"):
            _Handler.seen.clear()
            _Handler.fail_with = 0
            self._json({"ok": True})
            return
        if self.path.startswith("/fail"):
            _Handler.fail_with = 500
            self._json({"ok": True, "failing": True})
            return
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._json({"data": [{"id": "gpt-4o-mini", "object": "model"}]})
            return
        self.send_error(404, "this fake speaks /v1/chat/completions")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        _Handler.seen.append(
            {
                "path": self.path,
                "model": body.get("model"),
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "messages": body.get("messages"),
                "at": time.time(),
            }
        )
        if _Handler.fail_with:
            self._json({"error": {"message": "the mock was told to fail"}}, _Handler.fail_with)
            return
        self._json(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "answered by the cloud mock"},
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
            }
        )


class MockCloud:
    """The fake, as a context manager."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "MockCloud":
        _Handler.seen.clear()
        _Handler.fail_with = 0
        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(_Handler.seen)

    def reset(self) -> None:
        _Handler.seen.clear()
        _Handler.fail_with = 0

    def fail_next(self) -> None:
        _Handler.fail_with = 500

    def __enter__(self) -> "MockCloud":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    fake = MockCloud(args.host, args.port).start()
    print(f"mock cloud on {fake.url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fake.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
