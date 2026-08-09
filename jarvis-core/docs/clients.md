# Connecting a client to jarvis-core

Every Jarvis client — the web HUD and management console, the Android app, the
ESP32 satellites, `curl` — speaks the **same websocket protocol** against the
same endpoint. There is one contract, and it is deliberately identical to the
Home Assistant websocket API so a client written against either backend works
against the other.

```
jarvis-core   http(s)://<host>:<port>
              ├── /api/websocket        the socket everything real happens on
              ├── /api/tts_proxy/<t>.wav synthesised speech (token in the path)
              ├── /api/...               a REST mirror of the same operations
              └── /healthz               liveness
```

Default bind is `0.0.0.0:8080`; the repo's compose file and every example use
`8123` because that is the port existing clients were written against:

```sh
python -m jarvis --config ./config --host 0.0.0.0 --port 8123
```

## Authentication

One credential type: a long-lived bearer token. No accounts, no login form.

- REST: `Authorization: Bearer <token>`.
- Websocket: the token goes in the `auth` message (below), or as an
  `authorization` field on it.

On first run — nothing in `<config>/.storage/auth.json` and no `JARVIS_TOKEN` in
the environment — jarvis-core mints a token and prints it in a banner. Set
`JARVIS_TOKEN` to pin one instead. More can be minted with
`python -m jarvis --create-token <name>` or `POST /api/auth/tokens`, and revoked
at `DELETE /api/auth/tokens/{id}`; only a SHA-256 digest is ever stored, so a
token is shown exactly once.

## The handshake

```
server  {"type": "auth_required", "ha_version": "jarvis-0.1.0"}
client  {"type": "auth", "access_token": "..."}
server  {"type": "auth_ok", "ha_version": "jarvis-0.1.0"}      (or auth_invalid, then close)
```

`ha_version` is reported so Home-Assistant-shaped clients recognise the
handshake without a special case. After `auth_ok`, every client message carries
a monotonically increasing integer `id`, unique per connection:

```
client  {"id": 1, "type": "get_states"}
server  {"id": 1, "type": "result", "success": true, "result": [...]}

client  {"id": 2, "type": "subscribe_events", "event_type": "state_changed"}
server  {"id": 2, "type": "result", "success": true, "result": null}
server  {"id": 2, "type": "event", "event": {"event_type": "state_changed", "data": {...}}}
```

A failure comes back as
`{"id": n, "type": "result", "success": false, "error": {"code": ..., "message": ...}}`.
The code a client must handle specially is **`unknown_command`**: it means this
backend does not implement that command, and the client should degrade (show a
hint, hide a page) rather than treat it as a fault. That is exactly how
jarvis-web keeps working against Home Assistant, which knows `get_states` and
`call_service` but not everything else.

## Commands

| Command | Purpose |
| --- | --- |
| `ping` | replies `{"id": n, "type": "pong"}` |
| `get_states` | every state object |
| `get_config` | location, version, components, areas |
| `get_services` | the service catalogue: `{domain: {service: {description, fields, supports_response}}}` |
| `call_service` | `domain`, `service`, `service_data`, optional `target` and `return_response`; result carries `context` and `changed_states` |
| `subscribe_events` / `unsubscribe_events` | bus events, optionally filtered by `event_type`; unsubscribe takes `subscription: <the subscribe id>` |
| `fire_event` | put an event on the bus |
| `conversation/process` | one text turn through the conversation agent |
| `jarvis/approve` | resolve a Tier-3 approval the safety gate is holding |
| `config/entity_registry/list` · `/update` | rename, re-area, hide, or set `exposed` on an entity |
| `config/device_registry/list` · `/update` | device names and area assignment |
| `config/area_registry/list` · `/create` · `/update` · `/delete` | areas |
| `assist_pipeline/pipeline/list` | available voice pipelines + the preferred one |
| `assist_pipeline/run` | a voice run (below) |

Registry updates skip **null-valued** fields, so a client clears an assignment
by sending `""` — `{"type": "config/entity_registry/update", "entity_id":
"light.a", "area_id": ""}` — not `null`.

The same operations exist over REST (`GET /api/states`,
`POST /api/services/{domain}/{service}`, `POST /api/config/area_registry/create`
and friends) for scripts that do not want a socket.

## Voice runs

