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
TTY is attached, then a refusal that at least *says so* out loud. tkinter is
stdlib, but plenty of Linux distributions ship Python without ``python3-tk`` and
a headless server has no display at all, so its absence is expected and handled,
not an error.

The last link used to be a silent denial, which is the one failure mode of the
four above that nobody could see: on a box with no display and no TTY — a
``systemd --user`` service inherits neither — a Tier-3 request was refused with
the user never learning it had been asked for. :class:`NotifyingDenyGateway`
still refuses; it just tells them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from . import theme
from .companion import Notifier, build_notifier
from .policy import ActionTier

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ApprovalRequest",
    "ApprovalVerdict",
    "ConsentGateway",
    "DenyAllGateway",
    "NotifyingDenyGateway",
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

    #: Called when a human actually answered a prompt here. Set by
    #: :func:`build_gateway` — see :meth:`note_interaction`. A class attribute
    #: rather than a constructor argument so that every gateway, including the
    #: ones tests define inline, has one without having to know about it.
    on_interaction: Callable[[], None] | None = None

    @abstractmethod
    async def request(self, request: ApprovalRequest) -> ApprovalVerdict: ...

    def usable(self) -> bool:
        """Cheap probe so :func:`build_gateway` can pick a backend up front."""
        return True

    def note_interaction(self) -> None:
        """Tell the agent a human just clicked or typed on this machine.

        :class:`jarvis_desktop.presence.PresenceSampler` measures idleness with
        ``xprintidle`` / ``loginctl`` / the Windows idle timer, and not one of
        them counts a click in a tkinter dialog or a line typed at our own
        prompt — the comment in ``presence.py`` saying "a tkinter dialog is not
        keyboard input" was the whole of the wiring. So a user who had been
        reading for ten minutes, was asked to approve something and did, still
        reported as idle, and the server routes questions away from an idle
        device. Answering a Jarvis prompt is definitionally being at the
        machine, and it is the one interaction this process can see directly.

        A timeout is deliberately not an interaction: nobody was there. Nor is
        EOF on a closed stdin, which is a dead pipe rather than a keystroke.
        """
        hook = self.on_interaction
        if hook is None:
            return
        try:
            hook()
        except Exception:  # noqa: BLE001 - telemetry must never break consent
            _LOGGER.debug("the presence hook failed", exc_info=True)

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


