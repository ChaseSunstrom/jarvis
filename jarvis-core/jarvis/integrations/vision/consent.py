"""Consent and the audit trail — the part of `vision` that is not about pixels.

A camera is different from a temperature sensor and the difference is not
technical. "The assistant can see" is a sentence people need to be able to
check, so this module holds two things:

**The policy.** Every camera carries ``consent: always | ask | never``, and it
is enforced here, before a single byte is fetched. ``never`` refuses. ``ask``
puts the question on whichever device the user is at through ``companion.ask``
and only an explicit yes proceeds — silence, a timeout, an unparseable answer,
a queued message nobody has seen, or no companion channel at all all deny.
``always`` proceeds without asking, and is still audited.

The check happening *before* the fetch is the whole point. A denial that
arrives after the frame is already in memory is not a denial, it is an
apology.

**The trail.** Every look, allowed or denied, is recorded: which camera, when,
why, who asked, what was decided. The trail deliberately does **not** store
the frame or the description — a privacy log that accumulates a transcript of
everything the cameras saw is a worse artefact than the thing it audits.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .fence import sanitize_untrusted

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)
_AUDIT = logging.getLogger("jarvis.vision.audit")

# --- policy ----------------------------------------------------------------
CONSENT_ALWAYS = "always"
CONSENT_ASK = "ask"
CONSENT_NEVER = "never"
CONSENT_MODES = (CONSENT_ALWAYS, CONSENT_ASK, CONSENT_NEVER)

#: The default is `ask`, and it is the default because the alternative is a
#: camera that answers questions about your house without telling you.
DEFAULT_CONSENT = CONSENT_ASK

ALLOW_OPTION = "allow"
DENY_OPTION = "deny"

#: The only answers that count as "yes, look". Everything else — including
#: silence, "maybe", "in a minute", and anything a model might phrase on the
#: user's behalf — denies. Deliberately short.
AFFIRMATIVE = frozenset(
    {"allow", "allowed", "yes", "y", "ok", "okay", "sure", "approve", "approved", "confirm"}
)

DEFAULT_ASK_TIMEOUT = 60.0
MAX_REASON_CHARS = 300
DEFAULT_TRAIL_SIZE = 200

# --- decisions -------------------------------------------------------------
POLICY_ALWAYS = "policy_always"
POLICY_NEVER = "policy_never"
USER_APPROVED = "user_approved"
USER_DENIED = "user_denied"
USER_SILENT = "user_silent"
NO_CHANNEL = "no_channel"
RATE_LIMITED = "rate_limited"
UNKNOWN_CAMERA = "unknown_camera"

_DENIAL_TEXT = {
    POLICY_NEVER: (
        "This camera is configured `consent: never`. Jarvis will not look "
        "through it, and no request was made to it. Change the camera's "
        "consent setting in configuration.yaml if that is not what you want."
    ),
    USER_DENIED: (
        "The user said no, so no frame was fetched. Do not retry and do not "
        "ask again in different words."
    ),
    USER_SILENT: (
        "Nobody answered the request to look, so no frame was fetched. "
        "Silence is not consent."
    ),
    NO_CHANNEL: (
        "There was no way to ask the user for permission (no device is "
        "reachable), so no frame was fetched. A camera is never looked "
        "through unattended when its consent policy is `ask`."
    ),
    RATE_LIMITED: (
        "This camera was looked at too recently. Vision calls are rate "
        "limited per camera; wait and try again."
    ),
    UNKNOWN_CAMERA: "No such camera.",
}


def normalise_consent(value: Any) -> str:
    """Coerce a YAML consent value, defaulting to the safe one.

    An unrecognised value is *not* an error that disables the camera — it
    falls back to ``ask``, which is the setting that cannot surprise anyone.
    """
    text = str(value or "").strip().lower()
    if text in CONSENT_MODES:
        return text
    if text:
        _LOGGER.warning(
            "vision: unknown consent %r; using %r", value, DEFAULT_CONSENT
        )
    return DEFAULT_CONSENT


def is_affirmative(answer: Any) -> bool:
    """Strict. Only a recognised yes is a yes; everything else denies."""
    if not isinstance(answer, str):
        return False
    return answer.strip().strip(".!").lower() in AFFIRMATIVE


def clean_reason(value: Any) -> str:
    """A reason string safe to show a human and to store.

    It may have been written by the model, which may have read a web page, so
    it is sanitised for fence markers and cut short before it goes anywhere.
    """
    text = " ".join(str(value or "").split())
    return sanitize_untrusted(text)[:MAX_REASON_CHARS]


@dataclass(frozen=True)
class Decision:
    """The answer to "may Jarvis look through this camera, right now?"."""

    allowed: bool
    decision: str
    consent: str = ""
    asked: bool = False

    @property
    def message(self) -> str:
        return _DENIAL_TEXT.get(self.decision, "Refused.")


# --- audit -----------------------------------------------------------------
@dataclass
class LookRecord:
    """One entry in the trail. No frame, no description — on purpose."""

    camera: str
    entity_id: str = ""
    action: str = "look"          # look | describe_change | snapshot
    reason: str = ""
    requester: str = "unknown"
    consent: str = ""
    decision: str = ""
    allowed: bool = False
    outcome: str = "pending"      # pending | ok | denied | camera_error | model_error
    error: str = ""
    at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "camera": self.camera,
            "entity_id": self.entity_id,
            "action": self.action,
            "reason": self.reason,
            "requester": self.requester,
            "consent": self.consent,
            "decision": self.decision,
            "allowed": self.allowed,
            "outcome": self.outcome,
            "error": self.error,
        }


class AuditTrail:
    """The last N looks, newest last. In memory, bounded, never on disk."""

    def __init__(self, size: int = DEFAULT_TRAIL_SIZE) -> None:
        self._records: deque[LookRecord] = deque(maxlen=max(1, int(size)))

    def add(self, record: LookRecord) -> LookRecord:
        self._records.append(record)
        _AUDIT.info(
            "vision %s camera=%s allowed=%s decision=%s requester=%s reason=%s",
            record.action, record.camera, record.allowed, record.decision,
            record.requester, record.reason or "(none given)",
        )
        return record

    def all(self) -> list[LookRecord]:
        return list(self._records)

    def as_dicts(self, limit: int | None = None, camera: str | None = None) -> list[dict[str, Any]]:
        records = self.all()
        if camera:
            wanted = str(camera).strip().lower()
            records = [
                r for r in records
                if wanted in (r.camera.lower(), r.entity_id.lower())
            ]
        records.reverse()  # newest first is what a reviewer wants
        if limit is not None and limit >= 0:
            records = records[:limit]
        return [r.as_dict() for r in records]

    def __len__(self) -> int:
        return len(self._records)


# --- the broker ------------------------------------------------------------
def consent_question(camera_name: str, reason: str) -> str:
    """The sentence the human sees. Names the camera and says why."""
    why = clean_reason(reason) or "no reason given"
    return (
        f"Jarvis wants to look at the {sanitize_untrusted(camera_name)} "
        f"camera: {why}\n\n"
        f"Nothing has been fetched yet. Reply {ALLOW_OPTION!r} to allow one "
        f"look, anything else to refuse."
    )


class ConsentBroker:
    """Turns a camera's policy plus the user's answer into one Decision."""

    def __init__(self, jarvis: "Jarvis", timeout: float = DEFAULT_ASK_TIMEOUT) -> None:
        self.jarvis = jarvis
        self.timeout = float(timeout)

    async def authorize(self, camera_name: str, consent: str, reason: str) -> Decision:
        consent = normalise_consent(consent)

        if consent == CONSENT_NEVER:
            return Decision(False, POLICY_NEVER, consent)
        if consent == CONSENT_ALWAYS:
            return Decision(True, POLICY_ALWAYS, consent)

        answer = await self._ask(consent_question(camera_name, reason))
        status = str(answer.get("status") or "unknown")
        if status == "answered" and is_affirmative(answer.get("answer")):
            return Decision(True, USER_APPROVED, consent, asked=True)
        if status == "answered":
            return Decision(False, USER_DENIED, consent, asked=True)
        if status == "timeout":
            return Decision(False, USER_SILENT, consent, asked=True)
        # queued, undeliverable, unavailable, or a shape we do not recognise:
        # there is no path where "we could not ask" means "go ahead".
        return Decision(False, NO_CHANNEL, consent, asked=True)

    async def _ask(self, question: str) -> dict[str, Any]:
        if not self.jarvis.services.has_service("companion", "ask"):
            return {"status": "unavailable"}
        try:
            result = await self.jarvis.services.async_call(
                "companion",
                "ask",
                {
                    "question": question,
                    "options": [ALLOW_OPTION, DENY_OPTION],
                    "importance": "high",
                    "timeout": self.timeout,
                },
                blocking=True,
                return_response=True,
            )
        except Exception:
            # A broken companion channel denies. It never proceeds.
            _LOGGER.exception("vision: companion.ask failed; treating as a denial")
            return {"status": "unavailable"}
        return result if isinstance(result, dict) else {"status": "unknown"}


__all__ = [
    "AFFIRMATIVE",
    "ALLOW_OPTION",
    "CONSENT_ALWAYS",
    "CONSENT_ASK",
    "CONSENT_MODES",
    "CONSENT_NEVER",
    "DEFAULT_CONSENT",
    "DENY_OPTION",
    "AuditTrail",
    "ConsentBroker",
    "Decision",
    "LookRecord",
    "NO_CHANNEL",
    "POLICY_ALWAYS",
    "POLICY_NEVER",
    "RATE_LIMITED",
    "UNKNOWN_CAMERA",
    "USER_APPROVED",
    "USER_DENIED",
    "USER_SILENT",
    "clean_reason",
    "consent_question",
    "is_affirmative",
    "normalise_consent",
]
