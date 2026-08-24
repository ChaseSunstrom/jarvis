# MILESTONES.md — from the audited state to the target state

Ordered as the brief asks: harness → design system → web → desktop → Android → platform →
automation → final integration. Each milestone has a scope (derived from the gaps in
`docs/AUDIT.md`), the exact command that decides whether it is done, a size, its dependencies,
and whether it may run in parallel with its neighbours (`PROCESS.md` §3). Tick the box in the
same commit as the work (`PROCESS.md` §1). `make verify-all` runs every verify script; a
milestone whose script fails is not done, whatever the diff says.

Sizes are focused agent work: **S** ≤ ½ day · **M** 1–2 days · **L** 3–5 days · **XL** 1–2 weeks.
Paths are repo-relative. "parallel-ok" names the milestones this one may overlap with — and the
files both would touch, which the integrating session merges.

---

## Harness

- [x] **M00 — Verification harness** · size S · deps none
  - Scope: `make verify-all` → `scripts/verify/all.sh` (one table, `.verify/*.log`, exit 1 on any
    failure, no skip state); `scripts/verify/lib.sh`; scanners `web_adhoc_scan.mjs`,
    `web_dead_controls.mjs`, `web_states_check.py`, `tokens_check.py`; one `mNN-*.sh` per
    milestone below; Playwright runnable beside the live HUD (`E2E_PORT`, Chromium installed);
    planning artefacts `docs/AUDIT.md`, this file, `PROCESS.md`, `CHANGELOG.md`, `BLOCKERS.md`.
  - Verify: `bash scripts/verify/m00-harness.sh`
  - Done when the script passes and every other script fails for a reason it names.

## Design system

- [x] **M01 — Design system: one token source, generated for every surface, linted** · size M · deps M00 · parallel-ok M09
  - Scope: `design/tokens.json` (DTCG; seeded from Reactor II, `docs/design/c2-reactor.html`) is
    the only place a value is typed. `design/build.py` (stdlib, `--check`) generates, each with
    the `@generated from design/tokens.json` marker: `jarvis-web/src/lib/styles/tokens.css`,
    `jarvis-web/src/lib/tokens.ts`, `jarvis-desktop/jarvis_desktop/tokens.py`,
    `android-app/…/ui/theme/JarvisTokens.kt`, `…/ui/theme/JarvisTheme.kt` (Compose
    `MaterialTheme`; Compose enabled in the Gradle build), `res/values/tokens.xml` and
    `colors.xml` (aliases only). Consumers alias the generated values (`JarvisUi.kt`,
    `theme.py`, `motion.ts`, the favicon generator); the orb palette (`SiriPalette.kt`,
    `Orb.svelte`) is declared as `color.orb.*` and drift-checked, not rewritten, because
    `reactor_orb_test.py` already pins it to the shader. Self-hosted faces (Barlow, Space
    Grotesk, JetBrains Mono; OFL) under `jarvis-web/static/fonts`. The three parity tests
    (`tokens.test.ts`, `tests/test_theme.py`, `tools/design_token_test.py`) read the JSON.
    `scripts/verify/token_lint.py` fails on any hard-coded colour/spacing/type/motion value in
    app code, ratcheting against `design/token-lint.baseline.json` (legacy counts only fall;
    new files must be clean; documented exceptions carry a reason). `/styleguide` renders every
    group and the four screen states on tokens only. `.claude/skills/jarvis-design-system/SKILL.md`
    + `.claude/rules/design-system.md` bind every future session to all of this.
  - Verify: `bash scripts/verify/m01-design-tokens.sh` (includes `python3 scripts/verify/token_lint.py`, `python3 design/build.py --check`, the style-guide e2e)
  - Done when the script passes; M03 and M08 later require the baseline to be empty for their surface.

