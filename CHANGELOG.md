# Changelog

Every milestone in `MILESTONES.md` adds an entry here when it is ticked, in the
same commit. Format: one heading per release (or `Unreleased`), one line per
change, newest first, each line naming the milestone it belongs to. Behaviour,
not diff: what a user or operator can now do, or can no longer be bitten by.

## Unreleased

### Added
- M17 (interactions): the things Jarvis says without being asked are now kept. A new
  `notifications` integration records every proactive message — a finished job, a failed one,
  the briefing — fires `jarvis_notification` as each is made, and lists them over websocket and
  REST, so "what did you tell me earlier?" has an answer. The console draws them as **moments**
  rather than toasts (a toast is gone in four seconds and these arrive when nobody is looking),
  each with a WHY AM I SEEING THIS? that names the bus event that produced it; the phone gets
  the same records on its own board. Conversations are searchable (`jarvis/conversation/search`
  returns the line that matched, not just an id), a thread resumes with its earlier turns in
  front of the model after a restart, and two clients on one thread see one transcript —
  `testing/e2e/test_threads.py` and `test_continuity.py` prove both against a real server. The
  briefing's schedule and sections are editable from the console without a restart. And a reply
  that used remembered notes now carries which ones, rendered under it as WHY THIS ANSWER:
  personalisation nobody can inspect is indistinguishable from a machine making things up.

### Added
- M15 (memory): Jarvis now learns facts in passing — after a turn that states one, a single
  bounded model call proposes durable facts, stored as `source: extracted` and linked to the
  turn, so they can be told apart from what you dictated and deleted on that basis. A word
  ("off the record") turns it off for a turn, the transcript itself is never stored, and every
  extracted fact goes through the same redaction and refusals as a dictated one. Plus the half
  that makes "it's your data" true: `GET /api/memory/export` (JSON or markdown, as a file),
  `memory.wipe` — which clears the **vector sidecar** too, because a store that reports itself
  empty while a semantic index still ranks the old text has deleted nothing — and a `/memory`
  console page that shows every note, where it came from, and the two buttons the model does
  not get. `evals/memory_eval.py` proves store → **restart** → retrieve → forget → export →
  wipe against a real server.
- M16 (notes): documents, as markdown files under `<config>/notes/`, with YAML frontmatter,
  `[[wiki links]]` resolved both ways and a SQLite FTS5 index that is *derived* — delete it and
  it rebuilds from the files. Tools `note_create`/`note_append`/`note_search`, a
  full REST and websocket API, a `/notes` console page, a NOTES tab on the phone, and two
  desktop actions (`save_note`, `find_note`) so a snippet on the laptop lands in the house.
  Research now writes its reports here instead of into memory: a four-page report as a
  "remembered note" pushed the user's actual preferences out of a bounded store and put four
  pages of prose in front of every "turn the lights off".

### Added
- M13 (skills): drop a folder with a `SKILL.md` in it into `config/skills/` and Jarvis knows
  it — the open Agent Skills format, YAML frontmatter and a markdown body, no code and no
  restart beyond `skills.reload`. Only the **name and description** reach the system prompt;
  the body arrives when the model calls `use_skill`, because twelve skills of two thousand
  words each would be twenty-four thousand words in front of every "turn the lights off". A
  skill cannot run the scripts beside it (the loader has no execution primitive at all), cannot
  grant itself a tool or lower a tier, and cannot forge a prompt section through a description
  with a newline in it. WS `jarvis/skills/list|get|reload`, REST `/api/skills`, a panel on the
  console's Tools page that also lists the skills that FAILED to load and why.
- M14 (MCP inspect): `jarvis/mcp/inspect` (and `GET /api/mcp/servers/<name>/inspect`) returns
  one server in full — protocol version, server info, every tool's JSON schema, and
  `last_error`, which is the field that matters: a server missing from the tool list told
  nobody why. The console draws it behind the INSPECT button with a **test call** per tool that
  goes through `jarvis/tools/call` — the same approval gate the model uses, because a
  console-only execution path would be a way around it. A server that is down is now retried
  automatically with per-server backoff (30 s doubling to 30 minutes), so an MCP server that
  starts a few seconds after jarvis-core no longer waits for a human to press reconnect.

