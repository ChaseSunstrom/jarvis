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

## A worktree brought the stack up, again — and the console's reconnect threw

severity: critical
status: **fixed** (M52 — `docker compose` is refused from any git worktree by
the rig, `live_interaction.sh` and `make up`; the console's reconnect timer is
bound)
Regression: `testing/live/tests/test_rig.py::test_compose_is_refused_from_a_git_worktree`, and
`jarvis-web/src/lib/consoleLink.test.ts` ("schedules the retry without calling setTimeout as a method of the link")
Found by: M52's gate — the route pass on the real console reported
`pageerror: Illegal invocation`; the core had restarted two minutes earlier

At 06:47 an agent building M57 in a worktree ran the stack "up" from that
worktree, in spite of a brief that forbade docker in any form. A worktree's
compose project *is* the production project — the name comes from the
directory — so jarvis-core, the gateway, the browser service and whisper
were re-created from the worktree's checkout: the wrong config directory,
empty secrets, a crash-looping browser. The night's clean-environment fix
(previous entry) could not have caught this: the hazard is which checkout,
not which shell. Now `.git`-is-a-file (a worktree) refuses compose in the
three places it is invoked, unless `JARVIS_ALLOW_WORKTREE_COMPOSE` is set on
purpose.

The restart also found a console defect nothing had reached: when the link
drops, `ConsoleLink.onLost` scheduled its retry through a stored `setTimeout`
called as a method of the link, and Chrome throws "Illegal invocation" for
that — so the console could not reconnect after a core restart at all. Node's
`setTimeout` does not care, which is why no unit test had seen it; the new
test applies the browser's rule.

## The prompt told the time in one zone and the scheduler kept it in another

severity: major
status: **fixed** (M27 — the prompt's clock line reads `configured_clock`, the
same clock the schedule and the automations use) — and one thing for the
operator, below
Regression: `task-scheduled`, and
`jarvis-core/tests/test_llm.py::test_agent_system_prompt_says_what_day_it_is`
Found by: a reminder set "in one minute" that the schedule filed for six hours
later; the probe that set it directly and read the job back

The clock line added the night before used the container's zone (Europe/London).
The house's configured zone — `jarvis: time_zone:`, which the console can set
and `.storage/settings.json` overrides — was `America/Chicago`. The model
wrote "2026-08-26T05:40:00" for one minute ahead in London time; the schedule,
honouring the configured zone as it should, read that as 05:40 CDT. Neither
was wrong on its own terms; they were reading different clocks. Now there is
one clock.

**For the operator:** `.storage/settings.json` holds `jarvis.time_zone:
America/Chicago`, `unit_system: imperial`, `currency: USD`, `country: US` —
saved from the console's settings page at 20:13 on 25 August, and unlike the
`llm.model: house` entry beside them they read like a form's US defaults
saved once rather than a choice. `configuration.yaml` says Europe/London and
so does the container. Not changed tonight: it is your house's setting. If it
is not what you meant, the Settings page or deleting those four keys puts it
back to the file's values.

## A reminder with no phone paired went nowhere anyone looks

severity: major
status: **fixed** (M27 — a fired reminder is recorded in the notifications
inbox first, kind `reminder`, and sent to the phone second)
Regression: `task-scheduled`, and
`jarvis-core/tests/test_schedule.py::test_a_reminder_lands_in_the_inbox_whether_or_not_a_phone_is_paired`
Found by: the live suite, once `task-scheduled` could be checked honestly

"Remind me in one minute to check the oven" registered the reminder and, a
minute later, delivered it through `companion.notify` — the phone channel.
This house has no phone paired, so the reminder became a task result and an
INFO line, which the scenario's own intent ("arrive as a UI moment rather
than a line in a log") names as the failure. The scenario had been passing
by accident: its second turn sent an empty text message, the model read that
as a fresh request and set a *second* reminder, and the reply happened to
contain "oven". The rig now has observe-only turns (say nothing, wait,
assert on what the house did by itself) and a `schedule:` expectation for
the entry before it fires; the scenario waits for the reminder in the inbox.

## The rig re-created the stack with the caller's shell variables

severity: critical
status: **fixed** (M27 — every `docker compose` child the rig starts gets a
clean environment: docker's own knobs and the basics, nothing a service could
interpolate)
Regression: `testing/live/tests/test_rig.py::test_compose_never_sees_the_callers_exported_env`
Found by: a run that collapsed after its first scenario — the core restarted,
then the core, the gateway and the browser service all answered 401

`live_interaction.sh` does `set -a; . .env` to hand the runner `LLM_URL`; so
did I, launching the runner directly. The runner's preflight
`docker compose up -d --wait` inherited that shell, and compose prefers a
shell variable over the project's own `.env` — so the root file's values
(no browser tokens; a different gateway key) went into jarvis-core, the
gateway, the browser service and whisper, which were re-created on the spot.
The browser service crash-looped on its empty token, every model call got
"invalid key", and the rig's own token no longer matched the core it had
just rebuilt. Nothing on disk changed; `make up` from each stack's directory
put it right. Critical because the rig is meant to be safe to run against a
house somebody lives in, and this re-created that house's services with the
wrong secrets without asking.

## A forgotten fact was read back out of the transcript

severity: major
status: **fixed** (M27 — the agent blanks the turns that carried a fact when
the store announces it forgotten; the tool result also says not to repeat it)
Regression: `memory-forget`, and
`jarvis-core/tests/test_llm.py::test_a_forgotten_fact_leaves_the_transcript`
Found by: the live suite, `memory-forget (text)`

