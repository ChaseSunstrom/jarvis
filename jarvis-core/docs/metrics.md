# Metrics and dashboards

Where a graph's numbers come from, and how a dashboard is stored.

## The shape

A widget asks one question — *this series, over this window* — and must not care
who answers it. So every source implements the same three methods
(`jarvis/metrics/__init__.py`):

```python
source.list_series()                  # what can be graphed
await source.query(keys, window)      # the points
await source.healthy()                # (False, "why not") shows in the picker
```

Two rules the shape exists to enforce:

**A source never invents a point.** A window with nothing recorded returns a gap
(`None`), not a zero, and the console breaks the line there. A chart that cannot
tell "nothing happened" from "nothing was recorded" is worse than no chart.

**Every source is local.** `metrics: sources:` in `configuration.yaml` is the
whole list of what Jarvis may reach for a number.

## `internal` — always there, needs nothing

* `entity.<entity_id>` — the recorder's history for any numeric entity.
* `host.load1` · `host.load5` · `host.load15` · `host.memory_used` ·
  `host.memory_percent` · `host.disk_free` — from `/proc` and `statvfs`.
* `jarvis.turns` · `jarvis.tool_calls` · `jarvis.tool_ms` ·
  `jarvis.tasks_started` · `jarvis.tasks_failed` · `jarvis.first_token_ms` —
  counted off the bus into bounded rings. A restart loses them, which is the
  honest trade for not writing a second database: what deserves to survive a
  restart is a state, and a state belongs in the recorder.

## `influx` — an InfluxDB you already run

```yaml
metrics:
  sources:
    influx:
      url: !env_var INFLUX_URL http://127.0.0.1:8086
      token: !env_var INFLUX_TOKEN
      org: !env_var INFLUX_ORG
      bucket: !env_var INFLUX_BUCKET homelab
```

Jarvis works out which InfluxDB it is talking to rather than asking you:
`/health` answers with a version on 2.x and 3.x, `/ping` carries
`X-Influxdb-Version` on 1.x. It then speaks Flux or InfluxQL accordingly, asks
the server for the schema (so you do not write it out), and reads only — there
is no write path, and a source that could write would need a permission story
nothing in a dashboard wants.

A series key is `measurement.field`. The token travels in an `Authorization`
header and never in a URL, because a token in a query string ends up in every
proxy log there is.

Check yours:

```bash
python3 scripts/check-influx.py                 # uses INFLUX_* from the environment
python3 scripts/check-influx.py http://nas:8086 --bucket homelab
```

## Dashboards

A layout is a list of widgets on a twelve-column grid; the shape is
`tests/contracts/dashboard_layout.json`, which jarvis-core's tests and the
console's both read.

There are no user accounts here — `auth.py` says so in its first line — so a
token is the identity, and a dashboard belongs to the token that saved it. One
token can neither read nor overwrite another's. A board with no owner is shared;
the examples in `<config>/dashboards/*.yaml` are shipped, read-only and appear
for everybody.

Six chart types: `line`, `area`, `bar`, `stat`, `gauge`, `table`. Picking one is
picking a question, which is why the picker shows what each is for rather than
only its name.