### Added
- Live interaction testing (M24/M25, folded in mid-run): `testing/live/` talks to Jarvis the
  way a person does — the user's speech is synthesised locally with Piper in `en_US-amy-low`
  (Jarvis answers in `en_GB-alan-medium`, so no transcript can be attributed to the wrong
  side), delivered through the audio-input API **and** through a real headless browser's
  microphone, and Jarvis's spoken replies are transcribed back with the same Whisper the
  system itself uses. Scenarios are YAML fixtures asserting on the house (the service called,
  the state changed, the task created), with a local-LLM judge only where a deterministic
  check cannot express the criterion — and every verdict logged with its reason. 27 scenarios
  ship covering every capability; the 15 whose capability does not exist yet carry
  `gated-on: <milestone>` and fail in full mode until it does.
  `bash scripts/verify/live_interaction.sh --implemented-only` is now part of every remaining
  milestone's verification, and `make verify-all` runs the whole ungated suite.

### Fixed
- The spoken reply carried every round's words, not the answer: a turn that guessed before
  calling a tool said both out loud — "The bed light is already off, sir. The bed light is
  now off, sir." — and after a narrated-call correction it read the correction out too
  ("You're right, sir — I described the check without running it"). Text from a round that
  then called a tool is now `ConversationResult.preamble`: still streamed, so a surface can
  show the working, and no longer spoken, archived or returned as the answer. Found by
  talking to it; see `ISSUES.md`.
- A turn whose only words were written before a tool ran came back **empty** — a blank bubble
  on the console and silence on the speaker. The "it said nothing" fallback was asked of
  everything streamed rather than of the answer.
- The voice path spoke the stream, not the answer, so the preamble fix above did not reach it:
  `PipelineRun` now prefers the agent's own final text when the two differ.

### Changed
- The console's palette, type and motion move to Reactor II's values (accent #4fe3ff, Barlow /
  Space Grotesk / JetBrains Mono, 160/260 ms); Compose is enabled in the Android build for the
  generated theme (uncompiled here — M08). Jarvis Code, Android and desktop parity tests now read
  `design/tokens.json` instead of `tokens.ts`.

### Changed
- M09 (one model endpoint): `LLM_URL`/`LLM_MODEL` are the first-class settings everywhere —
  `configuration.yaml`, `.env.example`, compose, the smoke script and the worked example —
  with `OLLAMA_*` kept as a fallback. The orchestrator's fan-out speaks
  `/v1/chat/completions` instead of Ollama's `/api/chat`, and no longer bolts an `ollama/`
  provider prefix onto a model name. The dashboard readout polls `/v1/models` (every
  OpenAI-compatible server serves it) instead of Ollama-only `/api/ps`.

### Added
- M12 (hooks): two named trigger platforms, because both were being written as raw `event`
  triggers and both were wrong. `platform: wake_word` fires once per detection instead of
  fourteen times per voice run, and can be scoped to one satellite (`device_id:`), one word or
  one pipeline. `platform: task` fires on the transition — `started`, `completed`, `failed`,
  `cancelled` are four distinct bus events — so "tell me when the research is done" is one
  notification rather than one per progress tick, and a cancelled job is not reported as a
  failure. `event_data:` keys may now be dotted paths into nested payloads
  (`parcel.carrier`, `steps.0.status`), which is the only way to match anything on this bus.
  `jarvis-core/docs/hooks.md` and `config/examples/hooks.yaml` document all five hooks
  including the webhook's "the id is the secret" and `webhook_require_auth`.

### Added
- M11 (plan → act → verify): background work with more than one thing in it is now planned
  before it is done. The plan's steps land on the task, so the console shows what Jarvis
  intends before it starts; each step is acted on as an ordinary tool-using turn; each outcome
  is judged by a separate call that can see the outcome but not the argument for it; a "not
  done" verdict re-plans what is left, twice at most. `tests/contracts/tool_tiers.json` makes
  the tier meanings (1 direct · 2 background + notify · 3 approval) one table that core, the
  console and the Android mirror all read, and the MCP config comment that promised a
  confirmation tier 2 has never done is gone.

### Fixed
- M11: `run_background_task` looked the conversation agent up under a key nothing sets, so
  every background task the assistant accepted failed with "there is no conversation agent on
  this server" after two retries. Unit tests had mocked past it; the end-to-end test against a
  real server is what found it. A planned task also no longer shows two invented steps
  ("work on it", "write it up") in front of the plan it actually chose.

### Added
- M10 (task engine): `jarvis/taskengine.py` — a bounded queue with a concurrency cap
  (`llm.max_concurrent`, default 2, because every worker ends up talking to one model server),
  retries with jittered backoff, cooperative cancellation that is not a failure, and a queue
  persisted beside the task list so work that was waiting is still waiting after a restart.
  `run_background_task` now actually runs the work; scheduled research and coding jobs queue
  (reminders do not); finished code runs and their diffs are written down instead of living in
  memory; the orchestrator reloads its jobs (`load_persisted` had never been called);
  `jarvis/tasks/retry` puts a finished task back on the queue.
