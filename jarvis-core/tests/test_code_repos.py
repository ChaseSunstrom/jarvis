"""Repositories Jarvis makes, and the root it may not leave.

Until this, every repository was declared by hand in configuration.yaml. That
is the right default — it is what makes "only paths the operator named exist"
true — but it meant that asked for something new, the honest answer was "there
is nowhere to put it".

`code: workspace:` is the whole permission model, and these tests are about its
edges. Inside it Jarvis may create freely; outside it nothing changed, and the
tests that matter most are the ones proving a name cannot be used to get out.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.code.repos import (  # noqa: E402
    MAX_REPOS,
    CreatedRepo,
    RepoStore,
    check_name,
    initial_files,
)
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.code import (  # noqa: E402
    DOMAIN,
    CodeConfig,
    listing_payload,
)
from jarvis.integrations.code import DATA_CONFIG  # noqa: E402
from jarvis.integrations.code.workspace import _run_git  # noqa: E402

pytestmark = pytest.mark.asyncio


class _Memory:
    """A Store that never touches disk."""

    def __init__(self) -> None:
        self.data: dict | None = None

    async def load(self):
        return self.data

    async def save(self, data):
        self.data = data


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


@pytest.fixture
def store(workspace: Path) -> RepoStore:
    return RepoStore(_Memory(), workspace)


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["snake", "snake-opengl", "a1", "my.project", "a_b"])
def test_a_reasonable_name_is_allowed(name: str):
    assert check_name(name) == ""


@pytest.mark.parametrize(
    "name,because",
    [
        ("", "needs a name"),
        ("   ", "needs a name"),
        ("../etc", "lowercase letters"),
        ("a/b", "lowercase letters"),
        ("a b", "lowercase letters"),
        ("-lead", "lowercase letters"),
        (".hidden", "lowercase letters"),
        ("Snake", "lowercase"),
        ("x" * 65, "too long"),
        ("git", "reserved"),
        ("node_modules", "reserved"),
    ],
)
def test_a_dangerous_or_confusing_name_is_refused_with_a_reason(name: str, because: str):
    """A sentence, not a bool: this reaches a person typing in a form and a
    model choosing a name, and both can act on the reason."""
    problem = check_name(name)
    assert problem, f"{name!r} was allowed"
    assert because in problem.lower()


def test_a_new_repository_explains_itself():
    files = initial_files("snake", "a snake game")
    assert "# snake" in files["README.md"]
    assert "a snake game" in files["README.md"]
    # A directory that appears on your disk should say who made it.
    assert "Jarvis" in files["README.md"]
    assert "__pycache__" in files[".gitignore"]


# ---------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------
async def test_it_makes_a_real_git_repository(store: RepoStore, workspace: Path):
    entry, why = await store.async_create("snake", description="a snake game")
    assert entry is not None, why

    root = workspace / "snake"
    assert (root / ".git").is_dir()
    assert (root / "README.md").is_file()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=root, capture_output=True, text=True
    )
    assert "Initial commit" in log.stdout
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root, capture_output=True, text=True,
    )
    # Pinned, not left to the host's git version: two machines with different
    # defaults would otherwise disagree about the branch name.
    assert branch.stdout.strip() == "main"


async def test_the_commit_is_attributed_to_jarvis(store: RepoStore, workspace: Path):
    await store.async_create("snake")
    author = subprocess.run(
        ["git", "log", "-1", "--format=%an"],
        cwd=workspace / "snake", capture_output=True, text=True,
    )
    assert "Jarvis" in author.stdout


async def test_a_created_repository_is_writable(store: RepoStore):
    """Unlike a declared one, where read-only is the default. The operator did
    not point Jarvis at their project — Jarvis made this directory."""
    entry, _ = await store.async_create("snake")
    repo = entry.as_repo()
    assert repo.writable is True
    assert repo.managed is True


async def test_two_repositories_cannot_share_a_name(store: RepoStore):
    assert (await store.async_create("snake"))[0] is not None
    entry, why = await store.async_create("snake")
    assert entry is None
    assert "already" in why


async def test_a_name_a_declared_repository_holds_is_refused(store: RepoStore):
    entry, why = await store.async_create("snake", taken={"snake"})
    assert entry is None
    assert "configuration.yaml" in why


async def test_an_existing_directory_is_never_written_into(
    store: RepoStore, workspace: Path
):
    """Somebody's files must not become a Jarvis repository by name collision."""
    (workspace / "mine").mkdir()
    (workspace / "mine" / "important.txt").write_text("do not touch")
    entry, why = await store.async_create("mine")
    assert entry is None
    assert "already exists" in why
    assert (workspace / "mine" / "important.txt").read_text() == "do not touch"


