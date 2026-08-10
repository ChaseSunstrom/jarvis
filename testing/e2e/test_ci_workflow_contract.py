"""The workflow files themselves, checked against how GitHub actually runs them.

Why this lives beside the end-to-end tests
------------------------------------------
Every other test in this directory boots a real jarvis-core. This one boots
nothing — it is here because it guards the *same* thing: whether the end-to-end
jobs in ``.github/workflows/e2e.yml`` can run at all. A broken shell block in
that file is not caught by any suite, is not caught by ``actionlint``, is not
caught by ``bash -n``, and is not caught by ``sh -n`` — it is only caught in CI,
tens of minutes into an emulator boot, as an unexplained exit code. It IS caught
here, in milliseconds, by the job that runs first.

The bug that made this file exist
---------------------------------
Run 31303989376's emulator step died in 29 seconds, before Gradle::

    [command]/usr/bin/sh -c set -uo pipefail
    /usr/bin/sh: 1: set: Illegal option -o pipefail
    ##[error]The process '/usr/bin/sh' failed with exit code 2

``/bin/sh`` on ``ubuntu-latest`` is **dash**, and dash rejects
``set -o pipefail``. ``set`` is a POSIX *special builtin*, so the error killed
the non-interactive shell outright: no Gradle, no instrumented tests, no
screenshots, no logcat, and a red job with nothing in it to read. Neither
``bash -n`` nor ``sh -n`` sees it, because it is a runtime error in a builtin
and not a syntax error.

Making that line POSIX would NOT have fixed the job, and this is the part worth
remembering. Look again at what the runner echoed: **one line**, not the block.
``reactivecircus/android-emulator-runner`` does not hand ``script:`` to a shell
as one program. ``src/script-parser.ts`` splits the input on
``/\r\n|\n|\r/``, trims each piece and drops blank and ``#``-comment lines;
``src/main.ts`` then runs the pieces one at a time::

    for (const script of scripts) {
      await exec.exec('sh', ['-c', script], { env: ... });
    }

Every line gets its own shell. An assignment is gone by the next line, a ``set``
applies to nothing after it, a backslash continuation is severed, and a
multi-line ``if ... fi`` becomes fragments that are each a syntax error alone —
and the first non-zero exit makes ``exec.exec`` throw, failing the step. A
"fixed" POSIX version of that block would simply have died a few lines lower, at
``if adb reverse ...; then``.

So the contract this module enforces for ``script:`` is not "keep it POSIX". It
is **one line, invoking one file**. The file is then a normal bash script,
checked here with ``bash -n``.

The two shells, and which blocks get which
------------------------------------------
* ``run:`` steps      -> GitHub runs ``bash -e {0}``. Bash, and errexit is ON
  even though nothing in the YAML says so.
* ``script:`` inputs of ``android-emulator-runner`` -> one ``sh -c`` PER LINE.

Mixing the two up in either direction is the failure this module exists to
prevent, so each block is checked against the shell that will really run it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator, NamedTuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: The action whose `script:` input is handed to `sh -c` rather than to bash.
SH_SCRIPT_ACTION = "reactivecircus/android-emulator-runner"

#: Constructs that bash accepts and dash does not. Each would be a runtime or
#: syntax failure inside a `script:` block, and several are invisible to
#: `sh -n`. The pattern is matched against the block with comments stripped.
BASHISMS: tuple[tuple[str, str], ...] = (
    (r"\bset\s+[-+][A-Za-z]*o\s+pipefail\b", "`pipefail` is not a POSIX sh option"),
    (r"(?<![\[\w])\[\[", "`[[ ]]` is a bash keyword; use `[ ]`"),
    (r"<<<", "here-strings (`<<<`) are bash-only"),
    (r"<\(", "process substitution (`<(...)`) is bash-only"),
    (r"^\s*\w+=\(", "arrays are bash-only"),
    (r"^\s*local\s+", "`local` is not in POSIX sh"),
    (r"^\s*function\s+\w+", "the `function` keyword is bash-only"),
    (r"\$\{\w+\[[^\]]*\]\}", "array subscripts are bash-only"),
    (r"^\s*source\s+", "`source` is bash-only; use `.`"),
    (r"\becho\s+-e\b", "`echo -e` is not portable; use printf"),
)

COMMENT = re.compile(r"(?m)^\s*#.*$")


class Block(NamedTuple):
    """One shell block from a workflow, and the shell that will run it."""

    workflow: str
    job: str
    step: str
    kind: str  # "run" | "script"
    shell: str  # "bash" | "sh"
    body: str

    def __str__(self) -> str:  # pragma: no cover - identifiers in test ids
        return f"{self.workflow}:{self.job}:{self.step} ({self.kind} -> {self.shell})"


def _strict_load(path: Path) -> Any:
    """Parse a workflow, refusing duplicate mapping keys.

    A duplicate key is legal YAML — last wins — but GitHub rejects the whole
    workflow, which then runs zero jobs and reports as a mysterious failure.
    """

    class StrictLoader(yaml.SafeLoader):
        pass

    def no_duplicates(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_duplicates
    )
    return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def _blocks() -> Iterator[Block]:
    """Every inline shell block in every workflow, tagged with its real shell."""
    for path in _workflows():
        doc = _strict_load(path)
        for job_name, job in (doc.get("jobs") or {}).items():
            # A `defaults.run.shell` would change the answer for `run:` blocks;
            # none of ours sets one, and test_no_job_overrides_the_run_shell
            # below keeps it that way.
            for index, step in enumerate(job.get("steps") or []):
                label = step.get("name") or step.get("uses") or f"step {index}"
                if "run" in step:
                    yield Block(path.name, job_name, label, "run", "bash", step["run"])
                    continue
                with_ = step.get("with") or {}
                if "script" in with_ and SH_SCRIPT_ACTION in str(step.get("uses", "")):
                    yield Block(
                        path.name, job_name, label, "script", "sh", str(with_["script"])
                    )


ALL_BLOCKS = list(_blocks())
SH_BLOCKS = [b for b in ALL_BLOCKS if b.shell == "sh"]
BASH_BLOCKS = [b for b in ALL_BLOCKS if b.shell == "bash"]


def _ids(blocks: list[Block]) -> list[str]:
    return [f"{b.workflow}::{b.job}::{b.step}" for b in blocks]


def _set_lines(body: str) -> list[str]:
    """Every `set` command in a block, comments and continuations ignored."""
    return [
        line.strip()
        for line in body.splitlines()
        if re.match(r"^\s*set\s+[-+]", line)
    ]


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------
def test_there_are_shell_blocks_to_check():
    """Guard against the extractor silently matching nothing.

    Every assertion below is parameterised over a list. If the list were empty
    — a renamed action, a restructured workflow — the whole module would go
    green while checking nothing at all.
    """
    assert ALL_BLOCKS, "no shell blocks were extracted from .github/workflows"
    assert SH_BLOCKS, (
        "no `sh`-executed blocks found. e2e.yml drives "
        f"{SH_SCRIPT_ACTION}, whose `script:` input runs under `sh -c`; if that "
        "is genuinely gone, delete these checks deliberately rather than "
        "letting them pass vacuously."
    )
    assert len(BASH_BLOCKS) >= 5


#: `bash "$GITHUB_WORKSPACE/some/file.sh"` -> the repo-relative path it names.
_RUNS_A_FILE = re.compile(
    r"""(?:^|\s)(?:ba)?sh\s+["']?(?:\$\{?GITHUB_WORKSPACE\}?/|\./)?([\w./-]+\.sh)["']?"""
)


