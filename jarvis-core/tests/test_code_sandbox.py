"""The fences around a coding job that may run anything it likes.

Jarvis Code shipped with no shell: `run_check` matched a whole string against
the repository's own `checks:` list and nothing else existed. That is still
true of the HOST. What this adds is a container the job can run commands in —
including `pip install`, including reaching the network — and the whole of the
safety argument now rests on the argv in `container_argv()`.

So every fence gets a test, and each one fails if the flag is dropped. The
argv builder is a pure function on purpose: no daemon, no filesystem, so all
of this is provable on a machine with Docker uninstalled (which is exactly the
machine this was written on).

The negatives matter as much as the positives. A test that only checked for
the flags we DO pass would still pass if somebody added `--privileged`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.code import sandbox  # noqa: E402
from jarvis.integrations.code.sandbox import (  # noqa: E402
    DEFAULT_IMAGE,
    MAX_COMMAND_CHARS,
    NETWORK_EGRESS,
    NETWORK_NONE,
    WORK_DIR,
    Environment,
    SandboxError,
    Session,
    container_argv,
    environment_from_dict,
    setup_script,
)


@pytest.fixture(autouse=True)
def _not_root(monkeypatch):
    """Every test here assumes an ordinary user.

    `container_argv` REFUSES to build a command line when jarvis-core is root,
    because `--user=0:0` would be a container advertised as unprivileged and
    running as root with the repository bind-mounted. CI and many dev boxes run
    as root, so the tests say which user they are rather than inheriting one.
    The refusal itself is pinned by `test_running_as_root_is_refused`.
    """
    monkeypatch.setattr(sandbox, "_current_ids", lambda: (1000, 1000))


def env(**kw) -> Environment:
    return Environment(name=kw.pop("name", "build"), **kw)


def argv_for(environment: Environment, root="/srv/repo", command="pytest -q") -> list[str]:
    return container_argv(environment, Path(root), command, uid=1000, gid=1000)


def flags(argv: list[str]) -> set[str]:
    return {part.split("=", 1)[0] for part in argv if part.startswith("--")}


# ---------------------------------------------------------------------------
# the fences
# ---------------------------------------------------------------------------
def test_the_container_is_thrown_away():
    """Everything it installed dies with the job, or the next job inherits it."""
    assert "--rm" in argv_for(env())


def test_no_network_unless_the_operator_asked_for_it():
    assert "--network=none" in argv_for(env())
    assert "--network=bridge" not in argv_for(env())


def test_egress_is_available_when_asked_for_and_only_then():
    """The setting that makes `pip install` work, and the one worth reading twice."""
    assert "--network=bridge" in argv_for(env(network=NETWORK_EGRESS))
    assert "--network=none" not in argv_for(env(network=NETWORK_EGRESS))


def test_it_runs_as_the_invoking_user_not_as_root():
    """Two reasons, and the boring one bites first.

    Security: root in the container is a worse starting point for anything that
    escapes. Practical: files written through the bind mount are owned by
    whoever wrote them, and a container running as root leaves a repository
    full of files the operator cannot edit without sudo.
    """
    assert "--user=1000:1000" in argv_for(env())


def test_every_capability_is_dropped():
    assert "--cap-drop=ALL" in argv_for(env())


def test_privilege_cannot_be_raised_inside():
    """A setuid binary in the image must not be a way up."""
    assert "--security-opt=no-new-privileges" in argv_for(env())


def test_a_fork_bomb_hits_a_wall():
    assert "--pids-limit=512" in argv_for(env())
    assert "--pids-limit=64" in argv_for(env(pids=64))


def test_memory_and_cpu_are_bounded():
    argv = argv_for(env(memory="512m", cpus="1.5"))
    assert "--memory=512m" in argv
    assert "--cpus=1.5" in argv


def test_there_is_somewhere_to_write_that_is_not_the_repo():
    """A build writes temporary files; without this it writes them into the
    repository and they turn up in the diff."""
    tmpfs = [p for p in argv_for(env()) if p.startswith("--mount=")]
    assert tmpfs and "destination=/tmp" in tmpfs[0]
    assert "tmpfs-size=" in tmpfs[0], "an unbounded tmpfs is a way to fill host RAM"


def test_exactly_one_host_path_is_mounted_and_it_is_the_repository():
    """The single most important line in the file.

    Not the workspace root, not the parent, not the config directory — one
    repository. A job that could see its neighbours could read every other
    project on the machine.
    """
    mounts = [p for p in argv_for(env(), root="/srv/work/myrepo") if p.startswith("--volume=")]
    assert mounts == [f"--volume=/srv/work/myrepo:{WORK_DIR}"]


def test_the_working_directory_is_the_mounted_repository():
    assert f"--workdir={WORK_DIR}" in argv_for(env())


def test_the_container_cannot_wait_on_a_terminal():
    assert "--interactive=false" in argv_for(env())


# ---------------------------------------------------------------------------
# the negatives — what must NEVER be there
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "forbidden",
    [
        "--privileged",
        "--network=host",
        "--pid=host",
        "--ipc=host",
        "--userns=host",
        "--cap-add",
        "--device",
        "--security-opt=seccomp=unconfined",
        "--security-opt=apparmor=unconfined",
    ],
)
def test_a_dangerous_flag_is_never_passed(forbidden: str):
    for environment in (env(), env(network=NETWORK_EGRESS), env(pids=8192)):
        argv = argv_for(environment)
        assert forbidden not in argv
        assert not any(part.startswith(forbidden + "=") for part in argv)


def test_the_docker_socket_is_never_mounted():
    """A container that can reach the daemon can start another one that mounts
    the host root. It is the standard escape and it is one bind mount away."""
    for environment in (env(), env(network=NETWORK_EGRESS)):
        joined = " ".join(argv_for(environment))
        assert "docker.sock" not in joined
        assert "/var/run" not in joined


def test_the_hosts_environment_is_not_forwarded(monkeypatch):
    """`--env FOO` without a value forwards the host's FOO. A coding job must
    not inherit the operator's shell — that is where the API keys are."""
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    argv = argv_for(env())
    assert not any(p == "--env=SECRET_TOKEN" for p in argv)
    assert "hunter2" not in " ".join(argv)
    # Bare `--env=NAME` is the forwarding form; every one we emit has a value.
    for part in argv:
        if part.startswith("--env="):
            assert "=" in part[len("--env=") :], f"{part} forwards a host variable"


