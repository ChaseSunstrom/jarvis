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

**Slow to attack, per caller.** After :data:`MAX_ATTEMPTS` failures in
:data:`ATTEMPT_WINDOW` a caller stops being answered usefully, so a scripted
sweep gets nowhere even if the entropy argument were somehow wrong.

The counter is **per client**, and that is not a refinement — a global one is a
denial of service. Anybody who can reach the endpoint could fail ten claims and
lock pairing for everybody, then keep it locked by failing one more every few
minutes. The operator would see "too many failed attempts" on a phone they had
never paired. Keyed by caller, an attacker locks out only themselves.

**Minting needs a second secret the relay does not hold.**
:data:`ENV_PAIRING_SECRET` is the one rule here that is not obvious, and
leaving it out is a real escalation rather than a missing nicety.

jarvis-web's ``/ws`` relay attaches the server-held admin token to whatever
connects, and its origin guard deliberately admits a request with no ``Origin``
header, because that is what a non-browser client looks like. So a script with
nothing but transient reach to the console's port is already an authenticated
API client: without this, it could mint a code and immediately claim it, and
walk away with a permanent token. Reach for as long as the script runs becomes
access forever.

The secret is typed by the operator into the console panel and travels with the
mint request; the relay never stores it.

It is generated on first run and kept next to the tokens in
``<config>/.storage/auth.json`` — see :meth:`jarvis.auth.AuthManager
.async_ensure_pairing_secret` — because requiring the operator to invent one
and set an environment variable before any phone can be added meant pairing did
not work at all on a fresh install. Generating it locally keeps the property
that matters: the value lives only in this machine's config directory, so the
relay still does not hold it. ``JARVIS_PAIRING_SECRET`` wins when set. With
neither — no token store to generate into — minting stays disabled and every
surface says so, fail closed, the same direction as ``require_token``.

**A browser may not claim.** A claim carrying an ``Origin`` header is refused
outright. Browsers always send one on a cross-origin POST and phones never do,
so this costs the real client nothing and removes the hostile-web-page attacker
from the one unauthenticated write in the API.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..auth import ENV_PAIRING_SECRET, get_auth

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

DATA_PAIRING = "pairing_codes"

# ENV_PAIRING_SECRET is re-exported from `jarvis.auth` rather than defined
# here: that module mints and persists the secret, and one constant with two
# definitions is one definition too many.

#: A secret shorter than this is a typo or a placeholder, not a secret.
MIN_SECRET_CHARS = 8

#: How long a code on a screen is worth anything.
CODE_TTL = 300.0

#: Codes alive at once. A person pairs one phone at a time.
MAX_OUTSTANDING = 8

#: Failed claims tolerated inside [ATTEMPT_WINDOW], per caller, before the
#: door shuts on that caller.
MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 300.0

#: Callers tracked at once. A spoofed source address cannot grow the failure
#: map without bound; past this the oldest entry is dropped, which at worst
#: forgives an attacker who was already being refused.
MAX_TRACKED_CLIENTS = 256

#: Used when the caller cannot be identified, so those attempts share one
#: bucket rather than each getting a fresh allowance.
UNKNOWN_CLIENT = "unknown"

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
    #: caller -> the times it got a claim wrong, inside the window.
    failures: dict[str, list[float]] = field(default_factory=dict)

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
        for client in list(self.failures):
            kept = [at for at in self.failures[client] if moment - at < ATTEMPT_WINDOW]
            if kept:
                self.failures[client] = kept
            else:
                del self.failures[client]
        return len(stale)

    # --- claiming -----------------------------------------------------------
    def claim(
        self, offered: str, now: float | None = None, client: str | None = None
    ) -> _Code:
        """Spend a code, or raise. Removes it BEFORE returning it.

        The removal is what makes two devices racing the same code produce one
        token: whichever call reaches the pop first is the only one that can
        continue, exactly as a single-use approval works.

        [client] identifies the caller for rate limiting. It is only ever used
        as a bucket key — nothing is decided by it, so a spoofed value can win
        an attacker a fresh allowance and nothing else, which is why the
        entropy of the code is what the security actually rests on.
        """
        moment = time.time() if now is None else now
        self.purge(moment)
        who = (client or UNKNOWN_CLIENT).strip()[:64] or UNKNOWN_CLIENT
        if len(self.failures.get(who, ())) >= MAX_ATTEMPTS:
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
            if len(self.failures) >= MAX_TRACKED_CLIENTS and who not in self.failures:
                # Oldest by its most recent failure. Dropping it forgives a
                # caller that was already being refused, which is a far better
                # failure than an unbounded map keyed by a spoofable value.
                oldest = min(self.failures, key=lambda c: self.failures[c][-1])
                del self.failures[oldest]
            self.failures.setdefault(who, []).append(moment)
            raise PairingError("That pairing code is not valid, or it has expired.")
        del self.codes[found.code]
        return found


