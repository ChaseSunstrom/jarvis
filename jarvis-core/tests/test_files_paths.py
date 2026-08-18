"""Where a path may point. The whole security surface of the `files` integration.

Its own file because the failure mode is silent: a traversal bug does not raise,
it returns the wrong file — and the wrong file here is `/etc/shadow` read out
loud by a voice assistant.

The path is chosen by the MODEL, whose input is shaped by pages, emails and
documents it has read. So every one of these is written as though somebody is
trying, because somebody is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.files.paths import (  # noqa: E402
    PathRefused,
    join_url,
    resolve_local,
    safe_relative,
)


# --- the string attacks ---------------------------------------------------------

@pytest.mark.parametrize(
    "attempt",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "notes/../../etc/passwd",
        "a/b/../../../etc/passwd",
        "..",
        "../",
        "./../x",
    ],
)
def test_climbing_out_is_refused(attempt: str) -> None:
    with pytest.raises(PathRefused):
        safe_relative(attempt)


@pytest.mark.parametrize(
    "attempt",
    [
        "..%2fetc%2fpasswd",
        "%2e%2e/etc",
        "%2e%2e%2fetc",
        "..%252fetc",          # double-encoded
        "%2E%2E/etc",          # upper-case hex
    ],
)
def test_an_encoded_climb_is_decoded_before_it_is_checked(attempt: str) -> None:
    """Decoding after the check is the classic way to get this wrong.

    The string `..%2f..%2f` contains no `../` at all, so a check that ran first
    would pass it to a client that decodes it on the way out.
    """
    with pytest.raises(PathRefused):
        safe_relative(attempt)


def test_a_windows_separator_is_not_an_ordinary_character() -> None:
    # `PurePosixPath` treats a backslash as a perfectly normal filename
    # character, so `..\..\etc` survives a naive POSIX check untouched.
    with pytest.raises(PathRefused):
        safe_relative("..\\..\\etc\\passwd")


def test_a_null_byte_is_refused() -> None:
    # The old C truncation trick: "safe.txt\x00../../etc/passwd".
    with pytest.raises(PathRefused):
        safe_relative("notes.txt\x00.png")


def test_an_absolute_path_is_read_as_relative_rather_than_honoured() -> None:
    """"/notes/a.md" is what a person types for a path inside a share.

    Refusing it would be pedantic; honouring it would be a filesystem root read.
    Dropping the leading separator is neither.
    """
    assert safe_relative("/notes/a.md") == "notes/a.md"
    assert safe_relative("///a") == "a"


def test_the_ordinary_cases_come_through_unchanged() -> None:
    assert safe_relative("notes/2024/january.md") == "notes/2024/january.md"
    assert safe_relative("a.md") == "a.md"
    assert safe_relative("") == ""
    assert safe_relative("./a.md") == "a.md"


def test_a_percent_in_a_real_filename_survives() -> None:
    # Decoding is not free: a file genuinely called "100%.md" must still work.
    assert safe_relative("100%.md") == "100%.md"


# --- the filesystem attack the strings cannot see ---------------------------------

def test_a_symlink_out_of_the_root_is_caught(tmp_path: Path) -> None:
    """The one traversal no amount of string work can see.

    A symlink inside the root pointing at `/etc` contains no `..`, no encoding
    and no separator trick. It survives every textual check, which is why the
    comparison is made against the RESOLVED path.
    """
    root = tmp_path / "share"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "passwd").write_text("root:x:0:0")
    os.symlink(outside, root / "escape")

    with pytest.raises(PathRefused):
        resolve_local(root, "escape/passwd")


def test_a_symlink_inside_the_root_is_fine(tmp_path: Path) -> None:
    # The check is "does it leave", not "is it a link".
    root = tmp_path / "share"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.md").write_text("hello")
    os.symlink(root / "real", root / "link")
    assert resolve_local(root, "link/a.md").read_text() == "hello"


def test_a_path_that_does_not_exist_yet_is_allowed(tmp_path: Path) -> None:
    # Otherwise creating a file would be impossible. Non-existence is the
    # caller's problem; leaving the root is this function's.
    root = tmp_path / "share"
    root.mkdir()
    assert resolve_local(root, "new/deep/file.md").name == "file.md"


def test_the_root_itself_resolves_to_the_root(tmp_path: Path) -> None:
    root = tmp_path / "share"
    root.mkdir()
    assert resolve_local(root, "") == root.resolve()
    assert resolve_local(root, "/") == root.resolve()


def test_a_root_that_is_itself_a_symlink_still_works(tmp_path: Path) -> None:
    # Common on a NAS: /srv/docs is a link to /volume1/docs. Comparing a
    # resolved target against an UNRESOLVED root would refuse every path.
    real = tmp_path / "volume" / "docs"
    real.mkdir(parents=True)
    (real / "a.md").write_text("hi")
    link = tmp_path / "docs"
    os.symlink(real, link)
    assert resolve_local(link, "a.md").read_text() == "hi"


def test_a_sibling_directory_with_a_shared_prefix_is_not_inside(tmp_path: Path) -> None:
    """The `startswith` bug, which is why this uses `parents`.

    `/srv/docs-secret` starts with `/srv/docs`, so a prefix comparison lets it
    through. It is a different directory.
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs-secret").mkdir()
    (tmp_path / "docs-secret" / "x").write_text("no")
    with pytest.raises(PathRefused):
        resolve_local(tmp_path / "docs", "../docs-secret/x")


# --- the URL half ------------------------------------------------------------------

def test_a_url_is_built_by_joining_not_by_urljoin() -> None:
    """`urljoin` lets the right-hand side replace the left.

    `urljoin("https://cloud/dav/files/me/", "//evil.test/x")` is
    `https://evil.test/x`, and `"/x"` is `https://cloud/x` — outside the share.
    Both are reachable from a string the model chose.
    """
    base = "https://cloud.test/remote.php/dav/files/me/"
    assert join_url(base, "notes/a.md") == (
        "https://cloud.test/remote.php/dav/files/me/notes/a.md"
    )
    assert join_url(base, "/x") == "https://cloud.test/remote.php/dav/files/me/x"
    assert join_url(base, "//evil.test/x") == (
        "https://cloud.test/remote.php/dav/files/me/evil.test/x"
    )


def test_a_double_slash_is_not_a_root_of_its_own() -> None:
    """POSIX gives `//` an implementation-defined root, and pathlib honours it.

    `PurePosixPath("//evil/x").parts` is `("//", "evil", "x")`, so a check that
    only skips `"/"` lets the `"//"` through — and `Path("/root") / "//evil/x"`
    is `//evil/x`, because an absolute-looking operand REPLACES the base. The
    resolved-path comparison catches it too, but a function whose whole job is
    to return something relative must not return something absolute.
    """
    assert safe_relative("//evil.test/x") == "evil.test/x"
    assert not safe_relative("//x").startswith("/")
    assert not safe_relative("////x").startswith("/")


def test_a_url_refuses_the_same_climbs_the_filesystem_does() -> None:
    with pytest.raises(PathRefused):
        join_url("https://cloud.test/dav/me/", "../../other-user/private")


def test_a_url_percent_encodes_what_a_filename_may_contain() -> None:
    got = join_url("https://cloud.test/dav/me/", "my notes/a&b.md")
    assert " " not in got
    assert "%20" in got
    assert "%26" in got
    # Separators stay separators; encoding them would ask for one long filename.
    assert got.count("/") == 6


def test_an_empty_path_is_the_collection_itself() -> None:
    assert join_url("https://cloud.test/dav/me", "") == "https://cloud.test/dav/me/"
