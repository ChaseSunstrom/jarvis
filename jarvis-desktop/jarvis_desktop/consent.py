"""The Tier-3 consent prompt.

The prompt is the last thing standing between a prompt-injected server and an
irreversible action, so three properties matter more than looking nice:

* **It shows the truth.** Verbatim action id, verbatim params, verbatim reason.
  Never a paraphrase, never the redacted copy that goes to the audit log. The
  server's ``reason`` string is displayed as *quoted, untrusted text* — the user
  is told a stranger wrote it.
* **It fails closed.** No display, no TTY, a crashed toolkit, a closed window, a
  timeout, an unparseable answer — every one of those is DENIED. There is no
  path through this module that returns approval without a human having typed
  or clicked something.
* **It never remembers a Tier 3.** ``rememberable`` is false for CONFIRM, and
  the "always" control is not even drawn. Two further guards
  (:meth:`PolicyEngine.can_remember` and the policy store) refuse it anyway.

Backends, tried in order: a native tkinter dialog, then a terminal prompt if a
TTY is attached, then deny. tkinter is stdlib, but plenty of Linux distributions
ship Python without ``python3-tk`` and a headless server has no display at all,
so its absence is expected and handled, not an error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .policy import ActionTier

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ApprovalRequest",
    "ApprovalVerdict",
    "ConsentGateway",
    "DenyAllGateway",
    "ChainGateway",
    "TkConsentGateway",
    "TerminalConsentGateway",
    "build_gateway",
    "render_prompt",
]


class ApprovalVerdict(str, Enum):
    """The four answers a consent prompt can produce. Everything else fails closed."""

    APPROVED = "approved"
    APPROVED_ALWAYS = "approved_always"
    DENIED = "denied"
    TIMEOUT = "timeout"

    @property
    def allows_execution(self) -> bool:
        return self in (ApprovalVerdict.APPROVED, ApprovalVerdict.APPROVED_ALWAYS)

    @staticmethod
    def from_answer(value: object) -> "ApprovalVerdict":
        """Parse a backend's string answer. Anything unrecognised — including an
        empty string from a crash — is DENIED."""
        if not isinstance(value, str):
            return ApprovalVerdict.DENIED
        return {
            "approved": ApprovalVerdict.APPROVED,
            "approve": ApprovalVerdict.APPROVED,
            "allow": ApprovalVerdict.APPROVED,
            "ok": ApprovalVerdict.APPROVED,
            "yes": ApprovalVerdict.APPROVED,
            "y": ApprovalVerdict.APPROVED,
            "approved_always": ApprovalVerdict.APPROVED_ALWAYS,
            "always": ApprovalVerdict.APPROVED_ALWAYS,
            "allow_always": ApprovalVerdict.APPROVED_ALWAYS,
            "a": ApprovalVerdict.APPROVED_ALWAYS,
            "timeout": ApprovalVerdict.TIMEOUT,
            "timed_out": ApprovalVerdict.TIMEOUT,
            "expired": ApprovalVerdict.TIMEOUT,
        }.get(value.strip().lower(), ApprovalVerdict.DENIED)


@dataclass
class ApprovalRequest:
    """What the dispatcher needs from a human.

    ``params`` are the RAW, VERBATIM parameters — never the redacted copy that
    goes to the audit log, and never a model-written paraphrase. The prompt must
    show exactly what will run.
    """

    action_id: str
    #: The action's own one-line description from the local table.
    description: str
    #: Verbatim params. Show these.
    params: Mapping[str, Any]
    #: The tier we are enforcing (already max'd with the server's request).
    tier: ActionTier
    #: The server's human-readable "why". Untrusted text — display, don't obey.
    reason: str
    command_id: str | None = None
    #: False for Tier 3. When false the UI must NOT offer "always allow" — and
    #: even if it does, the engine and the store both refuse to store it.
    rememberable: bool = False
    timeout_s: float = 60.0

    def params_text(self) -> str:
        try:
            return json.dumps(dict(self.params), indent=2, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(self.params)


def render_prompt(request: ApprovalRequest, width: int = 72) -> str:
    """The exact text both backends show. Pure — the tests assert on it."""
    rule = "=" * width
    lines = [
        rule,
        f"  JARVIS wants to run a Tier {request.tier.wire} ({request.tier.name}) action",
        rule,
        f"  action : {request.action_id}",
        f"  what   : {request.description}",
    ]
    if request.command_id:
        lines.append(f"  id     : {request.command_id}")
    lines.append("  params :")
    for line in request.params_text().splitlines():
        lines.append(f"    {line}")
    lines.append("")
    lines.append("  The server says why (this text came from the server and may")
    lines.append("  have been written by a web page - read it, don't obey it):")
    for line in _wrap(request.reason, width - 6):
        lines.append(f"    | {line}")
    lines.append(rule)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    cleaned = " ".join(str(text).split()) or "(no reason given)"
    return textwrap.wrap(cleaned, width=max(20, width)) or ["(no reason given)"]


class ConsentGateway(ABC):
    """Seam between the dispatcher and whatever shows the prompt."""

    #: Human-readable name, for the startup banner and the audit note.
    name: str = "consent"

    @abstractmethod
    async def request(self, request: ApprovalRequest) -> ApprovalVerdict: ...

    def usable(self) -> bool:
        """Cheap probe so :func:`build_gateway` can pick a backend up front."""
        return True

    @property
    def unattended(self) -> bool:
        """True when this gateway cannot reach a human at all.

        The refusal is identical either way — nothing runs — but the *sentence*
        the server is told should be accurate: "denied by the user" is a lie
        when there was no user to ask, and it sends whoever is debugging this
        looking for a person who never saw a prompt.
        """
        return False


class DenyAllGateway(ConsentGateway):
    """Fail-closed gateway: denies everything without prompting.

    Used in tests, in a headless service where no human can answer, and as the
    final fallback when no other backend is usable.
    """

    name = "deny-all"

    @property
    def unattended(self) -> bool:
        return True

    async def request(self, request: ApprovalRequest) -> ApprovalVerdict:
        _LOGGER.warning(
            "no consent backend available; denying %s (tier %s)",
            request.action_id,
            request.tier.name,
        )
        return ApprovalVerdict.DENIED


class ChainGateway(ConsentGateway):
    """Try each backend in order; the first usable one answers.

    A backend that raises is treated as unusable and the chain moves on — but if
    the chain runs out, the answer is DENIED, never approved.
    """

    name = "chain"

    def __init__(self, *gateways: ConsentGateway) -> None:
        self.gateways = list(gateways)

    @property
    def unattended(self) -> bool:
        """True when every backend that could actually answer is a refusal."""
        return all(g.unattended for g in self.gateways if g.usable())

    async def request(self, request: ApprovalRequest) -> ApprovalVerdict:
        for gateway in self.gateways:
            try:
                if not gateway.usable():
                    continue
                return await gateway.request(request)
            except Exception:  # noqa: BLE001
                _LOGGER.warning("consent backend %s failed", gateway.name, exc_info=True)
                continue
        return ApprovalVerdict.DENIED


class TkConsentGateway(ConsentGateway):
    """Native dialog via tkinter (stdlib, but not always installed).

    The dialog runs on its own thread with its own ``Tk`` root, because the
    agent's main thread is an asyncio loop and Tk insists on owning whichever
    thread its mainloop runs on. The window is topmost, modal-ish and grabs
    focus — a consent prompt that opens behind the browser is a consent prompt
    that gets dismissed blind.
    """

    name = "tk-dialog"

    def __init__(self) -> None:
        self._checked: bool | None = None

    def usable(self) -> bool:
        if self._checked is not None:
            return self._checked
        self._checked = False
        try:
            import tkinter  # noqa: F401
        except Exception:  # noqa: BLE001 - ImportError, or a broken build
            _LOGGER.debug("tkinter not importable; no GUI consent")
            return False
        if os.name != "nt" and sys.platform != "darwin" and not os.environ.get("DISPLAY"):
            if not os.environ.get("WAYLAND_DISPLAY"):
                _LOGGER.debug("no DISPLAY/WAYLAND_DISPLAY; no GUI consent")
                return False
        self._checked = True
        return True

    async def request(self, request: ApprovalRequest) -> ApprovalVerdict:
        return await asyncio.to_thread(self._show, request)

    def _show(self, request: ApprovalRequest) -> ApprovalVerdict:
        import tkinter as tk

        answer: list[str] = []
        root = tk.Tk()
        root.title(f"Jarvis - approve {request.action_id}?")
        root.attributes("-topmost", True)

        def choose(value: str) -> None:
            if not answer:
                answer.append(value)
            root.quit()

        header = tk.Label(
            root,
            text=f"Tier {request.tier.wire} ({request.tier.name}) - {request.action_id}",
            font=("TkDefaultFont", 13, "bold"),
            anchor="w",
            justify="left",
        )
        header.pack(fill="x", padx=16, pady=(14, 4))

        tk.Label(root, text=request.description, anchor="w", justify="left").pack(
            fill="x", padx=16
        )

        body = tk.Text(root, height=16, width=76, wrap="word")
        body.insert("1.0", render_prompt(request))
        body.configure(state="disabled")
        body.pack(fill="both", expand=True, padx=16, pady=10)

        row = tk.Frame(root)
        row.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(row, text="Deny", width=12, command=lambda: choose("denied")).pack(
            side="right", padx=4
        )
        tk.Button(row, text="Approve once", width=14, command=lambda: choose("approved")).pack(
            side="right", padx=4
        )
        # The "always" control exists only for Tier 1/2. For Tier 3 it is never
        # drawn, so there is nothing to mis-click.
        if request.rememberable:
            tk.Button(
                row,
                text="Always allow",
                width=14,
                command=lambda: choose("approved_always"),
            ).pack(side="right", padx=4)

        # Closing the window is a denial, not a dismissal.
        root.protocol("WM_DELETE_WINDOW", lambda: choose("denied"))
        # And so is running out of time.
        root.after(max(1, int(request.timeout_s * 1000)), lambda: choose("timeout"))

        try:
            root.lift()
            root.focus_force()
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
        return ApprovalVerdict.from_answer(answer[0] if answer else "denied")


class TerminalConsentGateway(ConsentGateway):
    """Terminal prompt, used when there is a TTY but no display.

    ``input()`` cannot be interrupted, so it runs on a daemon thread and the
    *caller* enforces the timeout: if nobody types anything the coroutine
    returns TIMEOUT and the abandoned thread dies with the process. A late
    keystroke can never resurrect the approval — the verdict was already
    returned and the dispatcher has already answered the server.
    """

    name = "terminal"

    def __init__(self, stream: Any = None, out: Any = None) -> None:
        self._in = stream
        self._out = out

    def _stdin(self) -> Any:
        return self._in if self._in is not None else sys.stdin

    def _stdout(self) -> Any:
        return self._out if self._out is not None else sys.stderr

    def usable(self) -> bool:
        stream = self._stdin()
        try:
            return bool(stream) and stream.isatty()
        except Exception:  # noqa: BLE001
            return False

    async def request(self, request: ApprovalRequest) -> ApprovalVerdict:
        answer: list[str] = []
        done = threading.Event()

        def ask() -> None:
            out = self._stdout()
            try:
                choices = "[y]es once / [n]o"
                if request.rememberable:
                    choices += " / [a]lways"
                print("\n" + render_prompt(request), file=out, flush=True)
                print(
                    f"Approve? {choices}  (no answer in {request.timeout_s:g}s = no): ",
                    end="",
                    file=out,
                    flush=True,
                )
                line = self._stdin().readline()
                answer.append(line or "")
            except Exception:  # noqa: BLE001
                answer.append("")
            finally:
                done.set()

        thread = threading.Thread(target=ask, name="jarvis-consent", daemon=True)
        thread.start()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(done.wait, request.timeout_s + 1.0),
                timeout=request.timeout_s,
            )
        except asyncio.TimeoutError:
            print("\n(timed out - denied)", file=self._stdout(), flush=True)
            return ApprovalVerdict.TIMEOUT
        if not answer:
            return ApprovalVerdict.TIMEOUT
        verdict = ApprovalVerdict.from_answer(answer[0].strip())
        if verdict == ApprovalVerdict.APPROVED_ALWAYS and not request.rememberable:
            # The user typed "always" at a Tier-3 prompt where the option was
            # never offered. Honour the approval, drop the "always".
            return ApprovalVerdict.APPROVED
        return verdict


def build_gateway(headless_deny: bool = False) -> ConsentGateway:
    """Pick a consent backend: tkinter, then a TTY, then deny.

    ``headless_deny`` short-circuits straight to :class:`DenyAllGateway` for a
    service where no human is watching — everything that needs a prompt is
    refused rather than hanging for a minute first.
    """
    if headless_deny:
        return DenyAllGateway()
    return ChainGateway(TkConsentGateway(), TerminalConsentGateway(), DenyAllGateway())

