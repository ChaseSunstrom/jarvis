# Overnight log — 26 August 2026

The 2 AM prompt (`docs/OVERNIGHT_BRIEF.md`) arrived at 02:00 UTC (03:00
London) while M50's verify script was mid-run. Per the brief, the work in
flight is finished first; the brief's rules apply from the moment it was
read. This file is the morning handoff: read it first, then the sections it
points at.

## State when the brief arrived

- 48 of 52 milestones ticked. Unchecked: **M50** (every page on Reactor II —
  the ledger in `docs/UI_MIGRATION.md` §3 is fully ticked and the verify script
  is on its second full run after two plumbing fixes), **M51** (the phone on
  the same look — an agent is finishing the Gradle run in its worktree),
  **M27** (the exploratory pass and the live report — three defects the rig
  found are fixed and committed, the report is not yet written), **M23**
  (final integration — every other box, `make verify-all` green, the suite in
  `--full` mode).
- The stack is up and healthy with tonight's core changes (notes enabled in
  the deployed config, the Wyoming hang-up, the model's clock line).
- Standing blockers (`BLOCKERS.md`): §2 the voice round trip is 15–20 s on
  this host with no GPU — the ≤ 2 s median threshold cannot be met by code;
  §3 anything needing a handset or microphone; §4 accounts and keys for the
  outside world; §5 the motion taste checkpoint is yours.

## The plan, dependencies first

1. **M50** — read the verify result; if green, tick, commit (the milestone
   is the integration of four page commits plus two WIP commits; they are
   left as they are — the brief forbids history rewriting), update
   `CHANGELOG.md` and `docs/verification.md`.
2. **M51** — collect the Android agent's commit, cherry-pick, run
   `m51-android.sh` (build, unit, lint, Roborazzi goldens — no device), tick.
3. **M27** — run the implemented-only suite and the exploratory pass against
   the live stack, `--write-report`, tick when `m27-live-report.sh` passes.
   Every ISSUES.md entry now names a regression that exists.
4. **M23** — `make verify-all` in full; the `--full` live run. The latency
   threshold will fail on this host (BLOCKERS §2): that is logged, not
   weakened. M23's tick depends on what verify-all says; if the only red is
   the latency threshold the box stays open and the reason is written here.
5. **The final phase** (the user's audit brief): prove nothing is faked, the
   cold re-run from `down -v` — with the config and memory volumes
   snapshotted first and restored after, per the brief — the capability
   audit (`docs/CAPABILITY_AUDIT.md`), improvements, `docs/FUTURE.md`,
   `docs/FINAL_STATE.md`.

Parallelised: the Android build (already in an agent's worktree). Everything
that touches the live stack or the console's port runs one at a time — two
suites on one model server double every latency.

Deferred to you: the motion taste checkpoint; the real-credential smoke of
email / channels / calendar (fixture servers only tonight); the phone.

## Assumptions made

- "History rewriting" covers squashing the WIP commits of a milestone into
  one, so M50 lands as a ticking commit on top of its parts rather than one
  squashed commit. `PROCESS.md` prefers one commit per milestone; the brief's
  rule wins tonight.
- The 2 AM job was scheduled with Claude Code's own session scheduler (no
  `at`, no `tmux`, no sudo on this box) and fired on the box's UTC clock.

## Running record

(appended as the night goes)

### 02:00–02:30 — M50 to the gate

- M50's first full gate run failed two plumbing checks, neither about the
  console: the folder check's regex also matched `path: string` in
  `screens.ts` (tightened to the quoted literal), and
  `testing/live/browser_routes.cjs` could not `require('@playwright/test')`
  from outside `jarvis-web` — nor could the rig's `browser_turn.cjs`, which
  only the `--full` variants load. Both now resolve Playwright by path.
  Route pass: 16/16 routes render against the stack, no console error, only
  palette colours.
- Looking at the regenerated pictures: KNOWLEDGE's graph printed "Boiler
  serviced" across "The spare key…". Labels now dodge each other (a colliding
  label goes above its node); `knowledge.spec.ts` asserts every pair of label
  boxes is disjoint and failed on the old build with that exact pair.
- M27's "every issue has a regression" check accepted only a live scenario
  name; seven entries name an eval, a verify script, the Android lint gate or
  a pytest node id instead. The check now accepts any regression that exists
  (it verifies the file, the test name in it, or the scenario/probe name)
  and every entry passes. The latency entry now names the `--full` threshold
  rather than a report that had not been written.
- The 2 AM prompt arrived at 02:00 UTC. M50's gate is on its final run; the
  implemented-only live suite is running with `--report` for M27; the
  Android agent is still in Gradle.

### 02:30–03:20 — the live suite, and what it found

- Implemented-only suite with `--report`: 42/53 scenario variants, 67/77
  turns, WER 5.4 %, median round trip 7.6 s. Eleven variants failed; the
  report is `docs/LIVE_TEST_REPORT.md`. My mistake: M50's gate ran at the
  same time and its smoke subset overwrote `.verify/live/results.json`, so
  the per-turn tool calls of the failures were lost and the eight scenarios
  are being re-run verbosely. From here one thing at a time on the stack.
- Fixed from the failures without waiting:
  - the rig's memory cleanup sent `memory_id=` to an API that takes
    `entry_id=` (every memory scenario "failed" in teardown), and the API's
    complaint said "say which note" about a memory;
  - `forget` now tells the model not to repeat the forgotten fact from the
    transcript ("under the second flowerpot — but you asked me to forget");
  - the persona says a room with one device of the kind asked for means that
    one — "do the same in the bedroom" got "shall I switch the Bed Light on
    instead?";
  - `CallLine` carries `data-mono`: the dashboards route showed a recall
    call in mono and the route pass read it as prose.
- Found: **no scenario has ever run through the console.** The browser
  transport existed, nothing declared a `voice-ui`/`text-ui` variant, nothing
  passed `--variants`, and `task-live-ui` asserted on a testid
  (`task-activity`) the console has never rendered. The brief asks for the
  task queue watched through the task UI via Playwright, so: `ui:` probes
  are implemented in `browser_turn.cjs` (poll a testid for text, bounded,
  report what was there), carried on the `Turn`, asserted by the runner; the
  browser variants run by default when a console is reachable and are
  skipped (not failed) under `--no-browser`; `task-live-ui` runs as `text-ui`
  and probes the task dock; a rig unit test now pins every probed testid to
  the console's source.

### 03:20–03:50 — the diagnostic re-run, and seven more fixes

Eight scenarios re-run verbosely on the rebuilt stack. The persona rule
fixed `resilience-core-restart` (both API variants green). The rest, with
what was actually wrong:

