---
name: homelab-status
description: Answering "how is the homelab doing" from the recorded measurements rather than from a guess.
allowed-tools: [metrics_query, get_state, list_entities, recent_events]
metadata:
  author: Jarvis
  permissions: [read_state]
version: "1"
---

# Homelab status

## Where the numbers are

`metrics_query` reads the same time series the dashboards draw. Call it with no
`keys` first and it tells you what this house actually records — do that rather
than guessing at a name, because a key that does not exist comes back as an
error and not as a zero.

Two stores, and the difference matters:

* `internal` — what Jarvis itself has seen this session. Short, always there.
* `influx` — the long-term database, when the operator has configured one.
  Weeks of history; this is the one that can answer "is it worse than last
  week".

`get_state` is the reading NOW. `metrics_query` is the reading over TIME.
"Is the loft hot?" is the first. "Is the loft getting hotter?" is the second,
and answering it from a single current reading is the mistake this skill
exists to stop.

## What a status answer is

Spoken, one or two sentences, worst thing first:

> Everything is up. The loft is 31°, seven degrees above this time yesterday.

Not a list of every series that was fine. Nobody asked for an inventory.

Name the number and the change: "31°, up seven" tells somebody whether to go up
there. "The loft is warm" does not.

## What not to do

Do not invent a threshold. If nothing in the house defines what "too hot" is,
report the number and say it is higher than usual — do not announce a problem
against a limit you made up.

Do not average a gap. A series with no samples for two hours has a gap, and
`metrics_query` returns the sample count for exactly this reason: five samples
over six hours is not a trend, it is a sensor that stopped reporting, and
**that** is the thing worth saying.

Do not restart, redeploy or power-cycle anything as part of answering. This
skill reads. If something is down, say what is down and what you would do about
it — the doing is a separate request, from a person.
