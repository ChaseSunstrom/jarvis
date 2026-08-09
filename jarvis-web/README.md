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
| `JARVIS_ALLOWED_ORIGINS` | — | Extra origins allowed to open `/ws`, comma-separated (e.g. `https://hud.example`). Same-origin is always allowed; see below |

A url **and** a token must resolve or the `/ws` relay closes with
`server missing JARVIS_URL/JARVIS_TOKEN` (or the `HA_*` names when
`JARVIS_BACKEND=ha`).

### Who may open `/ws`

A socket on `/ws` is an **authenticated admin session** — the server attaches
the backend token to it, so whoever holds one can read every state and every
event and call any service, `lock.unlock` included.

WebSocket upgrades are not covered by the same-origin policy: there is no
preflight, and `Origin` is advisory unless the server checks it. So the relay
checks it (`isOriginAllowed()` in `src/lib/server/backend.ts`, hand-copied into
`server/ws-proxy.js`) and answers **403 before the socket exists**:

* same-origin — allowed, comparing host:port, so a TLS terminator in front does
  not break it;
* an origin in `JARVIS_ALLOWED_ORIGINS` — allowed;
* **no** `Origin` header — allowed. Every browser sends one on a WS handshake,
  so its absence means a script or native client, which a hostile page cannot
  arrange;
* anything else, including `Origin: null` from a sandboxed frame — refused.

Without this, any page the user happens to open — an ad frame, a blog — could
open `ws://jarvis.local:8199/ws` and drive the house, with no token and no
prompt. Set `JARVIS_ALLOWED_ORIGINS` to the exact origin you serve the HUD on
if you want to pin it harder than "whatever `Host` says" (that default still
matches a DNS-rebinding attacker, who controls both headers).

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

## Design system

One source of truth, in three files under `src/lib/styles/`, all imported once
by `+layout.svelte`:

| File | What is in it |
| --- | --- |
| `tokens.css` | every `--jv-*` custom property on `:root` — the whole palette, type scale, spacing, radii, glows, durations |
| `base.css` | document ground, focus ring, selection, scrollbars, and the reduced-motion kill switch |
| `chrome.css` | the shared vocabulary: `.jv-grid`, `.jv-bracket`, panels, rows, pills, buttons, skeletons, toasts, palette, boot |

