# Configuration reference

Everything lives in `config/configuration.yaml`, one top-level key per
integration. The shipped file is a working example of all of it; this is the
key-by-key reference.

Restart to apply: `docker compose restart jarvis-core`. Automations, scripts
and scenes also reload in place via `automation.reload`, `script.reload` and
`scene.reload`.

## Loader syntax

Home Assistant compatible, so existing YAML pastes in.

| Tag | Does |
|---|---|
| `!secret name` | Pulls `name` from `secrets.yaml` (same directory). |
| `!env_var NAME default` | Reads the environment. Missing and no default is a startup error. |
| `!include file.yaml` | Splices that file in at this point. |
| `!include_dir_named dir/` | `{filename-without-.yaml: contents}` — used for `packages:`. |
| `!include_dir_merge_named dir/` | Merges every file's top-level keys into one mapping. |
| `!include_dir_list dir/` | A list, one entry per file. |
| `!include_dir_merge_list dir/` | Concatenates every file's list. |

`!secret` takes whole values only; it cannot be spliced into the middle of a
string. For `Authorization: Token abc123`, store the entire
`"Token abc123"` in `secrets.yaml`.

`secrets.yaml` is optional. The shipped configuration references no secrets, so
a fresh checkout starts with nothing to fill in. Copy `secrets.yaml.example`
when you add your first credential.

**One exception to all of this:** `*.tool.yaml` files in `tools_dir` are read
with a plain YAML parser, so `!secret` and `!env_var` do not work there. A tool
that needs a credential goes in the inline `llm: tools:` block in
`configuration.yaml` instead, which is loaded with the full loader.

## `jarvis:` — the core

Not an integration. Identity, location, and the HTTP server.

| Key | Default | Notes |
|---|---|---|
| `name` | `Jarvis` | Reported as `location_name` on `/api/config`. |
| `latitude` `longitude` | — | Used by `sun:` and by `person:` to decide home vs away. |
| `elevation` | `0` | Metres above sea level. Affects sunrise/sunset times. |
| `radius` | `100` | Metres from the coordinates that still counts as home. |
| `time_zone` | system | IANA name. Keep it in step with `TZ` in `docker-compose.yml`. |
| `unit_system` | `metric` | |
| `currency` `country` | — | Reported to clients. |
| `log_level` | `info` | `debug` `info` `warning` `error` `critical`. `-v` on the command line overrides it. |
| `http: host` | `0.0.0.0` | |
| `http: port` | `8080` | `8123` makes Jarvis a drop-in for anything already pointed at Home Assistant. |
| `cors_allowed_origins` | `["*"]` | Narrow it to your HUD's origin in production. |
| `webhook_require_auth` | `false` | `true` demands a bearer token on `/api/webhook/{id}` as well as the unguessable id. |
| `areas` | `[]` | Rooms. Strings, or `{name:, aliases: []}`. Aliases are what make "turn off the lounge lights" work. |

`--host`, `--port` and `--log-level` on the command line beat this file.

There is also HA's `logger:` block for per-module levels:

```yaml
logger:
  default: info
  logs:
    jarvis.integrations.mqtt: debug
    jarvis.llm.agent: debug
```

## `recorder:` — history storage

One SQLite file. Everything `history:` and `logbook:` read comes from here.

| Key | Default | Notes |
|---|---|---|
| `db_file` | `jarvis.db` | Relative paths resolve under the config directory. `db_url: sqlite:///abs/path.db` is equivalent. |
| `purge_keep_days` | `10` | |
| `commit_interval` | `5` | Seconds. Writes are queued and flushed in one transaction, so a burst of state changes costs one commit rather than hundreds. Raising it costs you the last N seconds on a hard power cut. |
| `auto_purge` | `true` | Nightly at 04:12 local. |
| `exclude:` / `include:` | — | `domains`, `entities`, `entity_globs`, `event_types`. |

Filter precedence, most specific first: `exclude.entities` →
`include.entities` → `entity_globs` → `exclude.domains` → `include.domains`.
With no `include:` block, everything not excluded is recorded.

Exclude the chatty diagnostics. `sensor.*_rssi`, `*_linkquality` and `*_uptime`
update constantly, are never queried, and are how a home database reaches
several gigabytes and starts eating SD-card write cycles.

