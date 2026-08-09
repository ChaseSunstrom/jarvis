# jarvis-web

Iron-Man-style HUD **and management console** for Jarvis. A **thin client**: the
browser captures mic audio, streams it over a WebSocket to the backend's
`assist_pipeline/run` API, renders streaming transcript/response, plays the
returned TTS, and animates a WebGL orb (idle / listening / thinking / speaking,
reactive to audio levels). The management pages drive the same socket.

All intelligence (STT, LLM, intents, TTS) lives in the backend.

## Backends

The default backend is **jarvis-core**, the standalone home-automation platform.
Home Assistant still works: the two speak the same websocket contract
(`/api/websocket`, the `auth_required` → `auth` → `auth_ok` handshake, the
`assist_pipeline/*` commands and `/api/tts_proxy/…` media paths), so a single
client covers both.

`JARVIS_BACKEND` picks which pair of variables wins; the other pair stays as a
fallback, so an HA-only deployment keeps working untouched.

| `JARVIS_BACKEND` | url comes from | token comes from |
| --- | --- | --- |
| `core` (default) | `JARVIS_URL`, else `HA_URL` | `JARVIS_TOKEN`, else `HA_TOKEN` |
| `ha` | `HA_URL`, else `JARVIS_URL` | `HA_TOKEN`, else `JARVIS_TOKEN` |

Both the `/ws` relay and the `/api/tts` proxy target whichever backend is
selected. `/api/config` reports the resolved backend (kind, url, which env vars
were used, whether a token is present — never the token itself), which is what
the settings page renders.

Commands only jarvis-core implements degrade gracefully: the client turns an
`unknown_command` error into a hint on the page instead of an exception, so the
voice HUD and every page that only needs `get_states` / `call_service` still work
against Home Assistant.

## Environment variables

