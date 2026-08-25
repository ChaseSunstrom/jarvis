# Deviations & constraints

Where this build knowingly differs from what was planned, where a thing is
genuinely impossible and what is done instead, and what was never run here.

For the per-capability claims register — what is proven, by which command —
see [`docs/verification.md`](docs/verification.md). This file is for the
judgement calls behind it.

## Phone automation is scaffolded and off, not built

The plan called for Jarvis to drive the phone's other apps — read their screens
through an accessibility service, read notifications, tap and type. What ships
is the *interface* for that (`automation/phone/PhoneAutomation.kt`), behind a
compile-time flag that is false in every build, with the two Android services
standing down while it is off, the bridge refusing the actions, and the runtime
master switch now defaulting off as well.

Four refusals for one feature, because the failure is not recoverable. An
accessibility service sees banking apps, messages and password autofill, with
no way to be selective and no way for the user to know afterwards what was
read; an injected tap is indistinguishable from a finger to the app receiving
it. What is missing before it could ship is not code — it is a per-app consent
scope, a record of what was read (the audit log covers actions, and a screen
read is not one), and a refusal path for sensitive fields that Android does not
mark. `android-app/docs/phone-automation.md` says all of that, and
`docs/ANDROID_DEVICE_TESTS.md` carries the four checks that would need a phone.

The master switch changing from ON to OFF is the other half of the judgement:
it defaulted on with the argument that a fresh install should be useful and the
per-action tiers keep it safe — and they do. What changed is what the switch
governs now that the phone interfaces exist beside the house ones.

## The Gradle wrapper's jar is committed

`android-app/gradle/wrapper/gradle-wrapper.jar` and `gradlew` are in the
repository. They were ignored, with a line in the README telling people to run
`gradle wrapper` once — which means the build needs a Gradle installation
before it can have one, and nobody could clone this repository and build the
app. That is what "the Android build has never run here" was made of.

Upstream Gradle recommends committing the wrapper for exactly this reason. The
jar is 43 KB, it is not built from this repository, and what it does is
download the distribution pinned in `gradle-wrapper.properties`
(`gradle-8.10-bin.zip`) and verify it against `distributionSha256Sum` when one
is set. `tools/bootstrap-toolchain.sh` installs everything else — JDK 17 and
the SDK — under `$HOME`, with no root, which is the same constraint every other
tool in this project is built to.

The alternative — a wrapper that must be generated — costs a working build on a
fresh clone and buys nothing that reviewing one 43 KB binary once does not.


## 1. Hardware-gated tests were not executed in this environment

This repository was built in a cloud container with **no GPU, no Ollama, no
GrapheneOS Pixel, no Android Auto head unit or DHU**, and no Docker daemon.

Everything verifiable without those is verified and green. Everything that
needs the missing hardware is written, wired and documented with an exact run
procedure, and is marked **Manual** or **Unproven** in
`docs/verification.md`. It has not been run here, and no row claims otherwise.

To close the remaining gaps on your own kit:

```bash
make smoke              # boots a throwaway jarvis-core against your services
make pipeline-smoke     # full stt->tts audio round trip through Wyoming
make test-web           # HUD build + unit + Playwright
make eval-persona BACKEND=jarvis
make eval-decomp        # ship/no-ship for tier-3 delegation
make egress-audit       # sandbox isolation, against the running stack
```

Then the device procedures in `docs/android.md` and `docs/android-auto.md`.

## 2. Android Auto in-car voice — a genuine OS impossibility

A third-party app **cannot** be the Android Auto voice button. Only Google's
assistant can, and Google removed Assistant from AA in March 2026 leaving
Gemini only. There is no "voice assistant" Car App category for third
parties, and the head-unit mic is routed to the AA stack while connected.

**The fallback, implemented:** phone-side "Hey Jarvis" runs in parallel while
AA is connected — the mic is the phone's, TTS routes out over the car's
Bluetooth link, nothing renders on the head unit. Full write-up in
[`docs/android-auto.md`](docs/android-auto.md).

**The car-BT wake policy, stated precisely.** This section used to claim that
`WakeWordGate` "turns detection on for the drive and off afterwards". The gate
implemented exactly that policy and **nothing in the app called it** — it had no
production caller at all, and the settings screen said so in its own heading
("When to listen — saved, not yet in effect"). It is wired now
(`assist/WakeListenWatch.kt`, pinned by
`android-app/tools/wake_listen_gate_test.py`), and what it does is:

