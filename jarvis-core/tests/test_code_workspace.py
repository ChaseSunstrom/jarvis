"""The repository a coding job works in: confinement, and the git around it.

Two things are being pinned here, and they fail in opposite ways.

**Confinement** fails loudly in a test and silently in production: a path that
escapes the repository writes somewhere it was never allowed to, and nothing
about the write looks wrong. Every escape this author could think of is below,
including the symlink one, which no amount of string checking can see.

**The git discipline** fails quietly: a job that commits to the branch somebody
is working on has already done the damage by the time anyone notices. The rules
are that a job refuses a dirty tree, makes its own branch, and never touches
theirs — and each has a test, because each is one line away from not being
true.

These use a REAL git repository in a tmp_path. Faking git here would test the
fake: `checkout -B`, `add -A --intent-to-add` and `diff` all have behaviour
this module depends on and a stub would simply agree with whatever was written.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.code.workspace import (  # noqa: E402
    BRANCH_PREFIX,
    GitError,
    PathRefused,
    Repo,
    Workspace,
    branch_name,
    check_argv,
    repo_from_dict,
)

pytestmark = pytest.mark.asyncio


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    """A small real repository, committed, on a branch called `work`."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def handle():\n    return 1\n")
    (root / "README.md").write_text("# project\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("// no\n")
    _git(root.parent, "init", "-q", "-b", "work", str(root))
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    return root


@pytest.fixture
def ws(repo_dir: Path) -> Workspace:
    return Workspace(Repo(name="project", path=str(repo_dir), writable=True))


# ---------------------------------------------------------------------------
# confinement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "escape",
    [
        "../outside.txt",
        "src/../../outside.txt",
        "src/./../../outside.txt",
        "..%2f..%2foutside.txt",
        "..%252f..%252foutside.txt",
        "..\\..\\outside.txt",
    ],
)
async def test_a_path_out_of_the_repository_is_refused(ws: Workspace, escape: str):
    """Every shape of "up and out" this author could think of.

    Percent-encoded and double-encoded forms are here because the decode has to
    happen BEFORE the check — a `..` that is still `%2e%2e` when it is looked
    for is a `..` that gets through. The backslash form matters because
    `PurePosixPath` would treat it as an ordinary character in a filename.
    """
    with pytest.raises(PathRefused):
        ws.resolve(escape)


@pytest.mark.parametrize(
    "absolute, lands_on",
    [
        ("/etc/passwd", "etc/passwd"),
        ("//etc/passwd", "etc/passwd"),
        ("....//outside.txt", "..../outside.txt"),
    ],
)
async def test_an_absolute_looking_path_lands_inside_rather_than_escaping(
    ws: Workspace, absolute: str, lands_on: str
):
    """A leading slash is DROPPED, not honoured and not refused.

    "/src/app.py" is what a person types for a path inside a share, and reading
    it as the filesystem root would be absurd. The property that matters is not
    that it is rejected — it is that it cannot escape, and this pins where each
    one actually lands.

    `//etc/passwd` is here because it is the one that got through a previous
    implementation: `PurePosixPath("//x").parts` begins with `"//"`, not `"/"`,
    so a check for a leading slash missed it — and `Path("/root") / "//x"` is
    `//x`, the absolute-looking operand replacing the base entirely.

    `....//` is the near-miss that looks like an escape and is not: the segments
    are `....` and `outside.txt`, so it makes an oddly-named folder in the repo.
    """
    landed = ws.resolve(absolute)
    assert landed == ws.root.resolve() / lands_on
    assert ws.root.resolve() in landed.parents


async def test_a_symlink_out_of_the_repository_is_refused(ws: Workspace, tmp_path: Path):
    """The escape no string check can see.

    `secret` is a perfectly ordinary relative path with no `..` in it. What is
    on the other end is the whole question, which is why resolution is done
    against the real filesystem and not against the text.
    """
    secret = tmp_path / "outside.txt"
    secret.write_text("not yours\n")
    (ws.root / "secret").symlink_to(secret)
    with pytest.raises(PathRefused):
        ws.resolve("secret")


