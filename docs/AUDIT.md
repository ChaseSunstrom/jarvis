# Audit — Jarvis against the target state

Measured on **2026-08-24** at commit `e0f0182` (branch `claude/repo-cleanup-jarvis-chat-4p9fu9`),
on the host that runs Jarvis (`jarvisdev` in a Debian 12 LXC, no GUI, no root, no Docker
socket access). Method: six parallel read-only audits (web/design system, desktop, Android +
toolchain, platform capabilities, task UI + dashboards + metrics, quality gates + host), each
reading the code rather than the docs, plus spot checks by the lead session. Nothing was
modified during the audit; the harness built afterwards is described in §9.

**Statuses.** `done` — exists and is verified; `partial` — exists, gaps named; `missing` —
nothing to reuse; `needs-rebuild` — exists but in a shape the target rules out. "Blocked by
host" is a note on a status, not a status: it says what this machine lacks.

**How to read this.** Each numbered target from the brief gets a section with a sub-item table
(status, key paths, one-line justification) and a *Reuse verdict* — what to keep, extend or
replace — because the brief's first instruction is to finish what is good rather than rewrite.
`MILESTONES.md` is derived from the gaps here; `scripts/verify/mNN-*.sh` are the gaps made
executable.

---

## 0. Summary

| # | Target | Status | One line |
|---|---|---|---|
| 1 | Design system | partial | 67 CSS tokens + TS mirror with parity tests; Android and desktop hand-copy them; no generator, no Compose theme, no style guide, 284 ad-hoc values |
| 2 | Web app | partial | Coherent token-driven console, 588 unit + 112 e2e tests, no dead controls; primitives are CSS classes not components; states and responsiveness uneven |
| 3 | Desktop app | needs-rebuild (GUI) / done (agent) | jarvis-desktop is a headless device agent (833 tests); no window, tray or hotkey; notifications exist |
| 4 | Android app | partial | View-based app with WebView parity and 55 Python mirrors; no JDK/SDK/wrapper on this host; no Compose; lint non-blocking; phone automation live, not flagged |
| 5 | Task-execution UI | partial | Server-side task registry with steps/progress, live tool events for chat turns, approvals, dock; no output stream, no live tool calls for jobs, no timeline |
| 6 | Dashboards | missing | No dashboard, chart library, layout persistence, data-source abstraction or user identity; history/stats API exists as a seed; no InfluxDB anywhere |
| 7 | Platform capabilities | partial | MCP client + UI done; OpenAI-compatible tool calling + hermes fallback done; schedules and webhooks done; skills missing; no queue/retries/engine; agent loop is act→observe without plan/verify; orchestrator still Ollama-native; Tier 2 ungated |
| 8 | Agentic automation | partial | Desktop: server-driven multi-step via `control_device` rounds, e2e-proven; phone: fully implemented and ON, must become scaffold + flag OFF |
| 9 | Quality gates | missing → harness built | No `verify-all`; three masking points; Playwright blocked by port and browsers; Android blocked by toolchain. `make verify-all` now exists (§9) |
| 10 | Research engine | partial | A real plan→search→read→note→write worker on SearXNG (in the stack, not running here); no lead-following, cross-check, confidence, markdown reports, viewer or eval |
| 11 | Coding agent | partial | Sandboxed plan/work/check/report with a proven container argv; no verify-until-green loop, no commits, no approval modes, no fixture, no live containment check (no Docker for `jarvisdev`) |
| 12 | Subagents | needs-rebuild | Orchestrator fan-out only (no roles, tools, budgets, queue, tree); nothing in core |
| 13 | Memory | partial | Durable store + hybrid keyword/embedding recall + remember/forget; no auto-extraction, UI, export or eval |
| 14 | Notes | missing | Nothing first-class; reports are squeezed into 400-char memory entries |
| 15 | User interactions | partial | Threads archived and resumable, barge-in and ⌘K exist, a briefing fires on a schedule; no thread search, notification record, proactive UI moments, "why" trace or the three tests |
| HC | Hard constraints | see end | Local-only mostly holds (orchestrator still speaks Ollama-native); one token source not yet; headless feasible; no device access enforced by the harness |

---

## 1. Design system

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| One token source (palette, type, spacing, radii, elevation, motion) | partial | `jarvis-web/src/lib/styles/tokens.css` (67 `--jv-*`), `jarvis-web/src/lib/tokens.ts` (mirror), `jarvis-web/src/lib/tokens.test.ts` | All six groups exist as CSS custom properties with a TS mirror and a parity test — the right seed. But it is a web file, not a language-neutral source; `--jv-stagger-*` are declared and unused (values duplicated in `motion.ts:14,17`); `--jv-surface-sunken` is used (`CodeDiff.svelte:55`) and never defined |
| CSS variables consumed by web | partial | 1,019 `var(--jv-` references; `jarvis-web/src/routes/+page.svelte:829-841`, `ChatPanel.svelte:325-332` | The HUD re-derives a private `--accent/--dim/--line/--line-soft` layer; `scripts/verify/web_adhoc_scan.mjs` finds **284 ad-hoc values in 76 files** (25 hex — 19 of them GLSL in `Orb.svelte:219-253`; 26 `rgba()`, 17 in `chrome.css`; 143 raw spacing/size values; 24 raw durations; 20 raw type values, 18 in the HUD) |
| CSS variables consumed by desktop | missing | `jarvis-desktop/jarvis_desktop/theme.py:77-105`, `tests/test_theme.py` | 12 colours hand-copied from `tokens.ts` and diffed by 19 tests (values + WCAG AA); the only consumers are two ephemeral Tk dialogs. There is no CSS surface on the desktop today (§3) |
| Generated Compose theme for Android | missing | `android-app/app/build.gradle.kts:94` (`compose = false`), `android-app/app/src/main/kotlin/ai/jarvis/app/ui/JarvisUi.kt:46-143`, `android-app/tools/design_token_test.py`, `type_scale_test.py` | Zero `@Composable`; 9 colour constants, a 7-step type scale and 8 spacing steps are hand-copied Kotlin, pinned only by two Python mirrors. `res/values/colors.xml:8-24` and `themes.xml:22-29` still carry the **pre-token palette** (`jarvis_dim #CC7FD7EA` vs token `#9fc0cc`, `jarvis_faint #FF5A7A86`, `jarvis_approve #35D08A` vs `--jv-ok #6ff2c0`, `jarvis_deny #FF5C5C` vs `--jv-danger #ff6b5c`) and no test reads them |
| Orb palette | partial | `jarvis-web/src/lib/components/Orb.svelte:234-253`, `android-app/.../ui/SiriPalette.kt`, `android-app/tools/reactor_orb_test.py` | A second, 16-colour four-state palette lives outside the tokens on both surfaces (only 4 of 16 equal a token), pinned web↔Android by a mirror test and exempted from the hex test |
| Rendered style-guide page | missing | `jarvis-web/src/routes/` (areas automations code devices healthz settings tasks tools) | No route, no Storybook/Histoire; `README.md:172-235` documents tokens in prose |
| Loading / empty / error / offline on every screen | partial | `Skeleton.svelte`, `Reconnect.svelte`, `chrome.css:199-232` (`.jv-empty`), `consoleLink.ts` | Seven console pages have skeleton + empty + error + reconnect. Gaps: `/settings` has no skeleton and no empty state; the chat sidebar prints "Nothing yet" before history arrives (`ChatPanel.svelte:180`); the HUD has an OFFLINE label but no reconnect control (`+page.svelte:128,577`); no `+error.svelte` anywhere; nothing observes `navigator.onLine`; the empty-state markup is hand-copied 8× across 6 pages. `scripts/verify/web_states_check.py`: 18 problems across 8 pages |
| No ad-hoc colours or spacing anywhere | partial | `tokens.test.ts:129-175` | The existing test bans hex only inside `<style>`/`.css`, allows `#000/#fff`, and exempts script bodies — so GLSL, `qr.ts:557` and every `rgba()`/px/duration literal pass it today |

**Reuse verdict.** Keep every token name and value in `tokens.css`/`tokens.ts` — they become the
seed of `design/tokens.json`. Keep the three parity tests (web, desktop, Android mirror) as
regression checks that the generator must satisfy. Add: the language-neutral source, a
generator for CSS/TS/Python/Kotlin/Compose/XML, the orb palette as a token group, a style-guide
route, one `<ScreenState>` component that owns the four states. Replace: hand-copied constants
in `JarvisUi.kt`, `SiriPalette.kt`, `theme.py`, and the stale `colors.xml`.

