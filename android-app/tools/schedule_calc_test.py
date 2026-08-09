#!/usr/bin/env python3
"""Executable spec for the Android trigger scheduler.

Mirrors `app/src/main/kotlin/ai/jarvis/app/automation/triggers/ScheduleCalculator.kt`,
which turns a {time, days_of_week, interval} schedule into the next fire time.
That Kotlin cannot be compiled in this container, so the arithmetic is written
down twice: once there and once here, where it runs.

THE TRICK THAT MAKES THIS DST-NAIVE AND TESTABLE
------------------------------------------------
The core works entirely in "local millis": the wall clock rendered as if it
were UTC. 2026-03-29 07:00 local is the same number whatever the offset is that
day, so day-of-week maths, midnight wrapping and interval alignment have no
timezone in them at all and can be checked with plain integers.

Exactly one conversion happens at the edges (`ScheduleCalculator.nextFireEpochMs`
in Kotlin, `next_fire_epoch_ms` here) and that is where DST becomes visible:

  * spring forward — 02:30 does not exist on the jump day. The platform
    resolves the gap forward, so the alarm fires at 03:30. We accept that.
  * fall back — 01:30 happens twice. We take the FIRST one. If the first is
    already in the past (we are living through the repeat) the candidate is
    <= now, so the loop asks the core for the next one and the schedule fires
    once that day, not twice. That guard is the part worth testing.

Run:  python3 android-app/tools/schedule_calc_test.py
  or: python3 -m pytest android-app/tools/schedule_calc_test.py -q
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS

MON, TUE, WED, THU, FRI, SAT, SUN = 1, 2, 3, 4, 5, 6, 7
WEEKDAYS = frozenset({MON, TUE, WED, THU, FRI})
WEEKEND = frozenset({SAT, SUN})

# A schedule that can never fire inside a week is a bug, not a schedule.
MAX_LOOKAHEAD_DAYS = 8
# Guard against an interval + day filter that walks forever.
MAX_INTERVAL_STEPS = 4096
MAX_INTERVAL_MINUTES = 7 * 24 * 60


# --- the rules, mirrored from ScheduleCalculator.kt -------------------------


@dataclass(frozen=True)
class ScheduleSpec:
    """{time, days_of_week, interval} from a task's trigger spec.

    minute_of_day    0..1439, local wall clock. None in interval mode.
    days_of_week     ISO 1=Mon .. 7=Sun. Empty means every day.
    interval_minutes when set, fires every N minutes instead of at a time.
    anchor_local_ms  grid origin for interval mode, in LOCAL millis. Default 0,
                     which is local midnight on 1970-01-01, so any interval that
                     divides a day lands on tidy wall-clock times (:00, :30).
    """

    minute_of_day: int | None = None
    days_of_week: frozenset[int] = field(default_factory=frozenset)
    interval_minutes: int | None = None
    anchor_local_ms: int | None = None

    def normalized_days(self) -> frozenset[int]:
        return frozenset(d for d in self.days_of_week if MON <= d <= SUN)

    def is_valid(self) -> bool:
        if self.interval_minutes is not None:
            return 1 <= self.interval_minutes <= MAX_INTERVAL_MINUTES
        if self.minute_of_day is not None:
            return 0 <= self.minute_of_day <= 1439
        return False


def day_index(local_ms: int) -> int:
    """Whole local days since the epoch. Floor division, so negatives work."""
    return local_ms // DAY_MS


def iso_weekday(local_ms: int) -> int:
    """ISO day of week, 1=Mon..7=Sun. 1970-01-01 was a Thursday (=4)."""
    return ((day_index(local_ms) + 3) % 7) + 1


def next_fire_local_ms(now_local_ms: int, spec: ScheduleSpec) -> int | None:
    """PURE core. Next fire strictly AFTER now_local_ms, in local millis.

    Returns None when the spec can never fire (invalid, or a day filter that
    excludes every day).
    """
    if not spec.is_valid():
        return None
    days = spec.normalized_days()
    if spec.days_of_week and not days:
        return None  # every requested day was garbage: refuse, do not fire daily

    if spec.interval_minutes is not None:
        return _next_interval(now_local_ms, spec, days)
    return _next_time_of_day(now_local_ms, spec, days)


def _align_up(value: int, anchor: int, step: int) -> int:
    """Smallest point on the anchor+n*step grid that is >= value."""
    delta = value - anchor
    n = -((-delta) // step)  # ceiling division that works for negatives
    return anchor + n * step


def _next_interval(now_local_ms: int, spec: ScheduleSpec, days: frozenset[int]) -> int | None:
    step = spec.interval_minutes * MINUTE_MS
    anchor_given = spec.anchor_local_ms is not None
    anchor = spec.anchor_local_ms if anchor_given else 0

    if anchor_given and anchor > now_local_ms:
        # An explicit anchor in the future is a start time, so do not extend
        # the grid backwards past it. The implicit anchor (0 = local midnight
        # on 1970-01-01) is only a phase reference and never a start time —
        # a device whose clock is set before 1970 must still get a fire.
        candidate = anchor
    else:
        candidate = _align_up(now_local_ms + 1, anchor, step)
        if candidate <= now_local_ms:  # exactly on the grid
            candidate += step

    if not days:
        return candidate

    # Day filter: skip whole days rather than stepping through them, so a
    # 5-minute interval restricted to Monday does not loop 288 times per day.
    for _ in range(MAX_INTERVAL_STEPS):
        if iso_weekday(candidate) in days:
            return candidate
        next_day_start = (day_index(candidate) + 1) * DAY_MS
        candidate = _align_up(next_day_start, anchor, step)
        if candidate <= now_local_ms:
            candidate = _align_up(now_local_ms + 1, anchor, step)
    return None


def _next_time_of_day(now_local_ms: int, spec: ScheduleSpec, days: frozenset[int]) -> int | None:
    today = day_index(now_local_ms)
    for offset in range(MAX_LOOKAHEAD_DAYS):
        day = today + offset
        candidate = day * DAY_MS + spec.minute_of_day * MINUTE_MS
        if days and iso_weekday(candidate) not in days:
            continue
        if candidate > now_local_ms:
            return candidate
    return None


def next_fire_epoch_ms(now_epoch_ms: int, spec: ScheduleSpec, zone) -> int | None:
    """The wrapper. `zone` converts between real epoch millis and local millis.

    It must provide:
        to_local(epoch_ms)  -> local_ms
        to_epoch(local_ms)  -> epoch_ms   (gap resolved forward, overlap earliest)

    The retry loop is the DST guard: when `to_epoch` maps the candidate onto an
    instant that is not actually in the future (a fall-back repeat, or a gap
    that resolved backwards on some other platform), we ask the core for the
    one after it rather than firing in the past or firing twice.
    """
    local_now = zone.to_local(now_epoch_ms)
    cursor = local_now
    for _ in range(MAX_LOOKAHEAD_DAYS + 2):
        candidate_local = next_fire_local_ms(cursor, spec)
        if candidate_local is None:
            return None
        candidate_epoch = zone.to_epoch(candidate_local)
        if candidate_epoch > now_epoch_ms:
            return candidate_epoch
        cursor = candidate_local
    return None


# --- test doubles for the timezone seam -------------------------------------


class FixedOffsetZone:
    """UTC+offset, no DST. The boring case."""

    def __init__(self, offset_ms: int = 0):
        self.offset_ms = offset_ms

    def to_local(self, epoch_ms: int) -> int:
        return epoch_ms + self.offset_ms

    def to_epoch(self, local_ms: int) -> int:
        return local_ms - self.offset_ms


class TwoOffsetZone:
    """A zone that changes offset once, at a known LOCAL wall-clock instant.

    `spring_forward_local` is the local millis at which the clock jumps from
    `winter_offset` to `summer_offset` (so wall-clock times in
    [jump, jump + delta) never happen), and `fall_back_local` is where it jumps
    back (so [jump - delta, jump) happens twice).

    to_epoch() resolves a gap FORWARD and an overlap to the EARLIEST instant,
    which is what java.time's ZonedDateTime.of does.
    """

    def __init__(self, winter_offset_ms, summer_offset_ms, spring_forward_local, fall_back_local):
        self.winter = winter_offset_ms
        self.summer = summer_offset_ms
        self.spring = spring_forward_local
        self.fall = fall_back_local
        self.delta = summer_offset_ms - winter_offset_ms

    def _offset_for_local(self, local_ms: int) -> int:
        if local_ms < self.spring:
            return self.winter
        if local_ms < self.fall:
            return self.summer
        return self.winter

    def to_epoch(self, local_ms: int) -> int:
        if self.spring <= local_ms < self.spring + self.delta:
            # The gap. ZonedDateTime.of shifts the local time forward by the
            # length of the gap, so 02:30 becomes 03:30 rather than 03:00.
            return (local_ms + self.delta) - self.summer
        return local_ms - self._offset_for_local(local_ms)

    def to_local(self, epoch_ms: int) -> int:
        # Winter first: during the overlap this reports the earlier reading,
        # which matches to_epoch()'s "earliest" resolution.
        if epoch_ms < self.spring - self.winter:
            return epoch_ms + self.winter
        if epoch_ms < self.fall - self.summer:
            return epoch_ms + self.summer
        return epoch_ms + self.winter


# --- helpers for readable tests ---------------------------------------------


def local(day: int, hour: int = 0, minute: int = 0) -> int:
    """Local millis for `day` days after the epoch at hh:mm."""
    return day * DAY_MS + hour * HOUR_MS + minute * MINUTE_MS


def at(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def show(local_ms: int) -> str:
    d = day_index(local_ms)
    rem = local_ms - d * DAY_MS
    names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    return f"day{d}({names[iso_weekday(local_ms)]}) {rem // HOUR_MS:02d}:{rem % HOUR_MS // MINUTE_MS:02d}"


# --- tests ------------------------------------------------------------------

FAILURES: list[str] = []


def check(name: str, got, want):
    """Raises on mismatch so this file works under pytest as well as standalone;
    `main()` catches and collects."""
    if got != want:
        got_s = show(got) if isinstance(got, int) and not isinstance(got, bool) else repr(got)
        want_s = show(want) if isinstance(want, int) and not isinstance(want, bool) else repr(want)
        raise AssertionError(f"{name}: got {got_s}, want {want_s}")


def test_time_of_day_later_today():
    """07:00 asked at 06:00 fires today."""
    spec = ScheduleSpec(minute_of_day=at(7))
    check("later today", next_fire_local_ms(local(0, 6, 0), spec), local(0, 7, 0))


def test_time_of_day_already_passed_rolls_to_tomorrow():
    spec = ScheduleSpec(minute_of_day=at(7))
    check("passed", next_fire_local_ms(local(0, 8, 0), spec), local(1, 7, 0))


def test_time_of_day_exactly_now_is_not_now():
    """Strictly future. Firing "now" would re-fire the alarm that just woke us."""
    spec = ScheduleSpec(minute_of_day=at(7))
    check("exactly now", next_fire_local_ms(local(0, 7, 0), spec), local(1, 7, 0))


def test_time_of_day_one_ms_before():
    spec = ScheduleSpec(minute_of_day=at(7))
    check("1ms before", next_fire_local_ms(local(0, 7, 0) - 1, spec), local(0, 7, 0))


def test_midnight_wraps():
    spec = ScheduleSpec(minute_of_day=0)
    check("midnight from 23:59", next_fire_local_ms(local(0, 23, 59), spec), local(1, 0, 0))
    check("midnight from 00:00", next_fire_local_ms(local(0, 0, 0), spec), local(1, 0, 0))


def test_last_minute_of_day():
    spec = ScheduleSpec(minute_of_day=1439)
    check("23:59", next_fire_local_ms(local(0, 12, 0), spec), local(0, 23, 59))


def test_weekdays_only_skips_the_weekend():
    # day 0 = Thursday. day 1 Fri, day 2 Sat, day 3 Sun, day 4 Mon.
    spec = ScheduleSpec(minute_of_day=at(7), days_of_week=WEEKDAYS)
    check("Fri 08:00 -> Mon", next_fire_local_ms(local(1, 8, 0), spec), local(4, 7, 0))
    check("Sat -> Mon", next_fire_local_ms(local(2, 12, 0), spec), local(4, 7, 0))
    check("Sun -> Mon", next_fire_local_ms(local(3, 12, 0), spec), local(4, 7, 0))
    check("Fri 06:00 -> Fri", next_fire_local_ms(local(1, 6, 0), spec), local(1, 7, 0))


def test_single_day_wraps_a_full_week():
    """Thursday-only, asked one minute after Thursday's slot."""
    spec = ScheduleSpec(minute_of_day=at(7), days_of_week=frozenset({THU}))
    check("Thu -> next Thu", next_fire_local_ms(local(0, 7, 1), spec), local(7, 7, 0))


