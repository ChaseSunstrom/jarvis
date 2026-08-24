# Hooks: the ways something outside starts an automation

A hook is a trigger platform with a name. Everything here could be written as
`platform: event` against a raw bus event — all four were, and that is the
argument for naming them:

* `voice_pipeline_event` fires **fourteen times** in a normal voice run
  (`run-start`, `wake_word-start`, `wake_word-end`, four `stt-*`, three
  `intent-*`, two `tts-*`, `run-end`). An automation written against it ran
  fourteen times per "hey Jarvis" unless the author got a nested `event_data`
  filter exactly right — and nothing warned them when they did not.
* `jarvis_task_updated` fires on every progress tick, and a listener cannot see
  what the status was a moment ago. "Tell me when the research is done" became
  a notification per step.

The named platforms fire on the moment you meant, and their trigger variables
are the things an action actually reads out loud.

| Hook | Fires when | Filters |
|---|---|---|
| [`wake_word`](#wake_word) | a wake word is detected | `wake_word:`, `pipeline:`, `device_id:` |
| [`task`](#task) | a background job starts, completes, fails or is cancelled | `status:`, `kind:`, `source:` |
| [`webhook`](#webhook) | something POSTs to `/api/webhook/<id>` | `webhook_id:`, `allowed_methods:` |
| [`time` / `time_pattern`](#schedules) | a wall-clock moment, or every N | `at:`, `hours:`, `minutes:`, `seconds:` |
| [`event`](#event) | anything on the bus | `event_type:`, `event_data:` (dotted) |

A worked example of each: [`config/examples/hooks.yaml`](../config/examples/hooks.yaml).

---

## `wake_word`

```yaml
trigger:
  - platform: wake_word
    device_id: workshop          # optional — which satellite
    wake_word: [hey_jarvis, ok_nabu]   # optional — a list means any of these
    pipeline: night              # optional — which pipeline was running
```

Fires **once**, on `wake_word-end`, which is the moment detection succeeded.

Trigger variables: `wake_word`, `device_id`, `pipeline`, `run_id`.

`device_id` is the id a satellite registered with over the websocket. A run
that did not come from a satellite — a browser tab, a REST call, a test —
carries an empty one, and an empty id never matches a named device. So
`device_id: workshop` means the workshop and nothing else, including nothing
anonymous.

What it does not do: it cannot tell you *who* said it (that is the speaker
gate, and it runs later in the pipeline), and it does not fire for a text turn,
because nothing was listening for a word.

## `task`

```yaml
trigger:
  - platform: task
    status: completed        # started | completed | failed | cancelled
    kind: research           # optional — background | research | code | …
    source: assistant        # optional — who asked for it
```

Four distinct bus events sit under this: `jarvis_task_started`,
`jarvis_task_completed`, `jarvis_task_failed`, `jarvis_task_cancelled`. They
are fired on the **transition** — the update where the status actually changed
— and in addition to `jarvis_task_updated`, never instead of it (the console
redraws from the updates).

Trigger variables: `status`, `task_id`, `kind`, `title`, `result`, `error`, and
the whole `task` dict.

Omitting `status:` hears all four. A status you invented logs a warning and
falls back to all four rather than to none: a hook that fires too often is
visible, one that never fires is not.

**Cancelled is not failed.** Somebody asked the job to stop and it stopped;
paging a human about that is noise. If you want to hear about both, say
`status: [failed, cancelled]`.

## `webhook`

```yaml
trigger:
  - platform: webhook
    webhook_id: 8f1c2a-doorbell-2b7e
    allowed_methods: [POST]    # optional; GET/POST/PUT/HEAD are accepted
```

```bash
curl -X POST http://jarvis.local:8123/api/webhook/8f1c2a-doorbell-2b7e \
     -H 'content-type: application/json' -d '{"pressed": true}'
```

Trigger variables: `webhook_id`, `json`, `data`, `query`, `method`.

**The id is the secret.** `/api/webhook/<id>` is an open route by design —
that is what lets a doorbell, a NAS or an IFTTT applet reach it without holding
a token. So make the id long and random, and treat it as a credential.

Two things narrow that:

* An id nobody registered is a 404, not a silent 200. Probing for ids does not
  read as success.
* `jarvis: webhook_require_auth: true` in `configuration.yaml` makes the route
  demand a bearer token as well as the id. It closes the open route to anything
  that cannot hold a token — which includes most of the devices webhooks exist
  for, so it is off by default and yours to turn on.

## Schedules

```yaml
trigger:
  - platform: time
    at: "03:15:00"           # a wall-clock moment
  - platform: time_pattern
    minutes: "/10"           # every ten minutes
```

Both read the zone in `jarvis: time_zone:`, not the process's — a box in UTC
running a house in Kathmandu still fires `at: "07:00:00"` at seven in the
morning, locally. `time_pattern` accepts `hours:`, `minutes:` and `seconds:`,
each `N` (exactly), `/N` (every N) or `*`.

A schedule is not a task queue: the action runs when it fires, and if the house
was off it does not catch up. For work that must happen even if it is late, put
it on a task and check `jarvis/tasks`.

## `event`

```yaml
trigger:
  - platform: event
    event_type: delivery
    event_data:
      parcel.carrier: [royal_mail, dhl]   # dotted path, list = any of these
```

The escape hatch for anything without a name of its own — and `event_data`
keys may be **dotted paths** into nested payloads (`steps.0.status` indexes a
list). Every interesting payload on this bus is nested, so a matcher that could
only read top-level keys could match `run_id` and little else; and writing the
whole nested dict out in YAML would be an exact match against a shape that
grows keys.

A path that does not exist does not match, and does not raise.

## Testing

```bash
cd jarvis-core && python3 -m pytest tests/test_hooks.py -q
```

Each platform is tested for the case it fires, the case a filter excludes it,
and the variables it carries — including the two failures that motivated the
named platforms: a wake-word hook must not fire on the other thirteen pipeline
events, and a task hook must not fire again on the ten updates after the one
that finished it.