- **The rig's task matcher had no time floor**: `task: {kind: background,
  within: 30}` was satisfied by four sensor audits interrupted hours earlier,
  so a turn that made no task at all "passed". `wait_for_task(since=…)` now
  ignores anything created before the turn; a rig test pins it. This is why
  `task-cancel-mid-run` and `task-live-ui` looked half-green.
- **A long job is done inline**: "write me a long report about every
  sensor" ran eight to eleven tool calls in the conversation instead of as a
  task, so there was nothing to cancel. Persona §6 now says a plainly long
  job goes to the background whether or not "don't wait" was said, and that
  "tell me later" with no time is a background job, not a reminder (the
  model had scheduled one).
- **`task-scheduled` expected a `notify` task kind that has never existed**;
  the schedule tool files `scheduled`. Fixed the scenario, and its judge now
  accepts a clock time a minute ahead.
- **`deep_research` was called with the question under the wrong key** and
  refused ("I need a question"), which the model narrated as "queued and
  waiting on your confirmation". The tool accepts `query`/`topic`/`text` as
  well and its refusal says what to do.
- **The forgotten fact was still in the transcript**: the tool message alone
  did not stop the model reading it back. The agent now listens for the
  store's `memory_changed: forgotten` event and blanks the turns that carried
  the fact — live history and archive — leaving the forget request itself.
  Unit test: `test_a_forgotten_fact_leaves_the_transcript`.
- **Piper logged `BrokenPipeError` at ERROR** when a synthesis was abandoned
  (a browser variant closed its page mid-answer). Every Wyoming connection
  exit is now the polite hang-up `describe` got earlier; test added.
- The recall judge failed "The blue tin on the shelf, Sir" for not repeating
  "spare key"; the criterion now says an elliptical answer counts. Not a
  weakening: the answer was right.
- A scenario with no `variants:` line stays on the API variants; the
  browser ones are opted into. (The first re-run put `resilience-core-restart`
  through the console and found that the console's chat thread does not
  survive a core restart the way the API's does — logged as an open issue.)

### 03:30 — M51 integrated

The Android agent's commit (`dfa68c6` here, `ab4bd3b` in its worktree)
cherry-picked cleanly on top of the token removal: `design/build.py --check`
reports nine generated files current and the phone's twenty-three geometry
constants agree with the web's; `make test-android` has no FAIL line. Its
Gradle run (assemble, 185 unit tests, lint, ten Roborazzi goldens verified)
passed in the worktree; the same run and the M51 gate are queued on this
checkout behind the live re-run, so the two do not share the CPU. The M51
ledger rows are ticked with what each got; `docs/verification.md` now says
185 JVM tests. No device was touched.
- 03:32 — the whole core suite passes after tonight's changes: 3100 tests in
  8m39s. The rig's own tests: 42. `ruff`: clean.

### 03:25–03:45 — an incident, mine, and what it taught the rig

The re-run of the eight scenarios collapsed after its first scenario: the
core restarted, then the core, the gateway and the browser service answered
401 / "invalid key" / "token must be set". Cause: to launch the runner
directly (not through `live_interaction.sh`) I did `set -a; . ./.env`, which
exported the **root** `.env` into the runner's environment; the runner's
preflight `docker compose up -d --wait` then interpolated services from those
shell variables, which compose prefers over the project's own `.env`. At
03:26:37 it re-created jarvis-core, jarvis-gateway, jarvis-browser and
whisper with the root file's values — no browser tokens (crash loop), a
different gateway key (401 on every model call) — and the run's own
end-of-scenario restore/restart at 03:34 finished the job. Nothing on disk
was lost: `jarvis-core/config`, `.storage` (two tokens) and both `.env` files
are intact and untouched. Note `live_interaction.sh` itself does the same
`set -a` (line 47), so the hazard was latent in the canonical path too.

Repair: `make up` (each stack from its own directory; no volume was
removed). Hardening: the rig now runs every `docker compose` child with a
clean environment — docker's own knobs and the basics, nothing that could
interpolate into a service — pinned by
`test_compose_never_sees_the_callers_exported_env`. The eight scenarios are
being re-run on the repaired stack.

### 03:45–04:05 — the repaired stack, and the next layer

On the repaired stack: `task-live-ui` **passes through the real console**
(the dock probe sees the job running), recall and both restart variants
pass. Six remained, each with its own cause:

- The redaction never fired: the history is written at the *end* of a turn,
  so the turns carrying the fact are stamped after the memory entry, outside
  a window that only looked before it. The window now spans either side; the
  unit test models the real ordering.
- `task-cancel-mid-run`'s turn 0 now made a task and the engine cancelled it
  on "stop that job" (the core log says so) — but the rig's new time floor
  was per *turn*, so the task created in turn 0 did not count in turn 1. The
  floor is the scenario's start.
- "Remind me in one minute" is a schedule entry until it fires, and only then
  a `scheduled` task; the scenario asked for a task within 30 s. The rig has
  a `schedule:` expectation now (`jarvis/schedule/list`), with the same
  floor, and the scenario uses it.
- `deep_research` was skipped this time in favour of one search and a page,
  although the request said "deep research"; the research skill now says the
  word decides, whatever the question looks like.
- The proactive task was "a third of the way through" at 240 s: one model
  round trip per sensor on this hardware. Not a code defect; noted against
  BLOCKERS §2.
- Piper logged one `ConnectionResetError` at 03:53:18 during a voice turn,
  after the polite hang-up was in place; not yet explained.

### 04:05–04:30 — down to the last four, and two more product gaps

Re-run: `memory-forget` passes (the redaction, with the window on both
sides), `task-cancel-mid-run` passes both ways (the engine cancels; the rig
now sees it), `task-live-ui` passes again through the console. Left:

- **`task-scheduled`**: the text variant "passed" for the wrong reason — its
  second turn sent an empty message, the model set a second reminder and the
  reply happened to say "oven"; the voice variant sent silence and got an STT
  error. Underneath: a fired reminder only went to `companion.notify`, and
  with no phone paired that is a task result and a log line. Fixed: the
  reminder lands in the notifications inbox first (kind `reminder`), the
  phone second; the rig gained observe-only turns and a `schedule:`
  expectation; the scenario watches for the moment.
- **`research-deep-report`**: `deep_research` was held for approval — by
  design: the model had called `web_search` first, the turn was tainted by
  untrusted content, and the quarantine escalates a tool that writes. The
  research skill now says the word "research" decides the tool before any
  search; whether the model obeys is what the re-run measures. Not weakened.
- **`interactions-proactive-moment`**: a third of the way at 240 s. Hardware.
- **piper `ConnectionResetError`** during the console-driven turn: the
  browser closes its page mid-answer, the core's TTS is cancelled, and a
  one-second drain was too short for a reply's worth of audio; the hang-up
  now drains for up to eight seconds and is shielded from the cancellation.

### 04:30–04:50 — the empty inbox and the six-hour reminder

Direct probe (a reminder set over the API, the job read back):

- **The notifications integration is not configured on the deployed
  Jarvis** — `not_configured` from `jarvis/notifications/list`. The same
  class of gap as notes: shipped in M17, verified against the harness's
  generated config, never switched on in `configuration.yaml`. Every empty
  inbox tonight (reminders, task-completed moments) was this. Enabled, with
  the same kind of note the notes block got.
- **The reminder was filed six hours out.** The running core's zone is
  `America/Chicago` — `.storage/settings.json` overrides the file's
  Europe/London (saved from the console's settings page yesterday at 20:13,
  alongside imperial/USD/US) — and my clock line used the container's zone,
  so the model wrote a London time the scheduler read in Chicago. Fixed in
  code: the prompt reads the same `configured_clock` as the schedule, with a
  test that moves the house to Kiritimati. **Not changed:** the setting
  itself; it is the operator's, and it is flagged in `ISSUES.md`.
- `NON_INTEGRATION_KEYS` gains `metrics`: the dashboards integration reads
  `metrics: sources:` and the loader warned "No integration named 'metrics'"
  at every start.

### 04:50–05:00 — the reminder, end to end

With the inbox on and one clock: `task-scheduled` passes on both variants —
the schedule entry appears within seconds, the reminder fires a minute later
and is in the notifications inbox as a `reminder` moment, on voice and on
text. `house-light-on` clean, piper clean. The one remaining red in this set
is `interactions-proactive-moment`: the background audit of every sensor is
a model round trip per sensor and does not finish inside the scenario's
240 s on this host (BLOCKERS §2). Not weakened; the full run will show it
red, and that is the true state.
- 05:00 — the whole core suite passes on the final core: **3101 tests**
  (38 min, sharing the CPU with a live run). The M27 work is committed as a
  WIP (`fbbefe6`) so nothing is lost; the milestone is ticked only when its
  gate passes on the report the full run writes. The **full-mode** live run
  with `--report` is under way (~1 h); the gates run after it, one at a time.
- 05:30 — correction: the "console loses its thread across a restart" issue
  was the rig, not the console: the browser transport opens a fresh page per
  turn, so a browser-driven scenario is one conversation per turn. The entry
  is rewritten, a rig test pins browser scenarios to one turn until the
  transport carries a thread, and the console deep link is in FUTURE.md.

## 06:00 onward — the brief changed: the overhaul

At 06:13 the operator set a new goal (`docs/OVERHAUL_PLAN.md`): genuinely
capable of anything online, cameras, any sensor, the sky; the voice tab
alive with the graph; simpler menus and the real models; motion when it
acts; Android the equal of the web and of Tasker; local only. The full-mode
live run in flight was stopped; its ticks (M27, M23) resume when the stack
is quiet. Done since:

- M50 committed on a passing gate (29/29); M51 ticked and committed (20/20).
- Four research documents in `docs/research/` (vision and cameras; sky,
  satellites and radio; local intelligence; devices and protocols).
- M52 — the graph and the living activity on the voice tab — built and its
  gate green (17/17): an activity strip fed by the bus, the graph lighting on
  use, "looking · Kitchen" under the reactor, mock hooks for every row kind.
- M53 — motion when it does things — designed (`docs/design/MOTION.md`), the
  reactor sweeping on a tool call, beating while speaking, irising while
  looking; the held bar pulsing; eight choreographies measured in
  `motion.spec.ts` against a new `motion.budget.frame` token; a fifth
  recording. Gate running.
- Agents in worktrees: M54 (settings and the real models — the operator's
  facts: `qwen3.8-27b` in use at ≈75 tok/s, `qwen3.6-35b` configured as fast
  and idle), M56 (cameras, the OpenAI wire for vision, go2rtc), M57 (MQTT
  discovery for any sensor), M58 (the sky with skyfield). BLOCKERS §2
  corrected: the model is not the wait; STT, prefill and TTS start are.
- 06:47 — incident: the M57 agent's worktree ran `docker compose` and
  re-created the house's containers from its checkout (the brief forbade
  it). Agent stopped; stack repaired with `make up`; `docker compose` is now
  refused from any git worktree in the rig, the live script and `make up`
  (test pinned). The restart exposed a console bug: the link could not
  reconnect after a core restart ("Illegal invocation" — an unbound
  `setTimeout`); fixed with a test that applies the browser's rule.
- 06:55 — M52 (`a455d30`) and M53 (`9cc55d3`) ticked and committed on green
  gates (17/17, 13/13; every choreography's worst frame 17–27 ms against a
  50 ms budget). The full-mode live run with `--report` is running on the
  quiet stack for M27/M23. M60's and M61's gates are written and fail first;
  the Tasker parity table (`docs/ANDROID_TASKER_PARITY.md`) is the measure:
  37 built-in actions plus the accessibility ones exist, the gaps are listed
  in landing order. Agents: M54, M56, M57 (re-spawned with the corrected
  brief and the hard rule), M58, M59.
- 07:25 — the full-mode run: 47/53 variants, 72/77 turns, WER 5.7 %, routing
  95 %, median 6.7 s; intent 93.5 % against the 95 % floor and the 2 s
  ceiling both missed and written down in `docs/verification.md` ("Known
  failures, 26 August") with the cause of each of the five turns. The rig
  now cancels the tasks a scenario started when it ends (a lingering audit
  had been what the dock showed). The five build agents were cut off by a
  model usage limit; their worktrees' work is committed as WIP and is being
  brought onto the branch one milestone at a time — M54 first (gate 37/38,
  the last red a stale picture folder, re-running), then M58 (its gate is
  green in its worktree), then M56 (one camera test to finish).
- 07:35 — M54 (`3294b96`, gate 38/38) and M27 (`ae5d8da`, gate 9/9) ticked
  and committed; the console container carries the five-section SETTINGS
  and the MODELS panel. M58 is on the branch with `sky:` switched on in the
  deployed config; its gate and the core rebuild are running.
- 07:45 — the sky is live: on the rebuilt core the integration fetched 22
  station element sets from CelesTrak and the ephemeris, and the live
  scenario "when is the space station next visible from here?" passes on
  voice and on text. M58's gate on the branch had one check written the
  wrong way round (it demanded the deployed config NOT enable the sky);
  reversed to the policy the notes and notifications blocks set, re-running.
  A startup race that fetched the elements twice is closed; the rig now
  breathes for three seconds before restarting a core at the end of a run
  (piper logged a reset each time a core was torn down mid-synthesis;
  `ISSUES.md`).
- 08:05 — M58 (`d8cc870`) ticked and committed. M56 brought onto the branch
  from its worktree and finished: the OpenAI wire's tests written, a public
  model url refused before any frame is read (documented by the agent, not
  enforced), go2rtc and Frigate wired into the setup, the harness given a
  fixture camera, `vision:` switched on in the deployed config with no
  cameras yet. Both vision suites green (116). Gate running; the live check
  needs a vision model on the model server, which serves none today.
- 08:25 — M56 committed (`7a8ec3f`), not ticked: gate 27/28, the open check is
  the live look through a served VLM (`VISION_MODEL`); the server serves none.
  M57 built: event/device_tracker components, the birth on the discovery
  prefix, allow/deny ids, canonical units, Tasmota and Shelly translated,
  four sensor tools, six device fixtures, the strip draws a button press.
  Gate 19/19; live against the stack 9/9 — the history question reached for
  `sensor_history` and answered "11.0 °C, Sir — it has since crept back up
  to 12.5." Ticked and committed next.
- 09:05 — M57 committed (`87d3c90`); the sky's live run recorded (`b7a0568`).
  M55 built: the menu inventory in UI_MIGRATION §4 read by menus.spec (17), the
  tools page one search, automation rows a switch and MORE, one way into the
  dashboard editor, areas' rename/delete one click in, entity rows offering the
  one move they can make. First spec run passed against a stale build — the
  gate now builds first. Gate running; tick and commit next.
- 09:25 — M55 committed (`c33cc13`), gate 21/21. M60 built: cache_prompt on
  every request, the prompt stable-first with a budget, early speech
  (tts-chunk + remainder) with the console playing it, whisper int8, the
  constrained corrective retry, read-only step batching; floors pinned.
  Gate running; then the stack is rebuilt and the live suite re-measured.
- 09:58 — M59 built and gated 17/17 with the live run green: a watch on a
  fixture page the rig rewrote landed as a moment inside 90 s, and "what is
  being watched" answered with the change. Committed next; M60's gate is
  running its full core suite (2,756 tests — slow, not stuck).
- 09:52 — M61, first stage: ActivityRows/ActivityStrip, KnowledgeGraph(View), chunked
  speech on the phone, eight Tasker rows closed, JVM tests and Python mirrors; the
  gate's gradle steps cannot run here (no SDK). M60's gate is still in the core suite.
- 09:58 — M61 follow-ups: the phone's reactor moves for what Jarvis does
  (`d470031`), the loops row closed — the engine had them (`34c5c6d`). The
  ledger carried (`e4ba97e`). Waiting on M60's core suite and the full live
  re-measure against the rebuilt stack.
- 10:20 — M60 gains the last item on its plan: `llm.fast_model`, when set, is the
  model for spoken turns (text keeps the chat model); the catalogue counts it.
  Five more Tasker rows closed on the phone (`16f0e24`), ui_key marked no with
  the reason; six rows stay gap. M23's status written in MILESTONES: not green
  on this host, for three reasons that are not the repository's. Both long
  runs (M60's core suite, the full live re-measure) still in flight.
- 10:21 — M60's gate 13/13, the full core suite green (2,756 tests). Ticked and
  committed; the live re-measure is still running and lands as its own record.
- 10:40 — The full live re-measure after M60: 52/58, 81/87, WER 5.9 %, median 5.90 s
  (from 6.67). Three of the six failures were one defect it found: `read_page`
  (M59) was not on READ_ONLY_TOOLS, so after a search the taint rule escalated it
  and the model said, truthfully, that the page was waiting on approval. Fixed —
  the readers of M57–M59 are read-only, read_page fences its text on every path
  and says what happened — and the two scenarios are re-running on the harness.
- 10:47 — Chasing the research failures on the harness: `deep_research` after a
  `web_search` is escalated by the taint rule (correctly), so the model's new
  search-first habit made research wait for approval; a tool rule now says to
  delegate first. research-cancel passes alone (its error was run order). The
  stack's SearXNG returns nothing — its upstream engines time out — recorded as
  the environment's. Two scenarios re-running; then the final full run.
- 10:53 — The loop fix committed (`66a7b43`): the same call three rounds running
  ends the turn and the last round is told to answer; deep_research says
  "answer now". JavaScript-page passes on the harness; deep-report re-running;
  the stack rebuilding for the final full run with the report.
- 10:59 — research-deep-report passes on the harness (delegates first, files the
  note); its 240 s run before was a reasoning block. A spoken turn now does not
  reason unless `voice: think: true` (think per turn on the agent, `enable_thinking`
  on the OpenAI wire); text keeps the model's default. Stack rebuilt; the final
  full-mode run with the report starts now.
- 11:26 — The full run without reasoning on spoken turns: median 3.07 s, intent
  87.2 %, routing 84.6 % (47/58). Worse tool choices on voice; the brief puts
  intelligence first, so the default is back to reasoning and the switch stays
  with both numbers beside it. Rebuilding; the final full run follows.
- 11:54 — The record: 49/58, 79/88, intent 89.8 %, routing 96.2 %, WER 5.9 %,
  median round trip 2.87 s (6.67 s at 06:54), p95 20.3 s. Intent and the 2 s
  median still missed, recorded. docs/LIVE_TEST_REPORT.md is this run.
  `make verify-all` starts as M23's whole-state record.
- 12:40 — verify-all's first half read: four of its reds were the repository's and
  are fixed (`d62d774`: a doc block, two stale gate paths, a JVM test's false
  claim); m19's 401 was the launcher's environment (the gate reads .env now);
  m03's graph count is a cross-spec leak (a knowledge reset in the mock). And
  the m08 gate proved this host has M08's toolchain: M61's Kotlin compiles,
  lints and passes its JVM tests here — its gate is 17/19, the goldens for the
  strip and the graph being recorded, the six gap rows staying gap.
- 12:45 — M61's gate 18/19 with the toolchain: build, JVM tests, lint and the goldens
  (the strip and the graph recorded and looked at; the graph's labels fixed on the
  first look) all green here; the six gap rows are the one open check. The smoke
  failure named: "Done, Sir — the bed light is off" with nothing called; a guard
  now sends that back to call or to say so.
- 12:50 — The console's whole suite 282/284 with the knowledge reset (the two
  misses are motion frame budgets under a concurrent verify-all). verify-all is
  past m24; its table lands when it ends.
- 13:35 — verify-all at m42; its next reds read: the phone's gate now runs the
  smoke set like every gate (m25), a job that also read a sensor routes as the
  task again (m26's route-6 — the readers sat above `task` in the router), two
  images off :latest by digest (m28), the two new decisions in the format the
  decisions doc keeps (m30). Each re-runs after the table lands.
- 14:40 — verify-all at m60 (its full suite). Three more reds read and fixed: the
  sky block's note had been pushed out of the check's reach by the vision block
  (m58); two 8-px dots used the pill radius where the console draws circles at
  50 % (m50); the M45 check predates M47's catalog store and counted the
  registry's own state as a subsystem (m45).
- 15:14 — `make verify-all` finished: 11,825 s, 43 gates green, 19 red. Twelve of the
  reds were fixed while it ran and are green on re-run (m02, m18, m19, m28, m30, m45,
  m58 re-run green; m25/m26 pending the rebuilt stack); the rest are the ones the
  repository already names — M56 (no served vision model), M61 (six gap rows), M23
  (both), and the smoke slice in m07/m08/m14/m21/m22 (chat-context-retention's claimed
  action, guarded in M60 and re-measured below). Full table in the log's summary; the
  next full run is after the M62 commit.
- 15:20 — the operator's new goal: everything works, the phone matches the web, the
  dashboard a main destination, the PR's seven red CI jobs green, keep testing, clean up.
  No `gh` here; the public API names each failing step. Reproduced all seven locally on
  the current head and fixed what they found (commit d7ffe8f): compose-smoke's service
  accounting broke on compose v2.24's profile-filtered `config --services` and never
  listed go2rtc; its knob sentinels were the pre-rename OLLAMA_* names; the harness
  self-test's taint scenario held `control_device` at the registry (M43) when the device
  already raises its own tier — `Tool.escalates_itself` now declares that; on 3.12 the code
  agent's cancel check never got a yield; the APK job's goldens step used a path relative
  to the wrong directory; the phone's home column grew a blank 200 dp graph before it had
  a node (the instrumented suite's likely clip). Desktop's Electron step and the web e2e
  pass here; CI will say. A standalone Python 3.12 under ~/.local/py312 runs the core
  suite the CI way: 3229 passed, 1 failed → fixed.
- 16:17 — the smoke set on the rebuilt stack: 6/7 scenarios, 8/8 turns, median 2.09 s;
  the one failure was the rig's stack-logs-clean check on an MQTT ERROR at every stack
  start — the collision heuristic counted a booting broker's refusals as evictions.
  Fixed (483d5e5); re-measured after the next rebuild.
- 15:30 — M62 under way: the dashboard leaves HOUSE and becomes the first console tab, on
  the console and the phone; the M48 cap moves to six (DEVIATIONS §20); the bar fades
  its overflowing edge on a phone so a sixth tab is seen to be there. Two agents in
  worktrees meanwhile: the six Tasker gap rows (M61) and the dashboard's widget kinds
  (M63).
- 16:05 — CI on 483d5e5: ten jobs green (the harness self-test, compose smoke, core on 3.12,
  the desktop-agent e2e, lint, static, the browser/desktop/orchestrator suites, android
  specs). Still red: the APK job's unit tests — Robolectric runs offline from ~/.cache and
  the only step that filled it ran after the tests (d2fe77a); the web e2e, with no
  annotation to name the test (4da6d39 adds the github and html reporters); the emulator
  suite, with no screenshot at all (614957c annotates each failing test and the first
  crash); the Electron build, which GitHub never gave a runner. The next push says.
- 16:30 — M62 ticked and committed (610ec24): gate 17/17, M48 15/15 at the new cap, M55
  21/21. The M50 render caught a tablet bar that overlapped itself with six tabs; fixed
  and pinned by a bar-overlap test at five widths. M61's six gap rows, built by an agent
  in a worktree, cherry-picked clean (95ab5df): 224 unit tests there; the live smoke
  slice its gate needs runs from here after the stack is rebuilt.
- 17:05 — the stack rebuilt with every fix (`make up`), then the smoke set: 7/7 scenarios,
  8/8 turns, WER 0.0, intent 100 %, routing 100 %, median round trip 2.515 s, and the
  rig's stack-logs-clean check green — the MQTT startup ERROR (483d5e5) and the claimed
  action on chat-context-retention (M60's guard) are both gone. `make test` on the merged
  tree: core 3234, every suite and both evals green. M50 re-rendered every screen under
  the six-tab bar, 29/29. CI on 4c9fda3: APK green at last (Robolectric's jars fetched
  first), compose smoke, desktop wheel/install and the rest green; two reds left with
  names now — Tools' link-drop test (a race with the client's reconnect, made
  deterministic in 69da624) and `python · jarvis-core` on 3.12 (reproducing here). The
  once-red gates and the full live run are in progress.
- 17:25 — CI on 69da624: the web e2e green with the deterministic link drop; the Electron
  shell green once launched without its sandbox (GitHub's 24.04 runners restrict user
  namespaces); the emulator suite finally named its five failures, and an agent found the
  one cause — M22's automation master switch, OFF by default and cleared by the test
  rule, so every known action was denied by the standing ban before its tier was read
  (228eb01: a hook that does the user's one tap, a test pinning the OFF case, and the
  contract narrowed to "the switch may be turned on, never off"). Two reds of my own
  making: M63's three websocket commands had no rows in docs/clients.md (the 3.12
  reproduction found it: 3260 passed, 1 failed), and the new annotation step took three
  green suites down because GitHub's run shell is `bash -e` and a grep with nothing to
  find returns 1. Both fixed in df0fab8 and pushed.
- 17:45 — M25's full live run on the rebuilt stack: 49/57 scenarios, 75/82 turns, intent
  91.5 %, routing 95.8 %, WER 5.9 %, median 3.23 s, p95 29 s — the two thresholds still
  missed, recorded not lowered. Eight failures read one by one: the stack had been started
  without the `mqtt`/`search` profiles (no broker, no search engine — the Makefile exports
  them now); two "look into the lights… tell me later" turns were routed to deep_research
  by M60's rule (the rule and the tool now keep house jobs out of research); one thread
  scenario inherited a light already on (it declares its state now); two research /
  delegation scenarios named "the fixture handbook" with no address and the model asked
  which one, as research-cancel already records is correct (they name the address now);
  one restart scenario claimed a light on without calling (a model miss after the
  restart, recorded); one delegation follow-up reported pending findings after 30 s.
  Committed b4010d0 and b7543dd; measured by the next full run.
- 17:50 — CI on df0fab8: every workflow green — CI (core on 3.12, web build + unit + e2e,
  browser, desktop, orchestrator/evals, lint, static, android specs), Build Jarvis APK,
  Build jarvis-desktop (wheel, three installs, the Electron shell), Compose smoke, and
  End-to-end (harness self-test, desktop agent, and the emulator suite, whose five
  failures were the automation master switch). The seven jobs the operator listed this
  afternoon are all green; the fixes were the repository's, not the tests'.
- 17:58 — the once-red gates on the rebuilt stack: M03 (three tests load-only, green alone),
  M07, M08, M14, M19, M21, M22, M28, M30, M45 green. M26's scorecard measured idle (median
  3.1 s, WER 0.07) but could not start its load job: its prompt was research-deep-report's
  old "the fixture handbook" wording, which the model now asks about — the prompt names the
  address and run.py expands it (7beb17b). M51's smoke slice lost chat-context-retention
  (text): the claimed-action guard's note read as the user complaining and the model's
  apology to it became the spoken reply; both guard notes now say what to do and that the
  user never sees them (f1e3795). The stack is rebuilt with these after the sequence, and
  the smoke set, the eight M25 scenarios, the scorecard and the M51/M61/M64 gates run again
  on it. Broker and search engine up beside the stack since 17:55.
- 18:08 — M61 (20/20) and M64 (64/64) ticked from the main checkout, live smoke slices
  included; M63 32/32 again, M58 21/21; M23 12/13 with M56 its one open check. 63 of 64
  overhaul milestones ticked. CI on 570158b: CI, APK, compose smoke and the desktop workflow
  green; End-to-end running. The sequence's last step, the full live run with its report,
  is under way; the rebuild-and-remeasure chain follows it.
- 18:13 — CI green on 570158b as well, End-to-end included: the emulator suite has now
  passed twice since the automation-switch fix. Pushed 49f39a7 (the M61 and M64 ticks, the
  guard and scorecard fixes, the 17:40 verification record, the House dashboard's pictures).
- 18:32 — the report run (docs/LIVE_TEST_REPORT.md): 51/58 scenarios, 78/84 turns, intent
  92.9 %, routing 92.0 %, WER 5.9 %, median 3.17 s, p95 19.2 s, on the pre-rebuild image with
  the broker and searxng up. Seven failures, recorded in docs/verification.md; two are
  defects to chase (memory-forget's store, a failed background sensor audit). The rebuild
  chain — today's rule and guard changes — is measuring now.
- 18:40 — the report run's two defects, read: every failed background task in the store
  says "interrupted when Jarvis restarted" — resilience-core-restart pulls the core out from
  under the sensor audit an earlier scenario started, so the rig now waits (bounded) for
  running tasks before a restart turn. And `forget`: a query like "shed key" was called
  ambiguous because "key" alone cleared the floor for another note, the result came back
  count 0 with candidates, and the model said "Forgotten" over it — forget now takes the one
  entry that matched every word when no other did, and every empty outcome says NOTHING was
  forgotten in the reply's own words. Smoke set green on the rebuilt stack; the eight are
  running.
- 18:50 — the eight scenarios M25 lost, alone on the stack rebuilt with the evening's rule and
  guard changes: 9 of 11 variants pass — the broker (sensors-discovered), the research/house
  boundary (task-live-ui, task-cancel-mid-run), the starting state (thread continuity), the
  handbook's address (research-deep-report, delegation-across-backends) and
  subagents-parallel-work all hold; the median over these heavy scenarios 5.25 s. Only
  resilience-core-restart fails, text and voice, the claimed action after the restart —
  the guard sees "do the same in the bedroom" as an action since ca6c57c, which the second
  rebuild measures. CI green on 6bf9214 (fourth all-green head); ca6c57c pushed.
- 19:00 — on the rebuilt stack: M51 20/20 (its smoke slice passing with the reworded guard);
  M26's scorecard scored 100 % on all five sections and measured idle latency (median 3.1 s,
  WER 0.07) but its load job still did not start with the handbook's address — the load is
  now the sensor audit the routing section proves starts (2dc5fdb), and the gate runs a third
  time behind the measurement chains. Queued after M61/M64: the memory scenarios with the
  core's forget log lines, then a second rebuild measuring the forget and guard-reference
  fixes.
- 19:05 — M64 64/64 on the rebuilt stack; the rebuild chain is done. CI on ca6c57c: one
  flake in home.spec — the bar's underline placed against the fallback face before the web
  font swapped in on a slow runner — fixed structurally with a ResizeObserver on the strip
  and its tabs (9b8fe2b). The memory rerun is up, then the second rebuild.
- 19:10 — the second rebuild (forget and guard-reference fixes) measured: resilience-core-
  restart passes on text AND voice — the guard now sees "do the same in the bedroom" — the
  proactive moment and memory recall pass, median 1.68 s over the four. memory-forget: the
  fact leaves the store now; the third turn's reply hinted that something had been forgotten
  ("that's precisely what you asked me to forget"), which the judge refuses; the tool's
  message says not to hint (65bf479), measured on the next rebuild. Of the report run's seven
  failures, the vision one is the M56 blocker and every other has a fix that held or a
  wording that is about to be measured.
- 19:20 — the third rebuild (the forget message): house-light-on and chat-context-retention
  green on both variants, median 1.7 s; memory-forget now fails one turn earlier and for a
  new reason — turn 1 leaves TWO notes, the explicit remember ("The shed key is under the
  second flowerpot") and the extractor's paraphrase of the same sentence ("The speaker keeps
  the shed key…"), so "shed key" is a true tie and forget asks which. The duplicate check at
  write time did not see a paraphrase whose words contain the other's; being fixed there.
- 19:35 — the duplicate: a note whose words contain another's is the same note (d3570cb);
  from the extractor it is not kept and the user's wording stays, from the user it replaces
  the old wording; digits are kept in the comparison so "note 3" and "note 4" stay two. A
  fourth rebuild measures memory-forget with it. CI green on e651a4c (fifth head); cd95876
  pushed. The operator reported no way to browse tools/MCP servers from Settings: the
  BROWSE CATALOG control is inside a collapsed fold and the deployed config lists no
  catalogue source, so browse answers "no catalog source is configured" — M65 (an agent in a
  worktree): a built-in source of the repository's own bundled skills, the catalogue at the
  top of Settings › Tools through the one search box, MCP add-by-URL visible.
- 19:30 — the scorecard, run directly with its output kept: every section 100 %, and the
  load job STARTED with the sensor-audit prompt — whole turn 4.8 s idle → 5.2 s under load,
  first word 3.1 → 3.4 s, STT 0.6 s both. The gate's failure was timing: the routing section
  asks the same sentence minutes earlier and a still-running job makes the second one
  "already under way". The load has its own subject now (lights and switches); the gate
  runs again behind the fourth rebuild.
- 19:40 — compose smoke red on cd95876 for the first time today: jarvis-embeddings and
  jarvis-reranker each restarted twice before their first healthy answer — the ~220 MB model
  download failing on the runner and being retried, read by the job as a crash loop. The
  job now caches ./models/embeddings across runs and counts restarts from a service's first
  healthy answer, which is where a loop would show; a retried first start is a warning.
- 19:30 — the fourth rebuild (the dedupe): the fact leaves the store and only one note is
  kept; memory-forget now misses on the reply to "forget that" — "Understood, Sir." — because
  the message forbade mentioning a forgetting at all. It says the two things separately now
  (confirm now; never hint later), 04cabae; a fifth rebuild measures it.
- 19:40 — the scorecard gate 13/14 with the load's own subject: every section 100 %, idle and
  under-load latency measured; its smoke slice lost one scenario (read below). CI green on
  cd95876 but for compose smoke's retried download, addressed; ae87e96 pushed (the dedupe,
  the forget confirmation, the compose-smoke retry rule and model cache, the scorecard
  load). The fifth rebuild measures the forget confirmation.
- 19:41 — the fifth rebuild: memory-forget's first two turns pass (one note, confirmed
  forgotten); the third still hints — "You asked me to forget it, Sir — so I can't say" — a
  tool result's note two turns back does not outweigh the conversation the model can see.
  Being made structural: a successful forget scrubs the forgotten text from the thread, so
  "nothing recorded" is the only thing left to say. house-light-on green both variants,
  median 1.25 s.
- 19:43 — the hint was the transcript scrub's own placeholder, "(something the user later
  asked Jarvis to forget)", read back verbatim; it says "(nothing recorded)" now and the
  rules say the same in words (7a16547). A sixth rebuild measures it behind the clean M26
  run. The scorecard gate's fourth run: 13/14, the scorecard itself passing with the load
  measured; the fifth run keeps its smoke slice's detail.
- 19:50 — the clean M26 run: the scorecard passes (every section, idle and under load); its
  smoke slice lost chat-context-retention on both variants — "now turn it off again"
  answered with turn_on (voice, the call written out and recovered) and with no call
  (text) — after four passes of the same slice today on the same rules. The mechanism is
  sound; the reading was wrong twice. `llm: temperature: 0.6` is the sampling knob the
  routing runs under; measuring the smoke set at a lower value is the next intelligence
  experiment, recorded here rather than tuned blind. CI green on ae87e96 so far (desktop,
  compose smoke with the cache, CI, APK).
- 19:53 — the sixth rebuild: the placeholder holds — nothing hints at a forgetting — and the
  thread scenarios pass both variants; memory-forget's confirmation came as "Done, Sir.",
  which the judge does not take as the word. The message names the word (a14377f); the
  seventh rebuild measures it. Each of these is a ten-minute loop of build, smoke, judge.
- 19:57 — CI fully green on ae87e96 (seventh all-green head), compose smoke included with
  the model cache and the first-health restart rule; 01bfb30 pushed (the placeholder, the
  forget wording, the logs). The seventh rebuild is measuring the forget confirmation.
- 20:00 — M65, the catalogue, cherry-picked from its worktree (ddec4bc): a built-in `bundled`
  source of the four shipped skills, browse answering installed/sources/errors, the Catalogue
  first on Settings › Tools through the one search box, ADD BY URL for MCP; verifying on the
  branch (core 181, vitest 757, the gates running). CI on 01bfb30: the web e2e reported a
  second filled control on six screens with no web file changed; both specs name the
  control now (d47d443) and pass here 36/36. (Stamps from here on are `date` on this host;
  the five before were ahead of it.)
- 20:15 — the operator: "why is the vision model not set in the settings?" It is set —
  `vision.model: house-vision` — and the model server lists `house` and `house-fast` only, so
  the catalogue marks it missing, the chooser hides missing models, and the panel's one
  sentence blamed cameras. The catalogue now says served / served_vision / cameras for the
  vision role and the panel tells the three cases apart: no block; a model no server lists
  (load one under that alias in llama-swap, or choose a served one); served but no camera.
  What the house needs is BLOCKERS §4: a GGUF vision model served as `house-vision`. M65
  verified on the branch (gates 27/27, M55 21/21, M47 18/18, M46 15/15) and ticked.
- 20:21 — the seventh rebuild: memory-forget passes all three turns (4/4 scenarios, 7/7
  turns, median 1.58 s) — the chain of dedupe, confirmation, placeholder and the word holds.
  The operator's live reports of the last hour, gathered into M66–M72 (questions answered by
  voice and not spoken twice, settings under approval, search that falls back to the local
  engine, entities removable by voice, a faster voice, enrolment complete, a writable coding
  workspace); their new goal is to implement and verify all of them. Three go to agents in
  worktrees now; search, speed and the workspace are done here.
- 20:55 — the eighth rebuild carried M70 and M72: Piper at `--length-scale 0.9`, the
  `jarvis-workspace/` crossover mounted at `/workspace` on the core and chowned by config-init.
  Smoke set 7/7 (8/8 turns, WER 0.0 over 4 spoken samples, median 2.40 s); M72's gate 7/7 with
  uid 10003 writing under `/workspace` inside the running core — ticked. M70 6/6 but held open:
  the Settings › Voice line waits for M67's settings-registry change. M68 built here meanwhile:
  the web client tells "could not search" (timeout, unreachable, every engine unresponsive) from
  "nothing matched", asks the stack's own SearXNG after a configured one that could not, and the
  result says which instance answered; research shows the note and names the cause. Eight new
  tests; the gate is running.
- 21:06 — CI on 13b4fe6 (the M65 tick, the vision panel, the plan): four workflows green, the `CI`
  workflow red on one Playwright test, "the microphone can be muted, and stays muted across a
  reload" (`page.reload: net::ERR_ABORTED`, 294 passed). Nothing in the web tree changed but
  Models.svelte; the test passes 8 of 8 locally in 24.5 s — the runner, not the change; watched,
  not waived. At 20:37 one memory-pressure kill took the three agents building M66/M69, M67 and
  M71 with it (their transcripts stop in the same minute as my own suite's exit 137, during the
  eighth rebuild); M67's and M71's worktrees kept 552 and 566 uncommitted lines, M66/M69's
  nothing. Respawned at 21:05 from the recovered briefs, pointed at the old worktrees, with a
  memory rule (one suite at a time, commit at each step). M72 turned out to have a second
  blocker behind the first: with the workspace writable, `create_repository` over the REST API
  answered "git is not installed" — the core image had no git. Added to the image's apt line,
  pinned by packaging; the ninth rebuild (core only) carries it with M68's client.
- 21:21 — M68 committed (8c9bf26) and the research scenarios re-run on the ninth rebuild: 5 of 6
  variants. The miss, `research-deep-report`, was the model twice over: first it called
  `deep_research` with no question and told the user the work was "waiting on your
  confirmation"; re-run, it asked properly (turn 1 passed) and then wrote a correct report — 55 °C,
  12 % — with no note, although the user had said "save it as a note": the `remember` flag was
  never set. Both fall back to the user's own sentence now — the question when the call names
  none, the note when their words ask for one — with the rule unchanged that the model's
  initiative alone never stores a page's claims. Research suite 32 (3 new). Reaches the house
  at the tenth rebuild, with the agents' work.
- 22:04 — M67's four commits cherry-picked from its agent's worktree (one changelog conflict, both
  blocks kept): settings-tool suite 29, tier contract and the Android mirrors 6/6; its gate is
  running. With the registry landed, M70's last piece: Settings › Voice shows the pace as a
  number applied on restart, the note naming PIPER_LENGTH_SCALE and wyoming-piper — the key had
  to be `voice.tts.length_scale` (the registry pins key == path). Gate 14/14 on the running
  house, voice smoke 5/5 with WER 0.0 over 3 spoken samples; M70 ticked. CI on 68f1691 fully green.
- 22:07 — CI on ed1892d: the `CI` workflow red with fourteen Playwright "at rest" failures in
  look.spec and menus.spec, each finding an APPROVE for `lock_control` on a screen no test had
  touched — with no web file changed since the green 68f1691. The mechanism, in the mock backend:
  `test/raise_approval` appended a new entry per call, and the HUD test re-raises `req-hud-1`
  until the banner shows, so on a slow runner it raised twice, approved one, and left the other
  pending in the shared world for the rest of the run. The mock now treats a re-raised id as the
  same request (as the server does). HUD + look + menus together: 42/42. M67 ticked (gate 25/25
  on the branch); M70 ticked (gate 14/14, the Settings › Voice pace row).
- 22:28 — M66/M69 and M71 merged: nine commits cherry-picked over M67 with eight code conflicts
  resolved by keeping both sides (`speaker` and `spoken` both reach `converse`; the banner keeps
  M67's sentence and M66's clock). Re-verified on the branch, which found three merge seams the
  worktrees could not: a `/**` lost between two doc comments (the web build failed), `change_setting`
  missing from the Tier-3 service-twin table M69 added beside it, and — a defect of the console's
  own — the Devices screen subscribing to `state_changed` after its first load, so a removal made
  elsewhere in that gap was missed (1 in 9 locally; it subscribes first now). Gates on the branch:
  M66 28/28, M69 19/19, M71 37/37, M67 25/25. `llm.question_ttl` joined the settings registry.
  Three live scenarios written for the report run: house-confirm-by-voice (a spoken yes completes
  a held unlock), house-remove-by-voice (a spoken removal of a rig-announced sensor, asserted
  present then absent) and settings-by-voice (the operator's "demo mode", then a held setting
  change confirmed by voice). CI on 2257937: the python red was the table row; the web red a HUD
  short-screen test that passes 4/4 here.
- 23:14 — the operator's reports of the last hour, from live use: Piper reading markdown and symbols
  (M73, ticked: the reply becomes words at the one door to TTS); audio starting only after a long
  research answer was fully written (M74: early speech resumes after each tool call, the tail as
  the last chunk); every page read timing out at 20 s and every search paying the dead tailnet
  instance first (M75: text-first fetch in jarvis-browser, fallback-first for ten minutes, three
  reads at once); Jarvis near the top and the task cards floating over it (M76: the instrument
  centred, the dock the page's own under it); an alarm made twice and heard twice (M78, ticked:
  a second listener's copy of the words yields; `schedule` refuses the twin); Jarvis answering
  the enrolment phrases (M79: an enrolment in progress makes a turn yield). Planned from the
  same hour: n8n (M77), demo mode as a setting (M80), a capability denied though a tool has it
  and "ma'am" (M81), a coding job stuck in "queued" because the agents profile was not running
  (M82), and "pull things up" — panels around the instrument, movable (M83).
- 23:37 — M76, M79, M80, M81 ticked (gates 9/9, 10/10, 6/6, 8/8). M76's earlier "20 %" was the
  spec's own socket path hanging, not the layout; on a phone the page is taller than the screen
  and the instrument leads it, which the spec now says. M82 built: the stuck React app was a job
  the orchestrator had already failed — "opencode binary not installed", because the image's
  three-minute apt budget killed the package list on this network, the WARN swallowed it and the
  image had no curl — and a watcher reading its wrapper's "ok"; the watcher reads the job's own
  state now, and the image gets unzip and a proper budget. The M83 surface is under way. CI on
  0a4756b: the enrol spec had not been told about M79's heartbeat, and the voice-layout spec
  carried the wrong socket path; both fixed here.
- 23:50 — M83 built: "show me the front door camera" puts a panel beside the instrument, drawn by
  the dashboard's own widget for its kind, movable by hand, kept on the server; the first draft
  used the frame's `id` for the panel (nothing moved) and put panels over the stage, whose
  twelfth is narrower than the instrument (a panel covered it) — both found by the spec. M77
  built against a fake n8n; the key and the assistant's shape are the operator's (BLOCKERS.md).
  The phone's heartbeat needed `client?.` — the Kotlin compiles and its 227 unit tests pass here
  now, which CI had caught first.
- 00:06 — "I can't set the enrol mode when enrolling": the gate's mode was a read-only pill.
  `voice.speaker.mode` is a choice on Settings › Voice now, choosable before anyone is enrolled
  and inert until someone is (M71 follow-up; settings suites 52). Settings › Tools got the n8n
  line beside the catalogue (M77). The orchestrator image still cannot reach the Debian mirror
  from a container — a fresh `python:3.12-slim` timed out on `apt-get update` at five minutes —
  so OpenCode is not in it and M82's last checks stay red; recorded in BLOCKERS.md, with the
  watcher's fix meaning the card says so within a poll. The tenth rebuild's chain is on the
  core suite (one failure seen so far, named when the run ends).
- 00:23 — the whole core suite on the merged branch: 3464 passed, 3 failed in 44 minutes on the busy
  box. Two were the run outpacing the commits (the surface's websocket rows and n8n's table rows
  landed while it ran; both pass now); one was real — n8n fenced another server's words without
  marking the turn untrusted, the control test_device_control keeps for every integration that
  fences; fixed (e6142bb). Gradle assembled and ran the phone's unit tests on the branch; the
  tenth rebuild is up.
- 01:12 — the tenth rebuild's full report run (00:41–01:10, `docs/LIVE_TEST_REPORT.md`): 52 of 63
  scenarios, 86 of 96 turns, intent 89.6 %, routing 92.3 %, WER 5.45 % over 28 spoken samples,
  median 4.17 s, p95 21.4 s. Ten failed turns, none a new fault of the house: six were the rig's
  own — the confirm scenario left the front door unlocked for the lock scenario after it (locking
  is Tier 3 too), and the probe sensor was asserted under the topic's id where the house names an
  entity after its name — fixed in 7aa930b; one was settings-by-voice asking for "demo mode",
  which M80 made real the same evening (a held change is now the right answer; it asks for "party
  mode"); two were cancels arriving after a job the faster house had already finished (both cancel
  at once now); one is the vision role with no served model (BLOCKERS §4), and one the
  delegation scenario's known routing variance. Intent and median still miss their thresholds,
  as recorded since 26 Aug; the miss is the rig's and the model's, and the thresholds stand.
- 01:22 — after the report: M83 ticked (gate 15/15 on the running house), M75 ticked (the browser
  reads news.bitcoin.com as text in 6.0 s; the gate's own f-string fixed), M71's gate 37/37 on a
  quiet box and the harness 229/229; M74 waits for the spoken briefing scenario written for it;
  M82's binary is the mirror; M77 the operator's key. The six scenarios the report failed on the
  rig's account re-run now with the rig's fixes; the eleventh rebuild follows with n8n's taint and
  its no-tools-when-unconfigured, then the smoke set and the gates once more.
- 01:39 — the re-run of the six scenarios found one real fault of the house that no suite had: every
  spoken yes to a held action ran its tool and then said "I couldn't reach the language model" —
  the gateway in front of the model (LiteLLM) answers 400 "System message must be the first
  message" to the system note M66 put after the history, and the harness's scripted model had
  never objected. The note is a user-role note the user never sees now (the shape the nudges have
  always had), the fake model refuses a system message anywhere but first as the gateway does
  (the self-test fails first without the fix, 229/229 with it), and the answer suites are 82.
  The eleventh rebuild (n8n's taint and its no-tools-when-unconfigured): M75 9/9, M83 15/15;
  smoke 10/12 with the two misses being this fault and a briefing the model gave in one sentence
  (the scenario asks for separate sentences now). The twelfth rebuild carries the fix.
- 01:40 — CI on 8ee6d7f: all five workflows green (CI, compose smoke, the APK, the desktop build, the
  emulator end-to-end). The twelfth rebuild (the M66 note fix, core only) is up and running the
  spoken-yes scenarios and the briefing.
- 01:53 — the twelfth rebuild (the M66 note fix): every spoken yes runs its tool and the model answers —
  the removal, the settings change and its undo, the lock; the briefing's first audio at 19.6 s
  against a 25.8 s clip, M74 ticked (gate 7/7 once its own f-strings were fixed). One more fault
  of the house found by the confirm scenario's last turn: the serving layer handed a lock_control
  call back as text, the agent recovered it and the model also called it — the same request held
  twice, so the yes had two things waiting. An identical hold in a conversation is one card now
  (hold-path suites 53).
- 01:53 — CI on 27815de: one Playwright red, `responsive.spec.ts` at 1024 px, `page.goto('/dashboards')`
  aborted mid-navigation on the runner (317 passed) — the same runner-side class as the reload flake
  earlier; `/dashboards` has no client-side redirect of its own. Watched, not waived. The thirteenth
  rebuild (the one-card rule) is up and re-running the confirm scenario.
- 02:07 — the thirteenth rebuild: the confirm scenario's lock-again turn read a bare JSON call aloud
  (the model's claim, then the object, then the real sentence — all spoken), held the request twice, and
  its one-word yes came back from Whisper as "Yes, sir", which was no yes. `BareCallStripper` on the
  stream, `without_bare_calls` on the recovered text, the forms of address as edge fillers (contract +4
  cases), the hold's log line now says what it holds; recovery + spoken-answer suites 87.
- 03:16 — CI on 1fbad7e green across the three workflows. An hour lost to a suite chain whose waiter
  matched none of "no tests ran" / "ERROR: file not found" (a batch had named a file that does not exist);
  released, the suites are 87 + 134 + 60 green. The fourteenth core rebuild is up next, then the confirm,
  lock, removal, settings and briefing scenarios and the M74 gate.
- 03:23 — the fourteenth rebuild (78a331b): 8/9 scenarios, 19/20 turns, WER 0.0 over 10, median 2.2 s.
  Nothing read aloud but words now; every hold one card (the log line shows unlock and lock as two
  different requests, as they are); M74 7/7 on the house — first audio 19.65 s of a 28.17 s clip. Two
  left: the sentence the model writes before it is nudged into its call ("The front door is locked,
  Sir.") is still in the spoken clip, and the settings turn offered scenes for "party mode" (passed on
  the twelfth; rule tightened: a mode is a setting, not a scene). verify-all (M23) running since 03:21.
- 03:25 — why the guess was spoken: the voice integration wraps the agent's converse for a spoken turn
  (`fast_model`, `voice: think: false`), a closure has no `__self__`, and `_authoritative_answer` found no
  agent behind it — the M60 drop of the words written before a tool ran had never run on the voice path.
  The wrapper names its agent now; the pipeline looks both ways (voice suite +1). verify-all's first gate
  (m00) is red because `m63-dashboard-widgets.sh` was committed without its executable bit — fixed.
- 03:46 — verify-all (M23) to m17: five reds. m00 the m63 mode bit (fixed); m03 three Playwright specs
  (the dashboards kind picker, the two pairing tests) red at 379 s under the full box — the three pass alone
  in 7.7 s, so watched as load flakes; m05's check still said dashboards must be a section of House — it has
  been a destination of its own since M63 (check corrected, 29/29); m07/m14's smokes and m17's interactions
  failed under the gates' own core restarts (eight SIGTERMs 03:30–03:38, from the memory, skills and
  thread-persistence checks) — and the proactive-moment scenario read an older task's failure notification
  because the rig's wait had no `since`; scoped to the scenario now. The container-log check's Piper records
  are from 26 Aug 22:45–23:00 UTC (the restart scenarios' collateral), none in the last three hours.
- 03:51 — the audit (operator's goal of 03:3x): six read-only auditors on the running house while verify-all
  runs. From the services and quality reports so far, fixed in the tree: the orchestrator never received
  LLM_URL/LLM_API_KEY (compose passed OLLAMA_URL only — every delegation pointed at nothing behind a green
  healthcheck; now passed through, a bearer on its model calls, /healthz names its target, pinned by
  test_packaging and three orchestrator tests); `egress-audit.sh` said FAIL on a healthy isolated sandbox
  (the Proxmox kernel's `bonding_masters` file — links only now, PASS); M23's BLOCKERS check grepped a
  heading shape the file never had (every open entry now carries a Needed-by line and the check reads them);
  the duplicate M70 heading. The audit reports are kept under docs/research/audits/.
- 03:58 — the plan from the audit: M84–M93 in MILESTONES (a briefing volunteered, work that survives a
  restart, Jarvis notices, overnight reflection, a plan on the screen, stack hygiene, the claims register
  re-measured, no pass on a skip, the house by voice beyond lights, pick up where you left off). M84 built:
  the briefing block in configuration.yaml, `get_briefing` routed as its own capability, the
  briefing-on-demand scenario, gate 6/8 — the two live checks wait for the fifteenth rebuild.
- 04:07 — the audit's second pass, from the server, console and agentic reports: `read_page` read
  loopback and LAN pages through the watch integration's unguarded fallback (address-checked now, the
  private space refused, the browser's refusal final; test_watch 15); empty includes made a phantom
  automation and scene (test_core 44); the dead M37 `n8n:` block and a loader that refuses duplicate
  keys; three settings rows told the truth (test_settings 34); the claimed-action guard learned the
  record verbs; `get_user_context` on the house clock; the approvals seed and the n8n row read the
  server's shape, the mock answers in it (vitest 770). M94–M97 planned; the six audit reports kept
  under docs/research/audits/.
- 04:25 — M85 built: the engine puts an idempotent job it still has back to queued after a restart
  ("picked back up after a restart"), the background worker's factory is registered (register_kind had
  no caller), background jobs are idempotent by design, a resumed job plans only what is left, and the
  completion says it was picked back up; gate offline 4/5, the scenario restarts the house between
  turns. Found on the way: the rig's stack ground snapshots the whole config directory and restores
  it — with a core restart — at the end of every live run, so a `narrate:` block added during verify-all
  vanished; the restore now leaves the operator's own files alone (rig tests 49). The narrate block is
  back: doors, locks, smoke/gas, devices going unavailable — the first half of M86.
- 04:31 — M86 built: a narrate rule may carry an offer — the lock unlocked, the garage left open —
  asked as a question with Yes and No through companion.ask (the M66 table judges the yes), the offered
  service run only on the yes; the house's lock and garage rules carry theirs and do not wait for
  morning; narrate suite 15 (+4); gate offline 3/5, the two live checks wait for the rebuild.
- 04:38 — M87 built: `memory.reflect` reads the day's conversations from the archive (never a channel or
  background thread, never a fenced turn), asks the model once for what is new against what is known, keeps
  it as `learned` with the day's tag, skips what the user asked to forget (the store now keeps the forgotten
  texts across reloads) and what is already remembered, and says what it learned in a note and a card;
  nightly at 03:30 on the house; `jarvis/memory/reflect` for the console; the rig's `reflect: true` turn is
  the night on demand. Reflection suite 6, memory 20, rig 49; gate 4/6 offline, the live half waits.
- 04:41 — M89 built: SearXNG's granian bind (GRANIAN_HOST from SEARXNG_BIND_ADDRESS — the image ships `::`),
  the console image runs as `node`, the live sandbox pinned where it runs (network none, uid 10001, ro,
  cap_drop ALL, one mount), RUNBOOK/DEVIATIONS/the isolation matrix true; packaging +3; gate 3/5, the two
  stack checks wait for the recreate the fifteenth does after verify-all.
- 04:50 — M88 built: the surface follows a background job on its own — a `task` panel (the job's own
  card, steps and a stop, from the task record) while it runs, a `note` with the result when done, nothing
  after an error; `surface: plans: false` turns it off; the console and the mock mirror it (surface suite 7,
  surface spec 4/4, svelte-check clean). Also: SearXNG bounded (m28's rule), an upstream engine's refusal at
  init allowlisted in the container-log check (it failed three gates' smoke slices), architecture.md no
  longer says Ollama, and the second 07:30 alarm M78's fault left on the house was removed.
- 04:54 — M90 ticked: the claims register's suite-size tables regenerated from the commands beside them
  (core 2967 test functions in 110 files, desktop 536, browser 219, vitest 710, Playwright 234, JVM 227,
  91 gates), the per-file table re-measured, the four pessimistic rows moved to what the rig proves, the
  WebGL-orb row and the Ollama compose comment gone, the speaker row names its own skip, ISSUES gains the
  two asserts-without-evidence probes; gate 6/6 — it reruns the commands and fails when the table drifts
  (it caught its own first drift: the gate it added made 91).
- 04:56 — M92 built: ten scenarios for the house beyond lights (a thermostat, a window, the speaker, the
  vacuum, a fan and a switch, the coldest room, the moon, a note appended, "turn on the light" answered by
  the next turn, the surface by voice), the rig's `surface` expectation and `surface` capability; m37's two
  config checks now read the M77 block (the M37 one is gone). Rig tests 49; the live half waits for a quiet
  house.
- 05:06 — M93 built: `?conversation=<id>` opens a thread on the voice screen (its transcript, or a new thread
  under that id), the address bar follows the open thread, the page carries `data-conversation-id`; the rig's
  browser transport names the thread it holds and the one-turn rule on `-ui` variants is lifted —
  thread-continuity runs through the real console now. Spec 2/2, rig tests 49; the live half waits. m37's
  gate names M77's tests (10/10).
- 05:12 — M95 built: finished background, research and coding work is announced through companion.notify
  (spoken when present, a card when not; `notifications: speak_completions: false` turns it off); two
  read-only tools from the record — `recent_moments` (the inbox) and `explain_last_turn` (the previous
  turn's tools and memory from the archive, never reconstructed); two scenarios; notifications 18, agent +2.
- 05:16 — M96 built: `assist_pipeline/stop` cancels a run at the server; the run ends `run-end
  {interrupted: true}`; the console's barge-in sends it; the mock answers it; test_api +1 (a run stopped
  mid-answer, a run not in progress is not_found). Gate 2/3, the live check waits.
- 05:20 — M94 built: the device a request came from rides from the pipeline through converse to one line
  after the speaker ("the request came from the device 'X' in the kitchen: 'here' and 'this screen' mean that
  device"), is remembered per turn, and tell_user goes there unless a device is named; agent +1, voice +1,
  device-control +1. The live check registers a throwaway device and asks which device it is on.
- 05:27 — M97 built (routines and what's new; timers as entities follow): create_automation reads the
  routine back ("weekdays at 07:00: turn on light.kitchen_lights") and tells the model to say it; list_automations
  names the authored and installed ones; a tool the model writes itself defaults to tier 2 and is a capability
  card, as is a new MCP server or an installed extension; whats_new answers from those cards. Automation API +2,
  create_tool +1, notifications +1; two scenarios; the gate's live half waits.
- 05:31 — M98's first item built: the phone asks the server for the speaker gate's mode the moment its
  socket registers (the channel's `afterRegistered` hook; the host's `refreshSpeakerGate`, the same GET and
  expression as the enrolment screen), so a new phone against an enforcing house is no longer refused every
  turn while Settings says the opposite; the on-device-turn mirror pins it; gradle building.
- 05:37 — M91's first half: `check_pytest` in lib.sh (fails on failed, error, no tests ran, and on skips
  beyond what the gate allows; the summary printed), `VERIFY_GATE` exported by verify_begin so a gate's live
  slice writes results-<gate>.json beside the shared file, `make test-web` no longer swallows Playwright.
  m54's two reds fixed: the M93/M96 prose had split the commands table the documentation test reads (and
  `assist_pipeline/stop` had no row), and the catalogue test carried a loopback gateway URL. The gates that
  run harness-backed suites move to check_pytest once verify-all is done with lib.sh.
- 05:39 — M98's second item built: a Tier-3 action jarvis-core holds is raised on the phone's own consent
  screen (keyguard-aware, tapjacking-proof) with the server's summary and its clock, and the decision goes
  back as `jarvis/approve` on the assist socket; a held question still comes through companion.ask; the
  prompt mirror pins it (20/20); gradle building.
- 05:43 — M98's third and fourth items: a typed field on the phone's voice screen runs the same intent-stage
  pipeline a transcribed sentence takes (mirror check), and the tier contract records the phone's ask-once on
  tier 2 as the phone's own consent for its own device (both mirrors green, server contract suite 10). gradle
  green. Left under M98: PHONE TASKS' way in, and a room for a companion device on the console (M94's area).
- 05:44 — the M91 gate: check_pytest proven on a skipping suite and an empty one (2/3); its third check
  counts 87 pytest checks across the gates that still read the exit status — the conversion script is
  ready and runs once verify-all has stopped sourcing the gates. The fifteenth's launcher now runs every
  planned milestone's gate on the rebuilt house after the quiet pass.
- 06:29 — CI on 17e9123 red twice for tonight's making: the M86 no-answer test had asked for two questions
  from a sequence the narrator answers once (an entity's first state is not a change, and the second unlock
  fell inside the per-entity debounce) — it starts from a known lock now, 400 s apart (sensors 199); the
  reactor-motion mirror reads the first breath of the phone's onBusEvent and the M98 hook had pushed the
  iris out (moved below the rows); the register said the prompt mirror had 19 checks, M98's made it 20.
  The dashboards kind-picker spec passes alone 15 times running; its gate reds are load.
- 07:44 — verify-all finished at 07:38 after 15,418 s: 50 green, 34 red, the live suite timed out at 2400 s;
  the fifteenth rebuilt the core, the orchestrator, SearXNG and the console and started the quiet pass
  (a second launcher instance, left from the first mis-launch, ran beside it from the wrong directory and
  was killed). The orchestrator's OpenCode: the pinned 0.6.4 asset no longer unpacks (the project moved to
  anomalyco/opencode; the build's `|| echo WARNING` hid it) — pinned to 1.18.23, given the house's provider
  config written at startup (`OPENCODE_CONFIG`, a writable HOME on the tmpfs) and `house/<model>` names, and
  it answered "ready" through the gateway from inside the container. BLOCKERS' mirror entry resolved.
  CI on 0e38c70: one Playwright flake on the runner (enrol's four-states `release` before the route was hit).
- 08:06 — M98 ticked (16/16): PHONE TASKS' way in built as `import_tasks`/`list_tasks` builtin phone actions plus the
  `phone-tasks` skill; the gate's fake phone was sent `torch-on-plug-in` by the model. Found in the container logs while
  reading the quiet pass's stack-logs reds: the nightly memory reflection (`reflect_at: "03:30"`) died on its first line
  at every start — the raw string reached `next_time_of_day` — reported only as "Task exception was never retrieved"
  at shutdown, so it has never run; parsed once now, two tests. The fifteenth's one voice miss (a refused unlock, no
  hold for the spoken yes) reworded in the rules and made the first turn's assertion (`decision: hold`); the delegation
  scenario's routing miss (two research calls for a two-job request) addressed in the tool's and rule 4's wording —
  both are the next live run's to show. The house's clock says CDT because the operator saved America/Chicago from
  the console on the 26th (recorded then); not changed.
- 08:10 — M91 ticked (3/3): 76 gates converted to `check_pytest` by atomic replace while the quiet pass ran; no suite with a skip mark skips on this box (speaker+ssrf 103, harness selftest 43).
- 08:22 — M97's timers built (`integrations/timer`, 10 tests, scenario, gate probe); waits for the sixteenth rebuild. CI on 2661073: the android mirrors job is red — being read.
- 08:44 — M99 built (retry, live Areas/schedule/settings, a room for a phone, the unshown fields; spec 6/6); waits for the rebuild.
- 08:51 — M99 ticked (9/9): the quiet pass rebuilt the stack at 08:38 (core + console) from this tree, so the live half ran on it; the house now also runs the timer integration and the reflection fix ("overnight reflection at 03:30" in its log — the first time).
- 08:56 — M97 ticked (11/11 on the rebuilt house; timer.gate_probe active → finished, card left). Gate fixes on the way: `check_pytest` under
  `set -e` (a failing suite, or no summary line, ended the gate silently), m45 allows the M65 catalogue's index.json, m42 counts the
  delegation's backends on the lead task's children (one `delegate_to_agents` call is now the right shape), m97's quoting.
  Quiet pass so far: m37 10/10, m40 13/13, m45 15/15 on re-run, m48's pairing red is a load flake (passes alone).
- 09:01 — quiet pass reds read: m48/m49's pairing-secret test is a load flake (e2e.spec.ts 44/44 alone in 1.1 min; the gate ran it
  beside my console builds and spec runs); m49's tab pin said five where the bar has had six since the dashboard became a destination
  (610ec24) — pinned to the six in order. CI on 6c816c8: `python · jarvis-core` red in 3.6 min with no FAILED line on the check run
  (the job's own annotations carry pytest's FAILED lines and there are none) — the log needs a token (`gh auth login` would open it);
  a Python 3.12 venv (`.venv312`, uv) now runs the suite the way CI does, to find it here. A local full run on 3.11 was stopped:
  the voice tests found the live stack's wake service on 127.0.0.1:10400 and waited on it.
- 09:19 — M100 built (memory per person, consolidated; gate 10/11, live half after the next rebuild). The rig records a matched
  task's children at scenario end (m42's check reads them); m54's speaker-mode why line trimmed to the one-line rule (133 chars).
  CI on 07d9593 green — the python red on 6c816c8 did not recur (no FAILED line then either; a 3.12 local run is 65 % in with none).
- 09:35 — quiet pass finished 09:25 (8 green / 26 red of the 34); the plan pass began (m84 red on the rig's own milestone-name
  regex, two digits — M100 has three; fixed). Reds read: m42's check counted backends on the wrong house (now the lead's steps in the
  rig's record); m54's two why lines over 140; m79's grep behind a nullable client; m82's `docker exec` without the app's HOME
  (the image sets HOME=/tmp/home now); m59's watch refused the rig's loopback fixture by design (the harness allows the fixture
  host); m74 read another slice's results (runs its own); m25's rule gave m82/m88/m89/m101 live slices (m86/m94/m96 still
  have none — a notice, a room and a stop by voice need scenarios the rig cannot yet drive); chat-context-retention (text) and
  resilience-core-restart's second turn are the model's variance ("it" turned on again; "the bedroom has no ceiling lights,
  shall I…"); m03/m50/m48/m49/m55 are the pairing tests under load. M101 built. The 3.12 run of jarvis-core: 3537 passed,
  1 failed — the store race, fixed.
- 09:37 — M86 ticked (plan pass 6/6 on the house). m85's live run hit the two-digit milestone regex (fixed since) — re-run pending; m87's card is not made when extraction already learned the day (the scenario's premise).
- 09:39 — M89 ticked (plan pass 6/6). The sixteenth rebuild is queued behind the fifteenth's report run (core, orchestrator, console), then the gates the tree changed since 08:38, then its own report run.
- 09:49 — plan pass done 09:47 (m86, m89, m94, m96, m97 green; m84/m85/m87/m88 fixed since and queued for the sixteenth;
  m92 3/4, m93 6/7, m95 4/5 read below). M94 and M96 ticked. The rig gained `stop_after:`/`interrupted`, `do: states`,
  `setup: device:` — scenarios stop-means-stop, in-here-by-voice, notice-garage-door, and m86/m94/m96 have live slices.
- 09:57 — M102 built (Jarvis learns from its own mistakes): the review integration, the guard and the stop on the trace, the self-review scenario; live half after the rebuild.
- 10:12 — M103 ticked (7/7). M102 built; M104 (Jarvis proposes a routine) built with its suite — the miner over the recorder, a card and a question, a yes through create_automation.
- 10:43 — the fifteenth's report run (09:47–10:20, 98 scenarios): 57/98, 103/128 turns, intent 80.5 %, routing 84.4 %,
  WER 1.3 % over 31 (worst 28.6 %), median 2.51 s, p95 14.55 s — under every threshold but WER, and mostly one event: the
  house closed the rig's sockets (uvicorn's 1012, "service restart") around `notes-append`, and the rig, which only
  reconnects after its own `restart:` turns, failed the thirty stack scenarios after it on a dead socket — voice turns as
  `ConnectionClosedError`, text ones as "tasks were []"/"had []" while the console beside them showed the tasks. The
  recorder cannot say who restarted the house: the restore at the end of a protected run puts `jarvis.db` back as it was
  at the start. Fixed in the rig, not the thresholds: a listing on a socket the house closed raises with the close code
  instead of reading as an empty house; the runner reconnects between scenarios and puts every unasked close on the
  containers row; the containers row counts the core's boots from its own log (which survives a restart) against the
  ones the run ordered. Two rig crashes from the same run fixed (`Link.base_url`, `expect[...]`). The real misses that
  remain from the half that ran: ask-which-light (names no lights / turns one on), house-cover and in-here-by-voice
  (the model's routing), memory-reflection (the model's cheek), explain-yourself/sensors-compare/surface-by-voice (the
  garage sensor, order-dependent — announced per scenario since), vision-look-fixture (M56, no served model). Also this
  hour: CI's core leg names errored tests now (`-rfE`); the claims register re-measured; jarvis-desktop 853, jarvis-browser
  349, orchestrator 24 green; Android lint clean; the console suite 325/347 under load and 23/23 of the four failed files
  alone. The sixteenth's house (10:21 image) so far: m84 8/8, m74 8/8; m85/m87/m88/m42 red, being read.
- 11:19 UTC — the sixteenth's house (10:21 image): m84 8/8, m74 8/8, m93 7/7, m97 11/11, m98 16/16, m101 10/10, m104 4/4 —
  M84, M93, M101, M104 ticked. Its reds, each read to a cause and fixed where the fault was: m100 (the rig spoke inside the
  house's twenty-second enrolment window — `enrolling` on the speaker payload, the rig waits on it), m85 (the rig settled
  the scenario's own audit before restarting — earlier scenarios' tasks only now), m87 ("that store is for facts about you"
  — the remember wording), m88 (`code_task` for an audit — its description), m42 (two research pieces — the delegate
  wording), m94/m96/m102 (a typed turn was one REST call, with no device and nothing to stop — typed turns run through
  the pipeline as the console's do), m92 (cover.toggle for "close" — the toggle's wording; "clear the screen" — the
  tool's; ask-which-light said its question twice over REST — a unit test says the agent says it once), m95 (a reading in
  the summary needs no tool; a one-minute reminder became a timer — the timer's and schedule_task's wording), m99 (a
  probe reading frames by position), m86 (the offer went to `companion.ask` alone: with no phone it sat in a queue, was
  never on the record, and no spoken yes could reach it — an offer is a held question now, on the bar, on a phone, on
  the record, answered anywhere), m82 (the coding job's "2/3 checks passed" read back as "all tests pass"), m59 (the
  watch reply left out "changed once"). CI on 313a8b8: all five workflows green — the core leg lists errored tests
  since `-rfE`. The sixteenth's report run started 11:04; the seventeenth (core rebuilt from this tree, fourteen gates,
  a report) is armed behind it.
- 11:46 UTC — the sixteenth's report run (11:04–11:45, 99 scenarios, the 10:21 house, the rig as of 11:04): 82/99, 148/165
  turns, intent 89.7 %, routing 94.2 % (over its floor), WER 1.8 % over 51, median 2.95 s, p95 13.41 s. The containers row is
  clean — no socket the house closed, no boot the run did not order — so the fifteenth's 57/98 was one outside event.
  Seventeen misses: house-cover ×2 ("close" reached the generic turn_off on a cover), house-vacuum (answered, no start),
  in-here-by-voice (typed over REST, no device — asked which), memory-per-person (the rig's Piper voice was filed under
  the operator's own profile, not the one it enrolled — the gate's observe mode names the nearest person), memory-reflection
  and notice-garage-door and surface-by-voice's "clear the screen" (fixed on the tree, not on this house), self-review and
  stop-means-stop (the rig counted its own stop's cancel), resilience-core-restart's second turn, task-survives-a-restart
  (no "picked back up" completion in 300 s), timer-by-voice ×2 ("cancel the tea timer" left it active), what-did-you-tell-me
  (the judge read the record's other real entries as invented), vision-look-fixture (M56). The seventeenth runs next.
- 12:04 UTC — the seventeenth's house (11:45 image), first gates: m88 9/9 → M88 ticked; m86 red — the demo garage door had
  NO device_class (`DemoCover`'s second argument is its unique id), so the shipped rule could never match it on any house
  (fixed: `garage`, `window`; the tables know `garage`/`gate`; a test opens the demo door against the shipped rules);
  m85 red — the task engine popped a job off the queue as it started it and persisted only the queue, so the one job a
  restart interrupts was never in the store to pick back up (fixed: running items are persisted; a test goes through the
  engine's own path); m87 red — the judge, which sees the criterion and the reply but not the utterance, read "the
  youngest in the house" as invented (the criterion carries the words now). M105 planned and built: the operator's own
  speaker profile accepted the rig's synthetic Piper voice (4.15 under 4.93 — 38 dimensions of timbre and variability
  outvoting 8 of pitch at 9.35); blocks are equal votes and one block twice over the threshold is a named veto; 68
  speaker tests hold. CI's core leg went red again on 79bb9b4 with no FAILED/ERROR row (3.8 min, a full run); the step
  now annotates pytest's tail; a full 3.12 run of HEAD is queued here.
- 12:16 UTC — the seventeenth, mid-way: m94 7/7 ("turn off the lights in here" typed from a tablet in the kitchen is the
  kitchen — typed turns on the socket); m102's review answered from the record ("a run was stopped by you — twice, in
  fact — and one tool call was cut off") and the judge failed it for naming the second real stop — criterion fixed;
  m96's scenario passed and its probe stopped a run that had already finished (a fixed second after intent-start) —
  it stops on the first token now; m42 sent both pages to the summarizer, correctly, so the scenario's second piece is
  the coder's tests; m100 still the old scoring (M105 is on the tree, not this house). The rig refuses a turn key it
  does not read (a `reply_means` one level too shallow had loaded silently). CI's core leg: red again with nothing on
  the check run even with the tail annotation — the job log needs a token (BLOCKERS); the changed files pass under 3.12
  here (472), the full run is in progress.
- 12:19 UTC — M95 ticked: 5/5 on the seventeenth's house (explain-yourself from the trace, what-did-you-tell-me from the record).
- 12:25 UTC — the seventeenth, late gates: m99 9/9 (the probe reads replies by id), m59 17/17 (the watch says "changed once" now); m82 running, then its report. M105's console half: TEST names the refusing block and shows the three (8/8 e2e, 27 unit).
- 12:26 UTC — the seventeenth's gates done: m82 6/6 → M82 ticked; green m88 m94 m95 m99 m59 m82; red m86 m85 m87 m42 m100 m96 m102 m92, every one read to its cause and fixed on the tree for the eighteenth. Its report run started 12:25.
- 12:38 UTC — CI's red core leg, named at last by a full 3.12 run here (3576 passed, 1 failed in 23 min): `test_ask_and_answer` pinned that a request raised with no conversation is nobody's, and 6625b61 had made every house-raised request everyone's. Narrowed to the house's QUESTIONS (a notice's offer), never its held actions; pinned three ways; pushed behind the running CI poll. The check run never named it, even with `-rfE` and a tail annotation — the step's `::error::` lines do not reach the check-runs annotations. M105's phone half built (gradle green) and mirrored.
- 13:19 UTC — the seventeenth's report run (12:25–13:17, 99 scenarios, the 11:45 house): 80/99, 141/158 turns, intent 89.2 %,
  routing 92.5 %, WER 1.3 % over 49, median 4.50 s, p95 22.79 s — the times are contaminated: a full Python 3.12 suite, a
  gradle build and a Playwright run shared the box with it (one suite at a time, and I broke it). Nineteen misses. A new
  root cause among them: the held offer for the front door lock — left unlocked by an earlier scenario, and now a house
  question every conversation can answer — made a bare "yes" ambiguous ("2 things are waiting on you", house-confirm-by-
  voice and house-remove-by-voice) and was relayed as the REPLY to "turn on the swimming pool light". The rest: the
  lock left unlocked between scenarios (the rig's sweep does not put a lock back), a 15-second timer that had finished
  before "how long is left?" arrived under that load, explain-yourself's second criterion refusing the honest "no tool —
  the summary", cover.set_cover_position for "close", two house commands routed to a task, a stalled run (240 s), the
  engine pick-up and M105 (on the tree, not this house), M56, and piper's "Task exception was never retrieved" after a
  stopped run. The eighteenth started 13:17.