- [x] **M02 — Component library** · size L · deps M01
  - Scope: `jarvis-web/src/lib/ui/` — ≥ 12 primitives extracted from `chrome.css` (Button,
    IconButton, Input, Select, Toggle, Field, Panel, Row, Pill, Toolbar, Tabs with the sliding
    underline, Dialog, Toast, Skeleton, EmptyState, ErrorState, `ScreenState`) plus
    `Reactor.svelte` (the Reactor II instrument from `docs/design/c2-reactor.html`: bezel,
    blades, coil, level arc, lens; sized by prop, driven by pipeline state), each with a
    `<!-- @component` doc block, `index.ts` barrel, `README.md` with a `## Name` section per
    component, `ssr.test.ts` plus unit tests, and clean under the token lint; the 8 hand-copied
    `.jv-empty` blocks replaced by `<EmptyState>`; every component rendered in every state on
    `/styleguide` (which M01 created); `e2e/styleguide.spec.ts` extended to cover them.
  - Verify: `bash scripts/verify/m02-styleguide.sh`

## Web

- [x] **M03 — Web console redesigned on the system** · size XL · deps M02
  - Scope: every route re-expressed through `src/lib/ui`; `src/lib/screens.ts` manifest;
    `<ScreenState>` on every page (loading / empty / error / offline); `routes/+error.svelte`;
    `navigator.onLine` + socket state → offline; HUD gets a reconnect control; `/settings` gets
    skeleton + empty states; chat sidebar loses its false-empty flash. Zero ad-hoc values
    (`web_adhoc_scan.mjs` → 0): the HUD's private `--accent/--dim/--line` layer removed;
    `chrome.css`'s 17 `rgba()` → tokens / `color-mix()` / relative colour; raw spacing, type and
    durations → tokens. Responsive: breakpoints or container queries for 360/768/1024/1440, no
    page-level `overflow-x:hidden`, `e2e/responsive.spec.ts` asserts no horizontal overflow on
    every screen at five widths. `e2e/states.spec.ts` drives every screen into all four states
    through new mock hooks (`jarvis/test/slow`, `…/empty`, `…/fail`, socket close);
    `e2e/controls.spec.ts` clicks every enabled control and asserts an effect. The token-lint
    baseline for `jarvis-web/src` reaches zero (`token_lint.py --require-clean jarvis-web/src`).
    The whole Playwright suite (112 + new) green headless on this host.
  - Verify: `bash scripts/verify/m03-web.sh`

- [x] **M04 — Task-execution UI** · size L · deps M03 · parallel-ok M05 (shared: `api/websocket.py`, `tests/web/mock-ha.mjs`, `+layout.svelte`)
  - Scope: `tests/contracts/task_events.json` (event names + payload schemas) read by
    `jarvis-core/tests/test_task_events_contract.py` and `jarvis-web/src/lib/taskEvents.test.ts`;
    `jarvis-core/jarvis/tasks.py` gains `tool_started/finished`, `output()` (→
    `jarvis_task_output`), `raise_if_cancelled`, and a persisted per-task event log; the code
    agent and research emit tool + output events live; orchestrator jobs are registered as tasks
    (polled → events); generic tasks honour cancel. Web: `/tasks/[id]` detail (steps, live tool
    calls, `TaskOutput` streaming pane, `TaskTimeline` with a time axis and the event log,
    approve/cancel), dock and cards link to it; the mock streams `jarvis_task_output` and
    `jarvis_task_tool_*`; `e2e/task-live.spec.ts`; `jarvis-core/tests/test_task_events.py`.
  - Verify: `bash scripts/verify/m04-task-ui.sh`

- [x] **M05 — Dashboards + internal metrics source** · size XL · deps M03 · parallel-ok M04 (shared files as above)
  - Scope: `jarvis-core/jarvis/metrics/` — `DataSource` protocol (`list_series()`,
    `query(series, range, step)`), `sources/internal.py` over recorder history/stats plus new
    system metrics (`/proc`, no psutil), LLM token/latency counters from turn events, task
    counts; `integrations/dashboards/` storing layouts per token id through `store.py`; WS
    `jarvis/dashboards/list|save|delete`, `jarvis/metrics/query|sources` (+ REST twins);
    `tests/contracts/dashboard_layout.json`. Web `/dashboards`: widget grid with add / remove /
    resize / reorder (keyboard-accessible), `src/lib/dashboards/chartTypes.ts` with ≥ 4 types
    (line, area, bar, stat/gauge, table) rendered by a hand-written SVG layer or one dependency
    (decide in-milestone, using the `dataviz` skill for the palette), a data-source picker,
    persistence per user; nav entry + `ConsoleTab.kt` DASHBOARDS + `console_parity_test.py`;
    mock; `e2e/dashboards.spec.ts`; `tests/test_dashboards.py`, `tests/test_metrics.py`.
  - Verify: `bash scripts/verify/m05-dashboards.sh`

