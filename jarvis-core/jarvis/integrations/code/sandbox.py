"""Environments a coding job may run in, and the fences around them.

Jarvis Code began with no shell at all. `run_check` matched a whole string
against the repository's own `checks:` list and nothing else existed, and the
argument for that was simple: a coding loop that can run arbitrary commands on
the host is the largest hole anybody has ever put in this codebase.

That argument is still true, and this module does not weaken it. What it adds
is a *second* place for commands to run — a container that is thrown away when
the job ends — and a shell that exists **only there**. On the host there is
still no shell, and a repository with no environment behaves exactly as before.

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
    -v <repo>:/work  -w /work   THE ONLY host path in the container. Not the
                                workspace root, not the parent — one repo.

And the negatives, which matter as much: no `--privileged`, no
`-v /var/run/docker.sock` (a container that can talk to the daemon can start
another container that mounts anything), no host networking, and none of the
operator's environment variables. `env:` on the environment is an explicit
allow-list written in configuration.yaml.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_IMAGE",
    "Environment",
    "NETWORK_EGRESS",
    "NETWORK_NONE",
    "SandboxError",
    "container_argv",
    "environment_from_dict",
    "run_in_container",
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
#: Longest a single command may run. A build is minutes; nothing legitimate in
#: a coding job is an hour, and a wedged process must not hold the job open.
DEFAULT_TIMEOUT = 900.0
MAX_TIMEOUT = 3600.0

#: An environment name is used in log lines and config; keep it boring.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: An image reference. Deliberately permissive about registries and digests and
#: strict about everything that is not one — no spaces, no shell characters.
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")
#: Environment variable names an environment may set.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


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
        }

    def describe(self) -> str:
        """One line for a human deciding whether to trust it."""
        reach = (
            "can reach the network, including this LAN"
            if self.networked
            else "no network"
        )
        return f"{self.image} · {reach} · {self.memory} RAM · {self.cpus} CPU"


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

    if not environment.networked:
        argv.append("--network=none")
    else:
        argv.append(f"--network={environment.network_name or 'bridge'}")

    for key in sorted(environment.env):
        argv.append(f"--env={key}={environment.env[key]}")

    argv.append(environment.image)
    argv.extend(["/bin/sh", "-c", command])
    return argv


def setup_script(environment: Environment) -> str:
    """The environment's `setup:` commands as one script, or "".

    `set -e` so a failed install stops rather than leaving a half-built
    container that fails confusingly three commands later.
    """
    if not environment.setup:
        return ""
    return "set -e\n" + "\n".join(environment.setup)


async def run_in_container(
    environment: Environment,
    repo_root: Path,
    command: str,
    *,
    docker: str = "docker",
    timeout: float | None = None,
    writable: bool = True,
    runner: Any = None,
) -> tuple[int, str]:
    """Run one command in the environment. Returns `(exit code, output)`.

    `runner` is injected by tests so the whole path above can be exercised
    without a daemon. In production it is `_spawn`, which runs the argv with
    `create_subprocess_exec` — never a shell on this side.
    """
    # A name, so a timeout has something to kill. Random rather than derived
    # from the repository: two jobs in the same repository must not collide,
    # and a predictable name is one another container could squat.
    name = f"jarvis-code-{uuid.uuid4().hex[:16]}"
    argv = container_argv(
        environment, repo_root, command, docker=docker, writable=writable, name=name
    )
    limit = environment.timeout if timeout is None else timeout
    if runner is not None:
        return await runner(argv, limit)
    return await _spawn(argv, limit, docker=docker, name=name)


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