def test_weekend_only():
    spec = ScheduleSpec(minute_of_day=at(9), days_of_week=WEEKEND)
    check("Thu -> Sat", next_fire_local_ms(local(0, 10, 0), spec), local(2, 9, 0))
    check("Sat early -> Sat", next_fire_local_ms(local(2, 8, 0), spec), local(2, 9, 0))
    check("Sat late -> Sun", next_fire_local_ms(local(2, 10, 0), spec), local(3, 9, 0))
    check("Sun late -> Sat", next_fire_local_ms(local(3, 10, 0), spec), local(9, 9, 0))


def test_all_seven_days_is_the_same_as_no_filter():
    everyday = ScheduleSpec(minute_of_day=at(7), days_of_week=frozenset(range(1, 8)))
    plain = ScheduleSpec(minute_of_day=at(7))
    for hour in (0, 6, 7, 8, 23):
        now = local(0, hour, 0)
        check(f"everyday@{hour}", next_fire_local_ms(now, everyday), next_fire_local_ms(now, plain))


def test_interval_aligns_to_local_midnight():
    spec = ScheduleSpec(interval_minutes=30)
    check("30m from 09:00", next_fire_local_ms(local(0, 9, 0), spec), local(0, 9, 30))
    check("30m from 09:01", next_fire_local_ms(local(0, 9, 1), spec), local(0, 9, 30))
    check("30m from 09:29", next_fire_local_ms(local(0, 9, 29), spec), local(0, 9, 30))
    check("30m from 09:30", next_fire_local_ms(local(0, 9, 30), spec), local(0, 10, 0))