async def test_an_ordinary_path_resolves(ws: Workspace):
    assert ws.resolve("src/app.py").name == "app.py"
    assert ws.resolve("") == ws.root.resolve()


async def test_reading_and_writing_stay_inside(ws: Workspace):
    assert "def handle" in ws.read("src/app.py")
    ws.write("src/new/deep.py", "x = 1\n")
    assert (ws.root / "src" / "new" / "deep.py").read_text() == "x = 1\n"
    with pytest.raises(PathRefused):
        ws.write("../escaped.py", "x = 1\n")
    assert not (ws.root.parent / "escaped.py").exists()


async def test_reading_a_directory_is_a_clear_refusal_not_a_crash(ws: Workspace):
    with pytest.raises(FileNotFoundError):
        ws.read("src")


async def test_an_enormous_file_is_refused_with_advice(ws: Workspace):
    ws.write("big.txt", "x" * 5000)
    with pytest.raises(ValueError) as caught:
        ws.read("big.txt", limit=1000)
    assert "in pieces" in str(caught.value)


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------
async def test_the_listing_skips_what_nobody_wants_read(ws: Workspace):
    """`node_modules` in a listing is a context window spent on nothing."""
    paths = {entry.path for entry in ws.listing("", depth=2)}
    assert "src" in paths
    assert Path("src/app.py").as_posix() in {Path(p).as_posix() for p in paths}
    assert not any("node_modules" in p for p in paths)
    assert not any(p.startswith(".git") for p in paths)


async def test_the_listing_is_bounded(ws: Workspace):
    for index in range(600):
        ws.write(f"many/f{index}.txt", "x")
    entries = ws.listing("many", depth=1)
    assert len(entries) <= 400


async def test_search_does_not_walk_into_skipped_directories(ws: Workspace):
    files = {f.name for f in ws.files_for_search()}
    assert "app.py" in files
    assert "junk.js" not in files


# ---------------------------------------------------------------------------
# git discipline
# ---------------------------------------------------------------------------
async def test_a_repository_is_recognised_and_a_bare_directory_is_not(
    ws: Workspace, tmp_path: Path
):
    assert await ws.is_repo()
    plain = Workspace(Repo(name="plain", path=str(tmp_path / "plain")))
    (tmp_path / "plain").mkdir()
    assert not await plain.is_repo()


async def test_a_dirty_tree_is_visible(ws: Workspace):
    assert not await ws.is_dirty()
    ws.write("src/app.py", "def handle():\n    return 2\n")
    assert await ws.is_dirty()


async def test_a_job_works_on_its_own_branch_and_leaves_yours_alone(ws: Workspace):
    """The rule the whole design rests on.

    After a job, `work` must be exactly where it was. Not "nearly" — the point
    of a branch is that somebody can keep working while a job runs and find
    their own branch untouched afterwards.
    """
    before = (await ws.git("rev-parse", "work")).strip()
    await ws.start_branch(branch_name("abc123"))
    ws.write("src/app.py", "def handle():\n    return 2\n")
    await ws.commit("change the handler")
    assert (await ws.current_branch()).startswith(f"{BRANCH_PREFIX}/")
    assert (await ws.git("rev-parse", "work")).strip() == before


async def test_starting_the_same_branch_twice_is_not_a_failure(ws: Workspace):
    """A job re-run after a crash would otherwise die on "already exists"."""
    name = branch_name("abc123")
    await ws.start_branch(name)
    await ws.start_branch(name)
    assert (await ws.current_branch()) == name