| Var | Default | Purpose |
| --- | --- | --- |
| `JARVIS_BACKEND` | `core` | `core` (jarvis-core) or `ha` (Home Assistant) |
| `JARVIS_URL` | — | jarvis-core base URL, e.g. `http://jarvis-core:8123` |
| `JARVIS_TOKEN` | — | jarvis-core access token. **Server-side only.** |
| `HA_URL` | — | Home Assistant base URL. Used when `JARVIS_BACKEND=ha`, or as the fallback when `JARVIS_URL` is unset |
| `HA_TOKEN` | — | Long-lived HA access token. **Server-side only.** |
| `JARVIS_PIPELINE` | `Jarvis` | Assist pipeline name to select (falls back to the backend's preferred pipeline) |
| `JARVIS_TTS_VOICE` | `en_GB-alan-medium` | Exposed to the client via `/api/config` (voice is configured in the pipeline itself) |
| `PORT` | `8199` | HTTP listen port |
| `ORIGIN` | (assumes https) | Set to the public origin (e.g. `http://localhost:8199`) so adapter-node builds correct absolute URLs / cookie flags |

A url **and** a token must resolve or the `/ws` relay closes with
`server missing JARVIS_URL/JARVIS_TOKEN` (or the `HA_*` names when
`JARVIS_BACKEND=ha`).

## Pages

| Route | What it does |
| --- | --- |
| `/` | Voice HUD: push-to-talk, hands-free VAD, barge-in, orb, latency readout |
| `/devices` | Every entity grouped by area, live over `subscribe_events`, with inline controls (toggle, brightness, cover open/stop/close + position, climate setpoint and HVAC mode, media transport and volume, locks, selects, numbers) |
| `/areas` | Create / rename / delete areas and assign entities to them |
| `/automations` | Enable, disable and run automations; shows `last_triggered` |
| `/tools` | The LLM tool catalogue, an entity-exposure switchboard, and a test-runner that calls a tool with JSON arguments and prints the result |
| `/settings` | Resolved backend, pipeline and TTS voice, plus a filterable live event stream |

An entity's area is its own `area_id`, falling back to its device's — the same
rule jarvis-core resolves voice commands with.

`/tools` prefers a `jarvis/tools/list` websocket command. Backends that do not
implement it (including today's jarvis-core) fall back to the service catalogue
from `get_services`, projected into the same shape, and test runs go out as
`call_service`.

Clearing an area assignment sends `area_id: ""`, not `null`: jarvis-core's
registry skips null-valued fields on update.

## How the token stays server-side

The browser never sees the backend token:

- The page connects to `ws(s)://<origin>/ws`. The Node server (adapter-node
  build, see `server/ws-proxy.js`) opens the real connection to
  `${url}/api/websocket`, performs the auth handshake (`auth_required` →
  `auth` with the token → `auth_ok`) itself, swallows those auth frames, and
  then relays JSON and binary audio frames in both directions. Every management
  command rides the same relay.
- TTS audio: the `tts-end` event carries a path on the backend. The browser
  requests `/api/tts?path=...`; `mediaProxyTarget()` in
  `src/lib/server/backend.ts` resolves it and only lets it through if the
  **normalised** URL is still same-origin with the backend and still under
  `/api/tts_proxy/` or `/api/tts/`. Normalisation matters: the URL parser
  collapses `%2e%2e` into a dot segment just like `..`, so a substring test for
  `..` alone leaves `/api/tts_proxy/%2e%2e/%2e%2e/api/states` reachable — with
  the admin token attached. Redirects are refused (`redirect: 'error'`) so a
  30x cannot pivot the proxy onto another host. Then the bytes are streamed with
  the `Authorization: Bearer` header attached server-side.
- The browser session is identified only by a random `httpOnly` cookie set in
  `hooks.server.ts` — nothing auth-related in `localStorage`.
- CSP (`svelte.config.js`): `default-src 'self'`, no CDN assets; everything is
  bundled.

Because the token lives in the server's environment, `/settings` shows the
backend URL read-only and reports only *whether* a token is configured. Point
the app at a different backend by changing `JARVIS_BACKEND` / `JARVIS_URL` /
`JARVIS_TOKEN` where the web server runs and restarting it.

### Where the trust boundary actually is

Hiding the token is not access control. jarvis-web itself has **no login**: the
`jarvis_sid` cookie `hooks.server.ts` mints is an identifier, and nothing reads
it. The `/ws` relay authenticates to the backend with the admin token on behalf
of whoever opened the socket, so anything that can reach this server's port can
call any service — unlock a lock, run any automation.

Two consequences worth stating plainly:

- **Reachability is authority.** Bind it to a trusted network, or put an
  authenticating reverse proxy in front of both `/` and `/ws`. Do not expose
  the port to the internet.
- **`/ws` does not check `Origin`.** WebSocket handshakes are exempt from the
  same-origin policy and from CORS preflight, so a page on any other origin —
  loaded in a browser that can route to this server — can open `/ws` and drive
  the relay. Until `server/ws-proxy.js` rejects upgrades whose `Origin` header
  is not an allow-listed one, a reverse proxy that enforces it is the mitigation.

## Secure context / mkcert

`getUserMedia` requires a secure context:

- `http://localhost:8199` works out of the box (localhost is always a secure
  context).
- Accessing via a LAN IP (`http://192.168.x.y:8199`) will **not** get mic
  access. Either generate a locally-trusted cert with
  [mkcert](https://github.com/FiloSottile/mkcert) (`mkcert -install && mkcert
  jarvis.lan 192.168.x.y`) and terminate TLS in front of the app (Caddy,
  Traefik, nginx), or reach the box over WireGuard using a hostname signed by
  your local CA.

## Barge-in

The mic stays alive while TTS plays. If sustained speech energy is detected
(energy VAD, `src/lib/wake.ts`) while the assistant is speaking, all scheduled
audio sources are stopped immediately and a new pipeline run starts — you can
talk over Jarvis.

The "Hands-free (VAD)" toggle starts a run automatically on speech and ends it
on silence. `src/lib/wake.ts` documents the `WakeWordDetector` interface where
an openWakeWord-WASM model can be plugged in later (P4).

## Run

```sh
npm install
npm run dev          # dev server (includes the /ws proxy)
npm run build        # vite build + install ws-proxy launcher into build/
JARVIS_URL=http://jarvis-core:8123 JARVIS_TOKEN=... PORT=8199 node build

# against Home Assistant instead
JARVIS_BACKEND=ha HA_URL=... HA_TOKEN=... PORT=8199 node build
```

Health check: `wget -qO- http://localhost:8199/healthz` → `{"status":"ok"}`.

Docker: `docker build -t jarvis-web .` — multi-stage, listens on 8199,
`CMD ["node","build"]`.

## Tests

```sh
npm test                             # vitest unit tests (framing, downsample, pipeline
                                     # events, jarvisClient, the browser transport,
                                     # backend resolution, tts path allow-list)
node ../tests/web/smoke.test.mjs     # protocol smoke test against the mock backend
npx playwright test                  # e2e: built app + mock backend + fake mic in chromium
```

- `../tests/web/mock-ha.mjs` implements the shared contract (auth handshake,
  `assist_pipeline/pipeline/list`, `assist_pipeline/run` event sequence,
  binary audio framing with the 1-byte end-of-audio frame, a real WAV served at
  the TTS path) plus the management commands: `get_states`, `get_services`,
  `get_config`, `call_service` (which really mutates its world and pushes
  `state_changed`), `subscribe_events` / `unsubscribe_events` and the area /
  entity / device registries. It answers `unknown_command` for
  `jarvis/tools/list` on purpose, so the e2e run exercises the fallback path.
  `/_test/protected` is a token-guarded endpoint that stands in for the
  backend's REST surface; the e2e suite tries to reach it through `/api/tts`
  with both plain and percent-encoded traversal and asserts it cannot.
- `serve-e2e.mjs` points `JARVIS_URL` at the mock and `HA_URL` at a dead port,
  so the whole e2e suite doubles as a precedence check on `resolveBackend`.
- The e2e run uses the preinstalled chromium at `/opt/pw-browsers/chromium`
  with fake media-device flags; `?e2e=1` makes push-to-talk auto-stop after
  1.5 s so runs are deterministic.
- Latency measurements (audio-end → stt-end / first delta / tts-start) are
  shown in the status line and logged to the console.

## Audio path

- Capture: `getUserMedia` (mono, AEC/NS/AGC on) → `AudioWorklet`
  (`static/worklets/pcm-worklet.js`) downsamples context-rate Float32 to
  16 kHz Int16 (linear interpolation, ~1024-sample batches) and reports RMS.
  The DSP is mirrored as pure functions in `src/lib/audio/downsample.ts` for
  unit testing.
- Uplink framing: each binary frame is 1 byte `stt_binary_handler_id` +
  Int16LE PCM; end of audio is a single-byte frame (`src/lib/pipeline.ts`).
- Playback: `/api/tts` proxy → `decodeAudioData` → `AudioBufferSourceNode` →
  `AnalyserNode` → destination; the analyser drives the orb while speaking.
