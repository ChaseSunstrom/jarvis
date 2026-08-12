# Verification matrix — what is proven, and what is not

This is the honest accounting of what has actually been *tested* in Jarvis, as
opposed to what has been written. It exists because a system this size can look
finished while resting on assumptions nobody has checked, and because the parts
that need your hardware can never be checked from a build machine.

Read it as a claims register. Every row says what is proven, what proves it, and
what command reproduces the proof. Anything that nothing checks is listed as
**Unproven** and named — not softened, not omitted.

Counts and results below were measured on **2026-08-12**. Re-measure before
trusting them; the commands are given so you can.

---

## How to read the levels

| Level | Meaning |
|---|---|
| **Automated** | A test in this repo fails if the behaviour breaks. Needs no hardware, no network, no containers. This is the strongest claim here. |
| **Containerised** | A GitHub Actions job (`.github/workflows/compose-smoke.yml`) starts the real `docker compose` stack and fails if it does not come up. Needs Docker, the network and roughly half an hour, which is why it is a CI job and not a pytest suite. |
| **Scripted** | `scripts/e2e-smoke.sh` checks it on your box, against your real services. Proves the install, not just the code. |
| **Manual** | Needs a human, a phone, a microphone or a GPU. The command or procedure is given; nothing runs it for you. |
| **Unproven** | Nothing checks this. It may well work. Nobody has demonstrated that it does. |

A row can be Automated *and* Manual: the logic is proven in isolation and the
integration with real hardware still needs a human.

---

## Run everything

All of these are run from the repository root, each in its own subshell so the
directory changes do not compound:

```bash
# Python suites — no hardware, no network
for d in jarvis-core jarvis-desktop jarvis-browser jarvis-orchestrator jarvis-sandbox; do
    ( cd "$d" && echo "== $d" && python3 -m pytest tests -q )
done

# The routing table and the two places that mirror it
( cd evals && python3 -m pytest test_routing.py -q )

# The Android logic mirrors (the Kotlin itself cannot be built here — see below)
( cd android-app/tools && for f in *.py; do python3 "$f" || echo "FAILED: $f"; done )

# Web: unit tests, then the browser suite
( cd jarvis-web && npm run build && npm test && npx playwright test )

# On your own hardware, against your own services
./scripts/e2e-smoke.sh
```

### Suite sizes, measured 2026-08-09

| Suite | Tests | Result | Runtime |
|---|---:|---|---:|
| `jarvis-core` | 1545 | all pass | ~150 s |
| `jarvis-desktop` | 722 | all pass | ~16 s |
| `jarvis-browser` | 328 | all pass | ~2 s |
| `jarvis-orchestrator` + `jarvis-sandbox` | 23 | all pass | ~2 s |
| `evals` (routing table + its mirrors) | 17 | all pass | <1 s |
| `jarvis-web` (vitest, 20 files) | 325 | all pass | ~4 s |
| `jarvis-web` (Playwright, chromium) | 44 | all pass | ~56 s |
| `android-app/tools` (spec files) | all pass | all pass | ~3 s |

Within `jarvis-core`, by file:

| File | Tests | Covers |
|---|---:|---|
| `test_sensors.py` | 195 | the sensor layer and its inference |
| `test_features.py` | 119 | the shipped feature set, end to end |
| `test_web_integration.py` | 112 | `web.search`/`fetch`/`crawl`/`browse`, fencing, and the turn-taint that backs it |
| `test_vision.py` | 106 | camera frames as fenced, untrusted input |
| `test_api.py` | 94 | REST + websocket wire contract, auth, binary audio frames |
| `test_packaging.py` | 88 | the shipped `config/` is coherent; compose/YAML agreement |
| `test_automation.py` | 75 | triggers, conditions, actions, run modes |
| `test_voice.py` | 68 | pipeline runner, Wyoming protocol framing, pipeline store |
| `test_mqtt.py` | 53 | discovery, entity mapping, value templates |
| `test_llm.py` | 60 | agent, tool registry, the approval gate |
| `test_domains.py` | 47 | every domain service verb |
| `test_speaker.py` | 55 | the voiceprint: DSP against the DFT definition, and whether it separates anyone |
| `test_speaker_gate.py` | 33 | what the system does with that answer — what a refused turn reaches, and what the API hands out |
| `test_orchestrator.py` | 49 | delegation, coding jobs, the double-gated shell path |
| `test_recorder.py` | 44 | SQLite recorder, history, logbook, sun, person |
| `test_device_control.py` | 38 | cross-device command dispatch and tiering |
| `test_local_integrations.py` | 37 | template, rest, command_line, hue, wled, demo |
| `test_api_companion.py` | 28 | the device channel over the websocket |
| `test_companion.py` | 26 | presence ranking, routing, escalation |
| `test_core.py` | 28 | bus, state machine, services, registries |
| **`test_e2e.py`** | **12** | **the whole platform booted from a config file** |

---

## What `test_e2e.py` proves

This is the only suite that boots the real platform. Everything else tests one
subsystem with the rest faked. It builds a `Jarvis` from a temporary
`configuration.yaml` exercising jarvis+areas, demo, template, recorder, history,
logbook, sun, person, input helpers, script, scene, automation, companion, voice
and llm, and then drives it.

Exactly three things are faked, each at the point where the network would be:

* **Wyoming STT / TTS / wake** — plain objects injected through `jarvis.data`,
  the seam the voice integration documents for this purpose.
* **Ollama** — an `httpx.MockTransport` serving genuine NDJSON, so the real
  `OllamaClient` still does the streaming, chunk accumulation and tool-call
  parsing.
* **The phone/desktop transport** — a callable handed to `CompanionManager`,
  exactly as the API layer hands it one in production.

Everything between those edges is production code.