async def test_the_diff_shows_a_new_file_not_only_a_changed_one(ws: Workspace):
    """`--intent-to-add` is why. Without it a created file is invisible.

    This is the bug that makes a job report "nothing changed" after writing an
    entire new module, and it is invisible in any test that only edits.
    """
    await ws.start_branch(branch_name("j"))
    ws.write("src/brand_new.py", "y = 2\n")
    patch, stat = await ws.diff()
    assert "brand_new.py" in patch
    assert "brand_new.py" in stat


async def test_the_diff_is_truncated_rather_than_unbounded(ws: Workspace):
    await ws.start_branch(branch_name("j"))
    ws.write("huge.txt", "line\n" * 200_000)
    patch, _ = await ws.diff()
    assert len(patch) <= 400_000


async def test_a_failing_git_command_says_what_it_said(ws: Workspace):
    with pytest.raises(GitError) as caught:
        await ws.git("rev-parse", "no-such-branch-anywhere")
    assert "no-such-branch-anywhere" in str(caught.value)


async def test_discarding_leaves_the_branch_behind(ws: Workspace):
    """A cancelled job's branch is the record of what it tried.

    Deleting it would throw away the only evidence, and the person who
    cancelled is usually the person who wants to look.
    """
    name = await ws.start_branch(branch_name("j"))
    ws.write("src/app.py", "broken")
    await ws.discard()
    assert not await ws.is_dirty()
    assert name in await ws.git("branch", "--list", name)


async def test_a_commit_is_attributed_to_jarvis_not_to_you(ws: Workspace):
    """`-c user.name=` per command, not `git config`.

    Setting it in the repository's config would outlive the job and quietly
    re-author the human's next commit.
    """
    await ws.start_branch(branch_name("j"))
    ws.write("src/app.py", "def handle():\n    return 3\n")
    await ws.commit("change it")
    assert "Jarvis" in await ws.git("log", "-1", "--format=%an")
    assert "Test" in await ws.git("log", "-2", "--format=%an")


# ---------------------------------------------------------------------------
# no shell
# ---------------------------------------------------------------------------
def test_a_check_command_is_split_not_handed_to_a_shell():
    assert check_argv("pytest -q") == ["pytest", "-q"]
    assert check_argv("ruff check .") == ["ruff", "check", "."]


def test_shell_metacharacters_stay_arguments():
    """`; rm -rf /` must be an argument to pytest, not a second command.

    This is why `create_subprocess_exec` and not `_shell`. The string here is
    the operator's, but the property that makes it safe should not depend on
    that: a check list edited by a script, or one day chosen from somewhere
    else, must not become a shell injection.
    """
    argv = check_argv("pytest -q; rm -rf /")
    assert argv == ["pytest", "-q;", "rm", "-rf", "/"]


def test_an_empty_check_command_is_refused():
    with pytest.raises(ValueError):
        check_argv("   ")


async def test_git_arguments_never_reach_a_shell(ws: Workspace):
    """A branch name a model wrote, containing a semicolon, is one argument."""
    with pytest.raises(GitError):
        await ws.git("rev-parse", "x; touch /tmp/jarvis-code-pwned")
    assert not Path("/tmp/jarvis-code-pwned").exists()


async def test_git_cannot_wait_on_a_terminal_nobody_is_sitting_at(ws: Workspace):
    """Stdin is closed, so a git that decides to prompt fails instead of hanging.

    Asserted through `hash-object --stdin`, which reads stdin to EOF: with a
    closed stdin it hashes nothing and returns git's well-known empty-blob id.
    Inheriting this process's stdin instead would leave a job that hit a
    passphrase prompt stalled until its own wall clock killed it, twenty
    minutes later, with no explanation.
    """
    out = await ws.git("hash-object", "--stdin")
    assert out.strip() == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


