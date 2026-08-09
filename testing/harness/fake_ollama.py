#!/usr/bin/env python3
"""A fake Ollama: enough of the API for jarvis-core, with a scripted brain.

`jarvis/llm/ollama.py` only ever speaks three things, and this speaks all
three back:

    GET  /api/tags      the model list (used by `is_available` and llm.list_models)
    POST /api/chat      NDJSON stream — one JSON object per line, the last
                        flagged `done`, tool calls under
                        `message.tool_calls[].function.{name, arguments}`
    POST /api/generate   (completeness; nothing in Jarvis calls it today)

Nothing here is random and nothing here is timed out of a model: a script maps
a substring of what the *user* said to the exact answers to give, in order, on
successive calls. That is what makes a tool-calling turn testable::

    {
      "rules": [
        {
          "match": "turn on the lab lights",
          "responses": [
            {"tool_calls": [{"name": "turn_on",
                             "arguments": {"entity_id": "light.bed_light"}}]},
            {"say": "Turning on the lab lights, Sir."}
          ]
        }
      ],
      "default": {"say": "Very good, Sir."}
    }

The first `/api/chat` that mentions "turn on the lab lights" asks for the tool
call; jarvis-core runs it and comes back; the second call — which still carries
the same user message — gets the spoken answer. Responses are consumed in
order per rule and the last one repeats forever, so a turn that needs three
rounds does not fall off the end of its script.

Run it standalone, or drive it from a test:

    python3 fake_ollama.py --host 0.0.0.0 --port 11434 --script script.json
    curl -X POST localhost:11434/_control/script -d @script.json
    curl localhost:11434/_control/requests        # every payload it was sent

Stdlib only — no aiohttp, no uvicorn, no FastAPI — so it starts in
milliseconds and has nothing to install. It is deliberately self-contained
(no imports from the rest of this package) so it can be copied to a machine
that has nothing but python3 on it.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_MODELS = [DEFAULT_MODEL, "llama3.2:3b", "nomic-embed-text"]
DEFAULT_REPLY = "Very good, Sir."

#: Ollama's own content type for the NDJSON stream.
NDJSON = "application/x-ndjson"

__all__ = ["FakeOllama", "Script", "main"]


# ---------------------------------------------------------------------------
# the script
# ---------------------------------------------------------------------------
def _text_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _split_deltas(text: str) -> list[str]:
    """Break an answer into word-sized deltas, deterministically.

    A real model streams; a test that asserts on `intent-progress` needs more
    than one delta to assert about. Splitting after each space keeps every
    character (join(parts) == text), which is the property the pipeline's
    reassembled `response_text` depends on.
    """
    if not text:
        return []
    parts = re.findall(r"\S+\s*", text)
    return parts or [text]


class Script:
    """The mapping from "what the user said" to "what the model answers".

    Thread-safe: the HTTP server is threaded, and a test may rewrite the
    script from another thread while a request is in flight.
    """

    def __init__(self, data: Any = None) -> None:
        self._lock = threading.Lock()
        self._served: dict[int, int] = {}
        self.rules: list[dict[str, Any]] = []
        self.default: dict[str, Any] = {"say": DEFAULT_REPLY}
        self.models: list[str] = list(DEFAULT_MODELS)
        self.load(data)

    # --- loading ----------------------------------------------------------
    def load(self, data: Any) -> "Script":
        """Replace the script. Counters reset, so a run is reproducible."""
        data = data or {}
        if not isinstance(data, dict):
            raise ValueError("a script must be a JSON object")
        rules = data.get("rules") or []
        if not isinstance(rules, list):
            raise ValueError("'rules' must be a list")
        cleaned: list[dict[str, Any]] = []
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError(f"rule {index} is not an object")
            responses = rule.get("responses")
            if responses is None and "response" in rule:
                responses = [rule["response"]]
            if responses is None:
                responses = [{key: rule[key] for key in ("say", "chunks", "tool_calls",
                                                         "thinking", "error", "status")
                              if key in rule}]
            if not isinstance(responses, list) or not responses:
                raise ValueError(f"rule {index} has no responses")
            cleaned.append(
                {
                    "name": str(rule.get("name") or rule.get("match") or f"rule-{index}"),
                    "match": str(rule.get("match") or ""),
                    "match_type": str(rule.get("match_type") or "substring"),
                    "scope": str(rule.get("scope") or "user"),
                    "repeat": bool(rule.get("repeat", False)),
                    "responses": responses,
                }
            )
        default = data.get("default")
        models = data.get("models")
        with self._lock:
            self.rules = cleaned
            self.default = default if isinstance(default, dict) else {"say": DEFAULT_REPLY}
            self.models = [str(m) for m in models] if isinstance(models, list) and models \
                else list(DEFAULT_MODELS)
            self._served.clear()
        return self

    def reset(self) -> None:
        with self._lock:
            self._served.clear()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "rules": [dict(rule) for rule in self.rules],
                "default": dict(self.default),
                "models": list(self.models),
                "served": dict(self._served),
            }

    # --- matching ---------------------------------------------------------
    @staticmethod
    def _haystack(payload: dict[str, Any], scope: str) -> str:
        messages = payload.get("messages")
        messages = messages if isinstance(messages, list) else []
        if scope == "all":
            return "\n".join(_text_of(m) for m in messages)
        if scope == "last":
            return _text_of(messages[-1]) if messages else ""
        # "user" — the default. Deliberately not the system prompt: that holds
        # a summary of every exposed entity, so matching against it turns any
        # rule naming a device into a rule that always fires.
        return "\n".join(_text_of(m) for m in messages if m.get("role") == "user")

    @staticmethod
    def _matches(rule: dict[str, Any], haystack: str) -> bool:
        needle = rule["match"]
        if not needle:
            return True
        kind = rule["match_type"]
        if kind == "regex":
            return re.search(needle, haystack, re.IGNORECASE) is not None
        if kind == "exact":
            return haystack.strip().lower() == needle.strip().lower()
        return needle.lower() in haystack.lower()

    def response_for(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """``(rule name, response spec)`` for one ``/api/chat`` payload."""
        with self._lock:
            for index, rule in enumerate(self.rules):
                if not self._matches(rule, self._haystack(payload, rule["scope"])):
                    continue
                responses = rule["responses"]
                served = self._served.get(index, 0)
                self._served[index] = served + 1
                if rule["repeat"]:
                    spec = responses[served % len(responses)]
                else:
                    # The last response repeats: a turn that needs an extra
                    # round must not fall off the end of its script.
                    spec = responses[min(served, len(responses) - 1)]
                return rule["name"], spec if isinstance(spec, dict) else {"say": str(spec)}
            return "default", dict(self.default)

    def model_names(self) -> list[str]:
        with self._lock:
            return list(self.models)


# ---------------------------------------------------------------------------
# NDJSON chunk building
# ---------------------------------------------------------------------------
def _tool_call_parts(spec: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, call in enumerate(spec or []):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        name = function.get("name")
        if not name:
            continue
        part: dict[str, Any] = {
            "function": {
                "name": str(name),
                # Left exactly as scripted: a JSON *string* here is a real
                # shape some models emit, and the client is meant to cope.
                "arguments": function.get("arguments", {}),
            }
        }
        part["id"] = str(call.get("id") or f"call_{index}")
        out.append(part)
    return out


def build_chunks(spec: dict[str, Any], model: str) -> list[dict[str, Any]]:
    """The NDJSON objects one scripted response turns into."""
    chunks: list[dict[str, Any]] = []
    if spec.get("error"):
        return [{"error": str(spec["error"])}]

    thinking = spec.get("thinking")
    if thinking:
        chunks.append(
            {
                "model": model,
                "created_at": "1970-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": "", "thinking": str(thinking)},
                "done": False,
            }
        )

    text = str(spec.get("say") or "")
    pieces = spec.get("chunks")
    if isinstance(pieces, list) and pieces:
        parts = [str(piece) for piece in pieces]
    elif spec.get("stream_words") is False:
        parts = [text] if text else []
    else:
        parts = _split_deltas(text)
    for part in parts:
        chunks.append(
            {
                "model": model,
                "created_at": "1970-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": part},
                "done": False,
            }
        )

    final: dict[str, Any] = {
        "model": model,
        "created_at": "1970-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": str(spec.get("done_reason") or "stop"),
        "total_duration": 1_000_000,
        "eval_count": max(len(parts), 1),
    }
    tool_calls = _tool_call_parts(spec.get("tool_calls"))
    if tool_calls:
        # Ollama usually delivers the whole call on the final chunk.
        final["message"]["tool_calls"] = tool_calls
    chunks.append(final)
    return chunks


def merge_chunks(chunks: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Collapse a stream into the single object ``stream: false`` returns."""
    content = ""
    thinking = ""
    tool_calls: list[dict[str, Any]] = []
    done_reason = "stop"
    for chunk in chunks:
        if chunk.get("error"):
            return dict(chunk)
        message = chunk.get("message") or {}
        content += message.get("content") or ""
        thinking += message.get("thinking") or ""
        tool_calls.extend(message.get("tool_calls") or [])
        done_reason = chunk.get("done_reason") or done_reason
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "model": model,
        "created_at": "1970-01-01T00:00:00Z",
        "message": message,
        "done": True,
        "done_reason": done_reason,
    }


