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

- [x] **M07 — Desktop app** · size XL · deps M03, M04 · parallel-ok M08
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

- [x] **M08 — Android: headless build, tests, blocking lint, JVM screenshots, device backlog** · size XL · deps M01 · parallel-ok M07
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

- [x] **M18 — Research engine** · size L · deps M16, M11, M10 · parallel-ok M19
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

- [x] **M19 — Coding agent (Jarvis as its own Claude Code)** · size XL · deps M11, M10, M04 · parallel-ok M18
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

- [x] **M20 — Subagents & orchestration** · size L · deps M11, M10
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

- [x] **M21 — Agentic automation on the desktop** · size L · deps M07, M11, M20 · parallel-ok M22
  - Scope: `device_control.run_sequence` (variables carried between steps, per-step tier,
    stop-on-failure, a verification step) with `tests/test_device_control_sequence.py`;
    `jarvis-desktop/tests_e2e/test_agentic_automation.py` — the harness, a scripted model
    planning ≥ 3 desktop actions, a Tier-3 approval in the middle, a failed step reported, the
    task events the UI shows observed; verification row.
  - Verify: `bash scripts/verify/m21-desktop-automation.sh`

- [x] **M22 — Phone automation: scaffolded, flagged OFF** · size M · deps M08 · parallel-ok M21
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

- [x] **M26 — Intelligence eval and scorecard** · size L · deps M24, M25
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

## Compose is the runtime (added mid-run)

The stack in `docker-compose.yml` is what actually runs, so it is what the tests run against.
These land after the live rig and before the remaining capability milestones that would
otherwise be verified against something nobody deploys.

Docker access arrived on this host while this was being written (`docker run` works,
`jarvisdev` is in the `docker` group), which is what makes all of it possible — and what
unblocks M19's containment check and the live research backend at the same time.

- [x] **M28 — The compose stack is a described, pinned, healthy runtime** · size M · deps M00
  - Scope: every service pinned to a version (five are on `:latest` today — whisper, piper,
    openwakeword, photon, searxng), a healthcheck on every service including the three Wyoming
    ones the voice path depends on, resource limits (`mem_limit`/`cpus`) sized for a 4 vCPU /
    8 GB host, named volumes for everything that holds state, and `docker compose up -d --wait`
    bringing the whole stack to healthy from cold. Fix what this surfaces: `photon` is in a
    restart loop and `jarvis-web` is unhealthy right now. `docs/RUNBOOK.md` — bring-up,
    teardown, logs, and backup/restore per named volume, each command run and its output
    pasted. `scripts/verify/m28-compose.sh` checks the file statically (pins, healthchecks,
    limits, volumes) and then brings the stack up and asserts every container is healthy.
  - Verify: `bash scripts/verify/m28-compose.sh`

- [x] **M29 — The suite runs against the real containers** · size L · deps M28, M25
  - Scope: `scripts/verify/live_interaction.sh` starts with `docker compose up -d --wait`; the
    scenarios talk to the real service endpoints rather than to a harness-owned copy; the run
    fails if any container is unhealthy at the start or has ERROR-level log lines at the end
    (`docker compose logs --since`). Two resilience scenarios: restart the core container
    mid-conversation → the session recovers and the thread survives; kill the STT container
    mid-utterance → the failure is surfaced in the UI rather than swallowed. Data safety
    without fakes: destructive scenarios (memory wipe, forget, task-history clear) snapshot the
    affected named volumes to a tarball before and restore after, so the suite is re-runnable
    against a live stack; read-only scenarios write under a `test:` namespace and assert their
    own cleanup. Dev loop: `docker compose watch` (or an equivalent documented in the runbook)
    so a code change lands in the running container.
  - Verify: `bash scripts/verify/m29-compose-testing.sh`

## The local AI toolbelt (added mid-run)

Each slot below is adopted only if it moves a number this suite already reports. The contract
is the same for all of them, and `scripts/verify/m30-toolbelt.sh` enforces it: **a baseline is
recorded before** (`.verify/toolbelt/<slot>-before.json`, from the research eval, the routing
accuracy, the latency table and the WER), **the service ships in compose** with a pinned
version, a healthcheck and CPU-only defaults, **it gets live-scenario coverage**, and **the
after number must be better than the before or the service comes out**. Choices and rejections
— with the web research behind them — go in `docs/TOOLING_DECISIONS.md`.

