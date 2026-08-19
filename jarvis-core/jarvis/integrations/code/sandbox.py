"""Environments a coding job may run in, and the fences around them.

Jarvis Code began with no shell at all. `run_check` matched a whole string
against the repository's own `checks:` list and nothing else existed, and the
argument for that was simple: a coding loop that can run arbitrary commands on
the host is the largest hole anybody has ever put in this codebase.

That argument is still true, and this module does not weaken it. What it adds
is a *second* place for commands to run — a container that is thrown away when
the job ends — and a shell that exists **only there**. On the host there is
still no shell.

An environment also turned out to be the only honest way to run a check on a
repository a job can WRITE. A check is the operator's command string, but it
executes files out of the working tree — `conftest.py`, `package.json`, the
Makefile — and a job that can write those chooses what runs. So on a writable
repository, `run_check` now needs an environment (or the operator's own
`sandbox:` wrapper); without one it is withheld and refused. A READ-ONLY
repository with no environment behaves exactly as before.

## What an environment buys, and what it costs

A real coding job needs to install things. `npm install`, `pip install`, `cargo
build`, `go mod download` — none of them fit through a fixed `checks:` list,
and a job that cannot run them cannot work on most repositories. So an
environment may have **network access**, which is the thing this file is most
careful about:

    network: none     the default. Nothing reaches out. Fine for a test suite
                      whose dependencies are already vendored.
    network: egress   the container joins Docker's bridge network. This is
                      what makes "install the dependencies and run the tests"
                      possible.

`egress` is the honest name for what it is, and it is broader than "the
internet": on the default bridge, the host is reachable at the gateway address
and so is everything else on your LAN. A job with `egress` can talk to your
router, your NAS, and jarvis-core's own API. If that matters, create a Docker
network with the access you want and name it in `network_name:` — the fences
below still apply either way.

`egress` is not a small thing to hand a model: it can read your repository and
it can make outbound connections, so it can post your code somewhere. It is
opt-in per environment, it is named in the console, and the operator chooses
it. What it CANNOT do is reach the rest of your machine — see the fences.

## The fences, and why each one is there

Every one of these is in the argv `container_argv()` builds, and every one has
a test that fails if it is dropped:

    --rm                        the container and its filesystem die with the
                                job. Nothing it installed outlives it.
    --network none              unless the environment asked for egress.
    --user <uid>:<gid>          the HOST's uid, so files it writes into the
                                repository belong to you rather than to root.
                                A container writing as root through a bind
                                mount leaves a repository you cannot edit.
    --cap-drop ALL              no capabilities at all.
    --security-opt no-new-privileges
                                a setuid binary inside the image cannot raise
                                privilege.
    --pids-limit                a fork bomb hits a wall instead of the host.
    --memory / --cpus           likewise for RAM and CPU.
    --mount type=tmpfs,/tmp     somewhere to write that is not the repository
                                and not the image.
    --ulimit fsize              the bind mount is the host's disk and Docker
                                cannot quota it, so the cap is per file. NOT
                                inherited by `docker exec`, which is a new
                                process with the daemon's limits — `exec_argv`
                                re-applies it in the shell, and that is the
                                only reason it still holds.
    --ulimit nofile             a descriptor storm is the other cheap way to
                                hurt a host. Same caveat, same answer.
    -v <repo>:/work  -w /work   THE ONLY host path in the container. Not the
                                workspace root, not the parent — one repo.

And the negatives, which matter as much: no `--privileged`, no
`-v /var/run/docker.sock` (a container that can talk to the daemon can start
another container that mounts anything), no host networking, and none of the
operator's environment variables. `env:` on the environment is an explicit
allow-list written in configuration.yaml.

## What this does NOT stop

Written down because a fence list reads like a guarantee, and these are the
places where it is not one.

**The container can write `.git`.** The bind mount is the whole repository,
and `resolve_for_write` — which refuses `.git` — is a HOST-side check on the
host-side tools. A shell inside the container is under no such rule. That is
survivable only because it was assumed: `Workspace.git` re-checks the config
before every single invocation rather than once per job, precisely because
the job can rewrite it underneath. What a job CAN still do is corrupt its own
repository, which is a mess to clean up and not a host compromise.

**A hook written into `.git/hooks` outlives the job.** Host git never runs it
(`core.hooksPath=/dev/null`), and `unsafe_git_config` reports an executable
hook rather than ignoring it — but the operator's own shell, their editor,
their cron job run hooks normally. The report is the mitigation; there is no
way to stop a file existing in a directory the container can write.

**`persist: true` keeps what a job installed.** That is the feature, and it
means one job's `apt-get install` is in the next job's image. A job that
installs something hostile has left it for its successors. `reset_environment`
throws the image away, and the console offers it; nothing does so
automatically.

**`egress` really is egress.** See above — the LAN and jarvis-core's own API
are on the other side of it.

**None of this bounds a writable repository's host CHECKS**, because those are
refused outright now: a writable repository with no `environment:` and no
`sandbox:` wrapper is not offered `run_check` at all. See
`Workspace.unconfined_check_refusal`.

## Why this shells out to `docker` rather than using a library

Because the fences are then visible in one list of strings that a test can
assert on, and a reader can compare against `docker run --help`. A client
library would hide them behind keyword arguments and a version-dependent
default. `container_argv()` is a pure function returning a list — no I/O, no
daemon — which is why every property above is provable in a unit test on a
machine with no Docker at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_IMAGE",
    "Session",
    "exec_argv",
    "persisted_image",
    "reset_environment",
    "Environment",
    "NETWORK_EGRESS",
    "NETWORK_NONE",
    "SandboxError",
    "container_argv",
    "environment_from_dict",
]

#: A base with a compiler, git and python. Deliberately a plain upstream image
#: rather than something of ours: an operator who wants node or go changes one
#: line, and nobody has to trust an image this project built.
DEFAULT_IMAGE = "python:3.12-bookworm"

NETWORK_NONE = "none"
NETWORK_EGRESS = "egress"
NETWORKS = (NETWORK_NONE, NETWORK_EGRESS)

#: Where the repository is mounted inside the container. Fixed, not derived
#: from the host path — the container must not learn where the repo lives on
#: your disk, and a fixed path makes a command the model writes portable.
WORK_DIR = "/work"

MAX_COMMAND_CHARS = 4000
MAX_OUTPUT_CHARS = 20_000

#: A file-descriptor storm is a cheap way to hurt a host, so both the
#: container and every command inside it get the same ceiling.
MAX_OPEN_FILES = 4096
#: `--ulimit` reaches `setrlimit(2)` unscaled, and RLIMIT_FSIZE is in bytes.
BYTES_PER_MB = 1024 * 1024
#: `ulimit -f` in POSIX `sh` is in 512-byte blocks, which is NOT the same unit
#: as the flag above. Two names so that neither can be used for the other.
BLOCKS_PER_MB = 2048
#: Longest a single command may run. A build is minutes; nothing legitimate in
#: a coding job is an hour, and a wedged process must not hold the job open.
DEFAULT_TIMEOUT = 900.0
MAX_TIMEOUT = 3600.0

#: Where package managers keep downloads, inside the container.
#:
#: Caches only. Not `site-packages`, not `node_modules`, not `/usr/local` — a
#: volume over any of those would make installations persist between jobs,
#: which is the thing the throwaway container exists to prevent. Re-downloading
#: is the cost this avoids; re-installing is not.
CACHE_PATHS = (
    "/home/builder/.cache",
    "/root/.cache",
)

#: An environment name is used in log lines and config; keep it boring.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: An image reference. Deliberately permissive about registries and digests and
#: strict about everything that is not one — no spaces, no shell characters.
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")
#: Environment variable names an environment may set.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _current_ids() -> tuple[int, int]:
    """Who this process is, as one seam.

    A function rather than `os.getuid()` inline so a test can say which user it
    is running as without patching `os` globally — which breaks anything else
    that asks, pytest's own temp-directory ownership check included.
    """
    return os.getuid(), os.getgid()


class SandboxError(RuntimeError):
    """The environment could not be used, with what went wrong."""


@dataclass
class Environment:
    """One sandbox an operator has declared.

    Nothing here is chosen by the model. The image, the network policy and the
    limits are configuration; a job picks an environment BY NAME from the ones
    that exist, exactly as it picks a check from `checks:`.
    """

    name: str
    image: str = DEFAULT_IMAGE
    #: `none` or `egress`. See the module docstring — this is the setting worth
    #: reading twice.
    network: str = NETWORK_NONE
    memory: str = "2g"
    cpus: str = "2"
    pids: int = 512
    timeout: float = DEFAULT_TIMEOUT
    #: Passed into the container verbatim. An explicit allow-list: the host's
    #: own environment is never forwarded, so a token in the operator's shell
    #: cannot leak into a job by accident.
    env: dict[str, str] = field(default_factory=dict)
    #: Run once before the job's first command, in order. For "apt-get install
    #: -y libpq-dev" and friends — the things a repository needs that the image
    #: does not have.
    setup: list[str] = field(default_factory=list)
    #: Largest single file a job may write, in MiB.
    #:
    #: The bind mount is the one thing `--memory`, `--pids-limit` and the
    #: bounded tmpfs do not cover: `/work` is the operator's filesystem, and
    #: `dd if=/dev/zero of=/work/pad` fills it. Docker cannot put a quota on a
    #: bind mount — `--storage-opt size=` covers the container's own layer and
    #: only on some storage drivers — so this is `--ulimit fsize`, which the
    #: kernel enforces per file, everywhere.
    #:
    #: It does not stop many small files. That residual is documented rather
    #: than papered over; a job that wants to fill a disk with a million
    #: one-byte files still can, and the answer to that is a filesystem quota
    #: on the workspace, which is the operator's to set.
    max_file_mb: int = 2048
    #: Keep what a job INSTALLS, so the next one does not reinstall it.
    #:
    #: "It downloads a toolchain every single time" is the complaint this
    #: answers. On `close()` the container is committed to an image of its own
    #: — `jarvis-code-env-<name>:latest` — and the next job in this environment
    #: starts from that. `apt-get install cmake` happens once, not once a job.
    #:
    #: The IMAGE persists, never the container. That distinction is the whole
    #: design: a long-lived container would have to keep its mounts, and its
    #: mounts are fixed when it is created — so reusing one across repositories
    #: would mean mounting the whole workspace and letting every job see its
    #: siblings. Committing keeps the tools and keeps the one-repository mount.
    #:
    #: WHAT IT COSTS, plainly: a job can leave something in that image, and
    #: every later job in the same environment starts from it. That is real
    #: cross-job influence and it is why this is off by default. `code.reset_
    #: environment` throws the image away and goes back to the configured one.
    persist: bool = False
    #: Keep a named volume of package caches between jobs.
    #:
    #: The container dies with the job, so without this every job re-downloads
    #: its whole dependency tree — minutes of network per run, and a lot of
    #: somebody else's bandwidth. The volume holds ONLY cache directories
    #: (pip, npm, cargo, go); the repository and the installed packages are
    #: still thrown away.
    #:
    #: It is the one thing that survives a job, so it is opt-in: a poisoned
    #: cache is a way for one job to affect a later one in the same
    #: environment. Worth it for a scratch project, worth thinking about for
    #: anything else.
    cache: bool = False
    #: A Docker network the operator created, used instead of the default
    #: bridge when `network: egress`. The way to give a job the internet
    #: without giving it the LAN — Docker cannot express that itself, and
    #: pretending otherwise would be the lie this field exists to avoid.
    network_name: str = ""

    @property
    def networked(self) -> bool:
        return self.network == NETWORK_EGRESS

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "image": self.image,
            "network": self.network,
            "memory": self.memory,
            "cpus": self.cpus,
            "pids": self.pids,
            "timeout": self.timeout,
            # Names only. The VALUES may be a package-index credential, and a
            # console that listed them would put them on a screen and in a
            # browser's memory for no reason.
            "env": sorted(self.env),
            "setup": list(self.setup),
            "cache": self.cache,
            "persist": self.persist,
            "persist_image": persisted_image(self) if self.persist else "",
        }

    def describe(self) -> str:
        """One line for a human deciding whether to trust it."""
        reach = (
            "can reach the network, including this LAN"
            if self.networked
            else "no network"
        )
        extras = ""
        if self.persist:
            extras += " · keeps what it installs"
        elif self.cache:
            extras += " · cached downloads"
        return f"{self.image} · {reach} · {self.memory} RAM · {self.cpus} CPU{extras}"


def environment_from_dict(raw: Any) -> Environment | None:
    """Read one `environments:` entry, refusing anything malformed.

    Refuses rather than corrects. An image name with a space in it, or a
    network policy this module does not know, is a typo in a security-relevant
    setting — and quietly falling back to a default would give the operator an
    environment they did not ask for while looking as though it worked.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        _LOGGER.warning("code: %r is not a usable environment name", raw.get("name"))
        return None

    image = str(raw.get("image") or DEFAULT_IMAGE).strip()
    if not _IMAGE_RE.match(image):
        _LOGGER.warning("code: environment %s has an unusable image %r", name, image)
        return None

    network = str(raw.get("network") or NETWORK_NONE).strip().lower()
    if network in ("egress", "internet", "online", "true", "yes"):
        network = NETWORK_EGRESS
    if network not in NETWORKS:
        _LOGGER.warning(
            "code: environment %s asked for network %r, which is not one of %s",
            name,
            raw.get("network"),
            ", ".join(NETWORKS),
        )
        return None

    env: dict[str, str] = {}
    for key, value in (raw.get("env") or {}).items():
        if _ENV_NAME_RE.match(str(key)):
            env[str(key)] = str(value)
        else:
            _LOGGER.warning("code: environment %s: dropping variable %r", name, key)

    try:
        timeout = float(raw.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    try:
        pids = int(raw.get("pids") or 512)
    except (TypeError, ValueError):
        pids = 512

    return Environment(
        name=name,
        image=image,
        network=network,
        memory=str(raw.get("memory") or "2g").strip(),
        cpus=str(raw.get("cpus") or "2").strip(),
        pids=max(16, min(pids, 8192)),
        timeout=max(5.0, min(timeout, MAX_TIMEOUT)),
        env=env,
        setup=[str(c) for c in (raw.get("setup") or []) if str(c).strip()][:16],
        network_name=str(raw.get("network_name") or "").strip(),
        cache=bool(raw.get("cache")),
        persist=bool(raw.get("persist")),
        max_file_mb=max(1, min(_as_int(raw.get("max_file_mb"), 2048), 1_000_000)),
    )


def container_argv(
    environment: Environment,
    repo_root: Path,
    command: str,
    *,
    docker: str = "docker",
    uid: int | None = None,
    gid: int | None = None,
    writable: bool = True,
    name: str = "",
    detach: bool = False,
) -> list[str]:
    """The exact `docker run` command line, as a list.

    Pure: no daemon, no filesystem, no side effects. That is deliberate — every
    fence in the module docstring is a string in the list this returns, so the
    whole security surface is provable by a unit test on a machine with no
    Docker installed.

    The command is passed to `sh -c` INSIDE the container, which is where the
    shell is allowed to be. It is one argument to `docker run`, so nothing in
    it is interpreted by this process or by the host's shell.
    """
    if not command.strip():
        raise SandboxError("no command to run")
    if len(command) > MAX_COMMAND_CHARS:
        raise SandboxError(
            f"that command is {len(command)} characters; the limit is {MAX_COMMAND_CHARS}"
        )

    root = repo_root.expanduser().resolve()
    argv = [
        docker,
        "run",
        # Thrown away when the job ends, along with everything it installed.
        "--rm",
        # Named so it can be KILLED. `proc.kill()` on a timeout kills the
        # docker CLIENT; the daemon keeps the container running, and `--rm`
        # only fires when a container exits. Without a name there is no handle
        # and `run_command("sleep 999999")` outlives the job for ever, still
        # holding the repository mounted.
        f"--name={name}" if name else "--label=jarvis-code=1",
        # A session container idles in the background; commands reach it with
        # `docker exec`. See `Session` for why one container per JOB rather
        # than one per command.
        *(["--detach"] if detach else []),
        # Nothing is typed at a coding job. Without this a command that decides
        # to prompt waits on a terminal nobody is sitting at.
        "--interactive=false",
        f"--workdir={WORK_DIR}",
        # THE only host path. One repository, nothing above it — and mounted
        # `:ro` when the repository is read-only, because `writable: false`
        # withholds the EDIT tools and would otherwise mean nothing at all to a
        # shell.
        f"--volume={root}:{WORK_DIR}" + ("" if writable else ":ro"),
        # Somewhere to write that is neither the repo nor the image, and that
        # cannot be used to fill the host disk.
        "--mount=type=tmpfs,destination=/tmp,tmpfs-size=512m",
        f"--memory={environment.memory}",
        f"--cpus={environment.cpus}",
        f"--pids-limit={environment.pids}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        # The bind mount is the host's filesystem; nothing else here bounds
        # what a job writes into it.
        #
        # BYTES. `--ulimit` values reach `setrlimit(2)` unscaled — docker does
        # no unit conversion — and RLIMIT_FSIZE is documented in bytes. This
        # line used to multiply by 2048 in the belief that it took 512-byte
        # blocks, which capped the default 2048 MB environment at 4 MiB: not a
        # loose fence, a wrong one, strict enough to kill an ordinary build.
        f"--ulimit=fsize={environment.max_file_mb * BYTES_PER_MB}",
        # A file-descriptor storm is the other cheap way to hurt a host.
        f"--ulimit=nofile={MAX_OPEN_FILES}:{MAX_OPEN_FILES}",
    ]

    # As the invoking user, so files written through the bind mount are owned
    # by them. A container running as root leaves a repository whose new files
    # the operator cannot edit without sudo.
    current_uid, current_gid = _current_ids()
    uid = current_uid if uid is None else uid
    gid = current_gid if gid is None else gid
    if uid == 0:
        # Failing OPEN here would be the worst of both worlds: a container
        # advertised as unprivileged, running as root, with the repository
        # bind-mounted. A bare-metal install often runs jarvis as root because
        # that is how it reaches the docker socket — so this is a real
        # configuration, and it gets a refusal rather than a silent
        # `--user=0:0`.
        raise SandboxError(
            "jarvis-core is running as root, so a container would run as root "
            "too. Run it as an ordinary user and add that user to the `docker` "
            "group, or use rootless Docker."
        )
    argv.append(f"--user={uid}:{gid}")

    if environment.cache:
        # Per ENVIRONMENT, not per repository: two repositories using the same
        # image want the same wheels, and a volume per repository would defeat
        # the point. Mounted only at cache paths — never at the repository, and
        # never anywhere a package would be installed to.
        volume = f"jarvis-code-cache-{environment.name}"
        for destination in CACHE_PATHS:
            argv.append(f"--volume={volume}:{destination}")

    if not environment.networked:
        argv.append("--network=none")
    else:
        argv.append(f"--network={environment.network_name or 'bridge'}")

    for key in sorted(environment.env):
        argv.append(f"--env={key}={environment.env[key]}")

    argv.append(environment.image)
    argv.extend(["/bin/sh", "-c", command])
    return argv


def persisted_image(environment: Environment) -> str:
    """The image a persisting environment's tools are committed into."""
    return f"jarvis-code-env-{environment.name}:latest"


def exec_argv(
    name: str,
    command: str,
    *,
    docker: str = "docker",
    uid: int | None = None,
    gid: int | None = None,
    max_file_mb: int | None = None,
) -> list[str]:
    """Run a command inside an already-running session container.

    Most fences are on the container and `docker exec` cannot widen them: the
    network policy, the mounts, the capability set and the CGROUP limits
    (memory, cpus, pids) were decided at creation, and an exec joins the same
    cgroup.

    **Resource limits are the exception, and it is not a small one.** `ulimit`
    values are per-process, set by `setrlimit` on the container's INIT
    process; an exec is a new process spawned by the daemon and gets the
    daemon's defaults instead. So `--ulimit=fsize` — the only thing bounding
    what a job writes through the bind mount onto the host's disk — stopped
    applying the moment commands moved from `docker run` to `docker exec`,
    which is to say the moment a session existed. `docker exec` has no
    `--ulimit` flag to fix that with, so the limit is re-applied inside, by
    the shell, before the command runs.

    `ulimit -f` is in 512-byte blocks under POSIX `sh`. A `/bin/sh` that is
    really bash uses 1024, which makes the limit twice as generous as asked —
    the harmless direction for a disk-exhaustion guard, and not worth probing
    the shell to correct. Failures are swallowed: a shell that cannot lower
    its own limit should still run the command, and the container's cgroups
    are still there.
    """
    if not command.strip():
        raise SandboxError("no command to run")
    # Against the command the CALLER wrote. Measuring after the prologue was
    # prepended would move the limit by however long the prologue happens to
    # be, which is this function's business and not the caller's.
    if len(command) > MAX_COMMAND_CHARS:
        raise SandboxError(
            f"that command is {len(command)} characters; the limit is {MAX_COMMAND_CHARS}"
        )
    current_uid, current_gid = _current_ids()
    uid = current_uid if uid is None else uid
    gid = current_gid if gid is None else gid
    if max_file_mb is not None:
        command = (
            f"ulimit -f {max_file_mb * BLOCKS_PER_MB} 2>/dev/null; "
            f"ulimit -n {MAX_OPEN_FILES} 2>/dev/null; "
        ) + command
    return [
        docker,
        "exec",
        "--interactive=false",
        f"--workdir={WORK_DIR}",
        f"--user={uid}:{gid}",
        name,
        "/bin/sh",
        "-c",
        command,
    ]


class Session:
    """One container, alive for one job.

    ## Why not one container per command

    Because a coding job installs things, and the first version threw every
    installation away. `run_command("pip install pygame")` ran in a container
    that was removed the moment it exited; the next command ran in a fresh one
    with no pygame in it. The operator's own `setup:` was discarded the same
    way. "It can download and install what it needs" was not true of anything
    that outlived a single command — which is to say, of installing.

    So the container is created once, idles on `tail -f /dev/null`, and every
    command reaches it with `docker exec`. Installs persist for the length of
    the job and are destroyed with it.

    ## What still does not survive

    The job. `close()` removes the container, so the next job starts from the
    image again. That is deliberate: a container that outlived its job would be
    state a later job inherits without anyone deciding it should, and the whole
    argument for `--rm` was that nothing a job installs becomes permanent. A
    slow `pip install` every time is the price, and `cache: true` on the
    environment is the way to pay less of it without keeping the container.
    """

    def __init__(
        self,
        environment: Environment,
        repo_root: Path,
        *,
        docker: str = "docker",
        writable: bool = True,
        runner: Any = None,
    ) -> None:
        self.environment = environment
        self.repo_root = repo_root
        self.docker = docker
        self.writable = writable
        self._run = runner or _spawn_plain
        self.name = f"jarvis-code-{uuid.uuid4().hex[:16]}"
        self.started = False
        self._setup_done = False

    async def start(self) -> str:
        """Create the container. Returns "" or a sentence saying why not.

        Starts from the environment's PERSISTED image when it has one, so a
        toolchain a previous job installed is already there.
        """
        if self.started:
            return ""
        environment = self.environment
        if environment.persist:
            tag = persisted_image(environment)
            if await self._image_exists(tag):
                # `replace` rather than mutating: the configured image is what
                # a reset goes back to, and losing it here would make the reset
                # a no-op.
                environment = replace(environment, image=tag)
                _LOGGER.debug("code: reusing persisted image %s", tag)
        argv = container_argv(
            environment,
            self.repo_root,
            # Something that does nothing, for ever, in every image: `sleep
            # infinity` is GNU-only and busybox refuses it.
            "tail -f /dev/null",
            docker=self.docker,
            writable=self.writable,
            name=self.name,
            detach=True,
        )
        code, out = await self._run(argv, 120.0)
        if code != 0:
            return _explain_docker_failure(out)
        self.started = True
        return ""

    async def run(self, command: str, timeout: float | None = None) -> tuple[int, str]:
        if not self.started:
            problem = await self.start()
            if problem:
                return 1, problem
        limit = self.environment.timeout if timeout is None else timeout
        argv = exec_argv(
            self.name,
            command,
            docker=self.docker,
            max_file_mb=self.environment.max_file_mb,
        )
        code, out = await self._run(argv, limit, docker=self.docker, name=self.name)
        return code, out

    async def run_setup(self) -> tuple[int, str] | None:
        """The operator's `setup:`, once, inside THIS container."""
        if self._setup_done:
            return None
        self._setup_done = True
        script = setup_script(self.environment)
        if not script:
            return None
        return await self.run(script, timeout=self.environment.timeout)

    async def close(self, *, keep: bool = True) -> None:
        """Commit what was installed, then remove the container.

        `keep=False` for a job that failed in a way that makes its container
        not worth remembering — a half-applied `apt-get` is a worse starting
        point than the image it came from.
        """
        if not self.started:
            return
        self.started = False
        if keep and self.environment.persist:
            await self._commit()
        # Through the session's own runner, not `_kill_container`: the removal
        # is part of what a test of this class needs to see, and a path that
        # skipped the injected runner was a path nothing checked.
        code, out = await self._run(
            [self.docker, "rm", "--force", self.name], 60.0
        )
        if code != 0:
            _LOGGER.warning(
                "code: could not remove container %s (%s). `docker rm -f %s` "
                "will clear it.",
                self.name,
                (out or "").strip()[-200:],
                self.name,
            )

    async def _commit(self) -> None:
        tag = persisted_image(self.environment)
        code, out = await self._run(
            [
                self.docker,
                "commit",
                # No CMD change: the next `docker run` supplies its own, and a
                # committed CMD of `tail -f /dev/null` would be inherited by
                # anything that ran this image without one.
                self.name,
                tag,
            ],
            300.0,
        )
        if code != 0:
            _LOGGER.warning(
                "code: could not keep %s's tools (%s). The next job will start "
                "from %s again.",
                self.environment.name,
                (out or "").strip()[-200:],
                self.environment.image,
            )
            return
        _LOGGER.info(
            "code: kept %s's installed tools as %s", self.environment.name, tag
        )

    async def _image_exists(self, tag: str) -> bool:
        code, _out = await self._run(
            [self.docker, "image", "inspect", tag], 60.0
        )
        return code == 0


async def reset_environment(
    environment: Environment, *, docker: str = "docker", runner: Any = None
) -> tuple[bool, str]:
    """Throw away a persisting environment's image.

    The escape hatch for the cost `persist` carries: a job left something in
    there — a broken package set, a half-applied upgrade, anything worse — and
    every later job starts from it. This puts the environment back to the image
    the operator configured.
    """
    if not environment.persist:
        return False, f"{environment.name} does not keep anything, so there is nothing to reset."
    tag = persisted_image(environment)
    spawn = runner or _spawn_plain
    code, out = await spawn([docker, "image", "rm", "--force", tag], 120.0)
    if code != 0:
        said = (out or "").strip()
        if "no such image" in said.lower():
            return True, f"{environment.name} was already back to {environment.image}."
        return False, f"could not reset {environment.name}: {said[-200:]}"
    return True, (
        f"{environment.name} is back to {environment.image}. The next job will "
        "reinstall whatever it needs."
    )


def _explain_docker_failure(out: str) -> str:
    """Turn a docker error into something an operator can act on."""
    said = (out or "").strip()
    lowered = said.lower()
    if "permission denied" in lowered and "docker.sock" in lowered:
        return (
            "Jarvis cannot reach the Docker daemon: permission denied on the "
            "socket. Add the user jarvis-core runs as to the `docker` group, or "
            "use rootless Docker. " + said[-300:]
        )
    if "cannot connect to the docker daemon" in lowered:
        return (
            "The Docker daemon is not running, so the sandboxed environment "
            "cannot be used. Start it, or remove `environment:` from this "
            "repository to fall back to its declared checks."
        )
    if "manifest unknown" in lowered or "not found" in lowered and "pull" in lowered:
        return f"That environment's image could not be pulled: {said[-300:]}"
    return f"could not start the container: {said[-400:]}"


async def _spawn_plain(
    argv: list[str], timeout: float, *, docker: str = "docker", name: str = ""
) -> tuple[int, str]:
    """`_spawn`, named separately so `Session` can be given a fake."""
    return await _spawn(argv, timeout, docker=docker, name=name)


def setup_script(environment: Environment) -> str:
    """The environment's `setup:` commands as one script, or "".

    `set -e` so a failed install stops rather than leaving a half-built
    container that fails confusingly three commands later.
    """
    if not environment.setup:
        return ""
    return "set -e\n" + "\n".join(environment.setup)


async def _kill_container(docker: str, name: str) -> None:
    """Remove a container the client lost track of.

    `proc.kill()` sends SIGKILL to the local `docker` CLI. SIGKILL cannot be
    caught, so nothing is forwarded, and `--rm` only fires when a container
    EXITS — which one running `sleep 999999` never does. Without this the
    container outlives the job for ever, still holding the repository mounted
    read-write.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            docker,
            "rm",
            "--force",
            name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), 30)
    except Exception:  # noqa: BLE001 - best effort on a path that already failed
        _LOGGER.warning(
            "Could not remove container %s; it may still be running. "
            "`docker rm -f %s` will clear it.",
            name,
            name,
        )


async def _spawn(
    argv: list[str], timeout: float, *, docker: str = "docker", name: str = ""
) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 1, (
            "docker is not installed on this server, so the sandboxed "
            "environment cannot be used. Install Docker, or remove the "
            "`environment:` from this repository to fall back to its declared "
            "checks."
        )
    except (OSError, ValueError) as err:
        return 1, f"could not start the container: {err}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        # And the container itself, which does NOT die with its client.
        if name:
            await _kill_container(docker, name)
        return 1, f"timed out after {timeout:.0f}s; the container was killed"
    return proc.returncode or 0, out.decode("utf-8", "replace")[-MAX_OUTPUT_CHARS:]


def check_command(environment: Environment | None, command: str) -> str:
    """Normalise a command for comparison, or "" if it is unusable."""
    text = " ".join(str(command or "").split())
    if not text or len(text) > MAX_COMMAND_CHARS:
        return ""
    return text


def quote(argv: list[str]) -> str:
    """The argv as a copy-pasteable line, for logs and the console."""
    return " ".join(shlex.quote(part) for part in argv)
