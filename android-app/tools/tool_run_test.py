#!/usr/bin/env python3
"""Executable spec for the tool-activity model shown on the phone.

jarvis-core fires `jarvis_tool_started` and `jarvis_tool_finished` around every
tool call a turn makes. `ToolRun.kt` turns that stream into rows and a
percentage; this is the same logic in Python, so the arithmetic that decides
what somebody reads mid-turn is pinned without an emulator.

Three things here are easy to get wrong and impossible to notice by hand:

  * **the denominator** — a second round of calls adds rows beyond the first
    round's `total`, so a naive `total` shows "5 / 4";
  * **the key** — the same tool called in two rounds is two calls, and keying
    on the name alone makes the second overwrite the first;
  * **a finish with no start** — a socket that subscribes mid-turn misses the
    start frame, and dropping the finish too means the row never appears at all.

Run:  python3 android-app/tools/tool_run_test.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

DONE_HOLD_MS = 4_000
FAILED_HOLD_MS = 12_000
VALUE_CHARS = 40
MAX_PARTS = 3

RUNNING, OK, FAILED = "RUNNING", "OK", "FAILED"


@dataclass
class Row:
    key: str
    name: str
    summary: str
    index: int
    total: int
    state: str
    error: str | None = None
    duration_ms: int = 0


def key_of(name: str, round_: int, index: int) -> str:
    return f"{round_}:{index}:{name}"


def summarise(arguments: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for key, value in arguments:
        if value == "" or value == "null":
            continue
        short = value[: VALUE_CHARS - 1] + "…" if len(value) > VALUE_CHARS else value
        parts.append(f"{key}: {short}")
        if len(parts) >= MAX_PARTS:
            break
    return " · ".join(parts)


class ToolRun:
    """Mirrors ToolRun.kt."""

    def __init__(self) -> None:
        self.entries: list[Row] = []

    # --- input ----------------------------------------------------------

    def started(self, name, round_, index, total, summary="") -> None:
        self._put(
            Row(
                key=key_of(name, round_, index),
                name=name,
                summary=summary,
                index=index,
                total=max(total, 1),
                state=RUNNING,
            )
        )

    def finished(self, name, round_, index, total, ok, error=None, duration_ms=0) -> None:
        key = key_of(name, round_, index)
        existing = next((r for r in self.entries if r.key == key), None)
        self._put(
            Row(
                key=key,
                name=name,
                summary=existing.summary if existing else "",
                index=index,
                total=max(total, existing.total if existing else 1, 1),
                state=OK if ok else FAILED,
                error=error or None,
                duration_ms=duration_ms,
            )
        )

    def _put(self, row: Row) -> None:
        at = next((i for i, r in enumerate(self.entries) if r.key == row.key), -1)
        if at >= 0:
            self.entries[at] = row
        else:
            self.entries.append(row)
        self.entries.sort(key=lambda r: r.index)

    # --- output ---------------------------------------------------------

    @property
    def done(self) -> int:
        return sum(1 for r in self.entries if r.state != RUNNING)

    @property
    def total(self) -> int:
        if not self.entries:
            return 0
        return max(max(r.total for r in self.entries), len(self.entries))

    @property
    def percent(self) -> int:
        return 0 if self.total == 0 else (self.done * 100) // self.total

    @property
    def running(self) -> bool:
        return any(r.state == RUNNING for r in self.entries)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.entries if r.state == FAILED)

    def hold_ms(self) -> int:
        return FAILED_HOLD_MS if self.failed else DONE_HOLD_MS

    def visible(self, limit: int) -> list[Row]:
        if limit <= 0 or len(self.entries) <= limit:
            return list(self.entries)
        return self.entries[-limit:]


# --- the cases -----------------------------------------------------------


def check_empty() -> int:
    run = ToolRun()
    if (run.done, run.total, run.percent, run.running) != (0, 0, 0, False):
        print("FAIL  an empty run is not all zeroes")
        return 1
    return 0


def check_progress_is_the_models_count() -> int:
    """"3 / 4" means three of the four things the model asked for."""
    run = ToolRun()
    for i, name in enumerate(["get_state", "turn_on", "lock_control", "set_temperature"]):
        run.started(name, 1, i, 4)
    failures = 0
    if (run.done, run.total, run.percent) != (0, 4, 0):
        print(f"FAIL  four started is not 0/4 ({run.done}/{run.total} {run.percent}%)")
        failures += 1
    run.finished("get_state", 1, 0, 4, ok=True, duration_ms=40)
    run.finished("turn_on", 1, 1, 4, ok=True, duration_ms=50)
    run.finished("lock_control", 1, 2, 4, ok=False, error="no such entity")
    if (run.done, run.total, run.percent) != (3, 4, 75):
        print(f"FAIL  three of four is not 75% ({run.done}/{run.total} {run.percent}%)")
        failures += 1
    if not run.running:
        print("FAIL  a run with one call outstanding says it is not running")
        failures += 1
    run.finished("set_temperature", 1, 3, 4, ok=True, duration_ms=12)
    if (run.percent, run.running, run.failed) != (100, False, 1):
        print(f"FAIL  the finished run is wrong ({run.percent}% running={run.running})")
        failures += 1
    return failures


def check_a_second_round_extends_the_denominator() -> int:
    """The bug: round 2's `total` is 2, and 4 of 2 would read as 200%."""
    run = ToolRun()
    for i in range(2):
        run.started(f"a{i}", 1, i, 2)
        run.finished(f"a{i}", 1, i, 2, ok=True)
    for i in range(2):
        run.started(f"b{i}", 2, i, 2)
    failures = 0
    if run.total != 4:
        print(f"FAIL  two rounds of two is not four calls (total={run.total})")
        failures += 1
    if run.percent != 50:
        print(f"FAIL  two of four done is not 50% ({run.percent}%)")
        failures += 1
    if run.percent > 100:
        print("FAIL  the progress bar went past the end")
        failures += 1
    return failures