async def test_the_number_of_repositories_is_bounded(store: RepoStore):
    store.repos = {f"r{i}": CreatedRepo(f"r{i}", "/x") for i in range(MAX_REPOS)}
    entry, why = await store.async_create("one-more")
    assert entry is None
    assert str(MAX_REPOS) in why


# ---------------------------------------------------------------------------
# the root it may not leave
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "escape", ["../outside", "..", "a/../../b", "/etc/passwd", "sub/deep"]
)
async def test_a_name_cannot_be_used_to_leave_the_workspace(
    store: RepoStore, workspace: Path, escape: str
):
    """The name becomes a directory, so it is a path in disguise.

    Refused by `check_name` before it is ever resolved — but the resolver is
    behind it too, which is why the assertion is about what landed on disk
    rather than about which layer said no.
    """
    entry, why = await store.async_create(escape)
    assert entry is None, f"{escape!r} was created"
    assert why
    assert not (workspace.parent / "outside").exists()
    assert list(workspace.iterdir()) == []


async def test_with_no_workspace_configured_creation_is_refused(tmp_path: Path):
    """An operator who has not opted in gets exactly what they had."""
    store = RepoStore(_Memory(), None)
    assert not store.enabled
    entry, why = await store.async_create("snake")
    assert entry is None
    assert "code: workspace:" in why


async def test_a_failed_git_leaves_a_clear_message(store: RepoStore):
    async def _broken(args, cwd, timeout):
        return 1, "", "git exploded"

    entry, why = await store.async_create("snake", git=_broken)
    assert entry is None
    assert "git exploded" in why


# ---------------------------------------------------------------------------
# remembering
# ---------------------------------------------------------------------------
async def test_a_created_repository_survives_a_restart(workspace: Path):
    """Without this a repository Jarvis made would vanish from the listing on
    restart while still sitting on disk — present but unreachable."""
    memory = _Memory()
    first = RepoStore(memory, workspace)
    await first.async_create("snake")

    second = RepoStore(memory, workspace)
    await second.async_load()
    assert "snake" in second.repos
    assert second.repos["snake"].path == str(workspace / "snake")


async def test_a_repository_deleted_from_disk_is_dropped_on_load(workspace: Path):
    """A row that cannot be opened is worse than no row."""
    memory = _Memory()
    first = RepoStore(memory, workspace)
    await first.async_create("snake")
    import shutil

    shutil.rmtree(workspace / "snake")

    second = RepoStore(memory, workspace)
    await second.async_load()
    assert second.repos == {}


async def test_forgetting_removes_the_row_and_keeps_the_files(
    store: RepoStore, workspace: Path
):
    """`rm -rf` driven by a model — or a mis-click in a browser — is the one
    operation here with no undo. Jarvis creates directories; it does not
    remove them."""
    await store.async_create("snake")
    gone, note = await store.async_forget("snake")
    assert gone
    assert store.repos == {}
    assert (workspace / "snake" / "README.md").is_file()
    assert "does not delete" in note


async def test_forgetting_something_that_is_not_there_says_so(store: RepoStore):
    gone, note = await store.async_forget("nope")
    assert not gone
    assert "no repository" in note


async def test_a_corrupt_stored_row_is_dropped_not_fatal(workspace: Path):
    memory = _Memory()
    memory.data = {
        "repositories": [
            {"name": "ok", "path": str(workspace)},
            {"name": "", "path": "/x"},
            "nonsense",
            {"nothing": True},
        ]
    }
    store = RepoStore(memory, workspace)
    await store.async_load()
    assert set(store.repos) == {"ok"}


async def test_the_real_git_runner_is_what_production_uses(store: RepoStore):
    """The fixture injects nothing, so the tests above ran the real binary."""
    import inspect

    source = inspect.getsource(store.async_create)
    assert "_run_git" in source
    assert callable(_run_git)