## 2. Web app

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Complete visual redesign on the design system | partial | `jarvis-web/src/lib/styles/{tokens,base,chrome}.css` (1,472 lines), 9 routes, 20 components (6,041 LOC) | The chrome is coherent and largely token-driven already; "redesign" here means re-expressing every screen through an extracted component library on the generated tokens with zero ad-hoc values — not a restyle from nothing |
| Fully responsive | partial | 6 `max-width` breakpoints (480/560/640/720/800 px), 44 `clamp()`, `e2e/e2e.spec.ts:950-1038` (390 px proofs), `base.css:21` | Desktop-first, phone width proven at 390 px, nothing proven between phone and desktop; `html,body{overflow-x:hidden}` can hide the very overflow a responsive check should catch; fixed-square orb `min(58vmin,520px)` |
| Component library extracted | partial | `jarvis-web/src/lib/components/` (20 app components), `chrome.css:668-1198` | Primitives — button, input, select, pill, panel, row, field, toolbar, empty state, skeleton, toast, palette — are CSS classes, not components; only `Skeleton`, `Reconnect`, `Toasts`, `TaskBar`, `ModeToggle`, `CodeDiff` are generic; no index/barrel |
| Component library documented | partial | 18/20 components have a header doc comment (`EntityRow.svelte`, `Orb.svelte` do not) | No props/usage docs, no rendered examples |
| No dead buttons | done | `scripts/verify/web_dead_controls.mjs` → 0 hits in 29 files | 94 `<button>`s, every one handled or a form submit; 41 `disabled` states all state-driven with an on-screen reason; every `href` resolves |
| No placeholder screens | done | grep TODO/FIXME/"coming soon"/"not implemented"/`alert(` → none | Unsupported backend features hide with a `.notice` hint rather than a stub. Stale prose in `README.md:328,394,444` is documentation drift, not dead UI |
| Tests | done (as far as they go) | 588 vitest in 32 files (3.8 s), 112 Playwright in 11 specs, `tests/web/smoke.test.mjs`, `tests/web/mock-ha.mjs` (2,796 lines) | Broad coverage of every page's CRUD, approvals, tools, pairing, voice, embed, 390 px. The mock can fake errors and (partially) empty; offline is done from Playwright by closing sockets; no latency knob. Playwright could not run on this host until the harness installed Chromium and made the port a knob (§9) |

**Reuse verdict.** Keep the routes, the server side (`hooks.server.ts`, `src/lib/server/**`,
`ws-proxy.js`), `jarvisClient.ts` (55 WS commands), the Orb, the mock backend and every spec.
Extend: extract `src/lib/ui/` primitives from `chrome.css`, add `<ScreenState>`, a screen
manifest, `+error.svelte`, offline detection, tablet breakpoints, and the three new specs
(states, responsive, controls). Replace nothing wholesale.

## 3. Desktop app

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Same redesign on shared tokens | partial | `jarvis-desktop/jarvis_desktop/theme.py`, `consent.py:365-481` (`TkConsentGateway`), `companion.py:505-633` (`TkAsker`) | Only two ephemeral Tk dialogs draw anything; tkinter is not even installed here (`import tkinter` → ModuleNotFoundError) |
| Feature parity with web | missing | `jarvis_desktop/__main__.py` (subcommands run/tiers/policy/audit/cron/doctor/status/enrol), `status.py:14-20`, `README.md:108`, `docs/plan-settings-agency-pairing.md:738` | It is a CLI device agent: no chat/HUD, devices, automations, settings, code, tasks or tools UI. A settings UI is a documented non-goal for the *agent*; the *app* the target asks for does not exist |
| System tray | missing | `status.py:14-20`, `README.md:108` | Explicitly rejected for the agent ("single hard dep = websockets"); no `pystray` anywhere |
| Notifications | done | `actions/system.py:305-415` (`notify`), `companion.py:330-371` (`CommandNotifier`), `consent.py:243-318` | notify-send / osascript / Windows toast with a log fallback; used for the `notify` action, companion messages and refusal notices; unit-tested with fake runners |
| Global hotkey for push-to-talk | missing | `enrol.py:4-7,87-110` | No pynput/keyboard, no mic streaming to the assist pipeline; only one-shot enrolment recording via arecord/sox/ffmpeg (none present here) |
| Multi-step agentic automation on desktop | done (server-driven) | `channel.py:399-484`, `actions/registry.py:145-305`, `jarvis-core/jarvis/llm/agent.py:57,901` (`max_tool_rounds`=5), `integrations/device_control/__init__.py:316-322,547-565,632-645`, `api/devices.py:387-430` | 21 actions (`actions/builtins.py:37-95`), exactly-once per command, one in-flight per action, rate limits, untrusted results raise later steps to CONFIRM; e2e proves Tier 1, Tier 3, raise-only and reconnect. No device-side batch/sequence with state between steps |
| Automated headless verification | done for the agent | 833 unit tests pass here in 15 s with a `FakeTk`; 32 e2e via `testing/harness` | Anything with a window is untested against a real toolkit; a future shell needs Playwright/Electron under Xvfb (`/bin/xvfb-run`, `/bin/Xvfb` present; Chromium libs present) |

**Reuse verdict.** Keep the agent whole (14,036 LOC, policy engine, consent chain, 21 actions,
channel, audit) as the automation backend. Add a desktop *app*: an Electron shell that loads
the jarvis-web build (parity by construction), draws the tray, posts native notifications,
registers the push-to-talk shortcut, and talks to the local agent over a new IPC so consent
prompts move from Tk into the shell. Replace the Tk dialogs with that IPC path.

## 4. Android app

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Feature parity with web | partial | `ui/ConsoleTab.kt:54-60`, `ui/ConsoleFrame.kt`, `ManagementActivity.kt`, `tools/console_parity_test.py`, `tools/api_parity_test.py` | Parity by embedding: all 7 console sections load in an authenticated WebView behind a native tab strip pinned to `+layout.svelte`; the HUD and the PHONE tab are native |
| Same tokens / generated Compose theme | missing | `app/build.gradle.kts:90-97` (`compose=false`, `viewBinding=false`), `ui/JarvisUi.kt:46-143`, `res/values/colors.xml` | 167 Kotlin files, 0 XML layouts, 0 Composables — every screen is programmatic `android.widget` views; 19 `0xAARRGGBB` literals, 8 `Color.parseColor`, 45 numeric `dp()` and 35 `textSize` literals outside the theme file |
| `./gradlew assembleDebug` on this host | missing (blocked by host) | `android-app/gradle/wrapper/gradle-wrapper.properties` (8.10), `tools/local-android-build.sh`, `README.md:33-49` | `java` not found; no `ANDROID_HOME`; `gradlew`/`gradlew.bat`/`gradle-wrapper.jar` are gitignored and absent; no `local.properties`. Needs JDK 17, Gradle 8.10 (or a committed wrapper), `platforms;android-35`, `build-tools`, ~2 GB from dl.google.com — all installable under `$HOME` without root; 42 GB free, 8 GB RAM (`-Xmx3072m`) |
| Unit tests | done (CI) / not runnable here | `app/src/test/kotlin` (18 files, 172 `@Test`), `android-apk.yml:102-103` | JUnit 4 pure-logic suite, blocking in CI |
| Android lint | partial | `app/build.gradle.kts:138-144` (`abortOnError=false`), `android-apk.yml:108-111` (`|| true`, `continue-on-error`) | Runs, reports, can never fail anything; no `lint.xml`, no ktlint/detekt |
| Robolectric / JVM screenshot tests | missing | `gradle/libs.versions.toml`, `androidTest/support/Screenshots.kt`, `README.md:446-450` | No Robolectric/Paparazzi/Roborazzi; screenshots exist only as on-device diagnostics in the emulator suite; the README names `ApprovalBridge` as needing Robolectric |
| `docs/ANDROID_DEVICE_TESTS.md` | missing | `docs/verification.md:314-366` (15 Unproven rows), `android-app/docs/testing.md` | Seed for the backlog — phone-only: assist role + GrapheneOS reset, lock-screen popup and keyguard-inert APPROVE, Tier-3 prompt over a third-party app, pocket wake word + FGS/battery, real mic capture/silence/on-device STT, Bluetooth routing + media buttons, wake-listen gate on a drive, in-app updater install, alarms after reboot, TalkBack, voice enrolment, QR pairing, Android Auto, accessibility-service enable + UI automation on real apps, notification access, 16 dangerous-permission prompts, GPS geofences, QS tile, API 31 splash, GrapheneOS checklist, full-screen-intent degradation. Emulator-only (also deferred): 42 instrumented tests, overlay acceptance, API 29/30 insets crash class |
| Phone automation scaffolded + flagged OFF | needs-rebuild | `automation/accessibility/*` (gestures, screenshots), `automation/notify/JarvisNotificationListener.kt`, `automation/actions/builtin/*` (48 actions), `automation/policy/PolicyStore.kt:115` (`automation_enabled` defaults **true**) | The opposite of scaffolded: fully implemented and shipping, gated only by system opt-in + tier consent; no `buildConfigField`, no flag object anywhere |
| Existing verification to keep | done | `android-app/tools/*_test.py` (55 mirrors, all pass here in 5 s), `ci.yml` `android-specs` | The mirrors read the Kotlin as text and pin policy, channel, wake, audio, design, triggers, updater; they are the only Android proof that runs on this host today |

