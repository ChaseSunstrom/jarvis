"""File actions end to end: they must not be able to touch anything outside a root.

:mod:`tests.test_paths` proves the guard is right. This proves the actions
actually use it — a correct guard that one action forgets to call protects
nothing.
"""

from __future__ import annotations

import os

import pytest

from jarvis_desktop.actions.base import Status
from jarvis_desktop.actions.files import DeleteFile, ListDir, ReadFile, WriteFile


@pytest.fixture()
def secret(tmp_path):
    path = tmp_path / "outside-secret.txt"
    path.write_text("PRIVATE KEY MATERIAL")
    return path


# --- read -------------------------------------------------------------------


def test_read_file_reads_inside_the_workspace(ctx, workspace):
    (workspace / "notes.txt").write_text("hello there")
    result = ReadFile().run(ctx, {"path": "notes.txt"})
    assert result.ok
    assert result.data["content"] == "hello there"
    assert result.data["path"] == "notes.txt"
    # File contents are data, not instructions.
    assert result.data["_untrusted"] is True


@pytest.mark.parametrize(
    "path", ["../outside-secret.txt", "/etc/passwd", "~/.ssh/id_rsa", "../../etc/shadow"]
)
def test_read_file_refuses_to_escape(ctx, secret, path):
    result = ReadFile().run(ctx, {"path": path})
    assert not result.ok
    assert "PRIVATE KEY" not in str(result.data)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_read_file_refuses_a_symlink_out_of_the_workspace(ctx, workspace, secret):
    (workspace / "shortcut.txt").symlink_to(secret)
    result = ReadFile().run(ctx, {"path": "shortcut.txt"})
    assert not result.ok
    assert "PRIVATE KEY" not in str(result.data)


def test_read_file_truncates_at_the_limit(ctx, workspace):
    (workspace / "big.txt").write_text("z" * 10_000)
    result = ReadFile().run(ctx, {"path": "big.txt", "max_bytes": 100})
    assert result.ok
    assert len(result.data["content"]) == 100
    assert result.data["truncated"] is True
    assert result.data["size_bytes"] == 10_000


def test_read_file_on_a_missing_file_is_an_honest_error(ctx):
    result = ReadFile().run(ctx, {"path": "nope.txt"})
    assert not result.ok
    assert "no such path" in (result.error or "")


def test_read_file_needs_a_path(ctx):
    assert not ReadFile().run(ctx, {}).ok


# --- write ------------------------------------------------------------------


def test_write_file_creates_and_replaces(ctx, workspace):
    action = WriteFile()
    assert action.run(ctx, {"path": "out/report.md", "content": "# hi"}).ok
    assert (workspace / "out" / "report.md").read_text() == "# hi"

    assert action.run(ctx, {"path": "out/report.md", "content": "replaced"}).ok
    assert (workspace / "out" / "report.md").read_text() == "replaced"

    assert action.run(ctx, {"path": "out/report.md", "content": "!", "append": True}).ok
    assert (workspace / "out" / "report.md").read_text() == "replaced!"


@pytest.mark.parametrize("path", ["../escaped.txt", "/tmp/escaped.txt", "~/escaped.txt"])
def test_write_file_refuses_to_escape(ctx, tmp_path, path):
    result = WriteFile().run(ctx, {"path": path, "content": "x"})
    assert not result.ok
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_write_file_refuses_a_symlinked_parent(ctx, workspace, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (workspace / "door").symlink_to(elsewhere, target_is_directory=True)
    result = WriteFile().run(ctx, {"path": "door/planted.txt", "content": "x"})
    assert not result.ok
    assert not (elsewhere / "planted.txt").exists()


def test_write_file_needs_string_content(ctx):
    assert not WriteFile().run(ctx, {"path": "a.txt"}).ok
    assert not WriteFile().run(ctx, {"path": "a.txt", "content": 42}).ok


def test_write_file_caps_the_size(ctx):
    result = WriteFile().run(ctx, {"path": "big.txt", "content": "x" * (9 * 1024 * 1024)})
    assert not result.ok
    assert "limit" in (result.error or "")


# --- list -------------------------------------------------------------------


def test_list_dir_lists_the_workspace(ctx, workspace):
    (workspace / "a.txt").write_text("a")
    (workspace / "sub").mkdir()
    result = ListDir().run(ctx, {})
    assert result.ok
    names = [e["name"] for e in result.data["entries"]]
    assert names == ["sub", "a.txt"]  # directories first, then alphabetical
    assert result.data["_untrusted"] is True


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_list_dir_flags_a_symlink_that_escapes(ctx, workspace, secret):
    (workspace / "escape").symlink_to(secret)
    result = ListDir().run(ctx, {})
    entry = next(e for e in result.data["entries"] if e["name"] == "escape")
    assert entry["type"] == "symlink"
    assert entry["escapes_workspace"] is True


def test_list_dir_refuses_a_directory_outside(ctx):
    assert not ListDir().run(ctx, {"path": "/etc"}).ok


def test_list_dir_refuses_a_file(ctx, workspace):
    (workspace / "a.txt").write_text("a")
    result = ListDir().run(ctx, {"path": "a.txt"})
    assert not result.ok
    assert "not a directory" in (result.error or "")


# --- delete -----------------------------------------------------------------


def test_delete_file_removes_a_file(ctx, workspace):
    target = workspace / "gone.txt"
    target.write_text("x")
    result = DeleteFile().run(ctx, {"path": "gone.txt"})
    assert result.ok
    assert not target.exists()


def test_delete_file_refuses_a_directory_without_recursive(ctx, workspace):
    (workspace / "keep").mkdir()
    result = DeleteFile().run(ctx, {"path": "keep"})
    assert not result.ok
    assert (workspace / "keep").exists()


def test_delete_file_removes_a_tree_when_asked(ctx, workspace):
    tree = workspace / "tree"
    (tree / "deep").mkdir(parents=True)
    (tree / "deep" / "a.txt").write_text("a")
    (tree / "b.txt").write_text("b")
    result = DeleteFile().run(ctx, {"path": "tree", "recursive": True})
    assert result.ok
    assert not tree.exists()


def test_delete_file_refuses_the_workspace_root(ctx, workspace):
    for path in (".", str(workspace)):
        result = DeleteFile().run(ctx, {"path": path, "recursive": True})
        assert result.status == Status.DENIED or not result.ok
    assert workspace.exists()


@pytest.mark.parametrize("path", ["../outside-secret.txt", "/etc/hosts"])
def test_delete_file_refuses_to_escape(ctx, secret, path):
    result = DeleteFile().run(ctx, {"path": path})
    assert not result.ok
    assert secret.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_deleting_a_symlink_does_not_delete_its_target(ctx, workspace, secret):
    """The link resolves outside, so the action refuses before touching anything."""
    (workspace / "link").symlink_to(secret)
    DeleteFile().run(ctx, {"path": "link"})
    assert secret.exists(), "the symlink target was deleted"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_recursive_delete_does_not_follow_a_symlink_out(ctx, workspace, tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep me")
    tree = workspace / "tree"
    tree.mkdir()
    (tree / "door").symlink_to(outside, target_is_directory=True)

    result = DeleteFile().run(ctx, {"path": "tree", "recursive": True})

    assert result.ok
    assert not tree.exists()
    assert (outside / "keep.txt").exists(), "a recursive delete followed a symlink out"
