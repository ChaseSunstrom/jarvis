# Jarvis

A fully self-hosted, private, cinematic AI assistant. It runs the house, holds
a conversation, and does it entirely on hardware you own — STT, TTS, wake
word, geocoding, search and the model are all containers on the same machine.
Nothing goes to the cloud at runtime.

`jarvis-core` is the hub: automation engine, entity registry, voice pipeline
and a local 8B model wearing a dry-witted British-butler persona. Everything
else is a client or a service it calls.

```
Browser HUD (SvelteKit) ─┐
Android app (Kotlin)     ─┼─► jarvis-core ─► Ollama qwen3:8b (persona + tools)
Desktop agent (Python)   ─┘        │
                                   ├─► entities, automations, scenes, scripts
                                   ├─► get_user_context · run_background_task
                                   ├─► web_search / web_fetch ─► SearXNG, jarvis-browser
                                   ├─► delegate_to_agents ─► jarvis-orchestrator
                                   └─► execute_command    ─► jarvis-sandbox (network: none)
                            Wyoming: whisper 10300 · Piper 10200 · openWakeWord 10400
```

Jarvis used to be a layer on top of Home Assistant. It is not any more —
`jarvis-core` replaced it, and the HA-era pieces have been removed. If you are
looking for something that used to be here, [`docs/removed.md`](docs/removed.md)
says where it went.

## The components

| dir | what | tests |
|---|---|---|
| [`jarvis-core/`](jarvis-core/) | the assistant: state machine, event bus, service registry, automations, MQTT discovery, voice pipeline, LLM agent, tool registry, approval gate | 1203 |
| [`jarvis-desktop/`](jarvis-desktop/) | desktop agent for Linux/macOS/Windows; device-side policy enforcement | 722 |
| [`jarvis-browser/`](jarvis-browser/) | fetching, crawling and gated browser automation | 328 |
| [`jarvis-web/`](jarvis-web/) | SvelteKit HUD — WebGL orb, mic capture, streaming, barge-in; the token stays server-side | 325 + 44 e2e |
| [`android-app/`](android-app/) | standalone Android app (`ai.jarvis.app`): ASSIST role, lock-screen activation, wake word | executable specs in `tools/` |
| [`jarvis-orchestrator/`](jarvis-orchestrator/) | FastAPI: agent fan-out, OpenCode coding jobs, the approval-gated command broker | 17 |
| [`jarvis-sandbox/`](jarvis-sandbox/) | network-less execution jail | 6 |
| [`evals/`](evals/) | routing table and its mirrors, persona eval, decomposition ship/no-ship gate | 17 |
| [`scripts/`](scripts/) | firewall, egress audit, e2e smoke, audio pipeline smoke, adb assistant role | — |
| [`tests/web/`](tests/web/) | mock backend + Playwright e2e the HUD runs against | — |

## Quick start

Two compose stacks. `jarvis-core` is the assistant and comes first; the root
stack adds the HUD and, optionally, the orchestrator and sandbox.

```bash
# 1. the assistant
cd jarvis-core
cp config/secrets.yaml.example config/secrets.yaml
docker compose up -d
docker compose logs -f jarvis-core     # the first-run token is printed here
```

Copy that token — it is stored as a SHA-256 digest and never shown again.

```bash
# 2. the HUD
cd ..
cp .env.example .env                   # put the token in JARVIS_TOKEN
docker compose up -d
```

The orchestrator and the sandbox are a separate opt-in, because a command
broker that starts by default is not optional:

```bash
docker compose --profile agents up -d  # adds the orchestrator and the sandbox
```

The HUD is then at `http://<server>:8199` and jarvis-core's own API at
`:8080`, both over WireGuard/LAN only. For the phone, see
[`android-app/README.md`](android-app/README.md).

`ORCHESTRATOR_TOKEN` and `APPROVAL_SECRET` must be **different values** if you
enable the orchestrator — that split is what stops the API token alone from
being able to run a command.

## Tests

Every suite runs offline: no network, no hardware, no camera, no model.

```bash
make test                  # every python suite + the routing eval
make test-core             # just jarvis-core (the big one)
make test-web              # HUD build + unit + smoke + Playwright
make help                  # everything else
```

Against real hardware, once it exists:

```bash
make smoke                 # boots a throwaway jarvis-core, drives its real APIs
make pipeline-smoke        # full stt->tts audio round trip through Wyoming
make egress-audit          # proves the sandbox really has no network
```

What is proven by which test, and what is still unproven, is in
[`docs/verification.md`](docs/verification.md).

## Security

