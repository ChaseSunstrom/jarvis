# Verification matrix — what is proven, and what is not

This is the honest accounting of what has actually been *tested* in Jarvis, as
opposed to what has been written. It exists because a system this size can look
finished while resting on assumptions nobody has checked, and because the parts
that need your hardware can never be checked from a build machine.

Read it as a claims register. Every row says what is proven, what proves it, and
what command reproduces the proof. Anything that nothing checks is listed as
**Unproven** and named — not softened, not omitted.

Counts and results below were measured on **2026-08-09**. Re-measure before
trusting them; the commands are given so you can.

---

## How to read the levels

| Level | Meaning |
|---|---|
| **Automated** | A test in this repo fails if the behaviour breaks. Needs no hardware, no network, no containers. This is the strongest claim here. |
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
| `jarvis-core` | 1203 | all pass | ~53 s |
| `jarvis-desktop` | 722 | all pass | ~16 s |
| `jarvis-browser` | 328 | all pass | ~2 s |
| `jarvis-orchestrator` | 17 | all pass | ~1 s |
| `jarvis-sandbox` | 6 | all pass | ~1 s |
| `evals` (routing table + its mirrors) | 17 | all pass | <1 s |
| `jarvis-web` (vitest, 13 files) | 194 | all pass | ~2 s |
| `jarvis-web` (Playwright, chromium) | 20 | all pass | ~18 s |
| `android-app/tools` (spec files) | all pass | all pass | ~3 s |

Within `jarvis-core`, by file:

| File | Tests | Covers |
|---|---:|---|
| `test_sensors.py` | 173 | the sensor layer and its inference |
| `test_web_integration.py` | 109 | `web.search`/`fetch`/`crawl`/`browse`, fencing, and the turn-taint that backs it |
| `test_vision.py` | 106 | camera frames as fenced, untrusted input |
| `test_api.py` | 94 | REST + websocket wire contract, auth, binary audio frames |
| `test_features.py` | 115 | the shipped feature set, end to end |
| `test_packaging.py` | 74 | the shipped `config/` is coherent; compose/YAML agreement |
| `test_automation.py` | 72 | triggers, conditions, actions, run modes |
| `test_voice.py` | 67 | pipeline runner, Wyoming protocol framing, pipeline store |
| `test_mqtt.py` | 50 | discovery, entity mapping, value templates |
| `test_llm.py` | 48 | agent, tool registry, the approval gate |
| `test_domains.py` | 47 | every domain service verb |
| `test_recorder.py` | 44 | SQLite recorder, history, logbook, sun, person |
| `test_orchestrator.py` | 47 | delegation, coding jobs, the double-gated shell path |
| `test_device_control.py` | 38 | cross-device command dispatch and tiering |
| `test_local_integrations.py` | 36 | template, rest, command_line, hue, wled, demo |
| `test_api_companion.py` | 28 | the device channel over the websocket |
| `test_companion.py` | 26 | presence ranking, routing, escalation |
| `test_core.py` | 17 | bus, state machine, services, registries |
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
| Wyoming against **real** whisper/piper/openWakeWord | Scripted (reachability + one real synthesis) | `./scripts/e2e-smoke.sh` |
| Ollama client: streaming, tool-call parsing | Automated *against `httpx.MockTransport`* | `test_llm.py`, `test_e2e.py` |
| A **real** model turn | Scripted | `./scripts/e2e-smoke.sh` |
| Tool tiering and the approval gate | Automated | `test_llm.py`, `test_e2e.py` |
| `exclude_entities` blast-radius limit | Automated | `test_e2e.py`, `test_packaging.py` |
| Untrusted web content stays fenced | Automated | `test_web_integration.py` |
| Delegation / coding jobs / the shell tool reach the orchestrator | Automated *against `httpx.MockTransport`* | `test_orchestrator.py` |
| `execute_command` is unreachable from a model turn | Automated | `test_orchestrator.py` |
| The approval secret rides on exactly two request paths | Automated | `test_orchestrator.py` |
| Agent output, diffs and command stdout are fenced | Automated | `test_orchestrator.py` |
| The orchestrator against a **real** running service | **Unproven** | Needs the container up; see *Closing the gaps* |
| MQTT discovery and entity mapping | Automated *with `FakeMqttClient`* | `test_mqtt.py` |
| MQTT against a **real broker** with real devices | **Unproven** | see *Closing the gaps* |
| Hue and WLED | Automated *against `httpx.MockTransport`* | `test_local_integrations.py` |
| Hue / WLED against **real hardware** | **Unproven** | see *Closing the gaps* |
| Cross-device presence, routing, escalation | Automated | `test_companion.py`, `test_api_companion.py`, `test_e2e.py` |

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
| Presence signals, throttling, keyguard gating | Automated *as a Python mirror* | `presence_signals_test.py` |
| Boot timeline, geofence, schedules, screen pruning, task trust/vars | Automated *as Python mirrors* | the remaining `android-app/tools/*.py` |
| **The Kotlin compiles** | **Unproven** | There is no Android SDK in this environment. `./gradlew assembleDebug` has never been run here. |
| **The Kotlin matches its Python mirrors** | **Unproven** | The mirrors are a specification of the intended logic. Nothing mechanically checks that `ai.jarvis.app.*` implements them. This is the single largest unverified claim in the project. |
| The app on a real GrapheneOS phone | **Unproven** | Needs the device. See *Closing the gaps*. |
| Wake word on-device | **Unproven** | — |
| Assist gesture, lock-screen popup, Tier-3 consent screen | **Unproven** | Needs the device. |

### jarvis-desktop / jarvis-browser / jarvis-orchestrator

| Capability | Level | Proof / command |
|---|---|---|
| Desktop agent logic | Automated | `cd jarvis-desktop && python3 -m pytest tests -q` — 722 tests |
| Browser automation service logic | Automated | `cd jarvis-browser && python3 -m pytest tests -q` — 328 tests |
| Orchestrator API and exec gate, including adversarial cases | Automated | `cd jarvis-orchestrator && python3 -m pytest tests -q` — 17 tests |
| Desktop agent against a **real** desktop session | **Unproven** | Needs a logged-in machine with the agent installed. |
| Browser service driving a **real** browser | **Unproven** | Needs the container running with a real chromium. |
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

---

## Known failures, as of 2026-08-09

None. The Playwright failure previously recorded here
(`tests/web/e2e.spec.ts` — "automations page shows last_triggered, toggles and
runs now", which could not find `automation.night_mode` in the mock backend)
now passes; all 20 browser tests are green.

Everything listed in this document passed on the date given.

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
