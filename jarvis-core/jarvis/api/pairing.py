"""Pairing a phone: a short-lived code, exchanged once for a token.

Typing a forty-character token into a phone is the worst moment of setting
Jarvis up, and the usual shortcut — put the token in a QR code — is worse than
typing it. A QR on a screen can be photographed from across a room, ends up in
whatever screenshot or shared window captured it, and lives as long as the
token does. A credential in a picture is a credential in every copy of that
picture.

So the QR carries a **code**, not a token:

* the console asks for a code (authenticated, because only somebody who is
  already in may invite a new device);
* the code goes on screen, in a QR, next to the server's address;
* the phone scans it and exchanges it for a real token over HTTP.

The exchange is the only unauthenticated write in the API, which is what the
rules below exist for:

**Short.** :data:`CODE_TTL` is five minutes. A photograph of a screen is
useless a few minutes later, which is the whole difference from a QR with a
token in it.

**Single use.** The code is removed before the token is minted, so two devices
racing the same code produce exactly one token and one clear refusal — the same
shape as `ToolRegistry.approve_request`.

**Unguessable, and compared in constant time.** ``secrets.token_urlsafe(24)``
is 192 bits. The comparison is still :func:`hmac.compare_digest`, because a
timing oracle on the one endpoint that hands out credentials is not a thing to
leave to the arithmetic being infeasible anyway.

**Few.** :data:`MAX_OUTSTANDING` caps how many codes can be alive at once, so
an authenticated client cannot make the store grow without bound by asking for
codes in a loop.

**Slow to attack.** Claims are rate limited per code-shaped attempt window;
after :data:`MAX_ATTEMPTS` failures in :data:`ATTEMPT_WINDOW` the endpoint stops
answering with anything useful at all, so a scripted sweep gets nowhere even if
the entropy argument were somehow wrong.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

DATA_PAIRING = "pairing_codes"

#: How long a code on a screen is worth anything.
CODE_TTL = 300.0

#: Codes alive at once. A person pairs one phone at a time.
MAX_OUTSTANDING = 8

#: Failed claims tolerated inside [ATTEMPT_WINDOW] before the door shuts.
MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 300.0

#: What a device is called when it does not say.
DEFAULT_DEVICE_NAME = "Paired device"
MAX_NAME_CHARS = 60


class PairingError(Exception):
    """A claim that will not be honoured. The message is shown to the user."""


@dataclass
class _Code:
    code: str
    created: float
    expires_at: float


@dataclass
class PairingCodes:
    """Live pairing codes, and the attempt counter that guards them."""

    codes: dict[str, _Code] = field(default_factory=dict)
    failures: list[float] = field(default_factory=list)

    # --- issuing ------------------------------------------------------------
    def issue(self, now: float | None = None) -> _Code:
        moment = time.time() if now is None else now
        self.purge(moment)
        if len(self.codes) >= MAX_OUTSTANDING:
            # Drop the oldest rather than refuse: somebody who opened the
            # pairing panel eight times and walked away should not be unable to
            # pair on the ninth.
            oldest = min(self.codes.values(), key=lambda c: c.created)
            del self.codes[oldest.code]
        entry = _Code(
            code=secrets.token_urlsafe(24),
            created=moment,
            expires_at=moment + CODE_TTL,
        )
        self.codes[entry.code] = entry
        return entry

    def purge(self, now: float | None = None) -> int:
        moment = time.time() if now is None else now
        stale = [c for c, entry in self.codes.items() if entry.expires_at <= moment]
        for code in stale:
            del self.codes[code]
        self.failures = [at for at in self.failures if moment - at < ATTEMPT_WINDOW]
        return len(stale)

    # --- claiming -----------------------------------------------------------
    def claim(self, offered: str, now: float | None = None) -> _Code:
        """Spend a code, or raise. Removes it BEFORE returning it.

        The removal is what makes two devices racing the same code produce one
        token: whichever call reaches the pop first is the only one that can
        continue, exactly as a single-use approval works.
        """
        moment = time.time() if now is None else now
        self.purge(moment)
        if len(self.failures) >= MAX_ATTEMPTS:
            raise PairingError(
                "Too many failed pairing attempts. Wait a few minutes, then "
                "generate a new code."
            )
        candidate = str(offered or "")
        # Constant-time against every live code, and the loop does not stop
        # early on a match, so the time taken says nothing about which one it
        # was or how far down the list.
        found: _Code | None = None
        for code, entry in self.codes.items():
            if hmac.compare_digest(code, candidate):
                found = entry
        if found is None:
            self.failures.append(moment)
            raise PairingError("That pairing code is not valid, or it has expired.")
        del self.codes[found.code]
        return found


def get_codes(jarvis: "Jarvis") -> PairingCodes:
    store = jarvis.data.get(DATA_PAIRING)
    if not isinstance(store, PairingCodes):
        store = PairingCodes()
        jarvis.data[DATA_PAIRING] = store
    return store


async def async_issue(jarvis: "Jarvis") -> dict[str, Any]:
    """Mint a pairing code for the console to draw."""
    entry = get_codes(jarvis).issue()
    _LOGGER.info("Issued a pairing code, valid for %.0fs", CODE_TTL)
    return {
        "code": entry.code,
        "expires_at": entry.expires_at,
        "ttl": CODE_TTL,
    }


async def async_claim(jarvis: "Jarvis", payload: dict[str, Any]) -> dict[str, Any]:
    """Exchange a code for a real token. Unauthenticated, and single use."""
    from ..auth import get_auth

    auth = get_auth(jarvis)
    if auth is None:
        # No token store means no way to mint one. Refusing here rather than
        # letting an AttributeError become a 500 keeps the failure legible on
        # the one endpoint people hit while setting the thing up.
        raise PairingError("This server has no token store, so it cannot pair a device.")
    # The code is spent BEFORE anything else can fail, which is what decides a
    # race between two devices holding the same photograph of the same screen.
    entry = get_codes(jarvis).claim(payload.get("code"))
    name = str(payload.get("name") or DEFAULT_DEVICE_NAME).strip()[:MAX_NAME_CHARS]
    info, secret = await auth.create_token(name or DEFAULT_DEVICE_NAME)
    _LOGGER.info("Paired a new device as %r (token %s)", info.name, info.id)
    return {
        # The only time this value exists anywhere. It is not stored, not
        # logged, and not recoverable — the same contract as every other token
        # this system mints.
        "token": secret,
        "token_id": info.id,
        "name": info.name,
        "paired_at": entry.created,
    }