def check_the_same_tool_twice_is_two_rows() -> int:
    run = ToolRun()
    run.started("get_state", 1, 0, 1)
    run.finished("get_state", 1, 0, 1, ok=True, duration_ms=10)
    run.started("get_state", 2, 0, 1)
    if len(run.entries) != 2:
        print(f"FAIL  the same tool in two rounds collapsed to {len(run.entries)} row(s)")
        return 1
    if run.done != 1:
        print("FAIL  the second call was counted as already done")
        return 1
    return 0


def check_a_repeat_start_does_not_duplicate() -> int:
    run = ToolRun()
    run.started("turn_on", 1, 0, 1, summary="entity_id: light.lab")
    run.started("turn_on", 1, 0, 1, summary="entity_id: light.lab")
    if len(run.entries) != 1:
        print(f"FAIL  a repeated start made {len(run.entries)} rows")
        return 1
    return 0


def check_a_finish_with_no_start_still_shows() -> int:
    """Subscribing mid-turn misses the start frame. The row must still appear."""
    run = ToolRun()
    run.finished("lock_control", 1, 0, 1, ok=False, error="held for approval")
    failures = 0
    if len(run.entries) != 1:
        print("FAIL  an unmatched finish produced no row")
        failures += 1
    elif run.entries[0].state != FAILED or run.entries[0].error != "held for approval":
        print(f"FAIL  the unmatched finish lost its reason ({run.entries[0]})")
        failures += 1
    if run.running:
        print("FAIL  a run whose only call finished says it is still running")
        failures += 1
    return failures


def check_rows_are_in_the_models_order() -> int:
    """They finish out of order; they must not be drawn out of order."""
    run = ToolRun()
    for i, name in enumerate(["first", "second", "third"]):
        run.started(name, 1, i, 3)
    run.finished("third", 1, 2, 3, ok=True)
    run.finished("first", 1, 0, 3, ok=True)
    got = [r.name for r in run.entries]
    if got != ["first", "second", "third"]:
        print(f"FAIL  the rows are out of order: {got}")
        return 1
    return 0


def check_a_failure_holds_the_panel_longer() -> int:
    clean, broken = ToolRun(), ToolRun()
    clean.started("a", 1, 0, 1)
    clean.finished("a", 1, 0, 1, ok=True)
    broken.started("a", 1, 0, 1)
    broken.finished("a", 1, 0, 1, ok=False, error="nope")
    failures = 0
    if clean.hold_ms() != DONE_HOLD_MS:
        print(f"FAIL  a clean run holds for {clean.hold_ms()}ms")
        failures += 1
    if broken.hold_ms() != FAILED_HOLD_MS:
        print(f"FAIL  a failed run holds for {broken.hold_ms()}ms")
        failures += 1
    if broken.hold_ms() <= clean.hold_ms():
        print("FAIL  a failure does not stay on screen longer than a success")
        failures += 1
    return failures


def check_finishing_keeps_the_arguments() -> int:
    """The summary arrives with the start; the finish must not blank it."""
    run = ToolRun()
    run.started("turn_on", 1, 0, 1, summary="entity_id: light.lab")
    run.finished("turn_on", 1, 0, 1, ok=True, duration_ms=7)
    if run.entries[0].summary != "entity_id: light.lab":
        print(f"FAIL  the arguments were lost on finish ({run.entries[0].summary!r})")
        return 1
    return 0