def test_declared_variables_are_passed_and_nothing_else():
    argv = argv_for(env(env={"CI": "1", "PIP_INDEX_URL": "https://pypi.internal/simple"}))
    assert "--env=CI=1" in argv
    assert "--env=PIP_INDEX_URL=https://pypi.internal/simple" in argv


# ---------------------------------------------------------------------------
# the command itself
# ---------------------------------------------------------------------------
def test_the_shell_is_inside_the_container_not_on_the_host():
    """The whole design in one assertion.

    The command reaches `/bin/sh -c` as a single argument to `docker run`, so
    nothing in it is interpreted by this process. `; rm -rf /` is a string that
    a shell in a throwaway container sees, not one the host does.
    """
    argv = argv_for(env(), command="pytest -q; rm -rf /")
    assert argv[-3:] == ["/bin/sh", "-c", "pytest -q; rm -rf /"]
    assert argv[0] == "docker"


def test_the_image_is_the_last_thing_before_the_command():
    """Argument order is load-bearing: anything after the image is the
    container's command, so a flag that landed there would be silently passed
    to `sh` instead of to `docker`."""
    argv = argv_for(env(image="node:22"))
    assert argv[-4] == "node:22"


def test_an_empty_command_is_refused():
    with pytest.raises(SandboxError):
        argv_for(env(), command="   ")


def test_an_enormous_command_is_refused_rather_than_truncated():
    """Truncating a command changes what runs. The last thing to fall off the
    end is routinely the thing that BOUNDED it — a `| head`, a closing quote."""
    with pytest.raises(SandboxError) as caught:
        argv_for(env(), command="x" * (MAX_COMMAND_CHARS + 1))
    assert str(MAX_COMMAND_CHARS) in str(caught.value)


