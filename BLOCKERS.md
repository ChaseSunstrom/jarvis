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

Measured on this host (24 Aug): a spoken, tool-using turn took 15–20 s end to end, of
which ~11 s was streaming and recognising the audio and ~6 s the model. By 27 Aug, after
M60/M70/M74, the median over 63 scenarios is 4.17 s with a p95 of 21.4 s — still over. The
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
median moved from 6.67 s to 2.87 s with reasoning kept (11:54, the record;
the rig measures to `run-end`, so early speech is not in that number), and
measured 3.17 s at 18:32 on the stack with the broker and the search engine
up beside it — the smoke set alone runs at 2.5 s. What remains is hardware,
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
left as it is: a faster model is the fix, not a longer wait. One thing that
was not hardware is fixed: on the evening's report run the job ended
`jarvis_task_failed` because a later scenario restarted the core under it;
a restart turn now waits for running tasks first (23a8d5b).

## 3. Everything that needs a phone, a wall panel or a microphone

**Needed by:** M08's device backlog, M22's phone automation, and the rows in
`docs/verification.md` marked **Unproven**.

No device of any kind is reachable from this run, by instruction. The Kotlin is
verified by its JVM-side mirrors and — the toolchain is on this host now, see below — by
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

## n8n (M77): the key and the assistant's shape

**Needed by:** M77's live half — the operator's `N8N_API_KEY` and what `/assistant` on their
server answers (a chat webhook, or the built-in assistant behind a session).

Jarvis's n8n tools are built and tested against a fake n8n. To run against the
house's server put these in `jarvis-core/.env` (never in the repository):

    N8N_URL=https://n8n.tail05d9af.ts.net
    N8N_API_KEY=<Settings → n8n API on the n8n server>
    N8N_ASSISTANT_URL=https://n8n.tail05d9af.ts.net/assistant   # if that is where it answers

and say what `/assistant` is: a Chat Trigger's webhook (Jarvis posts
`{chatInput, sessionId, action: "sendMessage"}` and reads `output`), or
something else — its URL, auth and reply field decide `ask_n8n_assistant`.
`bash scripts/verify/m77-n8n.sh` then proves the connection.

## ~~The orchestrator image cannot reach the Debian mirror (M82)~~ — **resolved 2026-08-27**

The build reaches Debian and GitHub on the host's network now (`build: network: host`), git and
curl are in the image, and OpenCode 1.18.23 — the project moved to anomalyco/opencode and the old
0.6.4 asset no longer unpacked, which the build's `|| echo WARNING` had hidden — answers a prompt
through the house's own gateway from inside the container, with the provider config the broker
writes at startup. Kept for the record; the entry below is as it was.

**Needed by:** M82's coding worker — a route from BuildKit to `deb.debian.org` and GitHub
(the running orchestrator, on the host network, reaches both; the image build does not).

`jarvis-orchestrator`'s image needs `git`, `curl` and `unzip` from Debian and
OpenCode from GitHub. On the night of 26 Aug 2026 `apt-get update` against
`https://deb.debian.org` did not finish in fifteen minutes from inside a
container (a fresh `python:3.12-slim` timed out at five), while the same
mirror served the core image at 21:05. Until a container can reach the
mirror, the image builds without the tools and every remote coding job fails
at once with "opencode binary not installed" — which the card now says within
a poll (M82) instead of sitting at "queued". Check from the host:

    docker run --rm python:3.12-slim sh -c 'apt-get -o Acquire::ForceIPv4=true update'
    docker compose -f docker-compose.yml --profile agents build --no-cache jarvis-orchestrator

The local coding job (`start_coding_job`, the sandbox) does not need the
orchestrator and is unaffected.

## CI's job logs need a token this host does not have

Three times on 27 Aug 2026 (6c816c8, c44b168, 79bb9b4 → 9d6be59) the
`python · jarvis-core` leg of the `CI` workflow went red with nothing on the
check run but "Process completed with exit code 1" — a full four-minute run,
no `FAILED` or `ERROR` row even with `-rfE`, and nothing from the step that
now annotates pytest's tail. The public API refuses the job log ("Must have
admin rights to Repository", 403) and so does `gh`, which is installed but
not logged in. The same tree passes here in full under Python 3.12 (3563
tests at 10:21; the changed files, 472, at 12:10), so whatever it is, it is
CI's environment and only the log says what.

**Needed by:** every red `python · jarvis-core` leg since 79bb9b4 — M23's
claim that CI is green, and any tick that leans on it. What it needs: `gh
auth login` on this host (or a token with `actions:read` in `GH_TOKEN`); then
`gh run view <run id> --log-failed` reads the step. Until then a red core leg
cannot be diagnosed from here, only reproduced by guesswork.