| Test | What it establishes |
|---|---|
| `test_every_configured_integration_sets_up_and_entities_appear` | A realistic config file produces a working house: every integration sets up, 18 named entities exist across every source, areas and aliases resolve, entity→device→area wiring holds, and a template sensor really evaluated against a demo sensor. |
| **`test_voice_round_trip_from_pcm_to_a_light_that_is_really_on`** | **The money test.** PCM into the pipeline → fake STT → the real conversation agent → mock Ollama returns a `turn_on` tool call → the real tool registry → the real `light.turn_on` service → the entity object → fake TTS. Asserts the exact 12-event `assist_pipeline` sequence, that the audio bytes reached STT, that the tool result went back to the model verbatim, that the synthesised WAV parses, **and that `light.bed_light` genuinely went from `off` to `on`**. |
| `test_a_wake_word_run_prefixes_the_same_pipeline` | The satellite shape: one continuous stream whose first chunk trips the wake word and whose remainder is the utterance. Proves the wake stage hands audio on rather than consuming it. |
| `test_the_pipeline_and_service_calls_over_the_websocket_api` | The same round trip over a real ASGI client: websocket handshake, `assist_pipeline/pipeline/list`, `assist_pipeline/run` with binary audio frames, the identical event sequence on the wire, then `call_service` observed through a `subscribe_events` subscription, the TTS proxy serving unauthenticated WAV, and the REST conversation endpoint. |
| `test_a_state_trigger_runs_a_script_that_changes_another_entity` | A state trigger fires an automation that calls a script that changes two further entities, and `last_triggered` is stamped on both automation and script. Also that an unrelated transition does not re-fire it. |
| `test_a_scene_applies_across_domains` | One `scene.turn_on` reaches lights and switches with the right verb per domain. |
| `test_companion_ask_reaches_the_phone_and_the_automation_branches` | Two devices register; presence routes the question to the phone rather than the desktop; an automation blocks inside `companion.ask`; the phone answers; the automation resumes and takes the branch. |
| `test_a_no_answer_leaves_the_branch_untaken` | The negative case: answering "no" changes nothing. |
| `test_states_are_recorded_and_history_get_reads_them_back` | States land in SQLite and `history.get` reads them back with attributes intact, sourced from the database rather than the live state machine. |
| `test_a_restart_from_the_same_config_dir_keeps_everything` | Stop, rebuild from the same directory: areas, devices, entity registry entries, a runtime-created area with aliases, a user-typed entity alias and all four input-helper values survive — and the stored values win over `initial:`. |
| `test_a_gated_action_is_held_even_when_the_model_asks_for_it` | With the lock exposed and the model calling `lock_control`, the tool returns `approval_required`, the door does not move, the request is listed as pending, and the approval event carries the verbatim arguments. |
| `test_the_excluded_entity_is_invisible_to_the_model` | `exclude_entities` holds through the whole booted stack: the garage door cannot be read, cannot be driven, is absent from `list_entities` and absent from the house summary in the prompt. |

The money test was checked against a deliberate regression: with the
`light.turn_on` service replaced by a no-op, the light stays `off` and the test
fails. The assertion is load-bearing, not decorative.

---

## What `scripts/e2e-smoke.sh` proves

Run it on the machine that will actually run Jarvis:

```bash
./scripts/e2e-smoke.sh                              # everything it can reach
OLLAMA_URL=http://192.168.1.10:11434 ./scripts/e2e-smoke.sh
./scripts/e2e-smoke.sh --keep                       # keep the temp config to poke at
```

It boots a throwaway `jarvis-core` against a temporary config directory — your
own config, database and tokens are never touched — mints a token, and drives
the REST and websocket APIs. Checks needing a service that is not running are
**skipped with the reason**, not failed, and every network call is bounded, so a
dead service costs a timeout rather than a hung terminal. Exit status is 0 when
nothing failed (skips are fine), 1 on any failure, 2 if the environment is too
broken to test.

| Check | Needs |
|---|---|
| server starts and reports healthy | nothing |
| unauthenticated and bogus-token requests are refused | nothing |
| `GET /api/` with a token | nothing |
| entities exist (demo devices present) | nothing |
| service call changes state (`off → on` at brightness 200 `→ off`) | nothing |
| input helper writes and persists to `.storage` | nothing |
| recorder + history round trip through SQLite | nothing |
| voice pipelines are configured | nothing |
| websocket handshake, `get_states`, `call_service` | nothing |
| Ollama is reachable, and lists its models | Ollama |
| a real LLM turn answers in prose | Ollama + a pulled model |
| Wyoming STT / wake word are reachable | whisper / openWakeWord |
| Wyoming TTS synthesises a playable WAV | piper |
| SIGTERM shuts down cleanly with no tracebacks | nothing |

Observed on a machine with no local services: 10 pass, 5 skip, boot to healthy
in ~0.9 s. With stand-in services on the expected ports: 15 pass.

**What the smoke script does not do:** it never opens a microphone, never plays
audio, never drives a browser, and never touches a phone. Those are below.

---

## What `.github/workflows/compose-smoke.yml` proves

Until this job existed, nothing in CI had ever run `docker compose up`.
`test_packaging.py` reads the compose file as a document and checks what it
*says*; `scripts/e2e-smoke.sh` boots jarvis-core from source on your own
machine. Neither one starts a container, so "the stack comes up" rested on the
last time somebody typed the command by hand.

That gap cost six bugs in one fresh install. Three were startup failures:
`WHISPER_MODEL` in `.env.example` named a sherpa model while the pinned image is
faster-whisper (`ValueError: Invalid model size`); jarvis-core could not write
its bind-mounted `/config` as uid 10003 (`PermissionError: [Errno 13]`); and
`cap_drop: [ALL]` removed the three capabilities mosquitto's entrypoint needs in
order to stop being root (`chown: /mosquitto/data: Operation not permitted`).
From outside, all three looked identical — `Restarting (N)` — and none was
visible to a test that reads the file rather than running it. The other three
were config wiring: `OLLAMA_URL` documented and read by nothing, a chat model
with no variable at all, and `pull access denied` warnings from services
declaring `image:` and `build:` with no `pull_policy`.

The job copies `.env.example` to `.env` verbatim, adds only the three secrets
that file says to generate, builds `jarvis-core:local` and
`jarvis-browser:local` from source, and starts the stack with both optional
profiles enabled. Running the *documented defaults* is the point: substituting
CI-friendly model names would have let the sherpa one through untouched.

