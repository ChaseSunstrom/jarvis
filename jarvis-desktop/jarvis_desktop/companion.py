"""Companion: jarvis-core reaching *this machine*, and getting an answer back.

Everything else in the agent is server-asks/device-does. This is the other
direction — the server has decided the user is at this desktop and wants to
tell them something, or ask them something and wait::

    <-  {"type": "jarvis_message", "message_id": "a1b2c3", "kind": "ask",
         "mode": "ask", "text": "Deploy to production?",
         "options": ["yes", "no"], "conversation_id": "conv-7",
         "importance": "high", "timeout_s": 120}

    ->  {"type": "jarvis_message_result", "message_id": "a1b2c3",
         "status": "answered", "answer": "no"}

``status`` is one of ``answered`` / ``dismissed`` / ``timeout`` /
``undeliverable``. Anything but ``answered`` tells the server to escalate to
the next device, which is why "I could not put this in front of a human" has
to be *reported* rather than swallowed: a dropped message is a message that
never reaches the user on any device.

## The rules this module exists to keep

* **Exactly one answer per ``message_id``.** The ledger is checked on every
  send path. A redelivered message replays the identical stored frame and does
  not re-prompt — the same idempotency the command path gets from
  ``ratelimit.CommandGate``, for the same reason: the socket can die between
  our answer and the server's read of it.
* **An id we do not know is ``undeliverable``, not a crash.** A malformed
  frame, an unknown mode, a dialog toolkit that is not installed, a headless
  box with no TTY — every one of those is a reported ``undeliverable`` and the
  server tries somewhere else.
* **The message text is DATA.** It came off a socket and may quote a web page
  the model just read. It is rendered as text and nothing else. There is no
  path from this module to the action registry — no import, no callback, no
  dispatcher — so a proactive message cannot execute anything, and the answer
  it collects is data too, not an authorisation token. Acting on "yes" still
  goes through ``device_command`` and the full policy treatment.

Ask backends, tried in order: a tkinter dialog, then a terminal prompt if a
TTY is attached, then ``undeliverable``. Exactly the shape
:mod:`jarvis_desktop.consent` uses — and for the same reason, since plenty of
Linux installs ship Python without ``python3-tk`` and a headless service has no
display at all.

## Wiring

Two hooks, both in code this module deliberately does not reach into::

    # jarvis_desktop/channel.py — DeviceChannel._handle_frame()
    elif kind == companion.TYPE_MESSAGE and self.companion is not None:
        # Off the read loop: a question can sit on screen for two minutes,
        # and a blocked read loop cannot even receive a cancellation.
        self.companion.handle_background(frame)

    # jarvis_desktop/__main__.py — cmd_run()
    presence = PresenceReporter(emit)
    companion = CompanionHandler(
        channel.send_frame,                       # raw frame, not an event
        asker=build_asker(headless=config.headless_deny),
        on_interaction=presence.note_interaction,
    )
    channel.companion = companion
    await presence.start()

``DeviceChannel`` needs one new public method for that — ``send_frame(frame)``
putting a raw dict on the socket — because a ``jarvis_message_result`` is a
top-level frame, not a ``device_event``.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "TYPE_MESSAGE",
    "TYPE_RESULT",
    "STATUS_ANSWERED",
    "STATUS_DISMISSED",
    "STATUS_TIMEOUT",
    "STATUS_UNDELIVERABLE",
    "CompanionMessage",
    "AskOutcome",
    "Asker",
    "ChainAsker",
    "TkAsker",
    "TerminalAsker",
    "UnavailableAsker",
    "Notifier",
    "CommandNotifier",
    "LogNotifier",
    "Speaker",
    "CommandSpeaker",
    "SilentSpeaker",
    "CompanionHandler",
    "build_asker",
    "build_notifier",
    "build_speaker",
]

TYPE_MESSAGE = "jarvis_message"
TYPE_RESULT = "jarvis_message_result"

STATUS_ANSWERED = "answered"
STATUS_DISMISSED = "dismissed"
STATUS_TIMEOUT = "timeout"
STATUS_UNDELIVERABLE = "undeliverable"
VALID_STATUSES = (STATUS_ANSWERED, STATUS_DISMISSED, STATUS_TIMEOUT, STATUS_UNDELIVERABLE)

MODE_SPEAK = "speak"
MODE_ASK = "ask"
MODE_NOTIFY = "notify"
VALID_MODES = (MODE_SPEAK, MODE_ASK, MODE_NOTIFY)

#: kind -> the mode we use when the server did not send a usable one.
_KIND_TO_MODE = {"say": MODE_SPEAK, "ask": MODE_ASK, "notify": MODE_NOTIFY}

VALID_IMPORTANCE = ("low", "normal", "high", "critical")

#: Nothing about the wire needs to be bigger than this, and a dialog cannot
#: render it anyway. Clamped rather than rejected: a truncated question the
#: user can answer beats a dropped one.
MAX_TEXT = 4000
MAX_OPTIONS = 8
MAX_OPTION_LEN = 80
MAX_CONVERSATION_ID = 128

MIN_TIMEOUT_S = 5.0
MAX_TIMEOUT_S = 600.0
DEFAULT_ASK_TIMEOUT_S = 120.0
DEFAULT_QUIET_TIMEOUT_S = 30.0

#: How many settled ids to remember for replay. Bounded: this is fed by the
#: network, so it cannot be allowed to grow without limit.
MAX_REMEMBERED = 256

#: Slack on top of a question's own countdown, so the backend's timer always
#: fires first and this is only a net under a backend whose timer never does.
BACKSTOP_GRACE_S = 10.0


# --- the message ------------------------------------------------------------


@dataclass(frozen=True)
class CompanionMessage:
    """One parsed ``jarvis_message``.

    Note what is NOT here: no action, no params, no tier, no "run this". A
    struct with no slot for them cannot be talked into one, however the server
    words the frame.
    """

    message_id: str
    kind: str
    mode: str
    text: str
    options: tuple[str, ...] = ()
    conversation_id: str | None = None
    importance: str = "normal"
    timeout_s: float = DEFAULT_ASK_TIMEOUT_S
    #: An additive, optional field: audio jarvis-core already synthesised.
    #: Absent in the documented protocol; honoured if a server sends it.
    tts_url: str | None = None

    @property
    def wants_answer(self) -> bool:
        return self.mode == MODE_ASK

    @property
    def sensitive(self) -> bool:
        """High/critical messages are the ones worth hiding on a locked box."""
        return self.importance in ("high", "critical")

    @staticmethod
    def parse(frame: Mapping[str, Any]) -> "CompanionMessage | None":
        """Read the fields we know, ignore the rest.

        Returns None only when there is no usable ``message_id`` — without one
        there is nothing to answer and nothing the server could match a reply
        to, so the frame is dropped rather than guessed at. Every other kind of
        malformation is clamped into something answerable, because an
        answerable question beats a silent drop.
        """
        if str(frame.get("type") or "") != TYPE_MESSAGE:
            return None
        message_id = str(frame.get("message_id") or "").strip()[:128]
        if not message_id:
            return None

        kind = str(frame.get("kind") or "").strip().lower()
        mode = str(frame.get("mode") or "").strip().lower()
        if mode not in VALID_MODES:
            # The server's routing decision is the authority; when it is
            # missing or garbled, fall back to what the message *is*.
            mode = _KIND_TO_MODE.get(kind, "")

        importance = str(frame.get("importance") or "").strip().lower()
        if importance not in VALID_IMPORTANCE:
            importance = "normal"

        conversation_id = str(frame.get("conversation_id") or "").strip()[
            :MAX_CONVERSATION_ID
        ] or None

        default_timeout = (
            DEFAULT_ASK_TIMEOUT_S if mode == MODE_ASK else DEFAULT_QUIET_TIMEOUT_S
        )
        timeout = _clamp_timeout(frame.get("timeout_s"), default_timeout)

        tts_url = str(frame.get("tts_url") or "").strip() or None

        return CompanionMessage(
            message_id=message_id,
            kind=kind or "notify",
            mode=mode,
            text=str(frame.get("text") or "")[:MAX_TEXT],
            options=_clean_options(frame.get("options")),
            conversation_id=conversation_id,
            importance=importance,
            timeout_s=timeout,
            tts_url=tts_url,
        )


def _clamp_timeout(raw: Any, default: float) -> float:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if value != value or value <= 0:  # NaN or nonsense
        return default
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, value))


def _clean_options(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, (str, int, float, bool)):
            continue
        text = str(item).strip().replace("\n", " ")[:MAX_OPTION_LEN]
        if text and text not in out:
            out.append(text)
        if len(out) >= MAX_OPTIONS:
            break
    return tuple(out)


@dataclass(frozen=True)
class AskOutcome:
    """What a question ended as. ``answer`` is meaningful only when answered."""

    status: str
    answer: str | None = None

    @staticmethod
    def answered(answer: str) -> "AskOutcome":
        return AskOutcome(STATUS_ANSWERED, answer)

    @staticmethod
    def dismissed() -> "AskOutcome":
        return AskOutcome(STATUS_DISMISSED)

    @staticmethod
    def timed_out() -> "AskOutcome":
        return AskOutcome(STATUS_TIMEOUT)

    @staticmethod
    def undeliverable() -> "AskOutcome":
        return AskOutcome(STATUS_UNDELIVERABLE)


# --- backends ---------------------------------------------------------------


class Notifier(ABC):
    """Shows a message. Returns False when it could not."""

    name = "notifier"

    def usable(self) -> bool:
        return True

    @abstractmethod
    def notify(self, title: str, message: str, urgency: str = "normal") -> bool: ...


class LogNotifier(Notifier):
    """The floor. A log line is a delivered notification as far as the
    machine's journal is concerned, and it never fails."""

    name = "log"

    def notify(self, title: str, message: str, urgency: str = "normal") -> bool:
        logging.getLogger("jarvis_desktop.companion.notify").info(
            "JARVIS %s: %s", title, message
        )
        return True