Services: `recorder.purge` (`keep_days`, `repack`), `recorder.purge_entities`
(`entity_id`, `domains`).

## `history:` and `logbook:`

```yaml
history:
  days: 7                 # default window when a caller gives no start/end

logbook:
  max_entries: 5000       # in-memory ring buffer
  log_service_calls: true
  log_unavailable: false
  exclude: {domains: [sensor]}
  include: {domains: [light, lock, person]}
```

`history:` is a pure query layer and pulls in `recorder` as a dependency.
Services: `history.get`, `history.stats` (min/max/mean/first/last/changes) —
both return responses, so an LLM tool or a REST caller gets data back directly.

`logbook:` turns raw events into readable lines ("Front Door was opened",
"Bedtime routine started"). The ring buffer works with no database at all;
with `recorder` present, older entries are reconstructed on demand and merged.
Services: `logbook.log` (`name`, `message`, `entity_id`), `logbook.get`,
`logbook.clear`.

## `sun:`

```yaml
sun:
  update_interval: 60     # seconds
```

Creates `sun.sun` (`above_horizon` / `below_horizon`) with `next_rising`,
`next_setting`, `next_dawn`, `next_dusk`, `next_noon`, `next_midnight`,
`elevation`, `azimuth` and `rising`. Location comes from `jarvis:`, and can be
overridden here.

There is no `sun` trigger platform. Trigger on the entity:

```yaml
trigger:
  - platform: state
    entity_id: sun.sun
    to: below_horizon
```

For an offset, use a template condition on `next_setting`.

## `sky:`

```yaml
sky:
  tle_cache: sky/tle            # under the config dir; drop your own .csv/.tle here
  ephemeris: sky/de421.bsp      # under the config dir; downloaded once when absent
  refresh_hours: 24             # re-fetch elements older than this; never under 2
  min_altitude: 10              # degrees; a pass begins and ends here
  update_interval: 300          # seconds between entity recomputes
  satellites: [ISS (ZARYA)]     # tracked: one `sky.<name>_next_pass` entity each
  sources:
    - https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=csv
  download: true                # false: never touch the network
```

