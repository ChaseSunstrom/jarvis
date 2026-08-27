"""Getting a command run on the host by writing a file git will execute.

## What this is

Jarvis Code's central claim was "the agent has no shell". It was false, and had
been since the feature shipped — not because a shell existed, but because git
does not need one. git is configurable enough to be an execution primitive, and
every one of its levers lives in a file **inside the repository**, which is
precisely what a coding job is allowed to change:

    .git/hooks/post-checkout   runs on `git checkout -B`, which starts every job
    .git/config diff.*.textconv / diff.external
                               runs on the `git diff` that ends every job
    .git/config filter.*.clean runs on the `git add -A` that ends every job

All three were verified executing against real git before the fix, and all
three are reproduced below so they cannot come back. The host runs them as
whatever user jarvis-core runs as — outside any container, with the operator's
whole filesystem in reach.

It did not need the container work to be exploitable: `write_file` on any
writable repository was enough, and the payload fires on the NEXT job, which
makes it patient as well as quiet.

## The three answers

1. `Workspace.resolve_for_write` refuses `.git` outright. Nothing legitimate
   writes there.
2. `HOST_GIT_GUARDS` neutralises hooks, fsmonitor and the ext transport on
   every host invocation; `diff()` adds `--no-ext-diff --no-textconv`.
3. Clean/smudge filters have no disabling flag — the driver is named by
   `.gitattributes`, so there is no fixed `-c` — so they get a CHECK:
   `unsafe_git_config()`, and the job refuses to start.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.code.workspace import (  # noqa: E402
    HOST_GIT_GUARDS,
    GitError,
    PathRefused,
    Repo,
    Workspace,
    branch_name,
)

pytestmark = pytest.mark.asyncio


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    _git(root.parent, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    return root


@pytest.fixture
def ws(repo: Path) -> Workspace:
    return Workspace(Repo(name="project", path=str(repo), writable=True))


# ---------------------------------------------------------------------------
# 1. the write must not land in .git at all
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        ".git/hooks/post-checkout",
        ".git/config",
        ".git/hooks/pre-commit",
        "./.git/hooks/post-checkout",
        "sub/../.git/hooks/post-checkout",
    ],
)
async def test_the_agent_cannot_write_into_dot_git(ws: Workspace, repo: Path, path: str):
    with pytest.raises(PathRefused) as caught:
        ws.write(path, "#!/bin/sh\ntouch /tmp/pwned\n")
    assert ".git" in str(caught.value)
    assert not (repo / ".git" / "hooks" / "post-checkout").exists()


async def test_reading_git_is_still_allowed(ws: Workspace):
    """Only the write path is closed. A job may look at its own history."""
    assert ws.resolve(".git/config").name == "config"


async def test_an_ordinary_write_still_works(ws: Workspace, repo: Path):
    ws.write("src/new.py", "y = 2\n")
    assert (repo / "src" / "new.py").read_text() == "y = 2\n"


# ---------------------------------------------------------------------------
# 2. even if a hook exists, host git must not run it
# ---------------------------------------------------------------------------
async def test_a_planted_hook_makes_every_host_git_refuse(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """The patient one: written by one job, fired by the NEXT job's checkout.

    Planted directly, bypassing `resolve_for_write`, because that resolver is
    only the FIRST layer — a job with a container writes through the bind mount
    and never touches it. What is under test here is that host git refuses
    afterwards.

    An executable hook is refused rather than merely neutralised: this process
    can stop its own git running it, but the operator's shell, an editor or a
    cron job would still fire it.
    """
    marker = tmp_path / "ESCAPED_HOOK"
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)

    with pytest.raises(GitError) as caught:
        await ws.start_branch(branch_name("job1"))
    assert "hook" in str(caught.value)
    assert not marker.exists()


async def test_a_textconv_driver_makes_every_host_git_refuse(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """Fires within the SAME job: `_finish()` diffs the tree."""
    marker = tmp_path / "ESCAPED_TEXTCONV"
    script = tmp_path / "tc.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat \"$1\"\n")
    script.chmod(0o755)
    _git(repo, "config", "diff.evil.textconv", str(script))
    (repo / ".gitattributes").write_text("*.py diff=evil\n")

    (repo / "app.py").write_text("x = 2\n")
    with pytest.raises(GitError):
        await ws.diff()
    assert not marker.exists()


async def test_the_check_runs_before_every_git_not_once_per_job(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """Time of check / time of use, with the window held open by the model.

    The first version checked once, before planning. A sandboxed job then wrote
    `.git/config` through the bind mount — a path no write guard here can see —
    and the poisoned `git add` came afterwards. So the check is on `git()`
    itself.
    """
    # Clean at first: git works.
    assert (await ws.current_branch()).strip()
    # Now the "job" poisons it, exactly as run_command would.
    _git(repo, "config", "filter.evil.clean", "/bin/sh")
    with pytest.raises(GitError) as caught:
        await ws.current_branch()
    assert "run a command" in str(caught.value)


async def test_a_config_include_is_followed(ws: Workspace, repo: Path, tmp_path: Path):
    """`[include] path = evil` would otherwise make the scan theatre."""
    hidden = tmp_path / "hidden.cfg"
    hidden.write_text('[filter "e"]\n\tclean = /bin/sh\n')
    with (repo / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {hidden}\n")

    problem = ws.unsafe_git_config()
    assert problem
    assert "includes" in problem


async def test_a_one_line_section_is_caught(ws: Workspace, repo: Path):
    """git accepts `[a] b = c` on one line; an anchored pattern sees nothing."""
    with (repo / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write('[filter "e"] clean = /bin/sh\n')
    assert ws.unsafe_git_config()


async def test_the_operators_global_config_cannot_be_activated(ws: Workspace, repo: Path):
    """`.gitattributes` is a working-tree file a job may write, and it can
    activate a filter defined in the OPERATOR's `~/.gitconfig`. Host git reads
    neither the global nor the system config, so there is nothing to activate.
    """
    from jarvis.integrations.code.workspace import _host_git_env
    import os

    env = _host_git_env()
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_SYSTEM"] == os.devnull


def test_every_host_git_call_carries_the_guards():
    """The guards are on `git()`, so nothing can call git without them."""
    assert "-c" in HOST_GIT_GUARDS
    assert "core.hooksPath=/dev/null" in HOST_GIT_GUARDS
    assert "core.fsmonitor=" in HOST_GIT_GUARDS
    assert "protocol.ext.allow=never" in HOST_GIT_GUARDS


async def test_the_guards_are_actually_passed_to_git(repo: Path):
    """Layer two, on its own: even where the refusal is bypassed, the flags
    that neutralise hooks and diff drivers are on every invocation."""
    seen: list[list[str]] = []

    async def _spy(args, cwd, timeout, env=None):
        seen.append(args)
        return 0, "", ""

    ws = Workspace(Repo(name="p", path=str(repo)), runner=_spy)
    await ws.current_branch()
    assert seen, "no git ran at all"
    # Every call, not the first: `git()` now runs a config scan ahead of the
    # command, and a spot check on one invocation is how an unguarded second
    # one gets added without anybody noticing.
    for args in seen:
        assert args[:6] == list(HOST_GIT_GUARDS), args


def test_host_git_is_spawned_in_exactly_one_place():
    """One door, so the guards only have to be nailed to one door.

    Every host git goes through `Workspace.git`, which refuses on a poisoned
    config and prepends `HOST_GIT_GUARDS`. A second `create_subprocess_exec`
    naming git would be a way past both, and would look perfectly ordinary in
    review — so it is counted here instead.
    """
    import re

    source = Path(
        "jarvis/integrations/code/workspace.py"
    ).resolve()
    if not source.exists():  # pragma: no cover - running from another cwd
        source = (
            Path(__file__).resolve().parents[1]
            / "jarvis/integrations/code/workspace.py"
        )
    text = source.read_text()
    spawns = re.findall(r'create_subprocess_exec\(\s*\n?\s*"git"', text)
    assert len(spawns) == 1, f"{len(spawns)} places spawn host git, expected 1"


async def test_a_poisoned_config_says_which_file_and_which_key(
    ws: Workspace, repo: Path
):
    """The refusal is what an operator sees, so it has to be actionable.

    "Jarvis will not run git here" with no file and no key is a support
    ticket. Both halves of the check name both.
    """
    _git(repo, "config", "filter.evil.clean", "/bin/sh")
    for problem in (ws.unsafe_git_config(), await ws.async_unsafe_git_config()):
        assert str(repo / ".git" / "config") in problem
        assert "clean" in problem
        assert "run a command" in problem


# ---------------------------------------------------------------------------
# 3. filters have no flag, so the job refuses to start
# ---------------------------------------------------------------------------
async def test_a_clean_filter_makes_the_repository_unsafe_to_touch(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """`git add -A` runs it and no `-c` disables it, so this is a refusal."""
    script = tmp_path / "fc.sh"
    script.write_text("#!/bin/sh\ncat\n")
    script.chmod(0o755)
    _git(repo, "config", "filter.evil.clean", str(script))

    problem = ws.unsafe_git_config()
    assert problem
    assert "clean" in problem
    assert "run a command" in problem


@pytest.mark.parametrize(
    "key,value",
    [
        ("filter.x.smudge", "/bin/sh"),
        ("diff.x.textconv", "/bin/sh"),
        ("core.fsmonitor", "/bin/sh"),
        ("core.sshCommand", "/bin/sh"),
        ("credential.helper", "/bin/sh"),
        ("core.pager", "/bin/sh"),
    ],
)
async def test_every_execution_bearing_key_is_caught(
    ws: Workspace, repo: Path, key: str, value: str
):
    _git(repo, "config", key, value)
    assert ws.unsafe_git_config(), f"{key} was not noticed"


async def test_a_one_line_include_is_followed_too(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """The shipped hole: `[include] path = X` on ONE line.

    The include follower was anchored to the line start while the key scan
    below it was not, so a section header and its key on the same line — a
    form git honours, verified against git 2.43 — meant the included file was
    never read and its `filter.z.clean` never scanned. `git add` then ran it.
    """
    included = tmp_path / "included"
    included.write_text('[filter "z"]\n\tclean = touch /tmp/pwned\n')
    config = repo / ".git" / "config"
    config.write_text(config.read_text() + f"[include] path = {included}\n")

    # git honours it: this is the attack, not a hypothetical.
    assert "touch /tmp/pwned" in _git(repo, "config", "--get", "filter.z.clean")

    problem = ws.unsafe_git_config()
    assert problem, "the one-line include was not followed"
    assert "clean" in problem
    with pytest.raises(GitError):
        await ws.diff()


async def test_an_include_git_can_read_but_a_regex_cannot(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """Line continuation: the reason the textual scan is only half the check.

    git joins a value ending in a backslash with the next line. No regex over
    single lines models that, and rather than grow one, the second half asks
    git's own parser for the expanded key list. The assertion that the textual
    half MISSES this is deliberate: it records why the second half exists, and
    fails loudly if someone deletes it as redundant.
    """
    included = tmp_path / "evil"
    included.write_text('[filter "z"]\n\tclean = touch /tmp/pwned\n')
    head, tail = str(included)[:-2], str(included)[-2:]
    config = repo / ".git" / "config"
    config.write_text(config.read_text() + f'[include]\n\tpath = "{head}\\\n{tail}"\n')

    assert "touch /tmp/pwned" in _git(repo, "config", "--get", "filter.z.clean")

    assert ws.unsafe_git_config() == ""
    problem = await ws.async_unsafe_git_config()
    assert "filter.z.clean" in problem
    with pytest.raises(GitError):
        await ws.diff()


async def test_the_parser_half_never_clears_what_the_textual_half_condemned(
    ws: Workspace, repo: Path
):
    """Ordering: a passing `git config` must not overrule a textual refusal."""
    _git(repo, "config", "filter.evil.clean", "/bin/sh")
    assert ws.unsafe_git_config()
    assert await ws.async_unsafe_git_config()


async def test_gpg_program_is_in_the_roster(ws: Workspace, repo: Path):
    """`gpg.program` runs on a signed commit and was missing from the list."""
    _git(repo, "config", "gpg.program", "/bin/sh")
    assert ws.unsafe_git_config()


async def test_an_ordinary_repository_is_not_flagged(ws: Workspace):
    """`user.email` and friends must not trip it, or nothing ever runs."""
    assert ws.unsafe_git_config() == ""


async def test_a_repository_with_no_config_is_not_flagged(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    ws = Workspace(Repo(name="p", path=str(plain)))
    assert ws.unsafe_git_config() == ""


async def test_the_job_refuses_to_start_on_an_unsafe_repository(repo: Path, tmp_path: Path):
    """End to end: the check is wired into the agent, not merely available."""
    from types import SimpleNamespace

    from jarvis.core import Jarvis
    from jarvis.integrations.code.agent import CodeAgent
    from jarvis.integrations.code.workspace import GitError

    _git(repo, "config", "filter.evil.clean", "/bin/sh")

    jarvis = Jarvis(tmp_path / "cfg")
    jarvis.data["llm"] = SimpleNamespace(client=None, model="m")
    agent = CodeAgent(jarvis, Repo(name="p", path=str(repo), writable=True))

    with pytest.raises(GitError) as caught:
        await agent.execute("do something")
    assert "run a command" in str(caught.value)


# ---------------------------------------------------------------------------
# 4. what the container can reach, which is not the same question
# ---------------------------------------------------------------------------
async def test_a_symlink_out_of_the_repo_is_not_followed_by_search(
    ws: Workspace, repo: Path, tmp_path: Path
):
    """The asymmetry a bind mount does not cover.

    A symlink is inert in the container — `/work/up -> /` points at the
    CONTAINER's root — and live on the host, where the same link points at the
    operator's. `files_for_search` then walked it and `search` handed the
    matching lines back to the model, which is an arbitrary host-file read
    dressed up as a code search.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("BEGIN OPENSSH PRIVATE KEY\n")
    (repo / "escape").symlink_to(tmp_path)

    found = ws.files_for_search()
    assert not any("secret.txt" in str(f) for f in found), (
        "the search walk followed a symlink out of the repository"
    )


async def test_a_symlink_is_not_followed_by_the_listing_either(
    ws: Workspace, repo: Path, tmp_path: Path
):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "private.txt").write_text("x")
    (repo / "escape").symlink_to(tmp_path / "elsewhere")

    listed = {entry.path for entry in ws.listing("", depth=3)}
    assert not any("private.txt" in path for path in listed)


async def test_an_ordinary_file_is_still_found(ws: Workspace, repo: Path):
    """The guard must not blind the search to the repository itself."""
    (repo / "findme.py").write_text("needle = 1\n")
    assert any(f.name == "findme.py" for f in ws.files_for_search())