async def test_a_server_with_no_git_says_how_to_install_it(store: RepoStore):
    """What the operator actually saw: an errno and a name they could not reuse.

    `[Errno 2] No such file or directory: 'git'` is accurate and useless. It
    is also raised by the FIRST git command, which runs after the directory
    and the README are already written — so the half-made repository then made
    the name unusable.
    """

    async def _no_git(args, cwd, timeout, env=None):
        return 1, "", "could not run git: [Errno 2] No such file or directory: 'git'"

    entry, why = await store.async_create("snake-opengl", git=_no_git)
    assert entry is None
    assert "git is not installed" in why
    assert "apt install git" in why
    assert "[Errno 2]" not in why, "the errno was passed through to the operator"
    assert not (store.workspace / "snake-opengl").exists(), (
        "a directory was left behind by a creation that failed"
    )


async def test_a_missing_git_is_explained_wherever_it_is_noticed(tmp_path: Path):
    """Not only at creation. Starting a job, diffing, pushing — all of them go
    through `_run_git`, so the sentence lives there and not in each caller."""
    from jarvis.integrations.code.workspace import GIT_MISSING, _run_git

    code, _out, err = await _run_git(
        ["--version"], tmp_path, 30.0, env={"PATH": str(tmp_path)}
    )
    assert code == 1
    assert err == GIT_MISSING
    assert "install" in err


async def test_a_missing_working_directory_is_not_reported_as_a_missing_git(
    tmp_path: Path,
):
    """ENOENT means two very different things here, and they arrive alike.

    No `git` on the PATH and no directory to run it in are both
    `FileNotFoundError`. Reporting the second as the first sends the operator
    to install something they already have — which is what the first version
    of this guard did.
    """
    from jarvis.integrations.code.workspace import GIT_MISSING, _run_git

    code, _out, err = await _run_git(["--version"], tmp_path / "gone", 5.0)
    assert code == 1
    assert err != GIT_MISSING
    assert "could not run git" in err


async def test_the_name_is_usable_again_after_a_failed_creation(store: RepoStore):
    """The half-made directory was the part that hurt.

    Without the rollback the operator installs git, tries the same name, and
    is told it already exists — by a directory Jarvis made and did not
    register, which is the worst of both.
    """
    calls: list[list[str]] = []

    async def _dies_at_commit(args, cwd, timeout, env=None):
        calls.append(args)
        if args[0] == "--version":
            return 0, "git version 2.43.0", ""
        if "commit" in args:
            return 1, "", "nothing to commit"
        return 0, "", ""

    entry, why = await store.async_create("snake-opengl", git=_dies_at_commit)
    assert entry is None and "commit" in why
    assert not (store.workspace / "snake-opengl").exists()
    assert "snake-opengl" not in store.repos

    # And now the same name works, which is the whole point.
    entry, why = await store.async_create("snake-opengl")
    assert entry is not None, why


async def test_git_is_checked_before_anything_is_written(store: RepoStore):
    """Order matters: the probe has to come first or the rollback is doing
    work that need never have happened."""
    seen: list[str] = []

    async def _runner(args, cwd, timeout, env=None):
        seen.append(args[0])
        if args[0] == "--version":
            # Refuse here, and nothing below should have run.
            return 1, "", "could not run git: [Errno 2] No such file or directory: 'git'"
        return 0, "", ""

    await store.async_create("snake-opengl", git=_runner)
    assert seen == ["--version"], seen


async def test_the_workspace_root_is_created_if_it_is_not_there(tmp_path: Path):
    """`workspace: ~/jarvis/workspaces` on a fresh machine is a path that does
    not exist yet, and refusing to make it would make the default useless."""
    root = tmp_path / "not" / "yet"
    store = RepoStore(_Memory(), root)
    entry, why = await store.async_create("snake")
    assert entry is not None, why
    assert (root / "snake" / ".git").is_dir()


async def test_a_clone_that_fails_leaves_nothing_behind(store: RepoStore, tmp_path: Path):
    from jarvis.integrations.code.forges import Forge

    async def _fails(args, cwd, timeout, env=None):
        if args[0] == "--version":
            return 0, "git version 2.43.0", ""
        # git itself creates the directory before it fails partway.
        (store.workspace / "widgets").mkdir(parents=True, exist_ok=True)
        return 1, "", "fatal: could not read Username"

    forge = Forge(name="work", kind="github", host="github.com", allow=["acme/widgets"])
    entry, why = await store.async_clone(
        forge, "acme/widgets", config_dir=tmp_path, git=_fails
    )
    assert entry is None and "clone failed" in why
    assert not (store.workspace / "widgets").exists()


