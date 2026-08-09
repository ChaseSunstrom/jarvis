"""Presence signals: telling jarvis-core whether the user is actually here.

The server's :mod:`jarvis.presence` ranks devices by how likely you are to
see or hear something *right now*, and it can only rank what the devices
report. This module is this machine's half of that: sample a handful of cheap
signals, and push them up as a ``device_event`` with event ``presence``.

::

    {"type": "device_event", "event": "presence",
     "data": {"screen_on": true, "locked": false, "last_interaction": 1.7e9,
              "audio_available": true, "muted": false,
              "battery": 82, "charging": true}}

The keys are exactly the attribute names on ``jarvis.presence.DevicePresence``
— that struct's ``update()`` sets whatever it recognises and ignores the rest,
so a field this machine cannot determine is simply left out rather than
guessed at. **Nothing here is a command and nothing here is trusted input**:
it is one-way telemetry, it never carries user content, and no reply to it can
cause anything to run.

Two properties matter more than accuracy:

* **It is not a firehose.** A report goes out on a *meaningful* change or once
  every :data:`HEARTBEAT_S`, and never more often than :data:`MIN_INTERVAL_S`.
  "Idle went from 41.2s to 46.3s" is not a change; "the screen locked" is.
  The comparison is against the last snapshot that was actually *sent*, so a
  change suppressed by the rate floor still goes out on the next poll rather
  than being lost.
* **It fails quiet.** Every probe is best effort. A machine with no
  ``xprintidle``, no ``loginctl`` and no display reports what it knows and
  omits the rest; nothing in this module raises into the caller's loop, and a
  probe that has failed once is not retried on every poll.

The per-OS probes are injectable, which is how ``tests/test_companion.py``
exercises the throttle with no clock, no subprocess and no display.

## Wiring

``cmd_run`` already builds the ``emit`` closure the trigger layer uses; the
reporter takes the same one and nothing else::

    presence = PresenceReporter(emit)
    await presence.start()
    ...
    await presence.stop()
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "EVENT_PRESENCE",
    "HEARTBEAT_S",
    "MIN_INTERVAL_S",
    "POLL_INTERVAL_S",
    "ACTIVE_WITHIN_S",
    "PresenceSignals",
    "PresenceSampler",
    "PresenceReporter",
    "meaningful_change",
    "should_send",
    "screen_locked",
    "display_present",
    "audio_available",
    "audio_muted",
    "battery_state",
]

#: The ``device_event`` name the server routes into its presence registry.
EVENT_PRESENCE = "presence"

#: Send at least this often, even when nothing changed. The server treats a
#: device with no signal for 15 minutes as ABSENT, so this has to be well
#: inside that.
HEARTBEAT_S = 60.0

#: Never send two reports closer together than this, whatever changed.
MIN_INTERVAL_S = 3.0

#: How often the reporter *looks*. Looking is cheap; sending is what is
#: throttled.
POLL_INTERVAL_S = 5.0

#: Mirrors ``jarvis.presence.ACTIVE_WITHIN``. Crossing it is the difference
#: between "at the keyboard" and "in the room", so it is a meaningful change.
ACTIVE_WITHIN_S = 120.0

#: Battery moves constantly. Only a step this large is worth a frame.
BATTERY_STEP = 5

#: Flags whose value changing is, on its own, worth a report.
_TRACKED_FLAGS = (
    "screen_on",
    "locked",
    "audio_available",
    "muted",
    "driving",
    "charging",
    "zone",
)


# --- the signals ------------------------------------------------------------


@dataclass(frozen=True)
class PresenceSignals:
    """One sample. Frozen so a stored snapshot cannot drift under comparison."""

    #: A graphical session is attached. A desktop cannot cheaply tell whether
    #: the monitor is powered, so this is the honest proxy; `locked` and
    #: `last_interaction` carry the presence signal that actually matters.
    screen_on: bool = False
    #: True when the session is locked. None when this machine cannot say —
    #: which is reported as "not locked", because claiming a lock that is not
    #: there would hide messages from a user who is sitting right there.
    locked: bool = False
    #: Epoch seconds of the last keyboard/mouse input, or 0.0 when unknown.
    last_interaction: float = 0.0
    #: Seconds since that input, or None when this machine cannot say.
    idle_s: float | None = None
    audio_available: bool = False
    muted: bool = False
    battery: int | None = None
    charging: bool | None = None
    #: A desktop is not a car. Present so the shape matches the phone's.
    driving: bool = False
    zone: str | None = None

    @property
    def active(self) -> bool | None:
        """Did the user touch this machine recently? None when unknown."""
        if self.idle_s is None:
            return None
        return self.idle_s <= ACTIVE_WITHIN_S

    def as_event(self) -> dict[str, Any]:
        """The ``data`` block of the ``presence`` device_event.

        Fields this machine could not determine are omitted rather than sent
        as null: the server's ``DevicePresence.update()`` skips ``None``
        anyway, and leaving them out keeps the frame honest about what was
        actually measured.
        """
        data: dict[str, Any] = {
            "screen_on": self.screen_on,
            "locked": self.locked,
            "audio_available": self.audio_available,
            "muted": self.muted,
            "driving": self.driving,
        }
        if self.last_interaction:
            data["last_interaction"] = round(self.last_interaction, 3)
        if self.idle_s is not None:
            data["idle_s"] = round(self.idle_s, 1)
        if self.battery is not None:
            data["battery"] = self.battery
        if self.charging is not None:
            data["charging"] = self.charging
        if self.zone:
            data["zone"] = self.zone
        return data


def meaningful_change(before: PresenceSignals | None, after: PresenceSignals) -> str | None:
    """Name of the first signal worth a frame, or None if nothing moved.

    Deliberately does NOT look at ``last_interaction`` or ``idle_s`` directly:
    those change every second the machine is in use, and reporting each tick
    would be the firehose this whole module exists to avoid. What matters is
    the *edge* — the user arriving or going quiet — which is ``active``.
    """
    if before is None:
        return "first report"
    for name in _TRACKED_FLAGS:
        if getattr(before, name) != getattr(after, name):
            return name
    if before.active != after.active:
        return "active"
    if _battery_moved(before.battery, after.battery):
        return "battery"
    return None


def _battery_moved(before: int | None, after: int | None) -> bool:
    if after is None:
        return False
    if before is None:
        return True
    return abs(after - before) >= BATTERY_STEP


def should_send(
    before: PresenceSignals | None,
    after: PresenceSignals,
    since_sent_s: float,
    heartbeat_s: float = HEARTBEAT_S,
    min_interval_s: float = MIN_INTERVAL_S,
) -> str | None:
    """Why this sample should be sent, or None to stay quiet.

    Order is the policy:

      1. never reported anything yet -> send;
      2. the heartbeat is due -> send, changed or not;
      3. inside the rate floor -> stay quiet *whatever* changed (the change is
         still pending against the last sent snapshot, so the next poll sends
         it);
      4. otherwise, send only on a meaningful change.
    """
    if before is None:
        return "first report"
    if since_sent_s >= heartbeat_s:
        return "heartbeat"
    if since_sent_s < min_interval_s:
        return None
    return meaningful_change(before, after)


# --- per-OS probes ----------------------------------------------------------


def _run(argv: list[str], timeout: float = 3.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout or ""


def display_present() -> bool:
    """Is there a graphical session attached to this process?"""
    if os.name == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def screen_locked() -> bool | None:
    """True/False when this machine can say, None when it cannot.

    ``loginctl`` first because it is the only one that works under both X11 and
    Wayland; then the two desktop screensaver services; then macOS's session
    dictionary; then the Windows input desktop, which cannot be opened while
    the lock screen owns it.
    """
    system = platform.system()
    if system == "Linux":
        if shutil.which("loginctl"):
            session = os.environ.get("XDG_SESSION_ID") or "self"
            code, out = _run(["loginctl", "show-session", session, "-p", "LockedHint"])
            if code == 0:
                text = out.strip().lower()
                if text.endswith("=yes"):
                    return True
                if text.endswith("=no"):
                    return False
        if shutil.which("gdbus"):
            for dest, path in (
                ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
            ):
                code, out = _run(
                    [
                        "gdbus", "call", "--session", "--dest", dest,
                        "--object-path", path,
                        "--method", f"{dest}.GetActive",
                    ]
                )
                if code == 0 and "true" in out.lower():
                    return True
                if code == 0 and "false" in out.lower():
                    return False
        return None
    if system == "Darwin":
        if shutil.which("ioreg"):
            code, out = _run(["ioreg", "-n", "Root", "-d1", "-a"])
            if code == 0 and "CGSSessionScreenIsLocked" in out:
                # The key is present only while locked in most macOS builds;
                # when it is present with an explicit value, believe the value.
                match = re.search(
                    r"CGSSessionScreenIsLocked</key>\s*<(true|false)/>", out
                )
                if match:
                    return match.group(1) == "true"
                return True
            if code == 0:
                return False
        return None
    if os.name == "nt":
        try:
            import ctypes

            desktop = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0100)  # type: ignore[attr-defined]
            if not desktop:
                return True
            ctypes.windll.user32.CloseDesktop(desktop)  # type: ignore[attr-defined]
            return False
        except Exception:  # noqa: BLE001
            return None
    return None


def audio_available() -> bool:
    """Can this machine make a noise the user would hear?"""
    system = platform.system()
    if system in ("Darwin", "Windows"):
        return True
    if os.name == "nt":
        return True
    for tool in ("pactl", "wpctl", "aplay", "paplay"):
        if shutil.which(tool):
            return True
    # A soundcard node is the last-resort check for a machine with no
    # userspace audio tooling installed.
    return os.path.exists("/dev/snd") or os.path.exists("/proc/asound/cards")


def audio_muted() -> bool | None:
    """True when the default output is muted, None when unknown."""
    system = platform.system()
    if system == "Linux":
        if shutil.which("pactl"):
            code, out = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            if code == 0:
                lowered = out.strip().lower()
                if "yes" in lowered:
                    return True
                if "no" in lowered:
                    return False
        if shutil.which("wpctl"):
            code, out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if code == 0:
                return "muted" in out.lower()
        return None
    if system == "Darwin" and shutil.which("osascript"):
        code, out = _run(["osascript", "-e", "output muted of (get volume settings)"])
        if code == 0:
            return out.strip().lower() == "true"
        return None
    return None


def battery_state() -> tuple[int | None, bool | None]:
    """``(percent, charging)``. Either half may be None."""
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        psutil = None  # type: ignore[assignment]
    if psutil is not None:
        try:
            battery = psutil.sensors_battery()
        except Exception:  # noqa: BLE001
            battery = None
        if battery is not None:
            percent = int(round(battery.percent))
            return max(0, min(100, percent)), bool(battery.power_plugged)
    # Linux without psutil: /sys is right there.
    base = "/sys/class/power_supply"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None, None
    for name in names:
        if not name.upper().startswith("BAT"):
            continue
        percent = _read_int(os.path.join(base, name, "capacity"))
        status = _read_text(os.path.join(base, name, "status"))
        charging = None
        if status:
            charging = status.strip().lower() in ("charging", "full")
        return percent, charging
    return None, None


def _read_int(path: str) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return max(0, min(100, int(text.strip())))
    except ValueError:
        return None


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


# --- sampling ---------------------------------------------------------------


class PresenceSampler:
    """Turns the probes above into a :class:`PresenceSignals`.

    Every probe is injectable and every probe is allowed to fail. A probe that
    returns None once is remembered as "this machine cannot answer that" and is
    not called again — shelling out to a tool that is not installed, five times
    a minute, forever, is exactly the kind of thing that makes an agent
    unwelcome on a laptop.
    """

    def __init__(
        self,
        *,
        idle_probe: Callable[[], float | None] | None = None,
        lock_probe: Callable[[], bool | None] | None = None,
        display_probe: Callable[[], bool] | None = None,
        audio_probe: Callable[[], bool] | None = None,
        mute_probe: Callable[[], bool | None] | None = None,
        battery_probe: Callable[[], tuple[int | None, bool | None]] | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        if idle_probe is None:
            # Reuse the trigger layer's per-OS idle detection rather than
            # growing a second copy that can drift from it.
            from .triggers import system_idle_seconds

            idle_probe = system_idle_seconds
        self._idle = idle_probe
        self._lock = lock_probe or screen_locked
        self._display = display_probe or display_present
        self._audio = audio_probe or audio_available
        self._mute = mute_probe or audio_muted
        self._battery = battery_probe or battery_state
        self._wall = wall

        self._idle_dead = False
        self._lock_dead = False
        self._mute_dead = False
        self._audio_cached: bool | None = None

        #: Set by the agent when it knows the user just did something here
        #: (answered a companion question, approved a prompt). Beats a probe
        #: that cannot see it — a tkinter dialog is not keyboard input.
        self.interaction_at: float = 0.0

        #: None follows the system; True/False is an explicit override, which
        #: is what ``companion.set_muted`` amounts to on this device.
        self.muted_override: bool | None = None

    def note_interaction(self, when: float | None = None) -> None:
        self.interaction_at = when if when is not None else self._wall()

    def sample(self) -> PresenceSignals:
        now = self._wall()
        idle = self._probe_idle()
        last_interaction = 0.0
        if idle is not None:
            last_interaction = now - idle
        if self.interaction_at > last_interaction:
            last_interaction = self.interaction_at
            idle = max(0.0, now - self.interaction_at)

        locked = self._probe_lock()
        display = _safe(self._display, False)
        audio = self._probe_audio()
        muted = self.muted_override
        if muted is None:
            muted = self._probe_mute() or False
        battery, charging = _safe(self._battery, (None, None))

        return PresenceSignals(
            screen_on=bool(display),
            # An unknown lock state reports as unlocked: inventing a lock would
            # hide messages from a user who is sitting right in front of it.
            locked=bool(locked),
            last_interaction=last_interaction,
            idle_s=idle,
            audio_available=bool(audio),
            muted=bool(muted),
            battery=battery,
            charging=charging,
        )

    # --- probe plumbing ---------------------------------------------------

    def _probe_idle(self) -> float | None:
        if self._idle_dead:
            return None
        value = _safe(self._idle, None)
        if value is None:
            self._idle_dead = True
            _LOGGER.debug("idle detection is unavailable here; not asking again")
            return None
        return max(0.0, float(value))

    def _probe_lock(self) -> bool | None:
        if self._lock_dead:
            return None
        value = _safe(self._lock, None)
        if value is None:
            self._lock_dead = True
            _LOGGER.debug("lock detection is unavailable here; not asking again")
        return value

    def _probe_mute(self) -> bool | None:
        if self._mute_dead:
            return None
        value = _safe(self._mute, None)
        if value is None:
            self._mute_dead = True
        return value

    def _probe_audio(self) -> bool:
        if self._audio_cached is None:
            self._audio_cached = bool(_safe(self._audio, False))
        return self._audio_cached


def _safe(fn: Callable[[], Any], fallback: Any) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a probe must never break the loop
        _LOGGER.debug("a presence probe failed", exc_info=True)
        return fallback


# --- the reporter -----------------------------------------------------------

EmitFn = Callable[[str, dict], Awaitable[bool]]


class PresenceReporter:
    """Samples on a timer and emits when it is worth emitting.

    ``emit`` is the only thing this class is handed, and it reaches the channel
    and nothing else — the same shape the trigger layer uses. There is no
    reference to the action registry here, so presence telemetry cannot cause
    anything to run on this machine.
    """

    def __init__(
        self,
        emit: EmitFn,
        sampler: PresenceSampler | None = None,
        *,
        heartbeat_s: float = HEARTBEAT_S,
        min_interval_s: float = MIN_INTERVAL_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self.sampler = sampler or PresenceSampler()
        self.heartbeat_s = heartbeat_s
        self.min_interval_s = min_interval_s
        self.poll_interval_s = poll_interval_s
        self._clock = clock

        #: The snapshot the server has. Compared against, so a change dropped
        #: by the rate floor is still pending on the next poll.
        self.last_sent: PresenceSignals | None = None
        self._last_sent_at: float = 0.0
        self._sends = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    @property
    def sends(self) -> int:
        """How many frames have actually gone out. The throttle's scoreboard."""
        return self._sends

    def set_muted(self, muted: bool | None) -> None:
        """Explicit quiet on this device; None hands it back to the system."""
        self.sampler.muted_override = muted

    def note_interaction(self, when: float | None = None) -> None:
        self.sampler.note_interaction(when)

    async def poll(self, force: bool = False) -> str | None:
        """Sample once and maybe send. Returns the reason it sent, or None."""
        signals = self.sampler.sample()
        elapsed = self._clock() - self._last_sent_at if self.last_sent else 0.0
        reason = (
            "forced"
            if force
            else should_send(
                self.last_sent, signals, elapsed, self.heartbeat_s, self.min_interval_s
            )
        )
        if reason is None:
            return None
        try:
            delivered = await self._emit(EVENT_PRESENCE, signals.as_event())
        except Exception:  # noqa: BLE001 - a dead socket is not our problem
            _LOGGER.debug("presence emit failed", exc_info=True)
            return None
        if not delivered:
            # Dropped by the rate limit or the socket. Do NOT record it as
            # sent: the server still has the old snapshot, so the change is
            # still pending and the next poll will try again.
            return None
        self.last_sent = signals
        self._last_sent_at = self._clock()
        self._sends += 1
        _LOGGER.debug("presence reported (%s)", reason)
        return reason

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Poll until ``stop`` is set."""
        stopper = stop or self._stop
        while not stopper.is_set():
            await self.poll()
            try:
                await asyncio.wait_for(stopper.wait(), timeout=max(1.0, self.poll_interval_s))
                return
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self.run(self._stop))

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def describe(self) -> str:
        signals = self.last_sent
        if signals is None:
            return "presence: nothing reported yet"
        state = "locked" if signals.locked else "unlocked"
        idle = "unknown" if signals.idle_s is None else f"{signals.idle_s:.0f}s idle"
        return (
            f"presence: {state}, {idle}, "
            f"audio {'on' if signals.audio_available else 'off'}"
            f"{', muted' if signals.muted else ''}"
        )
