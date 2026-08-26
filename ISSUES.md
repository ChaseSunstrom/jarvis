# ISSUES.md — defects the live rig found

Every entry here was found by **talking to Jarvis**, not by reading its code:
`testing/live/` synthesises a user's speech, sends it through the real entry
points, and checks what the house did and what came back. The rule from
`PROCESS.md` applies — a defect gets an entry *and* a regression scenario, and
then it gets fixed. An entry that names no regression scenario has to say why
one cannot exist.

Severity: **critical** (data loss, a safety gate bypassed, an outright wrong
action) · **major** (a user notices and is misled) · **minor** (a user notices
and is not misled).

---

## "Note that…" was remembered, not noted

severity: major
status: **fixed** (M27 — `MEMORY_REQUESTS` no longer counts a note phrase; the
note-taking skill's rule is the words, not the length)
Regression: `notes-write-and-find`
Found by: the exploratory pass (`notes-then-recall`) and, the first time it
was ever selected, the scenario above — it had been `gated-on: M16` and
`--implemented-only` skipped every gated scenario forever

"Make a note that the boiler pressure was 1.2 bar today" produced a memory
entry and no note. Three things lined up: the bundled note-taking skill told
the model that one sentence is a memory ("the boiler service is due in
March"), the model read it (55 seconds, one `use_skill` round trip) and called
`remember`; the memory store accepted the write because "note that" and "make
a note" were on its list of phrases that mean *the user asked for this to be
remembered*; and the reply said "Noted, Sir". The user got a standing fact in
every future prompt they never asked for, and `note_search` had nothing to
find.

The store now refuses a note phrase — `{"stored": false, "use_instead":
"note_create"}` with a message naming the tool — unless the sentence also
says *remember*, and the skill says the words decide: "note that…" is a note
however short. `evals/test_routing.py` pins the store's two lists to the
router's definition so they cannot drift apart again.

## The Wyoming containers logged a reset on every `describe`

severity: minor
status: **fixed** (M27 — `wyoming_info` sends EOF and waits for the peer
before closing)
Regression: `stack-logs-clean`, which fails the run on any ERROR-level record
in a container's log
Found by: `stack-logs-clean`, red on a conversation that had gone perfectly

`whisper` and `piper` both logged `ERROR:asyncio:Task exception was never
retrieved … ConnectionResetError('Connection lost')` each time something asked
them to describe themselves: the client read the `info` event and closed the
socket while the server's own `drain()` was still in flight. Nothing was
wrong with the conversation; the check that reads the logs is right to
refuse it, because a log full of resets is a log nobody reads. The client now
hangs up politely (FIN, then read until the server closes, bounded at a
second), and `FakeWyomingServer` records how each connection ended so
`test_wyoming_info_hangs_up_politely` pins "eof" rather than a reset.

## `ask_user` said the question was on a phone that was not there

severity: minor
status: **fixed** (M27 — the tool's description no longer promises a phone)
Regression: none — the wording is the model's paraphrase of a tool
description, and no deterministic check can pin a paraphrase; the
exploratory probe `ambiguous-room` is where it was seen and where it will be
seen again
Found by: the exploratory pass (`ambiguous-room`)

"The question is on your phone, Sir" — with no phone connected, in a text
conversation, one turn before `tool-that-is-off` correctly said "no device of
yours is connected right now". The description said the question "appears on
their console and their phone"; it says now that it is put to them where they
are, and to say it is waiting rather than where.

## Semantic recall was configured, degraded silently, and had never run

severity: major
status: **fixed** (M33 — `jarvis-embeddings`, and the config now points at it)
Regression: the recall measurement in `evals/memory_eval.py`, and
`scripts/verify/m33-embeddings.sh` asserts the before and the after
Found by: reading what the model server actually serves

`memory/vectors.py` embeds through the LLM client. This deployment's model
server is llama-swap, which serves two chat models and answers `/embeddings`
with:

    {"error":"no router for requested model","src":"llama-swap"}

The code handles that correctly — one log line, then keyword search, exactly as
its "Degrading" section promises. So nothing was broken, nothing was logged
twice, and the feature had never worked on this host. Measured on six queries
that share no content word with the note that answers them ("where do we keep
the caffeine" against "I take my coffee black"), keyword search returned
**nothing at all**: recall@1 0%, recall@3 0%.

With a CPU embedding service of its own: **100% and 100%**.

Two things this hid, both worth naming:

* **The graceful degrade is why nobody noticed.** A feature that falls back
  silently is a feature that can be absent for months. The fallback is still
  right — a search that errors is worse than a search that is dumber — but the
  measurement that would have caught it did not exist until now, and that is
  the actual defect.
* **Had it worked, it would have been costing the voice path.** An embedding
  request through llama-swap evicts KV cache: writing a note would have made
  the next spoken sentence slower. That is the reason it is a separate CPU
  service rather than a model name to pull.

---

## The browser container could not open a page, and said it was healthy

severity: critical
status: **fixed** (`jarvis-browser/Dockerfile`, `jarvis_browser/browser.py`,
`jarvis_browser/app.py`, `jarvis-core/docker-compose.yml`)
Regression: `research-javascript-page` (live), and the `/healthz` check in
`scripts/verify/m31-browser-service.sh`
Found by: M31, the first time anything asked the running service to fetch a page

`jarvis-browser` reported `{"status":"ok","backend":"PlaywrightBackend"}` and
answered every `/fetch` with a 500. Nothing in the repository noticed, because
every research test in it talked to `testing/live/fixture_browser.py` — a
stand-in that serves the same two routes and is not a browser. The operator's
Jarvis could not read a web page at all.

Three separate faults, each hiding the next:

1. **`playwright install-deps chromium` installed nothing.** Playwright 1.49
   does not recognise Debian trixie (which `python:3.12-slim` now is), falls
   back to its Ubuntu 20.04 package list, and that list names `ttf-unifont` and
   `ttf-ubuntu-font-family` — neither of which exists in trixie. apt fails the
   whole transaction on one unavailable package, so *none* of chromium's
   libraries were installed. The build printed its warning and carried on, as
   designed. Chromium died with `libglib-2.0.so.0: cannot open shared object
   file`. The Dockerfile now installs the library list by name, and **launches
   the browser at build time** so an image that cannot browse fails loudly.
2. **A launch failure was a 500.** Playwright raises its own error type, which
   nothing handled, so a broken image produced a stack trace instead of the
   documented 502 with a reason. It is a `BrowserError` now, and its message
   says what a missing shared library means and what to do about it.
3. **`/healthz` did not ask.** It reported the backend class name, which is a
   fact about the code rather than about the browser. It now launches once,
   caches the result, and returns `browser: ok` or the error. The status stays
   `ok`: the security core is genuinely unaffected by a missing browser, and
   the Dockerfile documents building without one. What must not happen is that
   nobody can tell.

And one more underneath, found once the libraries were there: chromium's own
sandbox needs an unprivileged user namespace, which Docker's default seccomp
profile blocks. The service is the thing that opens pages nobody here wrote, so
the renderer sandbox is the layer worth keeping: `seccomp:unconfined` is set
and everything else stays (non-root, all capabilities dropped,
no-new-privileges, tmpfs). `DEVIATIONS.md` records the trade and what a better
answer would look like.

---

## A question asked through the notification channel, and denied, sounds like nonsense

severity: minor
status: **open** — intermittent, and only visible when the question is refused
Regression: the `garbled` case in `evals/intelligence/prompts.yaml`
Found by: the M26 scorecard, which denies every held action by design

Asked something unintelligible ("turn on the frunge in the blorridor", spoken
over a fan at 5 dB SNR, which Whisper rendered as "turn on the front in the
floor door"), Jarvis sometimes asks *"Front door or garage door?"* in the reply
— which is right — and sometimes raises the question as a gated notification
instead. When the gate is answered no, what the user hears is:

> I have no record of which site you mean, so my question asking for the
> handbook's address is waiting on your confirmation before it can reach you.

Which is a sentence about the plumbing. A person asked a question and was told
about an approval queue.

Two things are true and neither is a bug on its own: asking through the
interactions channel is a real thing to do (it leaves a trail, and it reaches
somebody who is not in the room), and a Tier-gated action that is refused has
to say so. What is wrong is the combination — a clarifying question about the
turn in progress belongs in the answer, where it costs nothing and arrives
immediately.

Left open rather than fixed because the fix is a judgement about which channel
a question belongs on, and that is M17's territory rather than a one-line
change here. The eval names it whenever it happens.

---

## "Don't wait for it" was ground through inline, sometimes

severity: minor
status: **open** — intermittent, and no instruction is missing
Regression: `task-background-plan` (live suite) and the `task` prompt in
`evals/intelligence/prompts.yaml`
Found by: the M26 scorecard, on the third of four runs

"Go through every sensor in the house one at a time, work out which look wrong,
and write it up. Don't wait for it — tell me when it's done." was handled by
six inline tool calls and a note, in the same turn, with the user waiting. Two
runs earlier and one run later the same sentence created a background task, as
it should.

`config/prompts/jarvis.txt` rule 6 already says the thing that would fix it, in
the strongest words available: *"If they SAY not to wait, or to be told when it
is done, that decision is already made: hand it over before you start. Grinding
through it inline is the one thing they asked you not to do, however quick each
step looks."* Adding more words to a rule the model reads and sometimes ignores
makes the prompt longer and the assistant no better, so nothing was changed.

What it costs when it happens: the user stands there for the length of the job
instead of being told it started. What it does not cost: correctness — the work
was done and the note was written.

It is inside the routing floor (0.85) rather than outside it, deliberately: a
floor of 100% on eight prompts would fail this milestone on a model's bad day,
and a floor that cannot be met stops being read. The scorecard names the case
every time it happens, which is the point.

---

## A style guide read before every answer, at a round trip each

severity: minor
status: **fixed** (`jarvis-core/jarvis/integrations/skills/__init__.py`, `index_block`)
Regression: `evals/intelligence/run.py` — the routing section, whose two
`answer` prompts fail if an ordinary question goes through a skill
Found by: the M26 scorecard, not by anybody reading the prompt

The skill index told the model to "call use_skill with the name to read one
before doing anything it covers", and `house-style` describes itself as "how
Jarvis should answer in this house — length, address, and when to say nothing".
That covers every answer, so the model read it before every answer: "which room
is the coffee machine in?" cost a tool call and a second round trip through a
30B model before a single word came back.

It was obeying its instructions exactly. The header now says that reading one
costs a round trip and to read a skill when the request is ABOUT what it
covers. Nothing else changed — the skill, its description and `use_skill` are
untouched.

The number this moved is routing accuracy on the intelligence scorecard, from
6/8 to 8/8, because two prompts whose right answer needed no tool at all were
being routed through a document. The latency it saves is on every turn of every
conversation, and no test was ever going to notice it: each of those turns was
correct.

---

## Three calls that would have crashed on Android 10, in shipped code

severity: major
status: **fixed** (`audio/AudioAttention.kt`, `assist/LocalTranscriber.kt`)
Regression: `./gradlew lintDebug`, which is blocking as of M08
Test: the lint step itself — this is the class of defect lint exists for, and a
device test would find it one phone at a time

`minSdk` is 29. Three call sites required API 31:

* `AudioManager.removeOnModeChangedListener` and the `OnModeChangedListener`
  class it takes — the call-detection path, reached whenever a turn ends;
* `SpeechRecognizer.createOnDeviceSpeechRecognizer` — every on-device
  transcription.

Each is an immediate `NoSuchMethodError` on Android 10 and 11, which is a third
of the phones this app supports.

Two of the three were *already guarded* and lint could not see it: the listener
is only non-null on API 31+ because `start()` returns early below it, and the
recogniser is behind an `isAvailable()` that checks the version one stack frame
away. The third had no guard at all. All three are now explicit — a version
check where the guard was invisible, `@RequiresApi` on the class, and a
suppression that names the reason where the guard is real.

They were found the day lint became blocking, having been reported and ignored
by CI (`continue-on-error: true`, `|| true`) for the life of the file. A check
that cannot fail a build is a check nobody reads.

---

## A model server that stalls made Jarvis wait for ever

severity: critical — **fixed**
status: **fixed** (`jarvis-core/jarvis/llm/ollama.py`, `call_timeout`)
Regression: `subagents-parallel-work`
Test: `jarvis-core/tests/test_llm.py::test_a_stalled_model_call_is_abandoned_rather_than_waited_on`

A four-part question, three tool calls, and then nothing. Ten minutes of
nothing, with `llm: timeout: 120` in the configuration and the model server
answering a one-word request in 0.2 s the whole time.

`llm.timeout` is httpx's timeout, and httpx's is **per read**: every byte
resets it. llama-swap sends an SSE keepalive comment about once a second while
its backend is busy, so the read timeout never fires and the call never ends.
There was no other bound anywhere: not in the client, not in the agent loop,
not in the API layer. A person talking to Jarvis got no answer and no error —
only silence, for as long as the server felt like being quiet.

Recorded as **critical** even though nothing was damaged, because of what it
does to the product: an assistant that can hang for ever on one unlucky turn is
one nobody can rely on in a room, and the failure is invisible from every side
— the server thinks it answered, the client is still politely waiting, and the
logs show a successful `200 OK`.

`OllamaClient.call_timeout` is an absolute deadline for one whole call —
`max(llm.timeout, 300s)`, so raising the read timeout can never lower the real
bound — applied at `ChatStream._collect`, which is the one place every consumer
of a model call goes through. A stall is now an error that says it was a stall
and not an outage.

Found by the live suite, four levels down: a scenario for subagents timed out,
which looked like a delegation bug, which looked like a tool bug, and was
neither.

---

## "How is that going?" — "I have no way to check on the job's progress"

severity: major
status: **fixed** (`jarvis-core/jarvis/llm/tools.py`, `task_status`)
Regression: `coding-fix-failing-tests`
Test: `jarvis-core/tests/test_tasks.py::test_task_status_reports_what_is_running`

Asked, mid coding job, how it was going, Jarvis said:

> "I have no way to check on the job's progress from here, Sir — I can only
> cancel it if you wish."

Which was true. It could **start** background jobs (`run_background_task`,
`start_coding_job`, `deep_research`) and it could **stop** them
(`cancel_task`), and there was no tool between those two. Every screen in the
system has shown live task progress since M12 — the console, the phone, the
task dock — so the gap was invisible to anyone testing with a screen in front
of them, and total for anyone in a room talking to it.

`task_status` reports what is running, how far through it is, what step it is
on, and the result of the last few if nothing is running — because "how did
that go" is the same question one minute later.

Found by the live suite the first time a coding job was long enough to ask
about.

---

## Nonsense in, a confident answer out

severity: minor
status: **fixed** (`jarvis-core/config/prompts/jarvis.txt`, rule 3)
Regression: `house-garbled`
Test: the scenario itself — this is a behaviour, and the judge is what can see it

Four seconds of a sentence that means nothing ("Fluxion the grendel past the
kitchen wibble"), and across three runs Jarvis answered three different ways:
once by asking what was meant, once by reporting the kitchen's state as though
the question had been about the kitchen, and once with

> "Noted, Sir — though I confess my records contain no Fluxian, no grendel, and
> certainly no wibble… Shall I make a note of Fluxian's movements?"

which is charming and is not a request for clarification. It never *acted* —
the safety property held every time — but treating nonsense as a topic and
answering around it is how a misheard command becomes a wrong action one step
later.

The prompt had eight operating rules and none of them was about the thing that
actually reaches the model: a transcript, from a room, which regularly contains
sentences nobody said. Rule 3 now says it — if the words do not resolve into a
request, say you did not catch it and ask again; do not answer around it, and
never act on the nearest thing it resembles.

Recorded as minor because nothing was ever done to the house, and recorded at
all because the *inconsistency* is the finding: three runs, three behaviours,
one of them the intended one.

---

## A reply that opened with an ellipsis was spoken as nothing at all

severity: major
status: **fixed** (`jarvis-core/jarvis/voice/pipeline.py`, `speakable()`)
Regression: `voice-room-tone`
Test: `jarvis-core/tests/test_voice.py::test_a_leading_ellipsis_is_not_sent_to_the_synthesiser`

Four seconds of room tone. Whisper heard `...  ...  ...`, and Jarvis answered:

> "...? Shall I fetch something, Sir, or were you merely testing the silence?"

Which is a good answer, and it was never spoken. The turn failed with
`tts-failed: # channels not specified`.

Piper splits its input into sentences and synthesises each one. The leading
`...?` phonemises to nothing, its wav writer closes having written no frames,
and **the whole request dies** — including the perfectly speakable sentence
after it. Measured against `wyoming-piper:2.3.1`: that text produced an error
and no audio, the same sentence without the ellipsis produced 183 KB.

A model reacting to a sound it could not make out opens with an ellipsis often,
so this is not an edge case: it is what Jarvis says when the room is quiet and
something rustles.

`speakable()` now collapses whitespace and drops any sentence with no letter or
digit in it before the text reaches the synthesiser; if nothing pronounceable
is left the turn simply does not speak, which is not an error. The reply itself
is untouched — the console, the transcript and the archive still show the
ellipsis, because that is what Jarvis said.

Found twice over, and that is the point of M29: the scenario failed on the
missing audio, and the container log gate failed the run independently with
piper's traceback. Neither had ever been visible, because until this milestone
nothing in the suite read what the containers said about themselves.

---

## Four the *stack* found, none of which any test could see

`ISSUES.md` says every entry here was found by talking to Jarvis. These four
were found by **looking at the containers while it talked** — M29's log gate
and the first attempt to run a scenario against the deployment rather than
against a copy of it. Each was true for days with every suite in this
repository green, which is the argument for the milestone.

### The model-server sensor polled `/v1/v1/models` and 404'd every 30 seconds

severity: major
status: **fixed** (`jarvis-core/jarvis/config.py`, `_join_url`)
Regression: the stack log gate (an ERROR-level record fails the run) is the
general net; the specific one is
`jarvis-core/tests/test_core.py::test_env_url_does_not_repeat_the_segment_where_the_two_meet`

An earlier fix replaced `!env_var` (which lost the sensor's path) with
`!env_url` (which always applies it). The mirror-image bug: the `llm:` block
requires `LLM_URL` to be a **base** url, an OpenAI-compatible base ends in
`/v1`, and applying `/v1/models` to it gives `https://host/v1/v1/models`. The
console's model-server readout has been reporting nothing since, while Jarvis
held conversations with the very server it said it could not see.

`_join_url` now collapses the segment where the two meet. `sensor.model_server_models`
reads `qwen3.6-35b` for the first time.

### Two Jarvises on one broker took each other down, 22 times a minute

severity: major
status: **fixed** (`jarvis-core/jarvis/integrations/mqtt/client.py`)
Regression: `jarvis-core/tests/test_mqtt.py::test_repeated_short_sessions_say_the_thing_the_tracebacks_never_do`

68 disconnects in three minutes, each with a twenty-frame traceback, every time
this repository's own test harness started a jarvis-core beside the container
one. MQTT allows one session per client id and the default id was the literal
string `jarvis`, so the broker evicted the first client, which reconnected and
evicted the second, forever. Neither process could tell: from inside, each one
only ever saw "disconnected".

Three changes: the default id is now derived from the hostname *and* the config
directory (stable for one installation, different between two — with
`network_mode: host` the hostname alone distinguishes nothing); a repeat
failure no longer prints a traceback, only the first does; and three
connect-then-drop cycles inside ten seconds each logs the sentence the
tracebacks never said — *this id is in use by another Jarvis*.

### `docker compose watch` synced code into a directory that does not exist

severity: major
status: **fixed** (`jarvis-core/docker-compose.yml`, `docker-compose.yml`)
Regression: `jarvis-core/tests/test_packaging.py::test_every_watch_rule_syncs_into_that_image_workdir`

Every `develop: watch:` rule written in M28 targeted `/app/...`; all three
Python images run from `/srv`. An edit would have synced into a path nothing
imports, the service would have restarted, and it would have restarted with the
old code — a dev loop that silently does nothing, which is worse than not
having one, because you conclude the change had no effect.

### The config directory locked its own author out

severity: major
status: **fixed** (`jarvis-core/docker-compose.yml`, `JARVIS_UID`/`JARVIS_GID`)
Regression: none — this is a deployment property, and the thing that would have
caught it is exactly what M29 added: trying to *use* the stack rather than
describe it.

`jarvis-config-init` chowned the whole bind-mounted `./config` to the image's
baked uid 10003. That directory contains `configuration.yaml`,
`automations.yaml` and `scenes.yaml` — **tracked files in this repository** —
so after any `up`, the person working on this checkout could no longer edit
their own config, and `git checkout` on those paths would have failed too. The
uid is a variable now, `.env` sets it to this host's user, and the image's uid
remains the default for anyone who does not.


---

## The reply carried every round's words, not the answer

severity: major
status: **fixed** (`jarvis-core/jarvis/llm/agent.py`, `ConversationResult.preamble`)
Regression: `house-light-on`
Test: `jarvis-core/tests/test_llm.py::test_words_written_before_a_tool_ran_are_not_the_answer`

Heard, in one breath, on the voice path:

> "The bed light is already off, sir. The bed light is now off, sir."

Both sentences were real. The model guessed in its first round, called
`turn_off`, and answered in the second; `converse` concatenated every round's
text into the reply, so the guess and the answer were spoken as one
contradictory utterance. On a screen you could just about tell them apart. Out
loud you cannot.

The same mechanism made the narrated-call correction audible:

> "You're right, sir — I described the check without running it. Let me
> actually look now."

which is Jarvis apologising to itself in front of the user.

Fixed by recording text from rounds that then called a tool (or that a
correction replaced) as `result.preamble` and taking it off the front of the
answer. It is still streamed, so a surface can show the working live; it is no
longer spoken, archived, or returned.

## A transcript is occasionally doubled on the wake-word path

severity: minor
status: **fixed** (M35 — `--vad-filter` on `wyoming-whisper`)
Regression: `voice-wake-word`, whose WER ceiling is back to the default 0.25

**The fix.** It was never occasional: re-tested under M35 it was three runs out
of three, every time. The two spaces in `"…lights.  Turn on…"` were the tell —
faster-whisper returning one utterance as two segments, which is the repeat
hallucination that long silences provoke. The container does not expose
`condition_on_previous_text`, which was the hypothesis below, but it does
expose `--vad-filter`, which removes the silence that causes it. WER 1.00 → 0.00,
three runs of three.

Two negative scenarios got stronger as a side effect: with the filter on,
silence and room tone produce NO text rather than Whisper's "You"
hallucination, so `voice-silence` and `voice-room-tone` now assert a coded
`stt-no-text-recognized` instead of the weaker "whatever it heard moved
nothing".

What was originally written here, kept because the ruling-out was right and
only the conclusion was wrong:

Through the wake stage, one utterance in several comes back from the recogniser
twice: `"Turn on the ceiling lights.  Turn on the ceiling lights."` The action
is still correct — the model reads it as one instruction — but the WER for that
turn is 1.00, and a user reading the transcript on the HUD sees themselves
stutter.

What has been ruled out, by measurement:

* Not the recogniser alone: the same WAV through the same Whisper, outside the
  pipeline, transcribes once (`testing/live/voice.py`, direct `Ears.hear`).
* Not the pacing: the same audio through the **stt** start stage, paced
  identically and with the same trailing silence, transcribes once.
* Not jarvis-core duplicating the stream: `_audio_stream` drains one queue once,
  and there is no pre-roll replay anywhere in `voice/pipeline.py`.

It is specific to the wake→stt handover and to particular utterances. The
scenario keeps asserting the *consequences* (the service call and the resulting
state), which are unaffected, and its per-turn WER ceiling is relaxed with a
pointer to this entry rather than to hide it: the number is still reported in
`docs/LIVE_TEST_REPORT.md`.

## A voice turn takes 15–20 seconds, against a 2-second target

severity: major
status: **open** — see `BLOCKERS.md`
Regression: measured on every voice scenario; the numbers are in
`docs/LIVE_TEST_REPORT.md`

Measured on this host, per stage, for a tool-using turn: streaming the audio
and recognising it ≈ 11 s, first model token ≈ 2–3 s after that, the rest of
the answer ≈ 3–4 s, synthesis ≈ 1–1.5 s. Median round trip over the suite:
**17 s**. A text-only turn is ≈ 5–7 s.

Nothing in the code accounts for it: the model is a 27-billion-parameter model
on a remote llama-swap with no GPU on this box, and faster-whisper `base.en`
runs on four shared vCPUs. The brief's full-mode threshold of a 2 s median
cannot be met with this model on this hardware, and no amount of code will
change that. It needs either a smaller model for the voice path (a 3–8 B class
model at the same endpoint) or a GPU — an operator's decision, so it is in
`BLOCKERS.md` rather than being quietly re-scored here.

## A nudged model argues instead of answering

severity: minor
status: **open**
Regression: `task-background-plan`

When the model writes out a tool call instead of making one, `agent.py` nudges
it once — correctly, and the recovery works. But the nudge's wording ("You
described calling X but you did not actually call it") invites a defence, and
the answer that reaches the user is sometimes:

> "I did call get_state in my previous turn and received results, but I'll run
> it again now to give you fresh confirmation."

The tool call does happen; the sentence is just an argument the user did not
need to hear. The preamble fix above removes it from the reply whenever the
corrected round then calls a tool, which is most of the time — this entry is
for the case where it does not.

## A false narrated-call alarm derailed a turn

severity: major
status: **fixed** (`jarvis-core/jarvis/llm/agent.py`, `narrated_tool_call`)
Regression: `task-background-plan`
Test: `jarvis-core/tests/test_narrated_tool_calls.py::test_a_turn_that_lists_its_tools_is_not_narrating_a_call`

Asked to go through every sensor in the house and write it up, Jarvis answered:

> "I thought about that but didn't manage to put an answer into words, Sir.
> Would you ask me again?"

and started no task. The log said the model had "described calling
`activate_scene` without calling it".

It had not. The detector required a call cue *anywhere* in the turn and a
registered tool name *anywhere* in the turn, and the model had written a
paragraph containing the word "call" and, further down, a list of what it could
do. The nudge told it to make a call it had never described; the corrected
round produced no text at all; the user got the canned apology instead of their
work.

Three rules now separate "this text scripts a call" from "this text mentions a
tool": the name must be written as a call (`name(`) or sit within 60 characters
of the cue; a turn naming several tools is enumerating its toolbox; and a cue
preceded by a modal ("I *can* call on `get_state`") is an offer, not a claim.

## The model narrates internal steps out loud

severity: minor
status: **open**
Regression: `house-garbled`

Replies sometimes report machinery the user did not ask about:

> "My apologies, sir — I've now read the house style, and I confess I still
> don't follow what you'd like me to do."

Reading a skill is an internal step. The preamble fix removes this whenever the
round that said it went on to call a tool, which is most of the time; here the
final round says it, so nothing catches it. It is a persona/prompt problem
rather than a wiring one — the system prompt already tells the model to report
outcomes rather than services — and the scenario now judges what matters (it
must not claim to have acted on the house) rather than the wording.

## The narrated-call nudge could cause an action nobody asked for

severity: critical — **fixed**
status: **fixed** (`jarvis-core/jarvis/llm/agent.py`)
Regression: `research-cancel`
Test: `jarvis-core/tests/test_narrated_tool_calls.py::test_a_turn_that_already_acted_is_never_nudged`

Asked to **stop** a research run, Jarvis called `cancel_task`, summarised what
it had done, and the summary mentioned `deep_research`. The narrated-call
detector matched that, the nudge told the model to "make the call properly",
and the model **started the research again**. The user asked for something to
stop and got another one started.

That is the worst shape a correction can have: a mechanism that exists to stop
the assistant claiming work it has not done, causing work nobody asked for.

Fixed by the rule that should always have been there: **a turn that has already
called a tool is reporting, not promising**, and is never nudged. Narrowing the
cue words to past tense was tried as well and reverted — "Now calling
code_task" is exactly the failure the detector is for, and it is a present
participle; what separates an offer from a claim is the modal in front of it,
which is a separate check.

Recorded as critical even though it was found and fixed in the same hour,
because the class matters: anything that can turn a user's "stop" into a
"start" is the kind of defect this suite exists to catch, and it was caught by
a scenario rather than by a unit test.
