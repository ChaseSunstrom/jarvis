"""The path-escape guard, tested hard.

The interesting cases are not "does it reject `../../etc/passwd`" — every
implementation rejects that. They are the ones where the string looks fine and
the *filesystem* does the escaping: a symlink inside the workspace, a symlinked
parent directory, a not-yet-created file whose parent is a symlink, and an
absolute path that happens to be inside a root.
"""

from __future__ import annotations

import os

import pytest

from jarvis_desktop.actions.paths import PathScope, ScopeError, safe_join


@pytest.fixture()
def scope(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "notes.txt").write_text("hello")
    (root / "sub").mkdir()
    (root / "sub" / "deep.txt").write_text("deep")
    return PathScope([root])


@pytest.fixture()
def outside(tmp_path):
    secret = tmp_path / "outside"
    secret.mkdir()
    target = secret / "secret.txt"
    target.write_text("SHOULD NEVER BE READ")
    return target


# --- the easy half: strings -------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "../secret.txt",
        "../../etc/passwd",
        "sub/../../escape.txt",
        "sub/../../../../../../etc/shadow",
        "./../outside",
        "a/b/c/../../../../x",
    ],
)
def test_dotdot_traversal_is_rejected(scope, raw):
    result = scope.resolve(raw)
    assert not result.allowed
    assert "escape" in (result.reason or "").lower()


@pytest.mark.parametrize(
    "raw",
    ["/etc/passwd", "/", "/tmp/anything", "/etc/../etc/passwd"],
)
def test_absolute_paths_outside_the_roots_are_rejected(scope, raw):
    result = scope.resolve(raw)
    assert not result.allowed
    assert "outside the allowed roots" in (result.reason or "")


@pytest.mark.parametrize("raw", ["~", "~/secret", "~root/.ssh/id_rsa", "~/../etc/passwd"])
def test_home_expansion_is_rejected(scope, raw):
    result = scope.resolve(raw)
    assert not result.allowed
    assert "home-relative" in (result.reason or "")


def test_null_bytes_are_rejected(scope):
    assert not scope.resolve("notes\x00.txt").allowed
    assert not scope.clean("a\x00b").allowed


@pytest.mark.parametrize(
    "raw",
    ["%2e%2e/secret", "sub/%2E%2E/%2e%2e/etc", "a%2fb", "a%5cb"],
)
def test_percent_encoded_separators_are_rejected(scope, raw):
    result = scope.clean(raw)
    assert not result.allowed
    assert "percent-encoded" in (result.reason or "")


@pytest.mark.parametrize("raw", ["file:///etc/passwd", "http://example.com/x", "a://b"])
def test_schemes_are_rejected(scope, raw):
    assert not scope.resolve(raw).allowed


@pytest.mark.parametrize("raw", ["//server/share/x", "\\\\server\\share"])
def test_unc_paths_are_rejected(scope, raw):
    assert not scope.resolve(raw).allowed


@pytest.mark.skipif(os.name == "nt", reason="POSIX separator rules")
def test_backslashes_are_rejected_on_posix(scope):
    result = scope.clean("sub\\..\\..\\etc")
    assert not result.allowed
    assert "backslash" in (result.reason or "")


def test_windows_drive_letters_are_rejected(scope):
    assert not scope.clean("C:/Windows/System32").allowed
    assert not scope.clean("c:\\windows").allowed


def test_empty_and_root_paths(scope):
    assert not scope.resolve("").allowed
    assert not scope.resolve(None).allowed
    assert not scope.resolve(".").allowed  # resolves to the root, not a file
    # ...but list_dir passes allow_root and gets the root itself.
    listing = scope.resolve(".", allow_root=True)
    assert listing.allowed and listing.path == scope.default_root


def test_a_path_that_is_not_a_string_is_rejected(scope):
    for raw in (42, [], {}, object()):
        assert not scope.resolve(raw).allowed


def test_absurdly_long_paths_are_rejected(scope):
    assert not scope.clean("a/" * 5000).allowed
    assert not scope.clean("x" * 300).allowed  # one over-long segment