| Check | What it catches |
|---|---|
| No container has a non-zero `RestartCount`, and every long-running one is `running` | The load-bearing one. A crash loop, whatever caused it. `docker inspect` rather than `docker compose ps`: the STATUS column is prose that has changed wording between releases, and it describes the container at that instant — sample a flapping container during its up phase and it says "Up 1 second". `.RestartCount` only goes up. |
| The one-shot `jarvis-config-init` exited 0 | The chown that makes the bind mount writable never ran, or failed. |
| `GET :8080/healthz` returns `status: ok` and `running: true` | jarvis-core boots from the shipped `config/` in a container, not just under pytest. |
| Sockets accept a connection on 10300, 10200, 10400, 1883, 8210, 8080 | Stronger than "did not crash". faster-whisper resolves the model name *before* it binds, so an open 10300 is positive proof that `WHISPER_MODEL` is a name it accepts. |
| Every healthcheck reports `healthy`, never `starting` or `unhealthy` | mosquitto's healthcheck publishes to its own broker, so this is the broker accepting a client and not merely holding a port. |
| A file can be written to `/config/.storage` from inside the container, as the uid it really runs as | The positive half of the permissions bug. An app that swallowed the `EACCES`, or wrote later, would look identical from outside. |
| `LLM agent ready: model=… url=…` echoes back two sentinel values handed in through `.env` | A knob documented in `.env.example` that reaches the container and is then ignored — which is exactly how a hardcoded `llm.url` looks from outside. |
| The `up` output contains no `pull access denied` | A service with both `image:` and `build:` but no `pull_policy: build`. |
| Every service in `docker-compose.yml` is either started or named in the skip list | Silent narrowing. Adding a service to the compose file fails this job until someone decides, in writing, whether it is covered. |

On failure the job prints `docker compose ps`, each container's `docker inspect`
state (including `OOMKilled`) and `docker compose logs`, so a crash loop is read
in the run rather than reproduced locally. It always tears down with
`docker compose down --volumes --remove-orphans`.

**What it covers:** jarvis-config-init, jarvis-core, jarvis-browser,
wyoming-whisper, wyoming-piper, wyoming-openwakeword, mosquitto (`--profile
mqtt`) and searxng (`--profile search`). The last two are in deliberately: they
are the only services carrying the `cap_add: [CHOWN, SETGID, SETUID]` fix, so
they are the only places that particular bug can come back.

**What it does not prove:**

* **photon is never started.** It is the one service excluded. Its first run
  downloads a geocoding index — tens of GB with `PHOTON_REGION` unset, which is
  what `.env.example` ships — and the runner has neither the disk nor the hour.
  A photon-specific startup failure would still reach a fresh install.
* **Nothing is asked to do its job.** Whisper is never given audio, Piper never
  synthesises, openWakeWord never hears a wake word, the browser never fetches a
  page, and no MQTT device is discovered. The claim is "it started and it is
  listening", which is the claim the three startup bugs falsified. Ollama is not
  in the stack at all, so no model turn happens.
* **Ollama connection failures are expected and ignored.** The job never greps
  logs for errors; it asks whether processes are alive and serving. A service
  that logs a stack trace every second and stays up passes.
* **Host networking on a runner is not your LAN.** Every service binds a port on
  a throwaway machine that has nothing else on those ports, and the only client
  is the job itself. A conflict with something you already run, and the ufw
  rules in `scripts/apply-firewall.sh` that decide who may reach any of it, are
  still unproven.
* **Upstream images are pinned to `:latest`.** A green run says the stack came
  up against whatever those tags meant that day. The job runs weekly on a cron
  for exactly that reason, but between runs the claim ages.
* **One architecture, one kernel.** amd64 on GitHub's Ubuntu image. Nothing here
  says anything about arm64, a Raspberry Pi, or a host whose kernel refuses the
  unprivileged user namespaces chromium's sandbox wants.

---

## The matrix

### jarvis-core

| Capability | Level | Proof / command |
|---|---|---|
| Bus, state machine, services, registries | Automated | `test_core.py` |
| YAML loader: `!include*`, `!secret`, `!env_var`, packages | Automated | `test_packaging.py` |
| Every domain service verb | Automated | `test_domains.py` |
| Automations: triggers, conditions, actions, run modes | Automated | `test_automation.py` |
| Scripts and scenes | Automated | `test_automation.py`, `test_e2e.py` |
| Input helpers, persistence across restart | Automated | `test_e2e.py` |
| Recorder / history / logbook (SQLite) | Automated | `test_recorder.py`, `test_e2e.py` |
| Sun and person | Automated | `test_recorder.py` |
| Template, rest, command_line entities | Automated | `test_local_integrations.py` |
| REST + websocket wire contract | Automated | `test_api.py`, `test_e2e.py` |
| Token auth: creation, verification, revocation | Automated | `test_api.py`; smoke script checks it live |
| Voice pipeline runner and its event contract | Automated | `test_voice.py`, `test_e2e.py` |
| Wyoming protocol framing | Automated *against a local fake server* | `test_voice.py` |
| The **real** whisper/piper/openWakeWord containers start and listen | Containerised | `compose-smoke.yml` — an open socket on 10300 is proof faster-whisper accepted `WHISPER_MODEL`, since it resolves the name before it binds |
| Wyoming against **real** whisper/piper/openWakeWord, with audio | Scripted (reachability + one real synthesis) | `./scripts/e2e-smoke.sh` — the CI job never speaks or transcribes |
| Ollama client: streaming, tool-call parsing | Automated *against `httpx.MockTransport`* | `test_llm.py`, `test_e2e.py` |
| A **real** model turn | Scripted | `./scripts/e2e-smoke.sh` |
| Tool tiering and the approval gate | Automated | `test_llm.py`, `test_e2e.py` |
| `exclude_entities` blast-radius limit | Automated | `test_e2e.py`, `test_packaging.py` |
| Untrusted web content stays fenced | Automated | `test_web_integration.py` |
| Delegation / coding jobs / the shell tool reach the orchestrator | Automated *against `httpx.MockTransport`* | `test_orchestrator.py` |
| `execute_command` is unreachable from a model turn | Automated | `test_orchestrator.py` |
| The approval secret rides on exactly two request paths | Automated | `test_orchestrator.py` |
| Agent output, diffs and command stdout are fenced | Automated | `test_orchestrator.py` |
| A command too long for the service is refused, never trimmed to fit | Automated | `test_orchestrator.py` |
| A released command is audited before the result comes back, so a failed call cannot hide it | Automated | `test_orchestrator.py` |
| The orchestrator against a **real** running service | **Unproven** | Needs the container up; see *Closing the gaps* |
| MQTT discovery and entity mapping | Automated *with `FakeMqttClient`* | `test_mqtt.py` |
| The shipped broker starts and accepts a client | Containerised | `compose-smoke.yml --profile mqtt`; mosquitto's healthcheck is a real `mosquitto_pub` against itself |
| MQTT against a real broker with **real devices** | **Unproven** | Nothing publishes a discovery message or drives a device. see *Closing the gaps* |
| Hue and WLED | Automated *against `httpx.MockTransport`* | `test_local_integrations.py` |
| Hue / WLED against **real hardware** | **Unproven** | see *Closing the gaps* |
| Cross-device presence, routing, escalation | Automated | `test_companion.py`, `test_api_companion.py`, `test_e2e.py` |
| The shipped `docker compose` stack builds, starts and stays up | Containerised | `compose-smoke.yml` — no container may have restarted, and the API answers `/healthz` from inside one |
| `./config` is writable by the uid the image runs as | Containerised | the job writes and removes a probe file from inside jarvis-core, as uid 10003, on the real bind mount |
| `OLLAMA_URL` and `OLLAMA_MODEL` reach the agent rather than being decoration | Automated + Containerised | `test_packaging.py` reads the wiring; the job hands both a sentinel and requires the startup log to echo them back |