class CommandNotifier(Notifier):
    """The platform notifier: ``notify-send`` / ``osascript`` / a Windows toast.

    Deliberately shells out through the same helpers the ``notify`` action
    uses rather than importing the action itself, because an action needs an
    ``ActionContext`` and a policy decision, and this path has neither — the
    server is not commanding anything, it is talking.
    """

    name = "desktop"

    def __init__(self, runner: Callable[[list[str]], int] | None = None) -> None:
        self._run = runner or _run_argv

    def usable(self) -> bool:
        system = platform.system()
        if system == "Linux":
            return bool(shutil.which("notify-send"))
        if system == "Darwin":
            return bool(shutil.which("osascript"))
        return system == "Windows"

    def notify(self, title: str, message: str, urgency: str = "normal") -> bool:
        if urgency not in ("low", "normal", "critical"):
            urgency = "normal"
        system = platform.system()
        try:
            if system == "Linux" and shutil.which("notify-send"):
                return self._run(["notify-send", "-u", urgency, "--", title, message]) == 0
            if system == "Darwin" and shutil.which("osascript"):
                script = (
                    f"display notification {_applescript(message)} "
                    f"with title {_applescript(title)}"
                )
                return self._run(["osascript", "-e", script]) == 0
            if system == "Windows":
                from .actions.system import _windows_toast  # local import: Windows only

                return bool(_windows_toast(title, message))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("desktop notification failed", exc_info=True)
        return False


