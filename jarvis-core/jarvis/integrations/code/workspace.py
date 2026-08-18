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
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..files.paths import PathRefused, resolve_local  # noqa: F401 - re-exported

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "BRANCH_PREFIX",
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "checks": list(self.checks),
            "writable": self.writable,
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

    def __init__(self, repo: Repo, *, runner=None, sandbox: list[str] | None = None) -> None:
        self.repo = repo
        self.root = Path(repo.path).expanduser()
        #: Injected so a test can drive git without a repository.
        self._run = runner or _run_git
        #: A command prefix every CHECK runs behind — see `sandbox_argv`. Not
        #: applied to git: confining the thing that makes the branch would
        #: leave nothing to review.
        self.sandbox = list(sandbox or [])

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
        target = self.resolve(path)
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
                if child.is_dir():
                    stack.append(child)
                elif child.is_file():
                    found.append(child)
                    if len(found) >= limit:
                        break
        return found

    # --- git --------------------------------------------------------------
    async def git(self, *args: str, timeout: float = GIT_TIMEOUT) -> str:
        code, out, err = await self._run(list(args), self.root, timeout)
        if code != 0:
            raise GitError(f"git {' '.join(args)}: {(err or out).strip()[:400]}")
        return out

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
        """The working tree against HEAD: the patch, and its stat line."""
        await self.git("add", "-A", "--intent-to-add")
        patch = await self.git("diff", "--no-color")
        stat = await self.git("diff", "--no-color", "--stat")
        return patch[:MAX_DIFF_BYTES], stat.strip()[-2000:]

    async def commit(self, message: str) -> str:
        await self.git("add", "-A")
        await self.git(
            "-c", "user.email=jarvis@local", "-c", "user.name=Jarvis",
            "commit", "-m", message[:2000],
        )
        return (await self.git("rev-parse", "--short", "HEAD")).strip()

    async def discard(self) -> None:
        """Throw the job's changes away, leaving the branch it made behind.

        Not `checkout -` and not a branch delete: the branch is the record of
        what was attempted, and somebody looking at a failed job wants to see
        it rather than to find it gone.
        """
        await self.git("checkout", "--", ".")
        await self.git("clean", "-fd")


async def _run_git(
    args: list[str], cwd: Path, timeout: float
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