def test_the_repository_path_is_resolved_before_it_is_mounted(tmp_path):
    """`~` and `..` in a configured path must not reach the docker argv."""
    (tmp_path / "real").mkdir()
    link = tmp_path / "sub"
    link.mkdir()
    argv = container_argv(env(), tmp_path / "sub" / ".." / "real", "ls", uid=1, gid=1)
    mount = [p for p in argv if p.startswith("--volume=")][0]
    assert ".." not in mount
    assert str((tmp_path / "real").resolve()) in mount


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def test_an_environment_needs_a_usable_name():
    assert environment_from_dict({"name": "build"}) is not None
    assert environment_from_dict({"name": ""}) is None
    assert environment_from_dict({"name": "../etc"}) is None
    assert environment_from_dict({"name": "a b"}) is None
    assert environment_from_dict("nonsense") is None


def test_an_image_with_a_shell_character_is_refused_not_sanitised():
    """It reaches an exec argv, not a shell, so this is belt and braces — but a
    typo in an image name should be a refusal rather than a container built
    from something the operator did not write."""
    assert environment_from_dict({"name": "a", "image": "python:3.12; rm -rf /"}) is None
    assert environment_from_dict({"name": "a", "image": "py thon"}) is None
    assert environment_from_dict({"name": "a", "image": "ghcr.io/x/y@sha256:abc"}) is not None


def test_the_default_environment_has_no_network():
    """The default has to be the safe one: an operator who did not think about
    it gets the version that cannot post their repository anywhere."""
    assert environment_from_dict({"name": "a"}).network == NETWORK_NONE
    assert environment_from_dict({"name": "a"}).networked is False
    assert environment_from_dict({"name": "a"}).image == DEFAULT_IMAGE


def test_an_unknown_network_policy_is_refused_rather_than_defaulted():
    """Silently falling back would give the operator an environment they did
    not ask for while looking as though the setting worked."""
    assert environment_from_dict({"name": "a", "network": "hostile"}) is None
    assert environment_from_dict({"name": "a", "network": "host"}) is None


def test_the_friendly_spellings_of_egress_are_accepted():
    for spelling in ("egress", "internet", "online", "yes", "true"):
        built = environment_from_dict({"name": "a", "network": spelling})
        assert built is not None and built.networked, spelling


def test_limits_are_clamped_whatever_the_file_says():
    huge = environment_from_dict({"name": "a", "pids": 10**9, "timeout": 10**9})
    assert huge.pids <= 8192
    assert huge.timeout <= 3600
    silly = environment_from_dict({"name": "a", "pids": "banana", "timeout": "soon"})
    assert silly.pids > 0 and silly.timeout > 0


def test_a_variable_with_an_impossible_name_is_dropped():
    built = environment_from_dict({"name": "a", "env": {"OK": "1", "not ok": "2", "": "3"}})
    assert set(built.env) == {"OK"}


def test_the_listing_never_carries_a_variables_value():
    """`PIP_INDEX_URL` can hold a credential. The console needs to know the
    variable is set, never what it is set to."""
    built = env(env={"PIP_INDEX_URL": "https://user:pass@index.internal"})
    listed = built.as_dict()
    assert listed["env"] == ["PIP_INDEX_URL"]
    assert "pass" not in repr(listed)


def test_the_description_does_not_undersell_what_egress_reaches():
    """"the internet" was the wrong word.

    On Docker's default bridge the host is reachable at the gateway address and
    so is the rest of the LAN — the router, a NAS, jarvis-core's own API. An
    operator choosing this setting is owed the accurate sentence.
    """
    assert "no network" in env().describe()
    said = env(network=NETWORK_EGRESS).describe()
    assert "LAN" in said


def test_an_operator_can_name_their_own_docker_network():
    """The only way to give a job the internet without the LAN: Docker cannot
    express that itself, and pretending otherwise would be the lie."""
    argv = argv_for(env(network=NETWORK_EGRESS, network_name="jarvis-egress"))
    assert "--network=jarvis-egress" in argv
    assert "--network=bridge" not in argv


def test_running_as_root_is_refused(monkeypatch):
    """Failing OPEN would be the worst of both worlds.

    A bare-metal install often runs jarvis as root, because that is how it
    reaches the docker socket without adding a user to the `docker` group. That
    is a real configuration, so it gets a refusal naming both fixes rather than
    a silent `--user=0:0`.
    """
    monkeypatch.setattr(sandbox, "_current_ids", lambda: (0, 0))
    with pytest.raises(SandboxError) as caught:
        container_argv(env(), Path("/srv/repo"), "ls")
    assert "root" in str(caught.value)
    assert "rootless" in str(caught.value)


