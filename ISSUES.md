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
status: **open**
Regression: `voice-wake-word`

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
