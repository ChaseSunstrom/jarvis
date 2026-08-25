"""Where a scenario runs: a harness of our own, or the running stack.

Two grounds, and the difference is the whole of M29:

* :class:`HarnessGround` — a jarvis-core this rig starts, with a throwaway
  house and this repository's fixture web behind it. Deterministic, safe to
  wipe, and the only ground on which a research scenario means anything: its
  answers have to come from pages this repository owns, or the assertion is
  about today's internet.

* :class:`StackGround` — the containers the operator is actually running.
  Their config, their database, their console on :8199, their model. Nothing
  is faked and nothing is isolated, which is the point: the two failures that
  survived two days on this host — a geocoder restarting 2,699 times and a
  console reporting unhealthy — were invisible to every suite in this
  repository precisely because no suite ever looked at the deployment.

Both expose the same four things the runner needs (`base_url`, `token`,
`console`, `restart_core`), so a scenario does not know which one it is on.
What makes the second safe to run twice is :class:`~testing.live.stack.StateGuard`,
above the run: snapshot before, restore after.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import LiveError
from .stack import Snapshot, Stack, StateGuard, docker_available, live_credentials
from .transport import Console

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Everything a stack run may write to, and therefore everything it snapshots.
#: `config/` holds the database, the notes and the memory; `.storage/` holds
#: the console's password hash. Both are bind mounts — see the rationale in
#: `jarvis-core/docker-compose.yml` — so a tarball is the backup, exactly as
#: `docs/RUNBOOK.md` tells a human to take one.
STACK_PATHS = ("jarvis-core/config", ".storage")
STACK_VOLUMES = ("jarvis-core_mosquitto-data",)

#: The container names the resilience scenarios act on. Names rather than
#: compose service names because `docker restart` takes the former and every
#: service here pins `container_name`.
CORE_CONTAINER = "jarvis-core"
STT_CONTAINER = "wyoming-whisper"

#: The prefix every artefact this suite creates on a real house carries, so
#: anything it leaves behind is identifiable as ours and can be swept. A note
#: called "shopping" could be the operator's; `test: shopping` could not.
TEST_NAMESPACE = "test:"


class Ground:
    """The interface the runner talks to."""

    name = "ground"
    #: Set when the ground owns containers, so the runner knows it can kill one.
    stack: Stack | None = None

    base_url: str = ""
    token: str = ""

    def start(self) -> "Ground":
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def console(self) -> Console | None:
        """A built console pointed at this ground, or None if not wanted."""
        return None

    def restart_core(self) -> None:
        raise NotImplementedError


class HarnessGround(Ground):
    """A jarvis-core this rig owns, with the fixture web behind it."""

    name = "harness"

    def __init__(self, *, verbose: bool = False, keep: bool = True,
                 web: dict[str, str] | None = None) -> None:
        self.verbose = verbose
        self.keep = keep
        self.web = web or {}
        self.harness: Any = None
        self._console: Console | None = None

    def start(self) -> "HarnessGround":
        from testing.harness import Harness

        work_dir = os.environ.get("LIVE_WORK_DIR") or str(
            REPO_ROOT / ".verify" / "live" / "harness"
        )
        self.harness = Harness(
            work_dir=work_dir,
            keep=True,
            verbose=self.verbose,
            model=os.environ.get("LLM_MODEL", ""),
            ollama_url=os.environ.get("LLM_URL", ""),
            wyoming={
                "host": os.environ.get("LIVE_STT_HOST", "127.0.0.1"),
                "stt": int(os.environ.get("LIVE_STT_PORT", "10300")),
                "tts": int(os.environ.get("LIVE_TTS_PORT", "10200")),
                "wake": int(os.environ.get("LIVE_WAKE_PORT", "10400")),
            },
            search_url=self.web.get("search", ""),
            browser_url=self.web.get("browser", ""),
        )
        self.harness.start()
        self.base_url = self.harness.base_url
        self.token = self.harness.token
        return self

    def console(self) -> Console | None:
        if self._console is None:
            self._console = Console(self.base_url, self.token).start()
        return self._console

    def restart_core(self) -> None:
        self.harness.restart_core()
        self.base_url = self.harness.base_url
        self.token = self.harness.token

    def stop(self) -> None:
        if self._console is not None:
            self._console.stop()
            self._console = None
        if self.harness is not None:
            self.harness.stop(cleanup=not self.keep)
            self.harness = None


class StackGround(Ground):
    """The running containers. The operator's own Jarvis, protected by a snapshot."""

    name = "stack"

    def __init__(self, *, protect: bool = True, console_url: str = "") -> None:
        self.protect = protect
        self.stack = Stack()
        self.guard = StateGuard()
        self.snapshot: Snapshot | None = None
        self._console_url = console_url or os.environ.get(
            "LIVE_CONSOLE_URL", "http://127.0.0.1:8199"
        )
        self._console: Console | None = None

    def start(self) -> "StackGround":
        if not docker_available():
            raise LiveError(
                "the stack ground needs Docker: `docker compose up -d --wait`. "
                "Without it the suite would be testing a jarvis-core that nobody runs."
            )
        self.stack.up()
        self.base_url, self.token = live_credentials()
        if self.protect:
            # Before a single word is spoken to a house somebody lives in.
            # Restoring is `stop()`'s job and happens even when the run dies.
            self.snapshot = self.guard.take(
                paths=list(STACK_PATHS), volumes=list(STACK_VOLUMES)
            )
        return self

    def console(self) -> Console | None:
        """The console container, not a copy of it.

        The harness ground builds and serves the console itself because there
        is no other one pointed at its throwaway core. Here there already is:
        the container on :8199 that the operator's browser opens, with their
        VAD hangover and their TTS voice. Serving a second copy would test a
        console nobody uses.
        """
        return _RunningConsole(self._console_url)

    def restart_core(self) -> None:
        self.stack.restart(CORE_CONTAINER)

    def stop_stt(self) -> None:
        self.stack.stop(STT_CONTAINER)

    def start_stt(self) -> None:
        self.stack.start(STT_CONTAINER)

    def stop(self) -> None:
        if self.snapshot is not None:
            # Restore first, restart second: jarvis-core holds the SQLite file
            # open, and a database swapped underneath a live process is only
            # sound once that process has re-opened it.
            self.guard.restore(self.snapshot)
            self.snapshot = None
            self.stack.restart(CORE_CONTAINER)


class _RunningConsole:
    """A `Console` that starts nothing, because it is already running."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.base_url = self.url
        self.token = ""

    def start(self) -> "_RunningConsole":
        return self

    def stop(self) -> None:
        return None
