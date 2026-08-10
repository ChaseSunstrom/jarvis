# Jarvis Core

A home-automation platform and voice assistant that runs on your own hardware,
with no Home Assistant underneath it. State machine, event bus, service
registry, entity/device/area registries, YAML automations, scripts, scenes,
templates, a recorder, MQTT discovery, a Wyoming voice pipeline and a local
LLM agent — one Python package, one container, one `/config` directory.

It talks to the same clients Home Assistant did. The REST API is shaped like
`/api/states`, `/api/services/{domain}/{service}`; the websocket does
`auth_required` → `auth` → `auth_ok`, `subscribe_events`, `call_service` and
`assist_pipeline/run` with the same binary audio framing. The browser HUD and
the Android app in the parent repo needed a URL and a token changed, nothing
else. An ESP32 Wyoming satellite speaks the same pipeline contract, though no
firmware for one ships here.

Nothing leaves the house. STT, TTS, wake word, geocoding and the model are all
containers on the same machine.

## Quickstart

```bash
cd jarvis-core
docker compose up -d
docker compose logs -f jarvis-core
```

The first start mints a long-lived access token and prints it in a banner in
the log. Copy it — it is stored as a SHA-256 digest and is never shown again:

```
==========================================================================
  JARVIS CREATED YOUR FIRST LONG-LIVED ACCESS TOKEN ('initial').
  Copy it into the web HUD / Android app now — it is never shown again:

      eyJhb...  (43 characters)

  Lost it? Delete /config/.storage/auth.json and restart to mint a new one.
==========================================================================
```

Check it works:

```bash
curl -s localhost:8080/healthz
curl -s localhost:8080/api/states -H "Authorization: Bearer $TOKEN" | head
```

Then point the HUD at `http://<server>:8080` and paste the token. More tokens,
one per device, without restarting:

```bash
docker compose exec jarvis-core python -m jarvis --config /config --create-token phone
```

The shipped `config/` boots into an **empty** house: the server, the voice
pipeline, the model, and a handful of sensors that watch Jarvis itself (is the
model loaded, is the disk filling up, where is the sun). No rooms, no devices,
no automations and no people — inventing those would fill your console with
things you do not own and cannot switch on, and you could never tell "not set
up yet" from "set up wrong".

Build the house one of three ways: plug in hardware and let `mqtt:` discovery
find it, use the console (Devices / Areas / Automations / Tools), or ask Jarvis,
which can create areas, helpers, automations and tools and will ask you for
anything it needs.

If you want something to talk to before hardware arrives, `config/examples/house/`
is the full fake house — lights, a thermostat, covers, a lock, a speaker, eight
automations, five scripts, scenes and helpers — kept whole, with copy-paste
instructions in `config/examples/README.md`.

### Permissions

`./config` is a bind mount, so the container cannot fix its ownership. Jarvis
runs as uid 10003 and writes `.storage/` and the recorder database there. On
`EACCES` at first start:

```bash
sudo chown -R 10003:10003 ./config
```

### Ollama

Jarvis expects a model on `127.0.0.1:11434` (`llm: url:` in the config). If you
run Ollama on the host, which is normal, pull the model there:

```bash
ollama pull qwen3:8b
```

If you do not, uncomment the `ollama` service in `docker-compose.yml`.

## Architecture

```
   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
   │ Browser HUD  │   │ Android app  │   │ ESP32 satellites │
   └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘
          │                  │                    │
          └──────────┬───────┴────────────────────┘
                     │  REST /api/*  +  WS /api/websocket
                     │  bearer token, LAN/WireGuard only
   ╔═════════════════▼══════════════════════════════════════════╗
   ║  jarvis-core                                       :8080   ║
   ║                                                            ║
   ║   api/         REST + websocket + assist_pipeline framing  ║
   ║   ────────────────────────────────────────────────────     ║
   ║   bus          fire / listen — everything is an event      ║
   ║   states       entity_id → state + attributes              ║
   ║   services     domain.service → handler                    ║
   ║   registries   entity · device · area                      ║
   ║   ────────────────────────────────────────────────────     ║
   ║   integrations/                                            ║
   ║     domains ── the verbs (turn_on, open_cover, lock…)      ║
   ║     automation · script · scene · template · input_*       ║
   ║     recorder → history → logbook   (one SQLite file)       ║
   ║     mqtt (discovery) · rest · command_line · hue · wled    ║
   ║     voice  ─┐                                              ║
   ║     llm    ─┼─ persona · tools · the approval gate         ║
   ╚═════════════╪══════════════════════════════════════════════╝
                 │
      ┌──────────┼───────────┬──────────────┬─────────────┐
      ▼          ▼           ▼              ▼             ▼
  openwake-   whisper      piper        ollama        MQTT broker
   word       :10300      :10200        :11434         :1883
   :10400      (STT)       (TTS)      (qwen3:8b)   Zigbee2MQTT · Tasmota
   (wake)                                          ESPHome · Shelly
```