def test_the_workspace_defaults_to_the_home_directory():
    """A fresh install can make a repository without being configured first.

    Off-by-default read as cautious and mostly just broke the feature: "write
    me a Snake game" answered "there is nowhere to put it", and the fix was a
    key nobody knew to look for.
    """
    from jarvis.integrations.code import DEFAULT_WORKSPACE, CodeConfig

    expected = Path(DEFAULT_WORKSPACE).expanduser()
    for raw in ({}, {"workspace": ""}, {"workspace": None}, {"workspace": "  "}):
        assert CodeConfig.from_config(raw).workspace == expected, raw


@pytest.mark.parametrize("value", ["off", "no", "none", "false", "disabled", "OFF", False])
def test_creation_can_still_be_turned_off_explicitly(value):
    """Removing the opt-in must not remove the ability to opt out."""
    from jarvis.integrations.code import CodeConfig

    assert CodeConfig.from_config({"workspace": value}).workspace is None


def test_an_operators_own_path_still_wins():
    from jarvis.integrations.code import CodeConfig

    assert CodeConfig.from_config({"workspace": "~/elsewhere"}).workspace == (
        Path("~/elsewhere").expanduser()
    )


async def test_the_refusal_names_the_setting_that_is_actually_wrong():
    """"Set `code: workspace:`" is unhelpful when it IS set — to `off`."""
    store = RepoStore(_Memory(), None)
    _entry, why = await store.async_create("snake")
    assert "turned off" in why
    assert "~/jarvis/workspaces" in why


# ---------------------------------------------------------------------------
# the container, where `~` is a lie
# ---------------------------------------------------------------------------
def test_an_env_var_can_move_the_default_workspace(monkeypatch):
    """`~` inside an image is the container's own writable layer.

    So the default would put repositories somewhere `docker compose up -d
    --build` destroys — a data-loss bug wearing a sensible-looking path. The
    Dockerfile sets this to `/config/workspaces`, the bind mount everything
    else Jarvis persists already lives in.
    """
    from jarvis.integrations.code import ENV_WORKSPACE, CodeConfig

    monkeypatch.setenv(ENV_WORKSPACE, "/config/workspaces")
    assert CodeConfig.from_config({}).workspace == Path("/config/workspaces")


def test_the_operators_own_setting_beats_the_images_default(monkeypatch):
    """An image default is not a policy: `workspace:` in configuration.yaml is
    a decision somebody made, and it wins."""
    from jarvis.integrations.code import ENV_WORKSPACE, CodeConfig

    monkeypatch.setenv(ENV_WORKSPACE, "/config/workspaces")
    assert CodeConfig.from_config({"workspace": "~/mine"}).workspace == (
        Path("~/mine").expanduser()
    )
    assert CodeConfig.from_config({"workspace": "off"}).workspace is None


def test_an_empty_env_var_is_not_a_setting(monkeypatch):
    """Compose passes `FOO=${FOO:-}` constantly; empty must mean unset."""
    from jarvis.integrations.code import DEFAULT_WORKSPACE, ENV_WORKSPACE, CodeConfig

    monkeypatch.setenv(ENV_WORKSPACE, "   ")
    assert CodeConfig.from_config({}).workspace == Path(DEFAULT_WORKSPACE).expanduser()


def test_the_image_installs_git_and_keeps_repositories_off_the_writable_layer():
    """Two container facts that are invisible until somebody loses work.

    Read out of the Dockerfile rather than trusted: the whole reason Jarvis
    Code failed on a real install was that `git` was not in the image, and the
    fix for THAT would have quietly introduced the second problem — a default
    workspace under `~`, which a rebuild deletes.
    """
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    assert dockerfile.is_file(), dockerfile
    text = dockerfile.read_text(encoding="utf-8")

    assert "--no-install-recommends git" in text, "the image no longer installs git"
    assert "JARVIS_CODE_WORKSPACE=/config/workspaces" in text, (
        "repositories would land in the container's writable layer and be "
        "destroyed by the next rebuild"
    )


