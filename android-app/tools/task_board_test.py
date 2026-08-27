#!/usr/bin/env python3
"""Executable spec for the task board shown on the phone.

jarvis-core keeps one registry of long work (`jarvis-core/jarvis/tasks.py`) and
fires `jarvis_task_added` / `_updated` / `_removed` on every move. `TaskBoard.kt`
turns that stream into rows, a headline and a percentage. This is the same logic
in Python, so what somebody reads on a phone mid-run is pinned without an
emulator — and pinned against the CONSOLE's version of the same decisions, which
is the thing an emulator could never check.

Four things here are easy to get wrong and produce a surface that looks fine:

  * **a null fraction.** jarvis-core sends `"fraction": null` whenever a
    percentage would be a guess. In Kotlin `optDouble` answers NaN and
    `optDouble(k, 0.0)` answers 0.0, so the obvious read turns "do not draw a
    number" into a bar that sits at 0% for the whole run — indistinguishable
    from a task that never started.
  * **a moving bar over `blocked`.** Blocked means waiting on a PERSON. An
    animation there says "working", which is exactly how an approval prompt
    goes unnoticed.
  * **a failure snapped to 0 or 100.** How far it got is the only interesting
    fact about a failed job.
  * **a stale frame winning.** One socket delivers in order, but a `tasks/list`
    response in flight while an event fires lands after it.

Run:  python3 android-app/tools/task_board_test.py
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DONE_LINGER_MS = 8_000
FAILED_LINGER_MS = 30_000
OVERLAY_ROWS = 3

QUEUED, RUNNING, BLOCKED, DONE, ERROR, CANCELLED = (
    "QUEUED", "RUNNING", "BLOCKED", "DONE", "ERROR", "CANCELLED",
)
TERMINAL = {DONE, ERROR, CANCELLED}

DETERMINATE, INDETERMINATE, NONE = "DETERMINATE", "INDETERMINATE", "NONE"


@dataclass
class Row:
    id: str
    title: str = "a job"
    kind: str = "background"
    status: str = QUEUED
    fraction: float | None = None
    detail: str = ""
    result: str = ""
    error: str = ""
    done_steps: int = 0
    total_steps: int = 0
    created: float = 0.0
    updated: float = 0.0

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL

    @property
    def bar(self) -> str:
        if self.fraction is not None:
            return DETERMINATE
        if self.finished:
            return NONE
        if self.status == BLOCKED:
            return NONE
        if self.status == RUNNING:
            return INDETERMINATE
        return NONE

    @property
    def percent(self) -> int:
        if self.fraction is None:
            return 0
        # floor(x + 0.5), which is what Java's Math.round and JavaScript's
        # Math.round both do for a non-negative value. Python's own `round` is
        # banker's rounding and would disagree on exactly the ties.
        return math.floor(min(1.0, max(0.0, self.fraction)) * 100 + 0.5)

    @property
    def says(self) -> str:
        if self.status == ERROR:
            return self.error or "it failed, and said no more than that"
        if self.status == CANCELLED:
            return self.detail or "cancelled"
        if self.status == DONE:
            return self.result or self.detail or "finished"
        if self.status == BLOCKED:
            return self.detail or "waiting for you"
        if self.status == QUEUED:
            return self.detail or "queued"
        return self.detail or "working"

    @property
    def steps(self) -> str:
        return "" if self.total_steps <= 0 else f"{self.done_steps} of {self.total_steps}"


@dataclass
class TaskBoard:
    """Mirrors TaskBoard.kt."""

    entries: dict[str, Row] = field(default_factory=dict)

    @property
    def rows(self) -> list[Row]:
        return sorted(self.entries.values(), key=lambda r: (-r.created, r.id))

    def upsert(self, row: Row) -> None:
        held = self.entries.get(row.id)
        if held is not None and held.updated > row.updated:
            return
        self.entries[row.id] = row

    def remove(self, task_id: str) -> bool:
        return self.entries.pop(task_id, None) is not None

    def replace_all(self, fresh: list[Row]) -> None:
        held = dict(self.entries)
        self.entries.clear()
        for row in fresh:
            old = held.get(row.id)
            self.entries[row.id] = old if old is not None and old.updated > row.updated else row

    def linger_for(self, row: Row) -> int:
        if not row.finished:
            return 0
        return FAILED_LINGER_MS if row.status == ERROR else DONE_LINGER_MS

    def visible(self, now_ms: int) -> list[Row]:
        kept = [
            r for r in self.rows
            if not r.finished or (now_ms - int(r.updated * 1000)) < self.linger_for(r)
        ]
        return [r for r in kept if not r.finished] + [r for r in kept if r.finished]

    def headline(self, now_ms: int) -> str:
        live = self.visible(now_ms)
        parts = []
        for status, word in ((RUNNING, "running"), (BLOCKED, "waiting on you"),
                             (QUEUED, "queued"), (ERROR, "failed")):
            n = sum(1 for r in live if r.status == status)
            if n:
                parts.append(f"{n} {word}")
        return " · ".join(parts)

    def next_expiry_ms(self, now_ms: int) -> int | None:
        lefts = []
        for row in self.entries.values():
            linger = self.linger_for(row)
            if linger == 0:
                continue
            left = linger - (now_ms - int(row.updated * 1000))
            if left > 0:
                lefts.append(left)
        return min(lefts) if lefts else None


# --- the checks -----------------------------------------------------------

failures = 0


def check(name: str, got, expected) -> None:
    global failures
    if got != expected:
        print(f"FAIL  {name}: expected {expected!r}, got {got!r}")
        failures += 1


def check_a_null_fraction_is_never_nought_per_cent() -> None:
    """The single mistake this file exists to catch.

    `optDouble("fraction")` is NaN for a JSON null and `optDouble(k, 0.0)` is
    0.0. Either one draws an open-ended crawl as a bar that has not moved.
    """
    crawl = Row("a", status=RUNNING, fraction=None, total_steps=4, done_steps=1)
    check("an unknown fraction is indeterminate", crawl.bar, INDETERMINATE)
    check("an unknown fraction has no number", crawl.percent, 0)

    real = Row("b", status=RUNNING, fraction=0.5)
    check("a real fraction is determinate", real.bar, DETERMINATE)
    check("a real fraction fills", real.percent, 50)


def check_a_real_zero_is_kept() -> None:
    started = Row("a", status=RUNNING, fraction=0.0, total_steps=3)
    check("nought per cent is a number", started.bar, DETERMINATE)
    check("and it is nought", started.percent, 0)


def check_a_failure_keeps_the_ground_it_covered() -> None:
    failed = Row("a", status=ERROR, fraction=0.4, done_steps=2, total_steps=5)
    check("a failure keeps its bar", failed.bar, DETERMINATE)
    check("at where it got to", failed.percent, 40)


def check_waiting_on_a_person_does_not_animate() -> None:
    check("blocked does not animate", Row("a", status=BLOCKED).bar, NONE)
    check("queued does not animate", Row("a", status=QUEUED).bar, NONE)
    check("cancelled does not animate", Row("a", status=CANCELLED).bar, NONE)


def check_a_half_rounds_the_way_the_console_rounds() -> None:
    # A percent of drift between the phone and the browser on the same task is
    # a difference nobody would ever chase and anybody could see.
    check("two thirds", Row("a", fraction=2 / 3).percent, 67)
    check("a tie rounds up", Row("a", fraction=0.675).percent, 68)
    check("a third", Row("a", fraction=1 / 3).percent, 33)


def check_a_fraction_out_of_range_is_clamped() -> None:
    check("over one clamps", Row("a", fraction=1.4).percent, 100)
    check("under nought clamps", Row("a", fraction=-1.0).percent, 0)


def check_an_update_for_an_unseen_task_inserts() -> None:
    # Work that began before the phone connected. Ignoring it loses it entirely.
    board = TaskBoard()
    board.upsert(Row("new", status=RUNNING, updated=5))
    check("an unseen update inserts", [r.id for r in board.rows], ["new"])


def check_a_stale_frame_cannot_undo_a_newer_one() -> None:
    board = TaskBoard()
    board.upsert(Row("a", status=DONE, updated=500))
    board.upsert(Row("a", status=RUNNING, updated=100))
    check("the later stamp wins", board.entries["a"].status, DONE)


def check_a_refresh_does_not_undo_an_event_in_flight() -> None:
    board = TaskBoard()
    board.upsert(Row("a", status=DONE, updated=900))
    board.replace_all([Row("a", status=RUNNING, updated=100)])
    check("a stale listing loses", board.entries["a"].status, DONE)


def check_a_refresh_decides_what_still_exists() -> None:
    board = TaskBoard()
    board.upsert(Row("gone", updated=1))
    board.upsert(Row("a", updated=1))
    board.replace_all([Row("a", updated=2)])
    check("the listing is the membership", sorted(board.entries), ["a"])


def check_a_finished_task_lingers_then_goes() -> None:
    # A job you were watching vanishing at the instant it succeeds is the one
    # frame you actually wanted to see.
    board = TaskBoard()
    board.upsert(Row("a", status=DONE, updated=1000))
    check("it stays for a moment", [r.id for r in board.visible(1_002_000)], ["a"])
    check("then it goes", board.visible(1_000_000 + DONE_LINGER_MS + 1_000), [])


def check_a_failure_lingers_far_longer() -> None:
    board = TaskBoard()
    board.upsert(Row("a", status=ERROR, updated=1000))
    after_ordinary = 1_000_000 + DONE_LINGER_MS + 1_000
    check("a failure outlasts a success", [r.id for r in board.visible(after_ordinary)], ["a"])
    check("but not for ever", board.visible(1_000_000 + FAILED_LINGER_MS + 1_000), [])


def check_live_work_sits_above_what_just_ended() -> None:
    board = TaskBoard()
    board.upsert(Row("done", status=DONE, created=9999, updated=1000))
    board.upsert(Row("live", status=RUNNING, created=1, updated=1000))
    check("live first", [r.id for r in board.visible(1_001_000)], ["live", "done"])


def check_waiting_is_counted_apart_from_running() -> None:
    # Folded together, an approval sits unnoticed behind a spinner.
    board = TaskBoard()
    board.upsert(Row("a", status=RUNNING, updated=1000))
    board.upsert(Row("b", status=RUNNING, updated=1000))
    board.upsert(Row("c", status=BLOCKED, updated=1000))
    check("headline", board.headline(1_000_000), "2 running · 1 waiting on you")
    check("nothing to say", TaskBoard().headline(0), "")


def check_one_timer_at_the_next_expiry() -> None:
    # A tick every second behind an empty overlay is a battery cost on a phone,
    # not merely an untidiness.
    board = TaskBoard()
    board.upsert(Row("a", status=DONE, updated=1000))
    check("the next wake-up", board.next_expiry_ms(1_000_000), DONE_LINGER_MS)
    check("nothing pending", board.next_expiry_ms(1_000_000 + DONE_LINGER_MS + 1), None)
    live = TaskBoard()
    live.upsert(Row("a", status=RUNNING, updated=1000))
    check("running never expires", live.next_expiry_ms(1_000_000), None)


def check_the_soonest_expiry_wins() -> None:
    board = TaskBoard()
    board.upsert(Row("old", status=DONE, updated=996))
    board.upsert(Row("new", status=DONE, updated=1000))
    check("soonest", board.next_expiry_ms(1_000_000), DONE_LINGER_MS - 4000)


def check_what_a_row_says() -> None:
    check("a failure keeps the server's words",
          Row("a", status=ERROR, error="the model server refused").says,
          "the model server refused")
    check("a failure with no reason still says something",
          Row("a", status=ERROR).says, "it failed, and said no more than that")
    check("a finished task shows its result",
          Row("a", status=DONE, result="all twelve read").says, "all twelve read")
    check("blocked says who it is waiting for",
          Row("a", status=BLOCKED).says, "waiting for you")


def check_steps_are_counted_only_when_there_are_any() -> None:
    check("counted", Row("a", done_steps=3, total_steps=8).steps, "3 of 8")
    check("not invented", Row("a").steps, "")


def check_removal() -> None:
    board = TaskBoard()
    board.upsert(Row("a", updated=1))
    check("removed", board.remove("a"), True)
    check("removing what is not there", board.remove("a"), False)


# --- the Kotlin has to agree ----------------------------------------------


def check_kotlin_agrees(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/tasks/TaskBoard.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    src = path.read_text(encoding="utf-8")
    bad = 0
    for const, value in (
        ("DONE_LINGER_MS", "8_000L"),
        ("FAILED_LINGER_MS", "30_000L"),
        ("OVERLAY_ROWS", "3"),
        ("EVENT_ADDED", '"jarvis_task_added"'),
        ("EVENT_UPDATED", '"jarvis_task_updated"'),
        ("EVENT_REMOVED", '"jarvis_task_removed"'),
    ):
        if f"const val {const} = {value}" not in src:
            print(f"FAIL  TaskBoard.{const} is no longer {value}")
            bad += 1

    # The fraction must be nullable all the way through. A non-null Double here
    # means somebody substituted a default, which is the bug at the top of this
    # file wearing a type.
    if "val fraction: Double? = null" not in src:
        print("FAIL  TaskBoard.Row.fraction is no longer nullable")
        bad += 1
    if "Math.round(it.coerceIn(0.0, 1.0) * 100).toInt()" not in src:
        print("FAIL  the phone no longer rounds its percentage the way the console does")
        bad += 1
    if "fraction != null -> Bar.DETERMINATE" not in src:
        print("FAIL  the bar no longer keys on whether there IS a fraction")
        bad += 1
    if "status == Status.BLOCKED -> Bar.NONE" not in src:
        print("FAIL  a blocked task would animate as if it were working")
        bad += 1
    return bad


def check_the_parser_never_substitutes_a_number(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/jarvis/app/tasks/TaskFrames.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    src = path.read_text(encoding="utf-8")
    bad = 0
    if 'task.isNull("fraction")' not in src:
        print("FAIL  the parser no longer checks for an explicit null fraction")
        bad += 1
    # `optDouble("fraction", <anything but NaN>)` is the exact mistake.
    for hit in re.findall(r'optDouble\("fraction"[^)]*\)', src):
        if "Double.NaN" not in hit:
            print(f"FAIL  {hit} substitutes a number for a missing fraction")
            bad += 1
    return bad


def check_core_fires_what_the_phone_listens_for(root: Path) -> int:
    """The two ends have to agree on the event names, or nothing is drawn."""
    tasks = root / "jarvis-core/jarvis/tasks.py"
    if not tasks.is_file():
        print(f"FAIL  {tasks} is missing")
        return 1
    src = tasks.read_text(encoding="utf-8")
    bad = 0
    for const, value in (
        ("EVENT_TASK_ADDED", '"jarvis_task_added"'),
        ("EVENT_TASK_UPDATED", '"jarvis_task_updated"'),
        ("EVENT_TASK_REMOVED", '"jarvis_task_removed"'),
    ):
        if f"{const} = {value}" not in src:
            print(f"FAIL  jarvis-core no longer fires {value} as {const}")
            bad += 1
    return bad


def check_the_console_agrees(root: Path) -> int:
    """The phone and the browser must not disagree about what Jarvis is doing.

    Same linger times, same three bar modes, same rule about a null fraction.
    Two surfaces drifting apart on this is worse than either being slightly
    wrong, because whichever one you are holding looks authoritative.
    """
    web = root / "jarvis-web/src/lib/tasks.ts"
    if not web.is_file():
        print(f"FAIL  {web} is missing")
        return 1
    src = web.read_text(encoding="utf-8")
    bad = 0
    for name, value in (("LINGER_MS", "8_000"), ("LINGER_FAILED_MS", "30_000")):
        if f"export const {name} = {value};" not in src:
            print(f"FAIL  the console's {name} is no longer {value} — the two surfaces disagree")
            bad += 1
    if "if (task.status === 'blocked') return 'none';" not in src:
        print("FAIL  the console would animate a blocked task; the phone would not")
        bad += 1
    return bad


def main() -> int:
    android = Path(__file__).resolve().parent.parent
    root = android.parent

    for name, fn in sorted(globals().items()):
        if name.startswith("check_") and callable(fn) and fn.__code__.co_argcount == 0:
            fn()

    total = (
        failures
        + check_kotlin_agrees(android)
        + check_the_parser_never_substitutes_a_number(android)
        + check_core_fires_what_the_phone_listens_for(root)
        + check_the_console_agrees(root)
    )
    print("task_board_test: FAILED" if total else "task_board_test: all checks passed")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