`src/lib/tokens.ts` mirrors `tokens.css` as data, for the values that have to
reach JavaScript (the HUD's per-state accent). `tokens.test.ts` diffs the two
and fails if they drift, so a colour cannot be changed in one place only.

**No file outside `tokens.css` may contain a raw hex value.** The HUD is the one
place that looks like an exception: it sets `--accent` per pipeline state and
re-derives `--jv-line` / `--jv-line-soft` from it, so the grid, brackets and
glow track the state without any of them owning a colour.

### Palette

| Token | Value | Used for |
| --- | --- | --- |
| `--jv-bg` | `#04070C` | the page ground |
| `--jv-panel` / `--jv-panel-solid` | `rgba(6,18,26,.72)` / `#06121A` | panels; the solid one for anything floating |
| `--jv-accent` | `#3FD8FF` | the system colour: active state, focus, links, the orb |
| `--jv-accent-deep` | `#2BB0D8` | idle/standby |
| `--jv-amber` | `#FF9E2C` | thinking |
| `--jv-gold` | `#FFCF5C` | speaking; notices; reconnecting |
| `--jv-danger` / `--jv-danger-text` | `#FF6B5C` / `#FF9184` | failures — the lighter one for text, so it clears AA |
| `--jv-line` / `--jv-line-soft` / `--jv-line-hair` | accent at 32% / 12% / 8% | borders, the grid, row rules |
| `--jv-text` / `--jv-text-bright` / `--jv-text-dim` | `#D7EDF5` / `#EAF7FC` / `#9FC0CC` | body, emphasis, captions |

Every text token clears **WCAG AA (4.5:1)** on `--jv-bg`, and still clears it at
the lowest opacity the CSS applies. `tokens.test.ts` computes the ratios rather
than trusting the eye.

### Type and chrome

Two families: `--jv-font-chrome` (monospace) for everything that is *system
furniture* — headings, labels, pills, buttons, entity ids, the readout — and
`--jv-font-body` for content the user wrote or the backend named. Chrome text is
uppercase with generous tracking (`--jv-track-chrome` `.16em`,
`--jv-track-wide` `.24em`, `--jv-track-logo` `.5em` for the wordmark).

Size scale: `--jv-fs-2xs` `.55rem` → `--jv-fs-display` `clamp(1.2rem, 3.2vw, 1.9rem)`.

The recurring devices: **corner brackets** (`.jv-bracket` ×4, overridable via
`--jv-bracket-size` / `--jv-bracket-inset`), the **masked technical grid**
(`.jv-grid`, sized by `--jv-grid-size`, faded out by `--jv-grid-mask`), and
**glow as elevation** (`--jv-glow-sm|md|lg` for lit things, `--jv-elev-panel` /
`--jv-elev-float` for things that sit above the page).

### The tab icon

The arc reactor, in the browser tab. It is generated, not drawn: `scripts/icons.mjs`
holds the whole thing as a list of rings, ticks, arcs and discs on a 64-unit
canvas, and emits both the vector and the rasters from that one description.

```
npm run icons              # regenerate static/favicon.{svg,ico} + apple-touch-icon.png
npm run icons -- --check   # fail if the committed files are stale
```

Each primitive declares the pixel range it survives (`minPx` / `maxPx`), because
a 16 px favicon cannot hold a dashed ring whose gaps are a third of a pixel —
the tab-strip sizes get a purpose-drawn simplification with the same silhouette
(dark plate, cyan ring, hot core) instead of a downsample of detail that was
never legible. `--jv-bg` and `--jv-accent` come from `tokens.css`, and
`icons.test.ts` fails if they stop matching, if the committed files stop matching
the description, or if `app.html` stops linking them.

The generated files are committed rather than built, so `vite dev` has a favicon
too. Note that `mrmime` — which sirv, and therefore both `vite dev` and
adapter-node, type static files with — has no entry for `.ico`; the dev plugin in
`vite.config.ts` and the launcher in `scripts/postbuild.mjs` each set
`image/x-icon` before sirv runs, which sirv then keeps.

## Motion

Fast, consistent, and always optional.

| Rule | How |
| --- | --- |
| Boot sequence | scan line → reactor ignite → rings → JARVIS wordmark → system checks → dissolve. ~1.2 s, **once per browser session** (`sessionStorage`), skippable with any key or click, and `pointer-events: none` throughout so it never gates the app |
| Route transition | the console body cross-fades and drifts up `--jv-drift` over `--jv-dur-base` (180 ms) |
| List entrance | rows stagger by `--jv-stagger-step` (26 ms), **capped at `--jv-stagger-cap`** (320 ms) — 200 rows cost 320 ms, not five seconds |
| State change | the affected value pulses (`.jv-pulse`, `--jv-dur-pulse`); restarted imperatively so the second change is as visible as the first |
| Loading | skeleton rows, never an empty flash; then a friendly empty state or a visible error |
| Press | `translateY(1px) scale(.98)` for `--jv-dur-instant` |
| Durations | `--jv-dur-instant` 90 ms · `--jv-dur-fast` 120 ms · `--jv-dur-base` 180 ms · `--jv-dur-slow` 320 ms |
| Easing | `--jv-ease-out` for arrivals, `--jv-ease-in-out` for loops, `--jv-ease-overshoot` where something should land with weight |

**`prefers-reduced-motion: reduce` turns all of it off.** `base.css` cuts every
animation and transition to 0.001 ms — the end state is identical, it simply
arrives at once — and `motion.ts:prefersReducedMotion()` makes the boot sequence
not run at all, since a timeline that gates content cannot be neutralised by
shortening it. `prefersReducedMotion` defaults to **true** when there is no
`matchMedia` to ask (SSR), because guessing "animate" and being wrong is the
failure that actually costs someone.

The boot timeline itself is pure arithmetic in `src/lib/boot.ts` — the web mirror
of the Android app's `ui/BootTimeline.kt`, same six stages, same discipline: one
rAF loop asks it what to draw at time `t`, nothing schedules itself, and
`boot.test.ts` asserts the stages tile `[0, TOTAL_MS]` exactly and that skipping
lands on the same frame the animation would have reached on its own.

## Console: keyboard and status

| Key | Does |
| --- | --- |
| `Ctrl`/`Cmd` `K` | command palette |
| `/` | focus this page's filter |
| `g d` `g r` `g a` `g t` `g s` `g h` | devices · areas · automations · tools · settings · HUD |
| `Esc` | close the palette, or drop focus |
| `↑` `↓` `Enter` `Shift+Enter` | in the palette: move, act, and "open instead of act" |

The palette indexes every entity, area, automation and route. `Enter` on an
entity that can be flipped **toggles it**; on anything else it jumps to the page
that owns it (`?focus=<id>`, which pre-fills that page's filter).
`Shift+Enter` forces the jump. Ranking, wrap-around and "what does Enter mean
here" are pure functions in `src/lib/commandPalette.ts`.

The header's connection indicator is driven by `src/lib/consoleLink.ts` — the
console's own socket, and the only one in the app that reconnects (pages
deliberately do not: a page that lost its socket also lost its subscriptions,
and silently reattaching would leave stale rows looking live). It backs off
exponentially with jitter and says `OFFLINE` after three consecutive failures
while still retrying.

Every `call_service` raises a toast, success or failure (`src/lib/toast.ts`),
alongside the inline error — the toast is what you notice, the inline error is
what is still on screen ten seconds later.

## Accessibility

- Real `<button>`s with `type="button"`; `aria-label` on every icon-only control
  and every unlabelled input.
- `aria-live="polite"` on the HUD transcript and response, on the toast rail and
  on the event log; `role="alert"` on failures.
- A visible focus ring on everything focusable — an `outline`, not a
  `box-shadow`, because half these controls already carry a glow and a
  shadow-based ring loses to it.
- A skip link as the first tab stop; `aria-current="page"` on the active nav
  item, which also gets a lit underline so the current route is not signalled by
  colour alone.
- The command palette is a proper combobox/listbox with `aria-activedescendant`.
- Colour contrast passes AA for text, asserted numerically in `tokens.test.ts`.
- Usable down to phone width (the Android app's WebView): the nav scrolls
  horizontally, panel-head filters take their own line, rows stack, and the
  connection badge drops to its dot with the state left on its `aria-label`.

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
                                     # backend resolution, tts path allow-list, and the
                                     # UI logic: design tokens, motion policy, the boot
                                     # timeline, palette ranking, chords, toasts, the
                                     # reconnecting console link)
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
- The chrome has its own e2e coverage: the boot sequence plays and — proved with
  a hit test rather than a click, which would merely wait it out — never
  intercepts a pointer; `page.emulateMedia({ reducedMotion: 'reduce' })` skips it
  entirely; route changes swap the body and move `aria-current`; the palette
  opens from the keyboard, filters, and toggles an entity; a rejected
  `call_service` raises a toast; the first tab stop has a real focus ring; and
  the console fits a 390 px viewport with nothing clipped off the right edge.
- The mock backend carries a `lock.front_door` with no `lock` domain in its
  service catalogue. That is not an oversight — it is the fixture for "the UI
  offers a control the backend cannot perform", which is the path a silent
  failure used to hide on.

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