def test_interval_crosses_midnight():
    spec = ScheduleSpec(interval_minutes=15)
    check("15m from 23:50", next_fire_local_ms(local(0, 23, 50), spec), local(1, 0, 0))


def test_interval_with_explicit_anchor():
    """Anchored at 09:07, every 20 minutes: 09:27, 09:47, 10:07…"""
    spec = ScheduleSpec(interval_minutes=20, anchor_local_ms=local(0, 9, 7))
    check("anchored", next_fire_local_ms(local(0, 9, 30), spec), local(0, 9, 47))
    check("anchored on grid", next_fire_local_ms(local(0, 9, 47), spec), local(0, 10, 7))


def test_interval_anchor_in_the_future_fires_at_the_anchor():
    spec = ScheduleSpec(interval_minutes=60, anchor_local_ms=local(3, 5, 0))
    check("future anchor", next_fire_local_ms(local(0, 9, 0), spec), local(3, 5, 0))


def test_interval_before_the_epoch_anchor():
    """Negative local millis exist (a device with a wrong clock). Floor
    division must not throw the grid off by one."""
    spec = ScheduleSpec(interval_minutes=30)
    check("negative now", next_fire_local_ms(local(-1, 9, 5), spec), local(-1, 9, 30))
    check("negative on grid", next_fire_local_ms(local(-1, 9, 30), spec), local(-1, 10, 0))