class Speaker(ABC):
    """Says something out loud. Returns False to fall back to a notification."""

    name = "speaker"

    def usable(self) -> bool:
        return True

    @abstractmethod
    async def speak(self, message: CompanionMessage) -> bool: ...


class SilentSpeaker(Speaker):
    """No audio here. Every ``speak`` becomes a notification."""

    name = "silent"

    def usable(self) -> bool:
        return False

    async def speak(self, message: CompanionMessage) -> bool:
        return False


class CommandSpeaker(Speaker):
    """Plays jarvis-core's TTS when the frame carries one, else synthesises
    locally.

    ``tts_url`` is additive and optional — the documented protocol does not
    include it, so the ordinary path here is the local voice. Either way, if
    nothing can make a sound this returns False and the caller downgrades to a
    notification rather than dropping the message, which is the same rule
    ``jarvis.presence._mode_for`` applies on the server.
    """

    name = "audio"

    def __init__(
        self,
        *,
        player: Callable[[str], bool] | None = None,
        say: Callable[[str], bool] | None = None,
        available: Callable[[], bool] | None = None,
    ) -> None:
        self._player = player
        self._say = say or _local_say
        if available is None:
            from .presence import audio_available as _audio

            available = _audio
        self._available = available

    def usable(self) -> bool:
        try:
            return bool(self._available()) and _speech_backend() is not None
        except Exception:  # noqa: BLE001
            return False

    async def speak(self, message: CompanionMessage) -> bool:
        text = message.text.strip()
        if not text:
            return False
        if message.tts_url and self._player is not None:
            if await asyncio.to_thread(self._player, message.tts_url):
                return True
        if not self.usable():
            return False
        return await asyncio.to_thread(self._say, text)


