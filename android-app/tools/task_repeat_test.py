#!/usr/bin/env python3
"""Loops in the task engine (Tasker's "For / Loop"), pinned (M61).

`docs/ANDROID_TASKER_PARITY.md` carried loops as a gap; they were not. The
task engine's `repeat` step runs a body a fixed number of times, or while a
condition holds, and every bound Tasker leaves to the user is a constant here:
`TaskLimits.MAX_REPEAT_ITERATIONS`, a count clamped to it, the whole-run step
and time budgets. This reads the Kotlin and holds the bounds in place.

Run:  python3 android-app/tools/task_repeat_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "app/src/main/kotlin/ai/jarvis/app/automation/tasks/TaskModels.kt"
RUNNER = ROOT / "app/src/main/kotlin/ai/jarvis/app/automation/tasks/TaskRunner.kt"
JSON = ROOT / "app/src/main/kotlin/ai/jarvis/app/automation/tasks/TaskJson.kt"


def test_repeat_is_a_step_with_a_count_or_a_condition():
    models = MODELS.read_text()
    assert 'REPEAT("repeat")' in models
    spec = models[models.index("data class StepSpec("): models.index("data class TaskDefinition(")]
    assert "val steps: List<StepSpec>" in spec and "val count: Int?" in spec and "val condition: ConditionSpec?" in spec


def test_the_runner_runs_it_and_every_bound_is_a_constant():
    runner = RUNNER.read_text()
    assert "StepType.REPEAT -> runRepeat(" in runner
    body = runner[runner.index("fun runRepeat("):]
    assert "TaskLimits.MAX_REPEAT_ITERATIONS" in body, "the loop has no iteration ceiling"
    assert re.search(r"count\?\.coerceIn\(0, TaskLimits\.MAX_REPEAT_ITERATIONS\)", body), "a count is not clamped"
    models = MODELS.read_text()
    assert re.search(r"const val MAX_REPEAT_ITERATIONS = \d+", models)
    assert re.search(r"const val MAX_STEPS_PER_RUN = \d+", models) and re.search(r"const val MAX_RUN_MS = ", models)


def test_the_wire_shape_is_parsed():
    src = JSON.read_text()
    assert "steps" in src and "count" in src, "TaskJson does not read a repeat's body and count"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
