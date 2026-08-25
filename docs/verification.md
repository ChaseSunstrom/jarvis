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
| No cloud search fallback exists anywhere | Automated | `scripts/verify/m18-research.sh` greps for one; `web.search` fails saying SearXNG is not configured |
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
| The orb palette on all three surfaces equals `color.orb.*` | Automated | the same command (drift check over `SiriPalette.kt` and `Orb.svelte`) + `python3 android-app/tools/reactor_orb_test.py` |
| No new hard-coded colour/spacing/type/motion value in web, Android or desktop app code; legacy counts only fall | Automated | `python3 scripts/verify/token_lint.py` (ratchet: `design/token-lint.baseline.json`, 340 legacy hits in 38 files on 2026-08-24, 4 documented exceptions) |
| Phone, desktop and console draw one palette, every text colour AA on its ground | Automated | `python3 android-app/tools/design_token_test.py` · `cd jarvis-desktop && python3 -m pytest tests/test_theme.py -q` · `cd jarvis-web && npx vitest run src/lib/tokens.test.ts` — all three read `design/tokens.json` |
| `/styleguide` renders every token group and the four screen states, headless | Automated | `cd jarvis-web && E2E_PORT=8299 npx playwright test e2e/styleguide.spec.ts` (screenshot under `.verify/styleguide.png`) |
| **The Kotlin builds** | Automated | `./gradlew assembleDebug` — a JDK 17 and the SDK under `$HOME` (`android-app/tools/bootstrap-toolchain.sh`), the wrapper committed, `app-debug.apk` produced. The first time this repository has built its own Android app |
| The Compose theme (`JarvisTheme.kt`) compiles | Automated | it is compiled by the build above, and `the generated theme` screenshot renders it |
| **178 JVM unit tests** | Automated | `./gradlew testDebugUnitTest` |
| **Lint is blocking, and clean** | Automated | `./gradlew lintDebug` with `abortOnError = true`. It found three real crashes-on-Android-10 while it was "reported, not enforced": two `AudioManager.OnModeChangedListener` calls and a `createOnDeviceSpeechRecognizer`, each requiring API 31 with `minSdk = 29` |
| **Six screens, rendered and compared** | Automated | Robolectric + Roborazzi on the JVM: the orb listening and thinking, the component sheet, the approval banner, the task overlay, the generated theme. `./gradlew verifyRoborazziDebug` fails on a difference; the goldens are PNGs in the repository |
| No hard-coded colour, size or type value left in the app's Kotlin | Automated | `python3 scripts/verify/token_lint.py --require-clean android-app/app/src/main/kotlin` — 132 hits to zero, which needed two new spacing steps, a `Size` scale and thirteen derived alpha constants in `design/tokens.json` |
| The whole design-system gate | Automated | `bash scripts/verify/m01-design-tokens.sh` — 46 checks, measured 2026-08-24 |

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
| Action table (48 actions) | Automated *as a Python mirror* | `action_table_test.py` |
| Device-channel protocol, host and URL rules | Automated *as a Python mirror* | `channel_protocol_test.py` |
| Command dispatch (1152 modelled dispatches) | Automated *as a Python mirror* | `dispatch_spec_test.py` |
| **Nothing is written, documented, tested and never called** | Automated *as a Python mirror* | `no_empty_seams_test.py`. The general form of six separate bugs found in one week — `CompanionSpeechHost`, `MediaButtonGate`, the three headset settings, `PolicyStore.panic`, the install-result broadcast, nine permissions — every one of which was made entirely of correct code that nothing reached. It checks global slots for a filler, settings for both a writer and a reader, and every module with an executable spec for a caller; the seven mutations in its own history are each caught. It is a static reader and says so: it cannot see reflection, and a name in a comment is not a caller, which is why every check strips comments first. Running it found a seventh — `AutomationBridge.uiAutomation`, filled by the accessibility service since the day it was written and read by nothing. |
| **The in-app updater can actually install** | Automated *as a Python mirror* | `updater_install_test.py` (12 checks). `PackageInstaller.commit()` shows nothing — it sends `STATUS_PENDING_USER_ACTION` to an `IntentSender`, carrying the system's install activity, and something has to start it. Nothing received that broadcast, so every update downloaded, committed, and installed nothing, while Settings printed "confirm the system prompt". Whether a real APK installs over a real phone is still **Unproven**. |
| **Every dangerous permission is actually requested** | Automated *as a Python mirror* | `runtime_permissions_test.py` (29 checks, 16 permissions). The manifest promised a runtime request "at the moment it is first needed" and nothing outside `RECORD_AUDIO` and `POST_NOTIFICATIONS` ever made one — `requestPermissions` is a method on `Activity` and every command arrives in a Service, so SMS, calls, contacts, calendar, location and step count were declared, checked for, denied and never asked for. The spec holds the manifest and `RuntimePermissions.ALL` against each other, and holds both against the checklist. |
| **A prompt reaches the person it was raised for, and the orb stays with it** | Automated *as a Python mirror* | `prompt_reaches_the_user_test.py` (18 checks). The eighteenth is the one that came back: clearing the prompt's buttons was first done with `View.GONE`, which fixed the touches by making the two surfaces mutually exclusive — any prompt going up took the orb off the screen, so Jarvis asking you something meant Jarvis disappearing while it asked, with a live conversation underneath and no surface showing it. The card now collapses to a badge at the top of the screen, opposite where a consent screen puts DENY/APPROVE, and stays visible; `FLAG_NOT_TOUCH_MODAL` passes the prompt's touches through on its own. Four defects behind one report. The Hey Jarvis surface was `android:noHistory`, so the consent prompt, a question and the permission trampoline each destroyed the conversation underneath on the way up and answering returned to nothing. `startActivity` returning proves nothing — a refused background start does not throw — and a full-screen intent degrades to a heads-up whenever the screen is on and unlocked, so on a phone in use nothing raised the prompt at all. `PolicyStore.setPolicy` had no caller outside the tests, so "may Jarvis do this without asking" was unanswerable. And an `ask` message was never spoken. Whether the prompt appears over a third-party app on a real phone is still **Unproven**. |
| **Every path the app calls answers on the console too** | Automated *as a Python mirror* | `api_parity_test.py` (4 checks). The app can be pointed at jarvis-core OR at the console — it has a whole `ServerKind` for it, and the console is the address people type because it is the one with a web page on it. Three separate reports, days apart, were each one missing file on the console side: `/api/voice/speaker/enrol` and `/api/voice/speaker/verify` ("Could not reach Jarvis" — a 404 from a server answering in 20 ms) and `/api/pair/claim` ("that url has no endpoint", when the QR's address correctly defaults to the console's own origin). The spec reads the paths out of the Kotlin's string bodies and requires a route for each; exemptions carry a reason and are themselves checked for staleness. Whether each route behaves is a different question, answered by `routes.test.ts`. |
| **The orb is actually drawing** | Automated *as a Python mirror* | `orb_is_started_test.py` (9 checks). `JarvisOrbView` draws every layer through `entranceProgress`, which starts at 0, and the three methods that move it off 0 are the only ones that start the frame clock. So an orb nobody starts is not a still orb — it is a hole that lays out, receives every `setMode`/`setAmplitude`/`setStateLabel` call and paints none of them. The enrolment screen shipped that way; nothing in the fast lane could see it, because it compiles, `onDraw` runs, and it throws nothing. |
| Presence signals, throttling, keyguard gating | Automated *as a Python mirror* | `presence_signals_test.py` |
| **The always-on battery policy is actually asked** | Automated *as a Python mirror* | `wake_listen_gate_test.py` (10 checks, 1152 gate input combinations). `WakeWordGate` implemented the whole listen-at-home/in-the-car/on-a-headset policy, had a unit test, four SharedPreferences keys and a section of the settings screen writing them — and `shouldListen` had **no production caller**. The screen said so in its own heading ("When to listen — saved, not yet in effect") and `no_empty_seams_test.py` carried all four keys in its exceptions list, while `DEVIATIONS.md` asserted the car rule as shipped behaviour. The missing piece was never the policy: `shouldListen` takes `isHome: Boolean` and a phone usually cannot supply one, so `decide` takes a nullable and `WakeListenWatch` gathers the signals — the audio device list, a geofence the user already configured called `home`, and the clock — re-asking on every edge. Whether the gate opens and closes the microphone on a real drive is **Unproven**. |
| **Audio focus, and a call that ends** | Automated *as a Python mirror* | `audio_attention_test.py` (8 checks). There was no `requestAudioFocus`, no `AudioFocusRequest` and no call-state awareness anywhere in the Kotlin: a turn talked over the user's music and was never told when a call took the audio, and the always-on listener discovered a call only by failing to open `AudioRecord` — recovering by blind exponential backoff plus a fifteen-minute inexact alarm, with nothing watching for the call to END. A turn now holds `GAIN_TRANSIENT_EXCLUSIVE` (the listener deliberately holds nothing), and `CallGuard` reads the audio mode, which needs no permission and sees a VoIP call as well as a telephony one — `READ_PHONE_STATE` was declared and requested for this job and used by nothing, so it is gone. Below API 31 there is no mode callback and the code says so rather than implying every phone gets the fast path. |
| **One conversation per device, and the documented handoff** | Automated *as a Python mirror* | `conversation_thread_test.py` (10 checks). `docs/cross-device.md` promised that answering on your phone lands back in the conversation the desktop started. On Android the `conversation_id` was parsed, put in `CompanionAskActivity`'s intent as `EXTRA_CONVERSATION_ID`, and read by nothing; `AssistPipelineClient.conversationId` was a `private var` with no constructor parameter and no setter, so no conversation could be seeded at all; `JarvisConversation .speakToServer` built a SECOND client for the on-device-transcription path (the default) and dropped the thread on every turn; and `DeviceLink` kept a third private copy. One persisted `ConversationRegistry` now backs all of them. `companion.handoff` turned out not to be a wire kind — it is `manager.send(kind="say", conversation_id=…)` — so the device implements it by adopting the thread any message names, and `docs/cross-device.md` has been corrected to say which mechanism does what. |
| **A screen Jarvis can describe** | Automated *as a Python mirror* | `accessibility_labels_test.py` (9 checks, 5 live surfaces). There were zero `contentDescription`, `announceForAccessibility` or `accessibilityLiveRegion` calls in `app/src/main/kotlin` outside `automation/accessibility/` — the module that reads OTHER apps' screens. The orb is a custom View with no text to find and is the only thing on the wake overlay; pipeline state changes were silent; a tool row was three fragments TalkBack read as three unrelated words; the consent screen announced its action id as one unpronounceable word and its auto-deny countdown ran out in silence. The spec holds each conversation surface to a named requirement and refuses an empty `contentDescription`, which is worse than none. Whether TalkBack reads well is **Unproven** — it needs a device. |
| **Enrolment does not ask for the same phrase twice** | Automated *as a Python mirror* | `enrolment_flow_test.py` (10 checks). `/api/voice/speaker/enrol` is one sample per request specifically so the phone can say "that one was too quiet, say it again" — the server returns `accepted` and a `sample` block, and `VoiceIdentityClient` parsed neither. `promptIndex` was a plain field, so a rotation restarted the phrase list from the top while the server's count climbed. The index is derived from `samples` now, there is a step list with per-phrase state, and SAY THAT ONE AGAIN says out loud what it cannot do: the API has no per-sample delete. |
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

## Maintaining this document

Whenever the answer to "does this work?" changes, this file changes with it.
Two rules keep it worth reading:

1. **Never promote a row without a command that demonstrates it.** "Probably
   fine" is Unproven.
2. **Re-measure the counts** rather than editing them by hand. Every number in
   this file came from the command printed beside it.