def check_the_phone_shows_the_latest_few() -> int:
    """A nine-call turn must not push the orb off the top of the screen."""
    run = ToolRun()
    for i in range(9):
        run.started(f"t{i}", 1, i, 9)
    failures = 0
    got = [r.name for r in run.visible(4)]
    if got != ["t5", "t6", "t7", "t8"]:
        print(f"FAIL  the last four are not the ones shown: {got}")
        failures += 1
    # ...and the header still counts all nine, so the cap is not a quiet lie.
    if run.total != 9:
        print(f"FAIL  capping the rows changed the count ({run.total})")
        failures += 1
    short = ToolRun()
    short.started("only", 1, 0, 1)
    if len(short.visible(4)) != 1:
        print("FAIL  a short run was padded or trimmed")
        failures += 1
    return failures


SUMMARY_CASES: list[tuple[str, list[tuple[str, str]], str]] = [
    ("nothing is nothing", [], ""),
    (
        "empty values are skipped rather than shown as blanks",
        [("entity_id", ""), ("brightness", "40")],
        "brightness: 40",
    ),
    (
        "the first three are kept, in the order given",
        [("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")],
        "a: 1 · b: 2 · c: 3",
    ),
    (
        "a long value is cut, not wrapped",
        [("prompt", "x" * 80)],
        "prompt: " + "x" * 39 + "…",
    ),
    (
        "a value exactly at the limit is not cut",
        [("prompt", "y" * VALUE_CHARS)],
        "prompt: " + "y" * VALUE_CHARS,
    ),
]


def check_summaries() -> int:
    failures = 0
    for name, args, expected in SUMMARY_CASES:
        got = summarise(args)
        if got != expected:
            print(f"FAIL  {name}: expected {expected!r}, got {got!r}")
            failures += 1
    return failures


# --- the Kotlin has to agree ---------------------------------------------


def check_kotlin_agrees(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/assist/ToolRun.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    src = path.read_text(encoding="utf-8")
    failures = 0
    for const, value in (
        ("DONE_HOLD_MS", "4_000L"),
        ("FAILED_HOLD_MS", "12_000L"),
        ("VALUE_CHARS", "40"),
        ("MAX_PARTS", "3"),
        ("EVENT_STARTED", '"jarvis_tool_started"'),
        ("EVENT_FINISHED", '"jarvis_tool_finished"'),
    ):
        if f"const val {const} = {value}" not in src:
            print(f"FAIL  ToolRun.{const} is no longer {value}")
            failures += 1

    # The denominator, spelled out. `maxOf(announced, arrived)` is the whole
    # defence against "5 / 4" on a second round of calls.
    if not re.search(r"maxOf\(entries\.maxOf \{ it\.total \}, entries\.size\)", src):
        print("FAIL  the total is no longer the larger of announced and arrived")
        failures += 1

    # The key must carry the round, or the same tool called twice is one row.
    if 'fun keyOf(name: String, round: Int, index: Int): String = "$round:$index:$name"' not in src:
        print("FAIL  the row key no longer includes the round")
        failures += 1
    return failures


def check_core_fires_what_the_phone_listens_for(root: Path) -> int:
    """The two ends have to agree on the event names, or nothing is drawn."""
    tools = root / "jarvis-core/jarvis/llm/tools.py"
    if not tools.is_file():
        print(f"FAIL  {tools} is missing")
        return 1
    src = tools.read_text(encoding="utf-8")
    failures = 0
    for const, value in (
        ("EVENT_TOOL_STARTED", '"jarvis_tool_started"'),
        ("EVENT_TOOL_FINISHED", '"jarvis_tool_finished"'),
    ):
        if f"{const} = {value}" not in src:
            print(f"FAIL  jarvis-core no longer fires {value} as {const}")
            failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parent.parent
    root = android.parent
    failures = (
        check_empty()
        + check_progress_is_the_models_count()
        + check_a_second_round_extends_the_denominator()
        + check_the_same_tool_twice_is_two_rows()
        + check_a_repeat_start_does_not_duplicate()
        + check_a_finish_with_no_start_still_shows()
        + check_rows_are_in_the_models_order()
        + check_a_failure_holds_the_panel_longer()
        + check_finishing_keeps_the_arguments()
        + check_the_phone_shows_the_latest_few()
        + check_summaries()
        + check_kotlin_agrees(android)
        + check_core_fires_what_the_phone_listens_for(root)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print("tool_run: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
