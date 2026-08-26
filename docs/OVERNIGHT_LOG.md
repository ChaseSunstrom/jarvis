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