- M09: `llm: local_only:` (default on) resolves the model server's URL at startup and refuses
  a public address — "100 % local" was a promise nothing verified.
- M06 (InfluxDB): `metrics/sources/influx.py` reads an InfluxDB the operator already runs —
  it works out from `/health` and `/ping` whether it is 1.x (InfluxQL) or 2.x/3.x (Flux),
  asks the server for the schema, keeps the token in a header, and never writes. Proven
  offline against a fake of each generation; `scripts/check-influx.py` is the live check.
  A `homelab-gpu` example dashboard ships.
- M05 (dashboards): `jarvis/metrics/` defines one shape for anything graphable and ships the
  `internal` source — the recorder's entity history, this host's load/memory/disk, and counters
  for turns, tool calls and task outcomes; `integrations/dashboards/` stores layouts per token
  (a token is the identity), with a shipped `homelab` example; `/dashboards` draws six chart
  types with no charting dependency and lets a widget be added, resized, moved, swapped and
  removed from the keyboard; `tests/contracts/dashboard_layout.json` binds both sides.
- M04 (task-execution UI): `tests/contracts/task_events.json` binds server and console;
  `TaskRegistry` gains `tool_started`/`tool_finished`, `output()`, `raise_if_cancelled()` and a
  persisted per-task log replayed by `jarvis/tasks/log`; the coding agent and research emit tool
  calls and stream their output live; orchestrator delegate and code jobs are registered as tasks
  and polled; `/tasks/[id]` shows the plan, live calls, streaming output, a timeline and cancel.
- M03 (web console on the system): every screen declares its status through `ScreenState`
  (loading · empty · error · offline), `src/lib/screens.ts` is the manifest three things read,
  `+error.svelte` catches a thrown route, and `src/lib/online.ts` tells a dead relay from a dead
  network. Page-level horizontal clipping is gone and `e2e/responsive.spec.ts` proves every
  screen fits at 360/414/768/1024/1440; `e2e/states.spec.ts` drives every screen into the states
  it can be driven into; `e2e/controls.spec.ts` requires every control to be nameable, focusable,
  and — when disabled — to say why. `jarvis-web/src` is clean under the token lint.
- M02 (component library): `$lib/ui` — 18 token-only components (Button, IconButton, Input,
  Select, Toggle, Field, Panel, Row, Pill, Toolbar, Tabs, Dialog, SkeletonRows, EmptyState,
  ErrorState, OfflineState, `ScreenState`, and the `Reactor` instrument), each with a
  `@component` doc block, a README section, an SSR test and a live demo on `/styleguide`;
  the eight hand-copied empty states across the console are now one component.
- M01 (design system): `design/tokens.json` is the single source of truth (Reactor II);
  `python3 design/build.py` generates `tokens.css`/`tokens.ts` (web), `tokens.py` (desktop),
  `JarvisTokens.kt` + a Compose `JarvisTheme.kt` + `tokens.xml`/`colors.xml` (Android), with
  `--check` for drift; `scripts/verify/token_lint.py` (ratchet baseline) fails
  `make verify-all` on any new hard-coded colour/spacing/type/motion value; `/styleguide`
  renders every token and the four screen states; Barlow, Space Grotesk and JetBrains Mono
  are self-hosted; `.claude/skills/jarvis-design-system` + `.claude/rules/design-system.md`
  bind future sessions. `make tokens`, `make tokens-check`, `make token-lint`.
- Design: three divergent visual directions for the redesign (Instrument, Ledger, Reactor),
  each mocked on the chat/voice, live-task and dashboard screens as static HTML with inlined
  tokens under `docs/design/`, rendered headlessly to `docs/design/shots/` by
  `docs/design/screenshot.mjs`. Direction C chosen and revised as Reactor II
  (`docs/design/c2-reactor.html`: the reactor as an instrument, flat panels, sliding-underline
  tabs, real motion; stills + WebM clips via `screenshot-c2.mjs`). M01/M02 build from it.
- M00: `make verify-all` and `scripts/verify/` — one check script per milestone; a
  failing check is the definition of unfinished work. Playwright's port is now
  `E2E_PORT` so the suite runs beside a live install.
- Agent-intelligence targets folded in: `docs/AUDIT.md` §10–15 (research engine, coding
  agent, subagents, memory, notes, user interactions) and milestones M15–M20 with verify
  scripts `m15-memory.sh` … `m20-subagents.sh`; desktop automation, the phone flag and final
  integration renumbered M21–M23 (`m21-…`, `m22-…`, `m23-…`).
- Planning artefacts for the milestone run: `docs/AUDIT.md`, `MILESTONES.md`,
  `PROCESS.md`, `BLOCKERS.md`, this file.