def test_interval_restricted_to_weekdays_skips_whole_days():
    # day 1 = Friday, day 2 Sat, day 3 Sun, day 4 Mon.
    spec = ScheduleSpec(interval_minutes=30, days_of_week=WEEKDAYS)
    check("Fri 23:45 -> Mon 00:00", next_fire_local_ms(local(1, 23, 45), spec), local(4, 0, 0))
    check("Sat noon -> Mon 00:00", next_fire_local_ms(local(2, 12, 0), spec), local(4, 0, 0))
    check("Fri 09:00 -> Fri 09:30", next_fire_local_ms(local(1, 9, 0), spec), local(1, 9, 30))


def test_interval_restricted_to_one_day_with_offset_anchor():
    """Monday only, every 7 hours, anchored 02:00 on day 0 (Thursday).
    7h does not divide 24h, so the grid drifts: the first point that lands on
    Monday (day 4) is 04:00, not the anchor's 02:00."""
    spec = ScheduleSpec(
        interval_minutes=7 * 60,
        days_of_week=frozenset({MON}),
        anchor_local_ms=local(0, 2, 0),
    )
    got = next_fire_local_ms(local(2, 12, 0), spec)  # Saturday noon
    check("mon-only grid", got, local(4, 4, 0))
    check("mon-only weekday", iso_weekday(got) if got else None, MON)
    # Still exactly on the anchor grid.
    check("mon-only on grid", (got - local(0, 2, 0)) % (7 * HOUR_MS), 0)