Untrusted web pages, camera frames, MQTT payloads and screen text sit next to
unlock, messaging, shell and code-exec. So the model is kept out of the loop
for anything dangerous:

- **Tiered actions**, with the tier decided in code and never by the model. A
  server may only ever *raise* a tier, never lower one.
- **Human approval enforced outside the model** for tier 3, and for the shell
  path enforced twice — once in `jarvis-core`, once in the orchestrator, with
  different credentials in different processes.
- **What was approved is what runs**: fuzzy targets are resolved to concrete
  entity ids, and contact names to numbers on the device, before a human is
  shown the prompt; commands are stored verbatim.
- **Everything from outside is fenced** as data before the model sees it, and
  cannot close its own fence.
- **A network-less sandbox**, LAN/WireGuard only, nightly purge.

The full model is [`docs/security.md`](docs/security.md).

## Honest limits

- **The head unit belongs to Google.** A third-party app cannot be the voice
  assistant on Android Auto — no API, no role, no category. Jarvis in the car
  is a phone-side experience playing through the car speakers.
  [`docs/android-auto.md`](docs/android-auto.md).
- **The wake word works on the phone, and cannot be proven from here.**
  `WakeWordService` is a real foreground service with real callers, it detects
  on-device, and `WakeStartPolicy` pins when Android will let it start — which
  is the fiddly part, because a `microphone` foreground service may not start
  from the background, so a reboot needs a battery-optimisation exemption or
  the overlay grant, and without one the service leaves a notification that
  starts it in a tap. Whether a phone in a pocket actually hears you across a
  room is a claim only a phone can settle, and no row here says otherwise.
- **Jarvis can be told to answer only your voice, and it is off by default.**
  A classical verifier — MFCC statistics and a pitch distribution, compared
  against an enrolled profile — runs on the turn's audio before the intent
  stage. It stops a house guest, a television and a stranger at the window. It
  does **not** stop a recording of you, and it is not a second factor for
  anything: the tier system and its human approval gate are still what stand in
  front of the dangerous verbs. Enrol on the phone, leave it in `observe` until
  you have read your own scores, and only then enforce.
  [`docs/voice-identity.md`](docs/voice-identity.md).
- **An earpiece is the good hands-free story, and it is switched off until you
  switch it on.** Capture moves to the headset, the reply is echo-cancelled so
  Jarvis does not hear itself, and the headset button starts a turn — after
  **Settings → Headset**, which is where the three switches live. Two limits
  worth knowing before you rely on it: the button only reaches Jarvis when
  Jarvis holds the media session, so an app that is genuinely playing usually
  gets the press instead; and none of it has been tried on real Bluetooth
  hardware. See [`docs/earpiece.md`](docs/earpiece.md).
- **Multi-agent delegation and coding jobs are aspirational at 8B.** They
  work; whether the decomposition is any good depends on your model.
  `make eval-decomp` is the ship/no-ship gate, and failing it is a reason to
  stay on tier 2.
- **No dashboards.** There is no Lovelace, no config UI, no add-on store. If
  your house needs Z-Wave JS, HomeKit, Matter or cloud-tied devices, Home
  Assistant is still the better tool and can run alongside.
  [`docs/standalone.md`](docs/standalone.md).
- **GrapheneOS clears the assistant role on every app update.** Re-run
  `scripts/adb-jarvis-role.sh` afterwards. This is OS hardening, not a bug,
  and no app can work around it.

## Docs

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | how the pieces fit, the client protocol, how to add a tool |
| [`docs/security.md`](docs/security.md) | threat model, tiers, isolation, egress |
| [`docs/standalone.md`](docs/standalone.md) | why the clients never noticed HA leaving |
| [`docs/verification.md`](docs/verification.md) | what is proven, what is not |
| [`docs/removed.md`](docs/removed.md) | what was deleted and why |
| [`docs/android.md`](docs/android.md) | the phone, and where its docs live |
| [`docs/grapheneos.md`](docs/grapheneos.md) | why the old fork crashed, what replaced it |
| [`docs/cross-device.md`](docs/cross-device.md) | one conversation across phone, desktop and HUD |
| [`docs/voice-identity.md`](docs/voice-identity.md) | answering only your voice: how it works, what it is worth, how to tune it |
| [`docs/wake-word-training.md`](docs/wake-word-training.md) | training `hey_jarvis` |
| [`jarvis-core/docs/`](jarvis-core/docs/) | configuration, integrations, voice, search, clients, migrating from HA |

`DEVIATIONS.md` records where this build knowingly differs from the original
plan, and what was not run in this environment.
