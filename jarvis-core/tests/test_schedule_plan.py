"""When a scheduled job next runs, and what happens to the ones that did not.

Its own file because the arithmetic is the feature, and two parts of it are
wrong in ways that only show up on the day they matter: a clock change, and a
restart.

Jarvis is a box on a shelf. It loses power, it gets rebuilt, its container
restarts at four in the afternoon — so "the job was due while nothing was
running" is the ordinary case. Firing the backlog and silently skipping are
both wrong, and which one is less wrong depends on what the job IS.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.schedule.plan import (  # noqa: E402
    DEFAULT_GRACE_SECONDS,
    MIN_EVERY_MINUTES,
    When,
    catch_up,
    describe_when,
    next_fire,
    parse_when,
)

LONDON = ZoneInfo("Europe/London")


def at(text: str, tz=LONDON) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=tz)


# --- reading a `when` ------------------------------------------------------------

def test_a_bare_timestamp_is_a_one_shot():
    when = parse_when("2026-01-02T07:00:00")
    assert when is not None and when.mode == "once"
    assert not when.recurring


def test_the_three_spellings_of_seven_o_clock_all_work():
    # A model asked for "HH:MM" writes all three about equally often.
    for text in ("07:00", "7:00", "07:00:00"):
        when = parse_when({"mode": "daily", "at": text})
        assert when is not None and when.at == "07:00", text


def test_a_mode_is_inferred_when_the_payload_only_makes_sense_one_way():
    assert parse_when({"minutes": 30}).mode == "every"
    assert parse_when({"days": ["mon"], "at": "09:00"}).mode == "weekly"
    assert parse_when({"at": "09:00"}).mode == "daily"


def test_a_repeat_faster_than_the_floor_is_refused():
    # Anything faster is a poll, and a poll belongs in a `time_pattern`
    # automation where it is not also minting a task each time.
    assert parse_when({"mode": "every", "minutes": 1}) is None
    assert parse_when({"mode": "every", "minutes": MIN_EVERY_MINUTES}) is not None


def test_nonsense_is_refused_rather_than_defaulted():
    assert parse_when({"mode": "daily", "at": "25:00"}) is None
    assert parse_when({"mode": "daily", "at": "soon"}) is None
    assert parse_when({"mode": "weekly", "at": "09:00", "days": ["funday"]}) is None
    assert parse_when({"mode": "once"}) is None
    assert parse_when(None) is None


def test_a_weekday_is_read_however_it_was_spelled():
    when = parse_when({"mode": "weekly", "at": "09:00", "days": ["Monday", "WED", "fri"]})
    assert when.days == ["mon", "wed", "fri"]


# --- the next firing -------------------------------------------------------------

def test_daily_lands_on_the_next_one():
    when = When(mode="daily", at="07:00")
    assert next_fire(when, at("2026-01-01T06:00")) == at("2026-01-01T07:00")
    # Already past today, so tomorrow.
    assert next_fire(when, at("2026-01-01T08:00")) == at("2026-01-02T07:00")


def test_a_firing_exactly_now_counts_as_past():
    # Otherwise a tick that lands precisely on the second re-fires for ever.
    when = When(mode="daily", at="07:00")
    assert next_fire(when, at("2026-01-01T07:00")) == at("2026-01-02T07:00")


def test_weekly_skips_to_a_day_it_wants():
    when = When(mode="weekly", at="09:00", days=["mon"])
    # 2026-01-01 is a Thursday.
    assert next_fire(when, at("2026-01-01T10:00")) == at("2026-01-05T09:00")


def test_weekly_can_be_several_days():
    when = When(mode="weekly", at="09:00", days=["mon", "thu"])
    assert next_fire(when, at("2026-01-01T10:00")) == at("2026-01-05T09:00")
    assert next_fire(when, at("2026-01-05T10:00")) == at("2026-01-08T09:00")


@pytest.mark.parametrize(
    "before,day,real_hours",
    [
        # Clocks forward at 01:00 on 29 March 2026: from 08:00 GMT to 07:00 BST
        # is 22 hours of real time, not 24.
        ("2026-03-28T08:00", 29, 22),
        # Clocks back at 02:00 on 25 October 2026.
        ("2026-10-24T08:00", 25, 24),
    ],
)
def test_seven_o_clock_stays_seven_o_clock_across_a_clock_change(before, day, real_hours):
    """The bug that is wrong twice a year and gets blamed on something else.

    A job set for 07:00 must run at 07:00 on both sides of a clock change. The
    elapsed REAL time between two consecutive 07:00s is not 86400 seconds
    across a boundary, so a schedule advanced by that many seconds lands an
    hour out — and "the alarm went off at six today" is not a bug anybody
    attributes to a scheduler.

    Measured on `.timestamp()` deliberately: subtracting two aware datetimes
    that share a `tzinfo` gives the WALL-CLOCK difference, because Python
    ignores the common zone. An earlier version of this test did that and was
    asserting nothing about elapsed time at all.
    """
    when = When(mode="daily", at="07:00")
    start = at(before)
    nxt = next_fire(when, start)
    assert nxt.hour == 7 and nxt.day == day
    assert (nxt.timestamp() - start.timestamp()) == pytest.approx(real_hours * 3600)


def test_the_offset_really_does_change_across_the_boundary():
    # Otherwise the test above would pass on a zone with no DST at all, and
    # prove nothing.
    assert at("2026-03-28T08:00").utcoffset() != at("2026-03-29T07:00").utcoffset()


def test_a_one_shot_with_no_zone_is_read_as_local():
    """A bare timestamp is what a person and a model both write.

    Reading it as UTC moves it by however far the house is from Greenwich —
    which is nothing in London in January, and an hour in July. A bug that is
    correct half the year in one city is the worst kind to find.
    """
    when = When(mode="once", at="2026-07-01T19:00:00")
    fired = next_fire(when, at("2026-07-01T10:00"))
    assert fired.hour == 19
    assert fired.utcoffset() == timedelta(hours=1)  # BST


def test_a_one_shot_that_names_its_zone_is_believed():
    when = When(mode="once", at="2026-07-01T19:00:00+00:00")
    fired = next_fire(when, at("2026-07-01T10:00"))
    assert fired.utcoffset() == timedelta(0)


def test_every_counts_from_now_not_from_a_grid():
    when = When(mode="every", minutes=30)
    assert next_fire(when, at("2026-01-01T06:07")) == at("2026-01-01T06:37")


# --- coming back after being off --------------------------------------------------

def test_a_job_still_in_the_future_simply_waits():
    when = When(mode="daily", at="07:00")
    decision = catch_up(when, at("2026-01-02T07:00"), at("2026-01-01T09:00"))
    assert decision.fire is False
    assert decision.next_at == at("2026-01-02T07:00")


def test_a_reminder_twenty_minutes_late_is_still_the_reminder():
    when = When(mode="once", at="2026-01-01T19:00:00")
    decision = catch_up(when, at("2026-01-01T19:00"), at("2026-01-01T19:20"))
    assert decision.fire is True
    assert decision.next_at is None  # a one-shot has no next


def test_a_reminder_a_day_late_is_not_delivered_at_three_in_the_morning():
    """"Take the bins out", spoken at 03:00 the next day, is worse than silence.

    And the user has to be TOLD, because the alternative — it simply never
    happened — is indistinguishable from the feature being broken.
    """
    when = When(mode="once", at="2026-01-01T19:00:00")
    decision = catch_up(when, at("2026-01-01T19:00"), at("2026-01-02T03:00"))
    assert decision.fire is False
    assert decision.next_at is None
    assert decision.skipped == 1
    assert "not running" in decision.missed_reason


def test_the_grace_window_is_the_line_and_it_is_configurable():
    when = When(mode="once", at="2026-01-01T19:00:00")
    due, inside = at("2026-01-01T19:00"), at("2026-01-01T19:59")
    assert catch_up(when, due, inside, grace_seconds=3600).fire is True
    assert catch_up(when, due, inside, grace_seconds=1800).fire is False


def test_a_recurring_job_off_for_two_days_does_not_come_back_and_run_forty_eight_times():
    """The other wrong answer, and the loud one.

    Hourly, off for two days. Firing the backlog means forty-eight runs, every
    one of them late — and if the job speaks, the house starts talking to
    itself.
    """
    when = When(mode="every", minutes=60)
    due = at("2026-01-01T00:00")
    now = at("2026-01-03T00:00")
    decision = catch_up(when, due, now)
    assert decision.fire is False, "a two-day backlog fired"
    assert decision.skipped > 40
    assert decision.next_at > now


def test_a_recurring_job_missed_by_minutes_runs_once_and_moves_on():
    # The common case: a restart. One run, not none, and not a backlog.
    when = When(mode="daily", at="07:00")
    decision = catch_up(when, at("2026-01-01T07:00"), at("2026-01-01T07:04"))
    assert decision.fire is True
    assert decision.skipped == 0
    assert decision.next_at == at("2026-01-02T07:00")


def test_what_was_skipped_is_counted_so_somebody_can_see_it():
    # An absence you have to infer is not information.
    when = When(mode="daily", at="07:00")
    decision = catch_up(when, at("2026-01-01T07:00"), at("2026-01-04T09:00"))
    assert decision.fire is False
    assert decision.skipped == 4
    assert decision.next_at == at("2026-01-05T07:00")


def test_a_job_off_for_a_year_does_not_take_a_hundred_thousand_steps():
    # Every five minutes, off for a year. The arithmetic must not become the
    # outage.
    when = When(mode="every", minutes=5)
    decision = catch_up(when, at("2025-01-01T00:00"), at("2026-01-01T00:00"))
    assert decision.next_at > at("2026-01-01T00:00")
    assert decision.fire is False


def test_a_job_with_no_schedule_yet_gets_one():
    when = When(mode="daily", at="07:00")
    decision = catch_up(when, None, at("2026-01-01T09:00"))
    assert decision.fire is False
    assert decision.next_at == at("2026-01-02T07:00")


# --- what it says ------------------------------------------------------------------

def test_a_schedule_reads_as_a_sentence():
    assert describe_when(When(mode="daily", at="07:00")) == "every day at 07:00"
    assert describe_when(When(mode="weekly", at="09:00", days=["mon", "fri"])) == (
        "Mon, Fri at 09:00"
    )
    assert describe_when(When(mode="every", minutes=30)) == "every 30 minutes"
    assert describe_when(When(mode="every", minutes=120)) == "every 2 hours"
    assert describe_when(When(mode="every", minutes=60)) == "every 1 hour"
    assert "once" in describe_when(When(mode="once", at="2026-01-01T19:00:00"))


def test_the_default_grace_is_long_enough_for_a_reboot_and_no_longer():
    # Six hours covers a power cut and a container rebuild. It does not cover
    # "yesterday", which is the point.
    assert 3600 <= DEFAULT_GRACE_SECONDS <= 12 * 3600
