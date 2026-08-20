"""Fetching a skill from GitHub, and why the result arrives switched off.

## The thing to be clear-eyed about

A skill is not data. It is **instructions the model follows**, loaded into the
same context as the persona and the safety rules. Installing one from a
repository is letting a stranger write part of Jarvis's system prompt.

Everything else in this codebase that reads somebody else's text — a web page,
an MCP tool result, a document — is FENCED and marks the turn untrusted,
because it is data being quoted. A skill cannot be fenced: following it is the
entire point. There is no version of "install this skill but do not do what it
says".

So the answer is not a fence, it is a **person**:

* installing is Tier 3, so a human approves the fetch;
* the skill arrives **disabled**, and nothing reads it until somebody enables
  it — the approval card cannot meaningfully show four pages of markdown, so
  the console shows the body and the operator turns it on;
* the source must be on an allow-list. `anthropics/skills` by default, and an
  operator adds their own.

That is three deliberate acts before a downloaded sentence can influence a
turn, which is the right number for this.

## Two ways to fetch, because one host is often blocked

`git` may not be installed — a real failure this deployment has already hit —
and cloning a whole monorepo to take one folder is absurd. So:

1. **The archive.** `codeload.github.com/<owner>/<repo>/tar.gz/refs/heads/<branch>`
   is one request, needs no credential for a public repository, and gets the
   whole skill folder including its scripts.
2. **Raw files**, when that host is unreachable. Plenty of networks — this
   project's own container among them — allow `raw.githubusercontent.com` and
   block `codeload`. Raw cannot list a directory, so this fetches `SKILL.md`
   and then the files the body itself mentions, which is how a skill refers to
   its own scripts (``scripts/with_server.py``).

The second is a genuinely partial fetch, and it says so: the result names the
strategy and the files, so "the script it wanted is not there" is something an
operator reads at install time rather than discovers at run time.

Extraction is the dangerous part of any archive, so it is done by hand rather
than with `extractall`: every member is checked to be a regular file, inside
the wanted folder, and inside the destination after resolution. `tarfile` has
a filter for this in new Pythons; the checks here do not depend on which
Python is running.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .model import SKILL_FILE, SkillError, check_skill_name, skill_from_text

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SOURCES",
    "MAX_ARCHIVE_BYTES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_REFERENCED_FILES",
    "Installed",
    "SkillSource",
    "install_from_github",
    "parse_reference",
    "permits",
    "referenced_files",
]

#: Anthropic's own collection. An operator adds their own with
#: `skills: sources:` in configuration.yaml.
DEFAULT_SOURCES = ("anthropics/skills",)

#: A monorepo of skills is a few MB. A hundred is somebody's mistake or
#: somebody's attack, and either way it is not going into memory.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_FILES = 400
MAX_FILE_BYTES = 4 * 1024 * 1024

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
CODELOAD = "https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
RAW = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

#: How many of the files a body mentions to go and get. A skill naming forty
#: files is one whose folder should have arrived as an archive.
MAX_REFERENCED_FILES = 24

#: A relative path inside a skill folder, as a body writes one: in backticks,
#: in a fenced command, or as a markdown link. Anchored to a file extension so
#: prose like `scripts/` or a bare word is not mistaken for one.
_REFERENCE_RE = re.compile(
    r"(?<![\w/.])((?:[A-Za-z0-9_-]+/){0,4}[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,6})(?![\w/])"
)
#: Extensions worth fetching. A skill's own scripts and references, not the
#: README of some project it happens to mention.
_REFERENCE_SUFFIXES = (
    ".py", ".sh", ".js", ".ts", ".mjs", ".json", ".yaml", ".yml",
    ".md", ".txt", ".csv", ".toml", ".sql", ".html", ".css",
)


@dataclass
class Installed:
    """What an install actually managed to fetch."""

    name: str
    files: list[str]
    #: `archive` (the whole folder) or `raw` (SKILL.md plus what it mentions).
    strategy: str
    #: "" when nothing is missing, else a sentence naming what was not fetched.
    caveat: str = ""


def referenced_files(body: str) -> list[str]:
    """Relative paths a skill body mentions, so `raw` can go and get them.

    Deliberately conservative. A false positive costs one 404 that is ignored;
    a path that escapes the folder is refused outright rather than fetched and
    checked later.
    """
    seen: list[str] = []
    for match in _REFERENCE_RE.finditer(body or ""):
        path = match.group(1)
        if not path.lower().endswith(_REFERENCE_SUFFIXES):
            continue
        if path.startswith(("http", "/", ".")) or ".." in path:
            continue
        if path == SKILL_FILE or path in seen:
            continue
        seen.append(path)
        if len(seen) >= MAX_REFERENCED_FILES:
            break
    return seen


@dataclass
class SkillSource:
    """A repository skills may be installed from."""

    owner: str
    repo: str
    branch: str = "main"

    @property
    def project(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class SkillReference:
    """`owner/repo`, `owner/repo/path/to/skill`, optionally `@branch`."""

    owner: str
    repo: str
    path: str = ""
    branch: str = ""

    @property
    def project(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def wanted_name(self) -> str:
        return (self.path.rsplit("/", 1)[-1] if self.path else self.repo).lower()


def parse_reference(text: Any) -> SkillReference:
    """Read `anthropics/skills/skills/pdf@main`. Raises `SkillError`."""
    said = str(text or "").strip()
    if not said:
        raise SkillError("which skill? Give it as owner/repo/path-to-the-skill.")
    branch = ""
    if "@" in said:
        said, _, branch = said.partition("@")
        branch = branch.strip()
    said = said.strip().strip("/")
    if said.lower().startswith(("http://", "https://")):
        raise SkillError(
            "Give it as owner/repo/path, not a URL — the host is not yours to "
            "choose, it is on the allow-list in configuration.yaml."
        )
    parts = [p for p in said.split("/") if p]
    if len(parts) < 2:
        raise SkillError(
            f"{said!r} is not owner/repo. A skill inside a repository is "
            "owner/repo/path/to/the/skill."
        )
    for segment in parts + ([branch] if branch else []):
        if not _SEGMENT_RE.match(segment):
            raise SkillError(f"{segment!r} is not a usable path segment.")
    return SkillReference(
        owner=parts[0], repo=parts[1], path="/".join(parts[2:]), branch=branch
    )


def permits(sources: list[SkillSource], reference: SkillReference) -> bool:
    """Is this repository on the allow-list? Case-insensitive, like GitHub."""
    wanted = reference.project.lower()
    return any(source.project.lower() == wanted for source in sources)


async def install_from_github(
    reference: SkillReference,
    destination: Path,
    *,
    branch: str = "main",
    transport: Any = None,
    timeout: float = 60.0,
) -> Installed:
    """Fetch one skill folder into `destination`.

    Tries the archive; falls back to raw files when that host is unreachable.
    Raises `SkillError` with a sentence for every failure, because every one
    of them is something an operator can act on: wrong path, wrong branch, no
    SKILL.md, too big.
    """
    use_branch = (reference.branch or branch or "main").strip()

    async with httpx.AsyncClient(
        timeout=timeout, transport=transport, follow_redirects=True
    ) as client:
        archive_problem = ""
        try:
            blob = await _fetch_archive(client, reference, use_branch)
        except SkillError as err:
            # Only a REACHABILITY failure falls back. "There is no such
            # branch" or "that is too big" is the answer, not a reason to try
            # a second way and report a worse version of the same thing.
            if not getattr(err, "retryable", False):
                raise
            archive_problem = str(err)
            blob = b""

        if blob:
            name, files = _extract(blob, reference, destination)
            return Installed(name=name, files=files, strategy="archive")

        _LOGGER.info(
            "skills: the archive host was unreachable (%s); fetching %s over raw",
            archive_problem,
            reference.project,
        )
        return await _fetch_raw(
            client, reference, destination, use_branch, archive_problem
        )


async def _fetch_archive(
    client: httpx.AsyncClient, reference: SkillReference, branch: str
) -> bytes:
    url = CODELOAD.format(owner=reference.owner, repo=reference.repo, branch=branch)
    try:
        response = await client.get(url)
    except httpx.HTTPError as err:
        raise _retryable(f"could not reach the archive host: {err}") from None

    if response.status_code == 404:
        raise SkillError(
            f"GitHub has no {reference.project} on branch {branch!r}. "
            "Check the branch — many repositories use `master`."
        )
    if response.status_code in (401, 403, 407):
        # A proxy that allows raw.githubusercontent.com and blocks codeload is
        # an ordinary corporate — and container — configuration.
        raise _retryable(
            f"the archive host answered {response.status_code}"
        )
    if response.status_code >= 400:
        raise _retryable(f"the archive host answered {response.status_code}")

    blob = response.content
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise SkillError(
            f"{reference.project} is {len(blob) // 1_000_000} MB, over the "
            f"{MAX_ARCHIVE_BYTES // 1_000_000} MB limit."
        )
    return blob


def _retryable(message: str) -> SkillError:
    err = SkillError(message)
    err.retryable = True  # type: ignore[attr-defined]
    return err


async def _fetch_raw(
    client: httpx.AsyncClient,
    reference: SkillReference,
    destination: Path,
    branch: str,
    archive_problem: str,
) -> Installed:
    """SKILL.md, plus the files its own body names. Cannot list a directory."""

    def raw_url(relative: str) -> str:
        prefix = f"{reference.path}/" if reference.path else ""
        return RAW.format(
            owner=reference.owner,
            repo=reference.repo,
            branch=branch,
            path=f"{prefix}{relative}",
        )

    try:
        response = await client.get(raw_url(SKILL_FILE))
    except httpx.HTTPError as err:
        raise SkillError(
            f"could not reach GitHub: {err} (the archive host also failed: "
            f"{archive_problem})"
        ) from None
    if response.status_code == 404:
        raise SkillError(
            f"there is no {SKILL_FILE} at {reference.path or '/'} in "
            f"{reference.project} on branch {branch!r}. Point at the FOLDER "
            "that contains it — for example anthropics/skills/skills/pdf."
        )
    if response.status_code >= 400:
        raise SkillError(
            f"GitHub answered {response.status_code} for {SKILL_FILE} in "
            f"{reference.project}."
        )

    text = response.text
    if len(text.encode("utf-8", "ignore")) > MAX_FILE_BYTES:
        raise SkillError(f"that {SKILL_FILE} is over the per-file limit.")
    skill = skill_from_text(text, source="installed", name_hint=reference.wanted_name)
    problem = check_skill_name(skill.name)
    if problem:
        raise SkillError(f"{reference.project}: {problem}")

    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / SKILL_FILE).write_text(text, encoding="utf-8")
    written = [SKILL_FILE]

    wanted = referenced_files(skill.body)
    missed: list[str] = []
    for relative in wanted:
        target = (root / relative).resolve()
        if root not in target.parents:
            # The body asked for something outside its own folder. Not fetched,
            # and not silently: a skill that does that is worth looking at.
            missed.append(relative)
            continue
        try:
            part = await client.get(raw_url(relative))
        except httpx.HTTPError:
            missed.append(relative)
            continue
        if part.status_code != 200 or len(part.content) > MAX_FILE_BYTES:
            missed.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(part.content)
        written.append(relative)

    caveat = (
        "The archive host was unreachable, so this was fetched file by file: "
        f"{SKILL_FILE} and {len(written) - 1} file(s) its instructions name. "
        "Anything in the folder the body does not mention was NOT fetched."
    )
    if missed:
        caveat += f" These were named but could not be fetched: {', '.join(missed)}."
    return Installed(
        name=skill.name, files=sorted(written), strategy="raw", caveat=caveat
    )


def _extract(
    blob: bytes, reference: SkillReference, destination: Path
) -> tuple[str, list[str]]:
    """Take one folder out of the tarball. Never `extractall`."""
    try:
        archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except tarfile.TarError as err:
        raise SkillError(f"that download is not a readable archive: {err}") from None

    with archive:
        members = archive.getmembers()
        if not members:
            raise SkillError("that archive is empty.")
        # GitHub wraps everything in `<repo>-<ref>/`.
        prefix = members[0].name.split("/", 1)[0]
        wanted = f"{prefix}/{reference.path}".rstrip("/") if reference.path else prefix

        skill_members = [
            m
            for m in members
            if m.isfile() and (m.name == f"{wanted}/{SKILL_FILE}" or m.name.startswith(f"{wanted}/"))
        ]
        if not any(m.name == f"{wanted}/{SKILL_FILE}" for m in skill_members):
            raise SkillError(
                f"there is no {SKILL_FILE} at {reference.path or '/'} in "
                f"{reference.project}. Point at the FOLDER that contains it — "
                "for example anthropics/skills/skills/pdf."
            )
        if len(skill_members) > MAX_FILES:
            raise SkillError(
                f"that skill has {len(skill_members)} files; the limit is {MAX_FILES}."
            )

        # Read and check the SKILL.md before writing anything at all: a skill
        # that will not parse should leave no directory behind.
        head = archive.extractfile(f"{wanted}/{SKILL_FILE}")
        text = head.read().decode("utf-8", "replace") if head else ""
        skill = skill_from_text(
            text, source="installed", name_hint=reference.wanted_name
        )
        problem = check_skill_name(skill.name)
        if problem:
            raise SkillError(f"{reference.project}: {problem}")

        root = destination.resolve()
        written: list[str] = []
        for member in skill_members:
            relative = member.name[len(wanted) + 1 :]
            if not relative:
                continue
            if member.size > MAX_FILE_BYTES:
                raise SkillError(
                    f"{relative} is {member.size} bytes, over the per-file limit."
                )
            target = (root / relative).resolve()
            # The check that matters. A member called `../../../etc/cron.d/x`
            # is the oldest archive attack there is, and `extractall` on an
            # older Python performs it faithfully.
            if root not in target.parents and target != root:
                raise SkillError(f"{member.name} would write outside the skill folder.")
            handle = archive.extractfile(member)
            if handle is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
            written.append(relative)

    return skill.name, sorted(written)
