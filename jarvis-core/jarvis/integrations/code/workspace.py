"""Repositories Jarvis may work in, and the git around them.

A repo is a directory the operator named. Everything a coding job does happens
inside one, on a **branch of its own**, and nothing is ever committed to the
branch somebody is working on.

## Why a branch and not a copy

A copy is tidier in theory and wrong in practice: a repo with submodules, a
virtualenv, a `node_modules` or a build cache is gigabytes, and copying it per
job turns "have a look at this" into a disk-space incident. A branch costs
nothing, is what a person would do, and leaves the change reviewable with the
tools they already have.

The rule that makes that safe is that Jarvis **never checks out over your
work**: a job refuses to start on a dirty tree unless told to, it makes its own
branch from wherever HEAD is, and the only way its branch reaches yours is a
person merging it.

## Confinement

Paths inside a repo go through `integrations/files/paths.py` — the same module,
already tested against every traversal this author could think of, including
the symlink one that no amount of string work can see. There is no second
implementation here, deliberately: two path checkers is one path checker and a
bug.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..files.paths import PathRefused, resolve_local  # noqa: F401 - re-exported

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "BRANCH_PREFIX",
    "HOST_GIT_GUARDS",
    "GitError",
    # Re-exported so the agent can catch one refusal type rather than
    # importing the files integration to name it.
    "PathRefused",
    "Repo",
    "RepoFile",
    "Workspace",
    "branch_name",
    "check_argv",
    "repo_from_dict",
]

#: Every branch a job makes starts with this, so `git branch --list 'jarvis/*'`
#: is the complete list of what Jarvis has ever done here.
BRANCH_PREFIX = "jarvis"

GIT_TIMEOUT = 120.0

#: Prepended to EVERY host git invocation.
#:
#: git is configurable enough to be an execution primitive, and its
#: configuration lives in files inside the repository — which is exactly what a
#: coding job is allowed to change. Each of these closes one verified way for a
#: job to get a command run on the host:
#:
#:   core.hooksPath=/dev/null   `.git/hooks/*` — a `post-checkout` hook runs on
#:                              the `git checkout -B` that starts every job.
#:   core.fsmonitor=           a command git runs on `status` and `add`.
#:   protocol.ext.allow=never  the `ext::` transport is "run this command", and
#:                              a submodule URL is a place to put one.
#:
#: `diff` adds `--no-ext-diff --no-textconv` on top; see `diff()`.
def _host_git_env() -> dict[str, str]:
    """The environment every HOST git runs with.

    `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` are pointed at nothing on
    purpose. A `filter.x.clean` in the OPERATOR's `~/.gitconfig` is activated
    by a `.gitattributes` line — and `.gitattributes` is an ordinary file in
    the working tree that a job may write. Reading neither means a job cannot
    reach a driver it did not also have to define in the repository, where the
    scan will find it.
    """
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


HOST_GIT_GUARDS = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=",
    "-c", "protocol.ext.allow=never",
)
#: A diff longer than this is not a change anybody is going to review; it is a
#: reformat or a checked-in build directory, and truncating it says so.
MAX_DIFF_BYTES = 400_000
MAX_LIST_ENTRIES = 400

#: Directories never listed, read or searched. Not security — the path checker
#: is that — but signal: a model that reads `node_modules` has spent its whole
#: context before reaching any of your code.
SKIP_DIRS = frozenset(
    {
        ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "dist", "build", ".svelte-kit",
        ".gradle", "target", ".next", ".terraform", ".tox",
    }
)


class GitError(RuntimeError):
    """A git command that failed, with what it said."""


@dataclass
class Repo:
    name: str
    path: str
    #: What it is, for the model's benefit. Free text from the operator.
    description: str = ""
    #: Commands a job may run to check its work, e.g. `pytest -q`. Only these:
    #: see `agent.py` for why a job does not get a shell.
    checks: list[str] = field(default_factory=list)
    #: False means a job may read and propose, never write. The default.
    writable: bool = False
    #: The name of a `code: environments:` entry, or "".
    #:
    #: With one, a job may run ARBITRARY commands — inside that container, with
    #: the repository as its only host path. Without one, it may run only the
    #: strings in `checks:` and there is no shell anywhere. That is the whole
    #: difference, and it is per repository so a throwaway scratch project can
    #: have a networked toolchain while your real one has neither.
    environment: str = ""
    #: `<forge>:<owner/name>` for a clone, else "". What `push` sends back to.
    origin: str = ""
    #: True for a repository Jarvis created inside the workspace root, false
    #: for one declared in configuration.yaml. The console shows which, because
    #: "I made this" and "you pointed me at this" deserve different care.
    managed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "checks": list(self.checks),
            "writable": self.writable,
            "environment": self.environment,
            "managed": self.managed,
            "origin": self.origin,
        }


def repo_from_dict(raw: Any) -> Repo | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    path = str(raw.get("path") or "").strip()
    if not name or not path:
        return None
    return Repo(
        name=name,
        path=path,
        description=str(raw.get("description") or "")[:300],
        checks=[str(c) for c in (raw.get("checks") or []) if str(c).strip()][:8],
        writable=bool(raw.get("writable")),
        environment=str(raw.get("environment") or "").strip(),
    )


def branch_name(job_id: str, now: float | None = None) -> str:
    """`jarvis/<date>-<job>`, so a stale one is obvious in `git branch`."""
    stamp = time.strftime("%Y%m%d", time.localtime(now if now else time.time()))
    return f"{BRANCH_PREFIX}/{stamp}-{job_id}"


@dataclass
class RepoFile:
    path: str
    is_dir: bool
    size: int = 0


class Workspace:
    """One repo, opened for one job."""

    def __init__(
        self,
        repo: Repo,
        *,
        runner=None,
        sandbox: list[str] | None = None,
        environment: Any = None,
    ) -> None:
        self.repo = repo
        self.root = Path(repo.path).expanduser()
        #: Injected so a test can drive git without a repository.
        self._run = runner or _run_git
        #: A command prefix every CHECK runs behind — see `sandbox_argv`. Not
        #: applied to git: confining the thing that makes the branch would
        #: leave nothing to review.
        self.sandbox = list(sandbox or [])
        #: A `sandbox.Environment`, or None. With one, commands run in a
        #: container whose only host path is this repository, and the agent
        #: gains `run_command`. Without one it has no shell at all.
        self.environment = environment
        #: The live container, once a job has asked for one.
        self._session: Any = None

    @property
    def sandboxed(self) -> bool:
        return self.environment is not None

    async def open_session(self):
        """The container this job's commands run in, created once.

        One per JOB, not one per command: the first version spawned a fresh
        container for every command, so `pip install` was thrown away before
        the next line could use it. Installing was, in effect, impossible.
        """
        from .sandbox import SandboxError, Session

        if self.environment is None:
            raise SandboxError(
                f"{self.repo.name} has no environment, so there is nothing to "
                "run commands in."
            )
        if self._session is None:
            self._session = Session(
                self.environment,
                self.root,
                writable=self.repo.writable,
            )
        return self._session

    async def run_sandboxed(self, command: str, *, timeout: float | None = None):
        """One command inside this repository's environment.

        Raises if there is no environment rather than falling back to the host:
        "run it here instead" is exactly the mistake this whole module exists
        to make impossible.
        """
        session = await self.open_session()
        return await session.run(command, timeout=timeout)

    async def close_session(self, *, keep: bool = True) -> None:
        """Commit the tools (if the environment persists) and remove it."""
        if self._session is not None:
            await self._session.close(keep=keep)
            self._session = None

    def sandbox_argv(self, argv: list[str]) -> list[str]:
        """A check command, behind the operator's wrapper if they set one.

        `{repo}` in the wrapper becomes the repository's absolute path, so a
        `docker run -v {repo}:/w -w /w` reads the way somebody would write it
        by hand. Nothing else is substituted: a wrapper is the operator's
        command line and rewriting more of it would be this module guessing at
        a container runtime it does not know.
        """
        if not self.sandbox:
            return argv
        prefix = [part.replace("{repo}", str(self.root)) for part in self.sandbox]
        return prefix + argv

    # --- files ------------------------------------------------------------
    def resolve(self, path: str) -> Path:
        """Inside the repo, or `PathRefused`. One implementation, not two."""
        return resolve_local(self.root, path)

    def resolve_for_write(self, path: str) -> Path:
        """Inside the repo AND outside `.git`.

        ## Why `.git` is not just another directory

        git executes things out of it. A file written to `.git/hooks/post-checkout`
        runs — on the HOST, as the user jarvis-core runs as — the next time
        anything does `git checkout`, which this module does at the start of
        every job. `.git/config` is worse: a `diff.<name>.textconv` or a
        `filter.<name>.clean` entry, paired with a `.gitattributes` line, is
        executed by the `git diff` and `git add` this module runs when a job
        finishes.

        All three were verified to execute against real git. That made "the
        agent has no shell" false in the only way that matters: it had no
        shell, and could write a file git would run as one. It predates the
        container work — `write_file` on any writable repository was enough.

        There is no legitimate reason for a coding job to write into `.git`.
        Reading it is fine and stays allowed; this is the write path only.
        """
        target = resolve_local(self.root, path)
        root = self.root.resolve()
        try:
            relative = target.resolve().relative_to(root)
        except ValueError:
            # `resolve_local` already refuses anything outside the root; this
            # is belt and braces for a target that resolves oddly.
            raise PathRefused(f"{path!r} is not inside {self.repo.name}") from None
        if relative.parts and relative.parts[0] == ".git":
            raise PathRefused(
                "writing inside .git is refused: git executes hooks, filters "
                "and diff drivers from there, on the host. Change the working "
                "tree instead."
            )
        return target

    def read(self, path: str, *, limit: int = 400_000) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"{path} is not a file in {self.repo.name}")
        raw = target.read_bytes()[: limit + 1]
        if len(raw) > limit:
            raise ValueError(
                f"{path} is larger than {limit} bytes; work on it in pieces"
            )
        return raw.decode("utf-8", "replace")

    def write(self, path: str, text: str) -> int:
        target = self.resolve_for_write(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        target.write_bytes(data)
        return len(data)

    def listing(self, path: str = "", *, depth: int = 2) -> list[RepoFile]:
        """What is here, skipping the directories nobody wants read."""
        base = self.resolve(path)
        if not base.is_dir():
            raise FileNotFoundError(f"{path or '/'} is not a folder in {self.repo.name}")
        out: list[RepoFile] = []
        self._walk(base, base, depth, out)
        out.sort(key=lambda f: (not f.is_dir, f.path))
        return out[:MAX_LIST_ENTRIES]

    def _walk(self, base: Path, here: Path, depth: int, out: list[RepoFile]) -> None:
        if depth < 0 or len(out) >= MAX_LIST_ENTRIES:
            return
        try:
            children = sorted(here.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for child in children:
            if child.name in SKIP_DIRS or child.name.startswith(".git"):
                continue
            # A symlink is a string in the container and a live pointer on the
            # host. `ln -s / /work/up` is worthless inside — it points at the
            # container's own root — and on this side it points at the
            # operator's. `is_dir()`/`is_file()` both follow it, so the walk
            # has to refuse before it asks.
            if child.is_symlink():
                continue
            relative = str(child.relative_to(base))
            if child.is_dir():
                out.append(RepoFile(path=relative, is_dir=True))
                self._walk(base, child, depth - 1, out)
            else:
                try:
                    out.append(RepoFile(relative, False, child.stat().st_size))
                except OSError:
                    continue
            if len(out) >= MAX_LIST_ENTRIES:
                return

    def files_for_search(self, limit: int = 2000) -> list[Path]:
        found: list[Path] = []
        stack = [self.root]
        while stack and len(found) < limit:
            here = stack.pop()
            try:
                children = list(here.iterdir())
            except OSError:
                continue
            for child in children:
                if child.name in SKIP_DIRS or child.name.startswith(".git"):
                    continue
                # Same reason as `_walk`: `search` reads what this returns and
                # hands matching lines to the model, so a symlink out of the
                # repository is an arbitrary host-file read.
                if child.is_symlink():
                    continue
                if child.is_dir():
                    stack.append(child)
                elif child.is_file():
                    found.append(child)
                    if len(found) >= limit:
                        break
        return found

    # --- git --------------------------------------------------------------
    async def git(self, *args: str, timeout: float = GIT_TIMEOUT) -> str:
        """One host git command, refusing if the repo's config would run code.

        The check is here — before EVERY invocation — and not once at the top
        of a job, because the job is what writes the config. A sandboxed job
        edits `.git/config` through the bind mount, which is not a path any of
        this module's write guards can see, and the poisoned `git add` comes
        after. Checking once was a time-of-check/time-of-use bug with the
        window deliberately held open by the model.
        """
        problem = self.unsafe_git_config()
        if problem:
            raise GitError(problem)
        code, out, err = await self._run(
            [*HOST_GIT_GUARDS, *args], self.root, timeout, env=_host_git_env()
        )
        if code != 0:
            raise GitError(f"git {' '.join(args)}: {(err or out).strip()[:400]}")
        return out

    def unsafe_git_config(self) -> str:
        """A repository whose own config would make host git run something.

        The flags in `HOST_GIT_GUARDS` close hooks, fsmonitor and the diff
        drivers. They cannot close **clean/smudge filters**: `filter.X.clean`
        is looked up by name from whatever `.gitattributes` says, so there is
        no fixed `-c` that disables it, and `git add -A` runs it. Verified
        executing against real git.

        So filters get a check instead of a flag. Returns "" when the config is
        safe, or a sentence naming the key when it is not — the caller refuses
        rather than running git and hoping.

        Reads `.git/config` textually rather than through `git config`, because
        asking git to tell you whether git is about to run something is asking
        the wrong party.
        """
        problem = self._unsafe_config_file(self.root / ".git" / "config")
        if problem:
            return problem
        # An executable hook is the other half. `core.hooksPath=/dev/null`
        # stops git running them, but a hook on disk is a loaded gun pointed at
        # any OTHER git — the operator's own shell, a cron job, an editor — so
        # it is reported rather than relied upon not to fire.
        hooks = self.root / ".git" / "hooks"
        try:
            for entry in hooks.iterdir():
                if entry.suffix == ".sample" or not entry.is_file():
                    continue
                if entry.stat().st_mode & 0o111:
                    return (
                        f"{entry} is an executable git hook. Jarvis will not run "
                        "git in this repository until it is removed — a job that "
                        "wrote it would be choosing what the host executes."
                    )
        except OSError:
            pass
        return ""

    def _unsafe_config_file(self, config: Path, depth: int = 0) -> str:
        """One git config file, and anything it includes.

        `[include]` is followed because that is the whole point of it: a
        one-line `path = evil` turns the scan below into theatre if the
        included file is not read too.
        """
        if depth > 4:
            return f"{config} includes too many files to check"
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

        # Followed first: an include can define any of the keys below.
        for match in re.finditer(
            r"^\s*path\s*=\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE
        ):
            raw = match.group(1).strip().strip('"')
            if not raw:
                continue
            included = Path(raw).expanduser()
            if not included.is_absolute():
                included = config.parent / included
            problem = self._unsafe_config_file(included, depth + 1)
            if problem:
                return f"{config} includes {included}, and {problem}"

        for key in (
            "clean",
            "smudge",
            "process",
            "textconv",
            "external",
            "fsmonitor",
            "hookspath",
            "sshcommand",
            "helper",
            "pager",
            "editor",
            "askpass",
        ):
            # Not anchored to the line start: git accepts `[a] b = c` on one
            # line, and an anchored pattern reads the file and sees nothing.
            if re.search(rf"(?:^|\s|\]){key}\s*=", text, re.IGNORECASE | re.MULTILINE):
                return (
                    f"{config} sets {key!r}, which tells git to run a command. "
                    "Jarvis will not run git in this repository until that is "
                    "removed — a job that wrote it would be choosing what the "
                    "host executes."
                )
        return ""

    async def is_repo(self) -> bool:
        try:
            await self.git("rev-parse", "--git-dir")
            return True
        except GitError:
            return False

    async def is_dirty(self) -> bool:
        return bool((await self.git("status", "--porcelain")).strip())

    async def current_branch(self) -> str:
        return (await self.git("rev-parse", "--abbrev-ref", "HEAD")).strip()

    async def start_branch(self, name: str) -> str:
        """Make and check out a branch from wherever HEAD is.

        `-B` rather than `-b`: a job re-run after a crash would otherwise fail
        on "branch already exists", and the branch it would be re-using is its
        own from moments ago.
        """
        await self.git("checkout", "-B", name)
        return name

    async def diff(self) -> tuple[str, str]:
        """The working tree against HEAD: the patch, and its stat line.

        `--no-ext-diff` and `--no-textconv` are not cosmetic. Both name a
        command in `.git/config` that git runs to produce the diff, and
        `.git/config` is a file inside the repository — so without these, a job
        that wrote one would have the host run it while jarvis-core was merely
        looking at what the job did.
        """
        await self.git("add", "-A", "--intent-to-add")
        patch = await self.git("diff", "--no-color", "--no-ext-diff", "--no-textconv")
        stat = await self.git(
            "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--stat"
        )
        return patch[:MAX_DIFF_BYTES], stat.strip()[-2000:]

    async def commit(self, message: str) -> str:
        await self.git("add", "-A")
        await self.git(
            "-c", "user.email=jarvis@local", "-c", "user.name=Jarvis",
            "commit", "-m", message[:2000],
        )
        return (await self.git("rev-parse", "--short", "HEAD")).strip()

    async def push(
        self, forge: Any, branch: str, *, config_dir: Path
    ) -> tuple[bool, str]:
        """Send one Jarvis branch to the forge. Never `main`, never a rewrite.

        Three refusals before anything leaves the machine:

        * the branch must be one Jarvis made (`jarvis/…`). Pushing `main` would
          put a model's work on the branch other people build from, with no
          review anywhere.
        * `origin` must still point at the forge it was cloned from and carry
          no embedded credential. A remote can be rewritten by anything that
          can write `.git/config` — including a previous job.
        * no force, ever. `--force-with-lease` is still a rewrite of somebody
          else's history if you guess the lease right; a coding agent has no
          business doing either.
        """
        from .forges import check_remote_url, git_env, is_jarvis_branch, redact

        if not is_jarvis_branch(branch):
            return False, (
                f"{branch!r} is not a branch Jarvis made. It only pushes its own "
                "`jarvis/…` branches — never main."
            )
        try:
            remote = (await self.git("remote", "get-url", "origin")).strip()
        except GitError as err:
            return False, f"no origin to push to: {err}"
        problem = check_remote_url(remote, forge)
        if problem:
            return False, f"refusing to push: {problem}"

        code, out, err = await self._run(
            [*HOST_GIT_GUARDS, "push", "--set-upstream", "origin", branch],
            self.root,
            GIT_TIMEOUT,
            env=git_env(forge, config_dir),
        )
        if code != 0:
            return False, f"push failed: {redact(err or out, forge).strip()[:300]}"
        return True, f"pushed {branch} to {forge.name}"

    async def discard(self) -> None:
        """Throw the job's changes away, leaving the branch it made behind.

        Not `checkout -` and not a branch delete: the branch is the record of
        what was attempted, and somebody looking at a failed job wants to see
        it rather than to find it gone.
        """
        await self.git("checkout", "--", ".")
        await self.git("clean", "-fd")


async def _run_git(
    args: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run one git command. Never a shell — the arguments come from a model.

    `create_subprocess_exec`, not `_shell`: with a shell, an argument
    containing `;` is a second command, and half of these arguments are
    branch names and commit messages a model wrote.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            # Nothing is ever typed at a job. Inheriting this process's stdin
            # means a git that decides to prompt — for a passphrase, for
            # credentials — waits on a terminal nobody is sitting at, and the
            # job hangs until its own wall clock kills it. Closed is the honest
            # answer: git fails, says it could not read a password, and the
            # operator gets a sentence instead of a stall.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Only when a caller supplies one — a credential reaches git this
            # way and nowhere else. `None` inherits, which is what every local
            # operation wants.
            env=env,
        )
    except (OSError, ValueError) as err:
        return 1, "", f"could not run git: {err}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        return 1, "", f"git {args[0] if args else ''} timed out after {timeout:.0f}s"
    return (
        proc.returncode or 0,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def check_argv(command: str) -> list[str]:
    """One of the repo's own check commands, split for exec.

    Split with `shlex` and run without a shell, for the same reason as git: a
    check command is configuration, but it lands next to model-authored data
    often enough that "it is only ever the operator's string" is a property
    worth not relying on.
    """
    argv = shlex.split(command)
    if not argv:
        raise ValueError("empty check command")
    return argv