def _script_commands(body: str) -> list[str]:
    """The pieces android-emulator-runner will actually run, one shell each.

    Mirrors `src/script-parser.ts`: split on any newline, trim, drop blanks and
    `#`-comments.
    """
    return [
        line.strip()
        for line in re.split(r"\r\n|\n|\r", body)
        if line.strip() and not line.strip().startswith("#")
    ]


@pytest.mark.parametrize("block", SH_BLOCKS, ids=_ids(SH_BLOCKS))
def test_a_script_input_is_a_single_command(block: Block):
    """THE check. `script:` must be ONE line.

    android-emulator-runner runs the input one line per shell (see this
    module's docstring). A multi-line block is therefore not a script at all —
    it is N unrelated programs, and any of them that is only a fragment of a
    larger construct is a syntax error that kills the step.

    Anything with real logic in it belongs in a file, invoked from the single
    line this check allows.
    """
    commands = _script_commands(block.body)
    assert len(commands) == 1, (
        f"{block} is {len(commands)} lines:\n    "
        + "\n    ".join(commands)
        + "\n\nandroid-emulator-runner runs each of those in its OWN "
        "`sh -c`, so variables do not survive between them and any multi-line "
        "`if`/loop/backslash-continuation is severed into fragments. Move the "
        "body into a checked-in .sh file and make this input one line that "
        "runs it."
    )


