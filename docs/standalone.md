# jarvis-core, and why the clients never noticed

Jarvis began as a layer on top of Home Assistant, with HA as the sole
tool-execution hub. `jarvis-core` replaced it. The HA-era configuration,
the tool generator and the Android fork are gone — see
[`removed.md`](removed.md) — but every client kept working through the
change without a line of client code being rewritten. This page explains why,
because that compatibility is deliberate and worth preserving.

| | **HA-backed** (what it was) | **`jarvis-core`** (what it is) |
|---|---|---|
| Automation engine | Home Assistant | `jarvis-core` |
| Where tools live | HA scripts + `rest_command`, from a generator | service registry + `config/tools/*.tool.yaml` |
| Persona prompt | pasted into HA's conversation agent | `jarvis-core/config/prompts/jarvis.txt`, loaded from config |
| Device integrations | HA's full catalogue | MQTT discovery, REST, template, command_line, Hue, WLED, or write one |
| Port | 8123 | 8080 (set it to 8123 if you want a literal drop-in) |
| Dashboards | Lovelace | none |
| Voice, model, geocoder | Wyoming 10400/10300/10200, Ollama 11434, photon | identical, same containers |

Everything else in this repo — the HUD, the Android app, the desktop agent,
the orchestrator, the sandbox, the evals, the firewall scripts — is shared and
was unaffected.

If your house depends on integrations outside MQTT and HTTP — Z-Wave JS,
HomeKit, Matter, cloud-tied devices — or you want Lovelace dashboards and the
config UI, Home Assistant is still the better tool, and you can run it
alongside: different ports, same MQTT broker, both consuming discovery.

## Why the clients do not care

`jarvis-core` implements the same contract Home Assistant does, because the
clients were written against it and rewriting four of them was never on the
table:

- REST at `/api/states`, `/api/services/{domain}/{service}`, `/api/config`,
  `/api/events`, `/api/history/period`.
- Websocket at `/api/websocket`: `auth_required` → `auth` → `auth_ok`, then
  `get_states`, `subscribe_events`, `call_service`, `config/*_registry/list`.
- `assist_pipeline/run` with the same stage events (`run-start`,
  `wake_word-end`, `stt-end`, `intent-progress`, `tts-end`, `run-end`) and the
  same binary audio framing: first byte is the handler id from `runner_data`,
  a lone id byte ends the audio.
- Long-lived bearer tokens in `Authorization: Bearer …` and in the websocket
  `auth` message.
- `/api/config` reports `ha_version: "jarvis-0.1.0"`, which is enough for
  HA-aware clients to proceed.

Switching a client between modes is a URL and a token. Nothing else.

## Getting started

```bash
cd jarvis-core
docker compose up -d
docker compose logs -f jarvis-core     # the first-run token is printed here
```

`jarvis-core/docker-compose.yml` is the full stack: `jarvis-core` plus the
Wyoming STT/TTS/wake services, photon and jarvis-browser. The HUD, the
orchestrator and the sandbox live in the companion stack at the repository
root — start this one first.

Coming from a Home Assistant install, the migration order is in
[`../jarvis-core/docs/migrating-from-ha.md`](../jarvis-core/docs/migrating-from-ha.md).

Read next:

- [`../jarvis-core/README.md`](../jarvis-core/README.md) — what it is, and an
  honest account of what it is not
- [`../jarvis-core/docs/configuration.md`](../jarvis-core/docs/configuration.md) — every YAML key
- [`../jarvis-core/docs/integrations.md`](../jarvis-core/docs/integrations.md) — adding a device
- [`../jarvis-core/docs/clients.md`](../jarvis-core/docs/clients.md) — pointing a client at it
- [`../jarvis-core/docs/security.md`](../jarvis-core/docs/security.md) — tokens, network posture, the approval gate
- [`architecture.md`](architecture.md) — how the whole repo fits together
- [`removed.md`](removed.md) — what the HA generation left behind, and why