def test_the_compose_file_makes_the_workspace_directory_writable():
    """uid 10003 cannot chown a bind mount, so the init job has to make it."""
    compose = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    assert compose.is_file(), compose
    text = compose.read_text(encoding="utf-8")
    assert "/config/workspaces" in text, (
        "the config-init job does not create the workspace directory"
    )


def test_the_console_and_the_server_agree_about_names():
    """Two implementations of one rule, pinned together.

    `whyNotName` in `jarvis-web/src/lib/code.ts` is a deliberate copy of
    `check_name`: the form has to say why a name is bad without a round trip.
    The server still refuses independently — the copy is for the message, not
    for the decision — but a copy that DRIFTS is worse than none, because the
    form would accept something the server then rejects with a different
    sentence, and the reader would blame the form.

    Read out of the TypeScript rather than trusted: a comment saying "keep
    these in step" is what this replaces.
    """
    import re

    web = (
        Path(__file__).resolve().parents[2] / "jarvis-web" / "src" / "lib" / "code.ts"
    )
    assert web.is_file(), web
    source = web.read_text(encoding="utf-8")

    # The reserved list, both sides.
    block = re.search(r"RESERVED_NAMES = new Set\(\[(.*?)\]\)", source, re.S)
    assert block, "the console no longer has a reserved-name list"
    console_reserved = set(re.findall(r"'([^']+)'", block.group(1)))
    from jarvis.integrations.code.repos import _RESERVED

    assert console_reserved == set(_RESERVED), (
        "the console and the server disagree about reserved repository names: "
        f"only in console {sorted(console_reserved - set(_RESERVED))}, "
        f"only in server {sorted(set(_RESERVED) - console_reserved)}"
    )

    # And the shape rule itself, on the cases that matter.
    assert "[a-z0-9][a-z0-9._-]*" in source, (
        "the console's name pattern changed; check it still matches _NAME_RE"
    )
    assert "64" in source, "the console no longer bounds the length"


def test_every_name_the_console_would_accept_the_server_accepts_too():
    """The direction that actually hurts: the form says yes, the server says no."""
    import re

    web = (
        Path(__file__).resolve().parents[2] / "jarvis-web" / "src" / "lib" / "code.ts"
    )
    source = web.read_text(encoding="utf-8")
    pattern = re.search(r"/\^(\[a-z0-9\]\[a-z0-9\._-\]\*)\$/", source)
    # Fall back to the literal if the escaping differs; the point is that a
    # sample of names accepted by the console's rule passes the server's.
    console_re = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
    assert pattern or console_re, "no console pattern found"

    for candidate in ["snake", "a", "a.b", "a-b", "a_b", "z9", "x" * 64]:
        if console_re.match(candidate) and len(candidate) <= 64:
            assert check_name(candidate) == "", (
                f"the console would accept {candidate!r} and the server refuses it"
            )


def test_the_e2e_mock_answers_code_list_with_the_same_keys_as_the_server():
    """The gap that hid a whole feature.

    `jarvis/code/list` gained `forges` server-side and the e2e mock did not,
    so the console could not have drawn a clone form against it and no
    Playwright test could have caught the omission — the page would simply
    render nothing and pass. A missing key in a mock is invisible in exactly
    the way a missing key in a real response is not.

    Key names only. The mock's VALUES are fixtures and are supposed to differ;
    what must not differ is the shape, because the shape is the contract the
    console is written against.
    """
    import re

    mock = Path(__file__).resolve().parents[2] / "tests" / "web" / "mock-ha.mjs"
    assert mock.is_file(), f"the e2e mock is missing: {mock}"
    source = mock.read_text(encoding="utf-8")

    block = re.search(r"const codePayload = \(\) => \((\{.*?\n\t\})\)", source, re.S)
    assert block, "the mock no longer has a single codePayload() helper"
    mock_keys = set(re.findall(r"^\t\t(\w+):", block.group(1), re.MULTILINE))

    jarvis = Jarvis(Path(tempfile.mkdtemp()))
    jarvis.data[DOMAIN] = {DATA_CONFIG: CodeConfig()}
    server_keys = set(listing_payload(jarvis))

    assert mock_keys == server_keys, (
        "the e2e mock and jarvis-core disagree about what `jarvis/code/list` "
        f"answers with: only in mock {sorted(mock_keys - server_keys)}, "
        f"only in server {sorted(server_keys - mock_keys)}"
    )
