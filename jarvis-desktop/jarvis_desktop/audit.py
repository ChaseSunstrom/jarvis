"""Local, append-only, user-readable audit log.

Every dispatch writes exactly one line — allowed, asked-and-approved,
asked-and-denied, denied outright, unsupported or crashed. If it is not in here,
it did not run.

Deliberately boring: one JSON object per line, no database, no index, so the
user can read it with ``tail -f`` and so a corrupt line costs one entry rather
than the whole history.

Two things are non-obvious and both are on purpose:

* **Redaction happens here and only here.** The consent prompt shows the raw,
  verbatim parameters — it has to, or it is lying about what will run. The log
  is a permanent file, so anything that smells like a credential is masked
  before it lands on disk. Over-redaction is the safe direction: ``country_code``
  losing its value in the log costs nothing, a logged OTP costs a lot.
* **Nothing here can fail a dispatch.** Every operation swallows its own I/O
  errors after recording them on the logger. An audit write must never be the
  reason an action does or does not run.

Mirrors ``android-app/.../automation/audit/{Redactor,AuditLog}.kt``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .policy import ActionTier, Decision

_LOGGER = logging.getLogger(__name__)

__all__ = ["Redactor", "redact_params", "AuditEntry", "AuditLog"]


class Redactor:
    """Decides which parameter keys are secrets and how long a value may be."""

    MASK = "[redacted]"

    #: Longest string value kept verbatim in the log; the rest is elided.
    MAX_VALUE_CHARS = 256

    #: Longest list we enumerate before summarising it.
    MAX_ARRAY_ITEMS = 20

    #: Deepest nesting we walk before giving up.
    MAX_DEPTH = 6

    #: Whole-token matches. Short, generic words go here so that ``pin`` matches
    #: ``pin``, ``sim_pin`` and ``pinCode`` but not ``spinner``.
    SECRET_TOKENS = frozenset(
        {
            "token",
            "password",
            "passwd",
            "pass",
            "passphrase",
            "pin",
            "otp",
            "code",
            "secret",
            "key",
            "apikey",
            "auth",
            "authorization",
            "credential",
            "credentials",
            "cookie",
            "session",
            "seed",
            "mnemonic",
            "cvv",
            "cvc",
            "ssn",
            "iban",
            "account",
            "bearer",
            "signature",
            "sig",
            "nonce",
        }
    )

    #: Substring matches for words long enough that a false positive is
    #: essentially impossible.
    SECRET_SUBSTRINGS = (
        "token",
        "password",
        "passwd",
        "secret",
        "apikey",
        "credential",
        "privatekey",
        "accesskey",
        "authorization",
        "otpcode",
        "pincode",
    )

    @staticmethod
    def tokenize(key: str) -> list[str]:
        """Split a key into lowercase words on ``_ - . / space`` and camelCase
        boundaries: ``apiKey`` -> ``[api, key]``, ``sim_pin`` -> ``[sim, pin]``.
        """
        out: list[str] = []
        current: list[str] = []
        prev_was_lower = False
        for ch in key:
            if not ch.isalnum():
                if current:
                    out.append("".join(current).lower())
                    current = []
                prev_was_lower = False
            elif ch.isupper() and prev_was_lower:
                if current:
                    out.append("".join(current).lower())
                    current = []
                current.append(ch)
                prev_was_lower = False
            else:
                current.append(ch)
                prev_was_lower = ch.islower() or ch.isdigit()
        if current:
            out.append("".join(current).lower())
        return out

    @classmethod
    def is_secret_key(cls, key: str) -> bool:
        """True when a parameter under this key must never be written verbatim."""
        flat = "".join(ch for ch in key.lower() if ch.isalnum())
        if not flat:
            return False
        if any(sub in flat for sub in cls.SECRET_SUBSTRINGS):
            return True
        return any(tok in cls.SECRET_TOKENS for tok in cls.tokenize(key))

    @classmethod
    def truncate(cls, value: str, max_chars: int | None = None) -> str:
        """Keep long free text (message bodies, HTTP payloads) from bloating
        the log."""
        limit = cls.MAX_VALUE_CHARS if max_chars is None else max_chars
        if len(value) <= limit:
            return value
        return f"{value[:limit]}...(+{len(value) - limit} chars)"

    @classmethod
    def redact_string(cls, key: str, value: str) -> str:
        """Mask or shorten one string value according to its key."""
        return cls.MASK if cls.is_secret_key(key) else cls.truncate(value)


def redact_params(params: Any, _depth: int = 0, _key: str = "") -> Any:
    """Walk a params structure and apply :class:`Redactor` to every leaf."""
    if _depth > Redactor.MAX_DEPTH:
        return "[too deep]"
    if isinstance(params, dict):
        out: dict[str, Any] = {}
        for raw_key, value in params.items():
            key = str(raw_key)
            if Redactor.is_secret_key(key):
                out[key] = Redactor.MASK
            else:
                out[key] = redact_params(value, _depth + 1, key)
        return out
    if isinstance(params, (list, tuple)):
        # The key of the containing field applies to every element: a list under
        # `tokens` is a list of tokens.
        if Redactor.is_secret_key(_key):
            return [Redactor.MASK for _ in params[: Redactor.MAX_ARRAY_ITEMS]]
        items = [
            redact_params(v, _depth + 1, _key) for v in params[: Redactor.MAX_ARRAY_ITEMS]
        ]
        if len(params) > Redactor.MAX_ARRAY_ITEMS:
            items.append(f"...(+{len(params) - Redactor.MAX_ARRAY_ITEMS} items)")
        return items
    if isinstance(params, str):
        return Redactor.redact_string(_key, params)
    if isinstance(params, (int, float, bool)) or params is None:
        return params
    # Anything exotic (a Path, an object) is stringified and truncated.
    return Redactor.truncate(str(params))


@dataclass
class AuditEntry:
    """One line of the audit log."""

    action_id: str
    #: The tier we actually enforced (max of local, per-params and requested).
    tier: ActionTier
    decision: Decision
    #: Wire status finally returned: ok | denied | error | unsupported.
    status: str
    ok: bool
    #: RAW params. :meth:`AuditLog.record` redacts before anything touches disk.
    params: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #: Who asked: "server", "user", "trigger", ...
    source: str = "server"
    #: ``command_id`` from the device_command, when there was one.
    command_id: str | None = None
    duration_ms: int = 0
    #: Free-text policy explanation, e.g. "raised by server, policy=ASK".
    note: str | None = None
    #: Wall-clock epoch seconds. Defaults to now.
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        """Serialised form. ``params`` is redacted here and only here."""
        out: dict[str, Any] = {
            "ts": round(self.timestamp, 3),
            "time": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self.timestamp)
            ),
            "action": self.action_id,
            "params": redact_params(self.params or {}),
            "tier": self.tier.name,
            "decision": self.decision.value,
            "status": self.status,
            "ok": self.ok,
            "source": self.source,
            "duration_ms": self.duration_ms,
        }
        if self.error:
            out["error"] = Redactor.truncate(self.error)
        if self.command_id:
            out["command_id"] = self.command_id
        if self.note:
            out["note"] = Redactor.truncate(self.note)
        return out

    @staticmethod
    def from_json(obj: dict[str, Any]) -> "AuditEntry":
        return AuditEntry(
            action_id=str(obj.get("action", "")),
            tier=ActionTier.from_name(obj.get("tier")) or ActionTier.CONFIRM,
            decision=_decision_or_deny(obj.get("decision")),
            status=str(obj.get("status", "")),
            ok=bool(obj.get("ok", False)),
            params=obj.get("params") if isinstance(obj.get("params"), dict) else {},
            error=obj.get("error"),
            source=str(obj.get("source", "server")),
            command_id=obj.get("command_id"),
            duration_ms=int(obj.get("duration_ms", 0) or 0),
            note=obj.get("note"),
            timestamp=float(obj.get("ts", 0.0) or 0.0),
        )


def _decision_or_deny(value: object) -> Decision:
    try:
        return Decision(str(value))
    except ValueError:
        return Decision.DENY


class AuditLog:
    """Append-only JSONL log with size rotation and an entry cap.

    Two independent limits, because they fail in different directions:

    * ``max_bytes`` bounds a single runaway entry stream (a screenshot action
      returning base64 into an error string). When crossed, the live file is
      rotated to ``.1``, ``.1`` to ``.2``, and anything past ``keep_rotations``
      is dropped.
    * ``max_entries`` bounds the *live* file so reading it stays cheap. When
      crossed, the file is compacted in place to the newest ``max_entries``
      lines.
    """

    DEFAULT_MAX_ENTRIES = 5000
    DEFAULT_MAX_BYTES = 8 * 1024 * 1024
    DEFAULT_KEEP_ROTATIONS = 3
    _ROTATE_SLACK = 250

    def __init__(
        self,
        path: str | os.PathLike[str],
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        keep_rotations: int = DEFAULT_KEEP_ROTATIONS,
    ) -> None:
        self.path = Path(path)
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1024, int(max_bytes))
        self.keep_rotations = max(0, int(keep_rotations))
        self._lock = threading.Lock()
        self._line_count = -1  # -1 = not counted yet

    # --- writing ------------------------------------------------------------

    def record(self, entry: AuditEntry) -> None:
        """Append one entry. Never raises."""
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(entry.to_json(), ensure_ascii=False, default=str)
                new_file = not self.path.exists()
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                if new_file:
                    try:
                        os.chmod(self.path, 0o600)
                    except OSError:
                        pass
                if self._line_count < 0:
                    self._line_count = self._count_lines()
                else:
                    self._line_count += 1
                self._maybe_rotate_locked()
            except Exception:  # noqa: BLE001 - bookkeeping must not break actions
                _LOGGER.warning("audit write failed for %s", entry.action_id, exc_info=True)

    async def record_async(self, entry: AuditEntry) -> None:
        """Off-loop wrapper for the dispatcher."""
        import asyncio

        await asyncio.to_thread(self.record, entry)

    # --- reading ------------------------------------------------------------

    def read(self, limit: int = 200, newest_first: bool = True) -> list[AuditEntry]:
        """Newest first by default — that is what a log view wants."""
        with self._lock:
            entries = list(self._iter_entries())
        if newest_first:
            entries.reverse()
        return entries[:limit] if limit and limit > 0 else entries

    def read_json(self, limit: int = 200) -> list[dict[str, Any]]:
        return [e.to_json() for e in self.read(limit)]

    def count(self) -> int:
        with self._lock:
            if self._line_count < 0:
                self._line_count = self._count_lines()
            return self._line_count

    def clear(self) -> None:
        """User-initiated wipe of the live file (rotations are left alone)."""
        with self._lock:
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError:
                _LOGGER.warning("audit clear failed", exc_info=True)
            self._line_count = 0

    # --- internals ----------------------------------------------------------

    def _iter_entries(self) -> Iterator[AuditEntry]:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue  # one corrupt line costs one entry
                    if isinstance(obj, dict):
                        yield AuditEntry.from_json(obj)
        except OSError:
            _LOGGER.warning("audit read failed", exc_info=True)

    def _count_lines(self) -> int:
        if not self.path.exists():
            return 0
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0

    def _maybe_rotate_locked(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size > self.max_bytes:
            self._rotate_locked()
            return
        if self._line_count >= self.max_entries + self._ROTATE_SLACK:
            self._compact_locked()

    def _rotate_locked(self) -> None:
        """``audit.jsonl`` -> ``.1`` -> ``.2`` ...; drop past ``keep_rotations``."""
        try:
            if self.keep_rotations == 0:
                self.path.unlink(missing_ok=True)
                self._line_count = 0
                return
            oldest = self.path.with_suffix(self.path.suffix + f".{self.keep_rotations}")
            oldest.unlink(missing_ok=True)
            for index in range(self.keep_rotations - 1, 0, -1):
                src = self.path.with_suffix(self.path.suffix + f".{index}")
                if src.exists():
                    src.replace(self.path.with_suffix(self.path.suffix + f".{index + 1}"))
            self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
            self._line_count = 0
        except OSError:
            _LOGGER.warning("audit rotate failed", exc_info=True)

    def _compact_locked(self) -> None:
        """Keep the newest ``max_entries`` lines, in place."""
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = [line for line in fh if line.strip()]
            keep = lines[-self.max_entries :]
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                fh.writelines(keep)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.path)
            self._line_count = len(keep)
        except OSError:
            _LOGGER.warning("audit compact failed", exc_info=True)


def summarize(entries: Iterable[AuditEntry]) -> dict[str, int]:
    """Counts by status, for ``python -m jarvis_desktop audit --stats``."""
    out: dict[str, int] = {}
    for entry in entries:
        out[entry.status] = out.get(entry.status, 0) + 1
    return out