* Car Bluetooth connects → detection on, whatever the hour. Disconnects → the
  at-home rule decides again. That much is the original claim and it now holds.
* **"Afterwards" depends on a place signal a phone usually does not have.** The
  gate only knows you are away from home if you have a location automation whose
  geofence is named `home`; with one, leaving the house stops the listening.
  Without one, "at home" is *unknown*, and unknown is resolved as at home — so
  the waking-hours window is what decides, and outside it nothing listens unless
  you are in the car or wearing a headset.

Resolving unknown the other way was considered and rejected: it would silence
always-on detection everywhere except a car for every user who has not drawn a
circle on a map, which is the feature switched off rather than a battery policy.

## 3. Tier-3 multi-agent quality at 8B is aspirational

`delegate_to_agents` and `code_task` are the weakest links on an 8B planner.
Rather than assert quality that cannot be verified here, the ship decision is
a deterministic gate: `evals/decomposition_eval.py` (5 three-part requests,
keyword-coverage scoring, ≥60% to ship tier 3). **Run it on your model.**

If it fails:

* leave the orchestrator running — `code_task` and the command broker are
  independent of decomposition quality;
* drop `delegate_to_agents` from what the model can see, by removing the
  `orchestrator:` block from `configuration.yaml` — that integration is what
  registers the tool. (`llm: expose:` will not do it: that block filters
  **entities**, not tools, and setting it there narrows nothing while looking
  as though it has.)
* tiers 1 and 2 ship regardless and are reliable.

Coder quality scales with the model: `CODER_MODEL` defaults to
`qwen2.5-coder:7b` and can be raised to `:14b`/`:32b` on a GPU.

## 4. Persona wit is aspirational; tone is not

8B gives reliable Sir/ma'am tone but not screenwriter-sharp wit. The persona
eval separates these: tone and routing cases gate (≥80%, and all 10
adversarial cases must pass), the 5 wit cases are scored and reported but
never gate. A LoRA or a larger model improves wit later; never train on
copyrighted scripts.

## 5. Credentials cannot be spliced into a `*.tool.yaml` manifest

Manifests in `config/tools/` are read with a plain YAML parser, so `!secret`
and `!env_var` do not work there — a half-resolved credential in a URL is a
worse failure than an honest one. A tool that needs a credential belongs in
the inline `llm: tools:` block in `configuration.yaml`, which is loaded with
the full config loader. This is documented at the point of use, in
`jarvis-core/config/tools/example.tool.yaml`.

## 6. OpenCode may be absent from the orchestrator image

If `opencode` fails to install (network policy, version pin), `code_task`
returns a clear "opencode binary not installed" error rather than pretending
to work. Alternatives (Aider, Continue) drop in by replacing `build_command`
in `jarvis-orchestrator/app/opencode.py`.

Likewise, if `apt-get` was unreachable during `docker compose build`, the
Dockerfiles treat the package step as best-effort and log a `WARN`. The
orchestrator API and the sandbox work regardless; `code_task` needs `git` and
will not.

## 7. The orchestrator and sandbox are opt-in, and start disabled

They are commented out in `jarvis-core/docker-compose.yml` and their
credentials default to empty. jarvis-core registers `delegate_to_agents`,
`code_task` and `execute_command` regardless, so the model is told the truth
about its toolbox, and they return "not configured" until you set
`ORCHESTRATOR_TOKEN` and `APPROVAL_SECRET` and start the services.

Those two values must **differ**. If they match, holding the API token is
enough to execute a command, which defeats the entire two-secret design;
jarvis-core logs an error at startup when it sees them equal, but it cannot
refuse on your behalf.

## 8. What the on-device Kotlin policy engine proves, and where

The Android policy engine, geofence, schedule maths and screen pruning are
mirrored by pure-Python executable specs in `android-app/tools/`, which CI
runs. The Kotlin itself is **not compiled** in this environment — there is no
Android SDK here. So "the tier system is enforced on the device" is proven in
the mirror and unproven in the shipped binary. `docs/verification.md` says so
in the row where it matters.

## 9. Voice identity is a classical verifier, not a neural one

`voice/speaker.py` is MFCC statistics plus a pitch distribution with a
per-dimension z-test. An ECAPA-TDNN trained on thousands of speakers is
markedly better at this, and if you have somewhere to run one, `Embedder` is the
seam.

