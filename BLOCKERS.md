# BLOCKERS.md — what needs you, not more work from me

Only two kinds of thing belong here: something that needs hardware or a
service this account cannot reach, and something that needs a decision only the
operator can make. Everything else is a milestone, not a blocker.

---

## 1. ~~Docker access for `jarvisdev`~~ — **resolved 2026-08-25**

Kept, rather than deleted, because three claims elsewhere were written around it.

`jarvisdev` is now in the `docker` group and the socket is reachable:

```
$ id -nG | grep docker        → docker
$ docker run --rm hello-world → Hello from Docker!
$ docker compose ls           → jarvis running(3), jarvis-core restarting(1), running(6)
```

What that unblocks, and where it is now tracked:

* **The coding agent's live containment check** — M19 runs it for real.
* **SearXNG**, so `evals/research_eval.py --backend live` (the Scripted claim in
  `docs/verification.md`) can be run here: `docker compose --profile search up -d
  searxng`, then `SEARXNG_URL=http://127.0.0.1:8888`.
* **The whole compose-native testing addition** — M28 and M29.

Two things it immediately surfaced, both real and both fixed under M28: `photon`
restarts in a loop, and `jarvis-web` reports unhealthy.

## 2. A faster model for the voice path, or a GPU

**Needed by:** the full-mode latency threshold (median round trip ≤ 2 s).

Measured on this host: a spoken, tool-using turn takes 15–20 s end to end, of
which ~11 s is streaming and recognising the audio and ~6 s is the model. The
model is `qwen3.8-27b` on a remote llama-swap; there is no GPU on this box and
faster-whisper runs on four shared vCPUs.

Corrected on 26 August with the operator's numbers: the chat model is
`qwen3.8-27b` at ≈75 tok/s with a 256k context, which is fast enough — the
model's generation is not the wait. What is: recognising the audio on four
shared vCPUs (~11 s), prefilling a large system prompt into a 256k window on
every turn, and starting synthesis. M60 took the parts of that this
repository can change — the prompt prefix kept on the server and ordered
stable-first, the first sentence spoken before the reply is finished, whisper
int8, `llm.fast_model` on the voice path when the operator sets one, and a
switch to drop the reasoning block on a spoken turn (measured: 3.1 s median
at 87 % intent against 5.9 s at 93 %, so it ships on) — and the full-mode
median moved from 6.67 s to 2.87 s with reasoning kept (11:54, the record) (10:27; the rig measures to
`run-end`, so early speech is not in that number). What remains is hardware,
and still needs one of:

* a small model (3–8 B class) served at the same endpoint for the voice path,
  with the large one kept for research and coding; or
* a GPU on the model host, or on this one.

Which of those to do is an operator's decision about cost and quality, so the
threshold is reported as missed in `docs/LIVE_TEST_REPORT.md` rather than
quietly lowered.

The same hardware fails one more scenario the same way. `interactions-
proactive-moment` hands "go through every sensor and tell me what looks
wrong" to the background and expects the finished-task moment within 240 s;
the task engine takes one model round trip per sensor, and on the night of
26 August the job was a third of the way through when the budget ran out
(it finishes, minutes later — the moment then arrives, now that the inbox is
switched on). The budget is the scenario author's idea of a small job and is
left as it is: a faster model is the fix, not a longer wait.

## 3. Everything that needs a phone, a wall panel or a microphone

**Needed by:** M08's device backlog, M22's phone automation, and the rows in
`docs/verification.md` marked **Unproven**.

No device of any kind is reachable from this run, by instruction. The Kotlin is
verified by its JVM-side mirrors and (once a JDK is installed) by
`./gradlew assembleDebug`, lint and Robolectric; the on-device gates are listed
in `docs/ANDROID_DEVICE_TESTS.md` with the exact steps for whoever has the
hardware.

The same applies to a real microphone in a real room: the live rig synthesises
the user's speech and delivers it through the real audio paths, which proves
the pipeline but not the acoustics of your kitchen.