Everything is an event. A service call fires `call_service`; a handler changes
a state; that fires `state_changed`; the recorder writes it, the logbook
narrates it, template entities re-render, automation triggers fire and the
websocket forwards it to subscribed clients. There is no other mechanism, and
no integration gets a private path around it — including the LLM, whose tools
dispatch through the ordinary `domain.service` layer like everything else.

## The integration model

An integration is a package under `jarvis/integrations/<name>/` that exposes
three things:

```python
DOMAIN = "acme"
DEPENDENCIES: list[str] = []                       # set up before me

async def async_setup(jarvis, config) -> bool: ...
```

`config` is that domain's block from `configuration.yaml`, already parsed —
whatever is under the `acme:` key, be it a dict, a list, or `None`. Presence of
the key is what makes an integration load; there is no separate enable step.

Integrations create entities, register services, or both. An entity subclasses
`Entity`, sets `_attr_*`, and implements the methods its domain defines
(`async_turn_on`, `async_open_cover`, `async_set_temperature` …). It does not
register services: the `domains` integration owns `light.turn_on` for every
light in the house and dispatches to whichever object is behind the entity_id.
An entity that cannot do something simply does not define the method, and the
caller gets a clear error instead of a silent no-op.

Full walkthrough with an annotated example: [docs/integrations.md](docs/integrations.md).

## Adding a device

### In YAML — no Python

Most things are already covered by a generic integration, and generic means
"edit `configuration.yaml`, restart, done":

| The device… | Use |
|---|---|
| publishes HA-format discovery to MQTT (Zigbee2MQTT, Tasmota, ESPHome, Shelly) | `mqtt: discovery: true` — nothing else. It appears. |
| speaks MQTT but does not self-announce | `mqtt:` with an explicit `switch:`/`sensor:`/`light:` block |
| has an HTTP/JSON API | `rest:` — one request feeds every entity in the block |
| can be read or poked from a shell | `command_line:` |
| is a value derived from other entities | `template:` |
| is a Hue bridge or a WLED controller | `hue:` / `wled:` |
| is something the LLM should be able to call | a `*.tool.yaml` in `config/tools/`, or a `script:` with `description:` and `fields:` |

### In Python — when it is a real protocol

Anything with its own protocol, a push socket, or state to keep needs a small
integration: a package directory, an `async_setup`, and one `Entity` subclass
per device kind. A working integration is on the order of 100 lines.
[docs/integrations.md](docs/integrations.md) walks through one end to end.

## Configuration reference

`config/configuration.yaml`, one top-level key per integration. Full detail in
[docs/configuration.md](docs/configuration.md).

| Key | What it does |
|---|---|
| `jarvis:` | Name, coordinates, timezone, areas, and the HTTP server (`http: host/port`, `cors_allowed_origins`). Not an integration — this is the core. |
| `recorder:` | SQLite history. `db_file`, `purge_keep_days`, `commit_interval`, `include`/`exclude`. |
| `history:` | Query layer over the recorder. Depends on it. |
| `logbook:` | Plain-English activity feed, in memory plus recorder. |
| `sun:` | `sun.sun` with elevation, azimuth and next rise/set. |
| `voice:` | Wyoming `stt`/`tts`/`wake` endpoints and named `pipelines`. |
| `llm:` | Ollama `url`/`model`, `persona_file`, `expose`, `user_context`, `tools_dir`. |
| `mqtt:` | Broker, HA-format `discovery`, and hand-declared entities. |
| `demo:` | A full fake house. Not shipped; see `config/examples/house/`. |
| `template:` | Entities whose state is a Jinja expression. |
| `rest:` | Entities from any HTTP API. |
| `command_line:` | Entities from shell commands. |
| `person:` | Presence, aggregated from device trackers. |
| `input_boolean/number/select/text/datetime:` | User-editable helpers. |
| `hue:` / `wled:` | Philips Hue bridge / WLED controller. |
| `automation:` `script:` `scene:` | Usually `!include`d from their own files. |
| `packages:` | `!include_dir_named packages` — one feature per file, merged in. |