# ---------------------------------------------------------------------------
# the HTTP server
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FakeOllama/1.0"

    # --- plumbing ---------------------------------------------------------
    @property
    def fake(self) -> "FakeOllama":
        return self.server.fake  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.fake.verbose:
            sys.stderr.write("fake-ollama %s\n" % (fmt % args))

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ValueError(str(err)) from err

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream_header(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", NDJSON)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def _end_stream(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # --- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/api/tags", "/api/ps"):
            models = [
                {
                    "name": name,
                    "model": name,
                    "size": 1,
                    "digest": "0" * 64,
                    "modified_at": "1970-01-01T00:00:00Z",
                    "details": {"family": "fake", "parameter_size": "0B"},
                }
                for name in self.fake.script.model_names()
            ]
            self._send_json({"models": models})
            return
        if path == "/api/version":
            self._send_json({"version": "0.0.0-fake"})
            return
        if path == "/_control/health":
            self._send_json(
                {"ok": True, "requests": len(self.fake.requests), "kind": "fake-ollama"}
            )
            return
        if path == "/_control/script":
            self._send_json(self.fake.script.as_dict())
            return
        if path == "/_control/requests":
            self._send_json({"requests": self.fake.recorded()})
            return
        self._send_json({"error": f"not found: {path}"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            payload = self._read_json()
        except ValueError as err:
            self._send_json({"error": f"invalid JSON: {err}"}, 400)
            return

        if path == "/_control/script":
            try:
                self.fake.script.load(payload)
            except ValueError as err:
                self._send_json({"error": str(err)}, 400)
                return
            self._send_json({"ok": True, "rules": len(self.fake.script.rules)})
            return
        if path == "/_control/reset":
            self.fake.reset()
            self._send_json({"ok": True})
            return
        if path in ("/api/chat", "/api/generate"):
            self._chat(payload if isinstance(payload, dict) else {}, generate=path.endswith("generate"))
            return
        self._send_json({"error": f"not found: {path}"}, 404)

    # --- the interesting one ---------------------------------------------
    def _chat(self, payload: dict[str, Any], generate: bool = False) -> None:
        self.fake.refresh_script_file()
        if generate:
            prompt = str(payload.get("prompt") or "")
            payload = {**payload, "messages": [{"role": "user", "content": prompt}]}
        name, spec = self.fake.script.response_for(payload)
        model = str(payload.get("model") or DEFAULT_MODEL)
        self.fake.record(payload, name)

        status = int(spec.get("status") or 200)
        if status >= 400:
            self._send_json({"error": str(spec.get("error") or "scripted failure")}, status)
            return

        chunks = build_chunks(spec, model)
        delay = max(0.0, float(spec.get("delay_ms") or 0) / 1000.0)

        if not payload.get("stream", True):
            self._send_json(merge_chunks(chunks, model))
            return

        self._send_stream_header()
        try:
            for chunk in chunks:
                if delay:
                    time.sleep(delay)
                self._write_chunk((json.dumps(chunk) + "\n").encode("utf-8"))
            self._end_stream()
        except (BrokenPipeError, ConnectionResetError):
            # The client walked away mid-stream — barge-in, a cancelled turn.
            # That is a thing under test, not an error.
            self.close_connection = True


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # A client that hangs up mid-stream must not take the server down.
    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: D102
        kind = sys.exc_info()[0]
        if kind in (BrokenPipeError, ConnectionResetError):
            return
        socketserver.BaseServer.handle_error(self, request, client_address)


class FakeOllama:
    """A scripted Ollama on a real socket.

    Usable in-process::

        fake = FakeOllama(script={"rules": [...]}).start()
        ...                                  # fake.url points at it
        fake.stop()
    """

    def __init__(
        self,
        script: Any = None,
        host: str = "127.0.0.1",
        port: int = 0,
        script_file: str | None = None,
        verbose: bool = False,
        max_requests: int = 500,
    ) -> None:
        self.host = host
        self.script_file = script_file
        self.verbose = verbose
        self.max_requests = max_requests
        self.script = Script(script if script is not None else self._read_file())
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._mtime: float | None = self._file_mtime()
        self._server = _Server((host, int(port)), _Handler)
        self._server.fake = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------
    def start(self) -> "FakeOllama":
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._server.serve_forever, kwargs={"poll_interval": 0.1},
                name="fake-ollama", daemon=True,
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FakeOllama":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # --- introspection ----------------------------------------------------
    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("", "0.0.0.0") else self.host
        return f"http://{host}:{self.port}"

    def record(self, payload: dict[str, Any], rule: str) -> None:
        with self._lock:
            self.requests.append({"rule": rule, "payload": payload, "at": time.time()})
            if len(self.requests) > self.max_requests:
                del self.requests[: len(self.requests) - self.max_requests]

    def recorded(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.requests)

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
        self.script.reset()

    def set_script(self, data: Any) -> None:
        self.script.load(data)

    # --- script file ------------------------------------------------------
    def _file_mtime(self) -> float | None:
        if not self.script_file:
            return None
        try:
            import os

            return os.path.getmtime(self.script_file)
        except OSError:
            return None

    def _read_file(self) -> Any:
        if not self.script_file:
            return None
        try:
            with open(self.script_file, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as err:
            raise SystemExit(f"fake-ollama: cannot read --script {self.script_file}: {err}")

    def refresh_script_file(self) -> None:
        """Re-read ``--script`` if it changed, so a test can rewrite it live."""
        if not self.script_file:
            return
        mtime = self._file_mtime()
        if mtime is None or mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self.script.load(self._read_file())
        except (SystemExit, ValueError) as err:  # a bad edit keeps the old script
            sys.stderr.write(f"fake-ollama: ignoring bad script file ({err})\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A scripted, deterministic fake Ollama.")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=0, help="bind port (0 = pick one)")
    parser.add_argument("--script", default=None, help="JSON script file (re-read when it changes)")
    parser.add_argument("--json-out", default=None, help="write {port, url} here once listening")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)

    fake = FakeOllama(
        host=args.host, port=args.port, script_file=args.script, verbose=args.verbose
    )
    fake.start()

    info = {"kind": "fake-ollama", "host": args.host, "port": fake.port, "url": fake.url}
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
    print(json.dumps(info), flush=True)

    stopping = threading.Event()

    def _stop(*_signal: Any) -> None:
        stopping.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass

    try:
        while not stopping.wait(0.25):
            pass
    finally:
        fake.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