### jarvis-web (HUD + management console)

| Capability | Level | Proof / command |
|---|---|---|
| Component and helper logic | Automated | `npm test` — 194 tests |
| The built app in a real browser | Automated *against a mock backend* | `npx playwright test` — 20 tests |
| The HUD driven against a **real jarvis-core** | **Unproven** | The Playwright suite runs the built app against `tests/web/mock-ha.mjs`, a JS stand-in. Nothing in CI points the HUD at an actual server. See *Closing the gaps*. |
| Microphone capture in the browser | **Unproven** | Playwright runs with `--use-fake-device-for-media-stream`; that proves the code path, not that a real microphone is captured, encoded and streamed. |
| Audio playback of TTS replies | **Unproven** | `--autoplay-policy=no-user-gesture-required` bypasses the thing most likely to break in a real browser. |
| WebGL arc-reactor orb rendering | **Unproven** | Headless chromium with software rendering says nothing about how it looks or performs on the user's GPU. |

### android-app

| Capability | Level | Proof / command |
|---|---|---|
| Policy truth table (AUTO / NOTIFY / CONFIRM) | Automated *as a Python mirror* | `python3 android-app/tools/policy_truth_table_test.py` |
| Action table (48 actions) | Automated *as a Python mirror* | `action_table_test.py` |
| Device-channel protocol, host and URL rules | Automated *as a Python mirror* | `channel_protocol_test.py` |
| Command dispatch (1152 modelled dispatches) | Automated *as a Python mirror* | `dispatch_spec_test.py` |
| **The in-app updater can actually install** | Automated *as a Python mirror* | `updater_install_test.py` (11 checks). `PackageInstaller.commit()` shows nothing — it sends `STATUS_PENDING_USER_ACTION` to an `IntentSender`, carrying the system's install activity, and something has to start it. Nothing received that broadcast, so every update downloaded, committed, and installed nothing, while Settings printed "confirm the system prompt". Whether a real APK installs over a real phone is still **Unproven**. |
| **Every dangerous permission is actually requested** | Automated *as a Python mirror* | `runtime_permissions_test.py` (22 checks, 17 permissions). The manifest promised a runtime request "at the moment it is first needed" and nothing outside `RECORD_AUDIO` and `POST_NOTIFICATIONS` ever made one — `requestPermissions` is a method on `Activity` and every command arrives in a Service, so SMS, calls, contacts, calendar, location and step count were declared, checked for, denied and never asked for. The spec holds the manifest and `RuntimePermissions.ALL` against each other, and holds both against the checklist. |
| Presence signals, throttling, keyguard gating | Automated *as a Python mirror* | `presence_signals_test.py` |
| Boot timeline, geofence, schedules, screen pruning, task trust/vars | Automated *as Python mirrors* | the remaining `android-app/tools/*.py` |
| **Every WebSocket presents the bearer token on the upgrade** | Automated *as a Python mirror* | `websocket_auth_test.py`. Written after `CompanionVoiceClient` was found authenticating only in band: against jarvis-web's relay the upgrade itself is authenticated, so it got a 401 before any frame — and the caller's graceful fallback to a notification made it look deliberate for the life of the app. The spec covers all three clients so a fourth cannot inherit nothing. |
| **A question is asked on the surface already on screen** | Automated *as a Python mirror* | `speech_host_test.py`. The `CompanionSpeechHost` seam was written, documented and never constructed by anything, so every question started a full-screen activity over whatever conversation was up. The first check in the file is simply "does anything construct one". |
| **A contact name is resolved to a number before the consent prompt** | Automated *as a Python mirror* | `contact_resolve_test.py`. "What was approved is what runs" applied to `send_sms` and `place_call`: a prompt reading `to: "Mum"` while the message goes to a number nobody saw is a prompt that lied. |
| **Reminders survive a reboot** | Automated *as a Python mirror* | The store and the boot re-arm are structural; `BootReceiver` re-arms ahead of the automation master switch, because a reminder is the user's own. Whether an `AlarmManager` alarm actually fires after a real restart is **Unproven** — it needs a device that has been turned off and on. |
| **The Kotlin compiles** | **Automated (CI only)** | `.github/workflows/android-apk.yml` runs a real Gradle build on every push and publishes the APK. It does not run in the dev container — there is still no Android SDK here — so a local `make test` says nothing about it. Read the workflow result, not the local suite. |
| **The Kotlin matches its Python mirrors** | **Unproven** | The mirrors are a specification of the intended logic. Nothing mechanically checks that `ai.jarvis.app.*` implements them. This is the single largest unverified claim in the project. |
| Headset routing rules (kind × opt-in × link availability) | Automated *as a Python mirror* | `audio_route_test.py` — 19 checks, 28 combinations |
| Headset button policy, incl. "never answers a consent prompt" | Automated *as a Python mirror* | `media_button_test.py` — 24 checks, all 400 input combinations |
| **The headset button reaches the gate at all** | Automated *as a Python mirror* | Same file, last eight checks. Everything above them tested a pure function nothing called: there was no `MediaSession` in the app, so no media button event ever reached the process, and all 400 combinations described a feature that did not exist while `docs/earpiece.md` documented it as shipped. `HeadsetButtonSession` is the caller. Whether a real Bluetooth headset's press arrives is still **Unproven**. |
| **Headset mode can be switched on** | Automated | `headsetMode`, `headsetButton` and `warmLink` had getters, defaults and a documentation page, and nothing in the app wrote any of them. `media_button_test.py` now asserts the settings screen writes all three and that `JarvisConversation` reads `warmLink`. |
| **The panic kill switch can be set** | Automated *as a Python mirror* | `policy_truth_table_test.py`. Four components read `policy.panic` and deferred to it, the automations screen rendered "PANIC — everything is stopped", and no code path wrote it: an exhaustively-tested rule about a state the phone could not enter, or leave. |
| The earpiece feature is *wired*, not just tested | Automated | `audio_route_test.py` asserts JarvisConversation resolves a route, passes the profile to the mic, ties playback usage to it, and releases the route on teardown |
| Gradle Kotlin DSL traps (`java.` accessor shadowing, import order) | Automated | `gradle_script_test.py` |
| The app on a real GrapheneOS phone | **Unproven** | Needs the device. See *Closing the gaps*. |
| **Wake word — always-on listening** | Automated *as a Python mirror*, plus **Unproven** on hardware | `WakeWordService` is a real foreground service with a real caller. `wake_listener_test.py` and `wake_start_policy_test.py` pin when it may start (Android forbids a microphone service starting from the background, so it needs a foreground Activity, a battery exemption, or the overlay grant) and the hand-back of the mic. Whether a phone in a pocket hears you is a claim only a phone can settle. |
| **Wake word detected on the phone rather than the server** | Automated *as a Python mirror* | `OnDeviceWakeWord` runs openWakeWord's three-model ONNX chain locally, so nothing is streamed until the name is said. `wake_score_test.py` pins the threshold / consecutive-frames / refractory logic — the half that can be proved without a device — and `tool_run_test.py`'s neighbours cover the rest. The weights are downloaded from the user's own server at runtime, never bundled: `jarvis-core/tests/test_model_mirror.py`. |
| **Speaker verification separates two voices** | Automated *against synthetic speech* | `jarvis-core/tests/test_speaker.py` (55). `tests/synth_voice.py` generates talkers from a source-filter model — the verifier's own claim about what distinguishes people, written as a signal generator. The cast includes two deliberately hard cases: a speaker at the owner's pitch with a different tract, and a breathy one whose pitch is not measurable at all. Everything is seeded. |
| **Speaker verification on REAL human speech** | **Unproven** | Nothing here has heard a person. Synthetic voices settle that the code separates signals differing in the cues it claims to use; they say nothing about false-accept and false-reject rates on real speech in a real room. This is why `observe` mode exists and why `docs/voice-identity.md` tells you to spend a few days in it reading your own scores. Only your own voice can close this row. |
| **A refused turn cannot reach a tool** | Automated | `jarvis-core/tests/test_speaker_gate.py` (33) asserts it by behaviour — the fake conversation agent records whether it was called — rather than by reading the code meant to prevent it. Also covers: `observe` never blocks, `off` does not even buffer the audio, a crashing verifier lets the turn through, a bad `mode:` falls back to `off`, and the wake leg is never used to identify anyone. |
| **The voiceprint never leaves the server** | Automated | Same file: every enrolment response is searched for the profile's own numbers. The audio is checked to be dropped when the run ends. |
| **Enrolment from the phone** | **Unproven** | `VoiceIdentityActivity` and `VoiceIdentityClient` are written and wired, and the server half they call is covered above. Nothing has driven the screen — it needs a device with a microphone. |
| **On-device transcription cannot bypass the speaker gate** | Automated | `test_speaker_gate.py` — a transcript flagged `audio_derived` is refused while enforcing, typed text is not, `observe` and `off` still let it through, and a server with nobody enrolled is unaffected. The phone's half (suspending the local path) is structural in `JarvisConversation.startLocalTurn`. The two are independent: the server holds even if the phone is wrong. |
| **Verifying the speaker ON the phone** | **Impossible on this path, not unfinished** | `SpeechRecognizer.createOnDeviceSpeechRecognizer` owns the microphone and hands the app partial text and an RMS level, never samples — so there is no audio on the device to embed. A Kotlin port of the embedding would have no input. See DEVIATIONS §10. |
| **Speech to text on the phone** | **Unproven** | `LocalTranscriber` uses `createOnDeviceSpeechRecognizer`, which is network-free *by contract* from API 31. Whether a given phone provides one at all — a degoogled build commonly does not — is checked at runtime and reported, never silently fallen back on. Nothing here proves the transcription quality. |
| An actual Bluetooth earpiece | **Unproven** | The routing rules and the wiring are checked; no headset has been paired to a real phone running this build. Echo cancellation in particular is a claim about hardware behaviour that only hardware can settle. |
| Assist gesture, lock-screen popup, Tier-3 consent screen | **Unproven** | Needs the device. |
| The floating orb is accepted by a real WindowManager | **Automated (emulator)** | `AssistOverlayTest` grants the appop through the shell, attaches the real `TYPE_APPLICATION_OVERLAY` window, and asserts it is attached, sized and visible. Written after the surface was reported broken three times and diagnosed by reading code — which found a plausible cause each time and was wrong twice. |
| The orb is solid, and is not inside a box | **Automated (emulator)** | The same test draws the view at full microphone level into a bitmap and asserts the centre is opaque and the corners are empty. Both complaints were invisible to a `background == null` check: the box was the halo growing past the view's bounds and being square-clipped by the parent, which only happened *while somebody was speaking*. |
| Un-pairing: a device can be cut off, including its live connection | Automated | `test_pairing.py::test_revoking_hangs_up_the_live_socket`. Revoking used to mean "revoked at the next reconnect" — a phone holds its command socket for days, so a device you had just cut off kept reading every state change until something unrelated dropped the connection. The console panel is covered by a Playwright case. |
| Pairing a phone by scanning a QR | Automated | `jarvis-core/tests/test_pairing.py` (23) for the code/token split, single use, expiry, per-caller rate limiting and the authenticated/unauthenticated split; `android-app/tools/pairing_payload_test.py` and `pairing_claim_test.py` for the parse and the exchange; a Playwright case for the console's half. The camera itself is **Unproven** — it hands off to whatever scanner the user installed. |

