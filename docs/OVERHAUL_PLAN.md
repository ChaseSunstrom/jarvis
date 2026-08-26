# The overhaul — plan of record (26 August 2026)

The operator's brief, verbatim in spirit: *overhaul Jarvis; do not stop until
it is genuinely capable of anything online; the voice tab gets the graph and
things actually going on that look nice (keep the arc reactor); the other
menus simpler, settings especially; "models" must be the actual AI models;
capabilities and intelligence, not trivial tasks — satellites, vision through
cameras, any sensor; animations for when it does things; the Android app as
good as the web and able to do anything Tasker can; local additions only;
don't ask, do; research what to add.*

This document is the spine. Each numbered item is a milestone in
`MILESTONES.md` (M52 onward) with its own failing-first verify script, per
`PROCESS.md`. The research that feeds the capability items is in
`docs/research/` (written by four research agents on the night of the 26th).

## What is true now

- 60 milestones ticked as of 16:30 on the 26th (M27, M50–M55, M57–M60 this
  morning, M62 — the dashboard as the first console tab — in the afternoon);
  M56, M61 (its six gap rows closed on a worktree branch, merging) and M23 are
  open. M56 is built and on the branch,
  its gate 27/28: the one open check is a live look through a served vision
  model, and the model server serves none. M60 is ticked (gate 13/13, the
  full core suite green). M61's phone work compiles, lints and passes its JVM tests here (M08's
  toolchain under $HOME). M23 is the final gate and cannot be green on this host for those two
  reasons and the round-trip threshold (BLOCKERS §2, §3, §4).
- Cameras: the `vision` integration speaks the OpenAI wire to the same model
  server as the chat model, go2rtc restreams behind `--profile cameras`,
  Frigate's events become moments, and it is switched on with no cameras.
  Sensors: HA MQTT discovery in full (event, device_tracker, the birth on
  the prefix, allow/deny ids, canonical units), Tasmota and Shelly
  translated, four sensor tools. Sky: ISS passes, what is overhead, the
  moon, the planets, from cached elements. Web: search, fetch, crawl, browse,
  deep research, and now time — `watch_page`, `watch_feed`, `watch_for`,
  `read_page`, `feed_latest`.
- The phone has a Tasker-shaped automation layer — accessibility UI
  automation, a notification listener, triggers, a task engine, a policy
  engine with tiers and an audit — reached from the hub as `control_device`;
  as of M61's first stage its voice screens draw the activity strip and the
  knowledge graph from the console's contracts and play a reply as the
  console does. Fourteen Tasker rows were closed (loops were already there);
  six stay gap — SMS, the call log, ending a call, NFC, and a camera
  pipeline for photos and barcodes, which is a dependency decision
  (`docs/TOOLING_DECISIONS.md`) — and `ui_key` is a no: an accessibility
  service cannot inject keys.
- The console: five tabs, Reactor II throughout; the voice tab shows the
  graph and the activity strip (M52), the instrument moves for what Jarvis
  does (M53), SETTINGS is five sections with the real models (M54), and
  HOUSE, WORK, KNOWLEDGE and the tools page hold to a menu inventory a test
  reads (M55).
- Speed: the prompt prefix is kept on the server and ordered stable-first,
  the first sentence is spoken before the reply is finished, whisper runs
  int8, `llm.fast_model` is the voice path's when set, and a spoken turn does
  not reason unless `voice: think: true`; a small model's narrated tool call
  is retried under a schema, and a turn that repeats the same call is ended
  and told to answer. Measured: the full-mode median round trip 2.87 s
  (11:54, the record) from 6.67 s (06:54), to `run-end`, which does not
  credit the early first sentence; the 2 s threshold and the 95 % intent
  floor (89.8 %) are recorded missed, not lowered (verification.md).

## The milestones, in order

Dependencies first, value and risk next. Parallel where trees do not touch.

