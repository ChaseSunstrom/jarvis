# Testing Jarvis

Five layers, each proving something the one below it cannot. This is the map:
where a given behaviour is checked, what command checks it, and — the part that
matters most — what nothing checks at all.

[`verification.md`](verification.md) is the companion document. It is the
claims register: every behaviour, its level of proof, and the known gaps. This
one is the *pyramid*: how the layers fit together and how to run them.

```
                    ┌──────────────────────────────────┐
        5           │  your hardware                   │   a phone, a mic,
                    │  scripts/e2e-smoke.sh            │   a GPU, a human
                    ├──────────────────────────────────┤
        4           │  device end-to-end               │   emulator, desktop
                    │  Android emulator · desktop      │   agent, browser
                    │  agent · Playwright HUD          │
                    ├──────────────────────────────────┤
        3           │  THE HARNESS   testing/          │   real server,
                    │  real jarvis-core, fake GPU      │   fake model + voice
                    ├──────────────────────────────────┤
        2           │  in-process integration          │   the platform booted
                    │  jarvis-core/tests/test_e2e.py   │   from a config file
                    ├──────────────────────────────────┤
        1           │  unit suites                     │   one subsystem,
                    │  ~2500 tests, eight packages     │   everything else fake
                    └──────────────────────────────────┘
```

The rule that shapes all of it: **a fake goes where the network is, never
inside the code under test.** Layer 3 does not monkey-patch the pipeline; it
puts a different server on the far end of the socket. That is why a passing
layer-3 test is evidence about the real code path and not about the test.

---

## Quick reference

```bash
# 1 — unit suites (no network, no hardware, no models)
for d in jarvis-core jarvis-desktop jarvis-browser jarvis-orchestrator jarvis-sandbox; do
    ( cd "$d" && echo "== $d" && python3 -m pytest tests -q )
done
( cd evals && python3 -m pytest test_routing.py -q )
( cd android-app/tools && for f in *.py; do python3 "$f" || echo "FAILED: $f"; done )
( cd jarvis-web && npm test )

# 2 — the platform booted in-process
( cd jarvis-core && python3 -m pytest tests/test_e2e.py -q )

# 3 — the harness: a real server on a real socket
python3 -m pytest testing/e2e -q
testing/scripts/run-e2e.sh                  # same, keeping logs + audio

# 4 — devices (see each project; the harness is what they point at)
( cd jarvis-web && npx playwright test )

# 5 — your own machine, your own services
./scripts/e2e-smoke.sh
```

Layers 1–3 are the ones that must be green on every push. Nothing in them
needs a GPU, a model, a container, a microphone or a network.

---

## Layer 1 — unit suites

One subsystem at a time, everything around it replaced. Fast, precise, and by
far the largest layer: it is where a bug gets a name.

| Suite | Tests | Command |
|---|---:|---|
| `jarvis-core` | 1229 | `cd jarvis-core && python3 -m pytest tests -q` |
| `jarvis-desktop` | 722 | `cd jarvis-desktop && python3 -m pytest tests -q` |
| `jarvis-browser` | 328 | `cd jarvis-browser && python3 -m pytest tests -q` |
| `jarvis-orchestrator` | 17 | `cd jarvis-orchestrator && python3 -m pytest tests -q` |
| `jarvis-sandbox` | 6 | `cd jarvis-sandbox && python3 -m pytest tests -q` |
| `evals` (routing table + mirrors) | 17 | `cd evals && python3 -m pytest test_routing.py -q` |
| `android-app/tools` (Kotlin logic mirrors) | 11 files | `cd android-app/tools && for f in *.py; do python3 "$f"; done` |
| `jarvis-web` (vitest) | 194 | `cd jarvis-web && npm test` |

