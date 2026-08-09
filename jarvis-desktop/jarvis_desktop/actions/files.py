"""File actions, confined to the configured roots by :mod:`.paths`.

Every path parameter goes through :meth:`PathScope.resolve`, which is the only
way any of these touch the disk. There is no code path here that opens a file
from a raw string.

File *contents* are untrusted data. A README the model was asked to read may
have been written by anyone, so ``read_file`` flags its payload and nothing
downstream may treat it as an instruction.
"""

from __future__ import annotations

import os
import time
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult

__all__ = ["ReadFile", "WriteFile", "ListDir", "DeleteFile"]

MAX_READ_BYTES = 1 * 1024 * 1024
MAX_WRITE_BYTES = 8 * 1024 * 1024
MAX_LIST_ENTRIES = 1000


class ReadFile(Action):
    id = "read_file"
    tier = ActionTier.AUTO
    description = "Read a text file from the Jarvis workspace."
    params_schema = {
        "path": "string: path inside an allowed root",
        "max_bytes": "int (optional): stop after this many bytes (default 1 MiB)",
        "encoding": "string (optional): text encoding, default utf-8",
    }
    capability = "files"
    timeout_s = 20.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        resolved = ctx.scope.resolve(params.get("path"), must_be_file=True)
        if not resolved.allowed or resolved.path is None:
            return ActionResult.failed(str(resolved.reason))
        limit = max(1, min(self.int_param(params, "max_bytes", MAX_READ_BYTES), MAX_READ_BYTES))
        encoding = self.str_param(params, "encoding") or "utf-8"
        try:
            size = resolved.path.stat().st_size
            with resolved.path.open("rb") as fh:
                raw = fh.read(limit)
        except OSError as exc:
            return ActionResult.failed(f"could not read {resolved.relative}: {exc}")
        try:
            text = raw.decode(encoding, errors="replace")
        except LookupError:
            return ActionResult.failed(f"unknown encoding: {encoding}")
        # File contents did not come from this machine's owner in any meaningful
        # sense — the model asked for them and something else wrote them.
        return ActionResult.untrusted(
            {
                "path": resolved.relative,
                "root": str(resolved.root),
                "size_bytes": size,
                "truncated": size > len(raw),
                "content": text,
            }
        )


class WriteFile(Action):
    id = "write_file"
    tier = ActionTier.NOTIFY
    description = "Write or append to a text file in the Jarvis workspace."
    params_schema = {
        "path": "string: path inside an allowed root",
        "content": "string: the text to write",
        "append": "bool (optional): append instead of replacing (default false)",
        "encoding": "string (optional): text encoding, default utf-8",
    }
    capability = "files"
    timeout_s = 30.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        resolved = ctx.scope.resolve(params.get("path"))
        if not resolved.allowed or resolved.path is None:
            return ActionResult.failed(str(resolved.reason))
        content = params.get("content")
        if not isinstance(content, str):
            return ActionResult.failed("content is required and must be a string")
        data = content.encode(self.str_param(params, "encoding") or "utf-8", errors="replace")
        if len(data) > MAX_WRITE_BYTES:
            return ActionResult.failed(
                f"content is {len(data)} bytes; the limit is {MAX_WRITE_BYTES}"
            )
        append = self.bool_param(params, "append")
        target = resolved.path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Re-check after mkdir: a symlink could have appeared between the
            # resolve above and here, and creating the parent may have followed
            # one. Cheap, and closes the window.
            if ctx.scope.contains(target) is None:
                return ActionResult.failed("path escapes the workspace once symlinks are resolved")
            with target.open("ab" if append else "wb") as fh:
                fh.write(data)
        except OSError as exc:
            return ActionResult.failed(f"could not write {resolved.relative}: {exc}")
        return ActionResult.success(
            path=resolved.relative,
            root=str(resolved.root),
            bytes_written=len(data),
            appended=append,
        )


class ListDir(Action):
    id = "list_dir"
    tier = ActionTier.AUTO
    description = "List the files in a directory of the Jarvis workspace."
    params_schema = {
        "path": "string (optional): directory inside an allowed root; default the workspace root",
        "limit": "int (optional): stop after this many entries (default 1000)",
    }
    capability = "files"
    timeout_s = 20.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        resolved = ctx.scope.resolve(params.get("path"), allow_root=True, must_be_dir=True)
        if not resolved.allowed or resolved.path is None:
            return ActionResult.failed(str(resolved.reason))
        limit = max(1, min(self.int_param(params, "limit", MAX_LIST_ENTRIES), MAX_LIST_ENTRIES))
        entries: list[dict[str, Any]] = []
        try:
            with os.scandir(resolved.path) as it:
                for entry in it:
                    if len(entries) >= limit:
                        break
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        item = {
                            "name": entry.name,
                            "type": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                            "size_bytes": stat.st_size,
                            "modified": time.strftime(
                                "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)
                            ),
                        }
                        if entry.is_symlink():
                            # Flagged, not followed: a symlink out of the
                            # workspace is visible but unusable, because
                            # read/write resolve it and then refuse.
                            item["type"] = "symlink"
                            item["escapes_workspace"] = (
                                ctx.scope.contains(entry.path) is None
                            )
                        entries.append(item)
                    except OSError:
                        continue
        except OSError as exc:
            return ActionResult.failed(f"could not list {resolved.relative or '.'}: {exc}")
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        # File names are chosen by whoever wrote them, not by the user.
        return ActionResult.untrusted(
            {
                "path": resolved.relative,
                "root": str(resolved.root),
                "entries": entries,
                "count": len(entries),
                "truncated": len(entries) >= limit,
            }
        )


class DeleteFile(Action):
    id = "delete_file"
    tier = ActionTier.CONFIRM
    description = "Delete a file from the Jarvis workspace. This cannot be undone."
    params_schema = {
        "path": "string: path inside an allowed root",
        "recursive": "bool (optional): delete a directory and everything under it",
    }
    capability = "files"
    timeout_s = 30.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        resolved = ctx.scope.resolve(params.get("path"), must_exist=True)
        if not resolved.allowed or resolved.path is None:
            return ActionResult.failed(str(resolved.reason))
        target = resolved.path
        # Never delete a root itself, however it was addressed.
        if any(target == root for root in ctx.scope.roots):
            return ActionResult.denied("refusing to delete the workspace root itself")

        recursive = self.bool_param(params, "recursive")
        try:
            if target.is_dir() and not target.is_symlink():
                if not recursive:
                    return ActionResult.failed(
                        f"{resolved.relative} is a directory; pass recursive=true to remove it"
                    )
                removed = _rmtree_confined(ctx, target)
                return ActionResult.success(
                    path=resolved.relative, removed_entries=removed, recursive=True
                )
            target.unlink()
        except OSError as exc:
            return ActionResult.failed(f"could not delete {resolved.relative}: {exc}")
        return ActionResult.success(path=resolved.relative, removed_entries=1)


def _rmtree_confined(ctx: ActionContext, root: Any) -> int:
    """Delete a tree without ever following a symlink out of the workspace.

    ``shutil.rmtree`` would be fine on its own — it does not follow directory
    symlinks — but doing the walk here means every entry is re-checked against
    the scope, so a mount point or a bind that appeared mid-walk cannot widen
    the blast radius.
    """
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        if ctx.scope.contains(dirpath) is None:
            continue
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                continue
        for name in dirnames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.islink(path):
                    os.unlink(path)
                else:
                    os.rmdir(path)
                removed += 1
            except OSError:
                continue
    try:
        os.rmdir(root)
        removed += 1
    except OSError:
        pass
    return removed