**Reuse verdict.** Keep the app, the View-based screens, the WebView parity, the policy engine,
the mirrors, the JUnit suite. Add: a committed Gradle wrapper, a `$HOME` toolchain, Compose
enabled with a *generated* theme (the Views keep using generated constants), Robolectric +
Roborazzi, blocking lint, the device-test backlog. Replace: hand-copied constants, the stale
XML palette, the unflagged automation — behind `BuildConfig.PHONE_AUTOMATION=false` and a
master switch that defaults OFF.

## 5. Task-execution UI

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Current step, live | done | `jarvis-core/jarvis/tasks.py:341-400`, `jarvis-web/src/lib/tasks.ts:242`, `TaskCard.svelte:63-116` | Server-owned steps; `jarvis_task_updated` over the single `/ws` relay; code and research jobs drive them |
| Tool calls as they happen | partial | `llm/agent.py:1004-1026` (`jarvis_tool_started/finished`), `ToolActivity.svelte`, `ChatMessage.svelte`; `integrations/code/agent.py:353` | Live for conversation turns only. Code jobs append a `trail` surfaced after the job ends (and the console never renders it); research jobs emit steps only |
| Streaming output | partial | `src/lib/pipeline.ts:315-336`, `chat.ts`; `tasks.py:105` (`TaskStep.detail` ≤ 2000 chars) | Chat text/thinking deltas stream; no stdout/log stream exists for any task, code job or `/execute` — check/command output lands in the final result |
| Progress | done | `tasks.py:177-207` (server `fraction`), `TaskBar.svelte`, `ToolActivity.svelte:41-43` | Determinate/indeterminate honestly from the server; per-round tool progress |
| Approve / cancel controls | partial | `Approvals.svelte` (console + HUD, questions, taint warning, expiry), `jarvisClient.ts:683,887`, `api/common.py:1298-1326` | Approvals are complete. Cancel is a request: code and research workers check it, generic tasks may not ("a worker that does not check may still be running"); `web.browse` approvals bypass the console (`companion.ask` to phones/desktops only) |
| Browsable history timeline | partial | `tasks/+page.svelte:277-288`, `tasks.py:102` (`MAX_TASKS=200`, persisted `.storage/tasks.json`), `code/__init__.py:124` (`MAX_KEPT=20` in memory) | A finished list with steps; no time axis, no per-task event log, diffs for 20 jobs then "no longer held in memory"; `trace.get` and `logbook.get` exist and nothing in the console consumes them |
| Orchestrator jobs | missing from the UI | `integrations/orchestrator/__init__.py` (never touches `TaskRegistry`), `jarvis-orchestrator/app/opencode.py:91` (in-memory dict, lost on restart) | Delegate/code_task/execute never appear on `/tasks` or the dock; status only when the model calls `code_task_status` |

**Reuse verdict.** Keep `TaskRegistry`, the three task events, `TaskCard/TaskBar/TaskDock`,
`Approvals`, `ToolActivity`, the chat reducers, and the mock's `jarvis/test/task_run` /
`tool_run` drivers. Extend with a task-events contract (`tests/contracts/task_events.json`)
that adds tool and output events to *every* task, a cooperative cancel API every worker calls,
a persisted per-task event log, a task detail route with a timeline, and orchestrator jobs
registered as tasks.

## 6. Customisable dashboards

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Widgets: add / remove / resize / reorder | missing | `grep` over `jarvis-web/src` for chart/graph/widget/dashboard/drag/resize → 25 incidental hits, none functional; `README.md:193` "**No dashboards.**" | Nothing to reuse on the web side |
| Persist per user | missing | `jarvis-core/jarvis/auth.py:3` ("no user accounts"), `auth.py:92-110` (`TokenInfo{id,name,…}`), `store.py` (atomic JSON `.storage/<key>.json`), `settings.py:14-19` (global allow-listed overlay) | There is no user; the token is the identity. Per-user = per-token-id, stored through `store.py` |
| Multiple chart types | missing | `jarvis-web/package.json` (runtime dep: `ws` only) | No chart library; dates via `toLocaleString`; QR and favicon are hand-written, so a small hand-written SVG chart layer is in character, or one dependency |
| Pluggable data-source abstraction | missing | — | None |
| Internal metrics source | partial | `api/rest.py:324-335` (`/api/history/period`), `integrations/history/__init__.py:147-187` (`history.get/stats`), `integrations/recorder/__init__.py:606-630` (SQLite `states`/`events`, 5 s flush, 10-day purge), `config/configuration.yaml:~916-935` (`command_line` sensors: disk, load, uptime; `rest` sensors polling Ollama) | Entity time series and min/max/mean exist and already feed the console's `state_changed` subscription. No long-term statistics, no system metrics (no psutil), no LLM token counts (`grep eval_count|usage|total_tokens` → 0), no pipeline stage latencies, no Prometheus; tool `duration_ms` only as bus events |
| InfluxDB source adapter | missing | `grep -rni influx` → 0 code hits; no `INFLUX*` key in either `.env`; `:8086` refused; nothing in either compose file | "The existing InfluxDB" is not on this host and not named anywhere in the repo; its generation (1.x InfluxQL / 2.x Flux / 3.x SQL) is unknown and must be detected at configuration time |

**Reuse verdict.** Keep the recorder/history API as the first data source. Add everything else:
a `DataSource` protocol in jarvis-core, an internal source (history + new system/LLM/task
metrics), dashboards stored per token id, a widget grid, ≥ 4 chart types, and an InfluxDB
adapter tested against a fake server, with a scripted live probe for the operator.

<!-- PLATFORM_SECTION_BEGIN -->
## 7. Platform capabilities

Audited in `jarvis-core/jarvis/` (llm, integrations, automation, api) and `jarvis-orchestrator/`.

### 7.1 MCP client

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Connect to MCP servers and expose their tools | done | `jarvis-core/jarvis/integrations/mcp/client.py` (414 lines: JSON-RPC 2.0, protocol `2025-06-18`, `initialize`, `tools/list` paged, `tools/call`, Streamable-HTTP and stdio transports), `catalog.py` (names `mcp_<server>_<tool>`, sanitised descriptions, schema depth ≤ 8, `MAX_TOOLS_PER_SERVER=64`), `__init__.py` (492 lines) | Hand-rolled, no SDK dependency; tools registered into the LLM `ToolRegistry`, results fenced as untrusted and the turn tainted; runtime-added servers persisted in `.storage/mcp_servers.json`; 45 tests in `tests/test_mcp.py`. Tools only — no resources, prompts, sampling or elicitation (the target does not ask for them) |
| UI to add / remove / inspect | done (inspect is thin) | `jarvis-web/src/lib/components/McpServers.svelte` (383 lines) on `/tools`, `mcpDraft.ts`, REST `/api/mcp/servers*`, `/api/mcp/reconnect` (`rest.py:680-712`), WS `jarvis/mcp/list\|add\|remove\|reconnect` (`websocket.py:1146-1149`), `e2e/mcp.spec.ts` (7) | Add form with validation, remove, reconnect, connected/error state, per-server tool list. "Inspect" today is the tool list only — no schemas, server info, last error, or a gated test call; no automatic reconnect with backoff |
| Tier semantics | needs-fix | `llm/tools.py:1047-1059` (`requires_approval`: tier ≥ 3, `GATED_DOMAINS`, per-tool gate), `config/configuration.yaml:580` ("2 = confirm first") | **Tier 2 is not gated in code**; MCP's `default_tier: 2` runs unprompted while the config comment promises a confirmation. Either the comment or the code is wrong; no shared table pins the tier meanings across core, web and Android |

### 7.2 Skills

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| SKILL.md loader (Agent Skills format) | missing | `grep -rni "SKILL.md\|skills\?\b" jarvis-core/jarvis jarvis-core/docs docs` → 0 | Nothing to reuse. Prompt assembly is one flat `persona_file` (`llm/agent.py:547-624`: persona + tool rules + toolbox sentence + house summary + memory notes). Capabilities are added today by Python integrations, `*.tool.yaml` HTTP manifests (1 in the repo), console-authored HTTP tools, MCP servers, and YAML scripts with a `description:` |