class Asker(ABC):
    """Puts a question in front of a human and waits for the answer."""

    name = "asker"

    def usable(self) -> bool:
        return True

    @property
    def unattended(self) -> bool:
        """True when this backend cannot reach a human at all."""
        return False

    @abstractmethod
    async def ask(self, message: CompanionMessage) -> AskOutcome: ...


class UnavailableAsker(Asker):
    """The floor: there is nobody to ask here."""

    name = "none"

    @property
    def unattended(self) -> bool:
        return True

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        _LOGGER.info(
            "no way to ask a question on this machine; reporting %s so the "
            "server tries another device",
            STATUS_UNDELIVERABLE,
        )
        return AskOutcome.undeliverable()


class ChainAsker(Asker):
    """First usable backend answers. Running out is ``undeliverable``."""

    name = "chain"

    def __init__(self, *askers: Asker) -> None:
        self.askers = list(askers)

    @property
    def unattended(self) -> bool:
        return all(a.unattended for a in self.askers if a.usable())

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        for asker in self.askers:
            try:
                if not asker.usable():
                    continue
                return await asker.ask(message)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.warning("ask backend %s failed", asker.name, exc_info=True)
                continue
        return AskOutcome.undeliverable()


class TkAsker(Asker):
    """A native dialog: the question, the options (or a text box), a countdown.

    Runs on its own thread with its own ``Tk`` root — the agent's main thread
    is an asyncio loop and Tk insists on owning whichever thread its mainloop
    runs on. Closing the window is a *dismissal*, not a timeout, because the
    two mean different things to the server: dismissing says "not here, try
    elsewhere" immediately, while a timeout burns the whole window first.
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
        except Exception:  # noqa: BLE001
            _LOGGER.debug("tkinter not importable; no GUI questions")
            return False
        import os

        if os.name != "nt" and sys.platform != "darwin":
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                _LOGGER.debug("no DISPLAY/WAYLAND_DISPLAY; no GUI questions")
                return False
        self._checked = True
        return True

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        return await asyncio.to_thread(self._show, message)

    def _show(self, message: CompanionMessage) -> AskOutcome:
        import tkinter as tk

        result: list[AskOutcome] = []
        root = tk.Tk()
        root.title("Jarvis")
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass

        entry: Any = None

        def settle(outcome: AskOutcome) -> None:
            if not result:
                result.append(outcome)
            root.quit()

        tk.Label(
            root,
            text="JARVIS",
            font=("TkFixedFont", 11, "bold"),
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=16, pady=(14, 2))

        body = tk.Message(root, text=message.text or "(no message)", width=520)
        body.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        countdown_label = tk.Label(root, text="", font=("TkFixedFont", 10))
        countdown_label.pack(fill="x", padx=16)

        row = tk.Frame(root)
        row.pack(fill="x", padx=16, pady=(6, 14))

        if message.options:
            for option in message.options:
                tk.Button(
                    row,
                    text=option,
                    width=max(8, min(18, len(option) + 2)),
                    command=lambda value=option: settle(AskOutcome.answered(value)),
                ).pack(side="left", padx=4)
        else:
            entry = tk.Entry(row)
            entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            entry.bind(
                "<Return>",
                lambda _event: settle(_typed(entry.get())),
            )
            tk.Button(
                row,
                text="Send",
                width=8,
                command=lambda: settle(_typed(entry.get())),
            ).pack(side="left", padx=4)

        tk.Button(
            row, text="Dismiss", width=10, command=lambda: settle(AskOutcome.dismissed())
        ).pack(side="right", padx=4)

        # Closing the window says "not here" — the server should try elsewhere
        # rather than wait out the clock.
        root.protocol("WM_DELETE_WINDOW", lambda: settle(AskOutcome.dismissed()))

        remaining = [int(message.timeout_s)]

        def tick() -> None:
            if result:
                return
            if remaining[0] <= 0:
                settle(AskOutcome.timed_out())
                return
            countdown_label.configure(text=f"{remaining[0]}s")
            remaining[0] -= 1
            root.after(1000, tick)

        tick()

        try:
            root.lift()
            root.focus_force()
            if entry is not None:
                entry.focus_set()
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
        return result[0] if result else AskOutcome.dismissed()


def _typed(value: object) -> AskOutcome:
    text = str(value or "").strip()
    return AskOutcome.answered(text) if text else AskOutcome.dismissed()


class TerminalAsker(Asker):
    """A TTY prompt, for a machine with no display.

    ``input()`` cannot be interrupted, so the read runs on a daemon thread and
    the *caller* enforces the timeout — exactly the shape
    :class:`jarvis_desktop.consent.TerminalConsentGateway` uses, including the
    stdin lock: a prompt that timed out leaves its reader blocked in
    ``readline()`` forever, and two readers racing for one keystroke means an
    answer meant for one question can be consumed by another.
    """

    name = "terminal"

    def __init__(self, stream: Any = None, out: Any = None) -> None:
        self._in = stream
        self._out = out
        self._stdin_lock = threading.Lock()

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

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        typed: list[str] = []
        done = threading.Event()

        if not self._stdin_lock.acquire(blocking=False):
            _LOGGER.warning(
                "a previous terminal question is still waiting for input; "
                "reporting undeliverable so the server tries elsewhere"
            )
            return AskOutcome.undeliverable()

        def read() -> None:
            out = self._stdout()
            try:
                print("\n" + render_question(message), file=out, flush=True)
                print("> ", end="", file=out, flush=True)
                typed.append(self._stdin().readline() or "")
            except Exception:  # noqa: BLE001
                typed.append("")
            finally:
                done.set()
                self._stdin_lock.release()

        thread = threading.Thread(target=read, name="jarvis-companion-ask", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._stdin_lock.release()
            return AskOutcome.undeliverable()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(done.wait, message.timeout_s + 1.0),
                timeout=message.timeout_s,
            )
        except asyncio.TimeoutError:
            print("\n(no answer - timed out)", file=self._stdout(), flush=True)
            return AskOutcome.timed_out()
        if not typed:
            return AskOutcome.timed_out()
        return _parse_typed_answer(typed[0], message)


def _parse_typed_answer(raw: str, message: CompanionMessage) -> AskOutcome:
    """Map a typed line onto an option, an index, a free answer, or a dismissal."""
    text = (raw or "").strip()
    if not text:
        return AskOutcome.dismissed()
    if message.options:
        lowered = text.lower()
        for option in message.options:
            if option.lower() == lowered:
                return AskOutcome.answered(option)
        if text.isdigit():
            index = int(text) - 1
            if 0 <= index < len(message.options):
                return AskOutcome.answered(message.options[index])
        # A typed answer the server did not offer is still the user's answer.
        # Passing it through unchanged is the honest thing: the server chose
        # to constrain the question, so the server can decide what to do with
        # an answer outside its list.
    return AskOutcome.answered(text)


def render_question(message: CompanionMessage, width: int = 72) -> str:
    """The exact text the terminal backend prints. Pure — the tests read it."""
    rule = "=" * width
    lines = [rule, "  JARVIS", rule]
    for line in _wrap(message.text or "(no message)", width - 4):
        lines.append(f"  {line}")
    if message.options:
        lines.append("")
        for index, option in enumerate(message.options, start=1):
            lines.append(f"    {index}) {option}")
        lines.append("")
        lines.append(f"  Type an option or its number ({message.timeout_s:g}s), "
                     "or press Enter to dismiss.")
    else:
        lines.append("")
        lines.append(
            f"  Type an answer ({message.timeout_s:g}s), or press Enter to dismiss."
        )
    lines.append(rule)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    cleaned = " ".join(str(text).split()) or "(no message)"
    return textwrap.wrap(cleaned, width=max(20, width)) or ["(no message)"]


# --- the handler ------------------------------------------------------------

SendFn = Callable[[dict], Awaitable[bool]]


@dataclass
class _Settled:
    status: str
    frame: dict


class CompanionHandler:
    """Routes one ``jarvis_message`` per mode and answers it exactly once.

    ``send`` is the only thing this class is handed and it reaches the channel
    and nothing else. There is no registry, no dispatcher and no policy store
    in this file — which is what makes "a proactive message cannot run
    anything" a property of the wiring rather than a rule someone has to
    remember.
    """

    def __init__(
        self,
        send: SendFn,
        *,
        notifier: Notifier | None = None,
        asker: Asker | None = None,
        speaker: Speaker | None = None,
        on_interaction: Callable[[], None] | None = None,
        max_remembered: int = MAX_REMEMBERED,
        backstop_grace_s: float = BACKSTOP_GRACE_S,
    ) -> None:
        self._send = send
        self.notifier = notifier or build_notifier()
        self.asker = asker or build_asker()
        self.speaker = speaker or build_speaker()
        self._on_interaction = on_interaction
        self._max_remembered = max(1, max_remembered)
        self._backstop_grace_s = max(0.0, backstop_grace_s)

        #: message_id -> the answer already sent. The single "have we answered
        #: this?" ledger; every send path consults it.
        self._settled: dict[str, _Settled] = {}
        #: Ids currently being shown. A redelivery of one of these is ignored
        #: outright rather than raising a second prompt.
        self._inflight: set[str] = set()
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()

    # --- inbound ----------------------------------------------------------

    def handles(self, frame: Mapping[str, Any]) -> bool:
        return str(frame.get("type") or "") == TYPE_MESSAGE

    async def handle(self, frame: Mapping[str, Any]) -> dict | None:
        """Parse one ``jarvis_message`` and deliver it.

        Returns the result frame that was sent, or None when there was nothing
        to answer (no ``message_id``) or the message is already on screen.
        """
        message = CompanionMessage.parse(frame)
        if message is None:
            # No id means no way to answer, and no way for the server to match
            # a reply. Dropping is the only option that does not invent one.
            _LOGGER.warning("ignoring a jarvis_message with no usable message_id")
            return None
        return await self.deliver(message)

    async def deliver(self, message: CompanionMessage) -> dict | None:
        """Show one already-parsed message and answer it exactly once."""
        async with self._lock:
            settled = self._settled.get(message.message_id)
            if settled is not None:
                # A redelivery. Replay the stored answer; prompt nothing. The
                # server may never have seen the first one, and replaying is
                # cheaper for it than a question the user answers twice.
                _LOGGER.info(
                    "replaying the stored %s for %s", settled.status, message.message_id
                )
                await self._transmit(settled.frame)
                return settled.frame
            if message.message_id in self._inflight:
                _LOGGER.debug(
                    "%s is already on screen; ignoring the redelivery",
                    message.message_id,
                )
                return None
            self._inflight.add(message.message_id)

        try:
            outcome = await self._route(message)
        except asyncio.CancelledError:
            # Shutting down mid-question. Say nothing: the server got no
            # answer, so a redelivery after reconnect is free to ask again.
            async with self._lock:
                self._inflight.discard(message.message_id)
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.warning("companion delivery blew up", exc_info=True)
            outcome = AskOutcome.undeliverable()

        async with self._lock:
            self._inflight.discard(message.message_id)
        return await self._answer(message.message_id, outcome)

    def handle_background(self, frame: Mapping[str, Any]) -> None:
        """Fire-and-forget :meth:`handle`, off the socket's read loop.

        A question can sit on screen for two minutes, and a blocked read loop
        is a channel that cannot even receive a cancellation.
        """
        task = asyncio.create_task(self.handle(dict(frame)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _route(self, message: CompanionMessage) -> AskOutcome:
        if message.mode == MODE_ASK:
            return await self._ask(message)
        if message.mode == MODE_SPEAK:
            return await self._speak(message)
        if message.mode == MODE_NOTIFY:
            return self._notify(message)
        # The server sent a mode this device does not implement, and the kind
        # did not rescue it. Say so rather than guessing at an interpretation
        # of a frame we do not understand.
        _LOGGER.warning("unknown companion mode %r; reporting undeliverable", message.mode)
        return AskOutcome.undeliverable()

    async def _ask(self, message: CompanionMessage) -> AskOutcome:
        if not message.text.strip():
            return AskOutcome.undeliverable()
        try:
            # The backend owns the countdown; this is only a backstop for a
            # backend whose own timer never fires.
            outcome = await asyncio.wait_for(
                self.asker.ask(message),
                timeout=message.timeout_s + self._backstop_grace_s,
            )
        except asyncio.TimeoutError:
            return AskOutcome.timed_out()
        if outcome.status not in VALID_STATUSES:
            _LOGGER.warning("ask backend returned %r; treating it as dismissed", outcome.status)
            return AskOutcome.dismissed()
        if outcome.status == STATUS_ANSWERED and self._on_interaction is not None:
            # Answering a question is the strongest presence signal there is.
            try:
                self._on_interaction()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("presence hook failed", exc_info=True)
        return outcome

    async def _speak(self, message: CompanionMessage) -> AskOutcome:
        if not message.text.strip():
            return AskOutcome.undeliverable()
        try:
            spoken = await self.speaker.speak(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.debug("speech failed; falling back to a notification", exc_info=True)
            spoken = False
        if spoken:
            return AskOutcome.answered("")
        # No audio, or it failed. A notification is a downgrade, not a drop —
        # the same rule the server applies when it routes.
        return self._notify(message)

    def _notify(self, message: CompanionMessage) -> AskOutcome:
        urgency = "critical" if message.importance == "critical" else "normal"
        if message.importance == "low":
            urgency = "low"
        text = message.text.strip() or "(no message)"
        try:
            shown = self.notifier.notify("Jarvis", text, urgency)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("notifier failed", exc_info=True)
            shown = False
        return AskOutcome.answered("") if shown else AskOutcome.undeliverable()

    # --- outbound ---------------------------------------------------------

    async def _answer(self, message_id: str, outcome: AskOutcome) -> dict:
        frame = result_frame(message_id, outcome.status, outcome.answer)
        async with self._lock:
            settled = self._settled.get(message_id)
            if settled is not None:
                # Somebody answered this while we were working. One answer per
                # id, and the first one is the one that counts.
                return settled.frame
            self._remember(message_id, outcome.status, frame)
        await self._transmit(frame)
        return frame

    async def report_unknown(self, message_id: str) -> dict | None:
        """Answer ``undeliverable`` for an id this device cannot honour.

        Used when something outside the handler holds a ``message_id`` that no
        longer maps to a live question — a dialog that outlived a restart, a
        redelivery after the queue was cleared. Reporting it lets the server
        escalate; staying quiet leaves it waiting for the full timeout.
        """
        clean = str(message_id or "").strip()
        if not clean:
            return None
        async with self._lock:
            if clean in self._settled or clean in self._inflight:
                return self._settled.get(clean, _Settled("", {})).frame or None
            frame = result_frame(clean, STATUS_UNDELIVERABLE)
            self._remember(clean, STATUS_UNDELIVERABLE, frame)
        await self._transmit(frame)
        return frame

    def _remember(self, message_id: str, status: str, frame: dict) -> None:
        self._settled[message_id] = _Settled(status, frame)
        while len(self._settled) > self._max_remembered:
            # dicts are insertion-ordered, so this drops the oldest.
            self._settled.pop(next(iter(self._settled)))

    async def _transmit(self, frame: dict) -> bool:
        try:
            return bool(await self._send(frame))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.debug("could not send a jarvis_message_result", exc_info=True)
            return False

    # --- introspection ----------------------------------------------------

    def status_of(self, message_id: str) -> str | None:
        settled = self._settled.get(message_id)
        return settled.status if settled else None

    @property
    def settled_count(self) -> int:
        return len(self._settled)

    @property
    def in_flight(self) -> int:
        return len(self._inflight)

    async def close(self) -> None:
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def describe(self) -> str:
        return (
            f"companion: ask via {self.asker.name}, notify via {self.notifier.name}, "
            f"speech via {self.speaker.name if self.speaker.usable() else 'none'}"
        )


def result_frame(message_id: str, status: str, answer: str | None = None) -> dict:
    """The one wire shape this module produces.

    An unrecognised status becomes ``undeliverable``: a garbled answer must
    never read as ``answered``, which is the only status that stops the server
    escalating.
    """
    frame: dict[str, Any] = {
        "type": TYPE_RESULT,
        "message_id": message_id,
        "status": status if status in VALID_STATUSES else STATUS_UNDELIVERABLE,
    }
    if frame["status"] == STATUS_ANSWERED:
        frame["answer"] = "" if answer is None else str(answer)[:MAX_TEXT]
    return frame


# --- construction -----------------------------------------------------------


def build_notifier() -> Notifier:
    desktop = CommandNotifier()
    return desktop if desktop.usable() else LogNotifier()


def build_asker(headless: bool = False) -> Asker:
    """tkinter, then a TTY, then nothing. ``headless`` skips straight to
    nothing so a service nobody is watching reports ``undeliverable`` at once
    instead of burning the server's timeout first."""
    if headless:
        return UnavailableAsker()
    return ChainAsker(TkAsker(), TerminalAsker(), UnavailableAsker())