def test_a_read_only_repository_is_mounted_read_only():
    """`writable: false` withholds the EDIT tools; without this it would mean
    nothing at all to a shell, which can write whatever it likes."""
    argv = container_argv(
        env(), Path("/srv/repo"), "ls", uid=1000, gid=1000, writable=False
    )
    assert f"--volume=/srv/repo:{WORK_DIR}:ro" in argv
    writable = container_argv(env(), Path("/srv/repo"), "ls", uid=1000, gid=1000)
    assert f"--volume=/srv/repo:{WORK_DIR}" in writable


def test_the_container_is_named_so_a_timeout_can_kill_it():
    """`proc.kill()` kills the docker CLIENT. The daemon keeps the container,
    and `--rm` only fires when one exits — which `sleep 999999` never does."""
    argv = container_argv(env(), Path("/x"), "ls", uid=1, gid=1, name="jarvis-code-abc")
    assert "--name=jarvis-code-abc" in argv


def test_setup_commands_stop_at_the_first_failure():
    """Without `set -e` a failed install leaves a half-built container that
    fails confusingly three commands later."""
    script = setup_script(env(setup=["apt-get update", "apt-get install -y libpq-dev"]))
    assert script.startswith("set -e")
    assert "libpq-dev" in script
    assert setup_script(env()) == ""


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------
async def test_running_passes_the_built_argv_through_untouched():
    """Through `Session`, because that is the only way to run anything now.

    There used to be a second entry point — `run_in_container`, one container
    per command — kept alive by these tests alone after `Session` replaced it.
    A dormant execution path carrying its own copy of the fences is a thing
    somebody re-wires later without re-reading them, so it was deleted and
    what it proved moved here.
    """
    seen: list[list[str]] = []

    async def _runner(argv, timeout, **kwargs):
        seen.append(argv)
        return 0, "ok"

    session = Session(env(), Path("/srv/repo"), runner=_runner)
    code, out = await session.run("pytest -q")
    assert (code, out) == (0, "ok")
    assert seen[0][0] == "docker"  # the `docker run` that made the container
    assert seen[-1][0] == "docker" and seen[-1][1] == "exec"
    assert seen[-1][-3:-1] == ["/bin/sh", "-c"]
    assert seen[-1][-1].endswith("pytest -q")


async def test_the_environments_timeout_is_used_unless_overridden():
    seen: list[float] = []

    async def _runner(argv, timeout, **kwargs):
        if argv[1] == "exec":
            seen.append(timeout)
        return 0, ""

    session = Session(env(timeout=42), Path("/x"), runner=_runner)
    await session.run("ls")
    await session.run("ls", timeout=5)
    assert seen == [42, 5]


async def test_a_missing_docker_says_what_to_do_rather_than_raising():
    """An operator who set `environment:` without installing Docker gets a
    sentence naming both fixes, not a traceback in a log they never read."""
    session = Session(env(), Path("/x"), docker="definitely-not-a-real-binary-xyz")
    code, out = await session.run("ls")
    assert code == 1
    assert "docker is not installed" in out
    assert "environment:" in out


def test_there_is_one_way_to_run_a_command_in_a_container():
    """No second entry point, so there is one place the fences have to be."""
    from jarvis.integrations.code import sandbox as module

    entry = [
        name
        for name in dir(module)
        if name.endswith("_in_container") and not name.startswith("_")
    ]
    assert entry == [], f"a second execution path came back: {entry}"


