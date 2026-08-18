"""When a scheduled job next runs, and what to do about the ones that did not.

Pure: datetimes in, datetimes out, no store and no event loop. The arithmetic
here is the whole feature, and two parts of it are wrong in ways nobody notices
until the day they matter.

## Missing a fire is not a rounding error

Jarvis is a thing on a shelf in a house. It gets restarted, it loses power, its
container gets rebuilt at four in the afternoon. So "the job was due while
nothing was running" is the ordinary case, not the exotic one, and there are
exactly two wrong answers:

**Fire the backlog.** A job set for every hour, off for two days, comes back and
runs forty-eight times. Every one of them is late, and if the job speaks, the
house starts talking to itself.

**Silently skip.** A one-shot reminder for 7pm, missed because the box rebooted
at 6:55, vanishes. Nobody is told. The user finds out by the thing not happening.

So the policy is split by what the job IS, in :func:`catch_up`:

* a **one-shot** fires late, once, if it is within the grace window — a reminder
  twenty minutes late is still the reminder — and is marked MISSED beyond it,
  because "take the bins out" delivered at three in the morning is worse than
  not delivered;
* a **recurring** job never fires a backlog. It skips to its next occurrence and
  counts what it skipped, so the count is a fact somebody can see rather than
  an absence they have to infer.

## The zone is where the user is

`daily at 07:00` means seven o'clock where the house is, on both sides of a
clock change. That is why every calculation here takes an aware `datetime` and
rebuilds the next fire with `replace()` on it rather than adding 86400 seconds:
across a DST boundary those two differ by an hour, and the second one is wrong
twice a year in a way that looks like a bug in something else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

__all__ = [
    "DEFAULT_GRACE_SECONDS",
    "MIN_EVERY_MINUTES",
    "Decision",
    "When",
    "catch_up",
    "describe_when",
    "next_fire",
    "parse_when",
]

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: How late a ONE-SHOT may be and still run. Six hours covers a reboot, a power
#: cut and a container rebuild; it does not cover "yesterday".
DEFAULT_GRACE_SECONDS = 6 * 3600

#: The shortest repeat. Anything faster is a poll, and a poll belongs in an
#: automation's `time_pattern` where it is not also minting a task each time.
MIN_EVERY_MINUTES = 5
MAX_EVERY_MINUTES = 60 * 24 * 7

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


@dataclass
class When:
    """When a job runs. One of four modes, and only one is recurring-free."""

    #: "once" | "daily" | "weekly" | "every"
    mode: str = "once"
    #: For `once`: an ISO timestamp. For `daily`/`weekly`: "HH:MM".
    at: str = ""
    #: For `weekly`: which days, as three-letter names.
    days: list[str] = field(default_factory=list)
    #: For `every`: the gap in minutes.
    minutes: int = 0

    @property
    def recurring(self) -> bool:
        return self.mode != "once"

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "at": self.at, "days": list(self.days), "minutes": self.minutes}


def parse_when(raw: Any) -> When | None:
    """Read a `when` out of an API payload or the store. None if unusable.

    Deliberately strict about the shape and forgiving about the spelling: a
    model asked for `{"mode": "daily", "at": "7:00"}` writes `"07:00"`, `"7:00"`
    and `"07:00:00"` about equally often, and all three mean the same thing.
    """
    if isinstance(raw, str):
        raw = {"mode": "once", "at": raw}
    if not isinstance(raw, dict):
        return None

    mode = str(raw.get("mode") or "").strip().lower()
    at = str(raw.get("at") or raw.get("time") or "").strip()
    if not mode:
        # Inferred, because a payload with only `minutes` or only `days` plainly
        # means one thing, and rejecting it teaches nobody anything.
        if raw.get("minutes"):
            mode = "every"
        elif raw.get("days"):
            mode = "weekly"
        elif _HHMM.match(at):
            mode = "daily"
        else:
            mode = "once"

    if mode == "every":
        try:
            minutes = int(raw.get("minutes") or raw.get("every") or 0)
        except (TypeError, ValueError):
            return None
        if minutes < MIN_EVERY_MINUTES or minutes > MAX_EVERY_MINUTES:
            return None
        return When(mode="every", minutes=minutes)

    if mode in ("daily", "weekly"):
        match = _HHMM.match(at)
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        days: list[str] = []
        if mode == "weekly":
            for day in raw.get("days") or []:
                name = str(day).strip().lower()[:3]
                if name in WEEKDAYS and name not in days:
                    days.append(name)
            if not days:
                return None
        return When(mode=mode, at=f"{hour:02d}:{minute:02d}", days=days)

    if not at:
        return None
    return When(mode="once", at=at)


def _parse_iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def next_fire(when: When, after: datetime) -> datetime | None:
    """The first firing strictly after `after`, in `after`'s own zone.

    `after` must be aware. The zone it carries is the zone `07:00` means, which
    is why the caller passes a clock that honours `jarvis: time_zone:` rather
    than whatever the container was started with.
    """
    if when.mode == "once":
        moment = _parse_iso(when.at)
        if moment is None:
            return None
        # A bare local timestamp is what a person and a model both write.
        # Reading it as UTC would move it by however far the house is from
        # Greenwich, which is the kind of wrong that is right in London.
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=after.tzinfo)
        return moment

    if when.mode == "every":
        return after + timedelta(minutes=max(MIN_EVERY_MINUTES, when.minutes))

    match = _HHMM.match(when.at)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))

    # `replace` on the aware local datetime, then step by whole DAYS. Adding
    # 86400 seconds instead would drift by an hour across a clock change, and
    # "the alarm went off at six today" is a bug nobody attributes to this.
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)

    if when.mode == "daily":
        return candidate

    wanted = set(when.days)
    for _ in range(8):
        if WEEKDAYS[candidate.weekday()] in wanted:
            return candidate
        candidate += timedelta(days=1)
    return None


@dataclass
class Decision:
    """What to do with a job whose next firing is in the past."""

    #: Run it now.
    fire: bool
    #: Where its next firing goes. None means it is finished (a spent one-shot)
    #: or unschedulable.
    next_at: datetime | None
    #: Firings that were due while nothing was running and will NOT happen.
    skipped: int = 0
    #: Set when the job will never run again and somebody should be told why.
    missed_reason: str = ""


def catch_up(
    when: When,
    due: datetime | None,
    now: datetime,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> Decision:
    """Reconcile a job's schedule with a clock that has moved on without it.

    Called on load and on every tick, so it is also the ordinary "is it time
    yet" question — `due` in the future simply answers no.
    """
    if due is None:
        return Decision(fire=False, next_at=next_fire(when, now))

    if due > now:
        return Decision(fire=False, next_at=due)

    late = (now - due).total_seconds()

    if not when.recurring:
        if late <= grace_seconds:
            # Late but still the reminder. Fire once; there is no "next".
            return Decision(fire=True, next_at=None)
        return Decision(
            fire=False,
            next_at=None,
            skipped=1,
            missed_reason=(
                f"it was due {_ago(late)} ago, while Jarvis was not running, and "
                "is too old to be useful now"
            ),
        )

    # Recurring. Walk forward to the next future firing, counting what is being
    # skipped rather than running any of it. Bounded, because a job set to every
    # five minutes and off for a year is 100,000 steps of arithmetic nobody is
    # waiting for.
    skipped = 0
    cursor = due
    for _ in range(MAX_CATCH_UP_STEPS):
        nxt = next_fire(when, cursor)
        if nxt is None:
            return Decision(fire=False, next_at=None, skipped=skipped)
        cursor = nxt
        if cursor > now:
            break
        skipped += 1
    else:
        # Ran out of steps: jump to the first firing after now rather than
        # leaving a job stuck in the past for ever.
        cursor = next_fire(when, now) or now

    # The most recent missed firing runs, if it is recent enough. One, not the
    # backlog: a job off for two days must not come back and speak forty-eight
    # times.
    fire = late <= grace_seconds
    return Decision(
        fire=fire,
        next_at=cursor,
        skipped=skipped if fire else skipped + 1,
    )


MAX_CATCH_UP_STEPS = 5000


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def describe_when(when: When) -> str:
    """One line a person reads. Shown on the console and spoken back."""
    if when.mode == "every":
        minutes = when.minutes
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"every {hours} hour{'' if hours == 1 else 's'}"
        return f"every {minutes} minutes"
    if when.mode == "daily":
        return f"every day at {when.at}"
    if when.mode == "weekly":
        names = ", ".join(d.capitalize() for d in when.days)
        return f"{names} at {when.at}"
    moment = _parse_iso(when.at)
    return f"once, at {moment.strftime('%d %b %H:%M')}" if moment else "once"