def get_codes(jarvis: "Jarvis") -> PairingCodes:
    store = jarvis.data.get(DATA_PAIRING)
    if not isinstance(store, PairingCodes):
        store = PairingCodes()
        jarvis.data[DATA_PAIRING] = store
    return store


def configured_secret(jarvis: "Jarvis | None" = None) -> str:
    """The pairing secret in force, or empty when pairing is switched off.

    The environment wins, then the one generated on first run and kept in the
    auth store. Passing [jarvis] is what reaches the stored value; without it
    only the environment is visible, which is all a caller with no box to hand
    can honestly report.

    This is the in-process accessor for an HTTP layer to read. It authenticates
    nobody: anything that serves the value must gate it behind the operator's
    own credential, because handing it to every holder of an API token gives
    away precisely the second factor this module exists to keep separate.
    """
    import os

    auth = get_auth(jarvis) if jarvis is not None else None
    if auth is not None:
        # Precedence lives in AuthManager, in one place. Re-deciding it here
        # would be two rules to keep in step, and the one that drifted would
        # decide which secret is actually accepted.
        return auth.pairing_secret
    return os.environ.get(ENV_PAIRING_SECRET, "").strip()


def check_secret(offered: Any, jarvis: "Jarvis | None" = None) -> None:
    """Raise unless [offered] is the configured pairing secret.

    Fails closed twice over: an unset secret refuses everything rather than
    accepting anything, and a wrong one is compared in constant time.
    """
    configured = configured_secret(jarvis)
    if not configured:
        raise PairingError(
            "Pairing is switched off on this server: it has no store to keep a "
            f"generated pairing secret in. Set {ENV_PAIRING_SECRET} where "
            "jarvis-core runs, and restart it."
        )
    if len(configured) < MIN_SECRET_CHARS:
        raise PairingError(
            f"{ENV_PAIRING_SECRET} is too short to be a secret. Use at least "
            f"{MIN_SECRET_CHARS} characters."
        )
    if not hmac.compare_digest(configured, str(offered or "")):
        raise PairingError("That pairing secret is not correct.")


async def async_issue(jarvis: "Jarvis", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mint a pairing code for the console to draw.

    Guarded by the pairing secret rather than only by the API token, because
    the console's relay hands the API token to anything that connects to it.
    """
    check_secret((payload or {}).get("secret"), jarvis)
    entry = get_codes(jarvis).issue()
    _LOGGER.info("Issued a pairing code, valid for %.0fs", CODE_TTL)
    return {
        "code": entry.code,
        "expires_at": entry.expires_at,
        "ttl": CODE_TTL,
    }


async def async_claim(
    jarvis: "Jarvis", payload: dict[str, Any], client: str | None = None
) -> dict[str, Any]:
    """Exchange a code for a real token. Unauthenticated, and single use."""
    auth = get_auth(jarvis)
    if auth is None:
        # No token store means no way to mint one. Refusing here rather than
        # letting an AttributeError become a 500 keeps the failure legible on
        # the one endpoint people hit while setting the thing up.
        raise PairingError("This server has no token store, so it cannot pair a device.")
    # The code is spent BEFORE anything else can fail, which is what decides a
    # race between two devices holding the same photograph of the same screen.
    entry = get_codes(jarvis).claim(payload.get("code"), client=client)
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