The classical route was chosen for one reason: it runs in the process that is
already running, with no model file, no GPU and no new dependency, on the same
Pi that is already doing STT — which is the difference between a feature that is
ON and a feature that is documented. jarvis-core's requirements are deliberately
pure-Python-installs-from-a-wheel, and adding numpy or onnxruntime for one
feature would end that.

Two consequences, both stated in `docs/voice-identity.md` and both real:

* **Accuracy on human speech is Unproven here.** The tests run against a
  source-filter synthesiser, which settles that the code separates signals
  differing in the cues it claims to use and nothing about false-accept rates in
  a real room. `observe` mode exists precisely so you find that out on your own
  voice before enforcement can refuse you.
* **It does not stop a recording of you.** It raises the cost from "be in the
  room" to "sound like the owner to a spectral matcher", which stops a guest, a
  television and a stranger at the window. It is not a second factor, and the
  tier system still stands in front of every dangerous verb.

## 10. On-device transcription suspends itself while voice identity enforces

The speaker check runs on the server, on the turn's audio. A turn the phone
transcribes locally sends words rather than sound, so there is nothing to check.
With `mode: enforce` and on-device transcription both switched on, every turn
walked straight past the gate — and neither setting looks dangerous on its own,
which is what made the combination worth engineering against rather than warning
about.

**Verifying on the phone instead is not available, and this is a platform fact
rather than unfinished work.** `LocalTranscriber` uses
`SpeechRecognizer.createOnDeviceSpeechRecognizer`, and the platform recogniser
*owns the microphone*: the app is handed partial text and an RMS level through
`onRmsChanged`, and never a single audio sample. There is no PCM on that device
to embed. An earlier version of this file proposed porting `voice/dsp.py` to
Kotlin so the phone could verify locally; that would have produced a correct
embedding implementation with nothing to feed it — precisely the "seam with no
caller" this codebase has been bitten by three times. It is not being written.

Owning the microphone instead would mean replacing the platform recogniser with
a bundled speech model, which is a different feature with a different cost.

So the two are made mutually exclusive, in code, on both sides:

* **The phone** suspends the on-device path while the gate enforces
  (`JarvisConversation.startLocalTurn`), streams instead, and the settings
  screen's status line says SUSPENDED and why. This is the half that keeps
  Jarvis *working*.
* **The server** refuses a transcript that admits it came from a microphone it
  never heard (`PipelineRun.audio_derived`). This is the half that keeps it
  *safe*, and it holds even if the phone is old, misconfigured, or wrong.

Typed input is untouched: a person at a keyboard is authenticated by the bearer
token they typed it with, and this gate is about who is speaking in a room where
the microphone is open to whoever is standing there.

A hostile client could omit the flag — but a client holding the token can
already send any transcript it likes. This closes the **accident**, not the
attack, and that distinction is stated at the point of use rather than implied.

## 11. The toolbox costs 59% of the context window, measured

`llm: options: num_ctx: 8192` is the whole window a turn lives in.
`ToolRegistry.as_openai_schema()` returns **every** registered tool — no
filtering, no relevance selection, no budget — and it is posted on each of up
to `max_tool_rounds` rounds.

Measured by `jarvis-core/tests/test_prompt_budget.py` on a stock install:

| | tokens | share of 8192 |
|---|---|---|
| tool schema | ~4,850 | 59% |
| system prompt, **empty** house | ~720 | 9% |
| together | ~5,570 | **68%** |

That is what is spent before the house has a single entity in it, before any of
the 20 turns of history, before the user's sentence and before the answer. On a
real house `house_summary` adds up to 120 more entity lines on top of it.

This is a **recorded position, not an accepted one**. The test is a ratchet: it
pins where this actually is so it cannot quietly get worse, and every tool
anyone adds is paid for by every turn — including the turns that could not
possibly use it. The fix is per-turn tool selection, not a bigger `num_ctx` and
not a bigger ceiling in the test; `SCHEMA_TARGET` in that file is what the work
has to reach and is deliberately not asserted, because a test that fails until
someone writes a feature is a test people learn to ignore.