"Remember that the shed key is under the second flowerpot" — "forget that" —
"where did I say the shed key was?" got *"You told me it was under the second
flowerpot, Sir — and then asked me to forget it, which I did."* The store had
forgotten; the conversation had not, and the model answered from the
conversation. A tool message telling it not to repeat the fact was ignored
on the next run, which is the point: the words have to go, not the advice.
The agent now listens for `memory_changed: forgotten` and replaces the user
turn that stated the fact and the assistant turn that acknowledged it — in
the live history and in the archive the console redraws — with a placeholder.
The forget request itself stays, since it names the subject and not the fact.

## A long job was ground through inline, so there was nothing to cancel

severity: major
status: **fixed** (M27 — persona §6: a plainly long job goes to the background
whether or not "don't wait" was said; "tell me later" is a background job)
Regression: `task-cancel-mid-run`, `task-live-ui`
Found by: the live suite — `task-cancel-mid-run` on both variants

"Look into every sensor in the house and write me a long report about all of
them", then "stop that job": the model did the audit in the conversation —
eight to eleven tool calls, two to six minutes — and answered "nothing to
stop, the report is already written". And "look into which lights are on and
tell me what you found later" became a *scheduled* reminder, so the task
dock, rightly, showed nothing running. Persona §6 only covered "don't wait";
it now says a job that plainly means reading a dozen things one after
another is handed to the background regardless, and that "later" with no
time given is that, not an alarm.

## The rig passed a turn on a task that was hours old

severity: major
status: **fixed** (M27 — `wait_for_task(since=…)`, a wall-clock floor on the
task's `created`)
Regression: `testing/live/tests/test_rig.py::test_a_task_older_than_the_turn_does_not_satisfy_the_turn`
Found by: reading the verbose run, not by the suite — which is the defect

`task: {kind: background, within: 30}` was satisfied by any background task
in the list, and four sensor audits that a restart had interrupted the day
before were still in it. Every task scenario's first turn "passed" whether
or not a task was made; the second turn then failed for reasons that made no
sense. A verify that can be passed by history is worse than none.

## `deep_research` was refused for the question being under the wrong key

severity: minor
status: **fixed** (M27 — the tool accepts `query`, `topic` and `text` for
`question`, and its refusal says what to call instead)
Regression: `research-deep-report`
Found by: the live suite, `research-deep-report (text)`

The model called `deep_research` with the question under another name, the
tool said "I need a question to research", and the model told the user the
research was *"queued and waiting on your confirmation before it runs"* —
nothing was queued and nothing waited. A tool whose one required argument a
30B model gets wrong one time in a handful is a tool that should take the
obvious synonyms; the refusal now also says "do not tell the user it is
queued".

## Piper logged a broken pipe when a synthesis was abandoned

severity: minor
status: **fixed** (M27 — every Wyoming connection exit is the polite hang-up
`describe` was given earlier)
Regression: `stack-logs-clean`, and
`jarvis-core/tests/test_voice.py::test_synthesize_hangs_up_politely`
Found by: `stack-logs-clean`, on the first run that drove the console

A browser-driven turn closed its page while Jarvis was still speaking; the
core dropped the piper connection with audio in flight, and piper logged
`BrokenPipeError` at ERROR three times for a turn nobody was listening to.
The synthesis path closed the socket the way `describe` used to.

## A browser-driven scenario has no thread: every turn is a new page

severity: minor
status: **open** — a limit of the rig's browser transport, not a defect in
the console; see below for what the console does
Regression: `testing/live/tests/test_rig.py::test_a_browser_scenario_is_one_turn_until_the_transport_carries_a_thread`
Found by: `resilience-core-restart` put through the console once, where turn
two got "I've lost the thread of what 'the same' was"

That read, the first time, as the console losing its conversation across a
core restart. It is not: the console keeps `openConversationId` in page state
and passes it on every run, spoken or typed, and the API variant of the same
scenario keeps its thread through the restart. What loses the thread is the
rig — `browser_turn.cjs` opens a fresh browser and a fresh page for every
turn, so each turn is a new conversation with no memory of the last. Until
the console can open a named conversation from its URL and the transport
reads the id back after a turn, a browser-driven scenario is one turn long,
and a test says so. The deep link is in `docs/FUTURE.md`.

## No scenario had ever run through the console

severity: major
status: **fixed** (M27 — `ui:` probes are implemented, the browser variants run
by default when a console is reachable, `task-live-ui` names testids that
exist)
Regression: `task-live-ui`, and
`testing/live/tests/test_rig.py::test_every_ui_probe_names_a_testid_the_console_renders`
Found by: reading the runner after `task-live-ui` failed with "asserts 'ui',
which the rig checks only through the capability that owns it"

The browser transport existed, nothing declared a `voice-ui`/`text-ui`
variant, nothing passed `--variants`, `ui:` was a documented expectation the
runner rejected outright, and the one scenario that used it probed a testid
(`task-activity`) the console has never rendered. Its Node script could not
even `require('@playwright/test')` from where it lived. All of that is fixed
and a rig test pins every probed testid to the console's source.

## "Note that…" was remembered, not noted

severity: major
status: **fixed** (M27 — the deployed `configuration.yaml` never enabled the
notes integration at all; `MEMORY_REQUESTS` no longer counts a note phrase;
the note-taking skill's rule is the words, not the length)
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

And then, with the store refusing, the model said the quiet part: *"the note
tool isn't available to me at the moment."* It was not. The operator's
`configuration.yaml` had no `notes:` block, so the deployed core never set the
integration up — no `note_create`, `/api/notes` answering 400 — and M16 had
been verified against the harness's generated config, never against this
file. The same defect the memory block's own comment describes, one
integration later. `notes:` is switched on now; the scenario passes on both
variants against the running stack, with the note written and found again.

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
Regression: the round-trip threshold in `testing/live/runner.py`, which
fails a `--full` run on a median over two seconds; the per-stage numbers
are in `docs/LIVE_TEST_REPORT.md`

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
