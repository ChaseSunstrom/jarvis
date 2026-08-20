"""What this particular n8n can actually do, measured rather than assumed.

## The problem this solves

n8n ships one binary and sells several products out of it. Whether the AI
workflow builder exists on a given instance is decided by a signed licence
certificate, checked by a middleware that runs *before* the route handler. No
environment variable turns it on. Worse, the two switches that sound like they
would are unrelated:

    aiBuilder.setup     is a model wired up?          <- you control this
    aiBuilder.enabled   is the feature licensed?      <- the certificate does

Somebody who has pointed n8n's AI settings at their own local model has set the
first one and not the second, and there is no reason they should know that.
So Jarvis must never say "the AI builder failed"; it must say which of the
four possible reasons it was.

## Three layers, cheapest first

1. **Setup.** No login in the config, so `/rest` is closed and the relay tool
   is never registered at all. The model cannot try what does not exist.
2. **Probe.** Log in, read `GET /rest/settings`, report `aiBuilder`. Cached,
   refreshed on demand from the console's CHECK button.
3. **Call.** The definitive signal, and the only one that cannot be stale: a
   403 whose body says `Plan lacks license for this feature`. On seeing it,
   the capability is marked dead for the life of the process.

## What it deliberately does not do

It does not guess. Every `unavailable` carries a `reason` from a closed set
and a sentence that names the thing to change. "The AI builder is unavailable"
with no reason is the failure this module exists to prevent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .client import N8nClient, N8nError
from .session import N8nSession, SessionError

_LOGGER = logging.getLogger(__name__)

__all__ = ["N8nCapabilities", "Capability", "BUILDER_PATH", "LICENCE_MARKER"]

#: How long a probe result is believed. A licence can be added while Jarvis is
#: running; five minutes is short enough that somebody who just fixed it does
#: not have to restart, and long enough that a tool call is not a login.
CACHE_SECONDS = 300.0

BUILDER_PATH = "ai/build"
SETTINGS_PATH = "settings"

#: n8n's own wording, from the licence middleware. Matched case-insensitively
#: on a substring because it is a message, not an API.
LICENCE_MARKER = "lacks license"


@dataclass(frozen=True)
class Capability:
    """One yes-or-no about the instance, with the reason it is no."""

    available: bool
    #: A slug from a closed set, for code: "", "unconfigured", "credentials",
    #: "mfa", "licence", "too old", "unreachable", "not set up", "bot filter".
    reason: str = ""
    #: The same thing as a sentence, for a person.
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "detail": self.detail}


@dataclass
class N8nCapabilities:
    """The three-line answer to "does this work?".

    Public API, login, AI builder — each measured separately, because they
    fail separately and a single "n8n: broken" sends people to the wrong half.
    """

    client: N8nClient
    session: N8nSession | None = None
    _checked_at: float = field(default=0.0, repr=False)
    _api: Capability = field(default_factory=lambda: Capability(False, "unchecked"), repr=False)
    _login: Capability = field(default_factory=lambda: Capability(False, "unchecked"), repr=False)
    _builder: Capability = field(
        default_factory=lambda: Capability(False, "unchecked"), repr=False
    )
    #: Set by the definitive 403. Never cleared by a cache expiry, because a
    #: licence that was absent when the call was made will not have appeared
    #: five minutes later without a restart, and re-asking costs a login.
    _builder_is_dead: bool = field(default=False, repr=False)

    # --- reading ----------------------------------------------------------
    @property
    def api(self) -> Capability:
        return self._api

    @property
    def login(self) -> Capability:
        return self._login

    @property
    def builder(self) -> Capability:
        return self._builder

    @property
    def fresh(self) -> bool:
        return bool(self._checked_at) and (time.time() - self._checked_at) < CACHE_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "api": self._api.as_dict(),
            "login": self._login.as_dict(),
            "builder": self._builder.as_dict(),
            "checked_at": self._checked_at,
        }

    def summary(self) -> str:
        """Three lines, in the order somebody would fix them."""
        return "\n".join(
            [
                f"Public API: {self._api.detail or ('works' if self._api.available else '—')}",
                f"Login: {self._login.detail or ('works' if self._login.available else '—')}",
                f"AI builder: {self._builder.detail or ('available' if self._builder.available else '—')}",
            ]
        )

    # --- measuring --------------------------------------------------------
    async def refresh(self, *, force: bool = False) -> "N8nCapabilities":
        if self.fresh and not force:
            return self
        self._api = await self._check_api()
        self._login = await self._check_login()
        self._builder = await self._check_builder()
        self._checked_at = time.time()
        return self

    async def _check_api(self) -> Capability:
        result = await self.client.probe()
        return Capability(
            available=bool(result.get("ok")),
            reason="" if result.get("ok") else "unreachable",
            detail=str(result.get("detail") or ""),
        )

    async def _check_login(self) -> Capability:
        if self.session is None or not self.session.configured:
            why = self.session.why_not if self.session else "No n8n login is configured."
            return Capability(False, "unconfigured", why)
        try:
            await self.session.login()
        except SessionError as err:
            said = str(err)
            reason = "credentials"
            if "two-factor" in said:
                reason = "mfa"
            elif "did not answer" in said or "could not reach" in said:
                reason = "unreachable"
            elif "no " in said and "/login" in said:
                reason = "too old"
            return Capability(False, reason, said)
        return Capability(True, "", f"Logged in to {self.session.url} as {self.session.email}.")

    async def _check_builder(self) -> Capability:
        """Whether `/rest/ai/build` would answer, without calling it.

        Calling it for real would start a conversation and cost the user
        tokens on their own model. `GET /rest/settings` is free and says the
        same thing — with the one caveat that it is the instance's own claim,
        which is why the 403 at call time still wins.
        """
        if self._builder_is_dead:
            return self._builder
        if not self._login.available:
            return Capability(
                False,
                self._login.reason or "unconfigured",
                "The AI builder is on `/rest`, which needs a login. "
                + (self._login.detail or ""),
            )
        assert self.session is not None  # login.available implies it
        try:
            response = await self.session.request("GET", SETTINGS_PATH)
        except SessionError as err:
            return Capability(False, "unreachable", str(err))

        if response.status_code == 204 and not response.content:
            return Capability(
                False,
                "bot filter",
                "n8n answered 204 with an empty body — that is its bot filter, "
                "which means something in front of n8n rewrote the User-Agent.",
            )
        if response.status_code == 404:
            return Capability(
                False,
                "too old",
                "This n8n has no /rest/settings. It predates the version this "
                "was written against.",
            )
        if response.status_code >= 400:
            return Capability(
                False,
                "unreachable",
                f"n8n answered {response.status_code} for its own settings.",
            )
        try:
            payload = response.json()
        except ValueError:
            return Capability(
                False,
                "unreachable",
                "n8n's settings came back as something that is not JSON. Is "
                "that URL really an n8n?",
            )
        return _read_builder_settings(payload)

    # --- the definitive answer -------------------------------------------
    def note_refusal(self, status: int, body: str) -> Capability:
        """Record what a real call to the builder said, and believe it.

        The probe reads the instance's own claim about itself. This reads the
        licence middleware's actual verdict, which is the only thing that
        decides.
        """
        said = str(body or "")[:400]
        if status == 403 and LICENCE_MARKER in said.lower():
            self._builder_is_dead = True
            self._builder = Capability(
                False,
                "licence",
                "This n8n cannot use its own AI builder: the instance's "
                "licence does not include it (n8n answered 403, "
                f"{said.strip()[:120]!r}). Jarvis will not ask again until it "
                "is restarted.",
            )
        elif status == 404:
            self._builder_is_dead = True
            self._builder = Capability(
                False,
                "too old",
                "This n8n has no /rest/ai/build. It predates the AI builder.",
            )
        elif status == 403:
            self._builder = Capability(
                False,
                "credentials",
                f"n8n refused the builder with 403: {said.strip()[:200]}",
            )
        return self._builder

    def instead(self) -> dict[str, str]:
        """What to tell the model when the builder is not there.

        Not a silent internal fallthrough: writing the workflow itself is a
        turn of Jarvis's own model and cannot happen inside a tool call, so a
        tool that pretended to fall back would be lying about what it did.
        A sentence the model can act on in the next round is the honest shape,
        and it is the same one `coerce_arguments` already uses.
        """
        return {
            "status": "error",
            "error": self._builder.detail
            or "This n8n cannot use its own AI builder.",
            "instead": (
                "Write the workflow JSON yourself and call create_n8n_workflow. "
                "Call list_n8n_node_types first to see which nodes and versions "
                "this instance actually has."
            ),
        }


def _read_builder_settings(payload: Any) -> Capability:
    """`aiBuilder` out of `GET /rest/settings`, and what it means.

    Split out because the interesting case — licensed off, model wired up — is
    the state most self-hosted users are in, and it deserves its own sentence
    rather than a shrug.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    settings = data if isinstance(data, dict) else payload
    if not isinstance(settings, dict):
        return Capability(False, "unreachable", "n8n's settings were not an object.")

    raw = settings.get("aiBuilder")
    if not isinstance(raw, dict):
        return Capability(
            False,
            "too old",
            "n8n's settings say nothing about an AI builder, so this version "
            "does not have one.",
        )
    enabled = bool(raw.get("enabled"))
    setup = bool(raw.get("setup"))
    if enabled:
        return Capability(
            True,
            "",
            "n8n says its AI builder is licensed and set up."
            if setup
            else "n8n says its AI builder is licensed, but no model is wired "
            "up to it — set N8N_AI_ANTHROPIC_KEY or ANTHROPIC_BASE_URL on the "
            "n8n side.",
        )
    if setup:
        return Capability(
            False,
            "licence",
            "A model is wired up to n8n's AI builder, but the instance's "
            "licence does not include the feature — those are two separate "
            "switches and only the first one is yours. Jarvis will write "
            "workflows itself instead, which works on every n8n.",
        )
    return Capability(
        False,
        "not set up",
        "n8n's AI builder is neither licensed nor wired up to a model. Jarvis "
        "will write workflows itself instead, which works on every n8n.",
    )


def why_not_reachable(err: N8nError) -> str:
    """An `N8nError`, phrased for the capability line."""
    return str(err)