def build_speaker() -> Speaker:
    speaker = CommandSpeaker()
    return speaker if speaker.usable() else SilentSpeaker()


# --- small helpers ----------------------------------------------------------


def _run_argv(argv: list[str], timeout: float = 10.0) -> int:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    return proc.returncode


def _applescript(text: str) -> str:
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _speech_backend() -> list[str] | None:
    """The argv prefix for a local text-to-speech command, or None."""
    system = platform.system()
    if system == "Darwin" and shutil.which("say"):
        return ["say", "--"]
    if system == "Linux":
        for tool in ("spd-say", "espeak-ng", "espeak"):
            if shutil.which(tool):
                return [tool, "--"] if tool == "spd-say" else [tool]
    if system == "Windows":
        return ["powershell", "-NoProfile", "-Command"]
    return None


def _local_say(text: str) -> bool:
    argv = _speech_backend()
    if argv is None:
        return False
    if argv[0] == "powershell":
        # Single-quoted PowerShell literal: doubling the quote is the whole
        # escape, and the text is server-supplied, so it matters.
        safe = str(text).replace("'", "''")
        argv = argv + [
            "Add-Type -AssemblyName System.Speech;"
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
            f".Speak('{safe}')"
        ]
    else:
        argv = argv + [str(text)]
    return _run_argv(argv, timeout=60.0) == 0