# ---------------------------------------------------------------------------
# the session: one container per JOB, so installing works at all
# ---------------------------------------------------------------------------
class _FakeDocker:
    """Records argv and answers whatever the test queued."""

    def __init__(self, *, image_exists: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.image_exists = image_exists
        self.results: dict[str, tuple[int, str]] = {}

    async def __call__(self, argv, timeout, *, docker="docker", name=""):
        self.calls.append(argv)
        verb = argv[1] if len(argv) > 1 else ""
        if verb == "image" and argv[2] == "inspect":
            return (0, "") if self.image_exists else (1, "no such image")
        return self.results.get(verb, (0, "ok"))

    def argv_for(self, verb: str) -> list[list[str]]:
        return [c for c in self.calls if len(c) > 1 and c[1] == verb]


@pytest.mark.asyncio
async def test_every_command_runs_in_the_same_container():
    """THE bug this replaced.

    The first version did `docker run --rm` per command, so
    `run_command("pip install pygame")` installed into a container that was
    removed before the next line could import it. Installing was, in effect,
    impossible — which is the opposite of what an environment is for.
    """
    docker = _FakeDocker()
    session = sandbox.Session(env(), Path("/srv/repo"), runner=docker)

    await session.run("pip install pygame")
    await session.run("python -c 'import pygame'")

    assert len(docker.argv_for("run")) == 1, "a second container was created"
    execs = docker.argv_for("exec")
    assert len(execs) == 2
    # Both reached the SAME container.
    assert execs[0][-4] == execs[1][-4] == session.name


@pytest.mark.asyncio
async def test_the_container_idles_rather_than_exiting():
    """A container whose command exits is a container `--rm` removes."""
    docker = _FakeDocker()
    session = sandbox.Session(env(), Path("/x"), runner=docker)
    await session.start()

    run = docker.argv_for("run")[0]
    assert "--detach" in run
    # `sleep infinity` is GNU-only; busybox refuses it.
    assert run[-1] == "tail -f /dev/null"


@pytest.mark.asyncio
async def test_the_fences_are_on_the_container_not_on_each_exec():
    """`docker exec` cannot widen what the container was created with, which is
    why the network policy and the mounts are decided once."""
    docker = _FakeDocker()
    session = sandbox.Session(env(network=NETWORK_EGRESS), Path("/srv/repo"), runner=docker)
    await session.run("whoami")

    run = docker.argv_for("run")[0]
    assert "--cap-drop=ALL" in run
    assert "--network=bridge" in run
    assert any(p.startswith("--volume=/srv/repo:") for p in run)

    execute = docker.argv_for("exec")[0]
    assert not any(p.startswith("--network") for p in execute)
    assert not any(p.startswith("--volume") for p in execute)
    assert not any(p.startswith("--cap-") for p in execute)


@pytest.mark.asyncio
async def test_setup_runs_once_and_inside_the_job_container():
    """An operator's `setup:` used to run in a container of its own and be
    thrown away, so `apt-get install libglfw3-dev` reached nothing."""
    docker = _FakeDocker()
    session = sandbox.Session(env(setup=["apt-get install -y cmake"]), Path("/x"), runner=docker)

    await session.run_setup()
    await session.run_setup()
    await session.run("cmake --version")

    execs = docker.argv_for("exec")
    setups = [c for c in execs if "cmake" in c[-1] and "apt-get" in c[-1]]
    assert len(setups) == 1, "setup ran more than once, or not at all"
    assert len(docker.argv_for("run")) == 1


@pytest.mark.asyncio
async def test_the_container_is_removed_when_the_job_ends():
    docker = _FakeDocker()
    session = sandbox.Session(env(), Path("/x"), runner=docker)
    await session.run("ls")
    await session.close()
    removed = docker.argv_for("rm")
    assert removed and session.name in removed[0]


# ---------------------------------------------------------------------------
# persistence: the tools outlive the job, the container does not
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nothing_is_kept_unless_the_environment_asks():
    docker = _FakeDocker()
    session = sandbox.Session(env(), Path("/x"), runner=docker)
    await session.run("pip install pygame")
    await session.close()
    assert not docker.argv_for("commit"), "an environment kept state it did not opt into"


@pytest.mark.asyncio
async def test_a_persisting_environment_keeps_what_it_installed():
    """"if it closes a session, the tools are still there"."""
    docker = _FakeDocker()
    session = sandbox.Session(env(persist=True), Path("/x"), runner=docker)
    await session.run("apt-get install -y cmake")
    await session.close()

    committed = docker.argv_for("commit")
    assert committed, "the installed tools were thrown away"
    assert committed[0][-1] == "jarvis-code-env-build:latest"
    assert committed[0][-2] == session.name


@pytest.mark.asyncio
async def test_the_next_job_starts_from_the_kept_image():
    docker = _FakeDocker(image_exists=True)
    session = sandbox.Session(env(persist=True), Path("/x"), runner=docker)
    await session.start()
    run = docker.argv_for("run")[0]
    # The image argument sits immediately before the container's command.
    assert run[-4] == "jarvis-code-env-build:latest"


@pytest.mark.asyncio
async def test_with_no_kept_image_yet_it_starts_from_the_configured_one():
    docker = _FakeDocker(image_exists=False)
    session = sandbox.Session(env(persist=True, image="gcc:14"), Path("/x"), runner=docker)
    await session.start()
    assert docker.argv_for("run")[0][-4] == "gcc:14"


@pytest.mark.asyncio
async def test_the_configured_image_survives_a_persisted_run():
    """A reset has to have something to go back to.

    `start()` swaps the image for the persisted tag; mutating the Environment
    instead of copying it would make the configured image unrecoverable and
    `reset_environment` a no-op.
    """
    docker = _FakeDocker(image_exists=True)
    environment = env(persist=True, image="gcc:14")
    session = sandbox.Session(environment, Path("/x"), runner=docker)
    await session.start()
    assert environment.image == "gcc:14"


@pytest.mark.asyncio
async def test_a_failed_job_can_decline_to_keep_its_container():
    """A half-applied `apt-get` is a worse starting point than the image."""
    docker = _FakeDocker()
    session = sandbox.Session(env(persist=True), Path("/x"), runner=docker)
    await session.run("apt-get install -y nonsense")
    await session.close(keep=False)
    assert not docker.argv_for("commit")


@pytest.mark.asyncio
async def test_resetting_throws_the_kept_image_away():
    docker = _FakeDocker()
    ok, note = await sandbox.reset_environment(env(persist=True), runner=docker)
    assert ok
    removed = [c for c in docker.calls if c[1:3] == ["image", "rm"]]
    assert removed and removed[0][-1] == "jarvis-code-env-build:latest"
    assert "reinstall" in note


@pytest.mark.asyncio
async def test_resetting_an_environment_that_keeps_nothing_says_so():
    docker = _FakeDocker()
    ok, note = await sandbox.reset_environment(env(), runner=docker)
    assert not ok
    assert "does not keep anything" in note


@pytest.mark.asyncio
async def test_a_commit_failure_is_a_warning_not_a_lost_job(caplog):
    """The job's diff is the deliverable; failing to keep its tools is not
    worth losing that over."""
    import logging

    docker = _FakeDocker()
    docker.results["commit"] = (1, "no space left on device")
    session = sandbox.Session(env(persist=True), Path("/x"), runner=docker)
    await session.run("ls")
    with caplog.at_level(logging.WARNING):
        await session.close()
    assert any("could not keep" in r.getMessage() for r in caplog.records)
    assert docker.argv_for("rm"), "the container was left running"


def test_the_cache_volume_covers_downloads_and_not_installs():
    """A volume over `site-packages` would make installs persist by accident,
    which is what `persist:` is for and what `cache:` deliberately is not."""
    argv = argv_for(env(cache=True))
    volumes = [p for p in argv if p.startswith("--volume=")]
    cache_mounts = [v for v in volumes if "jarvis-code-cache-build" in v]
    assert cache_mounts
    for mount in cache_mounts:
        destination = mount.rsplit(":", 1)[-1]
        assert ".cache" in destination
        assert "site-packages" not in destination
        assert destination != WORK_DIR


def test_a_docker_that_is_not_running_says_which_of_the_two_things_is_wrong():
    from jarvis.integrations.code.sandbox import _explain_docker_failure

    assert "not running" in _explain_docker_failure("Cannot connect to the Docker daemon")
    assert "docker` group" in _explain_docker_failure(
        "permission denied while trying to connect to /var/run/docker.sock"
    )


@pytest.mark.asyncio
async def test_the_workspace_reuses_one_session_across_commands():
    """The same property, one layer up.

    `Session` keeping one container is only useful if the Workspace hands out
    the same Session — a fresh one per command would create a fresh container
    per command and put the original bug straight back.
    """
    from jarvis.integrations.code.workspace import Repo, Workspace

    docker = _FakeDocker()
    ws = Workspace(
        Repo(name="p", path="/srv/repo", writable=True), environment=env()
    )
    session = await ws.open_session()
    session._run = docker  # the container is faked; the reuse is not

    await ws.run_sandboxed("pip install pygame")
    await ws.run_sandboxed("python -c 'import pygame'")

    assert len(docker.argv_for("run")) == 1
    assert len(docker.argv_for("exec")) == 2


@pytest.mark.asyncio
async def test_closing_the_workspace_session_lets_the_next_job_start_clean():
    from jarvis.integrations.code.workspace import Repo, Workspace

    docker = _FakeDocker()
    ws = Workspace(Repo(name="p", path="/srv/repo"), environment=env())
    session = await ws.open_session()
    session._run = docker
    await ws.run_sandboxed("ls")
    await ws.close_session()

    assert docker.argv_for("rm")
    assert ws._session is None, "a closed session was left attached"


def test_the_size_of_a_written_file_is_bounded():
    """The bind mount is the host's filesystem, and nothing else caps it.

    `--memory`, `--pids-limit` and the 512 MB tmpfs all leave `/work` alone,
    so `dd if=/dev/zero of=/work/pad` filled the operator's disk. Docker cannot
    quota a bind mount, so this is `--ulimit fsize`, which the kernel enforces
    per file on every driver.
    """
    argv = argv_for(env(max_file_mb=100))
    # BYTES. docker passes `--ulimit` values to `setrlimit(2)` unscaled, and
    # RLIMIT_FSIZE is in bytes; this used to assert 512-byte blocks, which
    # made a 100 MB environment a 200 KiB one and agreed with the code because
    # both were wrong the same way.
    assert "--ulimit=fsize=104857600" in argv
    assert "--ulimit=nofile=4096:4096" in argv


def test_the_file_size_limit_reaches_commands_and_not_only_the_container():
    """The regression that came with sessions.

    `ulimit` values are per-process, set on the container's INIT process.
    `docker exec` spawns a new one from the daemon, which inherits the
    daemon's limits, not the container's — so `--ulimit=fsize` stopped
    applying to every command a job ran the moment commands moved from `run`
    to `exec`. `docker exec` has no `--ulimit`, so the shell re-applies it.
    """
    from jarvis.integrations.code.sandbox import exec_argv

    argv = exec_argv("box", "dd if=/dev/zero of=/work/pad", max_file_mb=100)
    script = argv[-1]
    # 512-byte blocks here, bytes on the flag above. Different units, and
    # using one for the other is a fence off by a factor of 512.
    assert script.startswith("ulimit -f 204800 2>/dev/null; ")
    assert "ulimit -n 4096 2>/dev/null; " in script
    assert script.endswith("dd if=/dev/zero of=/work/pad")


async def test_a_session_command_carries_the_file_size_limit():
    """Wired, not merely available: the argument has to be passed."""
    seen: list[list[str]] = []

    async def _spy(argv, timeout, **kwargs):
        seen.append(argv)
        return 0, ""

    session = Session(env(max_file_mb=7), Path("/tmp/x"), runner=_spy)
    session.started = True
    await session.run("make")
    assert seen and seen[0][-1].startswith(f"ulimit -f {7 * 2048} ")


def test_the_command_length_limit_is_measured_before_the_prologue():
    """Otherwise the ceiling moves whenever the prologue's wording changes."""
    from jarvis.integrations.code.sandbox import MAX_COMMAND_CHARS, exec_argv

    longest = "x" * MAX_COMMAND_CHARS
    argv = exec_argv("box", longest, max_file_mb=1)
    assert argv[-1].endswith(longest)
    with pytest.raises(SandboxError):
        exec_argv("box", "x" * (MAX_COMMAND_CHARS + 1), max_file_mb=1)


def test_the_file_size_limit_is_clamped_and_survives_nonsense():
    from jarvis.integrations.code.sandbox import environment_from_dict

    assert environment_from_dict({"name": "a"}).max_file_mb == 2048
    assert environment_from_dict({"name": "a", "max_file_mb": "banana"}).max_file_mb > 0
    assert environment_from_dict({"name": "a", "max_file_mb": 0}).max_file_mb >= 1