- [x] **M06 — InfluxDB data-source adapter** · size M · deps M05
  - Scope: `metrics/sources/influx.py` detecting 1.x vs 2.x from `/health` / `/ping`, InfluxQL
    via `/query` and Flux via `/api/v2/query`, token in the `Authorization` header, series
    listing (measurements / fields / tags), range + step → query; `metrics.sources` config;
    `.env.example` `INFLUX_URL/INFLUX_TOKEN/INFLUX_ORG/INFLUX_BUCKET`; `jarvis-core/docs/metrics.md`;
    `tests/test_metrics_influx.py` against a fake InfluxDB (v1 + v2); `scripts/check-influx.py`
    live probe (the Scripted claim in `docs/verification.md`); example
    `jarvis-core/config/dashboards/homelab-gpu.yaml`; UI source picker; mock source.
  - Verify: `bash scripts/verify/m06-influx.sh`

## Desktop

- [ ] **M07 — Desktop app** · size XL · deps M03, M04 · parallel-ok M08
  - Scope: `jarvis-desktop-app/` — an Electron shell (TypeScript) that loads the jarvis-web build
    (parity by construction), draws the tray (status, mute, push-to-talk, quit), posts native
    notifications for approvals / tasks / companion messages, registers a `globalShortcut`
    push-to-talk that drives the renderer's mic, consumes a generated `src/renderer/tokens.css`,
    and connects to jarvis-core with a device token. The existing agent stays the automation
    backend: `jarvis_desktop/ipc.py` (loopback socket) lets the shell show agent status and
    answer consent prompts (`ShellConsentGateway` replaces the Tk dialogs); a DESKTOP tab in the
    console nav. Verified headless: vitest with Electron mocked, Playwright `_electron` under
    `xvfb-run`, `npm run dist:dir` (electron-builder, unpacked); `desktop-dist.yml` extended;
    `tests/test_ipc.py`; README + verification rows.
  - Verify: `bash scripts/verify/m07-desktop.sh`

## Android

- [ ] **M08 — Android: headless build, tests, blocking lint, JVM screenshots, device backlog** · size XL · deps M01 · parallel-ok M07
  - Scope: toolchain under `$HOME` with no root — `android-app/tools/bootstrap-toolchain.sh`
    installs JDK 17 (`~/.local/jdk`), cmdline-tools, `platforms;android-35`, `build-tools`,
    `platform-tools` (`~/Android/Sdk`); the Gradle wrapper (`gradlew`, `gradle-wrapper.jar`) is
    committed (decision recorded in `DEVIATIONS.md`). `compose = true` and the generated
    `JarvisTheme.kt` compiled; Views consume `JarvisTokens`; `colors.xml`/`themes.xml` from
    `tokens.xml`; the 45 raw `dp()` and 35 `textSize` literals → `JarvisUi.Space/Type`.
    Robolectric + Roborazzi: JVM screenshot tests for the HUD/orb, approval, settings, task
    overlay, console frame (≥ 5 goldens under `app/src/test`), `ApprovalBridge` fail-closed under
    Robolectric; `lint { abortOnError = true }` (baseline only for documented pre-existing
    findings) and the CI step no longer `|| true`. The token-lint baseline for
    `android-app/app/src/main/kotlin` reaches zero. `docs/ANDROID_DEVICE_TESTS.md`: a table of
    `ADT-NNN` rows (ID · Area · Check · Why device-only · Milestone) seeded from `docs/AUDIT.md`
    §4 (≥ 20). Parity: the DASHBOARDS tab from M05 in `ConsoleTab.kt`.
    Never: a device, an emulator, `connectedAndroidTest`.
  - Verify: `bash scripts/verify/m08-android.sh`

## Platform capabilities

