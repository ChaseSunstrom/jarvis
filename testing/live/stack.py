"""The compose stack, as the thing under test.

Until now the live rig booted its own jarvis-core — real voice services, real
model, throwaway house. That proves the code. It does not prove the deployment,
and the deployment is where the failures were: on this host `photon` had
restarted 2,699 times and `jarvis-web` had reported unhealthy for two days
while every suite was green.

So there are two targets now:

* `harness` — a throwaway core. Fast, isolated, safe to wipe. The default, and
  the right one for a laptop with no stack up.
* `stack`   — the containers that are actually running, with the operator's
  own house behind them. Slower, and the only thing that can fail the way a
  deployment fails.

What makes the second one safe to run twice is in here as well: a scenario that
wipes memory or clears history snapshots what it is about to destroy and puts
it back afterwards, so "run the suite against my Jarvis" is not a sentence that
ends in an apology.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import LiveError

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two files, in the order they are brought up. Core first: the console
#: without a backend shows its own "cannot reach the server" state, which is a
#: worse first impression than waiting ten seconds.
COMPOSE_FILES = (REPO_ROOT / "jarvis-core" / "docker-compose.yml", REPO_ROOT / "docker-compose.yml")

#: Where a snapshot goes. Under `.verify/` because it is output, not state.
SNAPSHOT_DIR = REPO_ROOT / ".verify" / "live" / "snapshots"

#: The one image used for snapshot and restore. Pinned like everything
#: else in the stack, and the same recipe `docs/RUNBOOK.md` gives a human.
BUSYBOX = "busybox:1.36"

#: What makes a log line worth failing a run over. `ERROR` and `CRITICAL` are
#: a service saying it could not do its job; a traceback's first line is one
#: saying it did not mean to stop.
ERROR_MARKERS = ("ERROR", "CRITICAL", "Traceback (most recent call last)")

#: Records that match a marker and are still not failures. Each is a named
#: exception with a reason, and the list is short on purpose — an allowlist is
#: how a gate like this stops meaning anything.
ERROR_ALLOWED = (
    # A Wyoming client that hangs up while the service is writing its `info`
    # reply. Every probe that only wants to know whether a port answers does
    # this, including Home Assistant's and this repository's own reachability
    # check; the service logs a traceback for what is a client politely leaving.
    "ConnectionResetError: Connection lost",
    "BrokenPipeError",
    # onnxruntime cannot set thread affinity inside an LXC. It says so once per
    # session and then works perfectly.
    "pthread_setaffinity_np failed",
    # SearXNG initialising an upstream engine that refuses it (wikidata's 403
    # on a fresh container, 27 Aug 2026): the engine is suspended and retried
    # by SearXNG itself, the other engines answer, and the house's search
    # falls back to the second instance (M68). Logged at ERROR by SearXNG for
    # what is one engine's rate limit, not the stack's fault.
    "engine INIT failed",
    # Piper, after a run the rig stopped mid-answer (M96): the core drops the
    # synthesis stream it was reading and piper's handler task ends with an
    # exception nobody retrieves. Scoped to piper's own asyncio line — the
    # same words from any other container are still a failure.
    "wyoming-piper: ERROR:asyncio:Task exception was never retrieved",
)

#: Lines that continue the record above them rather than starting a new one:
#: a traceback's frames, its `File "..."` lines, and the source echo under them.
_CONTINUATION = ("  ", "\t", "Traceback (most recent call last)")

#: And the line a traceback ENDS on, which is unindented and is the only line
#: that names the exception. Dropping it left the allowlist matching on
#: `Task exception was never retrieved` — true of every async failure there is.
_EXCEPTION_LINE = re.compile(r"[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Timeout)\b")


#: What a `docker compose` child gets as its environment: enough to find
#: docker and the user, and nothing that could interpolate into a service.
#: The caller's shell has often just done `set -a; . .env` to give the rig
#: LLM_URL — and compose prefers a shell variable over the project's own
#: `.env`. One such run re-created jarvis-core, the gateway and the browser
#: service with the root `.env`'s values: empty browser tokens (a crash loop),
#: a different gateway key (401 on every model call), a core whose token store
#: the rig no longer matched. The compose file's directory holds its `.env`;
#: that, and only that, decides what a container is built with.
_COMPOSE_ENV_KEEP = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TZ")
_COMPOSE_ENV_PREFIXES = ("DOCKER_", "COMPOSE_", "BUILDKIT_")


def _compose_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _COMPOSE_ENV_KEEP or key.startswith(_COMPOSE_ENV_PREFIXES)
    }
    env.update(extra or {})
    return env


def in_worktree(root: Path | None = None) -> bool:
    """True when `root` (the repo root by default) is a git worktree.

    In a worktree `.git` is a file that points at the common directory; in the
    main checkout it is the directory itself. The root is read at call time,
    not bound as a default, so a test can point it somewhere else.
    """
    return ((root or REPO_ROOT) / ".git").is_file()


def _run(argv: list[str], timeout: float = 600.0, check: bool = True,
         env: dict[str, str] | None = None) -> str:
    compose = argv[:2] == ["docker", "compose"]
    if compose and in_worktree() and not os.environ.get("JARVIS_ALLOW_WORKTREE_COMPOSE"):
        # Twice in one night an agent's worktree brought the stack "up" and
        # re-created the house's containers from its own checkout — wrong
        # config directory, empty secrets, a crash-looping browser service.
        # The compose project name is the directory's, so a worktree's compose
        # IS the production project. Refused here, where every compose call
        # of the rig passes; the Makefile and live_interaction.sh refuse too.
        raise LiveError(
            "refusing to run docker compose from a git worktree: it would re-create the "
            "production containers from this checkout. Run the live rig from the main "
            "checkout (or set JARVIS_ALLOW_WORKTREE_COMPOSE=1 if you really mean it)."
        )
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
            env=_compose_env(env) if compose else ({**os.environ, **env} if env else None),
        )
    except subprocess.TimeoutExpired as err:
        raise LiveError(f"{' '.join(argv[:3])}… timed out after {timeout:g}s") from err
    if check and done.returncode != 0:
        raise LiveError(
            f"{' '.join(argv[:4])}… failed ({done.returncode}): "
            f"{(done.stderr or done.stdout).strip()[-800:]}"
        )
    return done.stdout


def _strip_prefix(line: str) -> tuple[str, str]:
    """Split `docker compose logs`' `name  | text` into the two halves."""
    head, sep, rest = line.partition("|")
    if sep and head.strip() and " " not in head.strip():
        return head.strip(), rest[1:] if rest.startswith(" ") else rest
    return "", line