The toolchain M08 installs under `$HOME` (a JDK and the SDK) is on this host,
which CLAUDE.md's "cannot be built here" predates: `./gradlew assembleDebug`
and `lintDebug` pass with M61's Kotlin, and its JVM tests pass. What still
needs a handset is ADT-036…038 and, for the last six Tasker rows (a photo, a
scan, the inbox, the call log, a hang-up, a tag), ADT-040…046; ADT-039 is the
golden re-record.

## 4. Accounts and keys for the things that reach the outside world

**Needed by:** M38 (channels), M39 (calendar and mail), M40 (cloud providers,
optional), M41 (Claude Code as a coding backend), M47 (catalog sources).

None of these block the work — every one is built and verified against a local
fixture (a mock channel server, a Radicale container, a mail sink, a mock cloud
provider, a scripted stand-in that speaks Claude Code's protocol, a fixture
catalog). What they block is *your* Jarvis actually using them, and each needs
something only you can hand over:

| Item | What it needs | Default until then |
|---|---|---|
| Telegram | a bot token, and your own user id for the allowlist | the adapter loads, the allowlist is empty, and every sender is refused |
| Signal | a `signal-cli` registration (a phone number) | as above |
| Calendar | a CalDAV URL and credentials | the integration is inert |
| Mail | IMAP/SMTP host and credentials | the integration is inert |
| Cloud model providers | an OpenAI / Anthropic / Google / OpenRouter key | **off** — local-only is a complete configuration, and a request carrying memory or notes is refused cloud routing even after you add one |
| Claude Code backend | an Anthropic API key | **off** — coding tasks use the local agent |
| A vision model (M56) | a multimodal GGUF behind llama-swap, named `house-vision` (or `VISION_MODEL` for the rig) | the vision integration is on with no cameras and no served model: a look says it could not, and the MODELS panel says "not served" |
| Catalog sources | the list of origins you are willing to install from | empty, and nothing installs from an unconfigured origin |

Two of these are deliberate exceptions to "everything local", authorised by the
brief that asked for them: cloud model providers and the Claude Code backend.
Both are off until you supply a key, both are logged when used, and neither can
receive a request tagged `local-only`.

### An Anthropic API key, if you want the delegated coding backend (M41)

**Needed by:** `code: backend: claude-code`, and nothing else.

This is the single deliberate exception to "nothing goes to the cloud" in the
whole project: a delegated coding job sends the repository's contents to
Anthropic. Everything else about the job is unchanged — same sandbox, same
approval gate, same checks deciding whether it is green — but the code leaves
the network, and that is your decision rather than a default.

It ships **off**. With no key it refuses to start and says why. CI proves the
plumbing, the containment and the gate against
`testing/fixtures/fake_claude_code.py`, which speaks the same `--print
--output-format json` protocol; what a key would add is the model's actual
output, and no test here pretends to have it.

To turn it on:

    # secrets.yaml
    anthropic_api_key: sk-ant-…

    # configuration.yaml, under code:
    backend: claude-code          # or leave it `local` and ask per task
    claude_code:
      enabled: true
      api_key: !secret anthropic_api_key


## 5. The motion taste checkpoint (M44)

**Needed by:** M44's own completion clause — "the milestone is not done until
they have signed off", and the notes worked through as a second pass.

The harness proves motion is smooth, token-generated, reduced-motion-safe and
never blocking. It cannot prove it is *cool*, which is the half that matters,
so four headless Chromium recordings are waiting in `docs/motion-review/`:

| File | What it shows |
| --- | --- |
| `1-boot.webm` | the staged boot sequence, subsystems coming online |
| `2-orb-states.webm` | idle → listening → thinking → speaking |
| `3-task-running.webm` | a task view: streaming cursor, tool nodes, resolution |
| `4-navigation.webm` | shared-element transitions between pages |

They play in any browser (`webm`/VP8, no GUI or device needed). What would
unblock it is notes — "the boot is a beat too slow", "the thinking state reads
as an error", "the orb should breathe, not pulse" — at whatever length. A
second pass follows; the milestone is ticked for the buildable half only, and
`docs/motion-review/README.md` says the same thing next to the files.