Two things already landed against it: tool results are capped before they reach
the model (`truncate` was written for that and had only ever been applied inside
`build_yaml_tool`, so every built-in tool's result went into the window whole),
and `list_entities` — the tool `TOOL_RULES` tells the model to call whenever a
name fails to resolve — is bounded and reports the true total beside the
shortened list.

The chars-per-token divisor is an estimate, deliberately pessimistic for JSON. A
real tokeniser will report more, not less.

## 12. The design system is generated from one file, with three deliberate seams

`design/tokens.json` is the only place a colour, size, font, radius, shadow or
duration is typed; `design/build.py` generates the CSS, the TypeScript, the
desktop palette, the Android `JarvisTokens.kt`, a Compose `JarvisTheme.kt` and
the XML resources, and `scripts/verify/token_lint.py` refuses a hard-coded value
in app code. Three places knowingly stop short of "generated":

**The orb palette is drift-checked, not rewritten.** `SiriPalette.kt` and the
palette comments in `Orb.svelte`'s shader are declared as `color.orb.*` in the
JSON, and `build.py --check` fails if either differs — but neither file is
generated. `android-app/tools/reactor_orb_test.py` (1,400 lines) already pins
those two files to each other and to the shader's arithmetic; regenerating them
would mean rewriting that spec for no gain in truth.

**The phone keeps its own type and spacing scales.** `type.android` (sp) and
`space.android` (dp) sit beside the console's rem scales in the same file.
`tools/type_scale_test.py` pins the phone's numbers as "a rename, not a
redesign" and the console's floor is 0.7 rem; one shared scale would move one
surface or the other visibly, which is a decision for M03/M08, not a side
effect of moving the source of truth.

**Compose is enabled without a local compiler.** `JarvisTheme.kt` needs
Compose, so `compose = true`, the Kotlin Compose plugin and the BOM are in the
Gradle build — and this host has no JDK, so nothing here has compiled it. The
configuration is the standard one for Kotlin 2.0.21 / AGP 8.7.3; the first
`./gradlew assembleDebug` is milestone M08's job, and until then the theme file
is claimed as "generated", not "compiles".

**The lint ratchets rather than fails outright.** 340 legacy hard-coded values
in 38 files are recorded in `design/token-lint.baseline.json`; a file may not
exceed its count and a new file may have none. Failing the whole tree on day
one would have made the milestone unmergeable until M03 and M08 finished; the
ratchet keeps the rule enforceable now and makes "baseline empty" the
finishing line those milestones check.

## 13. The browser container trades Docker's syscall filter for chromium's sandbox

`jarvis-browser` runs with `security_opt: [no-new-privileges:true,
seccomp:unconfined]`. That is a real reduction and it buys back exactly one
thing: `clone(CLONE_NEWUSER)`, which chromium's own sandbox needs and which
Docker's default seccomp profile blocks.

The choice is between two layers and you cannot have both here:

* **Docker's default seccomp**, a broad filter over ~44 syscalls, written for
  containers in general.
* **Chromium's sandbox**, written for exactly this service's job — opening
  pages nobody in this house wrote, in a renderer that is assumed to be
  exploitable.

Keeping the first meant setting `BROWSER_CHROMIUM_NO_SANDBOX=1`, and a renderer
with no sandbox parsing hostile HTML is the worse of the two. Measured on this
host, the alternative was not theoretical: with the default profile chromium
refused to start at all (`No usable sandbox!`) and every fetch failed.

Everything else stays: uid 10003, `cap_drop: [ALL]`, `no-new-privileges`, `/tmp`
on tmpfs, no host mounts, and chromium's sandbox ON.

The better answer is a chromium-specific seccomp profile — Docker's default
plus the clone/unshare flags, which is what Docker's own `chrome.json` example
is. This repository does not carry one because it is a thousand lines of JSON
that drifts with every Docker release, and a stale copy of a syscall allowlist
is worse than a documented absence. If you maintain one, point `security_opt`
at it; nothing else has to change.

## Licensing notes

* Piper was archived Oct 2025 → OHF-Voice/piper1-gpl (GPL-3.0; MIT→GPL).
* openWakeWord code is Apache-2.0; the official models are CC BY-NC-SA 4.0
  and English-only (personal use fine). Train your own custom wake words and
  do not redistribute the NC models. See
  [`docs/wake-word-training.md`](docs/wake-word-training.md).
* microWakeWord on Android is experimental and battery-heavy — third-party
  apps get no low-power DSP path, which is why the wake gate exists.