def _records(lines: list[str]) -> list[str]:
    """Group log lines into records and return the ones that are failures.

    A record starts at a line matching a marker and swallows the indented lines
    under it plus the unindented line that names the exception — which is what
    turns a twenty-line traceback into one thing an allowlist can be honest
    about.

    Grouping is **per container**, because `docker compose logs` interleaves:
    piper's traceback arrives with jarvis-core's INFO lines threaded through
    it, and a grouper that treated any other line as the end of the record cut
    every traceback off before the line that names its exception — so the
    allowlist saw `Task exception was never retrieved` and nothing else.
    """
    out: list[str] = []
    open_records: dict[str, list[str]] = {}

    def flush(owner: str) -> None:
        current = open_records.pop(owner, None)
        if not current:
            return
        record = "\n".join(current)
        if not any(allowed in record for allowed in ERROR_ALLOWED):
            out.append((f"{owner}: {record}" if owner else record)[:1200])

    for line in lines:
        name, text = _strip_prefix(line)
        current = open_records.get(name)
        if current is not None:
            stripped = text.strip()
            if text.startswith(_CONTINUATION) or stripped.startswith('File "'):
                current.append(text.rstrip())
                continue
            if _EXCEPTION_LINE.match(stripped):
                current.append(stripped)
                continue
        if any(marker in text for marker in ERROR_MARKERS):
            flush(name)
            open_records[name] = [text.strip()]
            continue
        # A normal line from this container ends its record; a line from any
        # OTHER container is simply not part of it.
        if current is not None:
            flush(name)
    for owner in list(open_records):
        flush(owner)
    return out


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20, check=True
        )
    except Exception:  # noqa: BLE001 - a daemon that is not there is the answer
        return False
    return True


#: The compose projects that make up this stack. Both files, and nothing else
#: on the host: a developer's unrelated container must not fail a Jarvis run.
PROJECTS = ("jarvis-core", "jarvis")


@dataclass
class Container:
    name: str
    status: str
    health: str
    exit_code: int | None = None

    @property
    def one_shot_done(self) -> bool:
        """An init container that ran and finished.

        `jarvis-config-init` fixes the config directory's ownership and exits;
        `jarvis-init` does the same for the workspace. Both are `restart: "no"`
        and both show as `exited`, which is success, not a sick container.
        """
        return self.status == "exited" and self.exit_code == 0

    @property
    def ok(self) -> bool:
        if self.one_shot_done:
            return True
        return self.status == "running" and self.health in ("healthy", "")


