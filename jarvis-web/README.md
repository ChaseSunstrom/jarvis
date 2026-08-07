# jarvis-web

Iron-Man-style HUD for the Jarvis voice assistant. A **thin client** for Home
Assistant's Assist pipeline: the browser captures mic audio, streams it over a
WebSocket to HA's `assist_pipeline/run` API, renders streaming
transcript/response, plays the returned TTS, and animates a WebGL orb
(idle / listening / thinking / speaking, reactive to audio levels).

All intelligence (STT, LLM, intents, TTS) lives in Home Assistant.

## Environment variables

| Var | Default | Purpose |
| --- | --- | --- |
| `HA_URL` | — (required) | Home Assistant base URL, e.g. `http://homeassistant:8123` |
| `HA_TOKEN` | — (required) | Long-lived HA access token. **Server-side only.** |
| `JARVIS_PIPELINE` | `Jarvis` | Assist pipeline name to select (falls back to HA's preferred pipeline) |
| `JARVIS_TTS_VOICE` | `en_GB-alan-medium` | Exposed to the client via `/api/config` (voice is configured in the HA pipeline itself) |
| `PORT` | `8199` | HTTP listen port |
| `ORIGIN` | (assumes https) | Set to the public origin (e.g. `http://localhost:8199`) so adapter-node builds correct absolute URLs / cookie flags |

## How the token stays server-side

The browser never sees `HA_TOKEN`:

- The page connects to `ws(s)://<origin>/ws`. The Node server (adapter-node
  build, see `server/ws-proxy.js`) opens the real connection to
  `${HA_URL}/api/websocket`, performs the HA auth handshake (`auth_required` →
  `auth` with the token → `auth_ok`) itself, swallows those auth frames, and
  then relays JSON and binary audio frames in both directions.
- TTS audio: HA's `tts-end` event carries a path on HA. The browser requests
  `/api/tts?path=...`; the server validates the path (must start with
  `/api/tts_proxy/` or `/api/tts/` — SSRF guard) and streams the bytes from HA
  with the `Authorization: Bearer` header attached server-side.
- The browser session is identified only by a random `httpOnly` cookie set in
  `hooks.server.ts` — nothing auth-related in `localStorage`.
- CSP (`svelte.config.js`): `default-src 'self'`, no CDN assets; everything is
  bundled.

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
HA_URL=... HA_TOKEN=... PORT=8199 node build
```

Health check: `wget -qO- http://localhost:8199/healthz` → `{"status":"ok"}`.

Docker: `docker build -t jarvis-web .` — multi-stage, listens on 8199,
`CMD ["node","build"]`.

## Tests

```sh
npm test                             # vitest unit tests (framing, downsample, pipeline events)
node ../tests/web/smoke.test.mjs     # protocol smoke test against the mock HA server
npx playwright test                  # e2e: built app + mock HA + fake mic in chromium
```

- `../tests/web/mock-ha.mjs` implements the HA contract (auth handshake,
  `assist_pipeline/pipeline/list`, `assist_pipeline/run` event sequence,
  binary audio framing with the 1-byte end-of-audio frame, and a real WAV
  served at the TTS path).
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
