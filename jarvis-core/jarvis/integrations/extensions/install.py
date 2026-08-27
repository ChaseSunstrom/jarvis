"""Installing one thing, and running none of it.

The order here is the whole design, and it is deliberately the opposite of
every package manager's:

1. **fetch** the bytes;
2. **hash** them, and refuse if the hash is not the one that was approved;
3. **read** what is inside — including anything that looks like a program;
4. **ask a human**, showing the declared permissions and every hook found;
5. only then **write** it to disk;
6. **validate the manifest**, and delete it again if it does not hold.

Nothing at any step runs anything from the payload. There is no install hook,
no `setup.py`, no `postinstall`, and the absence is the feature: a skill folder
is read by `skills/` and never executed, so the strongest thing a hostile
payload can do here is sit on disk being a document that nobody has to read.

`hooks` is therefore not a safety mechanism. It is a *disclosure* one: "this
skill ships a shell script" is a sentence an operator can act on, and it is
shown before they approve rather than discovered afterwards.

**What can still go wrong**, written down because it is not defended here:
a skill is instructions to a model, and a hostile skill an operator installs
and approves is a hostile instruction an operator installed and approved. The
tier system is what stands between "the document says to unlock the door" and
the door unlocking, exactly as it does for a web page. That is the boundary,
and installing something does not move it.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .catalog import (
    Catalog,
    CatalogError,
    Entry,
    check_digest,
    digest,
    find_hooks,
    resolve_ref,
)
from .manifest import KIND_MCP

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Largest payload accepted, in bytes. A skill is prose.
MAX_PAYLOAD = 2 * 1024 * 1024
MAX_FILES = 64

#: Files that never land, whatever the payload says.
#:
#: Not a security boundary — nothing here executes — but a `.git` directory or
#: a symlink in a payload is a way to write outside the folder, and the answer
#: to "why would a skill ship one" is that it would not.
FORBIDDEN_PARTS = (".git", "..", "~")


class InstallError(RuntimeError):
    """An install that will not happen, and why."""


def _safe_name(name: str) -> str:
    """A path from a stranger, or an exception.

    Refused rather than sanitised, for the reason `scaffold.py` gives: a name
    quietly turned into a different one is how somebody ends up with a file
    they did not write in a place they did not name.
    """
    text = str(name or "").strip()
    if not text:
        raise InstallError("a payload entry with no name")
    # Refused, NOT stripped. `lstrip("/")` was here and turned `/etc/SKILL.md`
    # into `etc/SKILL.md`, which is the sanitise-rather-than-refuse mistake this
    # function's own docstring warns about — quietly writing somewhere the
    # payload did not name is not better than refusing to write at all.
    if text.startswith("/") or text.startswith("\\") or (len(text) > 1 and text[1] == ":"):
        raise InstallError(f"{name!r} is an absolute path, and nothing absolute lands")
    parts = [p for p in text.split("/") if p]
    for part in parts:
        if part in FORBIDDEN_PARTS or part.startswith("."):
            raise InstallError(f"{name!r} contains {part!r}, which never lands")
    if len(parts) > 4:
        raise InstallError(f"{name!r} is nested deeper than a skill folder goes")
    return "/".join(parts)


def read_payload(files: dict[str, bytes]) -> dict[str, bytes]:
    """Check a fetched payload's shape before anything touches disk."""
    if not files:
        raise InstallError("the payload is empty")
    if len(files) > MAX_FILES:
        raise InstallError(f"{len(files)} files; {MAX_FILES} is the limit for one skill")
    total = sum(len(v) for v in files.values())
    if total > MAX_PAYLOAD:
        raise InstallError(f"{total} bytes; {MAX_PAYLOAD} is the limit")
    out: dict[str, bytes] = {}
    for name, body in files.items():
        out[_safe_name(name)] = body
    if not any(n.rsplit("/", 1)[-1] == "SKILL.md" for n in out):
        raise InstallError("no SKILL.md in the payload, so there is no skill in it")
    return out


def fetch_local(entry: Entry) -> dict[str, bytes]:
    """Read a payload from this machine. The fixture and own-folder path."""
    parsed = urlparse(entry.url)
    if parsed.scheme != "file":
        raise InstallError(f"{entry.url} is not on this machine")
    root = Path(parsed.path)
    if not root.is_dir():
        raise InstallError(f"{root} is not a directory")
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            # A symlink writes wherever it points, which is the one way a
            # payload of plain files can reach outside its own folder.
            raise InstallError(f"{path.relative_to(root)} is a symlink")
        if path.is_file():
            files[str(path.relative_to(root))] = path.read_bytes()
    return files