### jarvis-desktop / jarvis-browser / jarvis-orchestrator

| Capability | Level | Proof / command |
|---|---|---|
| Desktop agent logic | Automated | `cd jarvis-desktop && python3 -m pytest tests -q` — 722 tests |
| Browser automation service logic | Automated | `cd jarvis-browser && python3 -m pytest tests -q` — 328 tests |
| Orchestrator API and exec gate, including adversarial cases | Automated | `cd jarvis-orchestrator && python3 -m pytest tests -q` — 17 tests |
| Desktop agent against a **real** desktop session | **Unproven** | Needs a logged-in machine with the agent installed. |
| The browser service's container starts, refuses to run unauthenticated, and answers `/healthz` | Containerised | `compose-smoke.yml`; the image is built from source in the job, and the service exits at startup unless both secrets are set |
| Browser service driving a **real** browser | **Unproven** | The CI job never fetches a page. Needs the container running with a real chromium. |
| Sandbox network isolation as deployed | Manual | `./scripts/egress-audit.sh` against the live stack |
| Firewall rules as deployed | Manual | `DRY_RUN=1 ./scripts/apply-firewall.sh` to preview, then run it |

### The security model

| Property | Level | Proof |
|---|---|---|
| The server may raise a tier, never lower it | Automated | `test_llm.py`, `android-app/tools/policy_truth_table_test.py` |
| CONFIRM is never auto-approved or remembered | Automated | `policy_truth_table_test.py` |
| A gated tool returns `approval_required` and does not run | Automated | `test_llm.py`, `test_e2e.py` |
| The approval carries verbatim parameters, not the model's paraphrase | Automated | `test_llm.py`, `test_e2e.py` |
| An approval cannot be replayed | Automated | `test_llm.py`, `test_orchestrator.py` |
| The shell path is gated twice, in two processes, with two credentials | Automated | `test_orchestrator.py`, `jarvis-orchestrator/tests/test_api.py` |
| A command rewritten in flight is refused, not approved | Automated | `test_orchestrator.py` |
| Holding the API token alone cannot execute anything | Automated | `jarvis-orchestrator/tests/test_api.py::test_bearer_token_alone_cannot_approve` |
| The persona promises no tool that is not registered | Automated | `test_orchestrator.py::test_the_persona_prompts_tools_all_exist` |
| Refusals fail closed (`"false"` denies) | Automated | `test_llm.py` |
| Fetched content is fenced before the model sees it, and cannot close its own fence | Automated | `test_web_integration.py`, `test_vision.py`, `test_orchestrator.py` |
| Untrusted content cannot reach a dispatcher without fresh approval | Automated | The fence is wording; the control is the tier. Every source that fences also marks the turn, so a later `control_device` is requested at CONFIRM: `test_web_integration.py::test_a_page_read_earlier_in_the_turn_forces_a_device_action_to_confirm`, `test_vision.py::test_looking_at_a_camera_raises_the_bar_for_the_rest_of_the_turn`, `test_orchestrator.py::test_command_output_raises_the_bar_for_the_rest_of_the_turn`, and `test_device_control.py::test_every_integration_that_fences_content_also_raises_the_tier`, which walks the source tree so a *new* integration cannot fence its output and forget the mark. |
| A device answer is data, not authorisation | Automated | `test_api_companion.py`, `test_e2e.py` |
| Excluded entities are unreachable by every tool | Automated | `test_e2e.py`, `test_packaging.py` |
| **The on-device policy engine enforces this in Kotlin** | **Unproven** | Proven only in the Python mirror. See the android-app row above. |
| A question Jarvis asks a human is gated like an action | Automated | `test_ask_user.py` — `ask_user` is a Tier-3 request, so it is single-use, expiring and resolvable only by a person. The answer reaches exactly one argument, named by the tool, so it can never re-target a held action: proved in both directions. |
| A question raised by a tainted turn says where its words came from | Automated | `test_ask_user.py`. The tier decides what may run; it cannot decide whether the sentence on screen is trustworthy, and a question — unlike an action, which displays pinned entity ids — displays whatever the model wrote. Marked, not refused: the legitimate case is a turn that read a page and needs to ask which result was meant. |