@pytest.mark.parametrize("block", SH_BLOCKS, ids=_ids(SH_BLOCKS))
def test_a_script_input_that_runs_a_file_names_one_that_exists_and_parses(block: Block):
    """The single line is only as good as the file it points at.

    A typo'd path fails at minute forty of an emulator boot with `No such file
    or directory`, which is a very expensive way to find out.
    """
    match = _RUNS_A_FILE.search(block.body)
    if not match:
        return
    target = REPO_ROOT / match.group(1)
    assert target.is_file(), f"{block} runs {match.group(1)}, which does not exist"
    result = subprocess.run(
        ["bash", "-n", str(target)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"{block} runs {match.group(1)}, which bash will not parse: "
        f"{result.stderr.strip()}"
    )


@pytest.mark.parametrize("block", SH_BLOCKS, ids=_ids(SH_BLOCKS))
def test_a_sh_block_set_line_is_accepted_by_the_real_sh(block: Block):
    """THE check. Run the block's `set` lines under /bin/sh and require success.

    `set -o pipefail` parses fine and then fails at run time, so this has to be
    an execution and not a parse. `set` is a special builtin: a failure here
    kills a non-interactive shell outright, taking the rest of the script with
    it, which is why an unsupported option is not a warning but a dead job.
    """
    lines = _set_lines(block.body)
    if not lines:
        # A one-liner needs no error-handling preamble; anything longer does,
        # because `sh -c` starts with neither -e nor -u and an unset variable
        # would otherwise expand to nothing and carry on.
        commands = [
            line
            for line in block.body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert len(commands) <= 1, (
            f"{block} is {len(commands)} commands long and states no error "
            "handling. `sh -c` starts with neither -e nor -u, so add an "
            "explicit `set` line."
        )
        return
    for line in lines:
        result = subprocess.run(
            ["/bin/sh", "-c", f"{line}\nexit 0\n"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{block}: `{line}` is rejected by this machine's /bin/sh "
            f"({shutil.which('sh')}): {result.stderr.strip()!r}.\n"
            "This block is executed by android-emulator-runner as "
            "`sh -c <script>`, and /bin/sh on ubuntu-latest is dash. A `set` "
            "that dash refuses terminates the script before its first real "
            "command, so the job fails with no Gradle run, no screenshots and "
            "no logcat. Keep it POSIX."
        )


@pytest.mark.parametrize("block", SH_BLOCKS, ids=_ids(SH_BLOCKS))
def test_a_sh_block_parses_under_sh(block: Block):
    """Syntax, as a second net under the `set` execution check."""
    result = subprocess.run(
        ["/bin/sh", "-n"], input=block.body, capture_output=True, text=True
    )
    assert result.returncode == 0, f"{block}: {result.stderr.strip()}"


@pytest.mark.parametrize("block", SH_BLOCKS, ids=_ids(SH_BLOCKS))
def test_a_sh_block_contains_no_bashisms(block: Block):
    """Catch the bashisms `sh -n` accepts, which is most of the dangerous ones.

    `[[ 1 == 1 ]]` parses cleanly under dash (it reads `[[` as a command name)
    and only fails when it runs, halfway through a job.
    """
    stripped = COMMENT.sub("", block.body)
    found = [
        f"{why} (matched {pattern!r})"
        for pattern, why in BASHISMS
        if re.search(pattern, stripped, re.MULTILINE)
    ]
    assert not found, (
        f"{block} runs under `sh -c`, not bash:\n  " + "\n  ".join(found)
    )


@pytest.mark.parametrize("block", BASH_BLOCKS, ids=_ids(BASH_BLOCKS))
def test_a_run_block_parses_under_bash(block: Block):
    result = subprocess.run(
        ["bash", "-n"], input=block.body, capture_output=True, text=True
    )
    assert result.returncode == 0, f"{block}: {result.stderr.strip()}"


@pytest.mark.parametrize("block", BASH_BLOCKS, ids=_ids(BASH_BLOCKS))
def test_a_run_block_that_reads_the_exit_status_turned_errexit_off(block: Block):
    """`status=$?` is unreachable under errexit, and GitHub sets errexit.

    GitHub runs a `run:` block as `bash -e {0}`. `set -uo pipefail` does NOT
    clear the `-e` the wrapper applied, so::

        python -m pytest ...     # fails
        status=$?                # never runs — the shell already exited
        if [ "$status" != 0 ]; then ...dump the logs... fi

    is a diagnostic block that has never executed. The step still goes red, so
    the mistake is invisible; what is lost is the reason. Either turn errexit
    off (`set +e`) or capture inline (`cmd || status=$?`).
    """
    stripped = COMMENT.sub("", block.body)
    bare_capture = re.search(r"(?m)^\s*(\w+)=\$\?\s*$", stripped)
    if not bare_capture:
        return
    assert re.search(r"(?m)^\s*set\s+\+e\b", stripped), (
        f"{block} captures `$?` into `{bare_capture.group(1)}` on a line of its "
        "own, but GitHub runs this block as `bash -e {0}` and nothing here "
        "clears errexit — the shell exits at the failing command and that "
        "capture, plus everything guarded by it, never runs. Add `set +e` "
        "(and check each command by hand), or capture inline with "
        "`cmd || " + bare_capture.group(1) + "=$?`."
    )


@pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
def test_a_workflow_has_no_duplicate_keys_and_declares_jobs_and_a_trigger(path: Path):
    """Duplicate keys make GitHub reject a workflow outright."""
    doc = _strict_load(path)  # raises on a duplicate
    assert doc, f"{path.name} is empty"
    assert doc.get("jobs"), f"{path.name} has no jobs"
    # `on` parses as the boolean True under YAML 1.1.
    assert True in doc or "on" in doc, f"{path.name} has no trigger block"


def test_no_job_overrides_the_run_shell():
    """The errexit reasoning above assumes the default `bash -e {0}`.

    A `shell:` or `defaults.run.shell` somewhere would make it wrong without
    making it look wrong, so require that the assumption still holds rather
    than trusting it.
    """
    offenders = []
    for path in _workflows():
        doc = _strict_load(path)
        if ((doc.get("defaults") or {}).get("run") or {}).get("shell"):
            offenders.append(f"{path.name}: workflow-level defaults.run.shell")
        for job_name, job in (doc.get("jobs") or {}).items():
            if ((job.get("defaults") or {}).get("run") or {}).get("shell"):
                offenders.append(f"{path.name}:{job_name}: job-level defaults.run.shell")
            for index, step in enumerate(job.get("steps") or []):
                shell = step.get("shell")
                # `shell: bash` is the one permitted override, and it is
                # REQUIRED rather than tolerated: a matrix job that includes
                # windows-latest gets PowerShell by default, where `if [ -d x ]`
                # and `$(ls ...)` are syntax errors. Bash is a superset of the
                # sh these checks assume, so a block that is valid under the
                # default is still valid here — the assumptions this module
                # makes survive. Anything else (pwsh, cmd, python, sh) changes
                # the language the block is written in, and then the checks
                # below stop describing it.
                if shell is not None and shell != "bash":
                    offenders.append(
                        f"{path.name}:{job_name}: step {index} sets shell: "
                        f"{shell}"
                    )
    assert not offenders, (
        "these override the shell, so the checks in this module no longer "
        "describe how the block is run — update them together:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# the end-to-end workflow's own promises
# ---------------------------------------------------------------------------
def _e2e() -> dict[str, Any]:
    return _strict_load(WORKFLOW_DIR / "e2e.yml")


def _emulator_program() -> str:
    """What the emulator step really executes, `script:` line and file together.

    The step's own input is now a single line that runs a file, so a check that
    reads only the YAML would be asserting against `bash .../run-instrumented-
    e2e.sh` and passing vacuously. Both halves are concatenated here so the
    assertions below keep meaning what they say wherever the text lives.
    """
    steps = _e2e()["jobs"]["android"]["steps"]
    inputs = [
        str((step.get("with") or {}).get("script", ""))
        for step in steps
        if SH_SCRIPT_ACTION in str(step.get("uses", ""))
    ]
    parts: list[str] = []
    for body in inputs:
        parts.append(body)
        match = _RUNS_A_FILE.search(body)
        if match:
            target = REPO_ROOT / match.group(1)
            if target.is_file():
                parts.append(target.read_text(encoding="utf-8"))
    program = "\n".join(parts)
    assert "connectedDebugAndroidTest" in program, (
        "no emulator step runs `connectedDebugAndroidTest` any more — either "
        "the instrumented suite is no longer being run, or this helper is "
        "looking in the wrong place and every check below is vacuous"
    )
    return program


def test_the_instrumented_emulator_step_cold_boots():
    """`-no-snapshot-load`, or the job fails before a single test runs.

    Restoring the cached snapshot broke this job on every run once the AVD
    cache started hitting: `sys.boot_completed` is restored as part of the
    snapshot, so it reads `1` immediately and the emulator action's boot gate
    proves nothing. The unlock keyevent the action sends next then landed on a
    system_server that was still coming back, and the step died with
    `android.os.DeadSystemException` before `script` was reached.

    That unlock is inside the action, so no amount of care in our own script
    can defend against it. Refusing the snapshot is the only lever there is.
    """
    step = next(
        step
        for step in _e2e()["jobs"]["android"]["steps"]
        if step.get("id") == "emulator"
    )
    assert "android-emulator-runner" in str(step.get("uses", "")), (
        "the `emulator` step is no longer the emulator action, so this check "
        "is looking at the wrong thing"
    )
    options = str((step.get("with") or {}).get("emulator-options", ""))
    assert "-no-snapshot-load" in options, (
        "the instrumented emulator step must cold boot; restoring a snapshot "
        "makes the action's readiness check vacuous and the run dies with "
        "DeadSystemException before any test executes"
    )


def test_no_emulator_step_saves_a_snapshot_nothing_loads():
    """Every emulator step passes `-no-snapshot-save`.

    Once the instrumented step cold boots, a written snapshot is a gigabyte of
    disk and restore time buying nothing — on a job that has already run the
    runner out of space once.
    """
    for step in _e2e()["jobs"]["android"]["steps"]:
        if "android-emulator-runner" not in str(step.get("uses", "")):
            continue
        options = str((step.get("with") or {}).get("emulator-options", ""))
        assert "-no-snapshot-save" in options, (
            f"emulator step {step.get('name')!r} still writes a boot snapshot, "
            "but nothing loads one any more"
        )


def test_the_e2e_workflow_has_the_three_jobs_it_claims():
    jobs = _e2e()["jobs"]
    assert set(jobs) == {"harness", "android", "desktop"}, sorted(jobs)


def test_every_artifact_upload_runs_even_when_the_job_failed():
    """`if: always()` on every upload, or a red job uploads nothing.

    The default is `success()`. An upload step without an explicit condition
    is skipped on exactly the runs whose artifacts matter.
    """
    missing = []
    for job_name, job in _e2e()["jobs"].items():
        for index, step in enumerate(job.get("steps") or []):
            if "actions/upload-artifact" not in str(step.get("uses", "")):
                continue
            if str(step.get("if", "")).strip() != "always()":
                missing.append(
                    f"{job_name}: step {index} "
                    f"({step.get('name') or step.get('with', {}).get('name')}) "
                    f"has if: {step.get('if')!r}"
                )
    assert not missing, "upload steps that would be skipped on failure:\n  " + "\n  ".join(
        missing
    )


def test_the_android_job_stops_the_harness_before_it_uploads_its_logs():
    """Order matters: jarvis-core.log is still being written until it is killed.

    Zipping a file that another process is appending to puts a truncated log in
    the artifact, and that log is the first thing a human opens.
    """
    steps = _e2e()["jobs"]["android"]["steps"]
    names = [str(step.get("name") or step.get("uses") or "") for step in steps]
    stop = next(i for i, n in enumerate(names) if n == "Stop the harness")
    uploads = [
        i
        for i, step in enumerate(steps)
        if "actions/upload-artifact" in str(step.get("uses", ""))
        and "testing/artifacts" in str((step.get("with") or {}).get("path", ""))
    ]
    assert uploads, "the android job no longer uploads the harness work directory"
    assert all(stop < i for i in uploads), (
        f"'Stop the harness' is step {stop} but the harness artifacts are "
        f"uploaded at {uploads}; stop it first so the logs are complete."
    )


def test_the_emulator_job_forbids_a_silent_skip_when_the_harness_is_missing():
    """`jarvisRequireHarness=true` must be passed to the instrumented run.

    `support/Harness.required` defaults to true, but the flag is what stops a
    future `-e jarvisRequireHarness false` in the job from turning "the server
    was never started" into a green run with a skipped test.
    """
    script = _emulator_program()
    assert "jarvisRequireHarness=true" in script
    assert "jarvisHarnessUrl=" in script
    assert "jarvisHarnessToken=" in script


def test_the_harness_is_reachable_from_the_emulator_by_a_route_the_app_permits():
    """Both routes in the emulator script must be allowed by a shipped config.

    `adb reverse` -> 127.0.0.1 is in the SHIPPING network-security config;
    10.0.2.2 is in the debug-variant override. If either address were dropped
    from the XML the job would fail with a cleartext-blocked socket and a
    misleading "the transcript never rendered".
    """
    script = _emulator_program()
    app = REPO_ROOT / "android-app" / "app" / "src"
    shipping = (app / "main" / "res" / "xml" / "network_security_config.xml").read_text()
    debug = (app / "debug" / "res" / "xml" / "network_security_config.xml").read_text()

    assert "127.0.0.1" in script, "the adb reverse route is gone from the job"
    assert "<domain includeSubdomains=\"false\">127.0.0.1</domain>" in shipping, (
        "the emulator job reaches the harness over `adb reverse` at 127.0.0.1, "
        "which the SHIPPING network-security config must permit cleartext to"
    )
    assert "10.0.2.2" in script, "the host-alias fallback is gone from the job"
    assert "<domain includeSubdomains=\"false\">10.0.2.2</domain>" in debug, (
        "the emulator job falls back to 10.0.2.2 when `adb reverse` fails, so "
        "the DEBUG network-security config must permit cleartext to it"
    )
    assert "10.0.2.2" not in shipping, (
        "10.0.2.2 is a test-harness exemption and must not ship in the release "
        "network-security config"
    )


def test_the_harness_binds_every_interface_so_the_emulator_route_can_work():
    """`--host 0.0.0.0`, or 10.0.2.2 reaches a socket bound to loopback only."""
    start = next(
        str(step["run"])
        for step in _e2e()["jobs"]["android"]["steps"]
        if "harness.py" in str(step.get("run", ""))
    )
    assert "--host 0.0.0.0" in start


def test_the_emulator_script_is_handed_every_variable_it_refuses_to_run_without():
    """The env coupling between the harness step and the extracted file.

    Moving the body out of the YAML bought correctness and cost visibility: the
    file now reads `$JARVIS_HARNESS_PORT`, `$JARVIS_HARNESS_TOKEN` and
    `$API_LEVEL`, and nothing in the step it runs from mentions two of them.
    Rename the variable at either end and the failure arrives after an AVD
    boot, an APK build and an install — as `::error::...are unset`, forty
    minutes in.

    `PORT`/`TOKEN` come from `$GITHUB_ENV` (written by the harness step, which
    is a different step and therefore a different shell). `API_LEVEL` has to be
    on the emulator step's own `env:`, because the action passes `process.env`
    through and a `${{ }}` inside a checked-in .sh is just text.
    """
    steps = _e2e()["jobs"]["android"]["steps"]
    emulator = next(
        step for step in steps if SH_SCRIPT_ACTION in str(step.get("uses", ""))
        and "run-instrumented-e2e" in str((step.get("with") or {}).get("script", ""))
    )
    script_path = REPO_ROOT / _RUNS_A_FILE.search(
        str(emulator["with"]["script"])
    ).group(1)
    body = script_path.read_text(encoding="utf-8")

    # What the file insists on before it will do anything.
    required = sorted(set(re.findall(r'\$\{(JARVIS_HARNESS_[A-Z_]+)[:}]', body)))
    assert required, (
        f"{script_path.name} no longer reads any JARVIS_HARNESS_* variable; "
        "this check has stopped meaning anything"
    )

    exported = "".join(
        str(step["run"])
        for step in steps
        if "GITHUB_ENV" in str(step.get("run", ""))
    )
    assert exported, "no step writes to $GITHUB_ENV any more"
    for name in required:
        assert f"{name}=" in exported, (
            f"{script_path.name} requires ${name}, but no step in the android "
            f"job writes {name}= to $GITHUB_ENV. The script exits 1 with "
            "'::error::...are unset' — after the emulator has booted."
        )

    # API_LEVEL is a matrix value, so it can only arrive as step env.
    if "${API_LEVEL" in body:
        assert "API_LEVEL" in (emulator.get("env") or {}), (
            f"{script_path.name} reads $API_LEVEL, but the emulator step does "
            "not set it — the annotations would name artifact "
            "'android-e2e-reports-apiunknown', which does not exist."
        )


# ---------------------------------------------------------------------------
# the compose smoke job's own promises
# ---------------------------------------------------------------------------
COMPOSE_SMOKE = WORKFLOW_DIR / "compose-smoke.yml"


def _compose_smoke_job() -> dict[str, Any]:
    doc = _strict_load(COMPOSE_SMOKE)
    jobs = doc["jobs"]
    assert len(jobs) == 1, f"expected one job, found {sorted(jobs)}"
    job = next(iter(jobs.values()))
    job["_env"] = doc.get("env") or {}
    return job


def test_the_compose_smoke_job_accounts_for_every_service_in_the_stack():
    """Adding a service must be a decision, not a silent gap in coverage.

    The job makes this check itself, at run time, against
    ``docker compose config --services``. It is repeated here because that
    version only runs on a machine with Docker, half an hour into a job — and
    because the whole reason this workflow exists is that a compose file nobody
    executed drifted away from what a real install does.
    """
    env = _compose_smoke_job()["_env"]
    started = set(str(env["SMOKE_SERVICES"]).split())
    skipped = set(str(env["SKIPPED_SERVICES"]).split())
    assert started, "SMOKE_SERVICES is empty; the job would start nothing"

    compose = yaml.safe_load(
        (REPO_ROOT / "jarvis-core" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    declared = set(compose["services"])

    unaccounted = sorted(declared - started - skipped)
    assert not unaccounted, (
        "jarvis-core/docker-compose.yml declares services that compose-smoke.yml "
        f"neither starts nor explicitly skips: {unaccounted}. Add them to "
        "SMOKE_SERVICES, or to SKIPPED_SERVICES with a reason in the workflow's "
        "header."
    )
    phantom = sorted((started | skipped) - declared)
    assert not phantom, (
        f"compose-smoke.yml names services that no longer exist: {phantom}"
    )


def test_the_compose_smoke_job_keeps_the_assertions_it_exists_for():
    """The job is only worth its runner minutes while these survive.

    Every one of them is here because dropping it turns the job into an
    expensive way to prove that `docker compose up` exits 0 — which it does
    even when three containers are crash-looping behind it.
    """
    steps = _compose_smoke_job()["steps"]
    program = "\n".join(str(step.get("run", "")) for step in steps)

    assert "RestartCount" in program, (
        "nothing reads .RestartCount any more. `docker compose ps` reports a "
        "flapping container as 'Up 1 second' if you sample it during its up "
        "phase; the restart counter is what makes the check survive timing."
    )
    assert "/healthz" in program, "the API is no longer probed"
    assert "docker compose logs" in program, (
        "the log dump is gone, so a crash loop in CI would have to be "
        "reproduced locally to be understood"
    )

    dumps = [
        step for step in steps
        if "docker compose logs" in str(step.get("run", ""))
        and str(step.get("if", "")).strip() == "failure()"
    ]
    assert dumps, "no step dumps `docker compose logs` on failure()"

    teardown = [
        step for step in steps
        if "docker compose down" in str(step.get("run", ""))
    ]
    assert teardown, "the job never tears the stack down"
    for step in teardown:
        assert str(step.get("if", "")).strip() == "always()", (
            "teardown must be if: always(); the default is success(), which "
            "skips it on exactly the runs that left containers behind"
        )
        assert "--volumes" in str(step["run"]), (
            "tear down volumes too, or mosquitto-data survives into the next run"
        )


def test_the_compose_smoke_job_enables_the_profiles_the_bugs_lived_behind():
    """mosquitto is behind `mqtt`, searxng behind `search`.

    Both carry `cap_add: [CHOWN, SETGID, SETUID]`, without which their
    entrypoints cannot drop privileges and they crash-loop. A job that leaves
    the profiles off starts neither and cannot see it.
    """
    env = _compose_smoke_job()["_env"]
    profiles = {p.strip() for p in str(env["COMPOSE_PROFILES"]).split(",")}
    assert {"mqtt", "search"} <= profiles, profiles

    compose = yaml.safe_load(
        (REPO_ROOT / "jarvis-core" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    started = set(str(env["SMOKE_SERVICES"]).split())
    for name, service in compose["services"].items():
        if service.get("cap_add"):
            assert name in started, (
                f"{name} needs cap_add to de-escalate, which is the shape of a "
                "crash loop this job is supposed to catch, but the job never "
                "starts it"
            )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