- [x] **M30 — The toolbelt contract, and the research behind it** · size M · deps M28, M26
  - Scope: `docs/TOOLING_DECISIONS.md` — for every slot below, what was chosen, what was
    rejected and why, checked against current sources rather than against what was
    state-of-the-art when the model was trained; the VRAM justification rule (nothing takes GPU
    residency without one, because the 3090s hold qwen's KV cache); and the measurement
    harness: `scripts/verify/toolbelt_baseline.py` snapshots the scorecard, `--compare` diffs
    two snapshots and exits non-zero when a metric got worse.
  - Verify: `bash scripts/verify/m30-toolbelt.sh`

- [x] **M31 — One headless browser service, shared** · size M · deps M28, M30
  - Scope: `jarvis-browser` becomes the only Chromium in the system — the research engine's
    agentic browsing and the test rig's browser transport both use it, and
    `testing/live/fixture_browser.py` becomes a fallback for a host with no stack rather than
    the default. Per-task browser installs go. Live scenarios cover a JS-heavy page that plain
    fetch cannot read.
  - Verify: `bash scripts/verify/m31-browser-service.sh`

- [x] **M32 — Crawling and document extraction** · size L · deps M31, M30
  - Scope: Crawl4AI (or whatever the research in M30 lands on) as a compose service for pages
    plain fetch handles badly, and Docling for documents (PDF/DOCX → clean markdown) so
    research and notes can ingest a real file. Baseline first: the research eval's pass rate
    and cited-source count on the fixture web and on a fixed set of hard pages. Adopted only if
    both improve.
  - Verify: `bash scripts/verify/m32-extraction.sh`

- [x] **M33 — Embeddings and reranking as services** · size L · deps M28, M30
  - Scope: a dedicated CPU embedding server (TEI/Infinity class) and a local cross-encoder
    reranker, used by memory, notes and research retrieval — rerank-after-retrieve is the
    cheapest quality win in the whole system. Embeddings stop going through llama-swap, which
    is a KV-cache eviction the voice path pays for. Baseline: recall on the memory eval's
    targeted queries and the research eval's source quality.
  - Verify: `bash scripts/verify/m33-embeddings.sh`

- [x] **M34 — The vector store, decided** · size S · deps M33
  - Scope: either promote memory/notes retrieval to Qdrant as a compose service, or write the
    paragraph justifying the embedded store — with the numbers that justify it (entries,
    query latency, recall). Either answer is acceptable; an unexamined one is not.
  - Verify: `bash scripts/verify/m34-vector-store.sh`

- [x] **M35 — Speech as services, and a TTS A/B** · size L · deps M28, M30
  - Scope: STT behind an OpenAI-compatible container (speaches / faster-whisper-server class)
    instead of the in-process Wyoming client; TTS stays Piper and gains a Kokoro-FastAPI
    container beside it, A/B'd through the live suite — judge scores and the operator's ears
    decide, and the WER and latency numbers are the tie-break. The doubled-transcript issue in
    `ISSUES.md` is re-tested here, because it may be a `condition_on_previous_text` setting the
    current container does not expose.
  - Verify: `bash scripts/verify/m35-speech-services.sh`

- [x] **M36 — Agent observability** · size L · deps M28, M30
  - Scope: self-hosted Langfuse (or an equivalent that fits 8 GB) tracing every agent step,
    subagent, tool call, token count, latency and judge verdict; wired into the research,
    coding and subagent loops; a "view trace" link from the task-execution UI. The memory cost
    is measured before it is adopted — it is the largest single ask in this list.
  - Verify: `bash scripts/verify/m36-observability.sh`

- [x] **M37 — n8n bridge, flag-gated and off** · size M · deps M28, M30
  - Scope: a bridge to the operator's existing self-hosted n8n over the tailnet, exposing
    selected workflows as Jarvis tools, behind a config flag that defaults OFF and a per-workflow
    allow-list. Nothing is enabled until the operator says so; the tests cover the flag being
    off, the allow-list refusing an un-listed workflow, and one worked example with the flag on.
  - Verify: `bash scripts/verify/m37-n8n-bridge.sh`

## Reach, routing and delegation (added mid-run)

Modelled on what the OpenClaw-class assistants do, and deliberately not on how they did it: the
security items below are milestones, not acceptance criteria buried in a scope line, because
that class of tool shipped 140k internet-exposed instances, a marketplace supply-chain attack
and one-click RCE. M43 is built **before** anything that consumes untrusted content in anger,
and every milestone here ends with its own live scenarios.

- [x] **M38 — Channels: Jarvis is reachable, and reaches back** · size L · deps M17, M43
  - Scope: `integrations/channels/` — an adapter interface (`receive`, `send`, `identify`,
    `health`) with Telegram and Signal shipped and Discord/Matrix/SMS-gateway droppable in
    without touching core. Inbound messages become ordinary conversations with the full tool
    set; outbound is where proactive moments (briefing, task-done, approval requests) go, so
    `notifications/` gains channel sinks rather than growing a second notion of "tell them".
    **Security, not polish**: every sender is authenticated against an allowlist of the
    operator's own identities — an unknown sender is ignored, never served, and the fact is
    logged; per-channel and global rate limits; tailnet or loopback only, no public exposure,
    no static token in a URL. `testing/fixtures/channel_server.py` is a mock channel — no
    external account is touched by any test.
  - Verify: `bash scripts/verify/m38-channels.sh`

- [ ] **M39 — Calendar, mail, and a tool-plugin interface** · size L · deps M11, M43
  - Scope: CalDAV (read, create, modify, availability) and IMAP/SMTP (read, send) as
    integrations, plus the drop-in self-describing tool-plugin interface they are the first two
    users of. Read-only is allowed by default; anything that mutates external state or reads
    private data goes through the tier/approval model; credentials come from the secrets store
    at call time (M43) and never from the environment or a note; every external call lands in
    the trace (M36). Fixtures: a Radicale container and a mail sink container, both in compose
    behind a profile — create-event appears on the calendar, send-mail lands in the fixture
    inbox, an unapproved state-changing call is refused.
  - Verify: `bash scripts/verify/m39-integrations.sh`

- [ ] **M40 — One gateway, many providers, and a privacy guard** · size L · deps M28, M36
  - Scope: a self-hosted LiteLLM container as the single internal model endpoint; llama-swap is
    the local default and OpenAI/Anthropic/Google/OpenRouter are configured but **off until the
    operator supplies keys** — local-only stays a complete configuration. Routing is policy:
    a default local model, per-capability overrides, automatic fallback on error or timeout,
    per-provider cost and rate caps. The hard rule is the privacy guard: a request carrying
    memory, notes or private-integration content is tagged `local-only` and the proxy
    **refuses** to route it to any cloud provider; leaving the LAN with personal data takes an
    explicit per-request opt-in, and that decision is logged. Verified with a mock cloud
    provider: default goes local, an override reaches the mock, a forced error falls back, and
    a tagged request is refused even with a provider available.
  - Verify: `bash scripts/verify/m40-model-gateway.sh`

- [ ] **M41 — Claude Code as an execution backend** · size L · deps M19, M40, M43
  - Scope: heavy coding work can be delegated to Claude Code headlessly (`--print`, structured
    output) as an alternative to the local coding agent, selectable per task. It runs in the
    same disposable sandbox under the same containment assertions and the same approval gates
    in the task UI — there is no path by which a delegated run writes outside the sandbox or
    acts outside the task's approval policy. Backend selection is recorded in the trace. Off
    until the operator supplies a key (a `BLOCKERS.md` user-input row, and the first deliberate
    exception to "no cloud" — authorised, flagged, and off by default); CI proves it against a
    scripted stand-in that speaks the same protocol.
  - Verify: `bash scripts/verify/m41-claude-code-backend.sh`

- [ ] **M42 — Delegation across backends** · size L · deps M20, M41
  - Scope: one spoken request fans out into a plan of subtasks across the specialised subagents
    and the backends (local agent, Claude Code, research, integrations), independent ones in
    parallel, rolling up to a lead that reports progress in the task UI and stops at approval
    gates. Concurrency stays bounded by `llm.max_concurrent` against the model endpoint.
    Verified by a multi-part request that produces a plan, executes across at least two
    backends, and rolls up a coherent result with trace evidence.
  - Verify: `bash scripts/verify/m42-delegation.sh`

- [x] **M43 — Hardening: injection, least privilege, secrets, red team** · size XL · deps M11, M13
  - Scope: **prompt injection is unsolved, so it is assumed.** Every piece of external content
    — email bodies, fetched pages, channel messages, file contents, catalog metadata — is
    wrapped and quarantined before it reaches the model and stripped of chat-template control
    literals (ChatML, Llama, Gemma, Mistral) so fetched text cannot forge a role boundary
    against a local model. Content from an external source can never silently trigger a
    state-changing tool: those hit the approval gate regardless of what the content asks.
    Least privilege everywhere — each subagent, integration and skill gets the narrowest tool
    allowlist and credential scope that works, and there is no ambient god-tool. A real secrets
    store: injected at call time, never persisted into memory, notes, logs or traces, with
    trace redaction. `docs/THREAT_MODEL.md` (short, and about this system). A red-team scenario
    file in the live suite: injection via a fetched page, injection via an inbound channel
    message attempting an unapproved action, a cross-conversation data-leak probe, and a
    non-allowlisted sender — **the suite fails if any probe succeeds.**
  - Verify: `bash scripts/verify/m43-hardening.sh`

## Motion (added mid-run)

- [ ] **M44 — The motion system, and the moments built on it** · size L · deps M02, M05, M29
  - Scope: motion joins the design tokens — durations, easings (standard/decelerate/accelerate/
    spring), stagger intervals — in `design/tokens.json`, generated into web, Android (Compose
    animation specs) and desktop exactly as colour and type already are, with reusable
    primitives (fade/slide/scale, shimmer, glow-pulse, shared-element) that every animation in
    the app draws from. `scripts/verify/token_lint.py` grows a rule for raw `transition:` /
    `animation:` values, and the style-guide page documents each token with a live example.
    Then the moments, on the existing aesthetic and its accent, never a restyle: a staged boot
    sequence as subsystems come online (≤ ~1.5 s, skippable, reduced on repeat launches); a
    living idle presence with clearly distinct listening / thinking / speaking states driven by
    real audio amplitude; in-task motion (streaming cursor, tool-call and subagent-tree nodes
    animating in, progress tweens, completion and error resolutions); shared-element page
    transitions and dashboard graphs that tween on data updates instead of snapping.
    **Verifiable constraints**: a Chrome DevTools performance trace captured headlessly over
    the boot sequence and a busy task view, asserting no frame over ~16 ms and no forced
    reflow in the animated paths; `prefers-reduced-motion` honoured as a full, tested path,
    asserted by the live suite; input stays responsive during every animation and nothing gates
    an action behind a decorative sequence. `docs/LIVE_TEST_REPORT.md` gains the trace results
    and the reduced-motion verdict.
  - Taste checkpoint: the harness can prove smooth, token-compliant and accessible; it cannot
    prove *cool*. On completion, record boot, idle → listening → thinking → speaking, and a
    live task view to `docs/motion-review/*.webm` (headless Chromium video capture — no GUI,
    no device) for the operator to watch. **The milestone is not done until they have signed
    off**, and their notes are worked through as a second pass.
  - Verify: `bash scripts/verify/m44-motion.sh`

## The skills and plugins ecosystem (added mid-run)

Built on the existing SKILL.md loader and MCP client — organised, curated and sandboxed, not
duplicated. It comes after the platform capabilities and after M43, because a catalog that can
install code is the marketplace attack surface that class of tool actually got burned by.

- [ ] **M45 — One registry over skills, MCP servers and plugins** · size L · deps M13, M14, M43
  - Scope: a single model over everything extensible — `SKILL.md` skills (the open Agent Skills
    format, so they move to and from Claude Code unchanged), MCP servers, and integration/tool
    plugins — each with a manifest: id, version, description, author, declared permissions and
    tool allowlist, declared network and filesystem needs, source URL. One registry indexes
    what is installed, what is enabled, its permission scope and its health. A JSON schema the
    manifests validate against, and a malformed manifest is rejected rather than half-loaded.
    First-party skills that exercise the system: a research-report skill, a note-taking skill,
    a homelab-status skill reading the existing InfluxDB, and a calendar skill.
  - Verify: `bash scripts/verify/m45-registry.sh`

- [ ] **M46 — The management surface** · size L · deps M45, M05
  - Scope: a Skills & Plugins section in the console on the design system with real loading,
    empty, error and offline states: browse installed items by category, enable and disable
    per item, view and edit each item's permission scope, see health, last-used and error
    state, read its description and its source. Creating a skill is guided — scaffold a
    `SKILL.md` from a template — because a management surface people edit JSON behind is not
    one. Asserted through the live suite against the real containers: toggling a skill enables
    and disables its tool, a disabled skill is not offered to the model at all, and an edited
    permission scope is enforced on the very next call.
  - Verify: `bash scripts/verify/m46-plugins-ui.sh`

- [ ] **M47 — The catalog, and installing from it safely** · size XL · deps M45, M43, M19
  - Scope: discovery and installation from configured catalog sources — Anthropic's own
    skills and plugins, the curated community lists (`awesome-claude-*`), MCP directories, and
    a named GitHub repository the operator points at — with a browser in the console: search,
    read the description and the *declared permissions*, install behind a visible permission
    prompt. Treated as hostile by default: sources are an explicit operator-controlled
    allowlist and nothing installs from an unconfigured origin; installation is pinned to a git
    ref or version **and a checksum**, never a blind `latest`, with the source and hash
    recorded; **nothing auto-runs on install** — the declared permissions are shown and
    approved first, anything carrying an executable hook or script is flagged and its code
    surfaced for review before it can run; installed third-party capabilities execute under the
    same sandbox and approval system as everything else, with the narrowest scope they declare
    and no ambient host access, credentials or network path; and the injection quarantine
    (M43) covers catalog metadata, so a description field cannot smuggle an instruction.
    `docs/THREAT_MODEL.md` gains the supply-chain surface. The red-team file gains a
    malicious-skill-install probe, and the suite fails if anything unapproved executes.
  - Verify: `bash scripts/verify/m47-catalog.sh`

## The console, finished (added mid-run)

- [ ] **M48 — Every page in the web console is C2** · size XL · deps M02, M05, M44
  - Scope: the chosen design direction is **C · Reactor II** (`docs/design/c2-reactor.html`,
    the decision is recorded in `docs/design/README.md`) and the console is only partly on it.
    Every route, view and modal in `jarvis-web/` implements it — no page keeps its old,
    bespoke styling.
    * **The inventory, walked rather than remembered**: `docs/UI_MIGRATION.md` lists one
      `- [ ]` row per page/view/modal, found by walking `src/routes` and the component tree,
      each honestly marked migrated / partial / old. That checklist is the source of truth and
      the milestone is not done while a box is unchecked.
    * **The migration**: each page rebuilt on the C2 tokens and the shared component library —
      no ad-hoc colour, spacing or one-off component; consistent layout, navigation and
      spacing; the four required states (loading, empty, error, offline) reachable on every
      page; responsive at mobile, tablet and desktop; the M44 motion tokens applied; zero
      hardcoded style values. A page needing a component the library lacks **adds it to the
      library** and documents it on the style guide rather than growing a one-off, and an
      old-styled component is deleted once nothing references it.
    * **Proof, page by page**: `scripts/verify/token_lint.py` passes for the page, it renders
      at three breakpoints, all four states are reachable, and a headless screenshot lands in
      `docs/ui-review/<page>/<breakpoint>.png`. A visual-consistency check flags a page whose
      rendered palette or spacing deviates from the tokens.
  - Verify: `bash scripts/verify/m48-webui-c2.sh` — it **fails** if any inventory row is
    unchecked, if token-lint finds a hardcoded style value anywhere in the console's source, if
    a page is missing a required state, or if an old-design component is still referenced. The
    live suite navigates to every inventoried route and asserts it loads with no console error
    and matches the C2 token expectations; `docs/LIVE_TEST_REPORT.md` gains a migration section
    (pages migrated / total, per-breakpoint screenshots, remaining offenders — which must be
    zero).
  - Not in scope: desktop and Android C2 parity. The same discipline applies to them later;
    this milestone is the web console.

## Final

- [ ] **M23 — Final integration** · size M · deps M00–M48
  - Scope: `make verify-all` green; **the stack comes up healthy and the whole suite runs
    against it** (M28/M29); **`bash scripts/verify/live_interaction.sh` in full mode exits 0** — every scenario, including the ones that were gated, inside the thresholds
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
- M28 before M29, and both before the toolbelt: a service adopted against a stack nobody
  brings up cleanly is a service adopted against nothing. M30 before M31–M37, because it is the
  measurement they are each judged by.
- M31 ∥ M33 ∥ M35 (browser, embeddings, speech: separate services, separate scorecard rows);
  M32 after M31; M34 after M33; M36 and M37 last, and M37 stays off.
- Everything else is serial in the order written.
