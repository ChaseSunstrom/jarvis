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

### Suite sizes, measured 2026-08-12

| Suite | Tests | Result | Runtime |
|---|---:|---|---:|
| `jarvis-core` | 1758 | all pass | ~180 s |
| `jarvis-desktop` | 803 | all pass | ~16 s |
| `jarvis-browser` | 328 | all pass | ~2 s |
| `jarvis-orchestrator` + `jarvis-sandbox` | 23 | all pass | ~2 s |
| `evals` (routing table + its mirrors) | 17 | all pass | <1 s |
| `evals` (entity resolution) | 22 + 19 skipped | all pass | <1 s |
| `jarvis-web` (vitest, 24 files) | 365 | all pass | ~6 s |
| `jarvis-web` (`svelte-check`) | 415 files | 0 errors | ~5 s |
| `jarvis-web` (Playwright, chromium) | 59 | all pass | ~80 s |
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
| `test_speaker.py` | 70 | the voiceprint: DSP against the DFT definition, and whether it separates anyone |
| `test_speaker_gate.py` | 63 | what the system does with that answer — what a refused turn reaches, who is speaking, what the API hands out (M71) |
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

### Dashboards and metrics

| Claim | Level | Proof |
|---|---|---|
| A data source never invents a point: a window with nothing recorded is a gap, not a zero | Automated | `cd jarvis-core && python3 -m pytest tests/test_metrics.py -q` · the console's half: `cd jarvis-web && npx vitest run src/lib/dashboards` |
| A query is downsampled to the step it was asked for, and says which aggregate it used | Automated | the same |
| The internal source graphs entity history, this host, and Jarvis's own counters | Automated | `tests/test_metrics.py` |
| A dashboard belongs to the token that saved it; one token can neither read nor overwrite another's | Automated | `cd jarvis-core && python3 -m pytest tests/test_dashboards.py -q` |
| A layout survives a restart | Automated | the same (`test_a_layout_survives_a_restart`) |
| The layout the console writes is the layout the server accepts | Automated | one contract, both suites: `tests/contracts/dashboard_layout.json` |
| Widgets can be added, resized, moved, swapped and removed — and it sticks | Automated | `cd jarvis-web && E2E_PORT=8299 npx playwright test e2e/dashboards.spec.ts` |
| Six chart types draw, and a gap breaks the line | Automated | the same spec |
| The InfluxDB adapter speaks 1.x InfluxQL and 2.x Flux, and works out which it is talking to | Automated *against a fake of each generation* | `cd jarvis-core && python3 -m pytest tests/test_metrics_influx.py -q` |
| The token never appears in a URL | Automated | the same (`test_the_token_never_appears_in_a_url`) |
| An InfluxDB the operator actually runs is reachable and queryable | **Scripted** | `python3 scripts/check-influx.py` — needs their database; nothing on a build machine can prove it |
| The whole milestone | Automated | `bash scripts/verify/m05-dashboards.sh` · `bash scripts/verify/m06-influx.sh` |

### Background work: one endpoint, a queue, and a plan

| Claim | Level | Proof |
|---|---|---|
| Jarvis talks to exactly one model endpoint, OpenAI-compatible (`/v1/chat/completions`), and nothing bolts a provider prefix onto a model name | Automated | `cd jarvis-core && python3 -m pytest tests/test_openai_compat.py -q` · `python3 -m pytest jarvis-orchestrator/tests -q` · `bash scripts/verify/m09-llm.sh` |
| "100 % local" is enforced, not promised: a public model-server URL is refused at startup | Automated | `cd jarvis-core && python3 -m pytest tests/test_llm_local_only.py -q` |
| No more than `llm.max_concurrent` jobs run at once, however many are submitted | Automated | `cd jarvis-core && python3 -m pytest tests/test_taskengine.py -q` (`test_no_more_than_the_cap_run_at_once`) |
| A failure is retried with jittered backoff and then reported; a cancellation is not a failure | Automated | the same |
| Work that was waiting when the process died is still waiting after a restart | Automated | the same (`test_queued_work_is_still_queued_after_a_restart`) |
| A background task the assistant accepts actually runs — against a real server, not a mock | Automated | `python3 -m pytest testing/e2e/test_agent_loop.py -q` |
| A multi-step request becomes a plan whose steps are on the task *before* any of them is attempted | Automated | the same (`test_a_multi_step_request_is_planned_acted_on_and_verified`) |
| Each step's outcome is judged by a call that can see the outcome but not the argument for it | Automated | `cd jarvis-core && python3 -m pytest tests/test_agent_loop.py -q` (`test_the_verifier_is_given_the_step_and_the_outcome_and_nothing_else`) |
| A "not done" verdict re-plans what is left, and re-planning is bounded | Automated | the same (`test_replanning_is_bounded`) + the e2e replan test |
| Tier meanings (1 direct · 2 background + notify · 3 approval) are one table, read by core, the console and the Android mirror | Automated | one contract, three suites: `tests/contracts/tool_tiers.json` |
| A **real** model planning a **real** request | **Scripted** | `./scripts/e2e-smoke.sh` with a model server up; the offline suites script the model, on purpose — what they pin is which prompt gets which answer |
| The whole milestones | Automated | `bash scripts/verify/m09-llm.sh` · `m10-task-engine.sh` · `m11-agent-loop.sh` |

### Talking to it: the live interaction rig

