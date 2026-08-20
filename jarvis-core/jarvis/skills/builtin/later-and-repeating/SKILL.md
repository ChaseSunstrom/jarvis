---
name: later-and-repeating
description: Use when the user wants something to happen at a time rather than now — a reminder, a look-up every morning, "do that again on Friday". Covers what can be scheduled and what has to be an automation instead.
license: Apache-2.0
---

# Later, and again

## The line that catches people out

`schedule_task` schedules **things you say and things you look up**. It does
not schedule actions on the house.

| they want | the answer |
|---|---|
| "remind me at seven" | `schedule_task` with `kind: notify` |
| "look up the weather every morning" | `schedule_task` with `kind: research` |
| "turn the lights off at eleven" | an **automation** — see `house-automations` |
| "email me the invoices on Fridays" | an **n8n workflow** — see `n8n-workflows` |

Reaching for the wrong one produces something that looks scheduled and never
fires, which is the worst outcome available. If the request is "do something
to the house at a time", it is a time trigger on an automation, and that
lives on the Automations page.

## Writing the schedule

One-off: `at`, an ISO timestamp **in their local time**. Work out the actual
date — "Friday" is not a timestamp, and "tomorrow at 7" depends on what today
is.

Repeating: `daily_at: "07:30"`, optionally with `days: ["mon", "tue", …]`.
Use `every_minutes` only for something genuinely periodic; a job every five
minutes is usually a sensor or an automation trigger in disguise.

## Say it back

Confirm the actual time, not their phrasing. "Every weekday at 7:30" tells
them you understood; "OK, scheduled" does not, and a reminder set for the
wrong day is only discovered by missing it.

Every firing shows up on the Tasks page, so "will I see it?" has an answer.

## Before adding another

`list_scheduled` when they ask what is set, and before adding something that
sounds familiar. Two identical morning briefings is a thing that happens and
that nobody notices until the second one arrives.

`cancel_scheduled` when they want one gone. If the wording is ambiguous about
which, list them and ask.