Counts collected 2026-08-09. Per-file breakdown for `jarvis-core` is in
[`verification.md`](verification.md#suite-sizes-measured-2026-08-09).

**What this layer proves.** That each piece behaves as specified in isolation:
the Wyoming framing encodes and decodes, the tier arithmetic never returns
less than the floor, the policy truth table matches on both sides of the
language boundary, the SSRF guard refuses what it should.

**What it cannot prove.** That the pieces are wired to each other. Every
subsystem here passes with the rest of the system replaced by a stub, so a
suite of 1229 green tests is entirely compatible with a server that does not
start.

The Android entry is the odd one: the Kotlin cannot be compiled without the
SDK, so the pure logic that matters most — policy tiers, geofencing, schedule
maths, screen pruning, the channel protocol — has runnable Python mirrors that
act as its executable specification. They are a specification, not the code. A
mirror passing says the *rules* are right; only layer 4 says the app obeys
them.

---

## Layer 2 — the platform, in-process

`jarvis-core/tests/test_e2e.py` builds a real `Jarvis` from a temporary
`configuration.yaml`, sets up every integration, and drives it — including
through a real ASGI test client. Three things are faked, each at the point
where the network would be: Wyoming (objects injected through `jarvis.data`),
Ollama (an `httpx.MockTransport` serving genuine NDJSON), and the device
transport (a callable, exactly as the API layer hands one over).

```bash
cd jarvis-core && python3 -m pytest tests/test_e2e.py -q
```

Its money test drives PCM in one end and asserts that `light.bed_light`
genuinely went from `off` to `on` at the other. The full list is in
[`verification.md`](verification.md#what-test_e2epy-proves).

**What it cannot prove.** That the process starts. Everything here runs inside
the pytest interpreter, so `python -m jarvis` never executes, uvicorn never
binds a port, no config file is read from a real directory, and no client
outside the process ever connects. That is layer 3.

---

## Layer 3 — the harness

**A real `python -m jarvis` process, on a real socket, with fake model and
voice backends.** This is the layer everything else with a client stands on.

```bash
pip install -r jarvis-core/requirements.txt -r testing/requirements.txt
python3 -m pytest testing/e2e -q
```

186 tests, about 13 seconds (measured 2026-08-12). No GPU, no models, no
hardware, no network beyond loopback.

### What it starts

```
  testing/harness/harness.py
        │
        ├── fake_ollama.py    :auto   GET /api/tags, POST /api/chat (NDJSON)
        ├── fake_wyoming.py   :auto   STT / TTS / wake, real Wyoming framing
        │
        └── python -m jarvis --config <tmp> --host 0.0.0.0 --port <auto>
                 ▲                                    ▲
                 │ REST + websocket                   │ configuration.yaml
            testing/harness/client.py             written by the harness
```

Everything between the client and the two fakes is production code: the real
websocket framing, the real pipeline runner, the real Ollama streaming client,
the real tool registry, the real device channel, the real recorder.

### Running it

```bash
# one-shot: print the JSON and tear everything down
python3 testing/harness/harness.py

# keep it up for an emulator, a device, or a manual poke
python3 testing/harness/harness.py --wait --json-out /tmp/harness.json

# a fixed port, your own model script, and the work directory kept
python3 testing/harness/harness.py --wait --port 8099 \
    --ollama-script my-script.json --work-dir ./run --keep
```

It prints exactly one JSON line:

```json
{
  "base_url": "http://127.0.0.1:41551",
  "ws_url": "ws://127.0.0.1:41551/api/websocket",
  "emulator_base_url": "http://10.0.2.2:41551",
  "emulator_ws_url": "ws://10.0.2.2:41551/api/websocket",
  "fake_host": "127.0.0.1",
  "token": "jarvis-test-token-0000000000000000000000",
  "ports": {"core": 41551, "ollama": 45373, "stt": 44129, "tts": 43561, "wake": 46783},
  "ollama_url": "http://127.0.0.1:45373",
  "ollama_control_url": "http://127.0.0.1:45373/_control",
  "work_dir": "/tmp/jarvis-harness-anjdlp0l",
  "logs": {"jarvis-core": "...", "fake-ollama": "...", "fake-wyoming": "..."},
  "pids": {"jarvis-core": 4129, "fake-ollama": 4125, "fake-wyoming": 4127}
}
```

Three details are load-bearing:

* **The server binds `0.0.0.0`.** An Android emulator reaches its host at
  `10.0.2.2` and nowhere else, so a harness bound to loopback is invisible to
  the device layer. `emulator_base_url` is that address pre-built.
* **The fakes bind loopback, and only loopback** (`--fake-host`, default
  `127.0.0.1`). Nothing but jarvis-core ever talks to them, and the fake
  Ollama's `/_control` plane can rewrite what the model says — that has no
  business on an outward-facing interface of a shared runner.
* **The token is deterministic.** `JARVIS_TOKEN` is jarvis-core's own
  documented override (see `jarvis/auth.py`): always accepted, never written to
  disk. No test has to scrape a token out of a log banner, and the same token
  works on every run.

Every child runs in its own process group. `stop()` signals the group, escalates
to `SIGKILL` after 15 s, and is also wired to `atexit` — a test process that
dies unexpectedly still leaves no orphans, and no port held.

The server's port is drawn by binding zero and letting go, then handed to the
child on its command line; nothing can hand a child a socket it did not open,
so there is always a gap in which another process may take the number. Two
harnesses starting together really do collide. The harness therefore re-draws
the port once the fakes have bound, and retries the boot up to four times on an
`EADDRINUSE` — and *only* on that; a server that starts and then does not
answer is reported with its own log rather than retried. Each losing attempt's
log is kept as `logs/jarvis-core-attempt<N>.log`.

### Driving it from a test

```python
from testing.harness import Harness, JarvisClient, FakeDevice, speech_pcm, parse_wav

with Harness() as jarvis:
    async with JarvisClient(jarvis.base_url, jarvis.token) as client:
        await client.connect()                       # auth_required -> auth_ok

        run = await client.run_pipeline(audio=speech_pcm())
        assert run.transcript == "turn on the lab lights"
        assert parse_wav(await client.get_bytes(run.tts_url))["frames"] > 0

        await client.call_service("light", "turn_on",
                                  target={"entity_id": "light.bed_light"})
        await client.wait_for_state("light.bed_light", "on")

        device = FakeDevice(client, "test-laptop")
        await device.register()
        command = await device.next_command(action="lock_screen")
        await device.answer(command["command_id"], "ok")
```

In `testing/e2e` the `harness` (session) and `client` (per test) fixtures do
the first two lines for you.

`JarvisClient` multiplexes one socket: a single reader task fans results out to
futures, subscribed and pipeline events to per-id queues, and the device
channel's pushes to their own. Every wait is a wait for a *condition* with a
deadline, so a slow machine makes the suite slower rather than flaky. If the
reader ever dies, the reason is pushed to everything waiting, so you get an
explanation instead of a stall.

### Scripting the model

`fake_ollama.py` maps a substring of what the **user** said to the responses to
give, consumed in order on successive calls. That is what makes a tool-calling
turn reproducible:

```json
{
  "rules": [
    {
      "match": "turn on the lab lights",
      "responses": [
        {"tool_calls": [{"name": "turn_on",
                         "arguments": {"entity_id": "light.bed_light"}}]},
        {"say": "Turning on the lab lights, Sir."}
      ]
    }
  ],
  "default": {"say": "Very good, Sir."}
}
```

The first `/api/chat` asks for the tool call; jarvis-core runs it through the
ordinary service layer and comes back; the second — which still carries the
same user message — gets the words. The last response repeats, so a turn that
needs an extra round does not fall off the end of its script.

Matching is against user messages only, never the system prompt: the prompt
carries a summary of every exposed entity, so a rule naming a device would
otherwise fire on every turn. Add `"scope": "all"` or `"last"` to change that,
and `"match_type": "regex"` or `"exact"` to change how.

Other keys a response takes: `chunks` (explicit deltas instead of word
splitting), `thinking` (emitted as `message.thinking`, which the agent's
`<think>` stripper must swallow), `error` and `status` (make the model fail),
`delay_ms`, `done_reason`.

From a test:

```python
harness.set_ollama_script({"rules": [...]})   # replace; counters reset
harness.set_ollama_script(None)               # back to the default brain
harness.reset_ollama()                        # forget what has been served
harness.ollama_requests()                     # every payload it was sent
harness.last_ollama_messages()                # ...and the last one's messages
```

Or over HTTP, from any language: `POST /_control/script`, `POST /_control/reset`,
`GET /_control/requests`, `GET /_control/health` on `ollama_control_url`.

### Scripting the voice

`fake_wyoming.py` speaks the real framing on three ports. TTS returns a real
16-bit sine, so the WAV the pipeline builds is a playable file rather than a
buffer of zeros, and its length tracks the text at 50 ms a character — which
is how a test checks that *this answer* reached synthesis rather than some
other string, without needing to hear it. The duration is clamped to
250 ms – 5 s, so above about a hundred characters the length stops
distinguishing one answer from another; assert on a short one, or set
`{"tts": {"seconds": …}}` explicitly.

```python
harness.set_transcript("lock the front door")
harness.set_transcripts(["first utterance", "second utterance"])  # then repeats
harness.set_wake_detection(detect=True, after=2, name="hey_jarvis")
harness.set_stt_length_mode("heard {ms} ms in {chunks} chunks")
```

That last one deserves its own paragraph. A scripted transcript cannot tell
"the audio stream works" from "the server invented the answer" — both look
identical from outside. In length mode the transcript is *derived from the
audio that actually arrived* (`{bytes}`, `{ms}`, `{chunks}`, `{samples}`), so a
test can assert that a one-second utterance produced a different transcript
from a two-second one, and that an empty stream produced `heard 0 ms of audio`.
`test_the_transcript_proves_the_audio_arrived_rather_than_being_a_constant`
does exactly that.

`set_transcripts` is a queue: one entry per run, the last one repeating. The
cursor into it belongs to the script that set it, so a queue set after any
number of earlier runs still starts at its first entry.

The fakes re-read their script file whenever it changes, so nothing needs
restarting mid-suite. The harness publishes that file by rename rather than by
truncating it, so a fake never reads half an edit, and change detection keys on
the inode as well as the timestamp — two rewrites inside one filesystem clock
tick are still two rewrites. Both are stdlib-only and self-contained — copy either to
a machine with nothing but `python3` and it runs:

```bash
python3 testing/harness/fake_ollama.py  --port 11434 --script script.json
python3 testing/harness/fake_wyoming.py --stt-port 10300 --tts-port 10200 \
                                        --wake-port 10400 --transcript "hello"
```

### What the self-test proves

`testing/e2e/test_harness_selftest.py` is the foundation everything else rests
on, so it is explicit about each claim:

| Group | What it establishes |
|---|---|
| boot | `/healthz` reports a running server with a real house in it; the process binds every interface — checked by connecting over this machine's own non-loopback address, which is the same route `10.0.2.2` is for an emulator (skipped, with a reason, on a host that has only loopback). |
| auth | The deterministic token works over REST and over the websocket; a missing one and a wrong one are both refused on both. |
| voice | A full `assist_pipeline/run`: `run-start` carries a usable `stt_binary_handler_id`; audio streams down that binary channel; the VAD opens and closes; `stt-end` carries the scripted transcript; the model streams more than one `intent-progress` delta and they reassemble into the answer; `tts-end` carries a `/api/tts_proxy/…wav` URL that serves a WAV with real frames in it. Plus text-only runs, wake-word runs, and a stage failure reported as an `error` event rather than a hang. |
| audio really moved | Length mode: a longer utterance produces a longer transcript, and no audio at all produces `heard 0 ms`. |
| the house | A service call changes state and the change is visible over REST, to an event subscriber, and to an automation in the generated config. |
| the model | A scripted tool call is dispatched through the ordinary service layer and the light really turns on; the model is handed the real toolbox and a system prompt with the live house in it; a model failure is reported and does not kill the server. |
| the device channel | Register, receive a `device_command`, answer it, and see the answer come back out of the service call. |
| **the tier invariant** | `effective_tier` is `max(local, requested)`: a caller may raise a tier and can never lower one, an action the device never advertised never reaches it, and a device saying no comes back as `denied`. |
| **the fence** | One real model turn reads the screen (an action the manifest marks `untrusted_output`) and then asks for an action the device calls AUTO. The frame the device actually receives carries tier 3, so the human is asked before anything runs: injected text can suggest an action, never cause one quietly. |
| trust | A `device_event` marked `untrusted` reaches the bus with its label intact. |
| presence | `companion.ask` routes a question to the device that reported itself present, and the human's answer comes back to the waiting caller. |
| no deadlock | A device answering a command on the *same* socket the command was issued from — the desktop agent's exact shape — completes rather than deadlocking. |
| the harness itself | Two clients at once; the reported ports are all distinct and all live; the fakes answer on loopback and nowhere else; a queue of transcripts is served one per run however many runs came first; a rewritten voice script is picked up even when two writes share a timestamp; the server survives another process stealing its port mid-boot; and a second run in the same work directory starts clean rather than inheriting the last one's ports or `.storage`. |

**What it cannot prove.** That any real client works. Nothing here runs the
Android app, the desktop agent or the HUD; `JarvisClient` is a test client, and
a test client agreeing with the server proves the server, not the app. That is
layer 4.

### Artifacts

`testing/scripts/run-e2e.sh` is what CI calls. It keeps every harness work
directory under `testing/artifacts/` whatever the outcome:

```
testing/artifacts/harness/
  config/configuration.yaml     exactly what the server was booted with
  config/harness.db             the recorder's SQLite file
  config/.storage/*.json        registries as they ended up
  logs/jarvis-core.log          the server's own log, in full
  logs/jarvis-core-attempt*.log a boot that lost a port race, if any
  logs/fake-ollama.log
  logs/fake-wyoming.log
  audio/stt-*.wav               every utterance the fake STT received
```

The audio is the one people forget. When a voice test fails, the question is
almost always "did the microphone bytes get there, and what did they sound
like" — so the fake writes what it was sent, and a failed CI job hands you a
WAV you can listen to.

On failure the script also tails all three logs into the job output, so the
first look does not require downloading anything.

### Wiring it into CI

The suites need only Python. A job looks like this:

```yaml
  e2e:
    name: end-to-end · real core, fake backends
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - name: Install
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r jarvis-core/requirements.txt
          python -m pip install -r testing/requirements.txt
      - name: End-to-end
        run: testing/scripts/run-e2e.sh
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: e2e-harness-artifacts
          path: testing/artifacts
          retention-days: 7
```

`if: always()` rather than `if: failure()` is deliberate: the audio and the
config are worth having from a green run too, as the baseline you compare the
next failure against.

---

## Layer 4 — devices

Layer 3 proves the server. This layer proves the things that talk to it, each
running for real.

**Android.** The instrumented suite under
`android-app/app/src/androidTest/` installs the real APK on an emulator and
drives the real activities — the boot animation, the assist flow, settings
persistence, the consent gate, the device channel — capturing screenshots so
the UI can be looked at rather than assumed. It is a Gradle
`connectedAndroidTest`, so it needs the Android SDK and a running emulator and
cannot be run from a plain Python checkout; `android-app/` carries its own
instructions. Where it wants a server rather than an in-process stub, the
harness is what it points at:

```bash
python3 testing/harness/harness.py --wait --json-out /tmp/harness.json &
python3 - <<'PY'
import json; info = json.load(open("/tmp/harness.json"))
print(info["emulator_base_url"], info["token"])   # -> the app's settings
PY
```

**Desktop agent.** `python -m jarvis_desktop` against the harness: it registers
over the same device channel, takes commands, and applies its own policy. The
tier invariant matters most here, because the desktop agent is the one holding
a shell.

**Browser HUD.** `cd jarvis-web && npx playwright test` drives the built HUD in
chromium.

**What this layer cannot prove.** Anything about a real radio, a real
microphone, a real speaker or a real GPU. An emulator has no wake-word DSP and
no cellular modem; a fake Wyoming has no acoustic model. Layer 5.

---

## Layer 5 — your own hardware

```bash
./scripts/e2e-smoke.sh              # a throwaway core against your real services
make pipeline-smoke                 # full STT -> TTS round trip (needs JARVIS_TOKEN)
make eval-persona                   # the persona eval (needs a model)
./scripts/egress-audit.sh           # sandbox network isolation
```

`e2e-smoke.sh` boots a throwaway jarvis-core against a temporary config — your
own config, database and tokens are never touched — and drives the real APIs
against whatever is actually running on the box. Checks whose service is not
running are **skipped with the reason**, never failed.

The difference from layer 3 is the point: layer 3 proves the code, on any
machine, in four seconds. Layer 5 proves *this installation* — that whisper is
loaded, that piper has the voice named in the config, that the model is pulled,
that the firewall lets the phone in.

---

## What nothing checks

Stated plainly, because a pyramid with a missing top is worth less than an
honest one:

* **Wake-word accuracy.** The fake fires on cue; openWakeWord's false-accept
  and false-reject rates against a real room are unmeasured here.
* **Speech recognition quality.** Fake STT returns a script. Whether whisper
  hears "turn on the lab lights" in your accent, over your fan noise, is not a
  question this repo can answer.
* **Synthesis quality.** Fake TTS returns a sine. It proves the bytes are valid
  audio of the right shape, not that piper sounds like anything.
* **Real microphone capture and playback.** No test opens an audio device.
* **Latency.** Every number in the pipeline is measured against fakes that
  answer instantly. Real latency is a layer-5 measurement.
* **Model behaviour.** The fake model does exactly what it is told. Whether a
  real qwen3 calls the right tool, or resists a prompt injection in a web page,
  is what `evals/` is for and is not proven by any layer here.
* **A real phone.** The emulator is not a Pixel: no GrapheneOS, no real
  permission dialogs from the OS's own UI, no doze, no cellular handoff.
  `docs/verification.md` lists the on-device gates.

---

## Writing a new end-to-end test

Put it in `testing/e2e/`. The fixtures are in `conftest.py`.

```python
async def test_the_thing_that_matters(client, harness):
    harness.set_ollama_script({
        "rules": [{
            "match": "close the blinds",
            "responses": [
                {"tool_calls": [{"name": "turn_off",
                                 "arguments": {"entity_id": "cover.living_room_window"}}]},
                {"say": "Blinds closed, Sir."},
            ],
        }],
    })
    try:
        reply = await client.conversation("close the blinds")
        assert reply["response"]["speech"]["plain"]["speech"] == "Blinds closed, Sir."
        # The assertion that carries the weight: the house really changed.
        await client.wait_for_state("cover.living_room_window", "closed")
    finally:
        await client.call_service("cover", "open_cover",
                                  target={"entity_id": "cover.living_room_window"})
```

That exact test is in the suite as
`test_a_test_supplied_script_drives_a_different_tool`, so this example stays
true.

Three rules for anything added here:

1. **Wait for a condition, never for a duration.** `wait_for_state`,
   `stream.wait_for(predicate)`, `next_device_command(action=...)` — all take a
   generous timeout and return the instant the thing happens. A `sleep(2)` is
   both slower on a fast machine and flaky on a slow one.
2. **Put the house back.** The harness boots once per session. A test that
   leaves `light.bed_light` on has changed the starting conditions for
   everything after it.
3. **Assert on the observable, not the incidental.** `run.transcript ==
   "turn on the lab lights"` is a claim about the pipeline. `len(run.events)
   == 16` is a claim about nothing, and it will break the first time a stage
   gains an event.

Environment variables the fixtures read:

| Variable | Effect |
|---|---|
| `JARVIS_HARNESS_KEEP=1` | Do not delete the work directory after the run. |
| `JARVIS_HARNESS_WORK_DIR=<path>` | Put config, logs and audio there instead of a temp dir. |
| `JARVIS_HARNESS_VERBOSE=1` | Debug logging from the server and both fakes. |

---

## When something fails

**The harness would not start.** The error carries the tail of
`jarvis-core.log`. The usual causes are a missing dependency (`pip install -r
jarvis-core/requirements.txt`) and a port that was taken between being chosen
and being bound — rare, and a re-run settles it.

**A pipeline run stalled.** The failure names the events that did arrive:
`pipeline run stalled after 60s; events so far: ['run-start', 'stt-start']`.
Where it stopped tells you which stage: no `stt-end` means the STT socket, no
`intent-end` means the model, no `tts-end` means synthesis. Then read
`logs/fake-wyoming.log` or `logs/fake-ollama.log` for that side of it.

**The model said the wrong thing.** `harness.ollama_requests()` is every
payload the model was sent, in order, with the rule each one matched. A rule
that matched `"default"` when you expected yours is usually a substring that is
not in the *user's* message.

**It passed locally and failed in CI.** Download the `e2e-harness-artifacts`
bundle:
the config it booted from, all three logs, and the audio the STT actually
received. Reproduce with the same work directory:

```bash
JARVIS_HARNESS_KEEP=1 JARVIS_HARNESS_WORK_DIR=./run python3 -m pytest testing/e2e -q
```

Then run the suite against a loaded machine, which is what a shared CI runner
is — several copies at once is the cheapest way to shake out an ordering
assumption:

```bash
for i in 1 2 3 4; do python3 -m pytest testing/e2e -q & done; wait
```
