"""The values that must never be written down, and one function that removes them.

## What "never persisted" means here, exactly

`secrets.yaml` is resolved when the configuration loads, so the values are in
this process's memory — that is how a token reaches an HTTP header. What must
never happen is that they reach anywhere *else*: a note, a memory entry, a log
line, a trace on disk, an approval shown on a phone, a report a model wrote.

Those are the paths where a secret stops being a credential and becomes a
liability somebody else can read, and every one of them goes through
:func:`redact`.

This is a narrower claim than "injected at call time", and it is the true one
for this system. `docs/THREAT_MODEL.md` says so rather than implying otherwise:
an attacker with code execution in this process has the secrets, and nothing in
this file pretends differently. What it defends against is the ordinary,
likely, and historically most common leak — a credential copied into a log,
a trace, or an LLM's context by a system that was never thinking about it.

## Why it is value-based rather than key-based

Redacting by key name ("anything called `api_key`") fails the moment a value is
interpolated into a sentence, which is exactly what a model does. Redacting by
VALUE works wherever the value ends up, including inside a URL somebody built
by hand, and needs no cooperation from the code doing the leaking.

Short values are excluded — a four-character secret would redact the word
"true" out of every log line in the system — and the floor is written down
rather than assumed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

#: What replaces a secret. Distinctive on purpose: grepping for it in a trace
#: tells you the redaction ran, which is the difference between "no secrets
#: here" and "no secrets found here".
MASK = "[redacted]"

#: Below this length a value is too common to redact safely — "true", "1234",
#: a two-letter language code. A secret shorter than this is a bad secret, and
#: masking every occurrence of "on" would make logs unreadable while protecting
#: nothing.
MIN_LENGTH = 8

#: Keys whose values are secrets wherever they appear, for the structural pass.
#: This is a belt to the value-matching braces: it catches a NEW secret that
#: nobody registered, in the payloads that carry it most often.
SECRET_KEYS = frozenset({
    "api_key", "apikey", "token", "access_token", "refresh_token", "secret",
    "client_secret", "password", "passwd", "authorization", "auth", "bearer",
    "private_key", "session", "cookie", "credential", "credentials",
    "n8n_api_key", "browser_approval_secret",
})


class SecretRegistry:
    """Every secret value this process knows about, and how to take them out.

    Registered rather than discovered: `configuration.yaml`'s `!secret` tags
    and the environment variables the compose file names are what put values
    in here, so the set is exactly what the operator declared plus what they
    put in `.env`.
    """

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._pattern: re.Pattern[str] | None = None

    def add(self, value: Any) -> bool:
        """Register one value. Returns False for anything too short to mask."""
        text = str(value or "").strip()
        if len(text) < MIN_LENGTH:
            return False
        if text in self._values:
            return True
        self._values.add(text)
        self._pattern = None
        return True

    def add_all(self, values: Iterable[Any]) -> int:
        return sum(1 for value in values if self.add(value))

    def load(self, secrets: dict[str, Any] | None, environment: dict[str, str] | None = None) -> int:
        """Everything from `secrets.yaml`, plus environment values that are keys."""
        count = 0
        for key, value in (secrets or {}).items():
            if isinstance(value, (str, int)) and self.add(value):
                count += 1
            elif isinstance(value, dict):
                count += self.load(value)
        for name, value in (environment or {}).items():
            if any(word in name.lower() for word in ("token", "key", "secret", "password")):
                count += 1 if self.add(value) else 0
        return count

    @property
    def count(self) -> int:
        return len(self._values)

    def _compiled(self) -> re.Pattern[str] | None:
        if self._pattern is None and self._values:
            # Longest first, so a secret that contains another is masked whole
            # rather than leaving a fragment behind.
            ordered = sorted(self._values, key=len, reverse=True)
            self._pattern = re.compile("|".join(re.escape(value) for value in ordered))
        return self._pattern

    def redact(self, value: Any) -> Any:
        """`value` with every known secret replaced, at any depth.

        Strings, dicts, lists and tuples are walked; anything else is returned
        unchanged. Dict KEYS in `SECRET_KEYS` have their values masked whole,
        which is what catches a secret nobody registered.
        """
        if isinstance(value, str):
            pattern = self._compiled()
            return pattern.sub(MASK, value) if pattern else value
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if str(key).lower() in SECRET_KEYS and item not in (None, "", 0, False):
                    out[key] = MASK
                else:
                    out[key] = self.redact(item)
            return out
        if isinstance(value, (list, tuple)):
            redacted = [self.redact(item) for item in value]
            return type(value)(redacted) if isinstance(value, tuple) else redacted
        return value


#: The process-wide registry. One, because the leak paths are process-wide.
REGISTRY = SecretRegistry()


def register(value: Any) -> bool:
    return REGISTRY.add(value)


def register_config(secrets: dict[str, Any] | None, environment: dict[str, str] | None = None) -> int:
    return REGISTRY.load(secrets, environment)


def redact(value: Any) -> Any:
    """Take every known secret out of anything about to be written down."""
    return REGISTRY.redact(value)


class RedactingFilter(logging.Filter):
    """A logging filter that redacts every record before a handler sees it.

    Installed on the root logger, because the leak this defends against is
    somebody logging a config dict at DEBUG — which is nobody's fault and
    happens in every codebase eventually.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if REGISTRY.count:
            try:
                if isinstance(record.msg, str):
                    record.msg = redact(record.msg)
                if record.args:
                    record.args = redact(record.args)
            except Exception:  # pragma: no cover - logging must never raise
                pass
        return True


def install_log_filter(logger: logging.Logger | None = None) -> RedactingFilter:
    """Put the filter on a logger (the root one by default). Idempotent."""
    target = logger or logging.getLogger()
    for existing in target.filters:
        if isinstance(existing, RedactingFilter):
            return existing
    installed = RedactingFilter()
    target.addFilter(installed)
    return installed
