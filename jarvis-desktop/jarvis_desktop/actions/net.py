"""``http_request`` — fetch a URL, guarded against SSRF.

Tier 2 for GET/HEAD (a read), Tier 3 for anything that writes to someone else's
system — a POST is "a web action that submits a form", which the shared policy
puts squarely in CONFIRM. :meth:`HttpRequest.tier_for` raises it; nothing can
lower it.

Only :mod:`urllib` is used, so there is no third-party HTTP dependency. Redirects
are followed manually, at most three deep, and **every hop is re-checked**
against :mod:`.ssrf` — an open redirect to ``http://169.254.169.254/`` is the
whole reason this is not left to ``urllib``'s own redirect handler.
"""

from __future__ import annotations

import json as jsonlib
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..policy import ActionTier
from . import ssrf
from .base import Action, ActionContext, ActionResult

__all__ = ["HttpRequest"]

MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 256 * 1024
HARD_MAX_BYTES = 2 * 1024 * 1024
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")

#: Headers the caller may not set: they either authenticate as someone else or
#: rewrite the request's identity in a way the guard cannot see.
_BLOCKED_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "cookie"}

#: Header names that carry a credential. They are allowed on the *first* hop —
#: talking to an authenticated API is the point — but they are dropped the
#: moment a redirect crosses to another origin, because an open redirect on a
#: site you hold a token for should not hand that token to whoever the redirect
#: names. urllib's own redirect handler does not do this, which is one more
#: reason redirects are followed by hand here.
_CREDENTIAL_HEADER = re.compile(
    r"(authorization|authentication|token|secret|api[-_]?key|password|"
    r"credential|cookie|session|bearer)",
    re.IGNORECASE,
)


def _origin(url: str) -> tuple[str, str, int]:
    """``(scheme, host, port)`` — what "same origin" means for header stripping."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        port = -1
    return scheme, host, port


def _drop_credentials(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if not _CREDENTIAL_HEADER.search(k)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into responses so the caller can re-check the target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102, ANN001
        return None


class HttpRequest(Action):
    id = "http_request"
    tier = ActionTier.NOTIFY
    description = "Fetch a URL over http(s) and return the response body."
    params_schema = {
        "url": "string: an http:// or https:// URL",
        "method": "string (optional): GET | HEAD | POST | PUT | PATCH | DELETE (default GET)",
        "headers": "object (optional): header name -> value",
        "body": "string (optional): request body for POST/PUT/PATCH",
        "json": "object (optional): sent as a JSON body, sets Content-Type",
        "max_bytes": "int (optional): stop reading after this many bytes",
        "timeout_s": "number (optional): give up after this long (default 20)",
    }
    capability = "http"
    timeout_s = 60.0

    def tier_for(self, params: Any) -> ActionTier:
        """A GET reads; a POST/PUT/PATCH/DELETE writes to someone else's system.
        Raise only."""
        method = (self.str_param(params, "method") or "GET").upper()
        return ActionTier.NOTIFY if method in ("GET", "HEAD") else ActionTier.CONFIRM

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        method = (self.str_param(params, "method") or "GET").upper()
        if method not in METHODS:
            return ActionResult.failed(f"method must be one of {', '.join(METHODS)}")
        url = self.str_param(params, "url")
        if not url:
            return ActionResult.failed("url is required")

        max_bytes = max(1, min(self.int_param(params, "max_bytes", DEFAULT_MAX_BYTES), HARD_MAX_BYTES))
        timeout = max(1.0, min(self.float_param(params, "timeout_s", 20.0), 55.0))
        allowed_hosts = ctx.allowed_hosts

        headers: dict[str, str] = {"User-Agent": "jarvis-desktop/0.1 (+local)"}
        raw_headers = params.get("headers")
        if isinstance(raw_headers, dict):
            for name, value in raw_headers.items():
                key = str(name).strip()
                if not key or key.lower() in _BLOCKED_HEADERS:
                    continue
                if any(ch in str(value) for ch in "\r\n"):
                    return ActionResult.failed(f"header {key} contains a newline")
                headers[key] = str(value)

        body: bytes | None = None
        if isinstance(params.get("json"), (dict, list)):
            body = jsonlib.dumps(params["json"]).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(params.get("body"), str):
            body = params["body"].encode("utf-8")
        if body is not None and method in ("GET", "HEAD"):
            return ActionResult.failed(f"{method} cannot carry a body")

        opener = urllib.request.build_opener(_NoRedirect)
        current = url
        hops: list[str] = []

        for _ in range(MAX_REDIRECTS + 1):
            check = ssrf.resolve_and_check(current, allowed_hosts)
            if not check.allowed:
                return ActionResult.denied(f"refused: {check.reason}")
            hops.append(current)

            request = urllib.request.Request(current, data=body, method=method)
            for name, value in headers.items():
                request.add_header(name, value)
            try:
                with opener.open(request, timeout=timeout) as response:
                    raw = response.read(max_bytes + 1)
                    status = response.status
                    resp_headers = {k.lower(): v for k, v in response.headers.items()}
            except urllib.error.HTTPError as exc:
                status = exc.code
                resp_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
                try:
                    raw = exc.read(max_bytes + 1)
                except Exception:  # noqa: BLE001
                    raw = b""
                if status in (301, 302, 303, 307, 308) and resp_headers.get("location"):
                    target = urllib.parse.urljoin(current, resp_headers["location"])
                    # Redirect targets get the full guard again, which is the
                    # point: an open redirect must not become an SSRF. And a
                    # credential for one origin is not a credential for another.
                    if _origin(target) != _origin(current):
                        headers = _drop_credentials(headers)
                    current = target
                    if method == "POST" and status in (301, 302, 303):
                        method, body = "GET", None
                    continue
                return self._respond(status, resp_headers, raw, max_bytes, hops, method)
            except urllib.error.URLError as exc:
                return ActionResult.failed(f"request failed: {exc.reason}")
            except (TimeoutError, OSError) as exc:
                return ActionResult.failed(f"request failed: {exc}")

            if status in (301, 302, 303, 307, 308) and resp_headers.get("location"):
                target = urllib.parse.urljoin(current, resp_headers["location"])
                if _origin(target) != _origin(current):
                    headers = _drop_credentials(headers)
                current = target
                if method == "POST" and status in (301, 302, 303):
                    method, body = "GET", None
                continue
            return self._respond(status, resp_headers, raw, max_bytes, hops, method)

        return ActionResult.failed(f"too many redirects (more than {MAX_REDIRECTS})")

    @staticmethod
    def _respond(
        status: int,
        headers: dict[str, str],
        raw: bytes,
        max_bytes: int,
        hops: list[str],
        method: str,
    ) -> ActionResult:
        truncated = len(raw) > max_bytes
        body = raw[:max_bytes]
        text = body.decode("utf-8", errors="replace")
        payload: dict[str, Any] = {
            "status": status,
            "url": hops[-1],
            "redirects": hops[:-1],
            "method": method,
            "headers": {
                k: v
                for k, v in headers.items()
                if k in ("content-type", "content-length", "etag", "last-modified", "server")
            },
            "body": text,
            "truncated": truncated,
        }
        content_type = headers.get("content-type", "")
        if "json" in content_type:
            try:
                payload["json"] = jsonlib.loads(text)
            except ValueError:
                pass
        # A fetched page is the canonical untrusted input. Flagged all the way
        # back to the server, which must not let it drive an action on its own.
        return ActionResult.untrusted(payload)