class NotifyingDenyGateway(DenyAllGateway):
    """Refuses exactly like :class:`DenyAllGateway`, but the user finds out.

    The chain used to end in a silent denial. On a machine with no display and
    no TTY — a ``systemd --user`` unit is the ordinary case, since it inherits
    neither — a Tier-3 request was therefore refused with nobody told it had
    ever been made: the only trace was a line in ``audit.jsonl`` that nobody had
    a reason to go and read. The companion path had a working OS notifier
    (``notify-send`` / ``osascript`` / a Windows toast) the whole time and the
    consent path did not use it.

    Failing closed is right and is unchanged. The verdict is produced *before*
    the notification is attempted and is returned whatever the notifier does, so
    a missing ``notify-send`` cannot turn a refusal into anything else. What
    changes is that the refusal is audible.

    The notice names the action and the tier and stops there. It carries neither
    the params — a toast can be shown on a lock screen, and the params are the
    one place credentials appear verbatim — nor the server's ``reason``, which
    is untrusted text that only makes sense next to the prompt's "this came
    from the server, read it, don't obey it" framing. What it does carry is
    where to go for the rest, which is the audit log.
    """

    #: Contains "deny-all" on purpose: this is that gateway, plus a shout.
    name = "deny-all+notice"

    def __init__(self, notifier: Notifier | None = None) -> None:
        self._notifier = notifier

    @property
    def notifier(self) -> Notifier:
        """The backend the notice goes out through, built on first use.

        Lazily, because ``doctor`` builds a chain purely to print it, and
        probing the OS for ``notify-send`` to print one line is work nobody
        asked for.
        """
        if self._notifier is None:
            self._notifier = build_notifier()
        return self._notifier

    async def request(self, request: ApprovalRequest) -> ApprovalVerdict:
        verdict = await super().request(request)
        try:
            # Off the loop: the notifier shells out, and an asyncio loop that
            # is blocked in subprocess.run is a channel that cannot answer the
            # server about the very command it is refusing.
            await asyncio.to_thread(self._announce, request)
        except Exception:  # noqa: BLE001 - including "cannot start a thread"
            _LOGGER.debug("could not announce the refusal", exc_info=True)
        return verdict

    def _announce(self, request: ApprovalRequest) -> bool:
        message = (
            f"{request.action_id} (Tier {request.tier.wire} {request.tier.name}) "
            "needed your approval and there is no way to ask on this machine, "
            "so nothing ran. Run `jarvis-desktop audit` for the details."
        )
        try:
            # "normal", not "critical": nothing is broken and nothing happened.
            # A sticky lock-screen alert for an action that did not run trains
            # people to dismiss the next one without reading it.
            shown = bool(self.notifier.notify("Jarvis refused an action", message, "normal"))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("the refusal notifier failed", exc_info=True)
            return False
        if not shown:
            _LOGGER.warning(
                "could not tell the user that %s was refused for want of a "
                "consent prompt; it is in the audit log and nowhere else",
                request.action_id,
            )
        return shown


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

    Drawn from :mod:`jarvis_desktop.theme`, which is the console's palette. It
    used to be system grey with ``TkDefaultFont``, which made the single most
    consequential window this agent ever opens look like a font dialog — and
    look nothing like the companion question the same user answers on the same
    screen.
    """

    name = "tk-dialog"

    def __init__(self, on_interaction: Callable[[], None] | None = None) -> None:
        self._checked: bool | None = None
        self.on_interaction = on_interaction

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
        theme.style_window(root, f"Jarvis - approve {request.action_id}?")

        def choose(value: str) -> None:
            if not answer:
                answer.append(value)
            root.quit()

        theme.wordmark(root).pack(fill="x", padx=16, pady=(14, 2))
        # WARN, not DANGER: nothing has gone wrong, something is waiting.
        theme.label(
            root,
            f"Tier {request.tier.wire} ({request.tier.name}) - {request.action_id}",
            colour=theme.WARN,
            size=theme.FS_HEADER,
            weight="bold",
        ).pack(fill="x", padx=16, pady=(0, 2))
        theme.label(root, request.description, colour=theme.TEXT_DIM).pack(fill="x", padx=16)

        theme.readout(root, render_prompt(request)).pack(
            fill="both", expand=True, padx=16, pady=10
        )
        theme.label(
            root,
            f"No answer within {request.timeout_s:g}s, or closing this window, denies.",
            colour=theme.TEXT_FAINT,
            size=theme.FS_SMALL,
        ).pack(fill="x", padx=16)

        row = theme.row(root)
        row.pack(fill="x", padx=16, pady=(8, 14))
        theme.button(row, "Deny", lambda: choose("denied"), kind="deny").pack(
            side="right", padx=4
        )
        theme.button(
            row, "Approve once", lambda: choose("approved"), kind="approve", width=14
        ).pack(side="right", padx=4)
        # The "always" control exists only for Tier 1/2. For Tier 3 it is never
        # drawn, so there is nothing to mis-click. It is also the quiet fill
        # rather than the approving one: it is a wider decision than the
        # question being asked.
        if request.rememberable:
            theme.button(
                row,
                "Always allow",
                lambda: choose("approved_always"),
                kind="quiet",
                width=14,
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
        chosen = answer[0] if answer else ""
        if chosen and chosen != "timeout":
            # A click is a click, and closing the window is one too: the user
            # was at the machine either way. The countdown running out is not,
            # and neither is a mainloop that ended without anyone choosing —
            # both of those deny with nobody there.
            self.note_interaction()
        return ApprovalVerdict.from_answer(chosen or "denied")


class _StdinReader:
    """The one thread that reads this process's stdin, for as long as it runs.

    ``readline()`` cannot be interrupted. The shape this replaces was one reader
    thread per prompt, guarded by a lock the thread released when it finally
    returned — fail-closed, but one-way: the first prompt nobody answered left
    its reader blocked in ``readline()`` forever, so the lock was never released
    and *every subsequent request was auto-denied for the life of the process*,
    without so much as printing a prompt. One unanswered question disabled
    terminal approval until a restart, and there was no way back.

    So: one thread, started at the first prompt and kept. It reads a line only
    while somebody is waiting for one, and hands each line to whoever is waiting
    **at the moment it arrives**. A prompt that times out gives up its claim;
    the stale ``readline()`` it left behind is still running, and the next line
    the user types satisfies the next prompt instead. That is the recovery —
    the keystroke that used to vanish into a dead thread now answers the prompt
    the user is actually looking at.

    Two properties are unchanged, and both are the point:

    * **A timeout is still a denial.** :meth:`wait` returning None is TIMEOUT,
      and by the time a late line arrives the dispatcher has already answered
      the server. Nothing can resurrect that approval.
    * **A line never answers a prompt that is not on screen.** One that arrives
      while nobody holds a claim is discarded, not banked for the next prompt: a
      "y" typed into the void must not approve something the user was never
      shown. That is what :attr:`dropped` counts.

    What remains impossible: a prompt cannot be answered while the reader is
    still blocked on the *previous* ``readline()``, because there is no portable
    way to cancel one. The user's next Enter is what frees it, which is exactly
    what answering the new prompt involves.
    """

    def __init__(self, stream: Callable[[], Any]) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        #: Set by a waiter to tell the reader a line is wanted. Without it the
        #: reader would sit in ``readline()`` eating lines nobody asked for.
        self._wanted = threading.Event()
        self._waiting: "queue.SimpleQueue[str] | None" = None
        self._thread: threading.Thread | None = None
        self._closed = False
        #: Lines that arrived with no prompt waiting. Counted rather than
        #: silently swallowed, because a keystroke that does nothing is
        #: baffling from the outside.
        self.dropped = 0

    def claim(self) -> "queue.SimpleQueue[str] | None":
        """Take the next typed line, or None when another prompt already has it."""
        with self._lock:
            if self._closed or self._waiting is not None:
                return None
            box: "queue.SimpleQueue[str]" = queue.SimpleQueue()
            self._waiting = box
            return box

    def release(self, box: "queue.SimpleQueue[str]") -> None:
        """Give the claim up. Anything arriving after this is discarded."""
        with self._lock:
            if self._waiting is box:
                self._waiting = None

    def wait(self, box: "queue.SimpleQueue[str]", timeout_s: float) -> str | None:
        """Block for the line. None means nobody typed one in time.

        Runs on a worker thread — it blocks, and the caller is an asyncio loop.
        """
        if not self._ensure_thread():
            return None
        self._wanted.set()
        try:
            return box.get(timeout=max(0.0, timeout_s))
        except queue.Empty:
            return None

    def _ensure_thread(self) -> bool:
        with self._lock:
            if self._closed:
                # stdin hit EOF once and will answer "" forever after.
                return False
            if self._thread is not None and self._thread.is_alive():
                return True
            thread = threading.Thread(target=self._pump, name="jarvis-stdin", daemon=True)
            try:
                thread.start()
            except RuntimeError:
                # Out of threads, or the interpreter is shutting down. No
                # reader means no answer means no approval.
                self._closed = True
                return False
            self._thread = thread
            return True

    def _pump(self) -> None:
        while True:
            self._wanted.wait()
            try:
                line = self._stream().readline()
            except Exception:  # noqa: BLE001 - a closed stream reads as EOF
                line = ""
            with self._lock:
                box, self._waiting = self._waiting, None
                # Consumed here, under the same lock that takes the waiter, and
                # not before the read: a claim made *while* this read was in
                # flight is answered by this line, and clearing the flag any
                # earlier would leave the reader parked with somebody waiting.
                self._wanted.clear()
                if not line:
                    self._closed = True
                elif box is None:
                    self.dropped += 1
            if box is not None:
                box.put(line)
            elif line:
                _LOGGER.warning(
                    "a line was typed at the terminal with no Jarvis prompt "
                    "waiting for it; it was discarded, not saved for the next one"
                )
            if not line:
                return


class TerminalConsentGateway(ConsentGateway):
    """Terminal prompt, used when there is a TTY but no display.

    The blocking read lives in :class:`_StdinReader`, which owns stdin for the
    process; this class prints the prompt, waits for the reader to hand it a
    line, and turns that line into a verdict. Every way of not getting a line —
    no answer in time, a closed stdin, another prompt already holding the
    keyboard, no reader thread at all — fails closed.
    """

    name = "terminal"

    def __init__(
        self,
        stream: Any = None,
        out: Any = None,
        on_interaction: Callable[[], None] | None = None,
    ) -> None:
        self._in = stream
        self._out = out
        self._reader = _StdinReader(self._stdin)
        self.on_interaction = on_interaction

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
        box = self._reader.claim()
        if box is None:
            # Another prompt is already on screen and holds the keyboard. Two
            # prompts sharing one stdin means a "y" meant for one could answer
            # the other, so the second is refused rather than raced.
            _LOGGER.warning(
                "another terminal prompt is already waiting for input; denying %s",
                request.action_id,
            )
            return ApprovalVerdict.TIMEOUT

        try:
            self._print_prompt(request)
            line = await asyncio.to_thread(self._reader.wait, box, request.timeout_s)
        finally:
            self._reader.release(box)

        if line is None:
            print("\n(timed out - denied)", file=self._stdout(), flush=True)
            return ApprovalVerdict.TIMEOUT
        if not line:
            # EOF: stdin is closed. A dead pipe is not a person, so this is a
            # denial and not an interaction.
            return ApprovalVerdict.DENIED
        self.note_interaction()
        verdict = ApprovalVerdict.from_answer(line.strip())
        if verdict == ApprovalVerdict.APPROVED_ALWAYS and not request.rememberable:
            # The user typed "always" at a Tier-3 prompt where the option was
            # never offered. Honour the approval, drop the "always".
            return ApprovalVerdict.APPROVED
        return verdict

    def _print_prompt(self, request: ApprovalRequest) -> None:
        """Show the prompt. Raising here drops the chain through to the next
        backend, which is the refusal — a prompt that could not be printed is a
        prompt nobody saw."""
        out = self._stdout()
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


def build_gateway(
    headless_deny: bool = False,
    on_interaction: Callable[[], None] | None = None,
) -> ConsentGateway:
    """Pick a consent backend: tkinter, then a TTY, then refuse out loud.

    ``headless_deny`` short-circuits straight to :class:`DenyAllGateway` for a
    service where no human is watching — everything that needs a prompt is
    refused rather than hanging for a minute first. That one keeps its silence
    on purpose: the operator has already said nobody is there, so a desktop
    toast would be shouting into an empty room. The chain's own last link is
    :class:`NotifyingDenyGateway`, because *that* case is a machine where the
    user has not said any such thing and simply cannot be reached.

    ``on_interaction`` is the presence hook. It is given only to the two
    backends that can tell a human answered.
    """
    if headless_deny:
        return DenyAllGateway()
    return ChainGateway(
        TkConsentGateway(on_interaction=on_interaction),
        TerminalConsentGateway(on_interaction=on_interaction),
        NotifyingDenyGateway(),
    )