async def test_a_check_that_hangs_is_killed(repo_dir: Path):
    """The realistic hang: a test suite that waits for something forever.

    Exercised through the real spawn path, with a real slow command, because
    the thing being pinned is that `wait_for` is paired with a `kill()` — a
    timeout that abandons the process leaves it running after the job is over.
    """
    from jarvis.integrations.code.agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.ws = Workspace(Repo(name="p", path=str(repo_dir)))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("jarvis.integrations.code.agent.CHECK_TIMEOUT", 0.4)
        code, out = await agent._spawn(["sleep", "30"])
    assert code == 1
    assert "timed out" in out


# ---------------------------------------------------------------------------
# the sandbox wrapper
# ---------------------------------------------------------------------------
def test_without_a_wrapper_a_check_is_the_command_itself(repo_dir: Path):
    ws = Workspace(Repo(name="p", path=str(repo_dir)))
    assert ws.sandbox_argv(["pytest", "-q"]) == ["pytest", "-q"]


def test_a_wrapper_wraps_and_knows_where_the_repository_is(repo_dir: Path):
    ws = Workspace(
        Repo(name="p", path=str(repo_dir)),
        sandbox=["docker", "run", "--rm", "--network", "none", "-v", "{repo}:/w"],
    )
    argv = ws.sandbox_argv(["pytest", "-q"])
    assert argv[:2] == ["docker", "run"]
    assert f"{repo_dir}:/w" in argv
    assert argv[-2:] == ["pytest", "-q"]


def test_the_wrapper_is_not_applied_to_git(repo_dir: Path):
    """Confining the thing that makes the branch would leave nothing to review.

    `sandbox_argv` is called at exactly one place — the check spawn — and this
    pins that git does not go through it, because a `docker run` around
    `git checkout` would make the branch inside a container that then exits.
    """
    import inspect

    from jarvis.integrations.code import agent as agent_module

    source = inspect.getsource(agent_module)
    assert source.count("sandbox_argv") == 1
    assert "sandbox_argv(check_argv(" in source


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def test_a_repository_needs_a_name_and_a_path():
    assert repo_from_dict({"name": "a"}) is None
    assert repo_from_dict({"path": "/tmp"}) is None
    assert repo_from_dict("nonsense") is None
    assert repo_from_dict({"name": "a", "path": "/tmp"}) is not None


def test_a_repository_is_read_only_unless_it_says_otherwise():
    """The default that matters. `writable` absent must not mean writable."""
    assert repo_from_dict({"name": "a", "path": "/tmp"}).writable is False
    assert repo_from_dict({"name": "a", "path": "/tmp", "writable": True}).writable


def test_checks_are_bounded_and_blank_ones_dropped():
    repo = repo_from_dict(
        {"name": "a", "path": "/tmp", "checks": ["pytest -q", "  ", *["x"] * 20]}
    )
    assert "  " not in repo.checks
    assert len(repo.checks) <= 8


def test_a_branch_name_says_when_it_was_made():
    name = branch_name("abc123", now=1_700_000_000)
    assert name.startswith(f"{BRANCH_PREFIX}/")
    assert name.endswith("-abc123")


def test_two_jobs_do_not_share_a_branch():
    assert branch_name("aaa") != branch_name("bbb")


async def test_a_missing_git_binary_is_an_error_not_a_traceback(tmp_path: Path):
    """A box without git installed must say so, not raise FileNotFoundError.

    `_run_git` catches OSError precisely so that "git is not installed" reads
    like every other failure a job can report.
    """
    from jarvis.integrations.code.workspace import _run_git

    code, _out, err = await _run_git(["--version"], tmp_path / "nope", 5.0)
    assert code == 1
    assert "could not run git" in err


def test_the_module_holds_no_second_path_checker():
    """Two path checkers is one path checker and a bug.

    `workspace.py` must resolve through `files/paths.py` and nowhere else. A
    local `..` check added here later would be a second implementation that
    drifts, and the symlink case is the one it would forget.
    """
    import inspect

    from jarvis.integrations.code import workspace as module

    source = inspect.getsource(module)
    assert "resolve_local" in source
    assert '".."' not in source, "a hand-rolled traversal check has crept in"

