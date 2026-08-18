"""Where a path is allowed to point, and nowhere else.

This is the whole security surface of the `files` integration and it is
deliberately its own module with no I/O in it, because the failure mode is
silent: a traversal bug does not raise, it just returns the wrong file.

## What the model controls

Everything after the root. The operator names a root — a local directory or a
WebDAV collection — and every tool call supplies a path *inside* it. The model
chooses that path, and the model's input is influenced by web pages, emails and
documents it has read. So the path must be treated as hostile, every time.

## The four ways out of a directory, and what closes each

    ../../etc/passwd        -> resolved and compared against the root
    /etc/passwd             -> a leading separator is stripped, never honoured
    ..%2f..%2fetc           -> percent-decoded BEFORE the check, not after
    a symlink to /etc       -> `resolve()` follows it, then the check catches it

The first three are closed by :func:`safe_relative`, which is pure string work
and is tested against every encoding of `..` this author could think of. The
fourth needs the filesystem and is closed by :func:`resolve_local`, which
compares the *resolved* path — the one symlinks have already been followed
through — against the *resolved* root.

`WebDAV` has no symlinks to worry about but every other case applies, plus one
of its own: a relative path must not be able to climb out of the collection's
URL. Same function, same answer.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

__all__ = [
    "PathRefused",
    "join_url",
    "resolve_local",
    "safe_relative",
]


class PathRefused(ValueError):
    """A path that would leave its root. Always the caller's fault, never ours."""


#: Segments that are never a real file and are always an attempt.
_FORBIDDEN = frozenset({"..", "."})


def safe_relative(path: str) -> str:
    """Normalise a caller's path to something strictly inside its root.

    Returns a clean relative POSIX path with no leading separator and no `..`.
    Raises :class:`PathRefused` rather than silently correcting, because a
    request for `../../etc/passwd` is not a typo to be helpfully fixed — it is a
    thing somebody should see in a log.

    Decoding happens FIRST. `..%2f..%2f` is `../../` and a check that ran before
    the decode would pass it straight through to a client that decodes on the
    way out.
    """
    raw = str(path or "").strip()
    # Twice: a double-encoded `..%252f` decodes to `..%2f` and then to `../`.
    # Two rounds is enough for every layer this stack has, and a fixed number
    # rather than a loop so a pathological input cannot spin here.
    for _ in range(2):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded

    if "\x00" in raw:
        raise PathRefused("a path may not contain a null byte")
    # Both separators: a Windows-style path reaching a POSIX join is still an
    # escape attempt, and `PurePosixPath` would treat the backslash as a
    # perfectly ordinary character in a filename.
    raw = raw.replace("\\", "/")

    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        # `"/"` OR `"//"`: POSIX gives a leading double slash its own
        # implementation-defined root, so `PurePosixPath("//evil/x").parts` is
        # `("//", "evil", "x")` — and a `"//"` that survived would come back out
        # as an absolute path. `Path("/root") / "//evil/x"` is `//evil/x`: the
        # absolute-looking operand REPLACES the base, and the join is the escape.
        if part.strip("/") == "":
            # A leading slash is dropped rather than refused: "/notes/a.md" is
            # what a person types for a path inside a share, and reading it as
            # the filesystem root would be absurd. What it must NOT do is
            # escape, and dropping it is exactly that.
            continue
        if part in _FORBIDDEN:
            if part == "..":
                raise PathRefused(f"{path!r} tries to leave its root")
            continue
        parts.append(part)
    return "/".join(parts)


def resolve_local(root: Path, path: str) -> Path:
    """A real path under `root`, symlinks already followed.

    `safe_relative` closes the string attacks; this closes the one it cannot
    see. A symlink inside the root pointing at `/etc` contains no `..` and
    survives every textual check — so the comparison is made against the
    RESOLVED path, after the link has been followed, using the resolved root.

    `strict=False` because a path that does not exist yet is a legitimate
    target for a write, and refusing it here would make creating a file
    impossible. Non-existence is the caller's problem; leaving the root is
    this function's.
    """
    relative = safe_relative(path)
    base = root.expanduser().resolve()
    target = (base / relative).resolve() if relative else base
    if target != base and base not in target.parents:
        raise PathRefused(f"{path!r} resolves outside its root")
    return target


def join_url(base: str, path: str) -> str:
    """`base` + a checked relative path, percent-encoded for a URL.

    Built by concatenation rather than `urljoin`, deliberately. `urljoin` is
    defined to let the right-hand side replace the left — `urljoin("https://a/b/",
    "//evil.test/x")` is `https://evil.test/x`, and `"/x"` gives `https://a/x`,
    outside the collection. Neither is what "a path inside this share" means,
    and both are reachable from a string the model chose.
    """
    relative = safe_relative(path)
    stem = base.rstrip("/")
    if not relative:
        return stem + "/"
    return f"{stem}/{quote(relative, safe='/')}"