class Stack:
    """The running compose stack: bring it up, watch it, put it back."""

    def __init__(self, files: tuple[Path, ...] = COMPOSE_FILES) -> None:
        self.files = files
        self.started_at = time.time()

    # --- bringing it up ---------------------------------------------------
    def up(self, timeout: float = 900.0) -> list[Container]:
        """`up -d --no-recreate --wait`, per file, in order. Raises with what refused.

        `--no-recreate`: this is "make sure the stack is up", not "apply
        the tree". Without it every gate's first `up()` after a launcher's
        `make up` recreated jarvis-core (a config hash that differs between
        the two invocations), so the house booted once more than the run
        ordered at the start of every live slice — the containers row was red
        on three houses for it (27 Aug 2026), with the garage scenario itself
        green. Images and compose changes are applied by the launchers'
        `make up`, never by the rig.
        """
        for path in self.files:
            _run(
                ["docker", "compose", "-f", str(path), "up", "-d", "--no-recreate", "--wait"],
                timeout=timeout,
            )
        self.started_at = time.time()
        bad = [c for c in self.containers() if not c.ok]
        if bad:
            raise LiveError(
                "the stack came up but these are not healthy: "
                + ", ".join(f"{c.name} ({c.health or c.status})" for c in bad)
            )
        return self.containers()

    def containers(self) -> list[Container]:
        out: list[Container] = []
        for project in PROJECTS:
            raw = _run(
                [
                    "docker", "ps", "-a",
                    "--filter", f"label=com.docker.compose.project={project}",
                    "--format", "{{json .}}",
                ],
                timeout=60,
                check=False,
            )
            for line in raw.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                text = str(row.get("Status") or "")
                health = ""
                # Two spellings, and the difference cost a scenario: a healthy
                # container says `(healthy)`, one inside its start period says
                # `(health: starting)`. Matching only the first spelling read
                # "no healthcheck" and the rig reconnected to a jarvis-core
                # that was three seconds into booting.
                for word in ("healthy", "unhealthy", "starting"):
                    if f"({word})" in text or f"(health: {word})" in text:
                        health = word
                exit_code = None
                match = re.search(r"Exited \((\d+)\)", text)
                if match:
                    exit_code = int(match.group(1))
                out.append(
                    Container(
                        name=str(row.get("Names") or ""),
                        status=str(row.get("State") or ""),
                        health=health,
                        exit_code=exit_code,
                    )
                )
        return out

    def unhealthy(self) -> list[Container]:
        return [c for c in self.containers() if c.status == "running" and c.health == "unhealthy"]

    def restarting(self) -> list[Container]:
        return [c for c in self.containers() if c.status == "restarting"]

    # --- watching it ------------------------------------------------------
    def errors_since(self, since: float | None = None) -> list[str]:
        """Every ERROR-level *record* a container logged during the run.

        The point of running against real containers: a service that is up and
        complaining is invisible to every assertion a scenario makes, and this
        is the only thing here that looks at it.

        Records rather than lines, because a traceback is one event spread over
        twenty of them. Grouping is what lets the allowlist name the exception
        (`ConnectionResetError: Connection lost`) instead of the useless line
        that introduces it (`Task exception was never retrieved`) — allowlisting
        the introduction would blind this to every async crash there is.
        """
        seconds = int(time.time() - (since if since is not None else self.started_at)) + 5
        found: list[str] = []
        for path in self.files:
            raw = _run(
                [
                    "docker", "compose", "-f", str(path),
                    "logs", "--no-color", f"--since={seconds}s",
                ],
                timeout=120,
                check=False,
            )
            found.extend(_records(raw.splitlines()))
        return found

    def boots_since(self, since: float | None = None, container: str = "jarvis-core") -> list[str]:
        """When the core came up during the run, from its own log — one entry per boot.

        `docker logs` survives `docker restart` (same container), which is the
        case this exists for: a restart nobody ordered mid-run. It does not
        survive a recreate, so a `compose up` that replaced the container
        shows as zero boots — the run's own scenarios then fail on the socket
        instead, which the runner names.
        """
        seconds = int(time.time() - (since if since is not None else self.started_at)) + 5
        # Not `_run`: the core logs to stderr, and `docker logs` keeps the
        # container's two streams apart — `_run` returns stdout, which for this
        # container is empty. Merged here, on purpose.
        try:
            done = subprocess.run(
                ["docker", "logs", "-t", f"--since={seconds}s", container],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        raw = done.stdout or ""
        found: list[str] = []
        for line in raw.splitlines():
            if "API listening on" in line:
                # `-t` prefixes RFC3339; the clock time is what a person
                # matches against a gate's log.
                stamp = line.split(" ", 1)[0]
                found.append(stamp[11:19] if len(stamp) > 19 else stamp)
        return found

    def recreate(self, service: str, env: dict[str, str] | None = None,
                 wait: float = 300.0) -> None:
        """Bring one service back up with an environment override.

        Compose substitutes `${VAR}` in the service's `environment:` from the
        process environment, so this is how a run says "and allow these hosts"
        without editing the operator's `.env`. Passing no override puts the
        service back the way their `.env` describes it, which is how the
        override is undone.

        Only the file that defines the service is used: `up -d` on a file that
        does not name it is an error, and running both files would recreate
        every service in them.
        """
        for path in self.files:
            if f"  {service}:" not in path.read_text(encoding="utf-8"):
                continue
            _run(
                ["docker", "compose", "-f", str(path), "up", "-d", "--wait", service],
                timeout=wait, env=env,
            )
            return
        raise LiveError(f"no compose file here defines {service!r}")

    def restart(self, container: str, wait: float = 120.0) -> None:
        _run(["docker", "restart", container], timeout=wait + 30)
        self.wait_healthy(container, timeout=wait)

    def stop(self, container: str) -> None:
        _run(["docker", "stop", container], timeout=120)

    def start(self, container: str, wait: float = 120.0) -> None:
        _run(["docker", "start", container], timeout=120)
        self.wait_healthy(container, timeout=wait)

    def health_of(self, container: str) -> str:
        """`healthy` / `starting` / `unhealthy`, or `` for no healthcheck.

        From `docker inspect` rather than the `docker ps` status line: the line
        is prose, and prose is what let a container three seconds into booting
        read as ready.
        """
        raw = _run(
            [
                "docker", "inspect", container,
                "--format", "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            ],
            timeout=30,
            check=False,
        )
        return raw.strip()

    def wait_healthy(self, container: str, timeout: float = 180.0) -> None:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = self.health_of(container)
            if last == "healthy":
                return
            if not last:
                # No healthcheck at all: the best available answer is that the
                # process is running. Every long-running service in this stack
                # has one, so this is for a container somebody adds later.
                match = [c for c in self.containers() if c.name == container]
                if match and match[0].status == "running":
                    return
            time.sleep(1.0)
        raise LiveError(
            f"{container} did not come back within {timeout:g}s (health: {last or 'none'})"
        )


#: Files under a snapshotted path that a person edits and a run never does —
#: left alone by `restore`, whatever the tarball holds. Paths relative to the
#: snapshotted directory; a trailing slash names a whole directory.
OPERATOR_FILES: tuple[str, ...] = (
    "configuration.yaml",
    "automations.yaml",
    "scenes.yaml",
    "scripts.yaml",
    "secrets.yaml",
    "packages/",
    "agents/",
    "examples/",
    # Skills are the operator's documents too: the phone-tasks skill lost a
    # line to a restore on 27 Aug 2026, an hour after it was written, and the
    # mirror that pins it went red on CI before anyone saw the file.
    "skills/",
    "models/",
    "dashboards/",
)


def restore_script(name: str) -> str:
    """The shell that puts a snapshotted directory back, minus the operator's files.

    Kept as a function so a test can read what the sweep and the extract will
    and will not touch without running a container.
    """
    protect = " ".join(f"-e '^{p}'" for p in OPERATOR_FILES)
    excludes = " ".join(f"--exclude='./{p.rstrip('/')}'" for p in OPERATOR_FILES)
    return (
        f"cd /v && tar tzf /in/{name} | sed 's#^\\./##' | grep -v '/$' | sort > /tmp/keep && "
        f"find . -type f | sed 's#^\\./##' | grep -v {protect} | sort > /tmp/have && "
        "comm -13 /tmp/keep /tmp/have | while read -r extra; do rm -f \"$extra\"; done; "
        f"tar xzf /in/{name} -C /v --overwrite {excludes}"
    )


@dataclass
class Snapshot:
    """What a destructive scenario is about to destroy, and where it went."""

    paths: dict[str, Path] = field(default_factory=dict)
    volumes: dict[str, Path] = field(default_factory=dict)


class StateGuard:
    """Snapshot before, restore after — so the suite is re-runnable.

    A scenario that wipes memory against the operator's own Jarvis is a
    scenario nobody runs twice unless this exists. Directories are tarred
    (`config/` is a bind mount on purpose — see the compose file), named
    volumes through a busybox container, which is the recipe `docs/RUNBOOK.md`
    documents for a human to use by hand.
    """

    def __init__(self, out: Path = SNAPSHOT_DIR) -> None:
        self.out = out

    def take(self, paths: list[str] | None = None, volumes: list[str] | None = None) -> Snapshot:
        self.out.mkdir(parents=True, exist_ok=True)
        snap = Snapshot()
        for relative in paths or []:
            source = REPO_ROOT / relative
            if not source.exists():
                continue
            name = relative.replace("/", "_") + ".tgz"
            # Through a container, not `tar` as this user: jarvis-core writes
            # `config/.storage/*` at 0600 under the container's uid, so the
            # operator's own account cannot read the very files a snapshot
            # exists to protect. A backup with holes in it is worse than none —
            # it restores a house missing its credentials.
            self._tar(f"{source}", name)
            snap.paths[relative] = self.out / name
        for volume in volumes or []:
            name = f"volume_{volume}.tgz"
            self._tar(volume, name, is_volume=True)
            snap.volumes[volume] = self.out / name
        return snap

    def _tar(self, source: str, name: str, is_volume: bool = False) -> None:
        mount = f"{source}:/v:ro" if not is_volume else f"{source}:/v:ro"
        _run(
            [
                "docker", "run", "--rm",
                "-v", mount,
                "-v", f"{self.out}:/out",
                BUSYBOX,
                "sh", "-c",
                # chown back: the tarball is written by root inside the
                # container and would otherwise be a file the operator cannot
                # delete from their own `.verify/` directory.
                f"tar czf /out/{name} -C /v . && chown {os.getuid()}:{os.getgid()} /out/{name}",
            ],
            timeout=900,
        )

    def restore(self, snap: Snapshot) -> None:
        """Put it all back. Stop the services first — see the note below."""
        for relative, archive in snap.paths.items():
            source = REPO_ROOT / relative
            self._untar(str(source), archive.name)
        for volume, archive in snap.volumes.items():
            self._untar(volume, archive.name)

    def _untar(self, target: str, name: str) -> None:
        # Restore means "as it was", so a file that appeared during the run is
        # removed as well as a changed one being put back. It has to: the tar
        # only knows how to overwrite, and a live run learned that the hard way
        # — a config file added mid-run survived the restore, collided with the
        # restored original, and jarvis-core would not boot afterwards.
        #
        # Except the operator's own files. What a run changes is the house's
        # state — `.storage/`, the recorder's database, the notes — and that is
        # what "as it was" means. `configuration.yaml`, the included YAML, the
        # packages and the agent definitions are edited by a person, and a
        # run that ends while they are being edited must not put an hour of
        # their work back the way it found it (a `narrate:` block, 27 Aug
        # 2026). Those are neither swept nor extracted.
        #
        # Extract AFTER the sweep, and no `rm -rf /v/*`: unlinking a file a
        # running service holds open is safe on Linux (it keeps its descriptor
        # until the restart that follows), while emptying the directory first
        # would leave a window in which the house does not exist.
        script = restore_script(name)
        _run(
            [
                "docker", "run", "--rm",
                "-v", f"{target}:/v",
                "-v", f"{self.out}:/in:ro",
                BUSYBOX,
                "sh", "-c", script,
            ],
            timeout=900,
        )


def live_credentials() -> tuple[str, str]:
    """The running core's URL and a token for it, from the operator's `.env`.

    Read rather than guessed: the token in `.env` is the one the console and
    the phone use, and a suite that minted its own would be testing a door
    nobody else comes through.
    """
    url = os.environ.get("JARVIS_URL", "").strip()
    token = os.environ.get("JARVIS_TOKEN", "").strip()
    if not url or not token:
        for name in (".env", "jarvis-core/.env"):
            path = REPO_ROOT / name
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if key.strip() == "JARVIS_URL" and not url:
                    url = value
                elif key.strip() == "JARVIS_TOKEN" and not token:
                    token = value
    if not url or not token:
        raise LiveError(
            "the stack target needs JARVIS_URL and JARVIS_TOKEN — they are in "
            "`.env`, which is where the console reads them from"
        )
    return url.rstrip("/"), token