- [x] **M09 — LLM through the llama-swap OpenAI-compatible endpoint, everywhere** · size M · deps M00 · parallel-ok M01–M08
  - Scope: `LLM_URL`/`LLM_MODEL` first-class (`!env_var LLM_URL` in `configuration.yaml`,
    `.env.example` leads with them; `OLLAMA_*` stay as aliases); `llm.local_only: true` default
    refusing model hosts that resolve to public addresses (loopback, RFC 1918, CGNAT/Tailscale
    allowed) with `tests/test_llm_local_only.py`; llama-swap named in
    `jarvis-core/docs/openai-compat.md` and `README.md`; the `rest` sensor polling Ollama's
    `/api/ps` replaced by one reading `/v1/models` or llama-swap's `/running`;
    `scripts/check-model-server.py` recognises llama-swap; `scripts/e2e-smoke.sh` probes
    `/v1/models`; the orchestrator's `fanout.py` and `opencode.py` use the same OpenAI-compatible
    client (no `/api/chat`, no `ollama/` prefix); hermes recovery tests stay green.
  - Verify: `bash scripts/verify/m09-llm.sh`

- [x] **M10 — Task engine** · size L · deps M04, M09
  - Scope: `jarvis-core/jarvis/taskengine.py` — bounded FIFO queue with a concurrency cap and
    priority, workers that run `run_background_task` work, retries with exponential backoff and
    jitter per task policy, `raise_if_cancelled` in every worker, queued work persisted and
    resumed after a restart (running work retried when idempotent, else `error`), history that
    keeps code job results (drop `MAX_KEPT` memory-only), orchestrator jobs persisted and
    reloaded (`load_persisted()` called), schedule firings minted through the engine, WS/REST
    `jarvis/tasks/retry`; `jarvis-core/docs/tasks.md`; `tests/test_taskengine.py` covering
    queue, retry, backoff, restart, cancel.
  - Verify: `bash scripts/verify/m10-task-engine.sh`