Loader tags, all Home Assistant compatible: `!secret`, `!env_var NAME default`,
`!include`, `!include_dir_named`, `!include_dir_merge_named`,
`!include_dir_list`, `!include_dir_merge_list`.

## What this is not

Read this part before you commit to it.

**It is not a replacement for Home Assistant's integration catalogue.** HA has
something like a thousand integrations written by a decade of contributors.
Jarvis Core has a framework and about a dozen. If your house runs on Z-Wave
JS, HomeKit, Matter, a cloud-tied vacuum or a manufacturer app with no local
API, none of that is here and porting it is your job.

What is actually covered:

- **MQTT with Home Assistant-format discovery**, which is the big one. Anything
  that publishes to `homeassistant/#` shows up on its own — Zigbee2MQTT (so
  every Zigbee device it supports), Tasmota, ESPHome, Shelly, Zwave-JS-UI in
  MQTT mode. In practice this is most of a normal smart home.
- **REST, template and command_line**, which cover any device with an HTTP API
  or a CLI, and anything derivable from what you already have.
- **Hue and WLED** natively.
- **A framework** for the rest: `async_setup`, `Entity`, a platform helper, and
  the domain service layer done for you.

Anything outside that list needs either a small Python integration or a YAML
REST/MQTT definition you write yourself. There is no add-on store, no
supervisor, and no one-click update.

**There is no Lovelace.** No dashboard editor, no card ecosystem, no
drag-and-drop layout. The UI is the HUD in the parent repo, which is a voice
interface with a status readout, not a dashboard. If you spend your evenings
arranging cards, you will miss it.

**There is no config UI.** Devices are not added by clicking through a wizard;
they are added by editing YAML and restarting. The entity, device and area
registries are editable over the API (rename things, assign areas, set
aliases), but the wiring is a file.

**It is one process on one machine.** No high availability, no clustering, no
remote backup service, no cloud fallback if the model is down.

**It is young.** Home Assistant has had a decade of people finding the edge
cases in production. This has tests and one house.

The trade you get for all of that: something you can read end to end in an
afternoon, that starts in under a second, that has no supervisor deciding when
to update you, and that an LLM can be given tools over without a bridge layer
in between.

## Layout

```
jarvis/
  bus.py state.py services.py registry.py entity.py core.py   the contract
  config.py                    YAML loader: !secret, !include, packages
  auth.py                      long-lived tokens (SHA-256 digests)
  api/                         REST + websocket
  automation/                  triggers · conditions · the script executor
  llm/                         Ollama client · tools · the approval gate
  voice/                       Wyoming clients · the pipeline
  helpers/template.py          the HA-compatible Jinja environment
  integrations/<name>/         one package per integration
config/                        your installation (mounted at /config)
docs/                          integrations · configuration · voice · security · migrating
tests/                         pytest, no network or hardware required
```

## Development

```bash
pip install -r requirements.txt
python -m jarvis --config ./config -v
python -m pytest -q
```

`python -m jarvis --help` for the flags. `--create-token NAME` prints a token
and exits without starting the server.

## Docs

- [docs/configuration.md](docs/configuration.md) — every YAML key
- [docs/integrations.md](docs/integrations.md) — writing one, with a full example
- [docs/clients.md](docs/clients.md) — the REST/websocket contract clients speak
- [docs/voice.md](docs/voice.md) — Wyoming wiring and latency targets
- [docs/security.md](docs/security.md) — tokens, network posture, the approval gate
- [docs/migrating-from-ha.md](docs/migrating-from-ha.md) — what ports over and what does not
