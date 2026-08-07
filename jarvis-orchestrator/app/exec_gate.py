"""Approval-gated command execution state machine.

Every command the LLM asks to run goes through this gate:

    requested ──approve(secret)──► approved ──executed──► done
        │
        └──deny(secret)──► denied

Invariants (unit-tested in tests/test_exec_gate.py, adversarially):
  * Nothing is ever handed to the sandbox before ``approve`` succeeds.
  * ``approve``/``deny`` require the approval secret (constant-time compare).
    Possession of the ordinary API token is NOT enough.
  * The stored command is verbatim what was requested — the approval prompt
    shown to the human comes from here, never from the model's paraphrase.
  * A request can be approved at most once (no replay).
"""

from __future__ import annotations

import hmac
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock


class GateError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class ExecRequest:
    request_id: str
    command: str
    why: str
    state: str = "requested"  # requested | approved | denied | done | expired
    created: float = field(default_factory=time.monotonic)


class ExecGate:
    def __init__(self, approval_secret: str, ttl_seconds: float = 300.0):
        if not approval_secret:
            raise ValueError("approval secret must be non-empty")
        self._secret = approval_secret
        self._ttl = ttl_seconds
        self._requests: dict[str, ExecRequest] = {}
        self._lock = Lock()

    def _check_secret(self, provided: str | None) -> None:
        if not provided or not hmac.compare_digest(
            provided.encode(), self._secret.encode()
        ):
            raise GateError(403, "invalid approval secret")

    def request(self, command: str, why: str) -> ExecRequest:
        if not command.strip():
            raise GateError(422, "empty command")
        req = ExecRequest(uuid.uuid4().hex, command, why)
        with self._lock:
            self._requests[req.request_id] = req
        return req

    def _get_live(self, request_id: str) -> ExecRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise GateError(404, "unknown request")
        if (
            req.state == "requested"
            and time.monotonic() - req.created > self._ttl
        ):
            req.state = "expired"
        return req

    def approve(self, request_id: str, secret: str | None) -> ExecRequest:
        self._check_secret(secret)
        with self._lock:
            req = self._get_live(request_id)
            if req.state != "requested":
                raise GateError(409, f"request is {req.state}, not approvable")
            req.state = "approved"
            return req

    def deny(self, request_id: str, secret: str | None) -> ExecRequest:
        self._check_secret(secret)
        with self._lock:
            req = self._get_live(request_id)
            if req.state not in ("requested",):
                raise GateError(409, f"request is {req.state}")
            req.state = "denied"
            return req

    def mark_done(self, request_id: str) -> None:
        with self._lock:
            req = self._requests.get(request_id)
            if req is not None and req.state == "approved":
                req.state = "done"

    def is_executable(self, request_id: str) -> bool:
        """The single source of truth the executor path consults."""
        with self._lock:
            req = self._requests.get(request_id)
            return req is not None and req.state == "approved"