---

## Three failures that this document would not have caught

Worth recording, because the first two were failures of *process* rather than of
coverage, and this file is exactly the sort of document that makes them
possible.

**A mutation stub was committed and pushed.** `PolicyEngine.effective_tier` in
jarvis-desktop was replaced with `return requested_tier or local_tier` — the
inverse of the invariant the device-side safety model rests on — and shipped in
db44263. Three existing tests fail against it. Nobody re-ran them between
mutating the code and committing, and the push was reported as green from a
local run that predated the commit while CI was still in progress. Fixed in
9696f19; `.github/workflows/ci.yml` now fails on a `MUTANT` marker in any
source file. **The lesson is that "the suite is green" means nothing unless the
suite ran against the commit.**

**The APK had not built since 9a6777a and the matrix did not say so.** This
document recorded "The Kotlin compiles — Unproven", which reads like a gap in
tooling. It was worse than that: the build was actively red for four commits
with a script-compilation error, so `assertNoTestHooksInRelease` — the check
that keeps debug-only hooks out of a release APK — had never executed once.
"Unproven" and "broken" are different states and this file conflated them.

**The app did not start on Android 11, and nothing in the repository knew.**
The first time `app/src/androidTest` was ever executed on a device — the
emulator job of `e2e.yml`, API 30 — `AppLaunchTest` failed on its first test and
took the whole instrumentation process with it:

```
java.lang.RuntimeException: Unable to start activity
  ComponentInfo{ai.jarvis.app/ai.jarvis.app.MainActivity}:
java.lang.NullPointerException: Attempt to invoke virtual method
  'android.view.WindowInsetsController
   com.android.internal.policy.DecorView.getWindowInsetsController()'
  on a null object reference
  at com.android.internal.policy.PhoneWindow.getInsetsController(PhoneWindow.java:3880)
  at ai.jarvis.app.ui.JarvisUi.immersive(JarvisUi.kt:46)
  at ai.jarvis.app.MainActivity.onCreate(MainActivity.kt:70)
```

