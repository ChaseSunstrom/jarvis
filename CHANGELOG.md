# Changelog

Every milestone in `MILESTONES.md` adds an entry here when it is ticked, in the
same commit. Format: one heading per release (or `Unreleased`), one line per
change, newest first, each line naming the milestone it belongs to. Behaviour,
not diff: what a user or operator can now do, or can no longer be bitten by.

## Unreleased

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
