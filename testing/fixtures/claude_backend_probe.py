#!/usr/bin/env python3
"""A delegated coding job, end to end, in a real sandbox — with a stand-in agent.

What M41 has to show is that switching backends changes who writes the code and
nothing else: the run happens inside the repository's environment container,
its edits land there, and a repository with no sandbox is refused.

There is no API key on this host and there should not be one, so the agent is
`fake_claude_code.py`, which speaks the same `--print --output-format json`
protocol. What that proves is the plumbing, the containment and the gate. What
it cannot prove is the quality of a real model's work, and nothing here claims
to.

    python3 testing/fixtures/claude_backend_probe.py
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO, REPO / "jarvis-core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from jarvis.integrations.code.claude_backend import ClaudeBackendError, build  # noqa: E402
from jarvis.integrations.code.sandbox import Environment  # noqa: E402
from jarvis.integrations.code.workspace import Repo, Workspace  # noqa: E402

IMAGE = "python:3.12-bookworm"
STAND_IN = REPO / "testing" / "fixtures" / "fake_claude_code.py"


async def main() -> int:
    if not shutil.which("docker"):
        print("docker is not available, so containment cannot be proved", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="claude-backend-"))
    (work / "README.md").write_text("a repository for the probe\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True, capture_output=True)

    failures: list[str] = []
    try:
        # The stand-in has to be INSIDE the container, so it goes in the
        # repository — which is the only thing mounted. That is itself the
        # containment claim: if the mount were wider, this would not be needed.
        stand_in = work / "fake_claude.py"
        shutil.copy(STAND_IN, stand_in)
        # Executable, and invoked as ONE token: `command_line()` quotes each
        # part, so a two-word command becomes a single quoted filename that no
        # shell can find.
        stand_in.chmod(0o755)
        environment = Environment(name="probe", image=IMAGE, network="none")
        repo = Repo(name="probe", path=str(work), writable=True, environment="probe")
        workspace = Workspace(repo, environment=environment)
        backend = build({
            "enabled": True,
            "api_key": "not-a-real-key",
            "command": "./fake_claude.py",
        })

        result = await backend.run(workspace, "fix the failing tests")
        if not result.ok:
            failures.append(f"the delegated run failed: {result.error}")
        else:
            print(f"  ok   a delegated run completes ({result.turns} turns)")

        marker = work / "fake_claude_was_here.txt"
        if not marker.is_file():
            failures.append("the run left no edit in the repository")
        else:
            print("  ok   its edits land in the repository, inside the container")

        # A failure from the agent is a failure of the job, not a crash.
        failed = await backend.run(workspace, "make this FAIL")
        if failed.ok:
            failures.append("a failing delegated run reported success")
        else:
            print(f"  ok   a failure is reported as one ({failed.error[:40]}…)")

        # Output that is not a result is named rather than believed.
        garbage = await backend.run(workspace, "print GARBAGE")
        if garbage.ok:
            failures.append("unreadable output was treated as success")
        else:
            print("  ok   unreadable output is not success")

        # And the containment claim itself.
        unsandboxed = Workspace(Repo(name="bare", path=str(work), writable=True))
        try:
            await backend.run(unsandboxed, "do anything")
        except ClaudeBackendError as err:
            if "sandbox" not in str(err):
                failures.append(f"refused, but not for the sandbox: {err}")
            else:
                print("  ok   a repository with no sandbox is refused")
        else:
            failures.append("IT RAN OUTSIDE A SANDBOX")

        await workspace.close_session()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for failure in failures:
        print(f"  FAIL {failure}")
    print(f"\nclaude-backend probe: {5 - len(failures)}/5")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
