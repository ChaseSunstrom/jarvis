"""What the running agent is doing, for a second terminal to read.

Once ``python -m jarvis_desktop run`` is going there was no way to see whether
it was connected, what it had just done, or what it had refused. The audit log
answers the last two after the fact and ``doctor`` answers what the machine
*could* do; nothing at all answered "is it up, and is it talking to the
server?", which is the question people actually ask. ``--log-file`` is not an
answer to it.

The fewest moving parts that are: the daemon rewrites one small JSON file in the
state directory every few seconds, and ``python -m jarvis_desktop status`` reads
it. Explicitly not:

* **a tray icon** — a new hard dependency (there is exactly one, ``websockets``)
  and a GUI event loop inside a process that already runs an asyncio loop plus
  Tk dialogs on threads of their own;
* **a socket** — a listening port on a desktop agent is an attack surface, and
  this feature only ever reports;
* **a second process** — nothing here is worth a supervisor.

The file deliberately does not contain a copy of what ran. The audit log already
records every dispatch, redacted, and a second copy in a second format is a
second thing to keep honest; :func:`render` reads both and prints them together.

Staleness is the interesting half. A status file is a claim that the agent was
alive when it was written, and nothing rewrites it when the process is killed —
so a file on its own means "an agent ran here", not "an agent is running here".
:meth:`StatusSnapshot.stale` and :func:`process_alive` are what turn the first
into the second.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "STATUS_INTERVAL_S",
    "StatusSnapshot",
    "StatusFile",
    "StatusWriter",
    "process_alive",
    "render",
]

#: How often the daemon rewrites the file. Frequent enough that "stale" means
#: something within a minute, rare enough to be free.
STATUS_INTERVAL_S = 5.0

#: A file older than this many intervals is not being maintained by anybody.
#: Three, so a single slow poll — a laptop coming back from suspend, a heavily
#: loaded box — does not read as a dead agent.
STALE_INTERVALS = 3.0

#: ...but never call a file stale sooner than this, whatever the interval says.
MIN_STALE_S = 20.0


@dataclass(frozen=True)
class StatusSnapshot:
    """One "the agent was alive and here is what it knew" record.

    Frozen: the writer builds a fresh one per tick from live state rather than
    mutating a shared object, so a half-updated snapshot can never be written.
    """

    pid: int = 0
    version: str = ""
    device_id: str = ""
    device_name: str = ""
    server_url: str = ""
    #: The consent backend's ``name``. Worth having here rather than deriving it
    #: at read time: what matters is the one the *running* process chose, which
    #: is not necessarily what a fresh probe would choose now.
    consent_backend: str = ""
    action_count: int = 0
    #: True only while a session is authenticated and registered. A process that
    #: is up but reconnecting is a real and common state, and reporting it as
    #: "running" without qualification is how people end up debugging the wrong
    #: end of the connection.
    connected: bool = False
    started_at: float = 0.0
    updated_at: float = 0.0
    interval_s: float = STATUS_INTERVAL_S

    def to_json(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "version": self.version,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "server_url": self.server_url,
            "consent_backend": self.consent_backend,
            "action_count": self.action_count,
            "connected": self.connected,
            "started_at": round(self.started_at, 3),
            "updated_at": round(self.updated_at, 3),
            "interval_s": self.interval_s,
        }

    @staticmethod
    def from_json(obj: dict[str, Any]) -> "StatusSnapshot":
        """Every field is defaulted and coerced.

        A status file is written by one version of the agent and read by
        whichever one the user happens to invoke, so a missing or renamed key
        has to degrade to "unknown" rather than crash the one command someone
        runs when something is already wrong.
        """

        def number(key: str, fallback: float = 0.0) -> float:
            try:
                return float(obj.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        return StatusSnapshot(
            pid=int(number("pid")),
            version=str(obj.get("version") or ""),
            device_id=str(obj.get("device_id") or ""),
            device_name=str(obj.get("device_name") or ""),
            server_url=str(obj.get("server_url") or ""),
            consent_backend=str(obj.get("consent_backend") or ""),
            action_count=int(number("action_count")),
            connected=bool(obj.get("connected", False)),
            started_at=number("started_at"),
            updated_at=number("updated_at"),
            interval_s=number("interval_s", STATUS_INTERVAL_S) or STATUS_INTERVAL_S,
        )

    def age_s(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.updated_at)

    def stale(self, now: float | None = None) -> bool:
        """Has nobody refreshed this recently enough to believe it?"""
        limit = max(MIN_STALE_S, self.interval_s * STALE_INTERVALS)
        return self.age_s(now) > limit

    def uptime_s(self, now: float | None = None) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, (time.time() if now is None else now) - self.started_at)


class StatusFile:
    """Reads and writes the one file. Never raises at the caller.

    An unwritable state directory must not stop the agent serving commands, and
    an unreadable status file must not stop ``status`` printing the audit log —
    this is a reporting surface, and a reporting surface that can break the
    thing it reports on is worse than no reporting surface.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, snapshot: StatusSnapshot) -> bool:
        payload = json.dumps(snapshot.to_json(), indent=2, sort_keys=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            # Replaced rather than truncated-and-rewritten: `status` in another
            # terminal reads this on a timer, and a half-written file read at
            # the wrong instant is a parse error reported as a dead agent.
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            return True
        except OSError:
            _LOGGER.debug("could not write the status file", exc_info=True)
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def read(self) -> StatusSnapshot | None:
        """The stored snapshot, or None when there is not a usable one."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        return StatusSnapshot.from_json(raw)

    def clear(self) -> None:
        """Remove the file on a clean shutdown.

        An agent that has stopped is not running, and the honest way to say so
        is to leave nothing behind claiming otherwise. A *killed* agent cannot
        do this, which is what the staleness check is for.
        """
        try:
            self.path.unlink()
        except OSError:
            pass


def process_alive(pid: int) -> bool | None:
    """True/False when this machine can tell, None when it cannot.

    POSIX only, on purpose. On Windows ``os.kill(pid, 0)`` does not mean "does
    this process exist": CPython maps *every* signal there onto
    ``TerminateProcess``, so the probe that is harmless everywhere else would
    kill the very agent it is asking about. Windows gets the staleness check and
    nothing more.
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists; it belongs to another user. That is still "alive".
        return True
    except OSError:
        return None
    return True


class StatusWriter:
    """Rewrites the status file on a timer for as long as the agent runs.

    Takes a callable rather than the channel, the registry or the config: this
    is telemetry about the agent and it has no business being able to reach into
    it. Same shape as :class:`jarvis_desktop.presence.PresenceReporter`, and for
    the same reason.
    """

    def __init__(
        self,
        path: Path,
        snapshot: Callable[[], StatusSnapshot],
        interval_s: float = STATUS_INTERVAL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.file = StatusFile(path)
        self._snapshot = snapshot
        self.interval_s = max(1.0, interval_s)
        self._clock = clock
        self._started_at = clock()
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[Any] | None = None
        self.writes = 0

    def tick(self) -> bool:
        """Take one snapshot and store it. Returns whether it reached disk."""
        try:
            snapshot = self._snapshot()
        except Exception:  # noqa: BLE001 - a broken probe is not worth a crash
            _LOGGER.debug("could not sample the agent's status", exc_info=True)
            return False
        snapshot = replace(
            snapshot,
            pid=os.getpid(),
            started_at=self._started_at,
            updated_at=self._clock(),
            interval_s=self.interval_s,
        )
        if not self.file.write(snapshot):
            return False
        self.writes += 1
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """Write now, then every ``interval_s`` until ``stop`` is set."""
        while not stop.is_set():
            self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
                return
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run(self._stop))

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.file.clear()


# --- rendering --------------------------------------------------------------


def render(
    snapshot: StatusSnapshot | None,
    entries: Iterable[Any] = (),
    *,
    path: Path | None = None,
    panic: bool = False,
    automation_enabled: bool = True,
    now: float | None = None,
) -> str:
    """The text ``status`` prints. Pure, so the tests can read it.

    ``entries`` are :class:`jarvis_desktop.audit.AuditEntry` objects, newest
    first — the same ones ``audit`` shows, because "what did it just do" and
    "what did it just refuse" are the other half of the question and there is
    no reason to make somebody run two commands to get them.
    """
    at = time.time() if now is None else now
    lines: list[str] = []

    if snapshot is None:
        lines.append("no agent is running (no status file)")
        if path is not None:
            lines.append(f"  expected at {path}")
        lines.append("  start one with: python -m jarvis_desktop run")
    else:
        alive = process_alive(snapshot.pid)
        stale = snapshot.stale(at)
        if alive is False:
            state = "NOT RUNNING - the process is gone and left this file behind"
        elif stale:
            state = (
                f"STALE - nothing has updated this for {_duration(snapshot.age_s(at))}; "
                "the agent is wedged, suspended or was killed"
            )
        elif snapshot.connected:
            state = "running, connected to the server"
        else:
            state = "running, NOT connected (reconnecting, or the server is down)"
        lines.append(f"jarvis-desktop {snapshot.version or '?'}: {state}")
        lines.append(f"  device      {snapshot.device_name} ({snapshot.device_id})")
        lines.append(f"  server      {snapshot.server_url}")
        liveness = "" if alive is None else (" (alive)" if alive else " (gone)")
        lines.append(f"  pid         {snapshot.pid}{liveness}")
        lines.append(f"  uptime      {_duration(snapshot.uptime_s(at))}")
        lines.append(f"  updated     {_duration(snapshot.age_s(at))} ago")
        lines.append(f"  consent     {snapshot.consent_backend or 'unknown'}")
        lines.append(f"  actions     {snapshot.action_count}")

    if panic:
        lines.append("  PANIC IS ON - every command is denied until you clear it")
    if not automation_enabled:
        lines.append("  automation is switched off - every command is denied")

    rows = list(entries or ())
    lines.append("")
    if not rows:
        lines.append("nothing has run yet")
    else:
        lines.append(f"the last {len(rows)} action(s), newest first:")
        for entry in rows:
            stamp = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            flag = {"ok": "  ", "denied": "!!", "error": "xx", "unsupported": "--"}.get(
                entry.status, "??"
            )
            lines.append(
                f"  {flag} {stamp}  {entry.action_id:<20} {entry.tier.name:<8} "
                f"{entry.status}"
            )
    return "\n".join(lines)


def _duration(seconds: float) -> str:
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    return f"{total // 86400}d {(total % 86400) // 3600}h"