| # | Milestone | Verify | Parallel with |
|---|---|---|---|
| M52 | **VOICE: the graph and the living activity around the reactor** — the knowledge graph on the voice tab, lighting as Jarvis uses it; a live activity strip fed by the bus (tool calls as they happen, tasks stepping, sensors changing, cameras being looked at, moments landing); the reactor's state reflecting real work; the C2 layout kept. | `m52-voice-live.sh`: e2e against the mock (events → graph node lit, activity rows, reactor state), route pass, screenshots | M54, M56–M58 |
| M53 | **Motion when it does things** — one motion design for actions (`docs/design/MOTION.md`): what moves for a tool call, a task step, a memory read, a sensor change, a camera look, an approval, an error; all on `motion.*` tokens; reduced motion honoured; signature recordings in `docs/motion-review/`. | `m53-motion-acts.sh`: motion.spec measures each choreography (no long frames, zero animations under reduced motion), token lint, recordings regenerated | M54 |
| M54 | **Settings that make sense, and the real models** — a MODELS panel that lists what the model server actually serves (name, family and size, role: chat / fast / vision / embeddings / rerank, loaded now, in use by Jarvis for which job) with a per-role choice; the settings information architecture cut to what a person changes (Assistant · Voice · House · Console · Tools), plain labels, the rest behind "everything". | `m54-settings-models.sh`: core `jarvis/llm/models` from the gateway (unit + live), e2e for the panel and the IA, look.spec | M52, M53 |
| M55 | **Simpler menus everywhere** — HOUSE, WORK, KNOWLEDGE trimmed to their jobs; one control per row where one will do; the tools page as one searchable list; no duplicate ways to the same thing; a menu inventory pinned in `docs/UI_MIGRATION.md` §4. | `m55-menus.sh`: the inventory test, dead-controls, look.spec, screenshots | M56–M58 |
| M56 | **Cameras and local vision** — the vision integration speaks OpenAI-style images (a GGUF VLM on the model server), go2rtc restreams RTSP/USB/ONVIF cameras (compose profile `cameras`), optional Frigate events → moments; snapshot and "looking" on the voice tab; consent and audit unchanged. | `m56-vision.sh`: unit (payload contract against a fake VLM), fixture camera (a still on the fixture site) end to end, live when a VLM is served | M57, M58 |
| M57 | **Any sensor** — Home Assistant MQTT discovery ingested (Zigbee2MQTT, ESPHome, rtl_433, Tasmota payload fixtures) into entities with history; tools to read, aggregate and compare; sensor changes on the voice tab. | `m57-sensors.sh`: discovery fixtures → entities (unit), mosquitto publish → Jarvis answers (live) | M56, M58 |
| M58 | **The sky** — skyfield with cached TLEs: the next ISS pass for the house, what is overhead now, moon phase, planets tonight; offline after the first download; optional ADS-B (readsb, profile `radio`). | `m58-sky.sh`: fixed TLE + fixed time (unit), live "when is the ISS next visible" | M56, M57 |
| M59 | **Anything online, locally** — watch a page for a change, feeds, a reader that survives JavaScript, "tell me when…" as scheduled research → a moment; all self-hosted. | `m59-online.sh`: fixture page changes → moment (live), unit for the watcher | M60 |
| M60 | **Intelligence and speed** — the operator's facts: llama-swap serves `qwen3.8-27b` for everything (≈75 tok/s, 256k context — fast enough) with `qwen3.6-35b` configured as "fast" and idle; so the wait on a voice turn is STT on shared vCPUs, prompt prefill of a large system prompt, and TTS start — not the model's speed. Scope: prompt-prefix caching through the gateway (`cache_prompt`), a leaner system prompt measured in tokens, sentence-streamed TTS, faster-whisper sized to the CPU (int8, a smaller model for the wake path); grammar-constrained tool calls; the task planner batching read-only steps; evals re-measured, never lowered. | `m60-intelligence.sh`: routing ≥ 90 %, intent ≥ 95 %, the proactive scenario inside its budget on the fast path where the hardware allows | M59 |
| M61 | **Android: the equal of the web, and of Tasker** — the phone's screens the console's (voice tab with the graph and activity, motion); the action registry audited against Tasker's categories and completed (comms, connectivity, device settings, media, apps, screen, clipboard, NFC, intents, HTTP, variables, flows); triggers as profiles; "on my phone, do X" end to end from the hub. Build, unit, lint, goldens only — no device. | `m61-android-tasker.sh`: registry coverage table vs `docs/ANDROID_TASKER_PARITY.md`, unit tests per action family, goldens, the tools mirror | M56–M60 |
| M62 | **The dashboard, a destination** — the operator's ask: "make the dashboard not its own subtab, and actually a main thing". The first console tab, its own path, no sections; the phone opens on it; the M48 cap becomes six (`DEVIATIONS.md` §20). | `m62-dashboard-main.sh` | S |
| M23 | **Final integration** — every box, `make verify-all`, the suite in full mode, docs re-measured. Deps extended to M62. | `m23-final-integration.sh` | — |

## How the work runs

- One milestone, one verify script that fails first, one commit that ticks it.
- The stack is one thing at a time; the console's port likewise. Agents run
  in worktrees on distinct ports and hand back commits.
- Research documents are read before the capability milestone they feed;
  their recommendations are adopted only when they are local, pinned and
  fit the compose profiles.
- The guardrails stay: consent before a camera is read, fenced untrusted
  content, approval tiers on actions, the sandbox's isolation, secrets never
  in the tree.
- Every hour, a scheduled prompt re-enters this plan at the next unchecked
  milestone.

## Motion when it does things — the brief for M53

Read `docs/design/README.md` (C2) first. The rule: motion tells you what
Jarvis is *doing*, never decorates. One vocabulary:

| Jarvis is… | what moves | where |
|---|---|---|
| listening | the level arc breathes with the room; blades idle | reactor |
| thinking | the inner ring sweeps; blades slow | reactor |
| calling a tool | a blade group lights and sweeps once; the call line draws in from the left with its name, then its result | reactor + activity strip |
| stepping a task | the task's ring gains a tick; the plan blade fills | WORK, activity strip |
| reading memory | the graph node blinks once, its edges glow briefly | graph |
| reading a sensor | the figure counts to the new value; the row's rule flashes | activity strip, HOUSE |
| looking at a camera | the lens irises in, a "looking" caption, irises out | reactor caption |
| waiting on you | the held bar slides up; the warn rule pulses slowly | held bar |
| speaking | the level arc follows the voice; blades in cadence | reactor |
| an error | the warn rule snaps in; the reactor's rim goes to `color.orb.error` for one blink | reactor |

Every duration and easing is a `motion.*` token; every colour a `color.*`
token; `prefers-reduced-motion` reduces each to a state change with no
transition.

## Android — the parity brief for M61

Two halves. **Looks and works like the web**: the same five sections, the
same instrument, the graph and activity on the phone's voice screen, the same
motion vocabulary on Canvas, one state machine (`reactor_geometry.json`).
**Does what Tasker does**: the action registry measured against Tasker's
action categories and filled — a parity table in
`docs/ANDROID_TASKER_PARITY.md` with each action's tier (direct / confirm /
approve), the permission it needs, and its test. Profiles are triggers plus
conditions plus a task, editable on the phone and from the hub. Everything
verifiable without a device is verified; what needs a handset goes to
`docs/ANDROID_DEVICE_TESTS.md`.
