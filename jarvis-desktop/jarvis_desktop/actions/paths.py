"""The path-escape guard: file actions may only touch configured roots.

The thing choosing the path is an LLM that reads attacker-controlled text, so
"does the string contain ``..``" is not a security check. Three independent
layers, all required, in this order:

1. **Syntactic rejection** of forms that are never legitimate here: null bytes,
   ``~`` expansion, percent-encoded separators, URL schemes, UNC paths, and (on
   POSIX) backslashes.
2. **Arithmetic traversal.** ``..`` pops a segment; popping past the root is a
   rejection. Done on the *cleaned* path before anything touches the disk, so a
   path that escapes is never even opened.
3. **Realpath containment.** The candidate is fully resolved — every symlink in
   every component, including a dangling final one — and the result must still
   sit under a root. This is the only layer that catches a symlink planted
   inside the workspace pointing at ``/etc/shadow``, and it is why layers 1 and
   2 are not sufficient on their own.

Mirrors ``android-app/.../automation/actions/PathScope.kt``, widened from the
phone's single app-private directory to a configurable list of roots.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

__all__ = ["PathScope", "PathResult", "ScopeError"]

MAX_PATH_CHARS = 4096
MAX_SEGMENT_CHARS = 255

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_IS_WINDOWS = os.name == "nt"


class ScopeError(ValueError):
    """Raised by :meth:`PathScope.require` when a path is refused."""


@dataclass(frozen=True)
class PathResult:
    """Either an allowed absolute path or a refusal with a reason."""

    allowed: bool
    path: Path | None = None
    #: Which configured root contained it.
    root: Path | None = None
    #: Path relative to ``root``, using ``/``. "" means the root itself.
    relative: str = ""
    reason: str | None = None

    def __bool__(self) -> bool:  # `if scope.resolve(...):`
        return self.allowed

    @staticmethod
    def reject(reason: str) -> "PathResult":
        return PathResult(False, reason=reason)


class PathScope:
    """Confines every file action to a fixed list of directories."""

    def __init__(self, roots: Sequence[str | os.PathLike[str]]) -> None:
        if not roots:
            raise ValueError("PathScope needs at least one root")
        resolved: list[Path] = []
        for raw in roots:
            root = Path(os.path.expanduser(os.path.expandvars(str(raw))))
            # Roots are resolved once, at construction, so a symlinked root
            # (``~/jarvis`` -> ``/data/jarvis``) is compared against its real
            # location rather than its alias.
            resolved.append(Path(os.path.realpath(root)))
        # Deduplicate while keeping order: roots[0] is the default workspace.
        seen: set[str] = set()
        self.roots: tuple[Path, ...] = tuple(
            r for r in resolved if not (str(r) in seen or seen.add(str(r)))
        )

    @property
    def default_root(self) -> Path:
        """Relative paths resolve against this one."""
        return self.roots[0]

    def ensure_roots(self) -> None:
        for root in self.roots:
            root.mkdir(parents=True, exist_ok=True)

    # --- layer 1 + 2: pure string work -------------------------------------

    @staticmethod
    def clean(raw: object, allow_root: bool = False) -> PathResult:
        """Syntactic + arithmetic normalisation of a *relative* path.

        Returns the cleaned root-relative path in ``relative``. Absolute inputs
        are rejected here; :meth:`resolve` handles them separately by matching
        them against a root first.

        ``allow_root``: when true a path that resolves to the root itself is
        allowed (``list_dir``); when false it is rejected (read/write/delete,
        which need an actual file).
        """
        if raw is None:
            return PathResult.reject("path is required")
        if not isinstance(raw, str):
            return PathResult.reject("path must be a string")
        path = raw.strip()
        if not path:
            return (
                PathResult(True, relative="")
                if allow_root
                else PathResult.reject("path is required")
            )
        if len(path) > MAX_PATH_CHARS:
            return PathResult.reject("path too long")
        if "\x00" in path:
            return PathResult.reject("path contains a null byte")
        if path.startswith("~"):
            return PathResult.reject("home-relative paths are not allowed")
        if "://" in path:
            return PathResult.reject("only plain filesystem paths are allowed")

        lower = path.lower()
        # Percent-encoded separators and dots are never legitimate here and are
        # the classic way to smuggle traversal past a naive check.
        if "%2e" in lower or "%2f" in lower or "%5c" in lower:
            return PathResult.reject("percent-encoded path segments are not allowed")

        if path.startswith("//") or path.startswith("\\\\"):
            return PathResult.reject("UNC paths are not allowed")
        if not _IS_WINDOWS and "\\" in path:
            return PathResult.reject("backslashes are not allowed in paths")
        if path.startswith("/"):
            return PathResult.reject("absolute paths are not allowed")
        if _WINDOWS_DRIVE.match(path):
            return PathResult.reject("absolute paths are not allowed")

        stack: list[str] = []
        for segment in path.replace("\\", "/").split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if not stack:
                    return PathResult.reject("path escapes the sandbox")
                stack.pop()
                continue
            if len(segment) > MAX_SEGMENT_CHARS:
                return PathResult.reject("path segment too long")
            stack.append(segment)

        if not stack:
            return (
                PathResult(True, relative="")
                if allow_root
                else PathResult.reject("path resolves to the sandbox root")
            )
        return PathResult(True, relative="/".join(stack))

    # --- layer 3: realpath containment --------------------------------------

    def contains(self, candidate: str | os.PathLike[str]) -> Path | None:
        """The root that really contains ``candidate``, or None.

        ``candidate`` is fully realpath'd first, so symlinks cannot be used to
        point out of a root. Python's ``realpath`` resolves as much of the path
        as exists and leaves the rest literal, which is exactly what a
        not-yet-created file needs.
        """
        real = Path(os.path.realpath(str(candidate)))
        for root in self.roots:
            try:
                if real == root or real.is_relative_to(root):
                    return root
            except ValueError:  # different drives on Windows
                continue
        return None

    def resolve(
        self,
        raw: object,
        allow_root: bool = False,
        must_exist: bool = False,
        must_be_file: bool = False,
        must_be_dir: bool = False,
    ) -> PathResult:
        """Turn a caller-supplied path into an absolute path inside a root.

        Accepts a relative path (resolved against :attr:`default_root`) or an
        absolute path that already lives inside one of the roots. Everything
        else is refused with a reason the model can act on.
        """
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if not allow_root:
                return PathResult.reject("path is required")
            return self._finish(self.default_root, self.default_root, "", must_exist, must_be_file, must_be_dir)
        if not isinstance(raw, str):
            return PathResult.reject("path must be a string")

        text = raw.strip()
        if "\x00" in text:
            return PathResult.reject("path contains a null byte")
        if text.startswith("~"):
            return PathResult.reject(
                "home-relative paths are not allowed; use a path inside the workspace"
            )
        if "://" in text:
            return PathResult.reject("only plain filesystem paths are allowed")

        is_absolute = text.startswith("/") or bool(_WINDOWS_DRIVE.match(text)) or (
            _IS_WINDOWS and text.startswith("\\")
        )

        if is_absolute:
            # An absolute path is allowed only when it is already inside a root.
            # ``..`` inside it is harmless because realpath flattens it before
            # containment is checked.
            if text.startswith("//") or text.startswith("\\\\"):
                return PathResult.reject("UNC paths are not allowed")
            candidate = Path(os.path.realpath(text))
            root = self.contains(candidate)
            if root is None:
                return PathResult.reject(
                    f"path is outside the allowed roots ({self.describe_roots()})"
                )
            relative = _relative_str(candidate, root)
            if not relative and not allow_root:
                return PathResult.reject("path resolves to the root of the workspace")
            return self._finish(candidate, root, relative, must_exist, must_be_file, must_be_dir)

        cleaned = self.clean(text, allow_root=allow_root)
        if not cleaned.allowed:
            return cleaned

        root = self.default_root
        candidate = Path(os.path.realpath(root / cleaned.relative if cleaned.relative else root))
        # The arithmetic pass proved the *cleaned* path stays inside; this
        # proves the *real* path does, which is the symlink case.
        containing = self.contains(candidate)
        if containing is None:
            return PathResult.reject(
                "path escapes the workspace once symlinks are resolved"
            )
        return self._finish(
            candidate,
            containing,
            _relative_str(candidate, containing),
            must_exist,
            must_be_file,
            must_be_dir,
        )

    def require(self, raw: object, **kwargs: object) -> Path:
        """:meth:`resolve` or raise :class:`ScopeError`."""
        result = self.resolve(raw, **kwargs)  # type: ignore[arg-type]
        if not result.allowed or result.path is None:
            raise ScopeError(result.reason or "path rejected")
        return result.path

    def describe_roots(self) -> str:
        return ", ".join(str(r) for r in self.roots)

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _finish(
        candidate: Path,
        root: Path,
        relative: str,
        must_exist: bool,
        must_be_file: bool,
        must_be_dir: bool,
    ) -> PathResult:
        if (must_exist or must_be_file or must_be_dir) and not candidate.exists():
            return PathResult.reject(f"no such path: {relative or '.'}")
        if must_be_file and not candidate.is_file():
            return PathResult.reject(f"not a regular file: {relative or '.'}")
        if must_be_dir and not candidate.is_dir():
            return PathResult.reject(f"not a directory: {relative or '.'}")
        return PathResult(True, path=candidate, root=root, relative=relative)


def _relative_str(candidate: Path, root: Path) -> str:
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return ""
    text = rel.as_posix()
    return "" if text == "." else text


def safe_join(root: str | os.PathLike[str], *parts: str) -> Path:
    """Join under ``root`` refusing any part that would escape it.

    A convenience for code that already has a trusted root and untrusted leaf
    names (a screenshot filename, a downloaded attachment).
    """
    base = Path(os.path.realpath(str(root)))
    rel = PurePosixPath("/".join(p.strip("/") for p in parts if p))
    for segment in rel.parts:
        if segment in ("..", "/") or "\x00" in segment:
            raise ScopeError(f"unsafe path segment: {segment!r}")
    candidate = Path(os.path.realpath(base.joinpath(*rel.parts)))
    if candidate != base and not candidate.is_relative_to(base):
        raise ScopeError("path escapes the root")
    return candidate


def iter_roots(roots: Iterable[str | os.PathLike[str]]) -> tuple[Path, ...]:
    return tuple(Path(os.path.realpath(os.path.expanduser(str(r)))) for r in roots)