# --- the hard half: the filesystem ------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_out_of_the_workspace_is_rejected(scope, outside):
    link = scope.default_root / "escape.txt"
    link.symlink_to(outside)
    assert link.exists()  # the link itself is perfectly readable
    result = scope.resolve("escape.txt")
    assert not result.allowed, "a symlink pointing outside the workspace was allowed"
    assert "symlink" in (result.reason or "")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlinked_parent_directory_is_rejected(scope, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "loot.txt").write_text("nope")
    (scope.default_root / "door").symlink_to(elsewhere, target_is_directory=True)
    assert not scope.resolve("door/loot.txt").allowed


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_writing_through_a_symlinked_parent_is_rejected(scope, tmp_path):
    """The file does not exist yet, so only the *parent* is a symlink. realpath
    still resolves it, which is the whole point."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (scope.default_root / "door").symlink_to(elsewhere, target_is_directory=True)
    assert not scope.resolve("door/new-file.txt").allowed


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_that_stays_inside_the_workspace_is_fine(scope):
    (scope.default_root / "alias.txt").symlink_to(scope.default_root / "notes.txt")
    result = scope.resolve("alias.txt")
    assert result.allowed
    assert result.path == scope.default_root / "notes.txt"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_dangling_symlink_out_of_the_workspace_is_rejected(scope, tmp_path):
    (scope.default_root / "ghost").symlink_to(tmp_path / "does-not-exist")
    assert not scope.resolve("ghost").allowed


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlinked_root_is_resolved_once_at_construction(tmp_path):
    """A workspace that is itself a symlink still contains its own files."""
    real = tmp_path / "real-workspace"
    real.mkdir()
    (real / "a.txt").write_text("x")
    alias = tmp_path / "aliased"
    alias.symlink_to(real, target_is_directory=True)

    scope = PathScope([alias])
    result = scope.resolve("a.txt")
    assert result.allowed
    assert result.path == real / "a.txt"


# --- what is allowed --------------------------------------------------------


def test_ordinary_relative_paths_are_allowed(scope):
    result = scope.resolve("notes.txt")
    assert result.allowed
    assert result.path == scope.default_root / "notes.txt"
    assert result.relative == "notes.txt"
    assert result.root == scope.default_root


def test_interior_dotdot_that_stays_inside_is_allowed(scope):
    result = scope.resolve("sub/../notes.txt")
    assert result.allowed
    assert result.relative == "notes.txt"


def test_an_absolute_path_inside_a_root_is_allowed(scope):
    absolute = str(scope.default_root / "sub" / "deep.txt")
    result = scope.resolve(absolute)
    assert result.allowed
    assert result.relative == "sub/deep.txt"


def test_a_second_root_is_honoured(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (second / "in-two.txt").write_text("x")
    scope = PathScope([first, second])

    # Relative paths go to the default (first) root...
    assert scope.resolve("x.txt").root == first
    # ...but an absolute path inside the second root is accepted.
    result = scope.resolve(str(second / "in-two.txt"))
    assert result.allowed
    assert result.root == second


def test_must_exist_and_type_checks(scope):
    assert not scope.resolve("nope.txt", must_exist=True).allowed
    assert not scope.resolve("sub", must_be_file=True).allowed
    assert not scope.resolve("notes.txt", must_be_dir=True).allowed
    assert scope.resolve("sub", allow_root=True, must_be_dir=True).allowed


def test_require_raises_on_rejection(scope):
    with pytest.raises(ScopeError):
        scope.require("../escape")
    assert scope.require("notes.txt").name == "notes.txt"


# --- safe_join --------------------------------------------------------------


def test_safe_join_refuses_traversal(tmp_path):
    with pytest.raises(ScopeError):
        safe_join(tmp_path, "..", "escape")
    with pytest.raises(ScopeError):
        safe_join(tmp_path, "a/../../b")
    assert safe_join(tmp_path, "shots", "a.png") == tmp_path / "shots" / "a.png"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_safe_join_refuses_a_symlinked_target(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "door").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ScopeError):
        safe_join(root, "door", "x.png")


# --- the guard is not fooled by case or trailing slashes --------------------


def test_trailing_slashes_and_dots_are_normalised(scope):
    for raw in ("sub/", "sub/.", "./sub/./"):
        result = scope.resolve(raw, allow_root=True, must_be_dir=True)
        assert result.allowed, raw
        assert result.relative == "sub"


def test_scope_needs_at_least_one_root():
    with pytest.raises(ValueError):
        PathScope([])