- [x] **M11 — Agent loop: plan → act → verify** · size L · deps M09, M10
  - Scope: `jarvis-core/jarvis/llm/plan.py` — for multi-step requests the agent writes a plan
    whose steps are registered as a Task (so M04's UI shows them), acts step by step through
    tool calls, verifies each outcome (tool-based checks or a verification call), re-plans a
    failed step within `MAX_REPLANS`; `agent.py` routes to it and keeps `max_tool_rounds`;
    `tests/contracts/tool_tiers.json` pins tier semantics (1 direct · 2 background + notify · 3
    approval) for core, web and the Android mirror, and the MCP config comment stops
    contradicting the code; `tests/test_agent_loop.py` (plan, verify, replan);
    `testing/e2e/test_agent_loop.py` through the harness with a scripted model;
    `jarvis-core/docs/features.md` describes plan → act → verify.
  - Verify: `bash scripts/verify/m11-agent-loop.sh`

- [x] **M12 — Hooks** · size M · deps M10 · parallel-ok M13, M14
  - Scope: trigger platforms `wake_word` (from `voice_pipeline_event wake_word-end`, optional
    pipeline/device filter) and `task` (`status: started|completed|failed`, `kind:`), with
    distinct `jarvis_task_started/completed/failed` events; `event` triggers match nested
    `event_data` (dotted paths); inbound webhooks documented with `webhook_require_auth`;
    schedules documented; `jarvis-core/docs/hooks.md`; `config/examples/hooks.yaml` with one
    automation per hook; `tests/test_hooks.py`.
  - Verify: `bash scripts/verify/m12-hooks.sh`

- [x] **M13 — Skills (SKILL.md loader)** · size M · deps M11 · parallel-ok M12, M14
  - Scope: `jarvis-core/jarvis/integrations/skills/` loading `config/skills/*/SKILL.md` in the
    open Agent Skills format (YAML frontmatter `name`, `description`, optional `allowed-tools`,
    `metadata`; `scripts/`, `references/`, `assets/` beside it); progressive disclosure — the
    index of name + description in the system prompt, the body through a `use_skill` tool;
    skill scripts run only through the gated `run_command` / sandbox path (Tier 3); `skills:`
    config; WS `jarvis/skills/list` + REST `/api/skills` (+ reload); the console's tools page
    lists loaded skills; example `config/examples/skills/<name>/SKILL.md`;
    `jarvis-core/docs/skills.md`; `tests/test_skills.py` (frontmatter, invalid, on_demand, gated).
  - Verify: `bash scripts/verify/m13-skills.sh`

- [x] **M14 — MCP: finish manage + inspect** · size S · deps M11 · parallel-ok M12, M13
  - Scope: `jarvis/mcp/inspect` (WS + REST): server info, protocol version, tool schemas,
    `last_error`, and a gated test call from the console; automatic reconnect with backoff; tier
    semantics from `tool_tiers.json` in `test_mcp.py`; `McpServers.svelte` inspect view;
    `jarvis-core/docs/mcp.md`; mock `jarvis/mcp/inspect`; `e2e/mcp.spec.ts` extended.
  - Verify: `bash scripts/verify/m14-mcp.sh`


## Memory, notes, interactions

- [x] **M15 — Memory: durable, transparent, user-owned** · size M · deps M09, M10 · parallel-ok M16
  - Scope: keep `integrations/memory/` (store, vector sidecar, redaction, trust rules,
    `remember`/`recall`/`forget`). Add: automatic extraction — after a turn, one bounded
    model call proposes durable facts (preferences, people, projects, standing instructions;
    never transcript), stored with `source: extracted` and a link to the turn, off per
    conversation with a word; `memory.export` (REST `GET /api/memory/export` → one JSON/markdown
    file with every entry) and `memory.wipe` (everything, including the vector sidecar);
    per-turn `memory_used: [ids]` on the turn event so a reply can say why; the console's
    `/memory` route (browse, search, edit, delete, pin, export, wipe) with `e2e/memory.spec.ts`;
    only personalisation reads memory (research stops writing reports into it — M16 owns that).
    `evals/memory_eval.py`: boot the harness, store a fixture set of facts through the API,
    restart the server, targeted queries retrieve the right entries, forget removes one and it
    no longer surfaces, export is complete, wipe empties everything — exit code.
  - Verify: `bash scripts/verify/m15-memory.sh`

- [x] **M16 — Notes: first-class, everywhere, an agent tool** · size M · deps M10 · parallel-ok M15
  - Scope: `integrations/notes/` — markdown files under `<config>/notes/<slug>.md` with
    frontmatter (title, tags, created, updated), a SQLite FTS5 index (`.storage/notes.db`),
    `[[wiki links]]` resolved and back-linked; REST `/api/notes*` + WS `jarvis/notes/*`
    (list/get/create/update/append/delete/search?q=&tag=); tools `note_create`, `note_append`,
    `note_search` (which reads one whole note when given an id — three tools rather than four,
    because `tests/test_prompt_budget.py` bounds what the toolbox may cost); research's "save the report" writes a note; the voice intent
    "note that …" / "make a note …" (routing table + `evals/routing` entry) creates one;
    `/notes` in the console (editor with preview, tags, search, link graph list) and the
    Android/desktop surfaces reach the same API. Tests: API CRUD + search + tag filter
    (`tests/test_notes.py`), a voice-intent fixture where a transcript in produces the right
    note (`tests/test_notes_voice.py`), `e2e/notes.spec.ts`.
  - Verify: `bash scripts/verify/m16-notes.sh`

- [x] **M17 — User interactions: threads, continuity, proactive moments** · size L · deps M15, M12
  - Scope: threads — `ConversationArchive` keeps tool results, gains `jarvis/conversation/search`
    (FTS over turns), and resumes with prior context after a restart; continuity — one
    conversation id across surfaces (the desktop/Android clients open a named thread and both
    see every turn); proactive — a notifications record (`integrations/notifications/`:
    `jarvis_notification` event + `.storage/notifications.json`, list/dismiss over WS/REST) fed
    by hooks (`task` completed/failed → announcement, schedule reminders, the briefing), each
    rendered as a designed moment (a `Notifications` inbox + a `Moment` component on the
    system; Android/desktop native notifications carry the same record); the daily briefing
    is configurable from the console; personalisation shows "why am I seeing this" — the
    `memory_used` ids from M15 rendered as a trace under a reply; barge-in and ⌘K kept.
    Tests: `testing/e2e/test_threads.py` (create → restart → resume with prior context
    present), `testing/e2e/test_continuity.py` (the same thread consistent via the API from
    two clients), `tests/test_notifications.py` (fire a hook → a notification record is
    created and retrievable), `e2e/moments.spec.ts`.
  - Verify: `bash scripts/verify/m17-interactions.sh`

## Agent intelligence

- [ ] **M18 — Research engine** · size L · deps M16, M11, M10 · parallel-ok M19
  - Scope: keep `integrations/research/` and the SearXNG-only stack. Add: lead-following (a
    read page may propose up to N further queries, bounded by depth), a cross-check pass (each
    key claim is matched against ≥ 2 sources or marked single-source), a confidence note per key
    claim in the report, two modes of one engine (`quick`: seconds, ≤ 3 pages; `deep`: multi-step,
    many sources) chosen by the tool/intent; findings stream as task events (`jarvis_task_output`
    from M04) so the UI shows queries issued, pages being read and findings accumulating;
    reports written as markdown files (`<config>/research/<date>-<slug>.md`, saved as a note via
    M16) and browsable from the task detail; SearXNG documented as the required service
    (`--profile search`). `evals/research_eval.py` + `evals/research_questions.yaml` (a fixed
    set): one report file per question, ≥ `min_sources` distinct cited sources, every link
    resolvable (HEAD/GET), exit code; `--backend fixture` replays recorded search/fetch
    responses so the pipeline is verified offline, `--backend live` is the Scripted claim.
  - Verify: `bash scripts/verify/m18-research.sh`

- [ ] **M19 — Coding agent (Jarvis as its own Claude Code)** · size XL · deps M11, M10, M04 · parallel-ok M18
  - Scope: keep `integrations/code/` and every sandbox invariant. Add: a verify-until-green loop
    (`run_tests` runs the repository's declared test command as a check and the loop continues
    on failure, bounded); commits with messages on the job branch (`git commit` inside the job,
    never to the operator's branch), diffs and commits in the task detail; approval gates —
    proposed edits and commands surface as `approval_required` in the task UI, with permission
    modes per task (`ask` · `accept-edits` · `auto-run-tests` · `full-auto` with an explicit
    per-task command whitelist; destructive/system-mutating commands always ask unless
    whitelisted); the sandbox stays the only place anything runs (explicit mount allowlist,
    per-task network policy). `fixtures/coding/failing-tests/` ships a small project whose tests
    fail; `evals/coding_eval.py` runs the agent on it inside the sandbox and passes only if the
    tests pass in the container, the exit code is 0, and **containment holds**: a host-side
    canary (every path outside the job's mount is unchanged; no new files under `$HOME`, `/tmp`
    or the config dir except the job's own record) — a sandbox escape is a test failure.
    Records the Docker-access prerequisite for this host in `BLOCKERS.md` if it cannot run.
  - Verify: `bash scripts/verify/m19-coding-agent.sh`

- [ ] **M20 — Subagents & orchestration** · size L · deps M11, M10
  - Scope: `jarvis/agents/` — drop-in markdown definitions under `<config>/agents/<name>.md`
    (frontmatter: `name`, `role`, `tools` allow-list, `model`, `max_tokens`, `context_budget`;
    body = system prompt); ship `researcher`, `coder`, `verifier`, `summarizer`; the agent loop
    spawns a subagent as a child task with its own context window and tool allow-list, runs
    independent ones in parallel, and rolls results up to the lead; `jarvis/llm/pool.py` — a
    per-task concurrency limit and FIFO queue in front of the model client (config
    `llm.max_concurrent`, default 2) with a context budget per subagent enforced before the
    call; parent/child task events (`jarvis_task_child_added`) so the task UI renders a live
    tree (which agent, doing what, status). The orchestrator's `/delegate` becomes a client of
    this. `evals/subagents_eval.py`: a fixture task that provably needs two parallel subagents
    plus roll-up, run against the harness's scripted model with artificial latency; the harness
    checks the artefacts (`.verify/subagents/rollup.json`) and log evidence of concurrent
    execution (overlapping child start/end timestamps).
  - Verify: `bash scripts/verify/m20-subagents.sh`

## Automation

- [ ] **M21 — Agentic automation on the desktop** · size L · deps M07, M11, M20 · parallel-ok M22
  - Scope: `device_control.run_sequence` (variables carried between steps, per-step tier,
    stop-on-failure, a verification step) with `tests/test_device_control_sequence.py`;
    `jarvis-desktop/tests_e2e/test_agentic_automation.py` — the harness, a scripted model
    planning ≥ 3 desktop actions, a Tier-3 approval in the middle, a failed step reported, the
    task events the UI shows observed; verification row.
  - Verify: `bash scripts/verify/m21-desktop-automation.sh`

- [ ] **M22 — Phone automation: scaffolded, flagged OFF** · size M · deps M08 · parallel-ok M21
  - Scope: `interface PhoneAutomation` + delegate scaffold under `automation/`;
    `buildConfigField("boolean", "PHONE_AUTOMATION", "false")` gating
    `JarvisAccessibilityService`, `JarvisNotificationListener` and `AutomationBridge`;
    `automation_enabled` defaults OFF in `PolicyStore.kt`; `PhoneAutomationFlagTest.kt` and
    `tools/phone_automation_flag_test.py`; `android-app/docs/phone-automation.md` (the
    interfaces, what enabling needs); `ADT-` backlog entries for enabling on a device;
    `DEVIATIONS.md` entry. Nothing run on a device.
  - Verify: `bash scripts/verify/m22-phone-automation-flag.sh`

## Live interaction (added mid-run, built before M13)

These four are built **next**, out of numeric order, because everything after them depends on
one of them: from M24 onward every milestone's verification ends with
`bash scripts/verify/live_interaction.sh --implemented-only`, so a capability does not count as
done until it also works when a person talks to it.

The suite is written **whole, now**, against the target state. A scenario for a capability that
does not exist yet carries `gated-on: <milestone>` in its fixture and is expected to fail; it
runs in full mode only. `--implemented-only` runs the ungated ones and must exit 0 from here on.

- [x] **M24 — Voice loopback rig** · size L · deps M00, M12
  - Scope: `testing/live/` — a rig that interacts with Jarvis exactly as a user does.
    `voice.py`: synthesises the user's utterances with **local Piper** in `en_US-amy-low`
    (deliberately not Jarvis's `en_GB-alan-medium`, so neither side's transcript can be mistaken
    for the other's), and transcribes Jarvis's spoken replies back with the **real Wyoming
    Whisper** on `:10300`. Two delivery paths, both real: the audio-input API
    (`assist_pipeline/run` + binary frames) and the **browser microphone** via headless Chromium
    with `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<wav>` — the page
    must be on `http://127.0.0.1` or `navigator.mediaDevices` does not exist. `audio.py`: noise
    overlays at a named SNR, silence, and clipping. `judge.py`: a local-LLM judge
    (`LLM_URL`, the loaded model) that scores "is this reply semantically right", logging a
    one-line reason per verdict. `report.py`: WER (Levenshtein over words), intent accuracy,
    routing accuracy, per-stage latency. Text chat gets the same scenarios through the console
    with Playwright. Wake-word positives *and* negatives, silence, and barge-in where the surface
    implements it (the web HUD does; the server has no interrupt, and the scenario says so).
  - Verify: `bash scripts/verify/m24-live-rig.sh`

- [x] **M25 — Full-capability scenario suite** · size XL · deps M24
  - Scope: `testing/live/scenarios/*.yaml` — multi-turn `say:` / `expect:` fixtures, each with a
    voice and a text variant, exercising **every** capability end to end through real
    interaction, never an API shortcut. Tasks (create by voice → the live task UI updates under
    Playwright → completion announced; a scheduled task fires on time; a recurring one fires
    twice; an injected failure shows retry/backoff and lands in history; cancel mid-run).
    Research (a local fixture website with known content; quick lookup returns a correct, cited
    answer; deep research shows its plan in the task UI, fetches several sources, saves a report
    as a note with resolvable citations, and its factual claims match the fixture
    deterministically, with synthesis quality additionally graded by the judge; cancelling
    mid-research leaves clean state). Coding ("fix the failing tests in the fixture repo" by
    voice → sandbox up, plan → edits → test runs streaming into the task UI, diff in the approval
    UI, approve via the UI → commit exists, tests pass, containment holds; and a second scenario
    where approval is denied and *nothing* is written anywhere). Subagents (a request that
    genuinely needs parallel research + coding; the live agent tree renders, the logs evidence
    concurrency, the roll-up is consistent with both children). Memory/notes/interactions (store
    a fact by voice → restart the service → recall it; forget → gone from retrieval *and* the UI;
    "note that…" → a note that exists, is linked and is searchable; a proactive hook fires → a
    proper UI moment that is retrievable afterwards; one thread continued across two surfaces).
    `scripts/verify/live_interaction.sh` gains its two modes and is appended to every remaining
    milestone's verify script.
  - Verify: `bash scripts/verify/m25-live-scenarios.sh`

- [ ] **M26 — Intelligence eval and scorecard** · size L · deps M24, M25
  - Scope: `evals/intelligence/` — a fixed eval set run through the **full voice pipeline**,
    producing `.verify/live/scorecard.json` and a markdown table: multi-turn context retention
    (later turns must reference earlier ones); tool-routing accuracy over prompts whose correct
    handling is respectively a plain answer, a memory recall, a note, a task, a quick lookup,
    deep research and a coding job; multi-step reasoning; instruction following (format, length,
    constraint); graceful failure on impossible or garbled input; and per-stage latency (STT,
    LLM TTFT, TTS start, total) measured **twice** — idle, and with a background task running.
    Deterministic checks wherever the state is inspectable; the judge, with logged reasons,
    only where it is not.
  - Verify: `bash scripts/verify/m26-intelligence-eval.sh`

- [ ] **M27 — Exploratory pass and the live test report** · size M · deps M25, M26
  - Scope: with the scripted suite green for every implemented capability, ten or more
    **unscripted** conversations through the rig, aimed at the weak spots `docs/AUDIT.md` names.
    Every defect becomes an `ISSUES.md` entry *and* a new regression scenario, and is then fixed.
    `docs/LIVE_TEST_REPORT.md`: per-capability pass rates, WER, routing accuracy, the latency
    table, and the open issues. Full-mode thresholds, enforced by the runner: intent accuracy
    ≥ 95 %, WER ≤ 10 %, routing accuracy ≥ 90 %, median round trip ≤ 2 s, zero critical issues.
  - Verify: `bash scripts/verify/m27-live-report.sh`

## Final

- [ ] **M23 — Final integration** · size M · deps M00–M27
  - Scope: `make verify-all` green; **`bash scripts/verify/live_interaction.sh` in full mode
    exits 0** — every scenario, including the ones that were gated, inside the thresholds
    (intent ≥ 95 %, WER ≤ 10 %, routing ≥ 90 %, median round trip ≤ 2 s, zero critical issues) —
    and `docs/LIVE_TEST_REPORT.md` exists and is current; a CI workflow runs `make verify-all`
    (JDK/SDK via actions); `README.md`, `docs/verification.md` (re-measured, names the harness,
    the style guide, dashboards, Robolectric, the desktop app, the live rig), `DEVIATIONS.md`,
    `CHANGELOG.md` current; `BLOCKERS.md` holds only device-access or user-input items; no
    placeholder markers in any surface; stale counts gone.
  - Verify: `bash scripts/verify/m23-final-integration.sh`

---

## Parallelism map

- M09 alongside M01–M08 (core config + orchestrator vs. web/Android/desktop files).
- M04 ∥ M05 after M03 (both add WS commands to `api/websocket.py`, mock handlers and a nav entry —
  the integrating session merges those three files).
- M07 ∥ M08 (desktop vs. Android trees).
- M12 ∥ M13 ∥ M14 after M11.
- M15 ∥ M16 after M10; M17 after both.
- M18 ∥ M19 after M16/M11; M20 after M11 (needs M10's engine).
- M21 ∥ M22.
- M24 → M25 come **before** M13, because every milestone after them must pass
  `live_interaction.sh --implemented-only`. M26 ∥ M27 only after the capabilities they measure
  exist, so in practice they land last, before M23.
- Everything else is serial in the order written.