### 7.3 Hooks

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Wake word | partial | `voice/pipeline.py:59-104,1007-1010` (`voice_pipeline_event {run_id,type,data}` incl. `wake_word-end`), `automation/triggers.py:516` (`event` platform) | Reachable only as `platform: event, event_type: voice_pipeline_event, event_data: {type: wake_word-end}`; no dedicated platform, no example, no test |
| Task lifecycle start / complete / fail | partial | `tasks.py:76-78,437-449` (`jarvis_task_added/updated/removed` → `{"task": {...}}`), `triggers.py:104-111` (`event_data` matches top-level keys only) | Status is nested under `task`, so filtering needs a template condition; no distinct started/completed/failed events; `jarvis_background_task` has no listener anywhere |
| Schedules | done | `automation/triggers.py:352,378,483` (`time`, `sun`, `time_pattern`), `integrations/schedule/{__init__,plan}.py` (once/daily/weekly/every ≥ 5 min, persisted `.storage/schedule.json`, DST-aware, boot catch-up with a 6 h grace, each firing minted as a Task), REST `/api/schedule*`, WS `jarvis/schedule/*`, `ScheduledJobs.svelte` | 58 tests. No cron syntax (not required) |
| Inbound webhooks | done | `api/rest.py:161-191` (`GET/POST/PUT/HEAD /api/webhook/{id}`, id is the secret, optional `webhook_require_auth`), `triggers.py:633`, `common.py:1231` | Works; documentation and an example are thin |
| Trigger / condition / action vocabulary | done | `triggers.py:769-786` (10 platforms), `conditions.py`, `actions.py` (delay, wait, choose, repeat, parallel …), modes single/restart/queued/parallel; 132 automation tests | A complete HA-style engine to hang the new platforms on; 57 bus event constants exist to trigger from |

### 7.4 Task engine

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Queue | missing | `tasks.py:39-45` ("does not run anything"), `integrations/code/__init__.py:910-913` (`ensure_future` immediately) | The registry is a record of work, not a scheduler: no FIFO, no concurrency cap, no priority |
| Scheduled / recurring | done | `integrations/schedule/` (see 7.3) | Persisted, catch-up, model may create only notify/research kinds |
| Background execution | partial | in-process asyncio tasks for code/research/schedule; `llm/tools.py:1939-1995` `run_background_task` **records only — no worker**; restart → queued/running marked `error "interrupted"` (`tasks.py:244-260`) | Work runs, but nothing owns it: no resume, no worker pool |
| Retries with backoff | partial | `llm/agent.py:1145-1281` (LLM round: 2 attempts, `0.5·2^n`, ≤ 30 s, before first token only) | None for tasks, schedule firings, code jobs or orchestrator jobs (`grep -rni "backoff\|retry"` → no hits there) |
| Persistent history | partial | `tasks.py:277-292` → `.storage/tasks.json` (200 cap, atomic via `store.py`); `code/__init__.py:124,1013` (`MAX_KEPT=20` in memory: diff, trail, checks, commands); `jarvis-orchestrator/app/opencode.py:272,363` (in-memory dict; `load_persisted` never called) | Task records survive; the interesting parts (diffs, tool trails, output) do not |

### 7.5 Agent loop and the model endpoint

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Plan → act → verify | partial | `llm/agent.py:664-782,887-963` (`_run_rounds`: ≤ `max_tool_rounds`=5 streamed rounds, sequential tool execution, one final round with tools withdrawn), `integrations/code/agent.py:1-35,223-270` (plan → work → check → report; `MAX_ROUNDS=40`, 20 min) | The chat agent is bounded act→observe (ReAct) with a "you narrated a call, make it" nudge and a think escalation — no plan, no verify. Jarvis Code has the shape, but "check" runs only if the model chooses to (`run_check` :593-628 is not enforced after work). The orchestrator's `/delegate` is fan-out + synthesis with no tools and no loop |
| Tool calling via the OpenAI-compatible endpoint | done | `llm/openai_compat.py` (608 lines: `/v1/chat/completions` SSE, `tools` + `tool_choice:auto`, fragment merging by `index`, `/v1/models`, `/v1/embeddings`), `integrations/llm/__init__.py:172,224-299` (`BACKENDS=("ollama","openai")`, `/v1` in the URL ⇒ openai) | 43 tests. llama-swap is "just another OpenAI URL" — named once, in a YAML comment (`configuration.yaml:917`). `num_ctx` is dropped on the OpenAI wire; the schema budget is ≈ 59 % of an 8 k context (`DEVIATIONS.md` §11, `test_prompt_budget.py` ceiling 0.62) |
| Hermes-style tool parser | done (as fallback) | `llm/toolcalls.py:70-76,140-240` (`<tool_call>` regex, `<\|python_tag\|>`, fenced JSON, bare-JSON brace matching; only names in the offered set, ≤ 8 calls), `agent.py:850-885,1216-1224`, `tests/test_tool_call_recovery.py` (24) | Primary path is the server's structured `tool_calls`; recovery runs only when none came back. Correct order of preference |
| Config surface | partial | `configuration.yaml:158-225` (`llm:` block), `!env_var OLLAMA_URL/OLLAMA_MODEL` (:162,167), `LLM_URL/LLM_MODEL` only as compose aliases (`jarvis-core/docker-compose.yml:112-113`), `PLANNER_MODEL/CODER_MODEL` orchestrator-only | The documented first-class variable is still Ollama's; the operator's live `.env` points `LLM_URL` at an off-host LiteLLM `/v1` proxy — where it routes is outside the repo |
| Orchestrator on the same endpoint | missing | `jarvis-orchestrator/app/fanout.py:51-103` (Ollama-native `/api/chat`), `opencode.py:243-248` (`opencode run --model ollama/<CODER_MODEL>`) | The second model client in the stack speaks a different protocol from the first; nothing tests it against an OpenAI-compatible server |
| 100 % local in code | done (config-dependent) | `requirements.txt` (no `openai`/`anthropic` packages), `voice/wyoming.py` (whisper :10300, piper :10200, openWakeWord :10400), `docker-compose.yml:181-242`; the 11 grep hits for cloud names are an SSRF deny entry, comments, a doc example and negative tests | Nothing stops `llm.url` pointing at a cloud proxy; a local-only guard does not exist |

**Task event stream (what the UI can subscribe to today).** `jarvis_task_added/updated/removed`
(`{"task": Task.as_dict()}` with steps, fraction, done/total), `jarvis_tool_started/finished`
(name, arguments, round, index, total, ok, status, error, duration_ms — conversation turns only),
`jarvis_tool_called`, `jarvis_approval_required/resolved` (`PendingRequest` with pinned args,
expiry, taint, choices), `voice_pipeline_event`, in-run `intent-*` events, `jarvis_schedule_fired`,
`jarvis_background_task`. REST/WS: `/api/tasks*`, `jarvis/tasks/*`, `/api/jarvis/approve`,
`jarvis/approve`, pending list only via the service `llm.pending_requests`. **No shared contract
fixture** pins any of these — `tests/contracts/` holds only `entity_id_rename.json` and
`forge_allow_list.json`; task/approval/turn payloads are pinned per side.

**Tests in the area.** jarvis-core: llm/agent/tools 293, mcp 45, code 260, automation 132,
schedule/tasks 81, api 189, voice 157, orchestrator-side 45; `jarvis-orchestrator/tests` 17.

**Reuse verdict.** Keep, as they are: `openai_compat.py`, `toolcalls.py`, the `ToolRegistry`
and approval gate, the MCP client/catalog/manager and its UI, the schedule integration, the
automation engine, the `TaskRegistry` record. Extend: `tasks.py` into an engine (queue,
workers, retries, resume, persisted results); `agent.py` with a plan/verify layer that writes
its steps into the registry; `triggers.py` with `wake_word` and `task` platforms; the MCP UI
with inspect + a gated test call; the config so `LLM_URL` is first-class with a local-only
guard. Add: the skills loader; the tier contract table. Replace: the orchestrator's model
client (Ollama-native → the same OpenAI-compatible client) and its in-memory job store.
<!-- PLATFORM_SECTION_END -->