def test_invalid_specs_never_fire():
    check("empty spec", next_fire_local_ms(0, ScheduleSpec()), None)
    check("minute too big", next_fire_local_ms(0, ScheduleSpec(minute_of_day=1440)), None)
    check("negative minute", next_fire_local_ms(0, ScheduleSpec(minute_of_day=-1)), None)
    check("zero interval", next_fire_local_ms(0, ScheduleSpec(interval_minutes=0)), None)
    check("negative interval", next_fire_local_ms(0, ScheduleSpec(interval_minutes=-5)), None)
    check(
        "interval too long",
        next_fire_local_ms(0, ScheduleSpec(interval_minutes=MAX_INTERVAL_MINUTES + 1)),
        None,
    )


def test_garbage_days_refuse_rather_than_firing_daily():
    """A day filter that survives as the empty set must NOT silently become
    "every day" — that would fire an automation the user restricted."""
    spec = ScheduleSpec(minute_of_day=at(7), days_of_week=frozenset({0, 8, 99}))
    check("garbage days", next_fire_local_ms(local(0, 6, 0), spec), None)


def test_days_are_iso_numbered():
    check("epoch day is Thursday", iso_weekday(local(0)), THU)
    check("day 4 is Monday", iso_weekday(local(4)), MON)
    check("day 2 is Saturday", iso_weekday(local(2)), SAT)
    check("day 3 is Sunday", iso_weekday(local(3)), SUN)
    check("day -1 is Wednesday", iso_weekday(local(-1)), WED)


def test_epoch_wrapper_fixed_offset():
    """A plain UTC-5 zone: 07:00 local is 12:00 UTC."""
    zone = FixedOffsetZone(-5 * HOUR_MS)
    spec = ScheduleSpec(minute_of_day=at(7))
    now_epoch = local(0, 11, 0)  # 06:00 local
    got = next_fire_epoch_ms(now_epoch, spec, zone)
    check("fixed offset", got, local(0, 12, 0))
    check("fixed offset is in the future", got > now_epoch, True)


# The DST doubles. Winter UTC-5, summer UTC-4.
# Spring forward at local day 10 02:00 (02:00-02:59 never happens).
# Fall back at local day 100 02:00 (01:00-01:59 happens twice).
DST = TwoOffsetZone(
    winter_offset_ms=-5 * HOUR_MS,
    summer_offset_ms=-4 * HOUR_MS,
    spring_forward_local=local(10, 2, 0),
    fall_back_local=local(100, 2, 0),
)


def test_dst_spring_forward_gap_resolves_forward():
    """02:30 does not exist on the jump day; the alarm lands at 03:30 local."""
    spec = ScheduleSpec(minute_of_day=at(2, 30))
    now = DST.to_epoch(local(10, 1, 0))
    got = next_fire_epoch_ms(now, spec, DST)
    check("gap resolves forward", DST.to_local(got), local(10, 3, 30))
    check("gap is still in the future", got > now, True)


def test_dst_spring_forward_normal_time_is_unaffected():
    spec = ScheduleSpec(minute_of_day=at(7))
    now = DST.to_epoch(local(10, 1, 0))
    got = next_fire_epoch_ms(now, spec, DST)
    check("07:00 on the jump day", DST.to_local(got), local(10, 7, 0))