```
client  {"id": 3, "type": "assist_pipeline/run", "start_stage": "stt",
         "end_stage": "tts", "input": {"sample_rate": 16000},
         "conversation_id": null, "pipeline": "jarvis"}
server  {"id": 3, "type": "result", "success": true, "result": null}
server  {"id": 3, "type": "event", "event": {"type": "run-start", "data": {
           "runner_data": {"stt_binary_handler_id": 1, "timeout": 300}}}}
client  <binary>  0x01 + Int16LE PCM     one chunk of 16 kHz mono audio
client  <binary>  0x01                   lone handler-id byte = end of audio
server  ... stt-start, stt-vad-start, stt-vad-end, stt-end, intent-start,
            intent-progress (streaming deltas), intent-end, tts-start,
            tts-end, run-end
```

Two details clients get wrong: the binary prefix byte is the
`stt_binary_handler_id` from `run-start` (not a constant), and pipeline events
arrive under the **run's** message id, so a client must route by id.

`tts-end` carries `{"tts_output": {"url": "/api/tts_proxy/<token>.wav",
"mime_type": "audio/wav"}}`. That path is open — the token in it is the secret —
so a client can fetch it without a bearer header, though sending one is fine.

## The clients

### jarvis-web (browser)

Runs as its own Node process (`node build`) and **proxies** the socket so the
token never reaches the browser: the page opens `ws(s)://<origin>/ws`, and the
server dials `${url}/api/websocket`, does the auth handshake itself, swallows
the `auth_*` frames and relays everything else — JSON and binary alike. TTS
audio goes through `/api/tts?path=…`, which validates the path and attaches the
`Authorization` header server-side.

The voice HUD (`/`) and the management pages (`/devices`, `/areas`,
`/automations`, `/tools`, `/settings`) share that one relay.

| Var | Default | Purpose |
| --- | --- | --- |
| `JARVIS_BACKEND` | `core` | `core` (jarvis-core) or `ha` (Home Assistant) |
| `JARVIS_URL` | — | jarvis-core base URL |
| `JARVIS_TOKEN` | — | jarvis-core token, server-side only |
| `HA_URL` / `HA_TOKEN` | — | used when `JARVIS_BACKEND=ha`, and as the fallback otherwise |
| `JARVIS_PIPELINE` | `Jarvis` | pipeline name to select |
| `JARVIS_TTS_VOICE` | `en_GB-alan-medium` | reported to the page via `/api/config` |
| `PORT` | `8199` | listen port |

The selected backend's variables win and the other pair is the fallback, so an
existing Home-Assistant deployment keeps running after an upgrade without
touching its env file.

```sh
JARVIS_URL=http://jarvis-core:8123 JARVIS_TOKEN=… PORT=8199 node build
```

### Android

`android-app/` talks to jarvis-core directly — no proxy, because there is no
browser to hide the token from. The server URL and token are entered in
Settings and stored on the device; `ServerUrl.websocketUrl()` turns
`http://host:8123` into `ws://host:8123/api/websocket`, and
`assist/AssistPipelineClient` runs the same `assist_pipeline/run` exchange with
the same binary framing. `ManagementActivity` is an origin-locked WebView, so
pointing it at the jarvis-web origin gives the phone the same management console
the desktop has.

### ESP32 satellites and scripts

Same socket, same handshake, same framing — a satellite is just a client that
only ever sends `assist_pipeline/run` and audio. For one-shot scripting the REST
mirror is usually easier:

```sh
curl -H "Authorization: Bearer $JARVIS_TOKEN" http://jarvis-core:8123/api/states
curl -X POST -H "Authorization: Bearer $JARVIS_TOKEN" \
     -H 'content-type: application/json' -d '{"entity_id": "light.lab_lights"}' \
     http://jarvis-core:8123/api/services/light/turn_on
```

## Serving a client from jarvis-core

If a directory named `www/` sits next to the `jarvis` package, jarvis-core
mounts it at `/` (after `/api/*` and `/healthz`, which always win). That is for
statically-built clients. jarvis-web is **not** one of them: it needs its Node
server for the token-hiding relay, so run it as its own process and point
browsers at that origin.

## CORS

`jarvis.cors_allowed_origins` in the YAML config sets the allowed origins
(`*` by default). Credentials are disabled on the CORS middleware on purpose —
authentication is bearer tokens, never cookies.