def plan(entry: Entry, files: dict[str, bytes], *, expected_sha: str = "") -> dict[str, Any]:
    """Everything an operator needs to decide, computed BEFORE anything lands.

    Returns the plan rather than performing it: the approval prompt is built
    from this, and `apply` takes the same plan back. Splitting them is what
    makes "nothing auto-runs on install" checkable — there is a step between
    knowing and doing, and a test can stand in it.
    """
    checked = read_payload(files)
    payload = b"".join(checked[name] for name in sorted(checked))
    sha = check_digest(payload, expected_sha)
    hooks = find_hooks(checked)
    return {
        "id": entry.id,
        "kind": entry.kind,
        "source": entry.source,
        "url": entry.url,
        "ref": entry.ref,
        "sha256": sha,
        "permissions": list(entry.permissions),
        "files": sorted(checked),
        "hooks": list(hooks),
        # The sentence the console puts next to the approve button.
        "warning": (
            f"{len(hooks)} file(s) in this payload are programs. Jarvis will not run "
            "them — a skill folder is read, never executed — but read them before "
            "you approve: " + ", ".join(hooks)
        )
        if hooks
        else "",
    }


def apply(
    jarvis: "Jarvis",
    entry: Entry,
    files: dict[str, bytes],
    approved: dict[str, Any],
) -> dict[str, Any]:
    """Write it, validate it, and take it away again if it does not hold.

    `approved` is the plan a human said yes to. The hash is checked against it
    a second time here, because the gap between approving and writing is
    exactly where a source that wanted to swap the payload would do it.
    """
    if not approved or not approved.get("sha256"):
        raise InstallError("nothing was approved, so nothing is installed")
    checked = read_payload(files)
    payload = b"".join(checked[name] for name in sorted(checked))
    sha = digest(payload)
    if sha != str(approved.get("sha256")):
        raise InstallError(
            "what was approved and what is being written are different: "
            f"{str(approved['sha256'])[:12]}… vs {sha[:12]}…"
        )
    store = jarvis.data.get("skills")
    root = getattr(store, "root", None)
    if root is None:
        raise InstallError("skills are not set up, so there is nowhere to put one")
    folder = Path(root) / entry.id
    if folder.exists():
        raise InstallError(f"there is already something at {folder}")

    folder.mkdir(parents=True)
    try:
        for name in sorted(checked):
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(checked[name])
        store.load()
        skill = store.get(entry.id)
        if skill is None:
            raise InstallError("it landed and the skill loader would not read it")
        from .registry import skill_manifest

        manifest = skill_manifest(skill)
    except Exception:
        # Out again, whole. A half-installed skill is one that is in the prompt
        # and not in the index, which is the state nobody can reason about.
        shutil.rmtree(folder, ignore_errors=True)
        store.load()
        raise
    _LOGGER.info(
        "extensions: installed %s from %s at %s (%s)",
        entry.id,
        entry.source,
        entry.ref or "unpinned",
        sha[:12],
    )
    return {
        "installed": entry.id,
        "path": str(folder),
        "sha256": sha,
        "ref": entry.ref,
        "source": entry.source,
        "permissions": list(manifest.permissions),
        "hooks": list(find_hooks(checked)),
    }


def prepare(catalog: Catalog, source_name: str, entry_id: str, refs: list[str] | None = None):
    """Find one entry in an allowed source, pinned. Fetches nothing."""
    source = catalog.source_for(source_name)
    for entry in catalog.search(kind=source.kind):
        if entry.id == entry_id and entry.source == source.name:
            entry.ref = resolve_ref(entry, refs)
            return entry
    raise CatalogError(f"{source_name} does not offer {entry_id!r}")


def refuse_mcp_stdio(spec: Any) -> None:
    """A catalog may not ask this machine to start a program."""
    if getattr(spec, "is_stdio", False) or str(getattr(spec, "transport", "")) == "stdio":
        raise InstallError(
            "a stdio MCP server is a program this machine starts. Those come from "
            "configuration.yaml, which a person edits, and never from a catalog."
        )


def install_mcp(jarvis: "Jarvis", entry: Entry, approved: dict[str, Any]) -> dict[str, Any]:
    """An http MCP server: a URL and a tier. Nothing lands on disk."""
    if entry.kind != KIND_MCP:
        raise InstallError(f"{entry.id} is a {entry.kind}, not an MCP server")
    if not approved or not approved.get("approved"):
        raise InstallError("nothing was approved, so nothing is installed")
    url = str(entry.url or "")
    if not url.startswith("https://"):
        raise InstallError(f"{url!r} is not https, and an MCP server carries a token")
    from ..mcp import get_manager

    manager = get_manager(jarvis)
    if manager is None:
        raise InstallError("the mcp integration is not set up")
    return {"queued": entry.id, "url": url, "tier": manager.default_tier}


__all__ = [
    "InstallError",
    "apply",
    "fetch_local",
    "install_mcp",
    "plan",
    "prepare",
    "read_payload",
    "refuse_mcp_stdio",
]