def test_dst_fall_back_fires_once_not_twice():
    """01:30 happens twice. Asked BEFORE the first, we fire at the first."""
    spec = ScheduleSpec(minute_of_day=at(1, 30))
    first = local(100, 1, 30) - DST.summer  # the earlier of the two instants
    now = first - 10 * MINUTE_MS
    got = next_fire_epoch_ms(now, spec, DST)
    check("fall back first pass", got, first)


def test_dst_fall_back_second_pass_moves_to_the_next_day():
    """Asked DURING the repeat, the naive candidate is in the past. The guard
    must roll to tomorrow rather than returning a past instant or firing the
    schedule a second time."""
    spec = ScheduleSpec(minute_of_day=at(1, 30))
    first = local(100, 1, 30) - DST.summer
    now = first + 30 * MINUTE_MS  # still local 01:xx, but the second time round
    got = next_fire_epoch_ms(now, spec, DST)
    check("fall back is not re-fired", got > now, True)
    check("fall back rolls to tomorrow", DST.to_local(got), local(101, 1, 30))


def test_epoch_wrapper_never_returns_the_past():
    """Whatever the zone does, the contract holds: strictly in the future."""
    spec = ScheduleSpec(minute_of_day=at(1, 30))
    for offset_min in range(0, 24 * 60, 17):
        now = DST.to_epoch(local(99, 0, 0)) + offset_min * MINUTE_MS
        got = next_fire_epoch_ms(now, spec, DST)
        if got is None or got <= now:
            raise AssertionError(f"epoch wrapper returned {got} for now={now} (+{offset_min}m)")


def test_interval_wrapper_survives_the_spring_gap():
    """Every 30 minutes across the jump: still strictly increasing, still 30
    minutes apart on the wall clock even though one step is a no-op in UTC."""
    spec = ScheduleSpec(interval_minutes=30)
    now = DST.to_epoch(local(10, 1, 0))
    seen = []
    for _ in range(6):
        nxt = next_fire_epoch_ms(now, spec, DST)
        if nxt is None or nxt <= now:
            raise AssertionError(f"interval wrapper stalled at {now} -> {nxt}")
        seen.append(DST.to_local(nxt))
        now = nxt
    check("interval walks the wall clock", seen[0], local(10, 1, 30))
    check("interval skips the gap", seen[1], local(10, 3, 0))
    check("interval continues", seen[2], local(10, 3, 30))


# --- structural check: the Kotlin still says the same thing -----------------

KOTLIN = (
    Path(__file__).resolve().parent.parent
    / "app/src/main/kotlin/ai/jarvis/app/automation/triggers/ScheduleCalculator.kt"
)

REQUIRED_IN_KOTLIN = [
    # the pure core and its wrapper must both still exist
    r"fun nextFireLocalMs\(",
    r"fun nextFireEpochMs\(",
    # strictly-future contract
    r"candidate > nowLocalMs",
    # ISO weekday derivation, 1970-01-01 = Thursday
    r"floorMod\(dayIndex\(localMs\) \+ 3, 7L\)",
    # the DST guard in the wrapper
    r"candidateEpoch > nowEpochMs",
    # a garbage day filter must refuse, not fire daily
    r"daysOfWeek\.isNotEmpty\(\) && days\.isEmpty\(\)",
]


def test_kotlin_source_still_matches():
    if not KOTLIN.exists():
        raise AssertionError(f"missing Kotlin source: {KOTLIN}")
    src = KOTLIN.read_text()
    problems = []
    if "import android." in src:
        problems.append("ScheduleCalculator.kt must stay free of Android imports")
    for pattern in REQUIRED_IN_KOTLIN:
        if not re.search(pattern, src):
            problems.append(f"ScheduleCalculator.kt no longer contains /{pattern}/")
    if problems:
        raise AssertionError("; ".join(problems))


# --- runner -----------------------------------------------------------------


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - a raising test is a failing test
            FAILURES.append(f"{t.__name__} raised {type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"FAIL  schedule_calc_test: {len(FAILURES)} problem(s) in {len(tests)} tests")
        for f in FAILURES:
            print("  -", f)
        return 1
    print(f"ok    schedule_calc_test: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
