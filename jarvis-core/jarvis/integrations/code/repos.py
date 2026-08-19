"""Repositories Jarvis may make, and where it is allowed to make them.

Until now every repository was declared in `configuration.yaml` by hand. That
is the right default — it is what makes "only paths the operator named exist" a
true sentence — but it means Jarvis cannot start anything. Asked for a Snake
game, the honest answer was "there is nowhere to put it".

## The workspace root

One directory. `~/jarvis/workspaces` unless the operator names another:

    code:
      workspace: ~/somewhere/else    # or `off` to refuse creation entirely

Inside it, Jarvis may create repositories freely. Outside it, nothing changes:
there is still no tool that takes a path, and a repository declared under
`repositories:` is still wherever the operator put it.

That one setting is the whole permission model, and it is worth being clear
about what it does and does not grant. It grants: make a directory, `git init`,
write a README, and — with an environment — run a build in there. It does not
grant: reaching anything above the root. Every path goes through the same
resolver as everything else in this package (`files/paths.py`), including its
symlink check, so a name is confined the same way a file path is.

This used to default to OFF, which read as cautious and was mostly just
broken: on a fresh install the answer to "write me a Snake game" was "there is
nowhere to put it", and the fix was a key nobody knew to look for. `off` still
refuses, and says so in those words rather than pointing at a key that is
already set.

## Why names are strict

A repository name becomes a directory name, a git branch prefix, a container
mount and a line in the console. `..`, a leading dash (which is an argument to
half the tools this touches), a space, a NUL — each of those breaks something
different, and none of them is a name anybody wants. So the rule is narrow and
the refusal says what is allowed.

## Persistence

Created repositories are recorded in `<config>/.storage/code_repos.json` and
reloaded at start. Without that, a repository Jarvis made would vanish from the
listing on restart while still sitting on disk — present but unreachable, which
is worse than either.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..files.paths import PathRefused, resolve_local
from .workspace import Repo

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "MAX_REPOS",
    "RepoStore",
    "check_name",
    "describe_new_repo",
    "git_problem",
    "initial_files",
]

#: A directory name, a branch prefix, a container mount and a console row.
#: Lowercase because a case-insensitive filesystem would otherwise let `Foo`
#: and `foo` be two registry entries and one directory.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Names that are a directory somewhere, an argument somewhere else, or simply
#: too confusing to allow.
_RESERVED = frozenset(
    {
        "con", "prn", "aux", "nul", "com1", "lpt1",  # Windows devices
        "git", ".git", "node_modules", "__pycache__", "venv", ".venv",
        "tmp", "temp", "test", "dist", "build",
    }
)

MAX_REPOS = 100
STORE_KEY = "code_repos"


async def git_problem(runner: Any, cwd: Path) -> str:
    """Empty when git can run here; otherwise a sentence saying how to fix it.

    Asked BEFORE anything is created. Without this the first git command is
    the one that fails, and it fails after the directory and the README are
    already on disk — which left a half-made repository that the next attempt
    then refused as "already exists". The operator saw an errno and a name
    they could no longer use.

    Through the injected runner rather than `shutil.which`, so a test that
    supplies a fake git is not forced to have a real one.
    """
    from .workspace import GIT_MISSING

    code, _out, err = await runner(["--version"], cwd, 30.0)
    if code == 0:
        return ""
    text = (err or "").strip()
    # The wording lives in `workspace._run_git`, which is where the missing
    # binary is actually noticed; a fake runner in a test may hand back either
    # that sentence or a raw errno, and both mean the same thing.
    if text == GIT_MISSING or "No such file or directory" in text:
        return GIT_MISSING
    return f"git does not work here: {text[:200]}"


def check_name(name: Any) -> str:
    """Return "" if the name is usable, or a sentence saying why not.

    A sentence rather than a bool: this reaches a person typing into a form and
    a model choosing a name, and both can act on "lowercase letters, digits,
    dot, dash and underscore" in a way they cannot act on `False`.
    """
    text = str(name or "").strip()
    if not text:
        return "A repository needs a name."
    if len(text) > 64:
        return "That name is too long — 64 characters at most."
    if text != text.lower():
        return "Use lowercase: a name is a directory, and some filesystems do not tell 'Foo' from 'foo'."
    if not _NAME_RE.match(text):
        return (
            "Use lowercase letters, digits, dot, dash and underscore, starting "
            "with a letter or digit. No spaces or slashes — the name becomes a "
            "directory and a git branch."
        )
    if ".." in text:
        return "A name may not contain '..'."
    if text in _RESERVED:
        return f"{text!r} is reserved — it means something else to git or the filesystem."
    return ""


def initial_files(name: str, description: str = "") -> dict[str, str]:
    """What a new repository starts with.

    A README and a `.gitignore`, and nothing else. Not a language scaffold:
    guessing wrong costs the first job a cleanup, and the job is about to write
    the real files anyway. The README says who made it, because a directory
    that appeared on your disk should explain itself.
    """
    heading = description.strip() or name
    return {
        "README.md": (
            f"# {name}\n\n{heading}\n\n"
            "Created by Jarvis. Work happens on `jarvis/<date>-<job>` branches; "
            "nothing reaches `main` without you merging it.\n"
        ),
        ".gitignore": (
            "__pycache__/\n*.py[cod]\n.venv/\nvenv/\nnode_modules/\n"
            "dist/\nbuild/\n.env\n.DS_Store\n"
        ),
    }


def describe_new_repo(name: str, path: Path, writable: bool) -> str:
    return f"{name} at {path}" + ("" if writable else " (read-only)")


@dataclass
class CreatedRepo:
    """One repository Jarvis made, as it is remembered across restarts."""

    name: str
    path: str
    description: str = ""
    environment: str = ""
    created: float = 0.0
    #: `<forge>:<owner/name>` when it came from GitHub or GitLab, else "".
    origin: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "environment": self.environment,
            "created": self.created,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CreatedRepo | None":
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        path = str(raw.get("path") or "").strip()
        if not name or not path:
            return None
        try:
            created = float(raw.get("created") or 0.0)
        except (TypeError, ValueError):
            created = 0.0
        return cls(
            name=name,
            path=path,
            description=str(raw.get("description") or "")[:300],
            environment=str(raw.get("environment") or "").strip(),
            created=created,
            origin=str(raw.get("origin") or "").strip(),
        )

    def as_repo(self, checks: list[str] | None = None) -> Repo:
        """A repository Jarvis made is writable by definition.

        Unlike a declared one, where `writable: false` is the default: the
        operator did not point Jarvis at their existing project, Jarvis made
        this directory for the job that asked for it.
        """
        return Repo(
            name=self.name,
            path=self.path,
            description=self.description,
            checks=list(checks or []),
            writable=True,
            environment=self.environment,
            managed=True,
            origin=self.origin,
        )


def _remove_tree(target: Path) -> None:
    """Undo a half-made repository. Best effort, and never anything else.

    Only ever called on a path this module just created — `async_create`
    refuses when `target.exists()`, so reaching the `mkdir` means it did not
    exist a moment ago. Jarvis does not delete repositories; it does clean up
    after itself.
    """
    import shutil

    try:
        shutil.rmtree(target)
    except OSError as err:  # pragma: no cover - best effort
        _LOGGER.warning("code: could not clean up %s: %s", target, err)


class RepoStore:
    """The repositories Jarvis made, on disk and in memory."""

    def __init__(self, store: Any, workspace: Path | None) -> None:
        self._store = store
        self.workspace = workspace
        self.repos: dict[str, CreatedRepo] = {}

    @property
    def enabled(self) -> bool:
        return self.workspace is not None

    async def async_load(self) -> None:
        data = await self._store.load() if self._store is not None else None
        for raw in (data or {}).get("repositories") or []:
            entry = CreatedRepo.from_dict(raw)
            if entry is None:
                continue
            # A repository somebody deleted from disk should not haunt the
            # listing: a row that cannot be opened is worse than no row.
            if not Path(entry.path).expanduser().is_dir():
                _LOGGER.info(
                    "code: forgetting %s — %s is not there any more",
                    entry.name,
                    entry.path,
                )
                continue
            self.repos[entry.name] = entry

    async def async_save(self) -> None:
        if self._store is None:
            return
        await self._store.save(
            {"repositories": [entry.as_dict() for entry in self.repos.values()]}
        )

    def resolve(self, name: str) -> Path:
        """Where a repository of this name would live. Confined to the root."""
        if self.workspace is None:
            raise PathRefused("no workspace is configured")
        return resolve_local(self.workspace, name)

    async def async_create(
        self,
        name: str,
        *,
        description: str = "",
        environment: str = "",
        taken: set[str] | None = None,
        git: Any = None,
    ) -> tuple[CreatedRepo | None, str]:
        """Make one. Returns `(repo, "")` or `(None, why not)`.

        `git` is injected so a test can drive this without a git binary; in
        production it is `workspace._run_git`.
        """
        if self.workspace is None:
            return None, (
                "Creating repositories is turned off: `code: workspace:` is set to `off` in configuration.yaml. Remove that line to get the default (~/jarvis/workspaces), or name a directory of your own."
            )
        problem = check_name(name)
        if problem:
            return None, problem
        name = str(name).strip()
        if name in self.repos:
            return None, f"There is already a repository called {name!r}."
        if name in (taken or set()):
            return None, (
                f"{name!r} is the name of a repository declared in "
                "configuration.yaml. Pick another."
            )
        if len(self.repos) >= MAX_REPOS:
            return None, f"There are already {MAX_REPOS} repositories."

        try:
            target = self.resolve(name)
        except PathRefused as err:
            return None, f"That name is not allowed here: {err}"
        if target.exists():
            return None, (
                f"{target} already exists. Add it under `repositories:` if you "
                "want Jarvis to work in it."
            )

        from .workspace import _run_git

        runner = git or _run_git
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            return None, f"Could not create the workspace {self.workspace}: {err}"

        # Before the directory exists, not after. See `git_problem`.
        problem = await git_problem(runner, self.workspace)
        if problem:
            return None, problem

        try:
            target.mkdir(parents=True)
            for filename, body in initial_files(name, description).items():
                (target / filename).write_text(body, encoding="utf-8")
        except OSError as err:
            _remove_tree(target)
            return None, f"Could not create {target}: {err}"

        # `-b main`: a repository whose default branch depends on the host's
        # git version is one whose branch names differ between two machines.
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            [
                "-c", "user.email=jarvis@local", "-c", "user.name=Jarvis",
                "commit", "-qm", "Initial commit",
            ],
        ):
            code, _out, err = await runner(args, target, 60.0)
            if code != 0:
                # Nothing half-made left behind. A directory with a README and
                # no `.git` is not a repository, it is not in the registry, and
                # it makes the name unusable on the next attempt — so the
                # failure has to undo itself.
                _remove_tree(target)
                return None, f"git {args[0]} failed in {target}: {err.strip()[:200]}"

        entry = CreatedRepo(
            name=name,
            path=str(target),
            description=str(description or "")[:300],
            environment=str(environment or "").strip(),
            created=time.time(),
        )
        self.repos[name] = entry
        await self.async_save()
        _LOGGER.info("code: created repository %s at %s", name, target)
        return entry, ""

    async def async_clone(
        self,
        forge: Any,
        project: str,
        *,
        config_dir: Path,
        name: str = "",
        environment: str = "",
        taken: set[str] | None = None,
        git: Any = None,
    ) -> tuple["CreatedRepo | None", str]:
        """Clone a PERMITTED repository into the workspace.

        The allow-list check is the caller's — `async_clone_repository` in the
        integration — so the refusal can name the forge. Everything after it is
        here: a local name that cannot escape the workspace, a URL with no
        credential in it, and a token that reaches git through the environment
        rather than the argv.
        """
        from .forges import ForgeError, clone_url, git_env, local_name, redact

        if self.workspace is None:
            return None, (
                "Creating repositories is turned off: `code: workspace:` is set to `off` in configuration.yaml. Remove that line to get the default (~/jarvis/workspaces), or name a directory of your own."
            )
        wanted = str(name or "").strip() or local_name(project)
        problem = check_name(wanted)
        if problem:
            return None, problem
        if wanted in self.repos:
            return None, f"There is already a repository called {wanted!r}."
        if wanted in (taken or set()):
            return None, (
                f"{wanted!r} is the name of a repository declared in "
                "configuration.yaml. Clone it under another name."
            )
        if len(self.repos) >= MAX_REPOS:
            return None, f"There are already {MAX_REPOS} repositories."

        try:
            target = self.resolve(wanted)
            url = clone_url(forge, project)
        except (PathRefused, ForgeError) as err:
            return None, str(err)
        if target.exists():
            return None, f"{target} already exists."

        from .workspace import _run_git

        runner = git or _run_git
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            return None, f"Could not create the workspace {self.workspace}: {err}"

        problem = await git_problem(runner, self.workspace)
        if problem:
            return None, problem

        code, _out, err = await runner(
            ["clone", "--depth", "50", url, str(target)],
            self.workspace,
            300.0,
            env=git_env(forge, config_dir),
        )
        if code != 0:
            # A clone that dies partway leaves the directory behind, and the
            # next attempt then refuses the name as "already exists".
            _remove_tree(target)
            # Redacted: git puts the URL in its errors, and an askpass failure
            # can echo more than you want into a log somebody pastes.
            return None, f"clone failed: {redact(err, forge).strip()[:300]}"

        entry = CreatedRepo(
            name=wanted,
            path=str(target),
            description=f"cloned from {forge.name}:{project}",
            environment=str(environment or "").strip(),
            created=time.time(),
            origin=f"{forge.name}:{project}",
        )
        self.repos[wanted] = entry
        await self.async_save()
        _LOGGER.info("code: cloned %s:%s into %s", forge.name, project, target)
        return entry, ""

    async def async_forget(self, name: str) -> tuple[bool, str]:
        """Drop it from the registry. Deliberately does NOT delete the files.

        Jarvis creates directories; it does not remove them. `rm -rf` driven by
        a model — or by a mis-click in a browser — is the one operation here
        with no undo, and the files are one `rm` away for a human who means it.
        """
        entry = self.repos.pop(str(name or ""), None)
        if entry is None:
            return False, f"There is no repository called {name!r}."
        await self.async_save()
        return True, (
            f"Forgot {entry.name}. The files are still at {entry.path} — "
            "Jarvis does not delete them."
        )

    def listing(self) -> list[dict[str, Any]]:
        return [entry.as_dict() for entry in self.repos.values()]