`JarvisUi.immersive` read `Window.insetsController` from `onCreate`, before
`setContentView`. On API 30 that getter dereferences the decor view with no null
check, and the decor is not installed yet — so the crash happens *inside the
getter*, where the Kotlin `?.` cannot help. All three immersive screens (home,
assist popup, companion prompt) shared the line, so all three were dead on
Android 11. The APK built, every `src/test` unit test passed, and the release
scan was clean the entire time: none of them starts an Activity. Fixed by going
through `window.decorView.windowInsetsController`, which installs the decor and
returns a controller that replays once the window is attached;
`android-app/tools/window_insets_test.py` fails on the old form.

**The lesson is that "it compiles", "it packages" and "it starts" are three
claims, and only the first two were ever being checked.**

The next run of the same suite went 35/36, and the one failure was the harness
blaming the app. `NavigationTest` asserts that Settings' AUTOMATIONS button
toasts rather than crashing, and reported "No toast was posted at all" — while
logcat from the same second showed `JarvisScreens` catching the missing class
and `NotificationService` retiring the toast it had shown. `UiAutomation` has a
single accessibility-event queue, and `executeAndWaitForEvent` clears it and
lowers `mWaitingForEventDelivery` on the way out *even when it is the inner of
two nested calls* — so the scroll inside the tap unsubscribed the toast wait
wrapping it. The identical assertion on the home screen passed, because that
button needs no scroll. Fixed by resolving the control before the toast window
opens; `instrumentation_contract_test.py` now fails on a toast action that waits
for anything.

The run after that went 35/36 again, on a different test and the same kind of
mistake: `BootAnimationTest` demanded more than ten `onHomeAlpha` callbacks for
a 1400ms sequence and got nine, because this emulator paints six
`BlurMaskFilter` glyphs a frame through swiftshader at about 6fps. The
assertion was reporting the runner's frame rate. "It animated rather than
collapsing" is a claim about elapsed time, so it is now made as one — the
callbacks must span at least `HANDOFF_START_MS`, which a collapsed run (two
callbacks, microseconds apart, from inside `skip()`) cannot do at any speed.
**A test that cannot tell a slow machine from a slow app should not claim
either.**

## Known failures, as of 2026-08-09

Both `ci.yml` path failures recorded here are now fixed, and fixing the second
one uncovered a third that had been invisible.

* `python · tools/orchestrator/sandbox/evals` — referenced `jarvis_tools/tests`,
  which has not existed since the HA-era cleanup, so the job exited 4 before
  running anything. The stale path is gone from the pytest line and from the
  `compileall` line in `static checks`; the orchestrator, sandbox and routing
  suites it was supposed to run now actually run.
* `web · build + unit + e2e` — Playwright reported `No tests found` plus an
  import failure on `@playwright/test`. `testDir` pointed at `../tests/web`,
  outside the `jarvis-web` package, and Node resolves a module from the
  importing **file's** directory: `tests/web/` has no `node_modules` in a fresh
  checkout. It passed locally only because that directory happens to exist on
  developer machines from an earlier `npm install` — and it is gitignored, so
  nothing in the repo revealed the dependency. The spec now lives at
  `jarvis-web/e2e/e2e.spec.ts`, inside the package that owns Playwright.

  **What that uncovered, and the second bug under it:** with the specs
  discoverable again, all 20 launched — and all but two then failed with
  `Failed to launch chromium because executable doesn't exist at
  /opt/pw-browsers/chromium`. `playwright.config.ts` pinned `launchOptions
  .executablePath` to a developer container's layout, a path that exists on no
  CI runner. That was invisible for the same reason: a suite reporting "No
  tests found" never launches a browser. Removed — Playwright finds its own
  browser from the default cache on CI and from `PLAYWRIGHT_BROWSERS_PATH` in a
  container. **20 of 20 pass.**

  **And the third bug under *that*.** One report was still unexplained: a run
  in the dev container — where the pinned path does resolve — in which 19
  passed and only `push-to-talk round trip renders transcript and response`
  failed, timing out at 15 s on the transcript. A missing executable cannot
  produce that shape; it fails every test that needs a browser, not one. Nor
  did the browser binary matter: under both the pinned chromium and the
  headless shell Playwright resolves for itself, the fake device delivers the
  same 47 104 PCM bytes to the mock and the suite is green here, 11 full runs
  and 21 repeats of that test alone.

  The defect is in the HUD, and it is the race the rest of this file already
  guards against. `page.goto` resolves on `load`, which is earlier than
  hydration. The line after it — `expect(status).toContainText(/standby/i)` —
  looks like the gate for that and was not one: `statusMsg` starts at
  `booting`, the label ignored `booting`, so the *server-rendered* markup
  already said STANDBY. The assertion passed off HTML no client code had
  touched, the click that follows landed on a button with no handler bound
  yet, and nothing ever started a run — 15 s later the transcript was still
  empty. Every other test in the file waits for
  `link-status[data-status=connected]` first, with a comment saying that is
  how it proves the page hydrated. This one had no equivalent, because the
  HUD offered none.

  Fixed in the app, not the test: `booting` now renders CONNECTING, so
  STANDBY means what this HUD uses it to mean — hydrated, socket open, ready
  for a press — and the assertion already in the test becomes the gate it was
  written to be. `online` had drawn that distinction all along; only the label
  did not. Reproduced in both directions by delaying every module 1.2 s
  (`page.route('**/*.js', …)`): before the change the transcript assertion
  times out at 15 s against an empty `transcript`, the reported failure
  exactly; after it, the same body completes the round trip in 4.2 s.
  Whether that is what the original run hit cannot be settled after the fact —
  the suite passes here with and without the fix at normal speed — but it is a
  defect that produces that failure and nothing else found does.