## 8. Agentic automation

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Desktop: multi-step automations against desktop capabilities | partial | §3 row "multi-step"; `jarvis-desktop/tests_e2e/test_desktop_e2e.py` (21 tests) | Works today as ≤ 5 server-driven tool rounds over one generic `control_device` tool plus YAML `device_control.run`; nothing carries state between steps, nothing verifies an outcome after acting (the plan → act → verify loop is §7's), and no e2e drives a ≥ 3-step automation with an approval in the middle |
| Phone: interfaces designed and scaffolded, flagged OFF, nothing device-tested | needs-rebuild | §4 row "phone automation" | Fully live implementation with the master switch ON; the target wants the interface, the flag, and the backlog — not the feature |

## 9. Quality gates

| Sub-item | Status at audit | Key paths | Justification |
|---|---|---|---|
| `make verify-all` exists | missing → **built** | `Makefile` (`verify` only, `-$(MAKE)` ignores smoke/egress/persona), now `verify-all` → `scripts/verify/all.sh` | The harness is described below |
| Builds web | partial | `make test-web` (`npm run build`), CI `web` job | Not reachable from `make test`/`verify` |
| Builds desktop (dist) | CI-only | `.github/workflows/desktop-dist.yml` (wheel + sdist, install matrix), `python -m build` absent in the venv | Repo policy: no frozen bundle |
| Builds Android | missing (blocked by host) | `android-apk.yml` (JDK 17, `gradle 8.10`, no wrapper); nothing local | See §4 |
| Runs all unit tests | partial | `make test` = ruff + Python suites + evals; vitest + svelte-check only via `test-web`; Kotlin `src/test` only in CI; `test-android` = Python mirrors | No single target covers Python + web + Kotlin |
| Lint | partial | ruff (`ruff.toml`, defect-only, clean); `svelte-check` 445 files 0 errors (type-check only, no eslint/prettier by design); Android lint non-blocking | Python done; web type-check only; Android cannot fail |
| Playwright E2E headless on this host | blocked → **unblocked by the harness** | `jarvis-web/playwright.config.ts` (hardcoded `:8199`, `reuseExistingServer:false`), no browsers in `~/.cache/ms-playwright` | The HUD container holds 8199. The harness installed Chromium (`npx playwright install chromium`, 104 MB) and made the port `E2E_PORT` (default unchanged); `bash scripts/verify/m03-web.sh` then ran the whole suite headless on this host: **110 passed in 1.6 min** (2 of the 112 are `test.skip`/`fixme`) |
| Fails on any error | missing | `Makefile:52-53` (`\|\| echo` on Playwright), `:118-124` (`-$(MAKE)`), `android-apk.yml:108-111` (`\|\| true`) | Three independent masking points; `docs/testing.md`'s "must be green on every push" is not enforced by any single command |
| Counts in docs | stale | `README.md`, `docs/verification.md:56-70`, `docs/testing.md:82` | Measured now: jarvis-core **2,540** tests (docs say 1,203/1,758), desktop **833** (722/803), browser **337** (328), vitest **588** (194/365), Playwright **112** (44/59), evals 58, orchestrator + sandbox 23, Android mirrors 55 |

**The harness (built during this audit).** `make verify-all` runs `scripts/verify/all.sh`,
which runs every `scripts/verify/mNN-*.sh` in order, writes `.verify/<milestone>.log`, prints
one table and exits non-zero if any script failed. `lib.sh` provides `check`/`check_not`/
`check_sh` that run *every* check and have no skip state. Each script is the machine-checkable
definition of its milestone; today every script but `m00` fails, by design. Three scanners
back the web checks: `web_adhoc_scan.mjs` (284 ad-hoc values today), `web_dead_controls.mjs`
(0 today), `web_states_check.py` (18 problems today). First full run: 150 s, M00 green, M01–M17
red for the reasons each names.

**Design directions.** Three divergent visual directions for the redesign — Instrument, Ledger,
Reactor — are mocked as static HTML under `docs/design/` (three signature screens each,
tokens inlined) and rendered headlessly to `docs/design/shots/`; the chosen one seeds M01/M02.

## 10. Research engine

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Plans queries, searches, reads, synthesises with citations | done | `jarvis-core/jarvis/integrations/research/__init__.py` (570 lines): plan → search → choose → read → note → write; `deep_research` tool, `research.run` service; config `max_queries` 4 / `max_sources` 8 / `per_domain` 2; `tests/test_research.py` (24), `test_research_plan.py` (37) | A real worker on the task registry: one planning call → several queries, dedupe + rank + per-domain cap, one note call per page, one synthesis call citing by number. Untrusted content stays fenced end to end |
| Search via self-hosted SearXNG, no paid APIs | done (not running here) | `integrations/web/client.py:26,331-379` (SearXNG only; "no cloud fallback"), `jarvis-browser/jarvis_browser/search.py:3,56` ("deliberately no fallback"), `jarvis-core/docker-compose.yml:402` (`searxng`, profile `search`, :8888), `jarvis-core/searxng/settings.yml` + `README.md`, `tests/test_web_integration.py:80,1052` (never reaches Google/Brave/serpapi) | In the stack behind `--profile search`; `:8888` is closed on this host and `jarvisdev` cannot start it (no Docker socket). Web search returns a named error rather than a cloud result when it is absent |
| Local fetch + readability extraction | done | `jarvis-browser/jarvis_browser/extract.py:3,253,302` ("readability-ish"), `browser.py:74,136,344` (`fetch`), `integrations/web/client.py` | Own extractor (tag-dropping + text), size caps, links; no PDF text |
| Follow leads (multi-step) | missing | `research/__init__.py:349-467` (`_run`: one search round, then read) | One round of queries decided up front; nothing a page says can add a query |
| Cross-check claims across sources | missing | `format_report` (`research/report.py`) | Notes are concatenated and synthesised; no claim ↔ source agreement pass |
| Confidence note per key claim | missing | — | Reports cite by number; no per-claim confidence |
| Quick vs deep modes | partial | `web_search` tool (one call, snippets) vs `deep_research` | Two tools, not two modes of one engine; no "quick lookup with a couple of pages read" |
| Runs as a task, cancellable, live in the UI | done / partial | `research/__init__.py:304-325,470-479` (`_check` → `_Stopped`), steps `search: <query>` / `read: <url>` | Steps show queries issued and pages being read; findings do not accumulate visibly — the report appears at the end as the task result |
| Reports saved as markdown, browsable | partial | task `result` string (`tasks.py`, `.storage/tasks.json`, 200 cap); optional `_remember` → a 400-char memory entry (`research/__init__.py:467`, `memory/__init__.py:112`) | No `.md` file, no report viewer, no markdown renderer in the console (`package.json` runtime deps: `ws`) |
| Scripted eval over a fixed question set | missing | `evals/` (routing, resolution, persona, decomposition only) | No question set, no report-file/citation/link-resolvability check |

**Reuse verdict.** Keep the engine, the SearXNG-only search and the extractor. Extend the
loop (lead-following, cross-check, confidence, quick mode), the output (markdown files +
viewer), the live events (findings), and add the eval. The host blocker is Docker access to
start `--profile search`.

## 11. Coding agent

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Workspace, read/write files, run commands and tests | done | `integrations/code/agent.py` (tools `list_files`/`read_file`/`search`/`edit_file`/`write_file`/`run_check`/`run_command`/`plan_step`; `MAX_ROUNDS`=40, 20 min), `workspace.py`, `repos.py`, `forges.py`; 260 tests | Repositories created under `code: workspace:`, cloned from allow-listed forges |
| Iterate plan → edit → run → verify until tests pass | partial | `agent.py:1-35,279-296,387-389,602-613` | Plan/work/check/report exists; "check" is model-optional and limited to the repo's `checks:` list; nothing loops until green |
| All execution in a disposable container | done | `sandbox.py` (`container_argv()`, one container per job, `--rm`, `--network none` unless `egress`, `--user`, `--cap-drop ALL`, tmpfs, ulimits, exactly one mount), `tests/test_code_sandbox.py` (62) | Argv-level proof on a machine with no Docker; the host itself never runs a shell (`agent.py:23-29`) |
| Explicit mount allowlist, per-task network policy | done | `sandbox.py:164,230-239` (`/work` = the one repo; `network: none|egress|network_name`) | Chosen by the operator per environment, never by the model |
| Verify script asserts nothing was written outside the sandbox | missing | `tests/test_code_sandbox.py` (static argv) | No live containment check; `jarvisdev` has no Docker socket, so none can run here today |
| Approval gates for diffs and commands in the task UI | partial | push = Tier 3 (`code.push_branch`), `Approvals.svelte`; `run_command` runs unprompted inside the environment | Edits and in-container commands are not gated; no per-task auto-approve rules |
| Configurable permission modes | missing | — | Nothing like Claude Code's `ask` / `acceptEdits` / `bypass` |
| Git-aware: branches, commits with messages, diffs in the UI | partial | `workspace.py` (`start_branch`, `jarvis/<date>-<job>`), `CodeDiff.svelte`, `code/+page.svelte`; `agent.py:33-37` ("why it never commits") | A branch and a viewable diff; work is left uncommitted on that branch by design |
| Fixture repo with failing tests + harness | missing | (no `fixtures/`) | Nothing in the tree; `find -iname '*fixture*'` → none |

**Reuse verdict.** Keep the sandbox and its invariants (`.claude/rules/jarvis-code-sandbox.md`),
the workspace/forge layer, the console. Extend: a verify-until-green loop that runs the
repository's tests as a check, commits with messages on the job branch, approval gates for
edits/commands with permission modes, a fixture repo and an eval, a live containment assertion.

## 12. Subagents & orchestration

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Spawn scoped subagents (researcher, coder, verifier, summariser) | partial / needs-rebuild | `jarvis-orchestrator/app/fanout.py:17,51-103` (`fan_out`: ≤ 8 tasks, `Semaphore(3)`, one `SPECIALIST_SYSTEM`, no tools, no loop, Ollama-native) | Fan-out + synthesis of plain prompts, out of process; no roles, no tool allow-lists, no context windows of their own |
| Drop-in markdown subagent definitions | missing | (no `config/agents/`) | — |
| Per-task concurrency limit + queue (KV-cache headroom) | missing in core | `fanout.py:62` (orchestrator only); `grep Semaphore\|max_concurrent jarvis-core/jarvis/llm` → none | Research reads pages sequentially; nothing bounds concurrent model calls across tasks |
| Context budget per subagent | missing | `openai_compat.py:79` (`num_ctx` dropped on the OpenAI wire) | No budget accounting at all |
| Live tree in the task-execution UI | missing | `TaskCard.svelte` (flat steps) | No parent/child task events |
| Fixture task proving two parallel subagents + roll-up | missing | — | — |

**Reuse verdict.** Keep the orchestrator's fan-out idea; rebuild it in-process on the task
engine with markdown-defined agents, a model-call pool with a per-task limit and queue,
budgets, parent/child task events for the UI, and a harness-driven fixture.

## 13. Memory

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Durable facts, preferences, people, projects, instructions | done | `integrations/memory/__init__.py` (1,114 lines: `MemoryStore`, `MemoryEntry` — text ≤ 400 chars, tags ≤ 8, source, ttl/expires, pinned), `.storage/memory.json`, redaction of secrets (`redact`, `is_only_secret`), untrusted-source refusal (`TRUSTED_SOURCES`, `allow_untrusted`) | Structured, listable, editable by hand |
| Explicit remember / forget commands | done | tools `remember`/`recall`/`forget`, services `memory.add/search/forget/list` | Voice or text, through the model |
| Automatic extraction of durable facts from conversations | missing | `grep extract\|after_turn memory/__init__.py` → none | Only the model's own `remember` calls write |
| Relevance retrieval: local embeddings + keyword | done | `memory/vectors.py` (`VectorIndex`, `fuse`; `nomic-embed-text` via the LLM client's `embed()`; JSON sidecar `memory-vectors`; degrades to keyword when no embed model), `tests/test_memory_vectors.py` (21) | Hybrid ranking; the store is a JSON file + in-memory cosine, not sqlite-vec/Chroma — adequate at `max_entries` 500, and local |
| Injected at conversation time | done | `llm/agent.py:547-624` (`remembered_notes` → `get_context_block`, `context_limit` 600 chars / `context_entries` 8) | |
| Memory UI: browse, search, edit, delete | missing | `grep -rl memory jarvis-web/src/routes` → only incidental | No route, no component |
| One-click export / full wipe | partial | `memory.forget all=true`; `memory.list` | Wipe exists as a service; no export endpoint or file |
| Personalisation consumes memory; nothing else does | partial | research `_remember` (`research/__init__.py:467`) writes reports into memory | Reads are the agent's only; a second writer exists because notes do not |
| Scripted eval (store → restart → retrieve; forget; export) | missing | — | |

**Reuse verdict.** Keep the store, the vector sidecar, the redaction and trust rules. Add:
auto-extraction (a bounded post-turn model call, durable facts only), the UI, export, the
eval, and move the report use-case out to notes.

## 14. Notes

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| First-class markdown notes with tags, full-text search, wiki-links | missing | `ls integrations/` (no notes); memory entries are 400-character facts | Nothing to reuse but the `Store` helper and the FTS-less recorder |
| Create/edit from any surface and by voice ("note that…") | missing | `evals/routing.py` (no note intent) | |
| Notes as an agent tool; research → note; tasks read/append | missing | research `_remember` writes to memory instead | |
| Synced across web / desktop / Android through the API | missing | — | |
| API CRUD + search + tag-filter tests; voice-intent fixture | missing | — | |

**Reuse verdict.** Add the integration (markdown files under `<config>/notes/`, a SQLite FTS5
index, tags, `[[links]]`), tools, REST/WS, a console route, the voice intent; point research's
"remember the report" at it.

## 15. User interactions

| Sub-item | Status | Key paths | Justification |
|---|---|---|---|
| Persistent, browsable conversation threads that survive restarts and resume with context | partial | `llm/history.py` (`ConversationArchive` → `.storage/conversations.json`, `max_conversations`, `max_turns`), `llm/memory.py` (live `ConversationStore`, 900 s TTL, 20 turns), `agent.py:815-821` (restores the archived tail on resume), `ChatPanel.svelte` sidebar, `jarvis/conversation/list|get|delete|rename` | Archived and resumable; tool results are not archived; no search across threads |
| Searchable threads | missing | `history.py` (no `search`) | |
| Continuity across surfaces (desktop → Android, same thread) | partial | pipeline `conversation_id`; `companion.py:173-246` (desktop frames carry it); Android `JarvisConversation.kt` | Ids exist on the wire; nothing proves two clients converge on one thread, and no test does |
| Interruption / barge-in on voice | done | `routes/+page.svelte` (always-on VAD + barge-in), `wake.ts` | Web HUD; Android has its own (`MicStreamer`, `AudioAttention`) |
| Quick commands | done (web) | `commandPalette.ts`, `shortcuts.ts` (⌘K, chords) | Console only |
| Proactive: task-completion announcements, reminders, daily briefing | partial | `integrations/briefing/` (schedule per kind, `briefing_ready`, `briefing.*` services), schedule kind `notify`, `companion.notify/say/ask`, `persistent_notification` (HA compat) | A briefing exists and fires on a schedule; no task-completion announcement; deliveries are companion pushes and toasts, not designed UI moments; no notification record to retrieve |
| Personalisation with a "why am I seeing this" trace | missing | `agent.py` (memory injected silently) | No per-turn record of which entries were used |
| Thread-persistence, cross-client, proactive-trigger tests | missing | `tests/test_history.py` (29, in-process only) | No harness test across a restart, no two-client test, no hook → notification test |

**Reuse verdict.** Keep the archive, the palette, barge-in, the briefing. Add thread search,
a notifications record + inbox, task-completion and reminder hooks → UI moments, the "why"
trace, and the three harness tests.

---

## Delta — live interaction testing (added 2026-08-24, mid-run)

A late addition to the target state: a rig that talks to Jarvis the way a person does — spoken
audio in, spoken audio out, and the real web UI in between — plus a scenario suite that
exercises every capability through that rig rather than through the API, an intelligence
scorecard, and an exploratory pass. What follows is what exists for it today and what each
piece will cost, measured on this host rather than assumed.

### What already exists and is reusable

| Piece | Status | Where |
|---|---|---|
| A real jarvis-core, booted from a generated config, with fakes for the model and the voice services | done | `testing/harness/` (`Harness`, `JarvisClient`, `fake_ollama.py`, `fake_wyoming.py`) — M00 |
| A websocket + REST client that can drive a pipeline run and read every event | done | `testing/harness/client.py` (`command`, `subscribe_events`, binary handlers) |
| Headless Playwright against a built console, on a port of its own | done | `jarvis-web/playwright.config.ts`, `e2e/*.spec.ts`, `E2E_PORT` |
| A voice pipeline whose every stage is an observable event | done | `jarvis/voice/pipeline.py` — 14 event types, mirrored onto the bus |
| Wake-word detection as a first-class trigger | done | `platform: wake_word` — M12 |
| Task lifecycle as distinct events the UI and automations both see | done | M10, M12 |
| A scripted model that can answer planning, acting and verifying differently | done | `fake_ollama.py` rules — used by M11's end-to-end test |

### What the rig needs, and whether this host can do it

Every line below was **measured today**, not assumed:

| Need | Verdict | Evidence |
|---|---|---|
| Synthesise the *user's* speech in a voice that is not Jarvis's | **yes** | `pip install piper-tts` (1.7.0) works in the repo venv; `en_US-amy-low` (60 MB) downloaded to `testing/live/voices/`; one sentence synthesises in 0.9 s at 16 kHz mono. Jarvis's own voice is `en_GB-alan-medium` — a different speaker, accent and sex, so a transcript cannot be confused for the other side |
| Transcribe Jarvis's spoken reply with real Whisper | **yes** | the Wyoming faster-whisper on `:10300` returned `'Hey Jarvis, turn on the hall light.'` — exact — for the Piper-synthesised utterance above |
| Deliver audio through the **real browser microphone path** | **yes** | headless Chromium with `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<wav>%noloop` gives the page one live audio track carrying the file's signal (measured peak 1.0 on `http://127.0.0.1`, which is a secure context; `about:blank` is not, and `navigator.mediaDevices` is undefined there — the specs must navigate first) |
| Deliver audio through the **audio-input API** | **yes** | `assist_pipeline/run` + binary frames; `testing/e2e/test_harness_selftest.py` already drives it |
| A real model, for the intelligence eval and the judge | **yes** | llama-swap at `LLM_URL` answers `/v1/models`; `qwen3.8-27b` is loaded |
| Noise overlays at several SNRs, silence, wake-word negatives | **yes** | arithmetic on PCM in `numpy`; no service needed |
| A fixture website with known content, for checkable research | **yes** | a local HTTP server serving fixed pages |
| **SearXNG pointed at it** | **blocked** | SearXNG is `--profile search` in compose and `jarvisdev` cannot reach the Docker socket. The rig therefore ships `testing/live/fixture_search.py`, which serves SearXNG's own `/search?format=json` response shape over the fixture pages — the research integration is exercised through its real search client, against a server that is not SearXNG. Recorded in `BLOCKERS.md`: it needs the operator to add `jarvisdev` to the `docker` group |
| **The coding sandbox** (a real container per job) | **blocked** | same Docker-socket problem; already anticipated for M19 |
| Barge-in | **partial** | the web HUD has always-on VAD and barge-in (`routes/+page.svelte`); the pipeline has no server-side interrupt, so the scenario covers the UI's behaviour and says so |

### What this adds to the plan

Four milestones — **M24–M27** — and one rule that changes every milestone after them: from M24 onward,
a capability is not "done" until its live scenarios pass. `scripts/verify/live_interaction.sh
--implemented-only` is appended to every remaining milestone's verification, and scenarios for
capabilities that do not exist yet are written **now**, marked `gated-on: <milestone>`, and
expected to fail until that milestone lands.

The honesty rule from `PROCESS.md` applies unchanged: a scenario that cannot run on this host is
a failure or a `BLOCKERS.md` entry, never a skip.

## Delta — compose-native testing and the local AI toolbelt (added 2026-08-25, mid-run)

A second late addition, and the bigger of the two: **the compose stack is the runtime**, so the
tests run against the containers that are actually in use, and a short list of local services is
adopted only where it measurably improves a number the suite already reports.

### The thing that changed underneath: Docker works now

`docs/AUDIT.md` recorded "no Docker socket for `jarvisdev`", and `BLOCKERS.md` asked the
operator for it. It is there:

```
$ id -nG | grep docker        → docker
$ docker run --rm hello-world → Hello from Docker!
$ docker compose ls           → jarvis running(3), jarvis-core restarting(1), running(6)
```

Everything that entry blocked is now doable on this host: the coding sandbox's live containment
check (M19), SearXNG for the live research backend, and this addition in full.

### What the running stack looks like today

| Fact | Measured |
|---|---|
| Services | 8 in `jarvis-core/docker-compose.yml` (core, browser, three Wyoming, photon, config-init, plus profiles for searxng/mosquitto), 4 in the root file (web, orchestrator, sandbox, init) |
| Pinned images | `busybox:1.36` only. **Five are `:latest`** — whisper, piper, openwakeword, photon, searxng — so "the version we tested" is not a thing that exists |
| Healthchecks | jarvis-core, jarvis-browser, jarvis-web, orchestrator, mosquitto, searxng. **None on the three Wyoming services**, which are exactly the ones the voice path fails on |
| Resource limits | none anywhere (the commented-out Ollama block has one) |
| Named volumes | `mosquitto-data` only; everything else is a bind mount into the repo |
| Health, right now | `photon` has been **restarting (75)** in a loop; `jarvis-web` is **unhealthy** (its healthcheck cannot connect, and its logs show `[404] GET /v1/models`). Both were true before this addition and nothing in the repo said so |
| Host | 4 vCPU, 8 GB RAM (2 GB used, 5 GB available), 41 GB free on `/`. No GPU |

That last row is the finding: the suite has been green while two containers in the stack it
claims to describe have been broken for two days. Nothing tested the runtime.

### What the addition asks for, and what it costs here

| Slot | Default named | Feasible on this host | Cost |
|---|---|---|---|
| Compose as the test runtime (`up -d --wait`, unhealthy = failure, ERROR logs = failure) | — | yes | seconds per run |
| Resilience scenarios (restart core mid-conversation, kill STT mid-utterance) | — | yes | needs the volume snapshot below to stay re-runnable |
| Volume snapshot/restore around destructive scenarios | — | yes (`docker run --rm -v vol:/v -v out:/o busybox tar`) | one tar per affected volume |
| Shared headless browser service | Playwright/Chromium | yes — jarvis-browser already is one; the rig currently uses the Node Playwright in `jarvis-web/node_modules` | none new if the two are merged |
| Crawling/extraction | Crawl4AI, Docling | **needs measuring**: both are large images and Docling's models are hundreds of MB on a 41 GB disk with 5 GB RAM free | to be recorded in `docs/TOOLING_DECISIONS.md` |
| Embeddings + reranking | TEI / Infinity + a cross-encoder | plausible on CPU; `memory/vectors.py` already speaks an embedding endpoint and currently points at the model server | ~1 GB RSS for a small model |
| Vector store | Qdrant, or a written justification for the embedded one | either; the store today is a JSON sidecar with cosine similarity over ≤ 500 entries | a paragraph, or a service |
| Speech as services | speaches / faster-whisper-server; Kokoro-FastAPI beside Piper | the Wyoming containers already are services — the change is the OpenAI-compatible shape and the A/B | Kokoro is ~1 GB |
| Agent observability | Langfuse (self-hosted) | needs Postgres + ClickHouse: on 8 GB RAM that is the largest single ask here | to be measured before adopting |
| n8n bridge | existing self-hosted n8n over the tailnet | flag-gated, off by default | none until enabled |

### The rule that decides each of them

The addition's own words: **snapshot the scorecard baseline before, and the metric must improve
after, or the service comes out.** The suite already reports the numbers this needs — research
eval pass rate and cited sources, routing accuracy, WER, per-stage latency
(`.verify/live/results.json`, `docs/LIVE_TEST_REPORT.md`) — so "it feels better" is not
available as an answer. `docs/TOOLING_DECISIONS.md` records each choice, each rejection, and
the before/after numbers.

Explicitly out, by instruction: a second inference runtime (no Ollama), agent frameworks that
would replace the agent loop, anything cloud, and GPU residency for any new service without a
written VRAM justification.

## Delta — reach, routing, delegation and an ecosystem (added 2026-08-25, mid-run)

The third and largest addition, in three parts: Jarvis becomes **reachable** (messaging
channels, calendar, mail), its model traffic goes through **one gateway with a routing
policy**, heavy coding work can be **delegated** to a second backend, the console gets a
**motion layer**, and skills/MCP/plugins become a **curated, sandboxed ecosystem** rather than
a loader and a config key.

The brief names the failure modes to avoid by name: the OpenClaw-class assistants this is
modelled on shipped 140k+ internet-exposed instances, a marketplace supply-chain attack, and
one-click RCE. So every item below is paired with the thing that makes it not that, and those
pairings are milestones in their own right rather than acceptance criteria buried in a scope
line.

### What already exists here and is reused, not rebuilt

| The addition asks for | What this repository already has |
|---|---|
| Proactive delivery to a channel | `integrations/notifications/` — every proactive moment is already an event and a record; a channel is a new *sink*, not a new source |
| Inbound message → a conversation with the full capability set | `conversation.process` + the thread archive; a channel adapter is a transport, exactly as `ApiVoice`/`Text` are in the live rig |
| Approval before a state-changing call | the tier model (`tests/contracts/tool_tiers.json`, read by three suites) and the approval UI from M11 |
| A tool plugin interface | `llm/tools.py` registration + `authored_tools.py` + the MCP client |
| Skills with a manifest | the `SKILL.md` loader (M13) — the format is already the open Agent Skills one |
| A sandbox for downloaded code | `jarvis-sandbox` (`network_mode: none`, mount allowlist, per-task policy) and the Jarvis Code invariants |
| A secrets store | `!secret` + `secrets.yaml` — real, but read at config load; the addition needs call-time injection and trace redaction |
| Per-request model choice | `llm/openai_compat.py` already speaks the OpenAI shape, and `configuration.yaml` already documents LiteLLM as a supported backend |
| Tracing | M36 (Langfuse) — the new work says "logged to the trace", so M36 moves ahead of the items that depend on it |

### What is genuinely new, and what it costs

| Item | New surface | Cost / risk on this host |
|---|---|---|
| Channel adapters (Telegram, Signal) | `integrations/channels/` with an adapter interface, an identity allowlist, per-channel and global rate limits | Signal needs `signal-cli` as a container; both are tailnet-only, never public. Verified against a mock channel server — no accounts in CI |
| Calendar (CalDAV), mail (IMAP/SMTP) | two integrations + fixture containers (Radicale, a mail sink) | small; the fixtures are the work |
| LiteLLM gateway | one container, all model traffic through it, policy routing + fallback + caps | ~300 MB; the privacy guard is the hard part — a request tagged local-only must be *refused* cloud routing, not merely defaulted away from it |
| Claude Code backend | an alternative execution backend for coding tasks, in the same sandbox, same approval gates | needs an API key the operator supplies — **off by default**, and a `BLOCKERS.md` user-input row until then. The first deliberate exception to "no cloud", authorised by the brief |
| Delegation across backends | extends M20's subagents with backend selection and roll-up | concurrency already bounded by `llm.max_concurrent` |
| Prompt-injection quarantine | wrap/quarantine every external text before it reaches the model; strip ChatML/Llama/Gemma/Mistral control literals | cheap, and the highest-value item in the addition: it is what stops a fetched page forging a role boundary |
| Motion system | motion tokens in `design/tokens.json`, primitives, signature moments, a perf-trace gate | the tokens and the reduced-motion path are mechanical; "cool" is not machine-checkable, hence the recorded-video checkpoint |
| Capability registry + management UI + catalog | manifests with declared permissions, a registry, a real management surface, pinned installation from an allowlist of sources | the catalog is the marketplace attack surface: pinned refs, checksums, nothing auto-runs, static review before first run, sandboxed execution, quarantined metadata |

### The rules these milestones are written against

1. **Nothing is exposed to the public internet.** Tailnet or loopback, no static tokens in URLs.
2. **Unknown senders are ignored, not served** — and the fact that they were ignored is logged.
3. **External content is data, never instruction.** Quarantined, control-token-stripped, and
   incapable of triggering a state-changing tool without the approval gate — whatever it asks.
4. **Least privilege by default.** Each subagent, integration and downloaded skill gets the
   narrowest tool allowlist and credential scope that works. No ambient god-tool.
5. **Secrets are injected at call time** and never persisted into memory, notes, logs or traces.
6. **Local remains a complete configuration.** Cloud providers are off until the operator
   supplies keys, and a request carrying memory, notes or private-integration data is refused
   cloud routing even when one is configured.
7. **Nothing installs, or runs, without being seen.** Allowlisted source, pinned ref, recorded
   hash, permissions shown, approval given.

The live suite is where these stop being prose: a red-team scenario file probes injection via a
fetched page and via an inbound message, a cross-conversation leak, a non-allowlisted sender and
a malicious skill install. **The suite fails if any probe succeeds.**

## Delta — the console, finished (added 2026-08-25, mid-run)

The design system landed in M02 and the console was rebuilt on it page by page as the
capabilities arrived — which means the console is now a mixture: the pages built after the
decision are C2 (`docs/design/c2-reactor.html`, chosen in `docs/design/README.md`), the pages
that predate it are not, and nothing has ever failed a build for the difference.

Thirteen `+page.svelte` routes exist today, plus layouts, modals and panels that are views in
every sense that matters to a person looking at them. The addition's first instruction is the
important one: **walk the router and the component tree**, do not list them from memory —
`docs/UI_MIGRATION.md` is the checklist, and a milestone that cannot complete while a box is
unchecked is what makes it more than a document.

What makes this verifiable rather than a matter of taste:

* `scripts/verify/token_lint.py` already exists and already has a baseline per file — the
  migration is finished when the console's entries reach zero, which is a number, not an
  opinion.
* The four states (loading, empty, error, offline) are already the house rule for a screen
  (`.claude/rules/design-system.md`); this makes them a per-page assertion.
* Screenshots at three breakpoints are captured headlessly, exactly as `docs/design/shots`
  already does for the direction studies — no GUI, no device.
* The live suite gains a navigation pass over every inventoried route: it loads, it logs no
  console error, and its rendered palette matches the tokens.

M44's motion tokens are a dependency rather than a parallel: a page migrated before the motion
layer exists would be migrated twice.

## Hard constraints

| Constraint | Status | Evidence |
|---|---|---|
| 100 % local (llama-swap OpenAI-compatible, Whisper, Piper) | partial | Both `.env` files set `LLM_URL` to an off-host LiteLLM `/v1` proxy (OpenAI-style; where it routes is outside the repo) and no `OLLAMA_*`; Wyoming faster-whisper 3.5.0 (:10300), piper 2.3.1 (:10200), openWakeWord (:10400) are running. But `make smoke` probes Ollama `/api/tags` (`scripts/e2e-smoke.sh:35`), `configuration.yaml` polls Ollama `/api/ps`, and the orchestrator's `/delegate` fan-out calls Ollama-native `/api/chat` (`jarvis-orchestrator/app/fanout.py:51-103`). No cloud SDK or URL in code (§7.5); nothing yet refuses a public model host |
| One design-token source of truth | partial | §1: one web source, two hand copies, one stale XML palette, no generator |
| Headless verification on this host | feasible | No display; `Xvfb`/`xvfb-run` present; Chromium runtime libs present; Playwright Chromium installed by the harness; tkinter absent; `docker ps` → permission denied for `jarvisdev` (compose-based checks unusable; `docker compose config` works client-side) |
| No phone/device access | enforced | `scripts/verify/m00-harness.sh` fails if any verify script mentions `adb`, an emulator or `connectedAndroidTest`; `.github/workflows/e2e.yml`'s emulator job is CI-only and is not touched by this run |

## Host and toolchain (what the milestones can count on)

Debian 12 (Proxmox LXC), kernel 6.8.12-pve, 4 vCPU, 8 GB RAM + 8 GB swap, 42 GB free on one
volume, no GPU, no `sudo`, no Docker socket for `jarvisdev`. Python 3.11.2 (CI uses 3.12),
Node 22.23.2, npm 10.9.8, git 2.39.5; **no Java, no Android SDK, no Gradle**. `~/.local/bin`
holds `gh` 2.98.0 but is not on `PATH` until the next login (`.profile` adds it). The repo venv
(`.venv`: pytest 9.1.1, ruff 0.16.3, playwright-py 1.49.1, every requirements file) and
`jarvis-web/node_modules` were bootstrapped today; `python -m build`/`twine` are absent.
Listening: jarvis-core :8080 (8 entities), HUD :8199, orchestrator :8188, browser :8210,
mosquitto :1883, Wyoming :10300/:10200/:10400; closed: :11434 (Ollama), :8086 (InfluxDB),
:8888 (SearXNG), :2322 (photon).

## Delta — what the intelligence scorecard is, and is not (added 2026-08-25, M26)

`evals/intelligence/` measures six things through the real voice pipeline and writes
`.verify/live/scorecard.json`. Read the numbers with these limits in front of you:

- **It is twenty-seven prompts.** A smoke test for intelligence, not a benchmark. It is
  sensitive enough to catch a skill being read before every answer and a "don't wait for it"
  ground through inline; it says nothing about how the assistant handles the ten thousandth
  conversation.
- **It runs on the harness house**, which has three lights and three sensors. That makes the
  latency numbers optimistic against a real house — a smaller summary in the prompt — and the
  routing prompts easier, because there is less to confuse.
- **Two sections rest partly on a local judge.** `graceful_failure` and one `reasoning` case
  cannot be read off the house. Every verdict logs its reason; a suite that passes for silly
  reasons reads as silly, which is the only defence available.
- **A floor is not a target.** They sit below the measurement on purpose (a floor at the
  measurement fails on the next run for nothing), and `PROCESS.md`'s rule applies: re-measure,
  never edit to taste.
- **Run-to-run variance is real.** Four runs of the same eight routing prompts scored 6, 7, 7
  and 8. The floors are set knowing that, and `ISSUES.md` names the case that flickered rather
  than pretending it did not.

## Cross-cutting findings

- **Documentation drift.** Every test count in `README.md`, `docs/verification.md` and
  `docs/testing.md` is stale (see §9); `README.md:328,394,444` describe UI and paths that no
  longer exist; `android-app/README.md:437-446` records a failure that was since fixed.
- **Identity.** There are no users; per-user features key on `TokenInfo.id`.
- **Orchestrator.** In-memory jobs, no streaming, Ollama-native API, not registered as tasks,
  `load_persisted` has no caller — the weakest subsystem against the target.
- **Branch model.** `origin/HEAD` → `claude/jarvis-ai-assistant-nbqf1p`; push-triggered CI only
  fires on `main`/`dev`, so the harness on this host is the only gate for this work.
- **Deliberate oddities to leave alone.** Committed `android-app/ci-keystore.jks` with a public
  password (the updater needs a stable signature); orchestrator + sandbox behind `--profile
  agents`; the sandbox's `network_mode: none`; ruff's defect-only ruleset.