| Claim | Level | Proof |
|---|---|---|
| A spoken sentence reaches Jarvis, changes the house, and comes back as speech | Automated | `bash scripts/verify/live_interaction.sh --implemented-only` — the user is synthesised with Piper (`en_US-amy-low`, deliberately not Jarvis's `en_GB-alan-medium`), Jarvis's reply is transcribed back with the real Whisper on `:10300` |
| The same scenarios work through a real browser microphone, not only the API | Automated | headless Chromium with `--use-file-for-fake-audio-capture`, driving the real HUD (its own VAD, its own websocket); `testing/live/browser_turn.cjs` |
| A question does not become an action, and an unknown thing is refused rather than invented | Automated | `house-state-question`, `house-unknown-thing` |
| A Tier-3 request spoken out loud does not unlock the door | Automated | `lock-needs-a-human` — the lock is still locked at the end of the scenario |
| The wake word fires on the phrase and NOT on silence or on an empty room | Automated | `voice-wake-word`, `voice-silence`, `voice-room-tone` — against the real openWakeWord |
| Recognition survives a room with a fan in it | Automated *at a measured SNR* | `house-light-off-noisy` (10 dB, measured, deterministic) |
| A transcript is not doubled on the wake path | Automated | `voice-wake-word` at the default WER ceiling. It was relaxed to 1.0 for two milestones; M35 found the doubling was 3 runs of 3, not "occasional", and `--vad-filter` on the recogniser took WER from 1.00 to 0.00 |
| Silence and an empty room produce NO text, not a hallucinated word | Automated | `voice-silence`, `voice-room-tone` — both now assert the coded `stt-no-text-recognized`, which is a stronger promise than "whatever it heard moved nothing" |
| Which speech engine sounds better | **Judgement, with the numbers laid out** | `python3 scripts/verify/tts_ab.py` measures both on five real replies; `docs/tts-review/` holds the audio. Piper 0.40–0.57x real time, Kokoro 0.39–0.47x, WER 0.000–0.040 for both. The numbers tie, so the operator's ear decides and Piper stays the default |
| Word error rate, routing accuracy and per-stage latency | Automated *and reported* | `.verify/live/results.json`, `docs/LIVE_TEST_REPORT.md` |
| Every capability, including the ones not built yet | Automated | `bash scripts/verify/live_interaction.sh --full` — scenarios for unfinished capabilities carry `gated-on:` and fail until their milestone lands |
| A real SearXNG | **Deliberately not, and it is not blocked any more** | Docker works for `jarvisdev` now (M28). The research scenarios still run against `testing/live/fixture_search.py` on purpose: "did it cite three independent sources" is a question about a web this repository owns, and today's internet is not a fixture. They are the seven scenarios that carry `ground: fixture`; everything else talks to the running containers |
| A 2-second median round trip | **Missed, and reported** | measured 15–20 s per spoken turn on this host (27 B model, no GPU, four shared vCPUs). `ISSUES.md` and `BLOCKERS.md` say what it would take |
| A real microphone in a real room | **Unproven** | the rig synthesises speech; acoustics are not simulated |

### How good it actually is: the intelligence scorecard (M26)

Six things a person notices in the first week, measured through the full voice
pipeline rather than against the API: `python3 evals/intelligence/run.py`. The
prompt set is fixed (`evals/intelligence/prompts.yaml`), the harness is this
repository's own with the fixture web behind it, and nothing it proposes is
ever approved — every held action is denied, so a scorecard cannot become a
run of real jobs.

| Claim | Level | Proof |
|---|---|---|
| A later turn knows what an earlier one said | Automated | the `context_retention` section — a fact carried across an intervening turn, "turn it on" resolved against the previous question (asserted on the entity's state, not the sentence), a correction that must stick, and a "one sentence only" that must still hold two turns later |
| Which capability a request went to | Automated *from what ran* | the `routing` section, scored with `testing/live/capability.py` — the same table the scenario suite uses. A held Tier-3 tool counts as routed: `jarvis_tool_started` fires before the gate blocks, so the coding prompt is scored without a coding job being run |
| More than one step of reasoning, and a hypothetical that must not move the house | Automated | the `reasoning` section — three cases with a checkable answer, one judged, and `consequence`, whose real assertion is that "if I turned them off" called no service |
| Format, length and constraint following | Automated *and counted* | the `instructions` section — word counts, sentence counts and regexes, no judge |
| An impossible, unknowable or garbled request fails visibly | Automated | the `graceful_failure` section — nothing in the house moves, and the judge (with its reason logged) says whether it admitted the failure. The garbled case is spoken over a fan at 5 dB SNR, so what the model sees is a real mis-hearing |
| Per-stage latency, idle and under load | Automated *twice* | the `latency` section: four probes with the box quiesced, then the same four with a research job running on the same model server. A leftover task counts as neither — the idle pass cancels everything first and the load pass requires a NEW task |
| A reply nobody can make out | Automated *and separate* | round-trip WER (Piper → Whisper against the words Jarvis wrote) is reported beside the scores and has its own ceiling. Text assertions read what Jarvis WROTE: a regex over a transcription measures the recogniser |
| The floors and ceilings themselves | Automated | `python3 -m pytest evals/intelligence -q` — twelve tests over the scoring, including "a section that never ran cannot pass" and "an idle pass that was not idle is reported as such" |
| That the scorecard is any good | **Judgement** | it is 27 prompts. It is a smoke test for intelligence, not a benchmark, and `docs/AUDIT.md` says so |

Measured on this host, 2026-08-25 (harness ground, 12 GB model, no GPU): idle
median first word 6.2–8.1 s and whole turn 8.0–9.4 s; under load 6.4 s and
9.3 s. Those are the numbers the ceilings in `run.py` were set from — lower
than the 15–20 s the scenario suite sees against the stack, because the demo
house's summary is a fraction of a real one's.

### The exploratory pass and the live report (M27)

`bash scripts/verify/m27-live-report.sh`. What the suite cannot script, a
person-shaped probe asks; what the suite found, written up.

| Claim | Level | Proof |
|---|---|---|
| The report was generated by the runner, not typed | Automated | the gate greps `docs/LIVE_TEST_REPORT.md` for the runner's own stamp |
| It carries the headline numbers: word error rate, routing accuracy, median round trip, per capability, latency | Automated | the gate asserts each heading is present |
| At least ten exploratory conversations were run against the real stack and recorded, with a judge's verdict each | Automated | `.verify/live/exploratory.json`, written by `python3 -m testing.live.exploratory` — twelve probes, house facts in the judge's brief |
| Every issue in `ISSUES.md` names a regression that exists — a scenario, a probe, an eval, a verify script, a lint gate or a pytest node id — or says why nothing can | Automated | the gate resolves every backticked name in each entry's `Regression:` paragraph |
| No critical issue is open | Automated | the gate reads `severity:` and `status:` per entry |
| The rig cannot be passed by history: a task older than the turn does not satisfy the turn | Automated | `testing/live/tests/test_rig.py::test_a_task_older_than_the_turn_does_not_satisfy_the_turn` |
| The console is driven for real: `ui:` probes run in a headless browser against the console on :8199, and every probed testid exists | Automated | `task-live-ui (text-ui)`; `test_every_ui_probe_names_a_testid_the_console_renders` |
| The smoke subset still passes | Live | `house-light-on`, `chat-context-retention`, `lock-needs-a-human` |
| Whether the answers are *good* | **Judged** | the exploratory verdicts are a local model's; the transcripts are in the report for a person to disagree with |

### The toolbelt, before anything is added to it (M30)

| Claim | Level | Proof |
|---|---|---|
| Every service M31–M37 proposes has a decision written before it is built | Automated | `bash scripts/verify/m30-toolbelt.sh` — the check fails if a slot named in the milestones has no section in `docs/TOOLING_DECISIONS.md` |
| The decisions were checked against current sources, not recalled | Automated *that they exist and are dated*; **Judgement** that they are right | the Sources section carries a date and the project's own documentation for each candidate; the check requires both |
| Nothing takes GPU residency without saying what it evicts | **Convention, written down** | the VRAM justification rule; the check requires it to name the KV cache and the embedding path |
| Adding a service can be shown to have helped, or not | Automated | `python3 scripts/verify/toolbelt_baseline.py --out before.json`, then `--compare before.json after.json` — non-zero when a metric got worse |
| A comparison cannot flatter a change by not running an eval | Automated | `test_a_metric_that_stopped_being_measured_is_a_regression`, and the snapshot refuses to write at all when an eval is missing |
| Latency noise does not fire the check, and a real slowdown does | Automated | `test_latency_noise_does_not_fire_and_a_real_slowdown_does` — +17 % passes, ×2 fails |
| That these are the right components | **Judgement, and revisable** | the doc says what would overturn each decision, and `--compare` is how |

### Retrieval: embeddings and reranking (M33)

| Claim | Level | Proof |
|---|---|---|
| A question that shares no word with the note that answers it finds it anyway | Automated *and measured* | `python3 evals/memory_eval.py` reports recall@1 and recall@3 over six paraphrase queries. Keyword only: **0% and 0%**. With `jarvis-embeddings`: **100% and 100%**. `scripts/verify/m33-embeddings.sh` runs both and fails if the second is not higher |
| Embeddings never touch the GPU or the chat model server | Automated | the same script compares `memory: embedding_url` against `llm: url` and fails if they share a host — an embedding through llama-swap evicts the KV cache the voice path is using |
| The reranker is used where it helps and not where it hurts | Automated *from a measurement* | `research:` reranks (3/5 → 4/5 on choosing the page that answers the question); `memory:` does not (6/6 → 5/6 on notes). The check asserts both settings AND that the numbers are written beside them |
| A rerank service being down cannot make a search worse | Automated | `tests/test_rerank.py` — an unreachable, slow, or nonsense-answering reranker all return "no opinion" and the caller keeps its order; it stops asking after the first failure |
| The similarity floor is a property of the model, not a constant | Automated | `test_the_similarity_floor_belongs_to_the_model` — 0.62 was tuned for nomic and discarded five of six bge paraphrases that had ranked correctly |
| Both services are up and can do the job, not merely running | Automated | the script asks the embedder for a paraphrase pair and the reranker for an ordering; a container that answers `/health` and nothing else fails |
| The note store's search is fast enough to keep in a file | Automated *and measured* | `python3 scripts/verify/vector_store_bench.py` — 6.3 ms per search at the configured 500-entry cap, 127 ms at 10 000, against a spoken turn of 7–10 s. `scripts/verify/m34-vector-store.sh` re-runs it and fails over 50 ms |
| No vector database is in the stack, and none is imported | Automated | the same script. `docs/TOOLING_DECISIONS.md` §4 names the three conditions that would reverse that — 25 000 entries, a second writer, or filtered search becoming common |
| That these are the best models available | **Judgement** | four were measured (bge-small, ms-marco-MiniLM, mxbai-rerank-xsmall, bge-reranker-base) and the numbers are in `docs/TOOLING_DECISIONS.md` §3. Nothing here says a fifth would not be better |

### Observability: what the agent did, and what it cost (M36)

| Claim | Level | Proof |
|---|---|---|
| Every tool call, model call, approval and subagent in a turn is recorded | Automated | `jarvis-core/tests/test_observability.py` — 17 tests over grouping, nesting, bounds and failure. The correlation needed no new plumbing: every bus event already carried a `Context` with an id and a parent |
| What a turn cost in tokens and time | Automated | `jarvis_model_call` is fired after each exchange; `Trace.totals()` sums prompt and completion tokens, model time and tool time. Token counts live in the raw payload and were discarded when the stream closed |
| A trace cannot eat the heap or slow a turn | Automated | `test_spans_are_bounded_and_the_truncation_is_counted`, `test_traces_are_bounded_and_the_oldest_goes_first`, `test_a_broken_event_cannot_break_a_turn` — and the truncation is *counted*, so a trace never lies about what it dropped |
| "Why did it do that" survives a restart | Automated | finished traces append to `<config>/traces/<date>.jsonl`; `test_a_finished_task_closes_its_trace_and_writes_it_down` reads the line back |
| A person can see it from the task that ran | Automated *in a real browser* | `jarvis-web/e2e/task-live.spec.ts` — the Trace panel shows what it cost, where the time went, and each span with its duration; a failed span reads as failed without opening anything |
| Traces from more than one Jarvis, or analytical queries over them | **Not built** | that is what a Langfuse would be for; `docs/TOOLING_DECISIONS.md` §6 records the measurement and says the JSONL is deliberately the shape you can ship elsewhere |

### Calendar, mail, and the plugin interface (M39)

| Claim | Level | Proof |
|---|---|---|
| An event created by Jarvis appears on a real calendar | Automated *against Radicale* | `testing/fixtures/integrations_probe.py` — created over CalDAV, then read back out of a `REPORT`, then deleted |
| A sent message lands in a real inbox | Automated *against smtp4dev* | the same probe, reading the sink's API. smtp4dev rather than MailDev because it also serves IMAP, so the READ path is tested against a real server too |
| An address nobody allow-listed is refused | Automated | the same probe, and `test_an_address_nobody_allow_listed_is_refused` — refused rather than asked about, because "send this to attacker@example?" is a prompt somebody clicks yes on |
| Reading is free; changing the outside world is not | Automated | the probe asserts the split by tier, and `PluginTool` defaults an unclassified tool to Tier 3 |
| An email body cannot instruct the model | Automated | bodies are quarantined and reading mail taints the turn, so M43's escalation applies — asserted in the gate |
| Credentials are read when a tool runs | Automated | `ToolPlugin.secret()`; nothing holds a credential in an attribute for the process's life |
| Every external call is in the trace with its duration | Automated | `EVENT_PLUGIN_CALL`, fired around every plugin tool call |
| No new dependency for either protocol | Automated | CalDAV is `httpx` + `xml.etree`; mail is `imaplib` + `smtplib`. The gate greps for `caldav`, `lxml`, `icalendar` |
| Recurrence, timezones beyond UTC, attachments | **Not built** | the iCal reader handles five fields and says so; an event it cannot fully parse still appears, without its recurrence |

### Channels: reachable, and only by you (M38)

| Claim | Level | Proof |
|---|---|---|
| Nothing is exposed to the internet | Automated | both shipped adapters POLL — `test_neither_shipped_adapter_opens_a_port`, and the gate greps for a webhook registration. No inbound port, no URL carrying a token |
| An unknown sender is ignored, not refused | Automated | `redteam-unknown-sender` (live) — no reply at all, and the reason names the allow-list. `test_an_unknown_sender_is_ignored_and_never_answered` asserts their words never reached the model |
| An empty allow-list means nobody, even switched on | Automated | `test_an_empty_allow_list_means_nobody` |
| A message cannot silently act | Automated | `redteam-injection-via-message` (live) — the message is quarantined and taints the turn, so M43's gate applies; the door stayed locked |
| Rate limits, per sender and overall | Automated | `test_the_per_sender_rate_limit_bites_before_the_model_does`, `test_the_global_limit_holds_across_senders`, and a sliding window test |
| Proactive moments go out on channels | Automated *as wiring* | the hub subscribes to `jarvis_notification`; `notifications` stays the one notion of "tell them" |
| Telegram and Signal against real accounts | **Unproven, by design** | no test touches an account. `MemoryChannel` ships in the product and the live probes drive the real hub through it — only the wire is a fake. `BLOCKERS.md` §4 has the accounts |

### Delegation across backends (M42)

| Claim | Level | Proof |
|---|---|---|
| One request can reach more than one kind of worker | Automated *live* | `delegation-across-backends` — and the gate asserts the run actually used two of `deep_research` / `delegate_to_agents` / `start_coding_job`, not that it could have |
| A plan entry may name a subsystem or a specialist | Automated | `split()` — `research`, `code`, `code:claude-code`, and everything else is a specialist's name |
| Delegated work reuses the subsystems' own tasks | Automated | `wait_for_task` waits on the registry; the research run and the coding job keep their own progress, steps and approval gates. The console draws the tree it already draws |
| An approval nobody answered ends the wait | Automated | `test_a_cancelled_task_ends_the_wait_rather_than_timing_out` — a stopped child is reported as stopped rather than waited out |
| A coding job will not guess between repositories | Automated | `test_a_coding_job_will_not_guess_between_repositories` |
| Concurrency stays bounded | **By construction** | the same `ModelPool` M20 built; nothing here spawns model calls outside it |
| A fan-out is reported as a fan-out | Automated | `capability.py` puts delegation ahead of the child kinds — reading the children first labels a fan-out as whatever it delegated, which hides what happened |

### The delegated coding backend (M41)

| Claim | Level | Proof |
|---|---|---|
| It is off, and needs a key supplied deliberately | Automated | `test_the_default_is_off`, `test_it_will_not_run_without_a_key`; the shipped config is asserted to say `backend: local` |
| A typo cannot select the cloud backend | Automated | `test_the_backend_choice_falls_back_to_local_on_a_typo` — the setting that decides whether code leaves the network fails safe |
| A repository can refuse to be delegated | Automated | `Repo.backend` is a pin and beats a task's request; asking for the *safer* backend is always honoured |
| A delegated run happens inside the sandbox | Automated *against a real container* | `testing/fixtures/claude_backend_probe.py` — the run's edits appear in the repository inside the container, and a repository with no sandbox is refused before anything starts |
| Failure and unreadable output are reported, not believed | Automated | the same probe: an agent that fails is a failed job; output that is not a result is named as such; a non-zero exit beats a cheerful payload |
| The same approval gate applies | **By construction** | the gate is in front of the `Workspace`, and the delegated driver uses the same `Workspace`. There is no second path to the files |
| What a real Claude Code produces | **Unproven, and no key exists** | CI runs `fake_claude_code.py`, which speaks the same `--print --output-format json` protocol. `BLOCKERS.md` §4 has the row |

### The model gateway, and what may leave the network (M40)

| Claim | Level | Proof |
|---|---|---|
| One endpoint, and the house still answers through it | Automated | `scripts/verify/m40-model-gateway.sh` runs live scenarios with `jarvis-core` dialling `jarvis-gateway`, which dials llama-swap |
| A request with no override goes local | Automated | `testing/fixtures/gateway_probe.py` — and the mock cloud provider recorded nothing |
| An override reaches a cloud provider | Automated | the same probe, against a mock that records what it was asked |
| A failing provider falls back instead of failing the turn | Automated | the same probe, with the mock told to return 500 |
| **A request carrying private content cannot leave** | Automated | the same probe: tagged `local-only`, aimed at the cloud mock, refused with 403 — **and the mock heard nothing**. That is the assertion; a log line saying "refused" would be the guard grading its own homework |
| …and an untagged request still reaches it | Automated | the control, in the same probe: the guard refuses a tag, not everything |
| Jarvis and the proxy agree about what "cloud" means | Automated | `test_the_two_halves_of_the_guard_agree` reads both files — they cannot import each other, one runs inside the LiteLLM container |
| Prompts are not logged by the proxy | Automated | `turn_off_message_logging: true`, asserted |
| Per-provider cost caps | **Not built** | LiteLLM's spend tracking needs its database. Rate limits (rpm) are config and are set; a budget is not, and `docs/TOOLING_DECISIONS.md` §8 says so |

### Hardening: what holds when the content is hostile (M43)

`docs/THREAT_MODEL.md` is the argument; this is what is asserted.

| Claim | Level | Proof |
|---|---|---|
| A page cannot forge a role boundary against a local model | Automated | `test_every_template_family_loses_its_role_markers` — ChatML, Llama 2, Llama 3, Gemma and Mistral literals are replaced with a visible scar, on the way IN (`mark_untrusted_result`), so a new inbound path cannot forget to do it |
| Content cannot escape or forge its own wrapper | Automated | `test_content_cannot_close_the_fence_around_it`, `..._forge_the_notice_either` |
| Nothing is filtered by keyword | Automated *as behaviour* | `test_nothing_pretends_to_detect_an_attack` — "ignore previous instructions" comes back word for word, wrapped. A filter with a bypass is a system exactly as vulnerable and now believed safe |
| A turn that has read external content cannot silently act | Automated | `test_a_tainted_turn_cannot_silently_change_state`; an unclassified tool escalates (`test_a_tool_nobody_classified_escalates`), which is the safe direction to be wrong in |
| …and `remember`/`forget`/`undo` refuse outright rather than asking | Automated | `REFUSE_WHEN_TAINTED`, held in step with its refusal tests by `test_the_refusers_really_do_refuse` — a human cannot audit "remember: the spare key is under the mat" in the two seconds an approval gets |
| A memory write nobody asked for does not happen | Automated *and found by a probe* | `redteam-cross-conversation-leak` caught it: a remark said in passing became a permanent fact a later conversation read back. `MEMORY_REQUESTS` now requires the USER's own words to ask |
| Secrets never reach a log, a trace or a note | Automated | `security/secrets.py` redacts **by value** (a model interpolates a key into a sentence, so key-name matching fails) plus a structural pass on known key names; the filter is installed at boot, before anything can log a config dict |
| The red-team probes | Automated *and the acceptance criteria* | `scripts/verify/m43-hardening.sh` runs them; the suite fails if any succeeds. Three run now; two are `gated-on: M38` because channels do not exist yet, and full mode runs them and fails |
| Prompt injection, as a class | **Not defended, and said so** | `docs/THREAT_MODEL.md` — the defence is structural (quarantine, then require a human to act), not detection. A model can still be talked into saying something foolish |

### The stack, as the thing under test (M28, M29)

| Claim | Level | Proof |
|---|---|---|
| The suite talks to the containers the operator actually runs | Automated | `bash scripts/verify/live_interaction.sh` starts with `docker compose up -d --wait` and the scenarios address the running jarvis-core and the console on `:8199`; 22 of 29 scenarios carry the default `ground: stack` |
| A container that is unhealthy at the start fails the run before a word is spoken | Automated | `up -d --wait` exits non-zero, and `Stack.up()` re-checks; an init container that ran and exited 0 is not counted as sick |
| A container that logged an ERROR-level record during the run fails the run | Automated | `stack-logs-clean` in `.verify/live/results.json` — `docker compose logs --since`, grouped into records so the allowlist can name an exception (`ConnectionResetError` from a probe that hung up) rather than the useless line that introduces it |
| A restart underneath a live conversation does not lose the thread | Automated | `resilience-core-restart` — `docker restart jarvis-core` between two turns, and "now do the same in the bedroom" still resolves |
| Speech recognition disappearing mid-utterance surfaces as a visible failure | Automated | `resilience-stt-down` — the container is stopped, the turn ends with `stt-stream-failed` rather than hanging, no service call happens, and the next turn works once it is back |
| The suite is safe to point at a house somebody lives in | Automated | `jarvis-core/config`, `.storage` and `mosquitto-data` are tarred before the run and restored after (`StateGuard`, through a container because the services own their own files); every thread it opens is `test:<scenario>:<variant>`; anything a scenario creates is deleted and its absence asserted before the next one |
| A code change reaches a running container | Automated | `develop: watch:` on the four services built here, with `test_every_watch_rule_syncs_into_that_image_workdir` pinning the target against each image's `WORKDIR` |
| Bring-up, teardown and per-volume backup/restore are written down | Scripted | `docs/RUNBOOK.md` — and the restore path is exercised, not just documented (`scripts/verify/m29-compose-testing.sh`) |
| The stack survives a host reboot | **Unproven** | `restart: unless-stopped` is set on every long-running service; nothing here reboots the host to find out |

### The research engine

| Claim | Level | Proof |
|---|---|---|
| A question becomes several searches, the best pages are read, and the report cites what was read | Automated | `python3 evals/research_eval.py --backend fixture` — four questions against a fixture web this repository owns, through the real search client, ranker, reader and writer |
| Every fact in a report is in a page that was read | Automated *against the fixture web* | the same command: `must_contain` and `expect_source` per question. The open web cannot be pinned this way, which is why the facts are only checked here |
| Every citation resolves | Automated | the same command — each link is fetched (HEAD, then GET for a server that refuses HEAD) |
| A page's leads are followed, once | Automated | `tests/test_research.py::test_a_lead_from_the_pages_is_followed_once` |
| Quick and deep are one engine with two budgets, and a mode cannot raise a configured limit | Automated | `test_quick_mode_does_not_follow_leads_and_stays_small`, `test_a_mode_cannot_raise_a_configured_limit` |
| Each key claim says how many of the sources read support it | Automated | `tests/test_research_plan.py` (cross-check), and the `## Confidence` section of every report |
| No cloud search fallback exists anywhere | Automated | `scripts/verify/m18-research.sh` greps for one; `web.search` fails saying SearXNG is not configured. The one fallback there is (M68) is a second SearXNG — the stack's own — and the grep covers it |
| A `deep_research` call that names no question researches what the user said this turn, and says so; with nothing said either it refuses and tells the model not to call it "queued" | Automated | `test_a_call_with_no_question_researches_what_the_user_said` — the 26 Aug deep-report miss on the ninth rebuild |
| "…and save it as a note" in the user's own words keeps the report as a note even when the model drops the `remember` flag; a sentence that asks for none keeps the default (nothing stored) | Automated | `test_a_note_asked_for_in_the_users_words_is_kept_even_when_the_flag_is_dropped`, `test_the_users_words_decide_whether_a_note_is_asked_for` — the re-run of the same scenario at 21:19 |
| The report is a markdown file a person can open | Automated | `test_the_report_is_written_to_a_file_somebody_can_open` — `<config>/research/<date>-<slug>.md` |
| Research against the **real** SearXNG and the open web | **Scripted** | `SEARXNG_URL=… python3 evals/research_eval.py --backend live` — needs the operator's SearXNG, which `jarvisdev` cannot start here (`BLOCKERS.md`). The command refuses clearly rather than pretending when the URL is unset |
| jarvis-browser's own guards (SSRF, robots, JavaScript rendering) | Automated *in its own suite* | `python3 -m pytest jarvis-browser/tests -q` — 337 tests |
| A PDF or a Word file is read as text | Automated | `research-reads-a-document` (M32) — the warranty is in the PDF and nowhere else on the fixture web; `jarvis-browser/tests/test_documents.py` covers the shapes, including a scanned PDF being NAMED rather than returned empty |
| A table survives extraction with its rows intact | Automated | `scripts/verify/m32-extraction.sh` asserts the night rate is still in the same row as its hours and its price; the research eval asks for exactly that |
| Scanned documents (OCR) | **Deliberately not** | `pypdf` reads a text layer. OCR means a model and gigabytes of it; `docs/TOOLING_DECISIONS.md` §2 records the measurement that decided it |
| Following a link from a page to another page | **Not built** | `lead_depth` follows new SEARCH QUERIES, not URLs. Jarvis reaches a document when a search surfaces one, which is why the fixture search indexes PDFs |
| A page whose content is written by JavaScript is read correctly | Automated *through the real browser* | `research-javascript-page` (M31) — the rig borrows the running `jarvis-browser`, and the same scenario FAILS under `LIVE_SHARED_BROWSER=0`, which is what makes it a test of the browser rather than of the fixture |
| The deployed browser can actually open a page | Automated | `scripts/verify/m31-browser-service.sh` asserts `/healthz` reports `browser: ok`, and the image's build launches chromium. Both exist because the container answered 200 for weeks with a chromium that could not load `libglib` |
| One Chromium, not several | Automated | the same script greps for a per-task browser install. The Playwright in `jarvis-web` and `jarvis-desktop-app` drives the CONSOLE and is not a page fetcher |

### The design system

| Claim | Level | Proof |
|---|---|---|
| One token source: `design/tokens.json` is the only file where a colour, size, font, radius, shadow or duration is typed; every surface's token file is generated from it | Automated | `python3 design/build.py --check` — seven generated files current (web CSS + TS, desktop `tokens.py`, Android `JarvisTokens.kt`, `JarvisTheme.kt`, `tokens.xml`, `colors.xml`) |
| The reactor's palette on every surface is `color.orb.*`, and its geometry is one contract | Automated | the same command (drift check over `SiriPalette.kt`; `Reactor.svelte`'s constants against `tests/contracts/reactor_geometry.json`) + `python3 android-app/tools/reactor_orb_test.py` (the web against the contract; the phone's two views draw one renderer; the phone's own reading of the contract lands with M51) |
| **No** hard-coded colour/spacing/type/motion value in web, Android or desktop app code | Automated | `python3 scripts/verify/token_lint.py` — the ratchet (`design/token-lint.baseline.json`) started at 340 hits across 38 files (2026-08-24) and is now **empty**: every file it walks is clean, so any raw value fails rather than fitting under an allowance. 4 documented exceptions. Re-measured under M44, where a planted `transition: all 240ms ease-in-out` had slipped through on `base.css`'s stale allowance |
| Phone, desktop and console draw one palette, every text colour AA on its ground | Automated | `python3 android-app/tools/design_token_test.py` · `cd jarvis-desktop && python3 -m pytest tests/test_theme.py -q` · `cd jarvis-web && npx vitest run src/lib/tokens.test.ts` — all three read `design/tokens.json` |
| `/styleguide` renders every token group and the four screen states, headless | Automated | `cd jarvis-web && E2E_PORT=8299 npx playwright test e2e/styleguide.spec.ts` (screenshot under `.verify/styleguide.png`) |
| **The Kotlin builds** | Automated | `./gradlew assembleDebug` — a JDK 17 and the SDK under `$HOME` (`android-app/tools/bootstrap-toolchain.sh`), the wrapper committed, `app-debug.apk` produced. The first time this repository has built its own Android app |
| The Compose theme (`JarvisTheme.kt`) compiles | Automated | it is compiled by the build above, and `the generated theme` screenshot renders it |
| **The JVM unit tests** (224 as of M61's last six rows) | Automated | `./gradlew testDebugUnitTest` — run here with M08's toolchain and in CI (`android-apk.yml`) |
| **Lint is blocking, and clean** | Automated | `./gradlew lintDebug` with `abortOnError = true`. It found three real crashes-on-Android-10 while it was "reported, not enforced": two `AudioManager.OnModeChangedListener` calls and a `createOnDeviceSpeechRecognizer`, each requiring API 31 with `minSdk = 29` |
| **Six screens, rendered and compared** | Automated | Robolectric + Roborazzi on the JVM: the orb listening and thinking, the component sheet, the approval banner, the task overlay, the generated theme. `./gradlew verifyRoborazziDebug` fails on a difference; the goldens are PNGs in the repository |
| No hard-coded colour, size or type value left in the app's Kotlin | Automated | `python3 scripts/verify/token_lint.py --require-clean android-app/app/src/main/kotlin` — 132 hits to zero, which needed two new spacing steps, a `Size` scale and thirteen derived alpha constants in `design/tokens.json` |
| The whole design-system gate | Automated | `bash scripts/verify/m01-design-tokens.sh` — 46 checks, measured 2026-08-24 |

### Skills, MCP servers and plugins, as one thing (M45)

`bash scripts/verify/m45-registry.sh` — 15 checks.

| Claim | Level | Proof |
|---|---|---|
| Every extensible thing carries a manifest: id, version, description, author, declared permissions, tool allowlist, network and filesystem needs | Automated | `jarvis/integrations/extensions/manifest.py`; derived from each subsystem rather than stored beside it, so a skill stays a portable Agent Skill |
| The manifest is validated against a JSON Schema an author can read | Automated | `manifest.schema.json` — a real draft 2020-12 document. Enforced by a hand-written validator rather than a dependency (this package installs from wheels with no compiler) |
| …and the validator implements every keyword the schema uses | Automated | `test_the_validator_knows_every_keyword_the_schema_uses` — the real failure mode of a hand-rolled validator is a schema that grows a keyword nothing enforces, so the document describes a stricter manifest than the one being accepted |
| A malformed manifest is rejected rather than half-loaded | Automated | `test_an_invalid_skill_manifest_leaves_the_system_prompt_too` — out of the index, out of the store, and out of `index_block()`. Dropping only the bad keys is how a `tools` list with one unparseable entry becomes a shorter allowlist than the author wrote and a wider one than they meant |
| A manifest cannot invent a permission | Automated | the vocabulary is a closed enum of 8. An accepted-but-unenforced permission is worse than a rejected one: it reads as a declaration in a management surface and constrains nothing |
| A manifest cannot list a tool it has not asked permission for | Automated | `TOOL_PERMISSIONS` + `under_declared()` — `write_file` without `filesystem_write`, `web_search` without `network`, `remember` without `memory_write` |
| A plugin's `act` permission cannot disagree with the taint gate | Automated | derived from `PluginTool.read_only`, which is the same field M43's escalation reads — not declared separately |
| The registry can say who holds each permission, not just what each item may do | Automated | `permission_scope()` — everything in the house that can write to memory, or start a process, in one list |
| Health never raises when something is down | Automated | `test_health_never_raises_when_something_is_broken` — a registry that throws when one server is down shows nothing at the moment somebody opened it |
| Indexing does not force MCP to exist | Automated | the gate asserts an empty install grows nothing but `extensions`. `DEPENDENCIES` would have set up MCP for an install that never configured one |
| Four skills ship and all four validate | Automated | research-report, note-taking, homelab-status, diary — 1,405 words, four `SKILL.md` files and nothing executable beside them |
| A skill in the operator's directory replaces a shipped one, without reading as a collision | Automated | `test_a_users_skill_overrides_a_bundled_one_of_the_same_name`; two of the same name in ONE root is still an error |
| A recorded measurement can reach a sentence | Automated | `metrics_query` (read-only, in `READ_ONLY_TOOLS`) over the same sources the dashboards draw; the gate asserts the skill names it |
| A third party's manifest, from a catalog | **Not yet** | M47. The gate for it is the same `Manifest.from_raw` call; what does not exist yet is anything that installs code from off this machine |

### Installing from a catalog (M47)

`bash scripts/verify/m47-catalog.sh` — 19 checks. `docs/THREAT_MODEL.md` carries the argument.

| Claim | Level | Proof |
|---|---|---|
| Only a document and a URL can be installed | Automated | `INSTALLABLE_KINDS == {skill, mcp}`; a plugin source is refused with "an in-process import has the whole interpreter", a stdio MCP server with "those come from configuration.yaml, which a person edits" |
| Nothing installs from an origin nobody named | Automated | `DEFAULT_SOURCES == ()`, and an unconfigured source raises. Shipping a default list would hand the supply chain to whoever owns those URLs, for every install. Since M65 one source ships that is not an origin — `bundled`, the package's own folder on this machine (`DEVIATIONS.md` §21) — and this claim is unchanged for anything remote |
| A source is https or this machine | Automated | `http://`, `ftp://`, a bare path and `gopher://` are all refused |
| `latest` is not a version | Automated | `resolve_ref` refuses it without a concrete ref to pin to — a blind `latest` makes the approved thing and the landed thing two different objects |
| What was approved is what lands | Automated | sha256 recorded when the plan is built and re-checked immediately before writing; a swapped payload raises |
| Nothing in a payload runs | Automated *and the acceptance criterion* | the fixture's `friendly-helper` ships an `install.sh` that would write `/tmp/jarvis-catalog-probe-should-not-exist`. It lands on disk, it is named in the approval prompt as a program, and the marker never exists |
| Installing without an approved plan is refused | Automated | `apply` raises, and the service answers with the name of the call that is missing |
| A payload cannot write outside its folder | Automated | traversal, absolute paths, dotfiles, symlinks and over-deep nesting are refused — refused rather than corrected, which is how `/etc/SKILL.md` had been silently becoming `etc/SKILL.md` |
| A catalog description cannot smuggle an instruction | Automated | quarantined, not filtered: the fixture's hostile description keeps its words and loses its `<\|im_start\|>` marker, and its invented `become_root` permission is dropped rather than shown as real |
| The console shows what an entry asks for before anything is installed | Automated | `e2e/catalogue.spec.ts` (M65; it was `extensions.spec.ts` while the catalogue lived in that fold) — declared permissions on the row, then a second dialog with the ref, the hash, every file and every program |
| A malicious entry cannot talk the model into installing it | Automated *against the real containers* | `redteam-malicious-skill-install` (live, `gated-on: M47`) — the model has no tool that installs anything, the marker file does not appear, and the reply does not treat the entry's own description as permission |
| Fetching over **https** | **Not exercised** | the transport is written and the offline gate cannot reach the open internet, so every test runs against `file://`. The difference is one function; the parsing, hashing, hook-finding, approval and write path are the same code |
| A source that was honest and stops being | **Not defended** | the hash pins a payload to an approval, not a source to a reputation. A taken-over origin fails the check on the NEXT install and does nothing about what is already on disk |

### The management surface (M46)

`bash scripts/verify/m46-plugins-ui.sh` — 15 checks.

| Claim | Level | Proof |
|---|---|---|
| Turning a plugin off takes its tools off the MODEL, not off a list | Automated | `test_disabling_a_plugin_takes_its_tools_off_the_model` — the tool registry, not the console's listing. A plugin hidden from a page is one the model can still call |
| An edited permission scope is enforced on the very next call | Automated | revoking `act` withdraws the writers and keeps the readers; `permissions: null` restores exactly what was withdrawn |
| A permission the manifest never declared cannot be granted | Automated | narrowing only — the manifest is the statement people read, and an operator granting more does not make the thing need more |
| A decision outlives the process | Automated | `.storage/extensions.json`; the gate boots twice and checks the skill is still off and the others are still on |
| Turning something back on actually brings it back | Automated *and found by the live suite* | `test_turning_a_skill_back_on_actually_brings_it_back`. The switch worked exactly once: disabling popped the skill out of the store and nothing ever put one back |
| A skill can be written from the console, and it validates | Automated | `extensions/scaffold` — the permissions its chosen tools require are written in for it, so the file cannot fail its own validator a second later |
| A name that is nearly a path is refused, not corrected | Automated | `../escape`, `Bin Day`, `bin/day` and `bin_day` are all refused; the folder is named exactly what was asked for. A name quietly turned into something else is how somebody ends up with a skill they did not write in a folder they did not name |
| A switch flipped MID-CONVERSATION reaches the running Jarvis | Automated *against the real containers* | `extensions-toggle-enforced` (live, `gated-on: M46`) — three turns against the deployment, asserted through `jarvis/skills/list`, which is the store the prompt's skill index is built from |
| The section has real loading, empty, error and rejected states | Automated | `e2e/extensions.spec.ts`, 6 tests, plus a structural check that each state is rendered |
| It did not cost a tab | Automated | the gate asserts the top-level nav count did not grow and that no `/extensions` route exists — the section lives on `/tools` |
| The same enforcement for an MCP server's tools | **Not measured** | an MCP server's tools are withdrawn by disabling the server, which `mcp/*` already does; nothing here asserts the two agree |
| The live half for a PLUGIN | **Not measured live** | no `ToolPlugin` is configured in a default deployment — calendar and mail need the operator's own account, and the radicale fixture is behind `--profile fixtures` deliberately. The plugin case is the pytest above, against a real registry |

### The console's structure (M48)

`bash scripts/verify/m48-webui-c2.sh` — 15 checks. `docs/UI_MIGRATION.md` is the inventory.

| Claim | Level | Proof |
|---|---|---|
| No more than five top-level destinations | Automated | the gate counts `nav: true` in `screens.ts` and fails at six. Eleven became four |
| Every current page has a home in the navigation architecture | Automated | the gate walks `src/routes` and fails on a page the architecture does not place |
| Every moved path redirects rather than 404s | Automated | ten 308s, each asserted to point where `MOVED` says. `/tasks/[id]` carries the id through |
| Every keyboard chord anybody learnt still lands on its page | Automated | the gate holds the pre-consolidation chord table against the current one; `g d` must still reach devices. `g b` is asserted present because the nav's tooltip had been promising it |
| The nav, the chords, the palette and the phone read one list | Automated | four copies of the route list became one; the gate fails if any of the four stops reading `screens.ts` |
| The palette indexes sections, not only destinations | Automated | with four front doors instead of eleven this is the fast path, and a palette offering only the four would make the console slower to use |
| The phone offers the same front doors as the browser | Automated | `console_parity_test.py` — four, in the same order |
| No hard-coded style value anywhere in the console | Automated | `token_lint.py` at zero, with the ratchet empty |
| Every screen declares itself and uses `ScreenState` | Automated | `web_states_check.py`, which now reads the section a route mounts rather than the two-line route file |
| Nothing overflows OR is crushed at five widths | Automated | `responsive.spec.ts` — the second half is new: the Extensions panel was rendering a sentence one letter per line at 390px while scrolling nothing sideways |
| 84 controls use the shared component library | Automated *as a count* | and the library grew `pressed`, an `approve` variant and attribute forwarding rather than the pages working around it |
| What is still a raw `<button>` | **Deliberate, and listed** | a mic, a disclosure triangle, a clickable row and five other one-offs — `docs/UI_MIGRATION.md` names each with its reason. All on tokens; none is a button in this library's sense |
| Whether it is *good* | **Needs eyes** | 48 screenshots in `docs/ui-review/`, 16 screens at three widths. The harness can prove structure, states and spacing; "would a first-time user understand this" is not a test |

### Settings that make sense, and the real models (M54)

`bash scripts/verify/m54-settings-models.sh`. What the model servers actually serve, and SETTINGS cut to what a person changes.

| Claim | Level | Proof |
|---|---|---|
| The vision role says which case it is in — no `vision:` block, a model no server lists (naming the served vision models, or how to load one under the alias), served but no camera — and keeps the configured value visible when it is not served | Automated | `test_llm_catalogue.py::test_a_vision_model_no_server_lists_is_configured_and_not_served` (the deployed house's exact state), the models e2e; the operator's question of 26 Aug is the reason |
| `jarvis/llm/models` lists the SERVED ids, never a gateway alias; the alias is on the model it stands for and is what `llm.model` takes | Automated | `jarvis-core/tests/test_llm_catalogue.py` against a `MockTransport` LiteLLM → llama-swap → vLLM chain with the shapes the deployed stack answered: `house` is not a row, `qwen3.8-27b` has `aliases: ["house"]` and `choice: "house"` |
| It never loads a model to describe one | Automated | the same suite records every URL asked and asserts `/upstream/qwen3.6-35b/…` (the unloaded one) is never among them, and that the fake swap's loaded set did not grow |
| A size the server did not report is read off the id and marked so; a size the server reported wins | Automated | `test_what_the_server_says_beats_what_the_id_says` (vLLM `root`/`max_model_len`, `described_by: "id"`, note "as named by the server"), `test_a_plain_llama_cpp_server_reports_its_own_numbers` (`meta.n_params` → `27B`, `described_by: "server"`, no note), `test_the_ollama_wire_is_read_from_tags_and_ps`; twelve ids through `describe_model_id` |
| The fast slot is reported as configured and idle, and what each role is set to is resolved through the gateway | Automated | `test_the_fast_slot_is_configured_and_idle` (`in_use_for: []`, `note` says idle, `roles.fast.source: "gateway"`), `test_a_chosen_fast_model_is_resolved_through_the_gateway`, `test_a_named_research_model_takes_that_job_off_the_conversation_model` |
| A configured model no server lists, a server that is down, a wrong gateway key, a cloud alias — each is a row or a reason, never an exception | Automated | `test_a_configured_model_the_server_does_not_list_is_shown_as_missing`, `test_a_server_that_is_down_is_a_reason_not_an_exception`, `test_the_wrong_gateway_key_is_a_reason_on_the_server_row`, `test_a_cloud_alias_behind_the_gateway_is_listed_as_not_local` (and nothing but the four fake hosts was asked) |
| No network in the tests | Automated | the gate greps the suite for the stack's addresses; every server is a `httpx.MockTransport` keyed on host name |
| `llm.fast_model` and `vision.model` are editable, land live, and the packaging pins hold | Automated | `test_the_fast_model_setting_lands_on_the_running_agent` (and empty is legal), `test_the_vision_model_setting_reaches_the_analyser`, `test_every_role_setting_the_catalogue_names_is_editable`; `tests/test_packaging.py` (the websocket table in `docs/clients.md` gained the row) and `tests/test_settings*.py` in the gate |
| The MODELS panel draws the served ids with role, dot and "used for", and a role choice writes the setting the raw row would | Automated | `e2e/models.spec.ts` against the mock: five roles, no `model-house` row, meta in mono and the name not, `data-loaded` and the dot's colour, `config/settings/set {key: "llm.model", value: "house-fast"}` on the wire, the conversation moving to the 4-B on the next list, the raw row behind EVERYTHING agreeing, `llm.fast_model` written, vision offering vision models only |
| The panel has all four states | Automated | the same spec: loading (the models frame held, the section ready), empty (`jarvis/test/models_mode: empty` → "The model server lists nothing"), error (`error` → the reason, a Retry that works), offline (sockets closed → `models-offline` with the last rows kept) |
| SETTINGS is five sections in order, each a real page, each opening on plain rows with a why | Automated | `e2e/settings.spec.ts`: the strip reads ASSISTANT · VOICE · HOUSE · CONSOLE · TOOLS, every section's probe renders, every featured row has its label and its why and no key, SAVE disabled until dirty, a wake-word save on the wire and a RESET back; the gate reads `sections/settingsPlan.ts` and refuses a label that is a key or a why outside 12–140 characters |
| Nothing was lost: every setting the server sends is on one section, behind EVERYTHING, with key, source and SAVE — and every panel the one page carried is somewhere | Automated | the same spec reads `config/settings/list` from the mock at run time and walks every key to its section's fold (closed by default, opened by its head); pairing, tokens, voice identity, enrolment, text size, this console, this window, paired computers, the event stream and the areas link each found; `/settings/desktop` and `/desktop` land on Console |
| The rest of the suite still holds on the new addresses | Automated | `e2e.spec.ts` (settings edit/reset/restart, backend and event stream, pairing ×3, voice identity ×4, the 390 px overflow walk) and `console-repairs.spec.ts` (settings reconnect, text size, a number saved as a number) re-pointed at the sections; `states.spec.ts`, `controls.spec.ts`, `responsive.spec.ts` on the five |
| The look holds on the settings screens; tokens only; every screen declared; no dead controls | Automated | `look.spec.ts -g` the five sections, `token_lint.py --require-clean jarvis-web/src`, `web_states_check.py`, `web_dead_controls.mjs`, `svelte-check`, vitest (the SSR test renders `Models` loading with no timers) |
| The pictures are current | Automated *by construction* | the gate regenerates `docs/ui-review/settings*/` at three widths and refuses a `settings-desktop/` |
| The settings routes open in the real console on the running stack | Automated — **from the main checkout only** | `LIVE_CONSOLE_ROUTES=/settings,/settings/assistant python3 testing/live/console_pass.py`; the gate refuses to run it from a git worktree (red, with the reason), the way the rig does |
| What the operator's own server says about its models beyond ids and loaded state | **Bounded by the server** | llama-swap answers ids and `status`; vLLM answers `root` and `max_model_len`; a parameter count or quant that is not in either is read off the name and the row says so. Nothing is invented |
| Whether the five sections are the *right* five | **Needs eyes** | `docs/ui-review/settings-*/` |

### The console on Reactor II (M50)

`bash scripts/verify/m50-webui-c2-look.sh`. The look, measured.

| Claim | Level | Proof |
|---|---|---|
| Every row in `docs/UI_MIGRATION.md` §3's M50 list is ticked, and the gate refuses an unticked one | Automated | the gate reads the section and fails on any `- [ ]` |
| The nine components the pages needed exist, are exported, documented, SSR-tested and on the style guide | Automated | the gate checks each of `TopBar`, `SectionStrip`, `StatusReadout`, `StagesBar`, `CallLine`, `DayStrip`, `ProgressRing`, `Figure`, `Graph` in `index.ts`, `README.md`, `ssr.test.ts` and `/styleguide` |
| No console furniture, grid or brackets survive | Automated | the gate greps `chrome.css` and every `.svelte/.css/.ts` under `src` |
| A pill radius only where a thing is round | Automated | the gate lists every `--jv-radius-pill` outside the dots, rings and the toggle track |
| Body prose is never mono, no text glows | Automated | the gate reads `base.css`/`chrome.css` for the ground face and greps `text-shadow`; `look.spec.ts` measures the computed face of every visible paragraph |
| The render matches the direction on every screen | Automated | `e2e/look.spec.ts`: Barlow ground, `--jv-bg` body, no `.jv-grid`/`.jv-bracket`/`<canvas>`, no pill-shaped control, panels only on palette colours, the bar's underline present, at most one filled accent control |
| The knowledge graph draws every note and memory entry, lights on use, and never prints one name over another | Automated | `e2e/knowledge.spec.ts`: a node per note and entry, every pair of label boxes disjoint, the mock's `[[heating]]` link as an edge, a node click opens the note, `jarvis/test/memory_used` lights and settles, `note_search` lights the match, reduced motion still reports `data-lit` |
| Every screen still renders, handles its four states, holds at five widths, every control works | Automated | `states.spec.ts`, `responsive.spec.ts`, `controls.spec.ts`, then the whole suite |
| The pictures are current | Automated *by construction* | the gate regenerates the 16 × 3 screenshots and the four recordings |
| Every route opens in the real console against the running stack with no console error and only palette colours | Automated | `python3 testing/live/console_pass.py` — a real browser against the container on :8199 |
| Whether it is *good* | **Needs eyes** | `docs/ui-review/` and `docs/motion-review/` |

### Enrolment, complete (M71)

`bash scripts/verify/m71-enrolment.sh`. Server: `jarvis-core/tests/test_speaker_gate.py` (the M71 block at the end:
the household, the pipeline naming who, the bus, the store, the configured threshold, the API with names, the
security posture — 33 → 63) and `test_llm.py::test_the_agent_is_told_who_is_speaking_and_only_then`; console:
`e2e/enrol.spec.ts` (7) beside the voice-identity tests in `e2e.spec.ts`, `voice-live.spec.ts` (the speaker row),
`activity.test.ts` and `enrolment.test.ts`; phone: `enrolment_flow_test.py` (17), `activity_mirror_test.py` (7),
`ActivityRowsTest` and the `voice-activity` golden. The contract both strips read is
`tests/contracts/speaker_verdict.json`. The operator asked to "make sure enrolment is completely implemented and
complete"; this is every step, end to end, with what proves it — and the gate fails on any row that says Missing.

| Step | Status | Evidence |
|---|---|---|
| Start: the phrases come from the server, one list for both surfaces, with `min_samples`/`max_samples` | Automated | `test_the_prompts_are_served_so_both_surfaces_agree`; `e2e.spec.ts` "the console offers enrolment, reading its phrases from the server"; `enrolment_flow_test.py::test_the_phrase_index_comes_from_the_server` |
| Who: a sample is enrolled under a name; empty is `owner`, as before names; a bad name is refused in the server's words; names match case-insensitively | Automated | `test_enrolling_with_a_name_adds_a_second_person`, `test_a_name_that_cannot_be_one_is_refused`, `test_status_for_one_person_is_case_insensitive_and_honest_about_absence`; `enrol.spec.ts` "enrolling under a name sends the name with every sample…", "a name that cannot be one is refused…"; `enrolment_flow_test.py::test_the_client_sends_the_name_with_every_sample_and_can_forget_one`, `…::test_the_screen_asks_who_before_what_and_follows_that_persons_count` |
| Recording: the browser records real audio through the worklet and refuses a tap before sending; the phone records tap-to-tap into a bounded buffer | Automated (console) / Unproven (phone) | `e2e.spec.ts` "enrolling from a locked console is refused…" records through Chromium's fake microphone and really posts; `enrolment.test.ts` "refuses a tap before it reaches the server"; the phone needs a microphone: ADT-021, ADT-052 |
| Upload: one sample per request, WAV or raw PCM, bounded, refused with a reason when it cannot be used | Automated | `test_raw_pcm_and_wav_enrol_identically`, `test_a_sample_with_no_speech_is_refused_with_a_reason`, `test_a_pitchless_sample_is_refused`, `test_an_oversized_sample_is_refused_before_it_is_decoded` |
| Embedding: MFCC statistics and a pitch histogram, in a worker thread, the audio dropped when the request or the run ends | Automated | `test_speaker.py` (70); `test_the_turn_audio_is_dropped_when_the_run_ends`, `test_nothing_is_buffered_when_the_gate_is_off` |
| Storing: `voice_profile.json` version 2 (`people`), chmod 600; a version-1 file loads as `owner`; a duplicate name keeps the first; a household survives a restart with everyone named | Automated | `test_a_store_from_before_names_loads_as_the_owner`, `test_the_store_round_trips_a_household`, `test_a_store_with_the_same_name_twice_keeps_the_first`, `test_a_store_with_nothing_recognisable_is_nobody`, `test_a_household_survives_a_restart_with_everyone_named`, `test_enrolment_survives_a_restart` |
| Verifying a later utterance: compared with everyone, the best verdict wins; `label` names who only when accepted, `nearest` names the closest person on a refusal; one named person can be asked for alone | Automated | `test_a_household_credits_each_voice_to_its_own_person`, `test_a_stranger_is_nobody_and_the_verdict_says_who_they_were_nearest`, `test_verifying_against_one_named_person_ignores_the_others`, `test_verify_says_who_it_was`, `test_verify_against_one_person_compares_with_that_person_only` |
| The pipeline's use of the verdict — who is speaking: the agent is told `speaker=<label>` and its prompt ends with one line, only for a turn the gate accepted; an agent that cannot take it is still called | Automated | `test_the_pipeline_names_who_spoke_to_an_agent_that_can_hear_it`, `test_an_agent_that_cannot_take_a_speaker_is_still_called`, `test_the_speaker_is_none_for_every_turn_the_gate_did_not_accept`; `test_llm.py::test_the_agent_is_told_who_is_speaking_and_only_then` |
| What changes when it is a stranger: the turn never reaches the agent, the refusal is spoken or silent as configured, its code is not an stt code, and `observe` never blocks | Automated | `test_an_impostor_never_reaches_the_conversation_agent`, `test_a_refusal_is_spoken_by_default`, `test_silent_refusal_says_nothing_at_all`, `test_the_refusal_code_is_not_an_stt_code`, `test_observe_scores_but_never_blocks` |
| The house sees it: `jarvis_speaker_verdict` on the bus in the contract's shape, never a vector, nothing when the gate is off; the console's strip and the phone's draw the name, "unverified", or "not recognised · nearest …" | Automated | `test_the_verdict_goes_on_the_bus_in_the_contract_shape`, `test_a_refusal_on_the_bus_names_nobody_and_says_it_was_enforced`, `test_an_unverifiable_turn_is_on_the_bus_as_unverifiable_not_as_a_stranger`, `test_an_on_device_transcript_refused_while_enforcing_is_on_the_bus_too`, `test_nothing_is_fired_when_the_gate_is_off_or_absent`; `activity.test.ts` "who the voice gate heard (M71)"; `voice-live.spec.ts` "a voice recognised, and a stranger refused, are rows too"; `ActivityRowsTest.aVoiceHeardIsNamedAndTooShortToJudgeIsNotAStranger`; `activity_mirror_test.py::test_the_speaker_row_follows_the_verdict_contract` |
| The confidence threshold: each profile's own leave-one-out suggestion, recomputed every sample; `voice: speaker: threshold:` wins over all of them, survives the next sample and a restart, and the screens say which is live | Automated | `test_a_configured_threshold_survives_every_enrolment_sample`, `test_a_configured_threshold_is_reapplied_on_restart`, `test_no_configured_threshold_means_each_profile_keeps_its_own`; `enrol.spec.ts` "a configured threshold is shown as the one in force…" |
| Re-enrolment: more samples widen that person's profile rather than replacing it; the phrase list follows that person's count | Automated | `test_enrolment_accumulates_and_reports_as_it_goes`; `enrol.spec.ts` ("Enrol owner again" at rest); `enrolment_flow_test.py::test_the_screen_asks_who_before_what_and_follows_that_persons_count` |
| Removing a voice: one person by name (404 if unknown) leaving the others, or everyone; a real delete on disk; behind the console password on the console | Automated | `test_forgetting_one_person_keeps_the_others`, `test_forgetting_everyone_is_still_all_or_nothing`, `test_forgetting_is_a_real_delete`; `enrol.spec.ts` "…REMOVE forgets one person" (the request carries `?label=Ted`, and is refused unlocked); `routes.test.ts`; `enrolment_flow_test.py::test_the_screen_lists_everyone_with_a_way_to_forget_one` |
| More than one enrolled person: up to `MAX_PEOPLE` (8), 409 when full rather than an eviction, adaptation teaches only the person who spoke, no vector on the people list | Automated | `test_the_house_holds_at_most_max_people`, `test_adaptation_teaches_the_person_who_spoke_and_nobody_else`, `test_nobody_in_the_people_list_carries_a_vector` |
| The console screen: one row per person with REMOVE, a name box, ENROL, TEST that says who and what enforcement would do, FORGET (everyone); loading, error, offline and two empties (nobody enrolled; no voice identity) as real states; the inventory holds | Automated / Manual (the look) | `enrol.spec.ts` (7), `settings.spec.ts`, `menus.spec.ts` "SETTINGS › Voice holds to the inventory", `states.spec.ts` (Voice settings); `docs/ui-review/settings-voice/{desktop,tablet,mobile}.png`, re-rendered by the gate and looked at on 26 Aug (the enrol row stacks under 720px) |
| The phone screen: "Who is this" above the phrases, the household listed with FORGET each, FORGET EVERYONE, TEST MY VOICE naming who, a loading line; the strip's speaker row | Automated as a Python mirror and on the JVM / Unproven on a device | `enrolment_flow_test.py` (17), `activity_mirror_test.py` (7), `ActivityRowsTest`, the `voice-activity` golden (recorded and verified), `assembleDebug`, `lintDebug`; a device is ADT-021, ADT-052, ADT-053, ADT-054 |
| Security posture: enrolment is a REST write with a credential a person holds — no tool, no socket command, no service, the routes on the token-gated router — so no turn, fenced or spoken, can enrol; the console's relay never lends the admin token | Automated | `test_no_tool_and_no_websocket_command_can_enrol`; `routes.test.ts` "the voice-identity WRITES forward the caller and never the admin token"; `speakerRelay.test.ts` (9); `docs/security.md` "Enrolling a voice is a durable write about a person…", DEVIATIONS §22 |
| The API is documented, every route with its `label` | Automated | the gate reads `jarvis-core/docs/clients.md` for the four routes and `docs/voice-identity.md` for the table, "Who is speaking" and the store's shape |
| Accuracy on REAL human speech, and whether two real voices separate | Unproven | as before: nothing here has heard a person, and the household tests use the synthetic cast (the soprano as the second person, the baritone as the stranger). ADT-053 |
| What the live rig can prove | Not exercised | the rig speaks with one synthetic voice (Piper `en_US-amy-low`, `testing/live/voice.py`); it could be enrolled and every rig turn would then be verified as it, but no scenario does, the rig refuses worktrees, and one voice cannot show separation — a green row there would prove a vocoder matches itself. The boundary is left honest |
### Settings under approval (M67)

`bash scripts/verify/m67-settings-tool.sh` — 25 checks, 25 green on the branch on 26 Aug (from a worktree with `.venv` linked in; the script has no live-stack check). Server: `jarvis-core/tests/test_settings_tool.py` (19) with
`test_tool_tiers_contract.py` (the `held_summary` half), `test_settings_api.py`, `test_llm_tools.py` and
`test_ask_user.py`; harness: the two M67 tests in `testing/e2e/test_harness_selftest.py`, against a real
jarvis-core; console: `e2e/approvals.spec.ts`, 3, and `src/lib/tierContract.test.ts`.

| Claim | Status | Evidence |
|---|---|---|
| `list_settings` is Tier 1 and read-only — a tainted turn may still read it — and reads the registry the console reads (`settings_payload`), never a second list: a write through `config/settings/set` shows in the tool at once | Automated | `test_list_settings_is_direct_and_read_only`, `test_the_list_is_the_console_registry_not_a_second_one` |
| The whole list is every setting, compact (key, label, type, value), and fits one tool result; a filtered list says what a setting does, what it accepts (bounded: twelve choices and a count of the rest) and when it takes effect; every setting has a note to show | Automated | `test_the_whole_list_is_every_setting_compact_and_bounded`, `test_a_filtered_list_says_what_a_setting_does_and_accepts`, `test_every_setting_has_one_line_saying_what_it_does` |
| "Demo mode" is answered with the nearest real keys, not a guess — from `list_settings`, from `change_setting` (refused before anything is held; no card), and end to end through the scripted model | Automated | `test_asked_for_a_setting_that_does_not_exist_the_list_names_the_nearest`, `test_an_unknown_key_is_refused_with_the_nearest_before_anything_is_held`; harness `test_a_setting_that_does_not_exist_is_refused_with_the_nearest_and_nothing_is_held` |
| `change_setting` is Tier 3; the exact key, the value as the validator coerced it, the value it replaces and the label are pinned, with the sentence the card shows; a plain name resolves when it names one setting and is refused with the candidates when it names three; nothing changes until a human approves; a denial runs nothing; a value the validator refuses is refused before a card is raised; the pin reads "from" at the moment of asking | Automated | `test_change_setting_is_tier_three_and_held_with_key_value_and_previous_pinned`, `test_a_plain_name_resolves_when_unambiguous_and_is_refused_when_not`, `test_denying_runs_nothing`, `test_a_value_the_validator_refuses_is_refused_before_anything_is_held`, `test_the_pin_reads_the_value_at_the_moment_of_asking` |
| One write path: the console's `config/settings/set`, `POST /api/config/settings/set` and the approved tool all call `async_set_setting` — a change through either is what the other reads — with one validation, one audit line (`jarvis.settings.audit`: what, from, to, by whom) and one `jarvis_setting_changed`; a reset is audited and announced too | Automated | `test_the_tool_and_the_console_are_one_write_path` (a spy on the function, both doors), `test_approving_writes_through_the_console_path_and_says_what_changed`, `test_a_reset_from_the_console_is_audited_and_announced_too`; the gate counts the callers of `settings.async_set` in the tree |
| End to end against a real jarvis-core: the scripted model asks by the plain name, the held result carries the pinned key/value/previous and the sentence, `llm.approve` writes it, the console's list shows the overlay value, the event says `origin: llm`, and the server's log has the audit line for the set and for the reset | Automated | harness `test_a_setting_change_is_held_approved_written_and_audited` (`testing/scripts/run-e2e.sh -k setting`) |
| A tainted turn is HELD and marked, not refused (the opposite decision from `remember`; the comment in `tools.py` and `docs/security.md` say why); no spelling of `llm.expose`, `jarvis.http.*`, `local_only` or CORS resolves, because resolution is membership in `SETTINGS`, not a path into the config | Automated | `test_a_tainted_turn_is_held_and_marked_not_refused`, `test_a_key_the_allowlist_does_not_have_cannot_be_reached_by_any_spelling`; the gate tries nine spellings |
| The model is told to look settings up before saying one does not exist, and to name the real one when asked for something adjacent | Automated | the gate reads `TOOL_RULES` in `agent.py` |
| The card reads "Change Wake word (voice.wake_word) from hey_jarvis to ok_nabu" — the server's sentence as the headline, the tool's name under it, no raw `key: value` line — and approving it changes the setting the Settings page shows; a request with no sentence still draws name-and-arguments; the Tools page test-run of `change_setting` is held with the same sentence and an unknown key is refused there too | Automated | `e2e/approvals.spec.ts` (3); the field's name is bound to `tests/contracts/tool_tiers.json` `held_summary` by `tierContract.test.ts` and `test_tool_tiers_contract.py`; the sentence is on the wire in `test_the_summary_travels_on_the_wire_and_is_empty_for_a_tool_without_one` |
| The phone shows the sentence | **Not done** | the phone renders a held request as name-and-arguments; `summary` is on the wire and ignored there, as the contract notes |
| A real model, asked to "enable demo mode", says there is no such setting and names the nearest | Manual | the live rig refuses worktrees; run `bash scripts/verify/live_interaction.sh` from `/opt/jarvis` and ask |

### Something to browse (M65)

`bash scripts/verify/m65-catalogue.sh`. Server: `jarvis-core/tests/test_extensions.py` (the M65 block at the end,
13 tests, 4 of them one per shipped entry) with `test_skills.py` and `test_packaging.py`; console:
`e2e/catalogue.spec.ts`, 7, beside `extensions.spec.ts`, `mcp.spec.ts` and the Tools rows of `menus.spec.ts`.

| Claim | Status | Evidence |
|---|---|---|
| A fresh install browses something: `jarvis/extensions/browse` with no configuration answers the four shipped skills from one source, `bundled`, with no error | Automated | `test_a_fresh_install_browses_the_shipped_skills_with_no_error`; the gate runs the same call against a fresh `Jarvis` |
| The shipped source is the package's own folder, resolved from the skills package — right at `/srv/jarvis` in the image and under a checkout alike — as `file://`, and `DEFAULT_SOURCES` is still empty (no default list of remote origins) | Automated | `test_the_shipped_catalogue_is_the_package_folder_wherever_the_package_is`; the M47 gate's `DEFAULT_SOURCES == ()` check, unchanged |
| Every shipped entry parses through `entry_from_raw` (the hostile-input parser), points inside the catalogue, is pinned to a concrete ref, and names a folder that exists | Automated | `test_every_shipped_entry_parses_and_names_a_shipped_folder`, `test_every_shipped_entry_points_inside_the_catalogue` |
| The index is honest: description, version, author and permissions equal what the SKILL.md beside it declares, entry for entry | Automated | `test_the_shipped_index_agrees_with_the_skill_md_beside_it` (parametrised over the index) |
| `installed` says whether the registry holds something of that kind and id — `true` for the shipped skills on a fresh box, `false` with `skills: bundled: false`, `true` again after installing one from the catalogue, which lands the operator's copy that overrides the shipped one | Automated | `test_the_catalogue_says_which_shipped_skills_are_not_loaded`, `test_installing_a_shipped_skill_lands_the_copy_that_overrides_it` |
| The operator's `bundled` line wins: `enabled: false` turns the shipped source off (browse then says no source is configured), a different url replaces it, and their own sources keep it beside them | Automated | `test_the_operator_can_turn_the_bundled_source_off_or_replace_it`, `test_browse_with_the_bundle_off_says_nothing_is_configured` |
| A source that cannot be read is reported with its reason (`errors`, and `error` when nothing else is left to show), never swallowed into "nothing matched" | Automated | `test_a_source_that_cannot_be_read_is_reported_not_swallowed` |
| The catalogue is on the tools page at rest, above every fold, the shipped entries saying INSTALLED, the fixture's offering one INSTALL each with what they ask for on the row, and the old browse button is gone | Automated | `e2e/catalogue.spec.ts` "the catalogue is the first thing on the tools page…"; the gate reads `Tools.svelte` for the mount order and `Extensions.svelte` for the absence |
| The page's one search filters the catalogue as it filters every fold; there is still exactly one search box | Automated | `e2e/catalogue.spec.ts` "the page's one search…"; `e2e/menus.spec.ts` (Tools row, search 1) |
| Installing from the catalogue is the M47 flow unchanged — the plan with the ref, the hash, the permissions and every program, then the approval — and afterwards the row says INSTALLED and the Extensions fold has the skill without a reload | Automated | `e2e/catalogue.spec.ts` "installing is a second decision…", "a benign entry installs…" |
| MCP: one line says servers are added by URL in the MCP fold and a stdio program only in `configuration.yaml`; its control opens the fold on its form with the caret in it | Automated | `e2e/catalogue.spec.ts` "MCP is add-by-URL, one press from the catalogue…" |
| Loading, empty, error and offline are real states of the section: a skeleton; "No catalogue sources" with the key to add one; the source's own error with Retry; the last read under an offline note | Automated (three) / Manual (offline) | `e2e/catalogue.spec.ts` "a source that cannot be read…", "no source at all…"; the gate reads the component for all four; the page-level offline state is `e2e/states.spec.ts` |
| One filled primary on the screen: NEW SKILL, in the Extensions fold; INSTALL is a ghost row control and the dialog's INSTALL appears only open | Automated | `e2e/menus.spec.ts` (Tools row: primaries ≤ 1, per row at rest 2); the gate greps `Catalogue.svelte` for `variant="primary"` outside the dialog |
| The mock answers browse in the server's shape (`installed`, `sources`, `errors`, `error`) | Automated | the gate reads `tests/web/mock-ha.mjs`; `jarvis/test/catalog_mode` and `jarvis/test/extensions_reset` are test hooks only |
| It reads as the first thing on the screen, on the design system, at three widths | Manual | `docs/ui-review/settings-tools/{desktop,tablet,mobile}.png`, rendered by the gate and looked at on 26 Aug |
| Browsing a REMOTE (https) source | **Not exercised** | as under M47: the transport is written and the offline gate cannot reach the open internet; `Catalog.read` still lists only `file://` sources |

### Search that answers (M68)

`bash scripts/verify/m68-search.sh`. Server: `jarvis-core/tests/test_web_integration.py` (the
"second SearXNG" block, 8 tests), `test_research.py`.

| Claim | Status | Evidence |
|---|---|---|
| An empty answer with every engine responding is final — one request, `count: 0`, no fallback | Automated | `test_an_empty_answer_with_every_engine_responding_is_final` |
| An empty answer with engines in `unresponsive_engines`, a timeout or an unreachable instance is "could not search", and the stack's own SearXNG is asked next — in that order, and only when `SEARXNG_URL` is somewhere else | Automated | `test_a_remote_searxng_that_times_out_is_followed_by_the_local_one`, `test_a_remote_whose_engines_all_fail_is_answered_nothing_not_no_results`, `test_the_fallback_is_the_local_default_only_when_the_configured_one_differs`; the unreachable-does-not-fall-back test of M18 still holds for the default URL |
| The result names the instance that answered and what happened first (`instance`, `notes`, with each failed engine and its reason) | Automated | the two tests above assert on the notes; `test_unresponsive_engines_parse_from_both_shapes_and_cap_the_list` |
| When both cannot search, the error names each instance and its engines, and `cloud_fallback` is still `false` | Automated | `test_when_both_answer_nothing_the_error_names_each_instance_and_its_engines` |
| An explicit `searxng_fallback_url: ""` disables the second instance | Automated | `test_a_disabled_fallback_stays_disabled` |
| No cloud engine anywhere, fallback included | Automated | the M18 gate's grep, repeated in `m68-search.sh`; `assert_no_cloud_calls` in every new test |
| Against the house's real instances the client reports the tailnet SearXNG answering nothing and the stack's own answering | Scripted | the gate's last check runs the branch's client from the host with `jarvis-core/.env`'s `SEARXNG_URL` — 26 Aug: five engines timed out there, 5 results from `127.0.0.1:8888` in 4.5 s |
| A research run on the deployed house reaches a report instead of "nothing was found" | Manual | the live rig's `research-*` scenarios after the next rebuild; recorded in `docs/OVERNIGHT_LOG.md` when run |

### A writable coding workspace (M72)

`bash scripts/verify/m72-workspace.sh` (26 Aug 21:06: 10/10 on the ninth rebuild). Server: `test_packaging.py`
(`test_the_coding_workspace_is_the_mounted_crossover`, `test_the_image_installs_git_for_coding_jobs`),
`test_code_workspace.py`, `test_code_repos.py`.

| Claim | Status | Evidence |
|---|---|---|
| The deployed config names `/workspace`, not `~/jarvis/workspaces` (which is `/jarvis/workspaces` in the image and unwritable) | Automated | the packaging test reads `configuration.yaml`; the gate loads it through `load_config` |
| The core and the config-init one-shot both mount `../jarvis-workspace` at `/workspace`, and the one-shot chowns it for uid 10003 | Automated | the packaging test reads both services' volumes and the chown line |
| Inside the running core, uid 10003 can create and remove a folder under `/workspace` | Containerised | the gate's `docker compose exec` probe |
| The core image has git — the step after the workspace, without which `create_repository` answers "git is not installed" | Automated / Containerised | the packaging test reads the Dockerfile's apt line; the gate runs `git --version` in the running core (2.47.3) |
| A coding job's `create_repository` lands in `/workspace/<name>` with a `.git` visible on the host side of the crossover | Containerised | the gate replays the operator's request through `POST /api/services/code/create_repository` and checks `jarvis-workspace/m72-probe/.git`; the probe repository stays, since nothing removes one. The live rig has no repository-creating scenario yet |

### Ask and answer (M66, built; the live rig has not heard it)

`bash scripts/verify/m66-ask-and-answer.sh` (26 Aug 22:10: 28/28 in a worktree). Server:
`jarvis-core/tests/test_spoken_answers.py` (44 — the contract's cases), `test_ask_and_answer.py`
(21), `test_ask_user.py` (17); harness: `testing/e2e/test_ask_and_answer.py` (a question answered
by the next turn, an expired one told so — its own harness with a six-second question clock);
console: `e2e/e2e.spec.ts` "a question that runs out of time says it lapsed"; phone:
`presence_signals_test.py` (69), `prompt_reaches_the_user_test.py` (19).

| Claim | Status | Evidence |
|---|---|---|
| A question is spoken ONCE: the reply carries it and is read aloud by the surface the user spoke to; the phone gets the card marked `spoken` and stays silent. A typed turn's question is still spoken by the phone | Automated (the chain) / **Unproven** (a real spoken turn on a real phone) | `test_the_pipeline_tells_the_agent_whether_the_reply_is_spoken`, `test_a_spoken_turn_stamps_its_question_as_spoken`, `test_the_phone_is_told_a_spoken_question_is_already_spoken`, `test_the_companion_puts_spoken_on_the_wire`; the mirrors' four `spoken` checks; the gate reads the pipeline, the registry, the bridge, the wire and the three Kotlin files. The Kotlin does not build here |
| A question waits `question_ttl` (1800 s), its own knob, never derived from `approval_ttl` (300 s); the request carries `ttl` and the model is told how long | Automated | `test_a_question_waits_thirty_minutes_and_an_action_five`, `test_the_question_clock_is_its_own_and_not_derived`; the gate reads both defaults, the shipped config and the example house |
| A late answer is told "That question expired after N minutes; ask again and I'll wait" (`expired: true`, `waited_seconds`), an action likewise naming the tool, and nothing lapsed ever runs; an id the server never held still gets "unknown, expired or already-used" | Automated | `test_a_lapsed_question_is_answered_in_words`, `test_a_lapsed_action_names_the_tool_and_its_clock`, `test_a_lapsed_request_never_runs`, `test_an_id_the_registry_never_held_still_gets_the_honest_guess`, `test_the_lapsed_memory_is_bounded`; end to end `test_an_expired_question_says_so_in_words` |
| `jarvis_approval_expired` fires when the registry notices a lapse, carrying the request and `expired: true` | Automated | `test_a_lapsed_question_is_answered_in_words` |
| The next thing said in the conversation resolves what waits on it, by the contract's rules: a bare yes/no for an action, words picking out one choice for a question (the choice's own text), the words verbatim for free text; "yes and also…" is not a yes; two waiting and a yes is "say which"; a yes in another conversation approves nothing | Automated | `tests/contracts/spoken_answers.json` run by `test_spoken_answers.py` (39 cases + the list equality); `test_a_question_is_answered_by_the_next_turn`, `test_a_held_action_is_confirmed_by_a_yes`, `test_a_no_declines_and_nothing_runs`, `test_an_unrelated_turn_leaves_it_waiting_and_says_so`, `test_a_yes_in_another_conversation_approves_nothing`, `test_two_things_waiting_and_a_yes_resolves_neither`; end to end `test_a_question_is_answered_by_the_next_turn` |
| A request raised after untrusted content is never resolved by voice; the turn says it waits on the console | Automated | `test_a_tainted_request_is_never_resolved_by_voice`; the contract's three tainted cases |
| The resolution is `approve_request`, unchanged — single use, the answer reaching only the named argument — and the turn carries it as a tool row and continues with the result | Automated | `test_the_turn_events_draw_the_resolution_as_a_tool_row`; the e2e's `tool_calls` assertions |
| The held bar keeps a lapsed card ("This question lapsed after N minutes — ask again and Jarvis will wait", CLEAR), shows the server's clock as m:ss, and counts waiting and lapsed separately | Automated | `e2e/e2e.spec.ts` "a question that runs out of time says it lapsed, and does not vanish"; the mock's `expiredSentence` is the server's, word for word |
| The operator hears the question once, and can answer it by saying so, on the deployed house | **Unproven** | waits for the live rig (`scripts/verify/live_interaction.sh` has no ask-and-answer scenario yet); the worktree cannot run the rig |
| Settings › Assistant shows `question_ttl` | Unproven | not added: M67 is in the settings registry at the same time (the M70 precedent); `configuration.md` documents the key |

### The house is editable by voice (M69, built; the live rig has not heard it)

`bash scripts/verify/m69-editable-house.sh` (26 Aug 22:10: 19/19 in a worktree). Server:
`jarvis-core/tests/test_entity_remove.py` (29), `test_api.py` (`test_ws_entity_and_device_removal`),
`test_gated_services.py` (the tier table), `test_packaging.py` (the documented commands); harness:
`testing/e2e/test_ask_and_answer.py` (a removal confirmed by the next turn, "all of the elements"
refused); console: `e2e/editable-house.spec.ts` (3), `dashboards/tiles.test.ts` (the removed tile).

| Claim | Status | Evidence |
|---|---|---|
| `remove_entities` / `remove_device` are Tier 3 with the targets pinned when raised — concrete, normalised ids; a device with its name and every entity on it — and what runs after the yes is what was shown | Automated | `test_remove_entities_is_tier_three_with_the_ids_pinned`, `test_the_confirmed_removal_removes_exactly_what_was_shown`, `test_a_name_is_resolved_and_pinned_like_a_locks`, `test_remove_device_pins_the_device_and_everything_on_it` |
| "All of the elements" — `*`, `all`, `everything`, `the house`, nothing named, or an unknown id — is refused with a sentence naming `list_entities` BEFORE anything is held; more than 20 at once likewise; `remove_device` refuses the vague, the unknown and two matches | Automated | `test_all_of_the_elements_is_refused_with_a_sentence` (8 spellings), `test_an_unknown_id_is_refused_rather_than_held`, `test_too_many_at_once_is_refused`, `test_remove_device_refuses_the_vague_and_the_unknown`, `test_two_devices_with_the_same_word_are_not_guessed_between`; end to end `test_all_of_the_elements_is_refused_before_anything_is_held` |
| The refusal check is part of the tool: a re-registration without one is a weakening and refused | Automated | `test_the_refusal_is_the_registrys_and_a_rereg_cannot_drop_it` |
| One delete path: the tools, `config/entity_registry/remove`, `config/device_registry/remove` and the REST twins all run `Jarvis.async_remove_entity` / `async_remove_device` | Automated | the gate reads the four call sites; `test_the_api_removes_and_answers_with_what_it_removed`, `test_ws_entity_and_device_removal` |
| A removed entity leaves the state machine (`state_changed` with no `new_state`), the registry (saved gone), the exposure list and the house summary, and its live object — the platform's poll loop cannot write it back | Automated | `test_removing_an_entity_takes_its_state_and_its_entry`, `test_removal_is_saved_so_a_restart_does_not_bring_it_back`, `test_a_removed_entity_leaves_the_exposure_list_and_the_house_summary`, `test_a_platforms_poll_loop_cannot_write_the_state_back` |
| A device removal takes its entities first, then the record, and fires `device_registry_updated: remove` | Automated | `test_removing_a_device_takes_its_entities_first`, `test_the_api_removes_a_device` |
| The confirmation is sayable: "remove the decorative lights" → held → "yes, go ahead" → gone from the states and the registry, through the real server | Automated | `test_a_confirmed_removal_takes_the_entity_out_of_the_house` |
| The Devices screen offers REMOVE (two presses), the row leaves on the `state_changed`, a removal made elsewhere drops the row live, and the registries are re-read on their events | Automated | `e2e/editable-house.spec.ts` "REMOVE takes an entity out of the house…", "a removal made elsewhere…" |
| A dashboard tile whose entity was removed says so ("was removed from this Jarvis") rather than drawing stale state or "add the device" | Automated | `tiles.test.ts` "says the entity was removed…", `e2e/editable-house.spec.ts` "a dashboard tile whose entity was removed says so…" |
| The knowledge graph: the console's graph holds notes and memory only, so a removed entity has nothing there to leave | Automated (by inspection) | the gate reads `NodeKind` in `graph.ts` |
| An entity a platform keeps publishing comes back under a fresh entry; automations naming a removed id are not edited | Documented, not fixed | `Jarvis.async_remove_entity`'s docstring; the automation check reports the stale id |
| "Remove the X" spoken to the deployed house, confirmed out loud, and gone from the Devices screen | **Unproven** | waits for the live rig |

### A faster voice (M70, in progress)
### A faster voice (M70)

`bash scripts/verify/m70-voice-speed.sh` (26 Aug 22:04: 14/14 on the running house). Server: `test_packaging.py`
(`test_compose_piper_length_scale_matches_the_example_env`), `test_settings.py`
(`test_the_voice_pace_is_a_setting_that_says_where_the_real_knob_is`); console: `e2e/settings.spec.ts` (the pace row).

| Claim | Status | Evidence |
|---|---|---|
| Piper is started at `--length-scale ${PIPER_LENGTH_SCALE:-0.9}` (1.1×), and the example env carries the same number | Automated | the packaging test parses both; the gate inspects the running `wyoming-piper`'s args |
| The faster voice is still understood: the smoke set's WER stays under the 10 % threshold | Scripted | the gate reads `.verify/live/results.json` — 26 Aug 20:31 smoke set 7/7, WER 0.0 over 4 spoken samples, median 2.40 s; 22:04 5/5, WER 0.0 over 3, median 2.79 s |
| Settings › Voice shows the pace as a number, applied on restart, with the note naming `PIPER_LENGTH_SCALE` and `wyoming-piper` | Automated | the registry spec `voice.tts.length_scale` (test above), the console plan and mock row (the gate greps both), `e2e/settings.spec.ts` "the voice pace is on Settings › Voice…" |
| The chain is whole: compose hands the variable to the core, `configuration.yaml` reads it, the package reads the key | Automated | `test_every_documented_env_var_is_actually_read_by_something`, `test_no_shipped_option_is_silently_ignored` — each refused a half-measure on the way |

### The dashboard, a destination (M62)

`bash scripts/verify/m62-dashboard-main.sh` — 17 checks.

| Claim | Status | Evidence |
|---|---|---|
| `/dashboards` is a top-level tab, the first after VOICE, with no destination above it; `/house/dashboards` is no longer a screen | Automated | the gate reads `screens.ts`; `scripts/verify/web_states_check.py` (every screen declared once, served once) |
| The old path is a permanent redirect and nothing serves a page there | Automated | `routes/house/dashboards/+page.ts` (308); the M48 gate checks every `MOVED` entry has its redirect |
| The bar links the voice screen to five destinations, the dashboard first, and the dashboard's own path is its page | Automated | `e2e/e2e.spec.ts` "the bar links the voice screen to the five destinations" |
| The phone's strip mirrors the bar exactly and the console opens on the dashboard | Automated | `android-app/tools/console_parity_test.py` (run by `make test-android` and the gate); `ConsoleTab.DEFAULT` |
| The dashboard renders, loads, errors and goes offline as a screen of its own, and holds to the menu inventory | Automated | `e2e/states.spec.ts`, `e2e/menus.spec.ts` (DASHBOARDS row of `docs/UI_MIGRATION.md` §4), `e2e/dashboards.spec.ts` |
| The palette finds it under its own path | Automated | the gate reads `commandPalette.ts` |
| It looks like a destination — title, lede, then the arranged graphs, under a six-tab bar | Manual | `docs/ui-review/dashboards/{desktop,tablet,mobile}.png`, rendered by the gate and looked at on 26 Aug |
| No more than six tabs | Automated | `scripts/verify/m48-webui-c2.sh` (the cap, five until M62) |

### The dashboard shows the house (M63)

`bash scripts/verify/m63-dashboard-widgets.sh` — 32 checks, green on 26 Aug. Server: `jarvis-core/tests/test_dashboards.py`
+ `test_dashboard_widgets.py`, 46 passed (32 + 14). Console: `npx vitest run src/lib/dashboards`, 70 passed
(the whole suite 757, from 716); `e2e/dashboards.spec.ts`, 11 passed (from 5).

| Claim | Status | Evidence |
|---|---|---|
| A widget has a kind — metric, entity, readings, camera, sky, moments — and the server, the console and the contract name the same six, each with what it needs | Automated | `tests/contracts/dashboard_layout.json` `kinds`; `test_dashboards.py` "the module shows the kinds the contract names"; `layout.test.ts` "names exactly the kinds the contract names"; the gate reads all three |
| A layout saved before kinds existed loads unchanged: a widget with no `kind` is a graph, a graph with no `type` a line | Automated | `test_dashboards.py` "a layout saved before kinds survives a reload" (M62's JSON byte for byte); `layout.test.ts` "reads a widget with no kind as a graph" |
| A widget missing what its kind needs is refused, not drawn blank; an entity tile needs an entity id the state machine could hold | Automated | `test_dashboards.py` "a kind missing what it needs is refused"; `layout.test.ts` the same, both driven from the contract's `needs` |
| The console sends the server only the fields a kind needs, and reads back what it sent | Automated | `layout.test.ts` "sends the server only the fields a kind needs" (`wireWidget` ↔ `toWidget`) |
| `jarvis/sensors/readings` is every sensor's newest reading with its room and age, the dead ones flagged, `area` filtering as the tool does; answers over the socket | Automated | `test_dashboard_widgets.py` (payload and a `WebSocketHandler` round trip) |
| `jarvis/sky/summary` is the next pass and the moon from cached elements — the same numbers the entities carry — and says `unknown` with the reason before anything was fetched; `configured: false` without `sky:` | Automated | `test_dashboard_widgets.py` against `tests/fixtures/tle/iss.csv` and the `de421` excerpt (the M58 fixture) |
| `jarvis/vision/still` is a look: the frame comes back as a JPEG data URL with one audit row (`snapshot`, the socket's token as requester), a `consent: never` camera is refused before any fetch and audited, an unknown camera names the ones there are, the only camera need not be named, no camera at all is `configured: false` | Automated | `test_dashboard_widgets.py` against the M56 fake camera; the gate reads `VisionManager.still` for the snapshot path and the absence of any disk write |
| The requester on a still comes from the socket's token, never from the payload | Automated | `test_dashboard_widgets.py` "takes its requester from the token not the payload" |
| Each kind renders its data, and its empty state is a sentence saying how the thing gets there | Automated | `tiles.test.ts` (server-rendered: a tile with its switch, a lock with UNLOCK, a sensor with none, "No entity called…", rooms in order, the refusal, "not fetched yet", "No moments yet"); `widgets.test.ts` (grouping, ages, the sentences) |
| The console opens on the House: a tile, the readings by room, the camera, the sky, the moments | Automated | `e2e/dashboards.spec.ts` "the console opens on the House" against the mock |
| A press on an entity tile sends the same `call_service` the Devices row sends, and the tile changes only when the backend says so | Automated | `e2e/dashboards.spec.ts` "a press on an entity tile round-trips call_service" — the frame is read off the socket, the tile off `state_changed` |
| A camera without consent shows its refusal and no frame; a camera that consents shows the frame | Automated | `e2e/dashboards.spec.ts` "a camera without consent…" and the Garden camera in "the kind picker…" |
| A moment landing live goes to the top; a reading changing live changes its row | Automated | `e2e/dashboards.spec.ts` "a moment landing live…", "a reading changing live…" |
| `+ Widget` asks the kind first; a tile, a camera and the sky can be added to a board you own and survive a reload; a name that is not an entity id is refused with a sentence | Automated | `e2e/dashboards.spec.ts` "the kind picker…" |
| The inventory row allows an entity tile's one switch at rest and nothing on the other kinds; the dashboard still renders, loads and goes offline as a screen | Automated | `e2e/menus.spec.ts` (DASHBOARDS row of `docs/UI_MIGRATION.md` §4, per row at rest 1), `e2e/states.spec.ts` |
| The shipped House opens first on a fresh install, names no device nobody owns (its tile is `sun.sun`), and leaves the camera unnamed | Automated | `test_dashboards.py` "the shipped house…", "a fresh install opens on the house…" |
| It looks like the design — near-black, hairline cards, the display face on the big figures, tabular numerals, one cyan on the hero's live value — at three widths | Manual | `docs/ui-review/dashboards/{desktop,tablet,mobile}.png`, rendered by the gate and looked at on 26 Aug |
| A still from a real camera on this host, and the sky from real downloaded elements | **Unproven** | this host has no camera configured and the sky cache is the fixture's; the paths are proven against the M56 fake camera and the M58 fixture, and the widgets say "no camera is configured" / "not fetched yet" on this box, which is the truth |
### The phone looks like the console (M64)

`bash scripts/verify/m64-phone-look.sh`. Build, unit, lint and goldens — no device. An audit put
the native screens beside `jarvis-web/src` and listed fourteen mismatches; the gate reads the
Kotlin for each one.

| Claim | Level | Proof |
|---|---|---|
| The tab strip is `TopBar.svelte` under 720px: the mark and JARVIS, the readout, tabs at the smallest step with tight tracking, ONE underline the anchor's width that slides on `motion.dur.base`, the current tab scrolled into view, the overflowing edge faded to the ground | Automated | the gate greps `ConsoleFrame.kt` (no `withUnderline`, one `UNDERLINE_TAG`, `Motion.Dur.BASE`, `getSolidColor`) and `ManagementActivity.markCurrentTab` (no rebuild); `console_parity_test.py` holds `ConsoleTab` and PHONE where they were; the `console-frame` golden |
| Rows are one panel with a hairline between (`JarvisUi.rows`); `checkRow` and the activity strip no longer box themselves | Automated | the gate greps `JarvisUi.kt`, `ActivityStrip.kt`, `SystemCheckActivity.kt`, `CrashLogActivity.kt`; the `settings-fields` and `voice-activity` goldens |
| APPROVE is the accent primary (`Approvals.svelte`), not OK green; the held bar's action is the screen's primary | Automated | the gate reads `consentButton` and `banner`; the `approval-banner` golden |
| An activity row spends the accent on a dot — glow and pulse while live, danger when failed, tick at rest — with the body face for the title and tabular mono for the datum | Automated | `StateDot.kt` on `motion.dur.pulse`; the `voice-activity` golden |
| Every chrome word is the label recipe; no `letterSpacing` literal and no `dp(this@Activity, N)` literal remains, and the lint now catches both | Automated | `scripts/verify/token_lint.py` (`KT_TRACK`, `KT_SPACE` with any receiver) — 55 literals it had missed (46 dp, 9 tracking) are on the scale; the baseline is still empty; the gate plants one of each and checks the lint reports them |
| No `Color.WHITE`; failure text is `--jv-danger-text`, never the mark colour | Automated | the gate greps `ToolActivityView.kt`, `CompanionAskActivity.kt`, `TaskProgressView.kt` and every `setTextColor(JarvisUi.DENY)` |
| The reactor rests in `--jv-accent-deep` with an accent dot on both surfaces, as `Reactor.svelte`'s `.reactor` block does; `SiriPalette` stays pinned; the caption is the console's; no wordmark over the orb | Automated | `android-app/tools/reactor_orb_test.py` reads the web's resting block and `ReactorOrb.Palette` and both views; `design/build.py --check`; the `orb-idle` golden |
| Screen titles are `ScreenTitle`: left, display face, sentence case, a lede — and every literal the instrumented suite taps is still on its screen | Automated | `instrumentation_contract_test.py` (the caps button labels stay; `textIgnoringCase` matches the sentence-case titles) |
| Every screen has the four states: `ScreenStates.kt` draws loading, empty, error and offline as the console does; the console screen uses three, the crash log the fourth | Automated | the gate greps both activities; `accessibility_labels_test.py` (the status panel is a live region); the `screen-states` golden |
| Settings has the console's section strip, in-page, over one scrollable column | Automated | the gate greps `SectionStrip.kt` and `SettingsActivity.kt`; the `section-strip` golden |
| Controls take the console's geometry: buttons 16×10, fields space-2/space-3, a chooser as a value | Automated | the gate reads the three builders; the `components` and `settings-fields` goldens |
| Bars are flat; the sweep runs on `motion.dur.sweep` | Automated | no `LinearGradient` / `Orientation.LEFT_RIGHT` in either bar; the `task-overlay` golden |
| Graph labels are the body face knocked out of the ground; memories are a faint dot, not gold; a name never leaves the box | Automated | `knowledge_graph_mirror_test.py`; the `voice-graph` golden |
| Lists enter staggered on `motion.stagger.step/cap` over `motion.dur.enter`; under reduced motion the orb's drift, the live dot, the task sweep and every entrance stop | Automated | `JarvisUi.reducedMotion` and `JarvisUi.enter`, read by the gate in the orb views, `StateDot.kt` and `TaskProgressView.kt` |
| It builds, its 207 unit tests pass, lint is clean, fourteen Roborazzi goldens verify (`screen-states` and `section-strip` new; eight re-recorded and looked at) | Automated | `gradle :app:assembleDebug testDebugUnitTest lintDebug verifyRoborazziDebug` |
| What only a handset can confirm: the slide, the resting colour on a panel, reduced motion against a real setting, the strip following a real scroll, whether the two surfaces read as one | **Manual** | `docs/ANDROID_DEVICE_TESTS.md` ADT-047…051 |

### The phone, the equal of the web (M61, first stage)

| Claim | Status | Evidence |
|---|---|---|
| The phone's activity strip speaks the console's vocabulary: the same events, kinds, cap, sensor domains and states | Automated | `android-app/tools/activity_mirror_test.py` reads `ActivityRows.kt` against `tests/contracts/activity_rows.json` |
| The device subscribes to every event in that vocabulary and feeds the strip | Automated | the mirror (`ActivityRows.EVENTS.keys`, `onBusEvent`) |
| The strip's arithmetic: a tool call is one row start to finish, a button pressed twice is two rows, a light is not a reading, a look names its camera, a dozen newest first | Automated | `app/src/test/…/assist/ActivityRowsTest.kt`, run here with M08's toolchain (`./gradlew testDebugUnitTest`) |
| The phone builds the same knowledge graph as the console for the same house | Automated | `jarvis-web/src/lib/knowledge/graph.test.ts` and `KnowledgeGraphTest.kt` (run here) against `tests/contracts/knowledge_graph.json`; `knowledge_graph_mirror_test.py` pins the constants and the PRNG |
| A reply plays as sentences then the remainder, never twice; an off-origin chunk is refused | Automated | `android-app/tools/tts_chunk_test.py` reads the client and the conversation |
| Twenty Tasker rows closed, each registered once at its stated tier, each with a unit test of its arithmetic; none left gap | Automated | `ParityActionsTest.kt` (run here); `action_table_test.py` pins registration, tiers and docs; `scripts/verify/m61-android-tasker.sh` reads the parity table against the registry |
| The last six — a headless Camera2 photo, a scan through the scanner app, the inbox and the call log behind three new Tier-3 grants, a hang-up, an NFC tag read and written — with every third-party result marked untrusted and the NDEF bytes encoded by hand | Automated | `CameraPhoneNfcActionsTest.kt` (19 tests, run here); `action_table_test.py` pins the tiers and the untrusted flags; `runtime_permissions_test.py` pins `READ_SMS`, `READ_CALL_LOG`, `ANSWER_PHONE_CALLS` against the manifest and the checklist |
| A photo is exposed and upright, a scanner answers, a message and a call read back, a call ends, a tag round-trips, and a command from the hub with the phone in a pocket fails in seconds rather than hanging | **Unproven** | ADT-040…046 |
| The phone's reactor sweeps once per tool call, beats while speaking and gathers its iris while looking — the M53 vocabulary, timed by the same tokens | Automated | `android-app/tools/reactor_motion_test.py` reads `ReactorOrb.kt`, `JarvisOrbView.kt`, the conversation and `Reactor.svelte` |
| Loops in the task engine: `repeat` by count or while a condition holds, bounded by `TaskLimits` | Automated | `android-app/tools/task_repeat_test.py` |
| The Kotlin compiles and lints | Automated | `./gradlew assembleDebug`, `lintDebug` — green in the m08 gate with M61's Kotlin, 26 Aug 12:05 |
| The goldens hold with the strip and the graph on the voice screen — twelve, `voice-activity` and `voice-graph` recorded on 26 Aug and looked at | Automated | `./gradlew verifyRoborazziDebug`, green here with M08's toolchain |
| Early speech is heard, the strip and graph are legible over the launcher, two presses look like two | **Unproven** | ADT-036, ADT-037, ADT-038 |

### Anything online, locally (M59)

| Claim | Status | Evidence |
|---|---|---|
| A page that changes lands as a moment (kind `watch`, source `watch`, the page as link) and a bus event; the first check is a baseline, a rewrap is not a change | Automated | `jarvis-core/tests/test_watch.py` against an httpx MockTransport |
| A feed with a new entry is a moment naming it; RSS 2.0 and Atom parse with the standard library; a page that is not a feed is refused | Automated | `test_watch.py` |
| "Tell me when …" asks again every interval, judges the results with the model's no-tools call, and stops on yes | Automated | `test_watch.py` with a stub model |
| `read_page` reads through jarvis-browser when configured and never also here; otherwise here, scripts dropped | Automated | `test_watch.py` |
| No watch checks faster than 30 s; a bad URL is refused; a failed fetch is a recorded error, not a change | Automated | `test_watch.py` |
| Watches survive a restart | Automated | `test_watch.py` |
| The rig can rewrite a fixture page (`fixture_write`, only under `live/`, removed afterwards) and name the fixture web (`{{handbook}}`) | Automated | `testing/live/tests/test_rig.py`; the gate parses `watch-page-change` |
| Asked to watch a page, the running Jarvis watches it; the page changes; the moment lands; asked what is watched, it says so | Containerised | the gate, 26 Aug ~09:55 on the harness ground: LIVE 8/8, `watch-page-change` 3/3 turns — "Watch the page at …/live/notice.html and tell me when it changes" → `watch_page` → "I'm now watching that page every 30 seconds and will let you know the moment it changes."; the rig rewrote the page; a `watch` moment landed inside 90 s; "What is being watched right now?" → `list_watches` → "One page is being watched: the live notice page at …, checked every 30 seconds — it has been checked twice and changed once, currently showing a pool notice (open 06:00–22:00 daily, with lane swimming on Tuesdays)." |
| `watch:` is switched on in the deployed config and every key is read | Automated | `test_packaging.py`; the gate |
| Price and stock parsing (changedetection.io) | **Not built** | `docs/research/local-intelligence.md` §3 — adopt when that is the question |

### Intelligence and speed (M60)

| Claim | Status | Evidence |
|---|---|---|
| Every model request asks the server to keep the prompt prefix (`cache_prompt`, top-level and in `extra_body`) | Automated | `jarvis-core/tests/test_openai_compat.py::test_every_chat_request_asks_the_server_to_keep_the_prompt_prefix` |
| The system prompt is stable-first and the clock is last, so two turns share the whole prefix | Automated | `tests/test_llm.py::test_the_prompt_prefix_is_stable_across_turns` |
| A full house's system prompt fits `PROMPT_TOKEN_BUDGET` (6,000 estimated tokens) | Automated | `tests/test_llm.py::test_the_system_prompt_fits_its_token_budget` |
| The first sentence is spoken (`tts-chunk`) before the reply is finished; `tts-end` carries the whole and the remainder | Automated | `tests/test_voice.py::test_the_first_sentence_is_spoken_before_the_reply_is_finished`; `jarvis-web/src/lib/pipeline.test.ts` plays chunks then the remainder |
| Early speech is off while a tool runs in the turn, and off by `voice: early_speech: false` | Automated | `test_voice.py` (the switch); the tool guard is `_speak_early`'s `_tools_ran`, set from `intent-tool-start` |
| After a narrated-not-made call the retry is answered under a schema naming exactly the tools offered, and the JSON answer is executed | Automated | `tests/test_llm.py::test_a_constrained_tool_call_is_schema_shaped` |
| The planner batches consecutive read-only steps into one round and verifies each on its own | Automated | `tests/test_task_plan_batching.py` |
| `voice: think: false` passes think=False for a spoken turn (the default, true, leaves the model's reasoning); `think=False` reaches the OpenAI wire as `chat_template_kwargs.enable_thinking: false` (top-level and forwarded); None leaves the model's default | Automated | `tests/test_llm.py::test_a_spoken_turn_does_not_reason_unless_the_house_says_so`; `tests/test_openai_compat.py::test_think_false_reaches_the_server_as_the_templates_switch_and_none_is_left_alone` |
| The same calls three rounds running end the turn, and the final round is told to answer | Automated | `tests/test_llm.py::test_a_turn_that_repeats_the_same_call_is_ended_and_answered` |
| `llm.fast_model`, when set, names the model for a spoken turn; text turns keep the chat model; the catalogue counts it as the fast path and stops calling it idle | Automated | `tests/test_llm.py::test_a_turn_can_name_its_model_and_the_voice_path_names_the_fast_one`; `tests/test_llm_catalogue.py` |
| Whisper runs `int8` on this CPU, decision written down | Automated | the gate: `--compute-type int8` in `jarvis-core/docker-compose.yml`; `docs/TOOLING_DECISIONS.md` |
| The routing table and its mirrors agree; the intelligence eval floors are what they were | Automated | the gate: `evals/test_routing.py`; `FLOORS` pinned in `scripts/verify/m60-intelligence.sh` |
| The live round trip, re-measured after the change | Manual | `bash scripts/verify/live_interaction.sh --full` against the rebuilt stack — recorded under "Known failures" below when run |

### Simpler menus everywhere (M55)

| Claim | Status | Evidence |
|---|---|---|
| The menu inventory (`docs/UI_MIGRATION.md` §4) names every leaf screen once with a rows marker, a per-row cap, a primary and a search count | Automated | the gate parses the table against `screens.ts` |
| Every screen holds to it: ≤ 1 primary at rest, no duplicate-named controls outside rows, no row over its cap, the declared search boxes | Automated | `jarvis-web/e2e/menus.spec.ts` (17) against the mock backend |
| The tools page is one search: a nonsense query empties every fold, `get_state` is found by name, each fold's header says how many match | Automated | `menus.spec.ts` |
| On an owned dashboard `+ Widget` is the one primary and the one way into the layout editor; DONE closes it | Automated | `menus.spec.ts`; `dashboards.spec.ts` enters through it |
| An entity row offers the one move it can make (open/close, play/pause, lock/unlock, start/dock) | Automated | `e2e.spec.ts` presses what the row shows (the front door starts locked → UNLOCK); the inventory caps a player row at 4 |
| The specs that drive the trimmed rows still pass | Automated | dashboards, e2e, console-repairs, knowledge, notes, memory, extensions, mcp, tasks, code, home, controls — 138 in the gate |
| Token lint, states, dead controls, svelte-check, vitest, look.spec | Automated | the gate |
| The trimmed screens at three widths | Scripted | the gate regenerates `docs/ui-review/{house-*,work-*,knowledge-*,settings-tools}/` |
| The trimmed routes open in the real console with no console error and only token colours | Containerised | the gate: `LIVE_CONSOLE_ROUTES=… testing/live/console_pass.py` against the running stack after `make up` |
| The phone mirrors the inventory | **Not built** | M61 |

### Cameras and local vision (M56)

`bash scripts/verify/m56-vision.sh`. Two wires, one refusal rule, the events the voice tab draws.

| Claim | Level | Proof |
|---|---|---|
| A look is one OpenAI-wire request with the frame as a base64 `image_url` part, the key from the env, the model as configured | Automated | `tests/test_vision_openai.py` — the request's exact shape against a fake model server |
| A refused look (consent, a fenced question, an unknown camera) sends nothing to the camera and nothing to the model | Automated | `test_vision_openai.py`, `test_vision.py` — the fake transport records every request |
| A model url off the LAN is refused before any frame is read while `local_only` holds | Automated | `test_a_public_model_url_is_refused_when_local_only` |
| A 4xx/5xx or an unreachable model is a clean could-not-look record, never a traceback | Automated | `test_vision_openai.py` |
| Every look fires the three events with the contract's fields and never a frame | Automated | `test_the_events_carry_the_contract_and_never_a_frame` against `tests/contracts/vision_events.json` |
| Frigate events become moments, one per event id | Automated | `test_frigate_events_become_moments_one_per_event_id` |
| go2rtc is behind `--profile cameras`, pinned, on loopback; `platform: go2rtc` resolves to its snapshot endpoint; compose, config and `.env.example` agree | Automated | the gate; `tests/test_packaging.py` |
| The fixture camera is served and the scenario asks the right question | Automated | the gate: the fixture site serves `/camera/kitchen.jpg` as a real JPEG; `vision-look-fixture` parses, gated on M56 |
| Jarvis describes the kitchen camera on the fixture ground | Live | `VISION_MODEL=<served vlm> python3 -m testing.live.runner --full --only vision-look-fixture` — needs a vision model on the model server (none is loaded today) |
| A real camera in this house | **Manual** | none configured; `vision: cameras:` in configuration.yaml names them |

### Any sensor (M57)

| Claim | Status | Evidence |
|---|---|---|
| `event` and `device_tracker` discovery components become entities; a button press is a bus event every time | Automated | `jarvis-core/tests/test_mqtt_sensors.py` (a Zigbee2MQTT button pressed twice → two `jarvis_mqtt_event`s) |
| The discovery birth is published on `<prefix>/status`; `discovery_birth: false` keeps quiet | Automated | `test_mqtt_sensors.py` |
| `discovery_allow_ids` / `discovery_deny_ids` keep a neighbour's rtl_433 TPMS out; deny wins | Automated | `test_mqtt_sensors.py` against `tests/fixtures/mqtt_discovery/rtl433_bresser.json` |
| Readings are one unit per device class at ingest (°F → °C, Wh → kWh); `canonical_units: false` passes them through | Automated | `test_mqtt_sensors.py` (ESPHome °F fixture, Shelly Wh) |
| Tasmota's own discovery and Shelly Gen2 status become switches with command topics and sensors with classes and units | Automated | `test_mqtt_sensors.py` end to end through the fake client: `cmnd/<t>/POWER` gets `OFF`, `<id>/command/switch:0` gets `off` |
| A malicious value template renders nothing | Automated | `test_mqtt_sensors.py` (four templates through the sandbox and one through a discovered sensor) |
| The model has `sensor_readings`, `sensor_compare`, `sensor_history`, `sensor_summary`, read-only, tier direct | Automated | `test_mqtt_sensors.py` |
| The six fixtures name their source | Automated | the gate counts them; each carries a `source` line (Zigbee2MQTT 2.13, ESPHome 2026.8, rtl_433 25.12, Tasmota 14, Shelly 1.4) |
| The rig can be the device: `do: mqtt_publish:` to the stack's broker | Automated | `testing/live/tests/test_rig.py`; `sensors-discovered` parses and publishes |
| A device announced over discovery is answered about a moment later, with its reading and unit; the lowest over the last hour reaches for `sensor_history` | Containerised | `LIVE_CAPABILITY=sensors bash scripts/verify/live_interaction.sh --full` against the running stack, 26 Aug ~08:30: LIVE 9/9, `sensors-discovered` 2/2 spoken turns — "What's the temperature in the garage?" → "12.5 °C, Sir — cool enough to keep the tools honest." (no tool: the reading was in the house state, routed `answer`, not scored); "What's the lowest the garage has been in the last hour?" → `sensor_history` → "11.0 °C, Sir — it has since crept back up to 12.5." routed `sensors`. The device retracted afterwards with an empty retained config |
| The keys are switched on in the deployed config and every one is read by code | Automated | `test_packaging.py::test_no_shipped_option_is_silently_ignored`; the gate |

### The sky (M58)

`bash scripts/verify/m58-sky.sh`. Green on unit tests alone, by design: everything below
runs against a real 2026 ISS element set, a 36 KB excerpt of de421, a frozen clock and London,
with `httpx.AsyncClient` replaced by a class that raises. The live half is a separate line the
integrator runs against the stack (below), never from the gate.

| Claim | Level | Proof |
|---|---|---|
| A pass is found within 48 hours with sane numbers: rise < culmination < set, minutes long, above the floor, compass words, a handful per two days | Automated | `test_a_pass_is_found_within_48_hours_with_sane_numbers` |
| The first pass and the first *visible* pass are different things — 01:35 in shadow, 04:45 lit and bright — and the answer leads with the visible one | Automated | `test_the_first_pass_and_the_first_visible_pass_are_different_things` |
| "Visible" is sunlit + house in the dark (sun < −6°) + above the floor; "bright" is visible and past 40° | Automated | the same test asserts `sunlit`, `dark_at_house`, `visible`, `bright` on both passes |
| Times are in the house's zone, and follow it (`time_zone`, or the automation clock a test puts there) | Automated | `test_times_are_in_the_house_zone` — the same instant as `+01:00` and `+05:45` |
| "ISS", "the space station", "zarya", nothing at all — all resolve; "Tiangong" with no elements cached says what IS cached | Automated | `test_a_satellite_can_be_asked_for_by_what_people_call_it` |
| `overhead_now`: below the horizon says so with the age; at culmination the station is up, at the pass's altitude and direction; a higher floor hides it | Automated | `test_overhead_now_says_below_the_horizon_with_the_age`, `test_overhead_now_finds_the_station_at_culmination` |
| The moon on known dates: waxing gibbous at 98 % on 2026-08-26, full 2026-08-28 05:18 BST, new 2026-09-11, new on the day of the eclipse, full at 100 % | Automated | `test_the_moon_on_known_dates`, `test_moon_phase_names_by_angle` |
| `planets_tonight`: dusk and dawn at −6°, every planet placed or listed as not up, rises/sets/best with a direction; Saturn at 42° due south, Venus low in the west at dusk; "already dark" starts the night now | Automated | `test_planets_tonight_shape_and_a_known_night`, `test_planets_tonight_when_it_is_already_dark` |
| Without the ephemeris the satellite tools still answer (visibility `null`, and the sentence says why) and the moon and planets say the file is missing | Automated | `test_without_an_ephemeris_passes_still_come_but_visibility_is_unknown` |
| A stale cache whose fetch fails keeps serving, reports its age (30 h), warns once, and the answer carries the epoch age | Automated | `test_a_stale_cache_keeps_serving_when_the_fetch_fails` |
| A fresh cache is not fetched; a fetch replaces the file and its clock atomically; a body with no satellites cannot clobber a good cache; `download: false` never fetches; the floor is CelesTrak's two hours | Automated | `test_a_fresh_cache_is_not_fetched`, `test_a_successful_fetch_replaces_the_set_and_its_clock`, `test_a_body_with_no_satellites_cannot_clobber_a_good_cache`, `test_download_off_means_no_fetch_however_stale`, `test_refresh_never_goes_below_celestraks_cycle` |
| OMM CSV is what is read; a hand-typed TLE is the fallback and builds the same orbit (epoch to the second, position to the kilometre) | Automated | `test_parse_elements_reads_omm_csv_and_falls_back_to_tle`, `test_a_tle_dropped_in_by_hand_is_read_and_dated_by_its_mtime` |
| Through `async_setup`: `sky.iss_next_pass` and `sky.moon` computed before they are added, the four tools at tier 1 and read-only, bad arguments degrade to defaults, no fetch and no loop with downloads off | Automated | `test_setup_registers_entities_and_read_only_tools`; the gate registers them against a bare `Jarvis` too |
| With downloads on, setup returns at once; the fetch runs after, fails against the shut network, keeps the fixture set, and the entities were computed from it | Automated | `test_setup_with_downloads_on_never_blocks_and_a_failed_fetch_keeps_the_cache` |
| No coordinates: warns, still works (for 0°N 0°E) | Automated | `test_setup_without_coordinates_still_works_and_warns` |
| Nothing in the tests touches the network | Automated | the autouse `no_network` fixture; the gate greps the test for it |
| skyfield is pinned to one minor; the venv has it; the builtin timescale loads offline | Automated | the gate reads `requirements.txt` back as a pin and imports it |
| The shipped config does not enable it; the router knows the four tools as `sky`; the scenario parses and is gated on M58 | Automated | the gate |
| Asked out loud, the running Jarvis picks the tool and answers with a time and a direction | Containerised | `LIVE_CAPABILITY=sky bash scripts/verify/live_interaction.sh --full` against the running stack, 26 Aug ~08:40: LIVE 9/9, `sky-iss-pass` voice and text both routed `sky` off `next_pass`. Voice: "The ISS is next visible tonight at 22:45 — it rises in the west, climbs to 88 degrees high in the north at 22:49, and is gone by 22:52. Bright, and nearly straight overhead. The elements are a little under a day old…" Text: "…the orbital elements are about 17 hours old, so allow a minute or two of leeway." |
| The first real download of elements and of de421 from this box | **Manual** | switch the block on, watch the log for `sky: refreshed stations.csv` and `sky: downloaded de421.bsp`; `sky.moon` leaves `unknown` |
| ADS-B through readsb behind profile `radio` | **Not built** | in M58's scope line; not in this change |
### Motion when it does things (M53)

`bash scripts/verify/m53-motion-acts.sh`. Each choreography measured; nothing moves under reduced motion.

| Claim | Level | Proof |
|---|---|---|
| The vocabulary names every act — listening, thinking, a tool call, a task step, a memory read, a sensor change, a camera look, waiting on you, speaking, an error, a moment — with what moves and its tokens | Automated | the gate reads `docs/design/MOTION.md` for each act; every token it names is in `design/tokens.json` and the generated files are current |
| Eight choreographies stay inside the frame budget while they play | Automated | `motion.spec.ts` "when it does things": each act fired through a mock hook, ninety frames sampled, the worst under `--jv-budget-frame`; the numbers in `.verify/motion.json` |
| All of them at once leave nothing running under reduced motion in the reactor, the strip or the caption | Automated | `motion.spec.ts`: `getAnimations({subtree})` across the three roots after every act, zero running |
| The reactor sweeps on a tool call, beats while speaking, irises while looking; the held bar pulses; only the strip's newest row moves | Automated *for the plumbing* | `Reactor.svelte` (`work`, `looking`, `[data-state='speaking']`), `Approvals.svelte`, `Activity.svelte`; whether it *reads* as work is the recording |
| No value typed by hand | Automated | the token lint, clean |
| The signature recording of Jarvis at work is current | Automated *by construction* | the gate regenerates `docs/motion-review/5-at-work.webm` |
| Whether it is *good* | **Needs eyes** | `docs/motion-review/` |

### VOICE, alive (M52)

`bash scripts/verify/m52-voice-live.sh`. The graph and the activity strip, driven by events.

| Claim | Level | Proof |
|---|---|---|
| The knowledge graph is on the voice tab with every note and remembered fact, and lights when a turn reads a fact or a note tool touches a note | Automated | `e2e/voice-live.spec.ts`: five nodes from the mock; `jarvis/test/memory_used` lights a node and it settles within one blink |
| Every kind of work is a row as it happens — tool (start → result, failure named), task (steps and status), sensor (reading and unit), camera (live while it lasts), memory, moment, approval | Automated | `voice-live.spec.ts` drives each through a mock hook that fires the core's own bus event; `src/lib/activity.test.ts` pins the mapping |
| A camera being looked at is said under the reactor while it lasts | Automated | `voice-live.spec.ts`: the caption reads *looking · Kitchen*, then does not |
| The strip is a glance, not a log: twelve rows, newest first | Automated | `voice-live.spec.ts` and `activity.test.ts` (fifteen tools → twelve rows, `tool_14` first) |
| Nothing in the strip animates under reduced motion | Automated | `voice-live.spec.ts` counts running animations in the strip: zero |
| The voice tab still holds at five widths and in its four states; the look is unchanged; no value typed by hand | Automated | `home.spec.ts`, `hud.spec.ts`, `responsive.spec.ts`, `states.spec.ts`, `look.spec.ts -g Voice`, the token lint |
| The voice tab opens in the real console against the stack with no console error | Live | `LIVE_CONSOLE_ROUTES=/ python3 testing/live/console_pass.py` |
| Whether it looks *alive* | **Needs eyes** | `docs/ui-review/hud/` |

### The phone, on the same look (M51)

`bash scripts/verify/m51-android-c2.sh`. Build, unit, lint and goldens — no device.

| Claim | Level | Proof |
|---|---|---|
| The phone's instrument is the web's: bezel, blades, coil, level, lens and dot from the one geometry contract, periods from the motion tokens | Automated | `android-app/tools/reactor_orb_test.py` pins the 23 constants and 8 periods to `tests/contracts/reactor_geometry.json`; `design/build.py --check` |
| Both orb views draw one renderer; the sphere shader, its specular and the brackets are gone | Automated | the gate greps `ReactorOrb.kt`, `JarvisOrbView.kt`, `SiriOrbView.kt` |
| No pill, ghost or bracket primitive survives; `button()`, `primary()`, `tab()` and the underline exist | Automated | the gate greps `JarvisUi.kt` and `ConsoleFrame.kt`; `make test-android` |
| It builds, its 185 unit tests pass, lint is clean, ten Roborazzi goldens verify (idle, listening, thinking, speaking among them) | Automated | `./gradlew assembleDebug testDebugUnitTest lintDebug verifyRoborazziDebug` |
| Nothing typed is a colour, size, font or duration | Automated | `scripts/verify/token_lint.py --require-clean android-app/app/src/main/kotlin` |
| What only a handset can confirm | **Manual** | `docs/ANDROID_DEVICE_TESTS.md` ADT-031…035 |

### The voice screen (M49)

`bash scripts/verify/m49-home-reactor.sh`. The signature surface, on Reactor II.

| Claim | Level | Proof |
|---|---|---|
| The reactor is the instrument — bezel, blades, coil, level, lens — drawn as an SVG from one geometry contract, not a shader | Automated | the gate: `Orb.svelte` and `orb-shader.spec.ts` are gone and nothing imports them; `reactor.test.ts` renders `Reactor.svelte` and counts 120 ticks, 36 blades and the radii against `tests/contracts/reactor_geometry.json`; `e2e/home.spec.ts` counts them in a real browser |
| Five distinct states, each a palette from `color.orb.*`, and an error state | Automated | `home.spec.ts` drives idle → listening → thinking → speaking and asserts four different computed stroke colours on the level arc; the gate asserts every state has its own rule |
| The level arc follows real amplitude | Automated *for the plumbing* | `home.spec.ts` samples `data-level` twelve times while a synthetic amplitude runs and asserts it moves; in use the number is the microphone's RMS while listening and the player's while speaking. Whether it *reads* as the voice is the operator's, on `docs/motion-review/2-orb-states.webm` |
| C2's chat layout: transcript left, exchange under the instrument, this turn right, the dock | Automated | `home.spec.ts` asserts the three regions' boxes at 1440px, the exchange filling from a turn, the timings in the THIS TURN panel, the reply in Space Grotesk, the question in Barlow, the timings in mono |
| One bar on every screen; the voice screen is its first tab; the underline is under it | Automated | `home.spec.ts` counts five tabs and polls the underline's box against VOICE's; `e2e.spec.ts` walks the four destinations from the voice screen and back |
| Nothing pre-C2 on the voice screen: no grid, brackets, tagline, pill, glowing text | Automated | the gate reads the page and chat mode for each; `home.spec.ts` asserts the DOM has no `.jv-grid`/`.jv-bracket` and no `<canvas>` |
| Reduced motion stops the instrument and rests the level | Automated | `hud.spec.ts` under `emulateMedia`: zero running animations in the reactor's subtree, `data-level` 0.00; the counter-test asserts the blades turn otherwise |
| The screen still works as a surface: approvals answerable, tools visible, mic mutable, palette left to the browser, scrolls on a short screen, a refused mic leaves typing | Automated | `hud.spec.ts`, unchanged in what it claims |
| The pictures are current | Automated *by construction* | the gate regenerates `docs/ui-review/hud/*.png` and `docs/motion-review/1-boot.webm` / `2-orb-states.webm` itself |
| A spoken turn and a typed one, through THIS screen in a real browser, against the running stack | Automated | `house-light-on` (voice, the fake capture device into the page's own VAD) and `chat-context-retention` (typed into chat mode) from the live suite |
| Whether it is *excellent* | **Needs eyes** | `docs/ui-review/hud/` at three widths and the two recordings. The harness proves geometry, palette, layout and motion; it cannot prove taste |

### Motion (M44)

`bash scripts/verify/m44-motion.sh` — 12 checks. Measured in a real headless
Chromium, on this host: four shared vCPUs, no GPU.

| Claim | Level | Proof |
|---|---|---|
| Durations, easings and stagger intervals are tokens, generated onto web and Android like colour and type | Automated | `python3 design/build.py --check` — `motion.*` in `design/tokens.json` becomes `--jv-dur-*`/`--jv-ease-*`, `lib/motion.ts` and `JarvisTokens.Motion`; the four curves the brief names (standard, decelerate, accelerate, spring) all exist |
| Every animation in the console comes from a primitive, not from a typed value | Automated | `scripts/verify/token_lint.py` covers `transition:`/`animation:` on the same ratchet as colour, and the gate asserts each keyframe's values are tokens or caller-set custom properties |
| No frame budget blown while things move | Automated *as a percentile, not a ceiling* | `e2e/motion.spec.ts` measures `requestAnimationFrame` gaps over the boot sequence and a busy task view: fewer than 10% of frames over 34 ms. **Not** "no frame over 16 ms" as the milestone asked — a 16 ms ceiling on four shared vCPUs measures this host's compositor, not the app |
| Nothing jumps under the reader | Automated | the same spec: cumulative layout shift below 0.1 across the boot sequence |
| `prefers-reduced-motion` removes motion rather than shortening it | Automated | `page.emulateMedia` (the context option did not reach the page — the spec asserts `matchMedia(...).matches` before it believes itself), one kill switch in `base.css`, and `src/lib/motion.test.ts` checks all five primitives return non-animating styles. A second, weaker rule of mine would have overridden the stronger one; the gate now fails if a second kill switch appears |
| The boot sequence never gates an action behind itself | Automated | the same spec types into the composer while the boot timeline is running and asserts the text arrived |
| A DevTools performance trace, and "no forced reflow in the animated paths" | **Not measured that way** | rAF gaps and CLS from inside the page, not a `Trace` artifact. Forced reflow is asserted structurally (the primitives never read layout during animation), not from a trace |
| The trace results and reduced-motion verdict in `docs/LIVE_TEST_REPORT.md` | **Not yet** | that file is M27's; the numbers exist in `.verify/` and go in when it is written |
| That any of it is *cool* | **Needs the operator** | four recordings in `docs/motion-review/`; `BLOCKERS.md` §5. The harness proves smooth, token-compliant and accessible, which is not the same claim |


### jarvis-web (HUD + management console)

| Capability | Level | Proof / command |
|---|---|---|
| Component and helper logic | Automated | `npm test` — 326 tests |
| The built app in a real browser | Automated *against a mock backend* | `npx playwright test` — 44 tests |
| The HUD driven against a **real jarvis-core** | **Unproven** | The Playwright suite runs the built app against `tests/web/mock-ha.mjs`, a JS stand-in. Nothing in CI points the HUD at an actual server. See *Closing the gaps*. |
| Microphone capture in the browser | **Unproven** | Playwright runs with `--use-fake-device-for-media-stream`; that proves the code path, not that a real microphone is captured, encoded and streamed. |
| Audio playback of TTS replies | **Unproven** | `--autoplay-policy=no-user-gesture-required` bypasses the thing most likely to break in a real browser. |
| WebGL arc-reactor orb rendering | **Unproven** | Headless chromium with software rendering says nothing about how it looks or performs on the user's GPU. |

### android-app

| Capability | Level | Proof / command |
|---|---|---|
| Policy truth table (AUTO / NOTIFY / CONFIRM) | Automated *as a Python mirror* | `python3 android-app/tools/policy_truth_table_test.py` |
| Action table (71 actions) | Automated *as a Python mirror* | `action_table_test.py` |
| Device-channel protocol, host and URL rules | Automated *as a Python mirror* | `channel_protocol_test.py` |
| Command dispatch (1152 modelled dispatches) | Automated *as a Python mirror* | `dispatch_spec_test.py` |
| **Nothing is written, documented, tested and never called** | Automated *as a Python mirror* | `no_empty_seams_test.py`. The general form of six separate bugs found in one week — `CompanionSpeechHost`, `MediaButtonGate`, the three headset settings, `PolicyStore.panic`, the install-result broadcast, nine permissions — every one of which was made entirely of correct code that nothing reached. It checks global slots for a filler, settings for both a writer and a reader, and every module with an executable spec for a caller; the seven mutations in its own history are each caught. It is a static reader and says so: it cannot see reflection, and a name in a comment is not a caller, which is why every check strips comments first. Running it found a seventh — `AutomationBridge.uiAutomation`, filled by the accessibility service since the day it was written and read by nothing. |
| **The in-app updater can actually install** | Automated *as a Python mirror* | `updater_install_test.py` (12 checks). `PackageInstaller.commit()` shows nothing — it sends `STATUS_PENDING_USER_ACTION` to an `IntentSender`, carrying the system's install activity, and something has to start it. Nothing received that broadcast, so every update downloaded, committed, and installed nothing, while Settings printed "confirm the system prompt". Whether a real APK installs over a real phone is still **Unproven**. |
| **Every dangerous permission is actually requested** | Automated *as a Python mirror* | `runtime_permissions_test.py` (29 checks, 19 permissions). The manifest promised a runtime request "at the moment it is first needed" and nothing outside `RECORD_AUDIO` and `POST_NOTIFICATIONS` ever made one — `requestPermissions` is a method on `Activity` and every command arrives in a Service, so SMS, calls, contacts, calendar, location and step count were declared, checked for, denied and never asked for. The spec holds the manifest and `RuntimePermissions.ALL` against each other, and holds both against the checklist. |
| **A prompt reaches the person it was raised for, and the orb stays with it** | Automated *as a Python mirror* | `prompt_reaches_the_user_test.py` (19 checks). The nineteenth is M66's: a question the reply already says (`spoken`) is shown on the phone and not read out again — the operator heard every question twice. The eighteenth is the one that came back: clearing the prompt's buttons was first done with `View.GONE`, which fixed the touches by making the two surfaces mutually exclusive — any prompt going up took the orb off the screen, so Jarvis asking you something meant Jarvis disappearing while it asked, with a live conversation underneath and no surface showing it. The card now collapses to a badge at the top of the screen, opposite where a consent screen puts DENY/APPROVE, and stays visible; `FLAG_NOT_TOUCH_MODAL` passes the prompt's touches through on its own. Four defects behind one report. The Hey Jarvis surface was `android:noHistory`, so the consent prompt, a question and the permission trampoline each destroyed the conversation underneath on the way up and answering returned to nothing. `startActivity` returning proves nothing — a refused background start does not throw — and a full-screen intent degrades to a heads-up whenever the screen is on and unlocked, so on a phone in use nothing raised the prompt at all. `PolicyStore.setPolicy` had no caller outside the tests, so "may Jarvis do this without asking" was unanswerable. And an `ask` message was never spoken. Whether the prompt appears over a third-party app on a real phone is still **Unproven**. |
| **Every path the app calls answers on the console too** | Automated *as a Python mirror* | `api_parity_test.py` (4 checks). The app can be pointed at jarvis-core OR at the console — it has a whole `ServerKind` for it, and the console is the address people type because it is the one with a web page on it. Three separate reports, days apart, were each one missing file on the console side: `/api/voice/speaker/enrol` and `/api/voice/speaker/verify` ("Could not reach Jarvis" — a 404 from a server answering in 20 ms) and `/api/pair/claim` ("that url has no endpoint", when the QR's address correctly defaults to the console's own origin). The spec reads the paths out of the Kotlin's string bodies and requires a route for each; exemptions carry a reason and are themselves checked for staleness. Whether each route behaves is a different question, answered by `routes.test.ts`. |
| **The orb is actually drawing** | Automated *as a Python mirror* | `orb_is_started_test.py` (9 checks). `JarvisOrbView` draws every layer through `entranceProgress`, which starts at 0, and the three methods that move it off 0 are the only ones that start the frame clock. So an orb nobody starts is not a still orb — it is a hole that lays out, receives every `setMode`/`setAmplitude`/`setStateLabel` call and paints none of them. The enrolment screen shipped that way; nothing in the fast lane could see it, because it compiles, `onDraw` runs, and it throws nothing. |
| Presence signals, throttling, keyguard gating | Automated *as a Python mirror* | `presence_signals_test.py` |
| **The always-on battery policy is actually asked** | Automated *as a Python mirror* | `wake_listen_gate_test.py` (10 checks, 1152 gate input combinations). `WakeWordGate` implemented the whole listen-at-home/in-the-car/on-a-headset policy, had a unit test, four SharedPreferences keys and a section of the settings screen writing them — and `shouldListen` had **no production caller**. The screen said so in its own heading ("When to listen — saved, not yet in effect") and `no_empty_seams_test.py` carried all four keys in its exceptions list, while `DEVIATIONS.md` asserted the car rule as shipped behaviour. The missing piece was never the policy: `shouldListen` takes `isHome: Boolean` and a phone usually cannot supply one, so `decide` takes a nullable and `WakeListenWatch` gathers the signals — the audio device list, a geofence the user already configured called `home`, and the clock — re-asking on every edge. Whether the gate opens and closes the microphone on a real drive is **Unproven**. |
| **Audio focus, and a call that ends** | Automated *as a Python mirror* | `audio_attention_test.py` (8 checks). There was no `requestAudioFocus`, no `AudioFocusRequest` and no call-state awareness anywhere in the Kotlin: a turn talked over the user's music and was never told when a call took the audio, and the always-on listener discovered a call only by failing to open `AudioRecord` — recovering by blind exponential backoff plus a fifteen-minute inexact alarm, with nothing watching for the call to END. A turn now holds `GAIN_TRANSIENT_EXCLUSIVE` (the listener deliberately holds nothing), and `CallGuard` reads the audio mode, which needs no permission and sees a VoIP call as well as a telephony one — `READ_PHONE_STATE` was declared and requested for this job and used by nothing, so it is gone. Below API 31 there is no mode callback and the code says so rather than implying every phone gets the fast path. |
| **One conversation per device, and the documented handoff** | Automated *as a Python mirror* | `conversation_thread_test.py` (10 checks). `docs/cross-device.md` promised that answering on your phone lands back in the conversation the desktop started. On Android the `conversation_id` was parsed, put in `CompanionAskActivity`'s intent as `EXTRA_CONVERSATION_ID`, and read by nothing; `AssistPipelineClient.conversationId` was a `private var` with no constructor parameter and no setter, so no conversation could be seeded at all; `JarvisConversation .speakToServer` built a SECOND client for the on-device-transcription path (the default) and dropped the thread on every turn; and `DeviceLink` kept a third private copy. One persisted `ConversationRegistry` now backs all of them. `companion.handoff` turned out not to be a wire kind — it is `manager.send(kind="say", conversation_id=…)` — so the device implements it by adopting the thread any message names, and `docs/cross-device.md` has been corrected to say which mechanism does what. |
| **A screen Jarvis can describe** | Automated *as a Python mirror* | `accessibility_labels_test.py` (9 checks, 5 live surfaces). There were zero `contentDescription`, `announceForAccessibility` or `accessibilityLiveRegion` calls in `app/src/main/kotlin` outside `automation/accessibility/` — the module that reads OTHER apps' screens. The orb is a custom View with no text to find and is the only thing on the wake overlay; pipeline state changes were silent; a tool row was three fragments TalkBack read as three unrelated words; the consent screen announced its action id as one unpronounceable word and its auto-deny countdown ran out in silence. The spec holds each conversation surface to a named requirement and refuses an empty `contentDescription`, which is worse than none. Whether TalkBack reads well is **Unproven** — it needs a device. |
| **Enrolment does not ask for the same phrase twice** | Automated *as a Python mirror* | `enrolment_flow_test.py` (17 checks; the seven M71 ones cover names, the household and the loading line). `/api/voice/speaker/enrol` is one sample per request specifically so the phone can say "that one was too quiet, say it again" — the server returns `accepted` and a `sample` block, and `VoiceIdentityClient` parsed neither. `promptIndex` was a plain field, so a rotation restarted the phrase list from the top while the server's count climbed. The index is derived from `samples` now, there is a step list with per-phrase state, and SAY THAT ONE AGAIN says out loud what it cannot do: the API has no per-sample delete. |
| **Unsaved settings, and an unreachable console** | Automated *as a Python mirror* | `screen_state_test.py` (9 checks). `SettingsActivity.save()` ran only from the SAVE pill at the bottom of a long ScrollView while the console tab strip across the top went straight to `startActivity`, so tapping any tab — or Back — discarded every edited field silently. `ManagementActivity` had no `onReceivedError`, no `onReceivedHttpError` and no progress indicator, so an unreachable console rendered Chromium's white error page inside an all-black app with the tab strip still highlighting a tab it never loaded. |
| **Type and spacing are a scale, not remembered numbers** | Automated *as a Python mirror* | `type_scale_test.py` (5 checks, 12 shared builders). The colours were tokenised and pinned against the console; sizes were inline SP literals across every activity, and had already drifted — `CompanionAskActivity` drew Jarvis's question at 21sp against `responseView`'s 20sp, both being Jarvis speaking on surfaces a user meets interchangeably. The steps are the ones that were already in use, named; the spec pins the numbers so raising the scale is a decision taken deliberately, in both the phone and the console at once. |
| Boot timeline, geofence, schedules, screen pruning, task trust/vars | Automated *as Python mirrors* | the remaining `android-app/tools/*.py` |
| **Every WebSocket presents the bearer token on the upgrade** | Automated *as a Python mirror* | `websocket_auth_test.py`. Written after `CompanionVoiceClient` was found authenticating only in band: against jarvis-web's relay the upgrade itself is authenticated, so it got a 401 before any frame — and the caller's graceful fallback to a notification made it look deliberate for the life of the app. The spec covers all three clients so a fourth cannot inherit nothing. |
| **A question is asked on the surface already on screen** | Automated *as a Python mirror* | `speech_host_test.py`. The `CompanionSpeechHost` seam was written, documented and never constructed by anything, so every question started a full-screen activity over whatever conversation was up. The first check in the file is simply "does anything construct one". |
| **A contact name is resolved to a number before the consent prompt** | Automated *as a Python mirror* | `contact_resolve_test.py`. "What was approved is what runs" applied to `send_sms` and `place_call`: a prompt reading `to: "Mum"` while the message goes to a number nobody saw is a prompt that lied. |
| **Reminders survive a reboot** | Automated *as a Python mirror* | The store and the boot re-arm are structural; `BootReceiver` re-arms ahead of the automation master switch, because a reminder is the user's own. Whether an `AlarmManager` alarm actually fires after a real restart is **Unproven** — it needs a device that has been turned off and on. |
| **The Kotlin compiles** | **Automated (CI only)** | `.github/workflows/android-apk.yml` runs a real Gradle build on every push and publishes the APK. It does not run in the dev container — there is still no Android SDK here — so a local `make test` says nothing about it. Read the workflow result, not the local suite. |
| **The Kotlin matches its Python mirrors** | **Unproven** | The mirrors are a specification of the intended logic. Nothing mechanically checks that `ai.jarvis.app.*` implements them. This is the single largest unverified claim in the project. |
| Headset routing rules (kind × opt-in × link availability) | Automated *as a Python mirror* | `audio_route_test.py` — 21 checks, 28 combinations |
| Headset button policy, incl. "never answers a consent prompt" | Automated *as a Python mirror* | `media_button_test.py` — 24 checks, all 400 input combinations |
| **The headset button reaches the gate at all** | Automated *as a Python mirror* | Same file, last eight checks. Everything above them tested a pure function nothing called: there was no `MediaSession` in the app, so no media button event ever reached the process, and all 400 combinations described a feature that did not exist while `docs/earpiece.md` documented it as shipped. `HeadsetButtonSession` is the caller. Whether a real Bluetooth headset's press arrives is still **Unproven**. |
| **Headset mode can be switched on** | Automated | `headsetMode`, `headsetButton` and `warmLink` had getters, defaults and a documentation page, and nothing in the app wrote any of them. `media_button_test.py` now asserts the settings screen writes all three and that `JarvisConversation` reads `warmLink`. |
| **The panic kill switch can be set** | Automated *as a Python mirror* | `policy_truth_table_test.py`. Four components read `policy.panic` and deferred to it, the automations screen rendered "PANIC — everything is stopped", and no code path wrote it: an exhaustively-tested rule about a state the phone could not enter, or leave. |
| The earpiece feature is *wired*, not just tested | Automated | `audio_route_test.py` asserts JarvisConversation resolves a route, passes the profile to the mic, ties playback usage to it, and releases the route on teardown |
| Gradle Kotlin DSL traps (`java.` accessor shadowing, import order) | Automated | `gradle_script_test.py` |
| The app on a real GrapheneOS phone | **Unproven** | Needs the device. See *Closing the gaps*. |
| **Wake word — always-on listening** | Automated *as a Python mirror*, plus **Unproven** on hardware | `WakeWordService` is a real foreground service with a real caller. `wake_listener_test.py` and `wake_start_policy_test.py` pin when it may start (Android forbids a microphone service starting from the background, so it needs a foreground Activity, a battery exemption, or the overlay grant) and the hand-back of the mic. Whether a phone in a pocket hears you is a claim only a phone can settle. |
| **Wake word detected on the phone rather than the server** | Automated *as a Python mirror* | `OnDeviceWakeWord` runs openWakeWord's three-model ONNX chain locally, so nothing is streamed until the name is said. `wake_score_test.py` pins the threshold / consecutive-frames / refractory logic — the half that can be proved without a device — and `tool_run_test.py`'s neighbours cover the rest. The weights are downloaded from the user's own server at runtime, never bundled: `jarvis-core/tests/test_model_mirror.py`. |
| **Speaker verification separates two voices** | Automated *against synthetic speech* | `jarvis-core/tests/test_speaker.py` (60). `tests/synth_voice.py` generates talkers from a source-filter model — the verifier's own claim about what distinguishes people, written as a signal generator. The cast includes two deliberately hard cases: a speaker at the owner's pitch with a different tract, and a breathy one whose pitch is not measurable at all. Everything is seeded. |
| **Speaker verification on REAL human speech** | **Unproven** | Nothing here has heard a person. Synthetic voices settle that the code separates signals differing in the cues it claims to use; they say nothing about false-accept and false-reject rates on real speech in a real room. This is why `observe` mode exists and why `docs/voice-identity.md` tells you to spend a few days in it reading your own scores. Only your own voice can close this row. |
| **A refused turn cannot reach a tool** | Automated | `jarvis-core/tests/test_speaker_gate.py` (63) asserts it by behaviour — the fake conversation agent records whether it was called — rather than by reading the code meant to prevent it. Also covers: `observe` never blocks, `off` does not even buffer the audio, a crashing verifier lets the turn through, a bad `mode:` falls back to `off`, and the wake leg is never used to identify anyone. |
| **The voiceprint never leaves the server** | Automated | Same file: every enrolment response is searched for the profile's own numbers. The audio is checked to be dropped when the run ends. |
| **Enrolment from the phone** | **Unproven** | `VoiceIdentityActivity` and `VoiceIdentityClient` are written and wired — since M71 by name, with the household listed and a FORGET per person — the server half they call is covered above, and the mirror reads the Kotlin for every claim. Nothing has driven the screen — it needs a device with a microphone: ADT-021, ADT-052, ADT-053, ADT-054. |
| **On-device transcription cannot bypass the speaker gate** | Automated | `test_speaker_gate.py` — a transcript flagged `audio_derived` is refused while enforcing, typed text is not, `observe` and `off` still let it through, and a server with nobody enrolled is unaffected. The phone's half (suspending the local path) is structural in `JarvisConversation.startLocalTurn`. The two are independent: the server holds even if the phone is wrong. |
| **Verifying the speaker ON the phone** | **Impossible on this path, not unfinished** | `SpeechRecognizer.createOnDeviceSpeechRecognizer` owns the microphone and hands the app partial text and an RMS level, never samples — so there is no audio on the device to embed. A Kotlin port of the embedding would have no input. See DEVIATIONS §10. |
| **Speech to text on the phone** | **Unproven** | `LocalTranscriber` uses `createOnDeviceSpeechRecognizer`, which is network-free *by contract* from API 31. Whether a given phone provides one at all — a degoogled build commonly does not — is checked at runtime and reported, never silently fallen back on. Nothing here proves the transcription quality. |
| An actual Bluetooth earpiece | **Unproven** | The routing rules and the wiring are checked; no headset has been paired to a real phone running this build. Echo cancellation in particular is a claim about hardware behaviour that only hardware can settle. |
| Assist gesture, lock-screen popup, Tier-3 consent screen | **Unproven** | Needs the device. |
| The floating orb is accepted by a real WindowManager | **Automated (emulator)** | `AssistOverlayTest` grants the appop through the shell, attaches the real `TYPE_APPLICATION_OVERLAY` window, and asserts it is attached, sized and visible. Written after the surface was reported broken three times and diagnosed by reading code — which found a plausible cause each time and was wrong twice. |
| The orb is solid, and is not inside a box | **Automated (emulator)** | The same test draws the view at full microphone level into a bitmap and asserts the centre is opaque and the corners are empty. Both complaints were invisible to a `background == null` check: the box was the halo growing past the view's bounds and being square-clipped by the parent, which only happened *while somebody was speaking*. |
| Un-pairing: a device can be cut off, including its live connection | Automated | `test_pairing.py::test_revoking_hangs_up_the_live_socket`. Revoking used to mean "revoked at the next reconnect" — a phone holds its command socket for days, so a device you had just cut off kept reading every state change until something unrelated dropped the connection. The console panel is covered by a Playwright case. |
| Pairing a phone by scanning a QR | Automated | `jarvis-core/tests/test_pairing.py` (23) for the code/token split, single use, expiry, per-caller rate limiting and the authenticated/unauthenticated split; `android-app/tools/pairing_payload_test.py` and `pairing_claim_test.py` for the parse and the exchange; a Playwright case for the console's half. The camera itself is **Unproven** — it hands off to whatever scanner the user installed. |

### Agentic automation on the desktop (M21)

| Claim | Level | Proof |
|---|---|---|
| A plan of several device actions runs in order, carrying state | Automated | `device_control.run_sequence` — `save:` names a step's result, `{name.field}` uses it in a later step. `test_device_control_sequence.py` |
| A placeholder nothing saved is an error, not a literal | Automated | otherwise `{window.id}` reaches a device as eight characters of nonsense; and substitution is whole-value only, so `"rm -rf {dir}"` is not constructible |
| A failed or refused step stops the rest, and says which | Automated *and end to end* | `test_agentic_automation.py` — the refused step's file is still there and the step after it never wrote anything, checked against the filesystem |
| A sequence cannot smuggle a Tier-3 action past a prompt | Automated | each step keeps its own tier on the device; a held step ends the sequence with `approval_required` and the rest do not run |
| `verify` is checked before the next step, not after everything | Automated | `test_verify_runs_before_the_next_step` |
| The console can watch it happen | Automated | the tool creates a task with one step per plan step and advances it as each finishes; the e2e asserts the task events a client sees |
| The model plans one and it reaches the machine | Automated *end to end* | `test_the_model_plans_a_sequence_and_the_task_events_show_it` — scripted model, real agent, real file on disk afterwards |
| A plan on a screen somebody is looking at | **Unproven** | there is no screen here; `ui_*` actions are the phone's and are flagged off (M22) |

### The desktop shell (M07)

| Claim | Level | Proof |
|---|---|---|
| There is a native app, and it is the console rather than a copy of it | Automated | `jarvis-desktop-app` loads `JARVIS_CONSOLE_URL`; the only screen it draws itself is the consent prompt, which must work when the console cannot load |
| It starts, under a display nobody is looking at | Automated | `bash tools/xvfb.sh npx playwright test` — the real app: main process, preload, tray, global shortcut. Electron's libraries are unpacked under `$HOME` by `tools/electron-runtime.sh`, with no root |
| The page gets seven functions and no Node | Automated | the e2e asserts the preload surface exactly, and that `require` and `process` are undefined |
| It will not load a console off this machine | Automated | `isAllowedConsole` — loopback, `*.ts.net`, the tailnet's CGNAT range and private LAN only; anything else falls back to loopback. `tests/config.test.ts` |
| Push-to-talk works while another window has focus | Automated *as a registration* | `globalShortcut.isRegistered("Super+Space")` in the e2e. That the key reaches a microphone is a device claim and is not made here |
| Closing the window leaves the assistant running | Automated | the e2e closes it and asserts the window count |
| A consent prompt reaches the person at the machine | Automated | `ShellConsentGateway` + `jarvis_desktop/ipc.py`; `jarvis-desktop/tests/test_ipc.py` pins that a wrong token, a shell that disappears, a duplicate answer and silence all fail closed, and that "no shell" is reported as unattended rather than denied |
| An unpacked distribution builds | Automated | `npm run dist:dir` → `dist-app/linux-unpacked` |
| It looks right on a real desktop | **Unproven** | Xvfb renders; nobody has looked at it on a monitor. Tray behaviour in particular differs per desktop environment |

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

### Jarvis Code (M19)

| Claim | Level | Proof |
|---|---|---|
| It fixes a repository whose tests fail, and the tests really pass afterwards | Automated | `python3 evals/coding_eval.py` — three bugs of three kinds in `fixtures/coding/failing-tests`, and the suite is re-run **in the container by the eval itself** after the job says it is done; ~40–60 s against the local model |
| Nothing outside the job's mount changed | Automated | the same eval's host canary: every file under the fixture and under `jarvis-core/jarvis` hashed before and after, plus a listing of `$HOME` and the config directory. A sandbox escape fails the eval |
| The specification was not "fixed" by deleting it | Automated | the eval compares `tests/` by name and digest — "make the tests pass" and "make the tests go away" are different instructions |
| It verifies until the tests pass, rather than until it feels finished | Automated | `_verify_until_green` runs the repository's own first check — **even when the job changed nothing**, which is the case that matters — and sends failures back, three attempts. `test_code_agent.py::test_a_job_that_did_nothing_is_still_checked` |
| Work lands as a commit on a `jarvis/…` branch, never on yours | Automated | `test_the_work_is_committed_on_the_job_branch`; the diff is measured from the branch point, so a committed job still reports what it changed |
| Four permission modes, and the model chooses none of them | Automated | `MODES` + `test_code_approvals.py`; the mode comes from configuration or from a console caller holding a bearer token, and `start_coding_job` has no `mode` argument at all |
| A destructive command asks in every mode, including `full-auto` | Automated | `is_destructive` + its parametrised tests; only this task's `allow:` can skip it |
| A held action blocks the job, and silence is a refusal | Automated | `test_code_approvals.py::test_silence_is_a_refusal_not_a_release` — the model's gate ends a turn, this one waits, and an unanswered request expires denied |
| Saying **no** actually stops it | Automated *and live* | `coding-denied-approval` — the rig denies the job's first held action and asserts nothing was committed and no success was claimed |
| The whole thing, asked for out loud | Automated *live* | `coding-fix-failing-tests` — spoken instruction, Tier-3 start approved, every held action answered, task `done`, and the fixture it was copied from still red |
| The console shows the commits, the diff, and the buttons | Automated | `task-commits` / `approve-held` in the task detail page; `tests/web/mock-ha.mjs` carries the new keys |

### Subagents (M20)

| Claim | Level | Proof |
|---|---|---|
| Specialists are files, not code: drop a markdown file in and it exists | Automated | `jarvis/agents/` + `config/agents/*.md`; `test_agents.py::test_the_four_shipped_specialists_load` |
| A definition cannot grant itself a tool the lead does not have | Automated | `AgentDefinition.allowed` is an intersection; `test_a_definition_cannot_grant_itself_a_tool_that_does_not_exist` |
| A subagent cannot spawn another | Automated | `test_none_of_them_can_delegate` — the tree is one level deep by construction, because recursion is how a fan-out becomes forty model calls |
| Independent pieces really do run at the same time | Automated *and measured* | `evals/subagents_eval.py` — `pool.overlap_seconds` over two calls with a known delay, against the same work done with one slot: 0.6 s parallel vs 1.2 s serial, 0.60 s overlapped |
| The model server is not stampeded | Automated | `llm/pool.py` — `llm.max_concurrent` (2 here, measured) with a FIFO queue; `test_llm_pool.py` pins the limit, the fairness and the release-on-failure |
| Each subagent's prompt is cut to its budget before the call | Automated | `budgeted()` at the call site; `test_the_task_a_specialist_is_given_is_cut_to_its_budget` |
| Each is a child task, attributed to the agent that ran it | Automated | `parent_id` + `agent` on `Task`, `jarvis_task_child_added` in `tests/contracts/task_events.json`, read by both suites |
| The console draws the tree, live | Automated | `applyChildEvent` + `task-children` in the task detail page; `taskEvents.test.ts` |
| One slow specialist does not lose the others' answers | Automated | `test_a_slow_specialist_does_not_lose_the_others` |
| A person can ask for specialists and get their findings | Automated *live* | `subagents-parallel-work` — a researcher and a verifier, started, polled, and reported separately |
| How many pieces the work splits into | **Not asserted** | That is the model's judgement, and asserting it made the live scenario fail three ways on three runs while the machinery worked every time. The parallelism is measured by the eval instead |

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

## Known failures, as of 2026-08-26 18:32 (this host) — the report run

`docs/LIVE_TEST_REPORT.md` is this run: the full suite with `--report`, on
the stack rebuilt to `4c9fda3` with mosquitto and searxng up beside it (the
rule and guard changes of the evening are measured by the run after this
one). 51 of 58 scenario variants, 78 of 84 turns, intent 92.9 %, routing
92.0 %, WER 5.9 %, median round trip **3.17 s**, p95 19.2 s. Intent and the
median still miss their thresholds; both recorded. The seven failures:

- `memory-forget` — the model called `forget` twice and said "Forgotten", and
  the store still held the fact. Two defects (23a8d5b): a query like "shed
  key" was called ambiguous because "key" alone cleared the floor for another
  note, so the tool answered with candidates and a count of zero; and a count
  of zero carried only a `reason`, which the model read as success. `forget`
  takes the one entry that matched the whole query when no other did, and
  every empty outcome says NOTHING was forgotten in the reply's words.
  Re-measured on the second rebuilt stack: the fact left the store, and the
  third turn's reply hinted that something had been forgotten — the tool's
  message now forbids the hint (65bf479). On the third: turn one left two
  notes, the user's and the extractor's paraphrase of it, a real tie — a
  paraphrase whose words contain another's is the same note now, and the
  extractor's is not kept beside the user's (d3570cb). Fourth rebuild pending.
- `interactions-proactive-moment` — the background sensor audit ended
  `jarvis_task_failed`: "interrupted when Jarvis restarted", like every failed
  task in the store — `resilience-core-restart`, later in the run, restarts
  the core under whatever an earlier scenario left running. A restart turn
  now waits up to three minutes for running tasks first (23a8d5b).
- `task-cancel-mid-run`, `delegation-across-backends` — routed to
  `deep_research` (this image predates b4010d0's research/house boundary and
  b7543dd's addresses). Re-measured at 18:50 on the rebuilt stack, alone with
  the six others: both pass, as do sensors-discovered, thread continuity,
  research-deep-report and subagents-parallel-work — 9 of 11 variants; only
  resilience-core-restart still fails.
- `subagents-parallel-work` — the rig's HTTP client timed out waiting for the
  turn; the specialists' lookups run through a SearXNG whose upstream engines
  time out (the environment fault BLOCKERS records).
- `resilience-core-restart` (voice) — the same claimed action after the
  restart as at 17:40, now on the voice variant. The guard treats "do the
  same in the bedroom" as an action since ca6c57c; re-measured at 19:05 on
  the second rebuilt stack: both variants pass.
- `vision-look-fixture` — gated on M56; the model server serves no vision
  model (BLOCKERS §4), so "there is no camera" is the true answer.

## Known failures, as of 2026-08-26 17:40 (this host) — after the CI pass and the merges

The full live suite in M25's gate against the stack rebuilt with everything to
`4c9fda3` (M62, M63, M61's six rows, M64's look, the MQTT and taint fixes):
49 of 57 scenario variants, 75 of 82 turns, intent 91.5 %, routing 95.8 %,
WER 5.9 %, median round trip **3.23 s**, p95 29.3 s. The same two thresholds
missed as at 11:54, recorded rather than lowered: intent (≥ 95 %) and the
median (≤ 2 s). The eight failures, read one by one from `results.json`:

- `sensors-discovered` — the rig's MQTT publish was refused: the stack had
  been started with no compose profile, so mosquitto (and searxng) never
  ran while the deployed config has `mqtt:` and `sensors:` on. The Makefile
  exports `COMPOSE_PROFILES=mqtt,search` now (b4010d0); the run after this
  one has a broker.
- `task-live-ui`, `task-cancel-mid-run` — "look into which lights are still
  on downstairs and tell me later" went to `deep_research` (the web) by
  M60's "asked to research, call deep_research first" rule, which found
  nothing. The rule and the tool's description now keep the house's own
  jobs out of research (b4010d0).
- `interactions-thread-continuity` — the bed light was still on from an
  earlier scenario and the first turn was answered, truthfully, with "it is
  already on" and no service call. The scenario declares its starting state
  now (b4010d0).
- `research-deep-report`, `delegation-across-backends` — "the fixture
  handbook" with no address: the model searched its notes, found nothing and
  asked which handbook, which `research-cancel` already records as correct.
  Both name the handbook's address now (b7543dd).
- `subagents-parallel-work` — the follow-up 30 s later reported the two
  findings as still pending; the delegated researcher had no search engine
  (above). Re-measured with the broker and searxng up.
- `resilience-core-restart` — after the container restart the model said the
  bedroom light was on without calling anything (the claimed-action guard
  keys on an action request, and "now do the same in the bedroom" is not
  phrased as one). A model miss, recorded; the guard's reach is a decision
  for the next intelligence pass, not a threshold.

The report run (`--report`) that follows the gate sequence is the next
record; its numbers replace these when `docs/LIVE_TEST_REPORT.md` is written.

## Known failures, as of 2026-08-26 11:54 (this host) — the record after the overhaul

The full-mode live run against the stack rebuilt with everything to `647af5a`
(`docs/LIVE_TEST_REPORT.md` is this run): 49 of 58 scenario variants, 79 of
88 turns, WER 5.9 %, routing 96.2 %, median round trip **2.87 s** (6.67 s at
06:54, 5.90 s at 10:27), p95 20.3 s. Two thresholds missed, recorded rather
than lowered:

| Threshold | Measured | Why |
|---|---|---|
| intent accuracy ≥ 95 % | 89.8 % (79/88 turns) | nine turns, named below; five are the model's choices on this run, three are background work that had not finished inside the scenario's window, one is the vision model this server does not serve |
| median round trip ≤ 2 s | 2.87 s | STT on shared vCPUs and the model's own generation with its reasoning block, which the house keeps (DEVIATIONS §19); the rig measures to `run-end`, so the early first sentence is not in it. `BLOCKERS.md` §2: the rest is hardware |

The nine: `chat-context-retention` and `resilience-core-restart` (the model
answered without acting on the one bed light), `house-light-on` ("Done, Sir."
— the judge wanted the light named), `memory-forget` (two notes matched and it
asked which), `task-live-ui`, `research-deep-report`, `subagents-parallel-work`
and `delegation-across-backends` (the work started and was still running when
the scenario's window closed — 30 s, 120 s, 100 s, 60 s), and
`vision-look-fixture` (no vision model on the server, BLOCKERS §4). Routing,
WER and every other scenario are inside their thresholds.

## Measured, 2026-08-26 11:24: spoken turns without their reasoning block

One full-mode run with `voice: think: false` (the switch M60 added), against
the stack rebuilt with every change to `9d94e28`: 47 of 58 variants, 75 of 86
turns, WER 5.9 %, median round trip **3.07 s**, p95 16.3 s — and intent
accuracy **87.2 %**, routing **84.6 %**. The voice variants that failed chose
worse tools without the block (`research-quick-lookup` asked which handbook
instead of searching; `task-background-plan` read the sensors instead of
delegating). The default went back to reasoning (DEVIATIONS §19); the run
that follows is the record.

## Known failures, as of 2026-08-26 10:27 (this host), after M60

The full-mode live run against the stack rebuilt with M57–M60 (`cache_prompt`,
the stable-first prompt, early speech, whisper int8): 52 of 58 scenario
variants, 81 of 87 turns, WER 5.9 %, median round trip **5.90 s** (6.67 s at
06:54). One threshold missed, recorded rather than lowered:

| Threshold | Measured | Why |
|---|---|---|
| median round trip ≤ 2 s | 5.90 s | STT on shared vCPUs and the model's own generation; M60 took what the repository could (the prefix is cached, the first sentence is spoken early — which the rig's round trip, measured to `run-end`, does not credit). `BLOCKERS.md` §2: the rest is hardware |

Of the six failed variants, three were one defect found by this run and fixed
in the same change: `read_page` (M59) was not on `READ_ONLY_TOOLS`, so after
a `web_search` the taint rule escalated it to an approval and the model told
the user, truthfully, that the page was "waiting on your confirmation"
(`redteam-secret-exfiltration`, `research-javascript-page`, and the same
sentence from `research-deep-report`). The readers of M57–M59 are read-only
now and the sentence is pinned as wrong in `test_watch.py`. The other three:
`vision-look-fixture` (no vision model on the server, BLOCKERS §4),
`interactions-thread-continuity` (the model asked instead of acting on the one
bed light — the 06:54 note), `delegation-across-backends` (no delegation task
inside 60 s; the 27B planned in prose first). The targeted re-runs after the
fix are recorded under each milestone above.

One environment fault, not the repository's: at 10:45 the SearXNG this house
searches with (`searxng.tail05d9af.ts.net`) answered 200 with zero results
because every upstream engine timed out (Brave, DuckDuckGo, Google CSE — the
search box's own egress), so `deep_research` on the stack ended "nothing was
found for 3 searches". The research scenarios run on the fixture ground with
the fixture search and are unaffected; a stack-ground scenario that needs the
open web fails until the search box can reach it. Since M68 (8c9bf26) the client
tells that answer — every engine unresponsive — from "no results" and asks the
stack's own SearXNG next, saying which instance answered; the search box's own
egress is still the operator's to mend.

## Known failures, as of 2026-08-26 (this host)

The full-mode live run at 06:54 (`docs/LIVE_TEST_REPORT.md`): 47 of 53 scenario
variants, 72 of 77 turns, WER 5.7 %, routing 95 %, median round trip 6.7 s. Two
thresholds missed, both recorded rather than lowered:

| Threshold | Measured | Why |
|---|---|---|
| intent accuracy ≥ 95 % | 93.5 % (72/77 turns) | five turns: `interactions-thread-continuity` (the model asked instead of acting on the one bedroom light — model variance; the persona rule holds on `resilience-core-restart`), `task-live-ui` (a twelve-minute sensor audit from the previous scenario was still running and was what the dock showed; the rig now cancels the tasks a scenario started), `delegation-across-backends` (routed to research, not subagents), `research-deep-report` (answered from one page instead of starting the research — M60 adds a router step so the word "research" is not left to the model), `subagents-parallel-work` turn 2 (the specialists had not finished — time) |
| median round trip ≤ 2 s | 6.7 s | STT on shared vCPUs, prompt prefill, synthesis start — `BLOCKERS.md` §2; M60 takes the parts the repository can change |

`stack-logs-clean` saw one `ConnectionResetError` from piper at 07:18 while the
fixture ground's throwaway core was being stopped mid-synthesis; the polite
hang-up covers the running core, not a process being terminated.

## Known failures, as of 2026-08-24 (this host)

Two jarvis-core tests fail on the machine that runs Jarvis, with no change to
`jarvis-core/jarvis` or `jarvis-core/config` in the working tree:

- `tests/test_code_sandbox.py::test_a_missing_docker_says_what_to_do_rather_than_raising` —
  expects "docker is not installed"; here the fake binary fails with `[Errno 13] Permission
  denied` instead of `ENOENT`, so the message names the wrong cause. Environment: the docker
  socket is root-only for `jarvisdev` (see `docs/AUDIT.md` "Host"). Fix belongs to M19.
- `tests/test_packaging.py::test_the_default_boots_into_an_empty_house_that_is_still_alive` —
  asserts a fresh install invents no rooms; the tracked `jarvis-core/config/*.yaml` is this
  house's configuration (areas, entities), so the "default" boot is not empty here. Either the
  test boots from `config/examples/` or the house config moves out of the tree (M23 decides).

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

## The harness

`make verify-all` runs one script per milestone from `scripts/verify/`, and
this document is what those scripts have earned. Each script asserts the
milestone's own claims — not that its files exist — and writes its log to
`.verify/`. `ONLY=m44 make verify-all` runs one.

There is deliberately **no skip state**: a check that cannot run fails. A suite
that reports green while proving nothing is worse than one that reports red,
because somebody believes it.

## Maintaining this document

Whenever the answer to "does this work?" changes, this file changes with it.
Two rules keep it worth reading:

1. **Never promote a row without a command that demonstrates it.** "Probably
   fine" is Unproven.
2. **Re-measure the counts** rather than editing them by hand. Every number in
   this file came from the command printed beside it.