- **jarvis-core's 1253 tests had not completed on CI at all.** Found while
  reading the run for `04bb677`: the `python · jarvis-core` job reached 80 % of
  the dots and then sat there until the job's 20-minute limit, and the result
  was reported as `cancelled` — which looks like somebody pushed over it, not
  like a failure. The same thing had happened on the previous commit, and the
  run's *other* six jobs were green, so the workflow read as passing.

  Two tests in `tests/test_voice.py` parked forever. Python 3.12 changed
  `asyncio.Server.wait_closed()` to wait for every connection the server
  accepted rather than only for the listening socket, so a fake Wyoming handler
  that returned with its writer still open never let the shutdown finish. On
  3.11 — which this repo is usually developed on, and which is why the suite is
  green in 50 s locally — `wait_closed()` returned the moment the listener was
  shut and the missing close was invisible. CI and the Docker image are both
  3.12.

  Proved by rebuilding CI's environment locally (`python3.12 -m venv`,
  `requirements.txt`, `pytest pytest-asyncio`), where the suite hangs the same
  way, and reduced to a four-case table: on 3.12, closing the *client* never
  releases the server, only closing the handler's own writer does. Fixed with
  one `one_shot_server` helper that wraps every handler, plus a bounded
  regression test that fails in ten seconds with a name instead of parking the
  run. The same environment then showed `test_packaging` spending 10.5 s per
  test inside a hardcoded ten-second wait for an absent MQTT broker; that wait
  is now `mqtt.ready_timeout`, default 2 s, and the suite went 239 s → 88 s.

  The CI-side fix is `--timeout=120 --timeout-method=signal` on every Python
  job. A hang is now one named failing test with the rest of the suite still
  running, instead of twenty minutes of silence labelled `cancelled`.

  Local development on 3.11 while CI and the image run 3.12 is what hid it. If
  you are changing anything that touches asyncio lifecycles, run the suite under
  3.12 before believing it.

- **The `e2e · android emulator` job had been red on every push since the AVD
  cache started hitting.** Not flaky, and not anything the instrumented suite
  tests: the emulator action's own boot sequence died before `script` ran.

  ```
  Successfully loaded snapshot 'default_boot' using 6438 ms
  adb ... shell getprop sys.boot_completed -> 1
  Emulator booted.
  adb ... shell input keyevent 82
  java.lang.RuntimeException: android.os.DeadSystemException
  ```

  `sys.boot_completed` is part of the state a snapshot restores, so it reads
  `1` the instant the snapshot loads and says nothing about whether *this*
  boot's `system_server` is alive. The action's readiness gate is therefore
  vacuous on a restore, and the unlock keyevent it sends immediately afterwards
  lands on a system still coming back up. A cache hit every run means this
  every run. It is also unreachable from our side — that unlock is inside the
  action, before `script` — so the only lever is to refuse the snapshot.

  `-no-snapshot-load` on the instrumented step makes `boot_completed` mean what
  the action thinks it means. Verified by the run for `011b205`: the step got
  past boot and the whole `End-to-end` workflow went green for the first time,
  against `7df9f3e` and the four commits before it where the same job died
  about a minute in. Two contract tests in
  `testing/e2e/test_ci_workflow_contract.py` pin the flags, each checked to
  fail without them.

  This one hid behind the same shape as the row above: the emulator job is the
  slow one, the other jobs were green, and a boot failure inside a third-party
  action reads as infrastructure rather than as something in this repo.

The lesson is the one this document keeps relearning, alongside the mutation
stub and the four-commit APK breakage: a suite that does not run is
indistinguishable from a suite that passes, and this file is the place that
difference has to be written down. Its newest form is that a *timed-out* suite
is worse than a failing one, because CI does not colour it red.

Everything else listed in this document passed on the date given.

These counts were taken while other work on the repository was still in flight,
and the web suite in particular moved during the measurement (a phone-width
layout failure appeared and was fixed within the same session). Re-run the
commands above rather than trusting the numbers on a later date.

---

## Closing the gaps — what only you can check

These need your hardware. None of them are covered above, and none of them
should be assumed to work until you have done them once.

### 1. The Kotlin actually builds

Needs an Android SDK with platform 35 and JDK 17+ (`android-app/README.md` has
the details). No wrapper jar is committed:

```bash
cd android-app
gradle wrapper              # once; writes gradlew and the wrapper jar
./gradlew :app:assembleDebug
```

Until this has run, treat the Android app as unbuilt source. If it builds,
install it and check the Tier-3 consent screen appears for a `CONFIRM` action
and that dismissing it runs nothing.

### 2. The HUD against a real jarvis-core

From the repository root:

```bash
TOKEN="$(cd jarvis-core && python3 -m jarvis --config ./config --create-token hud)"
( cd jarvis-core && python3 -m jarvis --config ./config --port 8080 ) &

cd jarvis-web && npm run build
JARVIS_BACKEND=core JARVIS_URL=http://127.0.0.1:8080 JARVIS_TOKEN="$TOKEN" node build
```

(Minting the token first is deliberate: it must be in the store before the
server loads it. This writes `jarvis-core/config/.storage/`, which is where
your real tokens and registries live — it is not currently in `.gitignore`, so
check `git status` before committing after doing this.)

Then open the HUD in a real browser and confirm: the orb renders and animates,
tap-to-talk captures the microphone, the transcript streams, and the reply plays
back as audio. Every one of those is unproven today.

### 3. The voice stack end to end, with a microphone

```bash
docker compose up -d          # whisper, piper, openWakeWord, ollama
./scripts/e2e-smoke.sh        # proves they are reachable and piper speaks
python3 scripts/pipeline-smoke.py   # the existing pipeline probe
```

Then say the wake word out loud and confirm detection, transcription accuracy
and reply latency. Wake-word *accuracy* — false accepts and false rejects in
your actual room, with your actual voice — is unproven and cannot be tested any
other way. `docs/wake-word-training.md` covers improving it.

### 4. Real devices

Point `mqtt:` at your broker and confirm Zigbee2MQTT/Tasmota/ESPHome devices
appear by discovery. Fill in `hue:` and `wled:` with real addresses and confirm
the entities go live and respond. All three are tested only against fakes.

### 5. The isolation claims

```bash
./scripts/egress-audit.sh              # sandbox has only lo; no LAN, no internet
DRY_RUN=1 ./scripts/apply-firewall.sh  # preview, then run for real
```

`docs/security.md` states these as properties of the deployment. They are true
only once these scripts have been run against the live stack and have passed.

### 6. Performance

Nothing anywhere measures latency, GPU throughput, memory under load, or how
long a cold model takes to answer. The smoke script prints timings for its own
checks, which is a starting point and nothing more.

---

## Maintaining this document

Whenever the answer to "does this work?" changes, this file changes with it.
Two rules keep it worth reading:

1. **Never promote a row without a command that demonstrates it.** "Probably
   fine" is Unproven.
2. **Re-measure the counts** rather than editing them by hand. Every number in
   this file came from the command printed beside it.