The next ISS pass for the house, what is overhead now, the moon's phase and
the planets tonight, computed here with skyfield. Location comes from
`jarvis:`, times are in its `time_zone`. Two downloads, both cached under the
config directory: orbital elements from CelesTrak (a few KB, refreshed when
older than `refresh_hours`; never fetched more often than every two hours,
which is CelesTrak's own cycle) and the planetary ephemeris (17 MB, once).
Neither happens on the way to start-up, and neither failing stops anything —
the cached elements keep serving with their age in every answer, and without
the ephemeris the satellite tools still work while the moon and the planets
say the file is not there yet. `download: false` is for an air-gapped box:
put a CSV (or a TLE) in `tle_cache` and the ephemeris in place by hand.

Entities: `sky.iss_next_pass` (state: when the next pass above `min_altitude`
rises, in the house zone; attributes `max_alt`, `direction`, `visible`,
`rise_direction`, `culmination`, `set`, `set_direction`, `next_visible`,
`tle_age_hours`, `elements_age_days`) and `sky.moon` (state: the phase name;
attributes `illumination`, `phase_angle`, `waxing`, `next_full`, `next_new`).

Tools, all tier 1 and read-only: `next_pass(satellite, hours)`,
`overhead_now(min_altitude)`, `moon_phase()`, `planets_tonight()`. Each
returns a short dict and a `spoken` sentence. "Visible" means the satellite
is above the floor, lit by the sun, and the sun at the house is below −6°;
"bright" is a visible pass that climbs past 40°.

Worked example, with the notes: `examples/sky.yaml`.

## `voice:`

```yaml
voice:
  language: en
  stt:  {host: 127.0.0.1, port: 10300}
  tts:  {host: 127.0.0.1, port: 10200, voice: en_US-lessac-medium}
  wake: {host: 127.0.0.1, port: 10400, model: hey_jarvis}
  pipelines:
    - name: Jarvis
      voice: en_GB-alan-medium
      wake_word: hey_jarvis
      language: en
```

Ports are the Wyoming containers in `docker-compose.yml`. Clients ask for a
pipeline by name; the first is the default. Per-pipeline keys: `name`, `id`,
`language`, `voice` (aliased to `tts_voice`), `wake_word`, `stt_engine`,
`tts_engine`, `conversation_engine`.

Services: `voice.say` (`text`, optional `entity_id` to play it on a media
player, `voice`, `language`) and `voice.get_pipelines`. Both return responses.

Wiring, tuning and latency targets: [voice.md](voice.md).

## `llm:`

```yaml
llm:
  url: http://127.0.0.1:11434
  model: qwen3:8b
  persona_file: prompts/jarvis.txt
  max_tool_rounds: 5
  approval_ttl: 300
  timeout: 120
  keep_alive: 30m
  options: {temperature: 0.6, num_ctx: 8192}
```

| Key | Notes |
|---|---|
| `url` `model` | The model server. `llm.list_models` tells you what it is serving. |
| `backend` | `ollama` (the default) or `openai`. Inferred from the url when unset — `/v1` anywhere in it means `openai`. See [openai-compat.md](openai-compat.md). |
| `api_key` | Sent as `Authorization: Bearer …` to the model server and nowhere else. `openai` backend only. |
| `headers` | Extra headers for a router that wants them (`x-litellm-tags`, a tenant id). `openai` backend only. |
| `backend_name` | What error messages call the server. Defaults to "the model server". |
| `persona_file` | Relative to the config directory. Defaults to `prompts/jarvis.txt` if that file exists. `persona:` sets the text inline instead. |
| `think` | Whether to let the model reason before answering. Unset leaves the model's own default alone; `false` is what the shipped config sets, because on a spoken turn deliberation is silence the user hears. |
| `allow_think_escalation` | With `think: false`, lets the model raise reasoning for a single turn it judges needs working out, once per turn, via a `think_it_through` tool the agent serves itself. Default true; only meaningful when `think` is false. |
| `max_tool_rounds` | Tool-call rounds per turn. Higher chains more steps and multiplies worst-case latency by the same factor. |
| `approval_ttl` | Seconds a Tier-3 approval request stays valid. Requests are single-use, so a model cannot replay one. |
| `options` | Passed to Ollama verbatim. On the `openai` backend the keys with an equivalent are translated and the rest go through as `extra_body`; `num_ctx` is dropped, because on that wire the context length is a property of how the server was started. |
| `conversation: {ttl, max_turns}` | How long the MODEL's context survives and how much of it is kept. |
| `conversation: {history, history_limit}` | The durable half: every finished turn in `.storage/conversations.json`, which is what the console's chat mode lists and reopens. `history: false` turns it off. A tool's *result* is never written there — only whether it worked. |
| `tools_dir` | Directory of `*.tool.yaml` manifests. |
| `tools:` | The same tools declared inline — and the only place a tool can use `!secret`. |

### A different model server

Anything speaking `/v1/chat/completions` works in place of Ollama — LiteLLM,
vLLM, llama.cpp's server, LM Studio, TGI, SGLang:

```yaml
llm:
  backend: openai
  url: http://litellm:4000/v1     # the /v1 is not optional
  model: house-model              # the name YOUR router knows the model by
  api_key: !env_var LLM_API_KEY
```

[openai-compat.md](openai-compat.md) is the full account: a worked LiteLLM
config pair, what differs between the two wires, and how failures are retried.

### `expose:` — the blast radius

```yaml
expose:
  domains: [light, switch, cover, climate, media_player]
  entities: [sensor.outside_temperature]
  areas: [kitchen]
  exclude_entities: [switch.coffee_machine]
  exclude_domains: [vacuum]
```

Anything not exposed is invisible to every tool, read-only ones included. With
nothing configured, a safe default set of domains is exposed. Setting any of
`domains` / `entities` / `areas` narrows to their union; the `exclude_*` lists
are then subtracted.

Exposing `lock` does not make unlocking free. `lock` and `notify` are in
`GATED_DOMAINS`, so a model turn targeting them always returns
`approval_required` and waits for a human.

Both of those rules are checked when a tool *resolves a target*, which
`run_script` and `activate_scene` do not do for the contents of the macro they
run — they resolve the `script.*`/`scene.*` entity and then execute it. So a
script that unlocks a door, or that names an entity you excluded, is reachable
unless you exclude the script itself:

```yaml
exclude_entities:
  - switch.coffee_machine
  - script.good_morning      # ...because it turns the coffee machine on
  - scene.away               # ...and this turns it off
```

Read [security.md](security.md#the-hole-this-leaves-scripts-and-scenes) before
relying on an exclusion. The shipped `config/configuration.yaml` excludes
`cover.garage_door` precisely because no shipped macro touches it.

### `user_context:` — how Jarvis decides to reach you

```yaml
user_context:
  presence: person.chris
  driving: binary_sensor.chris_driving
  awake: input_boolean.chris_awake
  active_device: sensor.chris_active_device
```

These entity ids feed the `get_user_context` tool, which is what the persona's
routing rules read: speak while driving, notify while away, stay silent while
asleep. Omit the ones you do not have.

## `watch:`

```yaml
watch:
  interval: 900      # seconds between checks by default; a watch may ask for its own (floor 30)
  max_watches: 50
  notify: true       # a change lands as a moment (kind `watch`) as well as a bus event
```

Anything online, with time in it (M59). `watch_page` keeps a snapshot of a
page and says when it changes, with what changed; `watch_feed` follows an RSS
2.0 or Atom feed and says what is new; `watch_for` ("tell me when …") asks a
question of the web every interval until the answer is yes. `read_page` reads a
page as text — through jarvis-browser when it is configured, so a page that
draws itself with JavaScript is read properly, and through jarvis-core when it
is not; `feed_latest` lists a feed. Snapshots live under `config/watch/`. No
watch checks faster than every 30 seconds, whatever it asked for.

## `mqtt:`

```yaml
mqtt:
  broker: 127.0.0.1
  port: 1883
  username: jarvis
  password: !secret mqtt_password
  discovery: true
  discovery_prefix: homeassistant
  birth_topic: jarvis/status
  will_topic: jarvis/status
```

`discovery: true` is the important line. Zigbee2MQTT, ESPHome, Z-Wave JS UI,
rtl_433 and Theengs publish Home Assistant-format discovery messages under
`homeassistant/#`, and Jarvis parses that format — single configs and device
bundles, every component Home Assistant has including `event` (a button press,
a doorbell — also fired on the bus as `jarvis_mqtt_event`) and
`device_tracker` — so those devices appear on their own, with names, device
grouping and areas intact. Two devices speak their own dialect and are
translated into the same entities: Tasmota (its own
`tasmota/discovery/<mac>/{config,sensors}` — Tasmota dropped the HA format in
2023) and Shelly Gen2 (`<id>/status/switch:<n>`, no discovery at all).
`translators: false` switches that off.

Four more keys, all optional (M57):

```yaml
mqtt:
  canonical_units: true          # °F → °C, inHg → hPa, Wh → kWh at ingest; one unit per device class
  discovery_birth: true          # also say "online" on <discovery_prefix>/status, so the bridges re-announce
  discovery_allow_ids: []        # glob patterns on unique_id / device identifiers; empty = everything
  discovery_deny_ids: ["Schrader-*", "*TPMS*"]   # deny wins — an RTL-SDR hears the whole street
```

The sensors integration (`sensors:`) gives the model four read-only tools over
whatever these produce: `sensor_readings` (filter by area, device class or a
word), `sensor_compare` (coldest / warmest / most power across rooms),
`sensor_history` (min / max / mean over `24h`, `7d`, `30m` — needs `history:`)
and `sensor_summary`.

Devices that do not self-announce get declared by hand. Every component block
takes a list:

```yaml
mqtt:
  switch:
    - name: Desk Lamp
      state_topic: stat/desk/POWER
      command_topic: cmnd/desk/POWER
      payload_on: "ON"
      payload_off: "OFF"
  sensor:
    - name: Greenhouse Temperature
      state_topic: greenhouse/telemetry
      value_template: "{{ value_json.temperature }}"
      unit_of_measurement: "°C"
      device_class: temperature
```

An unreachable broker logs a warning and retries in the background; it does not
stop startup. Without `aiomqtt` or `paho-mqtt` installed, the client degrades
to log-only mode (the container image has `aiomqtt`).

Services: `mqtt.publish` (`topic`, `payload`, `qos`, `retain`), `mqtt.dump`.

**Known ordering caveat:** the automation engine is set up before the MQTT
client exists, so an `mqtt` *trigger* attached at boot logs "mqtt trigger … is
inert" and never fires. Call `automation.reload` once after startup and it
attaches to a live client. MQTT *entities* and discovery are unaffected.

## `demo:`

```yaml
demo:
  create_areas: true
  prefix: ""
```

A full house of fake devices across three areas — lights, switches, sensors, a
thermostat, covers, a lock, a fan, a speaker, a number, a select, a button and
a vacuum. Every one implements the real method contract, so automations and
voice commands behave exactly as they will against hardware. Delete the block
when the real devices arrive.

## `template:`

Entities whose state is a Jinja expression.

```yaml
template:
  - sensor:
      - name: Average Temperature
        state: "{{ ((states('sensor.a')|float + states('sensor.b')|float) / 2) | round(1) }}"
        unit_of_measurement: "°C"
        device_class: temperature
        attributes:
          inputs: "{{ ['sensor.a', 'sensor.b'] }}"
    binary_sensor:
      - name: Anyone Home
        state: "{{ is_state('person.sam', 'home') }}"
        device_class: presence
    switch:
      - name: Study Lamp Proxy
        state: "{{ is_state('light.study', 'on') }}"
        turn_on:  {service: light.turn_on,  data: {entity_id: light.study}}
        turn_off: {service: light.turn_off, data: {entity_id: light.study}}
```

Every template entity re-renders whenever any *other* entity changes. Changes
to template entities themselves are ignored, which is what stops the graph
feeding itself.

Watch whitespace. A folded block (`>-`) that starts with `{% set %}` leaves
leading spaces in the result, and a numeric state with spaces around it stops
being numeric — graphs and `numeric_state` triggers quietly break. Use
`{%- … -%}`:

```yaml
state: >-
  {%- set t = states('sensor.outside_temperature') | float(0) -%}
  {{ (t * 9 / 5 + 32) | round(1) }}
```

## `rest:`

Entities from any HTTP API. A list of blocks; one request per block serves
every entity in it.

```yaml
rest:
  - resource: http://10.0.0.5/api/status
    scan_interval: 30
    method: GET
    headers:
      Authorization: !secret api_token
    sensor:
      - name: Solar Power
        value_template: "{{ value_json.power }}"
        unit_of_measurement: W
        device_class: power
        json_attributes: [voltage, current]
    binary_sensor:
      - name: Grid Online
        value_template: "{{ value_json.grid == 'up' }}"
    switch:
      - name: Garden Pump
        resource: http://10.0.0.5/api/pump
        body_on: '{"on": true}'
        body_off: '{"on": false}'
        is_on_template: "{{ value_json.on }}"
```

`value_json` is the parsed body; `value` is the raw text.

## `command_line:`

Entities from shell commands. A list of `- sensor:` / `- binary_sensor:` /
`- switch:` blocks.

```yaml
command_line:
  - sensor:
      name: Disk Free
      command: "df -P / | tail -1 | tr -s ' ' | cut -d' ' -f4"
      scan_interval: 300
      command_timeout: 15
      unit_of_measurement: GB
      value_template: "{{ (value | float(0) / 1048576) | round(1) }}"
  - switch:
      name: Server Fan
      command_on: "/usr/local/bin/fan on"
      command_off: "/usr/local/bin/fan off"
      command_state: "/usr/local/bin/fan status"
      value_template: "{{ value == 'on' }}"
```

Commands run through the shell with a hard timeout. A failure or a timeout
makes the entity `unavailable` rather than raising.

These run **inside the jarvis-core container**, which sees the container's
filesystem and the container's tools — not the host's. `python:3.12-slim` has
coreutils but no `awk`, `procps` or `curl` unless the image's best-effort apt
step succeeded. Stick to coreutils and `/proc`, or shell into the container and
check before relying on a command.

## `person:` and device trackers

```yaml
person:
  - name: Chris
    id: chris
    device_trackers: [device_tracker.chris_phone, device_tracker.chris_watch]
```

State is `home` if any tracker is home; otherwise the most recently updated
known tracker state. `device_tracker.see` is registered for phones, routers and
presence scripts to report into:

```yaml
service: device_tracker.see
data:
  dev_id: chris_phone
  gps: [51.5072, -0.1276]
  gps_accuracy: 12
  battery: 84
```

With no `location_name`, the coordinates are compared against `jarvis:
latitude/longitude` within `radius` to decide `home` vs `not_home`. The tracker
entity is created on first report, so the ids above may name devices that do
not exist yet.

## `input_*:` — user-editable helpers

The knobs automations and voice commands read and write. Values survive
restarts in `.storage/input_helpers.json`.

```yaml
input_boolean:
  guest_mode: {name: Guest mode, icon: "mdi:account-multiple", initial: off}

input_number:
  bedtime_volume: {min: 0, max: 100, step: 5, initial: 30, unit_of_measurement: "%"}

input_select:
  house_mode: {options: [home, away, night, holiday], initial: home}

input_text:
  last_announcement: {max: 255}

input_datetime:
  laundry_finished_at: {has_date: true, has_time: true}
```

Services: `input_boolean.turn_on/turn_off/toggle`,
`input_number.set_value/increment/decrement`, `input_text.set_value`,
`input_select.select_option/select_next/select_previous/set_options`,
`input_datetime.set_datetime`.

These keys are not integration names, so the loader logs
`No integration named 'input_boolean'` at startup. It is cosmetic — the
always-on `automation` integration bootstraps them, and the entities are
created normally.

## `hue:` and `wled:`

```yaml
hue:
  host: 192.168.1.20
  api_key: !secret hue_key
  scan_interval: 15
  version: 2          # optional; v2 is auto-detected, then v1
  groups: true        # expose rooms/groups as light entities too

wled:
  - host: 192.168.1.30
    name: Desk Strip
    scan_interval: 10
```

Hue keys are minted by pressing the bridge's link button then
`curl -X POST http://<bridge>/api -d '{"devicetype":"jarvis#core"}'`. WLED gets
a light entity plus a `select` for the effect list.

## `automation:`

Usually `automation: !include automations.yaml`. A list of:

```yaml
- id: hallway_motion            # stable; used by the API
  alias: Hallway motion light   # the friendly name — this is what the
                                # entity_id is derived from
  description: ...
  mode: restart                 # single | restart | queued | parallel
  max: 10                       # queue/parallel depth
  trigger: [...]
  condition: [...]
  action: [...]
```

`triggers:`/`conditions:`/`actions:` (newer HA spelling) are accepted too.

**Modes.** `single` drops a second run while one is in flight. `restart`
cancels the running one and starts over — this is what you want for a motion
light, so movement extends the timer instead of stacking timers. `queued` runs
them in order. `parallel` runs them at once, up to `max`.

### Triggers

| `platform:` | Keys |
|---|---|
| `state` | `entity_id`, `to`, `from`, `attribute`, `for` |
| `numeric_state` | `entity_id`, `above`, `below`, `value_template`, `for` |
| `time` | `at` (one or a list) |
| `time_pattern` | `hours`, `minutes`, `seconds` — `"/15"` means every 15 |
| `event` | `event_type`, `event_data` |
| `mqtt` | `topic`, `payload`, `value_template` (see the ordering caveat above) |
| `webhook` | `webhook_id` — POST to `/api/webhook/{id}` |
| `template` | `value_template`, `for` |
| `jarvis_start` | aliases `homeassistant_start`, `start` |

`trigger:` is accepted as an alias for `platform:`. Every trigger may carry an
`id:`, matched later by `{condition: trigger, id: motion}`.

Trigger variables available in templates: `trigger.platform`,
`trigger.entity_id`, `trigger.from_state`, `trigger.to_state`,
`trigger.payload` / `trigger.payload_json` (mqtt), `trigger.json` /
`trigger.query` (webhook), `trigger.event`, `trigger.now`.

### Conditions

`state`, `numeric_state`, `template`, `time` (`after`, `before`, `weekday`),
`and` / `or` / `not` with `conditions:`, and `trigger` with `id:`.

A bare list is an implicit `and`. A bare template string is a `template`
condition. A dict without a `condition:` key is inferred from its contents, so
`{entity_id: x, state: "on"}` works.

### Actions

```yaml
- service: light.turn_on            # `action:` is accepted as an alias
  target: {entity_id: light.hall}   # or area_id / device_id
  data: {brightness: 200}
  response_variable: result
  continue_on_error: false
- delay: "00:02:00"                 # or 5, or {minutes: 2}
- wait_template: "{{ is_state('binary_sensor.x','off') }}"
  timeout: "00:05:00"
  continue_on_timeout: true
- wait_for_trigger: [...]
- condition: ...                    # false stops the sequence
- choose:
    - conditions: [...]
      sequence: [...]
  default: [...]
- if: [...]
  then: [...]
  else: [...]
- repeat: {count: 3, sequence: [...]}          # or while / until / for_each
- variables: {name: value}
- parallel: [...]
- event: my_event
  event_data: {...}
- scene: scene.movie_time
- stop: "why"
  response_variable: result
```

Everything in `data`, `target` and `delay` is rendered as a template with the
caller's variables, so `{{ trigger.to_state.state }}` works as it does in HA.

Services: `automation.trigger`, `turn_on`, `turn_off`, `toggle`, `reload`.

## `script:`

Usually `script: !include scripts.yaml`. A mapping of name → definition:

```yaml
goodnight:
  alias: Good night
  description: Lock up and turn everything off.
  mode: single
  fields:
    delay_minutes:
      description: Wait this long first.
      example: 5
      required: false
  sequence:
    - service: light.turn_off
      target: {entity_id: all}
    - variables:
        result: {locked: true}
    - stop: done
      response_variable: result
```

Each entry becomes both a `script.<name>` entity and a `script.<name>` service.
That second half matters: a script with a `description:` and `fields:` is
offered to the LLM as a tool automatically. Ending in `stop:` with a
`response_variable:` returns structured data to the caller, model included.

This is how you give Jarvis new abilities without writing Python.

## `scene:`

Usually `scene: !include scenes.yaml`. A list of:

```yaml
- name: Movie time
  id: movie_time
  icon: mdi:movie-open
  entities:
    light.living_room: {state: on, brightness: 40}
    media_player.tv: playing
    cover.blinds: closed
    climate.thermostat: {state: heat, temperature: 20}
```

Each target is applied with the right verb for its domain — `turn_on` with
attributes, `open_cover`/`close_cover`, `lock`/`unlock`, `set_hvac_mode` plus
`set_temperature`, `select_option`, `set_value` — so a scene can mix domains
freely. Entities whose domain has no matching service fall back to a direct
state write, which is what makes scenes work for template and virtual entities.

A scene is a destination, not a transition. Anything with steps, timing or
conditions is a script.

Services: `scene.turn_on`, `scene.apply` (takes a raw `entities:` mapping, so a
model or a script can build one on the fly), `scene.reload`.

## `packages:`

```yaml
packages: !include_dir_named packages
```

Every `packages/*.yaml` is merged into the **top level** of the configuration,
so a package file uses the same top-level keys — `automation:`, `script:`,
`template:`, `input_boolean:` — and contributes to whatever is already there:

- lists (`automation`, `template`, `scene`, `rest`) are concatenated
- dicts (`script`, `input_*`) are merged key by key
- the same name defined twice is an **error**, deliberately

That last rule is the point. A package cannot silently override the main
configuration; a collision stops startup and names the offender, so you find
out at boot rather than wondering why an automation stopped firing.

One feature per file. Delete the file, the whole feature goes — helper,
automation, template entity and script together. `config/packages/example.yaml`
is a worked example.

## Templates

The Jinja environment is HA-compatible, so copied templates keep working:

```jinja
{{ states('sensor.outside_temperature') | float(0) }}
{{ state_attr('light.bed', 'brightness') }}
{% if is_state('binary_sensor.motion', 'on') %}…{% endif %}
{{ states.light | selectattr('state', 'eq', 'on') | map(attribute='name') | list }}
{{ now().hour }}   {{ as_timestamp(now()) }}   {{ value_json.main.temp }}
{{ expand('group.downstairs') | map(attribute='entity_id') | list }}
{{ area_name('light.kitchen_lights') }}
```

Available: `states` (callable, iterable, and `states.light.kitchen_lights`),
`is_state`, `state_attr`, `is_state_attr`, `has_value`, `expand`, `area_id`,
`area_name`, `area_entities`, `now`, `utcnow`, `as_datetime`, `as_timestamp`,
`strptime`, `timedelta`, `relative_time`, `to_json`, `from_json`, `iif`,
`slugify`, `ordinal`, the `regex_*` family, and forgiving `float`/`int`/`round`
/`min`/`max`/`average` that take a default instead of raising.

Undefined values chain instead of exploding, so `value_json.a.b.c` on a missing
key yields undefined rather than an error mid-render.
