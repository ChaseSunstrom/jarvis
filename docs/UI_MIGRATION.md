# UI_MIGRATION.md — the console on C2, and fewer places to be

The console is being rebuilt on **C · Reactor II** (`docs/design/c2-reactor.html`;
the decision is in `docs/design/README.md`). This file is the checklist that
work is done against, in two passes:

* **Pass 1 (M48, done):** the structure. Eleven destinations became four plus
  the HUD, every old path redirects, every page sits on the shared component
  library and the generated tokens, and `token_lint.py` is at zero. That pass
  moved the pages; it did **not** redraw them — its own commit says so.
* **Pass 2 (M49–M51, this file's §3 onwards):** the look. Every screenshot in
  `docs/ui-review/` still shows the pre-C2 aesthetic drawn with C2's token
  names: monospace body text, a technical grid and corner brackets on the
  ground, pill-shaped controls, glowing wordmarks, and on the HUD the old
  GLSL glass sphere where C2 puts a flat instrument. The tokens were adopted;
  the direction was not. This pass is the direction.

## 1. The navigation

The console has **five destinations plus the voice screen**: four decided under
M48 and kept, and the dashboard, a section of HOUSE until M62 made it the first
console tab (§4 says why). What changes in pass 2 is that the voice screen stops being a
different kind of place: in C2 (`c2-reactor.html`, `?view=chat`) the top bar is
the same on every screen and VOICE is its first tab, with the sliding accent
underline. Today the HUD paints its own chrome and reaches the console through
a floating CONSOLE pill, and the console reaches the HUD through the wordmark —
two screens that do not look like one product.

### The structure: six tabs in one bar, everywhere

| Tab | Path | What it is | Why it belongs in the bar |
|---|---|---|---|
| **VOICE** | `/` | Talking to Jarvis: the reactor, the exchange, the transcript, this turn's stages, the dock. | It is the product. C2 draws it under the same bar as everything else. |
| **DASHBOARDS** | `/dashboards` | The house at a glance: the graphs and readings somebody arranged. No sections; the path is the page. | It is what a person opens the console to look at, and as a section of HOUSE it sat two taps deep behind the device list (M62). |
| **HOUSE** | `/house` | The physical home: what is on, where it is, what it has been doing, the rules that run themselves. | One question — "what is my house doing" — that four tabs used to answer. |
| **WORK** | `/work` | What Jarvis is doing or has done: tasks, research runs, coding jobs. | A coding job IS a task. |
| **KNOWLEDGE** | `/knowledge` | What Jarvis knows: the notes it has written and what it remembers about you — drawn as one graph. | "What did you write down" and "what do you know" are the same question from two distances. |
| **SETTINGS** | `/settings` | Configuration and capability: the assistant, its tools, what is installed, the machines it runs on, pairing. | Everything here is opened rarely and deliberately. |

Six is the cap `scripts/verify/m48-webui-c2.sh` enforces (five from M48 to M61;
M62 spent the sixth on the dashboard, `DEVIATIONS.md` §20), and this uses all of
it. The bar is C2's: brand at the left (`JARVIS · v0.1 · local`), the tabs
centred with one sliding underline (`--jv-dur-base`), the status readout at the
right (link · model · stt · tts, or per destination: `2 running · 1 held`).

**Sections** inside a destination are a second, lighter strip under the page
title — C2's segmented control (`.seg`: hairline box, the active segment on
`--jv-surface-2`), not a second row of tabs. Moving between sections is a
shared-element transition; moving between tabs is the route transition.

**The phone** keeps its native strip of the five console front doors
(`ConsoleTab.kt`); VOICE on the phone is the native HUD, so `screens.ts` marks
`/` as `hud: true` and `console_parity_test.py` binds the phone's strip to the
`nav && !hud` screens. **The desktop app** loads the console build and gets
all of this by construction.

### Where each page lives (unchanged from M48)

| Old path | Lives at | How it appears |
|---|---|---|
| `/` | `/` | VOICE. The reactor is the centrepiece. |
| `/devices` | `/house/devices` | HOUSE's default section. |
| `/areas` | `/house/areas` | A section. |
| `/dashboards` | `/dashboards` | A destination of its own since M62 — the first console tab, on C2's dashboard cards. It was a section of HOUSE from M48 to M61; `/house/dashboards` redirects. |
| `/automations` | `/house/automations` | A section. |
| `/tasks` | `/work/tasks` | WORK's default section. |
| `/tasks/[id]` | `/work/tasks/[id]` | A detail view on C2's task layout: ring, plan, tool calls, output, approval. |
| `/code` | `/work/code` | A section. |
| `/notes` | `/knowledge/notes` | KNOWLEDGE's default section, beside the graph. |
| `/memory` | `/knowledge/memory` | A section, beside the graph. |
| `/settings` | `/settings/assistant` | SETTINGS' default section. |
| `/tools` | `/settings/tools` | A section: the catalogue first, open, above the folds (M65 — it was a button inside the Extensions fold, and it opened on nothing); then extensions, callables, the test runner, MCP servers, skills and exposure behind expanders. |
| `/desktop` | `/settings/console` | Two panels on the Console section (M54); `/settings/desktop` redirects there too. |
| `/styleguide` | `/styleguide` | Not in the nav. The library, every state, every token. |

Every row on the left is a 308 to the row on its right. Nothing 404s.

## 2. The bar every screen is measured against

Read off `docs/design/shots/c2-reactor-*.png` and `design/tokens.json`:

* **Ground.** Near-black `--jv-bg` with one radial lift at the bottom. Three
  faint field circles behind the reactor. **No grid, no corner brackets** —
  those are the previous direction and they go.
* **The reactor is an instrument, not a lamp.** A graduated bezel of 120
  ticks, 36 blades turning once in two minutes with a glint walking round,
  a dashed coil counter-rotating, a level arc in the accent that carries the
  live value (audio amplitude on VOICE, progress on a task, a figure on a
  dashboard), a dark lens with two iris arcs and one small hot dot. It never
  glows the screen; the glow budget is the core, the current step and the mic
  ring.
* **Panels are flat.** `--jv-panel` on a 1px `--jv-line-hair`, 6px radius,
  uppercase Barlow header with a mono figure at the right. No glass, no blur,
  no gradients as decoration, no pills — the only round things are status
  dots and the mic ring.
* **Type has three jobs.** Barlow for interface text and labels (uppercase +
  tracking for chrome), Space Grotesk 300 for the one line to read first — the
  reply, the big figure, the screen title — and JetBrains Mono only for data:
  timestamps, ids, ms, tool names. Body text is never mono.
* **The accent is spent, not spread.** `--jv-accent` on the current step, the
  live value, the active tab's underline and the one primary control per
  screen (`APPROVE`, `+ WIDGET`). Everything else is `--jv-text-dim` on
  hairlines. Amber is held, red is broken, green is done.
* **Density from information.** Hairlines over boxes, tabular numerals,
  generous gutters (`--jv-space-6/7`), one primary action per surface, rare
  controls behind expanders.
* **Motion says what changed.** Panels rise in with a 55ms stagger; a running
  dot pulses; the current step breathes; charts draw in and figures count up;
  the tab underline slides. Nothing moves for decoration; nothing moves under
  reduced motion.

## 3. The inventory — pass 2

One row per page, view and modal, found by walking `src/routes` and
`src/lib` rather than remembered. Status is what the current screenshot shows:
**old** — pre-C2 styling or a pre-C2 component; **partial** — on the tokens and
the library, not on the look; **migrated** — C2, with its screenshot in
`docs/ui-review/<page>/`. **The pass is not done while a box is unchecked.**

Each row is done when: it is drawn to §2 on the tokens and the shared library
(no ad-hoc colour, spacing or one-off component), `token_lint.py` is clean for
it, loading / empty / error / offline are real states, it holds at 390 / 834 /
1440, its motion is the M44 primitives, and its screenshot at all three widths
is in `docs/ui-review/`. A visual-consistency check (`web_look_check.mjs`,
M50) asserts the rendered result: body text in Barlow, no `.jv-grid` or
`.jv-bracket` in the DOM, the tab underline present, pill radii only on the
components allowed to have them.

### M49 — the signature surface

- [x] `/` VOICE · **migrated** · GLSL `Orb.svelte` sphere, grid, brackets, tagline, pill PTT, mono readout → C2 chat view: `Reactor` (instrument) centred with the level arc on real audio amplitude; idle → listening → thinking → speaking as distinct states on the `--jv-rx-*` clock; caption line (`listening · hands-free · kitchen`); the exchange under it (Barlow question, Space Grotesk reply with caret, tool-call line); transcript panel left; this-turn panel right (stages bar, wake/transcribe/first-token/speak ms, tool calls); the dock (mic ring, "Say or type", VOICE | CHAT, key hints). Loading = boot; empty = "Say something"; error = the turn's; offline = the link's.
- [x] `Reactor` (`lib/ui`) · **migrated** · C2 geometry, but state only tints the rim; needs per-state palette from `color.orb.*`, an amplitude-driven level, thinking's dashed inner ring, speaking's cadence, error, and a `figure` slot for the task ring and dashboard hero.
- [x] `Orb.svelte`, `orb-shader.spec.ts` · deleted once nothing references them; `design/build.py --check` and `reactor_orb_test.py` re-pointed at the instrument's geometry table (`tests/contracts/reactor_geometry.json`).
- [x] `BootSequence` · **migrated** · re-staged on the instrument: bezel → blades → coil → level → core, subsystems named as they come up.
- [x] `ModeToggle` · deleted → the dock's VOICE | CHAT underline pair.
- [x] `ChatPanel` / `ChatMessage` · **migrated** · pills and mono body → the chat mode as the same C2 view with the transcript expanded and the exchange as a list.

### M50 — the console

Chrome, drawn on every destination:

- [x] Layout shell: header, tabs, status readout, skip link · **migrated** (M49) · the C2 top bar with the sliding underline; VOICE in the bar; the console link on the HUD gone. The MOMENTS pill is `Notifications`, below.
- [x] `SectionStrip` (was `DestinationNav`, deleted) · **migrated** · C2's segmented control, in the four destination layouts with a `ScreenTitle`.
- [x] `CommandPalette` · **migrated** · Barlow labels, mono only for the kind and the keys, the selected row washed with the inset rule.
- [x] `Approvals` · **migrated** · C2's held bar: inset warn rule, the tool bright, args mono, APPROVE the one primary.
- [x] `Notifications` / `Moment` · **migrated** · a flat panel whose head is the disclosure; each moment a hairline row with the kind as a tag.
- [x] `TaskDock` / `TaskBar` / `TaskCard` · **migrated** · flat panels, the bar a thin accent rule, steps behind a disclosure, quiet CANCEL/FORGET.
- [x] `Toasts` · **migrated** · flat panel, inset rule by kind, Barlow.
- [x] `ToolActivity` · **migrated** · CallLine-shaped rows on a flat panel with a real progress rule (its own rows, because the spec reads the error text by testid).
- [x] `+error.svelte` · **migrated** · `ErrorState` and two `Button`s, nothing else.

Destinations and sections:

- [x] HOUSE shell · **migrated** · `ScreenTitle` + `SectionStrip`.
- [x] HOUSE · Devices · **migrated** · a panel per area, hairline rows, one control lit, MANAGE a quiet disclosure, the editor inset.
- [x] HOUSE · Areas · **migrated** · rooms as panels, CREATE the one primary.
- [x] HOUSE · Dashboards · **migrated** · C2's dashboard: the range as a segmented control, `+ WIDGET` the primary, flat cards with mono sources, `Figure` count-ups, gradient fills that draw in, bars that grow, the first widget the hero with a mini `Reactor`.
- [x] HOUSE · Automations · **migrated** · rows with mono last-trigger, pressed ENABLE/DISABLE, quiet RUN NOW, the editor inset.
- [x] WORK shell · **migrated** · `ScreenTitle` + `SectionStrip`.
- [x] WORK · Tasks · **migrated** · a `DayStrip` of today's tasks and the next firings over RUNNING / FINISHED, scheduled jobs as a panel.
- [x] WORK · Task detail · **migrated** · C2's task view: `ProgressRing` centre, PLAN and TOOL CALLS left, OUTPUT right, the rest in a two-column grid, the held bar with the warn rule, CANCEL / BACK quiet.
- [x] WORK · Code · **migrated** · panels, START the one primary, diffs sunken.
- [x] KNOWLEDGE shell · **migrated** · the graph is the hero: notes and memory as nodes, links and shared tags as edges, seeded force layout drawn in with the stagger; a node lights for one blink when a turn's `memory_used` names it or a note tool touches it; the URL is the selection.
- [x] KNOWLEDGE · Notes · **migrated** · hairline rows, the editor a flat panel with the markdown sunken, SAVE the one primary; `?open=<id>` ↔ the lit node.
- [x] KNOWLEDGE · Memory · **migrated** · hairline rows, quiet PIN/FORGET, FORGET EVERYTHING behind a confirm; `?entry=<id>` scrolls to and marks the picked node.
- [x] SETTINGS shell · **migrated** · `ScreenTitle` + `SectionStrip`.
- [x] SETTINGS · Assistant · **migrated** · a flat panel per settings group, hairline rows, SAVE primary only when dirty, restart as a warn tag.
- [x] SETTINGS · Tools · **migrated** · six expanders (Extensions, Callables and Test run open; MCP, Skills, Exposure closed) with counts, NEW SKILL the one primary, exposure as a `Toggle`.
- [x] SETTINGS · Extensions and catalog (`Extensions` + its three dialogs) · **migrated** · headerless rows inside the expander, tags for state, dialogs on `Dialog` with one primary each.
- [x] SETTINGS · Desktop · **migrated** · hairline device rows, `Pill` tones for online/offline.
- [x] SETTINGS · Pairing (`Pairing`) and `EnrolVoice` · **migrated** · two panels each, the QR on `--jv-paper`, one primary at a time.
- [x] `/styleguide` · **migrated** · every export live — the reactor's five states, the bar, the strip, the stages, the calls, the day strip, the ring, the figure, the graph — and no console furniture.

Views that are not routes:

- [x] `EntityRow` · **migrated** · hairline row, state tag, ghost buttons with `pressed` for on; the slider labels stay mono (they are data).
- [x] `TaskTimeline` / `TaskOutput` · **migrated** · dot marks and Barlow text; the output sunken with a live caret.
- [x] `CodeDiff` · **migrated** · sunken, mono, on the tokens.
- [x] `ScheduledJobs` · **migrated** · a panel of hairline rows, `+ SCHEDULE SOMETHING` the primary.
- [x] `SkillsPanel` · **migrated** · a headerless body inside its expander.
- [x] `McpServers` · **migrated** · a headerless body inside its expander, `Field`/`Input` editors.
- [x] `Chart` (dashboards) · **migrated** · gradient under the line, stroke draw-in on `--jv-dur-sweep`, bars that grow with a stagger, a tick on the last point, `Figure` for a stat.

The library (`src/lib/ui`), re-skinned once so the pages inherit:

- [x] `Button` (ghost · primary · danger · approve · pressed) · **migrated** · C2's `.btn`: uppercase Barlow on a hairline, `md` radius, the primary the one filled control, `pressed` an accent outline.
- [x] `Pill` · **migrated** · a hairline tag (radius `sm`, Barlow uppercase); status dots stay round.
- [x] `Tabs` · **migrated** · uppercase Barlow on a hairline with the accent underline.
- [x] `Toggle`, `Input`, `Select`, `Field`, `Toolbar`, `Panel`, `Row`, `Dialog`, `IconButton` · **migrated** · Barlow, `--jv-field` grounds, `md` radii, hairlines; the toggle's track is the one round control.
- [x] `SkeletonRows`, `EmptyState`, `ErrorState`, `OfflineState`, `ScreenState` · **migrated** · the four states on flat panels in the body face; mono only for an error's detail.
- [x] New: `TopBar`, `SectionStrip`, `ScreenTitle`, `StatusReadout`, `StagesBar`, `CallLine`, `DayStrip`, `ProgressRing` (a `Reactor` preset), `Graph` (knowledge, with `$lib/knowledge/graph`'s seeded layout), `Figure` (count-up) — each with a `@component` block, a README section, SSR/unit tests, and a style-guide entry.
- [x] `chrome.css` · **migrated** · the grid, the brackets, the skeleton classes and every `.console .thing` deleted; what remains is the frame, the motion primitives, the toasts and the palette.

### M52 / M53 — the voice tab, alive, and moving for what it does

- [x] `/` VOICE · **extended** · the knowledge graph (`Graph`, 300px) under the transcript, lighting when a turn reads a fact or a note tool touches a note; the activity strip (`Activity`, `$lib/activity`) under the turn — tool calls, task steps, sensor readings, camera looks, memory, moments, approvals, newest first, twelve rows; the caption says *looking · Kitchen* while a camera is looked at.
- [x] `Reactor` · **extended** · `work` sweeps the blades once per tool call; speaking runs them in cadence on `--jv-rx-speak`; `looking` irises the lens; a failed call flashes the rim to the error palette for one blink. `Approvals` pulses its warn rule while held. All still under reduced motion; each choreography measured (`motion.spec.ts`), recorded (`docs/motion-review/5-at-work.webm`).

### M51 — the phone, on the same look

- [x] HUD (`MainActivity`, `JarvisOrbView` / `ReactorOrb.kt` / `SiriOrbView`) · **old** · GLSL sphere, pill PAIR / SETTINGS → the instrument drawn on Canvas from the same geometry table, the same four states from `color.orb.*`, the same dock. — **done (M51)**: `ReactorOrb.kt` draws bezel, blades, coil, level, lens and dot from the same geometry contract as the web; both orb views use it; the sphere shader, its specular and the brackets are gone; three hairline field circles behind.
- [x] Console frame strip (`ConsoleFrame.kt`) · **partial** → C2 tabs with the underline. — **done (M51)**: `tab()` with the accent underline under the current tab.
- [x] Approval (`ApprovalActivity`, `ApprovalBridge` banner) · **partial** → the C2 approval bar. — **done (M51)**: the held bar — gold tier label, body-face prose, mono action id, filled APPROVE, quiet DENY; brackets removed.
- [x] Task overlay (`ToolActivityView`, task frames) · **partial** — **done (M51)**: draws through the rewritten `JarvisUi` primitives (no pill, ghost or bracket survives in `ToolActivityView`); `task-overlay` golden re-recorded.
- [x] PHONE settings (`SettingsActivity`), `SystemCheckActivity`, `VoiceIdentityActivity`, `PermissionRequestActivity`, `CompanionAskActivity`, `CrashLogActivity` · **partial** · `JarvisUi` pills and brackets → hairline panels, Barlow, one primary. — **done (M51)**: one primary per screen (SAVE, RECORD, SEND, MANAGE); `PermissionRequestActivity` carried no old furniture and needed no change.
- [x] Boot (`JarvisBootAnimation`, `BootTimeline`) · **partial** → the staged instrument. — **done (M51)**: the staged instrument, sans wordmark without blur.
- [x] `JarvisUi.kt` `pill()` / `ghost()` / `CornerBrackets` · **old** → replaced; Roborazzi goldens re-rendered; `docs/ANDROID_DEVICE_TESTS.md` gains what only a device can confirm. — **done (M51)**: `button()`, `primary()`, `tab()`, hairline panels; ten Roborazzi goldens recorded and verified; ADT-031…035.
- [x] Desktop app (`jarvis-desktop-app`) · loads the console build — nothing to restyle beyond the tray icon; verified by its existing Playwright `_electron` run. — **done (M51)**: nothing to restyle beyond what the console build carries; its Playwright run is unchanged.

### M54 — settings that make sense, and the real models

The operator's words: *"clean up the other menus to make them more simple,
especially settings, and the models isn't the actual AI models, which makes
it hard to understand."* SETTINGS was a generic rows renderer — every editable
key in the server's groups, key and all, on one page with pairing, voice
identity, the desktop and an event log under it — and "Model" was a dropdown
of the gateway's aliases (`house`, `house-fast`), which are names Jarvis uses
and not the models. This pass cuts SETTINGS to five sections a person can
name, features the rows somebody comes for in plain words with one line saying
why, keeps every other row behind an EVERYTHING fold exactly as the server
describes it, and puts a MODELS panel at the top that lists what the model
servers actually serve. What moved where, ticked as it was done
(`bash scripts/verify/m54-settings-models.sh`; `e2e/settings.spec.ts` walks every setting the
server sends to its section):

- [x] SETTINGS shell · **five sections** — Assistant · Voice · House · Console · Tools — in `screens.ts`, in that order; `Screen.label` for the strip's word where the unique `name` is longer (`Voice settings` → VOICE, `House settings` → HOUSE — the HUD is `Voice` and the destination is `House`, and every per-screen spec titles its tests by name); `g v` reaches Voice, `g e` (was Desktop) lands on Console; `/settings/desktop` and `/desktop` both 308 to `/settings/console`; the palette indexes the three new sections.
- [x] SETTINGS › Assistant · **MODELS panel** (`components/Models.svelte`, backed by `jarvis/llm/models`): one hairline row per served model — name, `family · size · quant` in mono, the role as a tag, a lit dot when loaded, "used for …" in words, "as named by the server" when the size was read off the id, "not served" when a configured name is listed by nobody — and a choice per role (chat · fast · vision) that writes `llm.model` / `llm.fast_model` / `vision.model` through the settings API. Loading, empty ("The model server lists nothing"), error (with the reason and a Retry) and offline (the last list, under the banner) are all real states.
- [x] SETTINGS › Assistant · **plain rows**: Temperature, Name, Language — label, one-line why, control, SAVE lit only when dirty, RESET when overridden (`components/SettingPlain.svelte`, the plan in `sections/settingsPlan.ts`).
- [x] SETTINGS › Assistant · **EVERYTHING** (`components/SettingsFold.svelte` + `SettingRaw.svelte`): the server's Assistant group as it was — key, source tag, note, SAVE, RESET — closed by default. A group the console has never heard of lands here too, so it is still reachable.
- [x] SETTINGS › Voice · **new section** (`sections/SettingsVoice.svelte`): plain rows Wake word, Voice, Speech language; the **Whose voice** panel and browser enrolment (`EnrolVoice`) moved here from the foot of Assistant; EVERYTHING holds the Voice group.
- [x] SETTINGS › House · **new section** (`sections/SettingsHouse.svelte`): plain rows Time zone, Units, and a **Rooms** row that links to HOUSE › Areas rather than growing a second editor; EVERYTHING holds the House group (name, language, currency, country, coordinates, elevation, log level).
- [x] SETTINGS › Console · **new section** (`sections/SettingsConsole.svelte`): Text size, This console (the web server's own environment), Pair a phone + What can reach this house (`Pairing`), This window + Paired computers (from the Desktop page), and the **Event stream** as a closed fold — none of them house settings, all of them moved off Assistant.
- [x] SETTINGS › Desktop · **deleted** (`sections/Desktop.svelte`, `routes/settings/desktop/+page.svelte`); its two panels are on Console; the route file is a redirect; `docs/ui-review/settings-desktop/` removed, `settings-voice/` `settings-house/` `settings-console/` added at three widths.
- [x] SETTINGS › Tools · **unchanged** by this pass (M55 makes it one searchable list).
- [x] The connection boilerplate every section re-typed → `sectionLink.svelte.ts` (dial generation, disposed flag, RECONNECT); the settings working copy → `settingsStore.svelte.ts` (drafts, save, reset, restart-needed), one per section, shared by its plain and raw rows through `SettingControl.svelte`.
- [x] Core · `jarvis/llm/models` and `GET /api/llm/models` (`jarvis/llm/catalogue.py`): LiteLLM `/model/info` → llama-swap `/v1/models` + `/running` → the backend's own `/v1/models` for models that are UP only (never through `/upstream/<id>/…` for one that is not, which would load it); Ollama `/api/tags` + `/api/ps`; TEI `/info` for the embedder and the reranker. `llm.fast_model` (empty = the chat model; held on the agent, read by nothing until M60 and the note says so) and `vision.model` (live, onto the analyser) join the allowlist; `llm.model`'s note stops calling it "the Ollama model".
- [x] Mock · `jarvis/llm/models` and `/api/llm/models` with a 27-B chat behind `house`, a 4-B fast behind `house-fast`, a vision model, the embedder, the reranker; `jarvis/test/models_mode` (ok · empty · error); the settings rows the new sections feature (`llm.fast_model`, `vision.model`, `voice.wake_word`, `voice.language`, `jarvis.unit_system`, `jarvis.language`, `jarvis.log_level`); `llm.model` is `house` with the aliases as choices, the way the deployed stack has it.

### M55 — simpler menus everywhere

The operator's words again: *"clean up the other menus to make them more
simple"*. M54 did SETTINGS. This pass holds HOUSE, WORK, KNOWLEDGE and the
tools page to the four rules of §4 in a way a test can ask: the **menu
inventory** below is read by `e2e/menus.spec.ts`, which opens every screen
against the mock backend and checks it row by row. What was cut, ticked as it
was done (`bash scripts/verify/m55-menus.sh`):

- [x] SETTINGS › Tools · **one search over everything**: a single box at the top (`data-jv-filter`) filters the extensions, the callables, the MCP servers, the skills and the entity exposure at once; each fold's header says how many of its rows match; the callables' and the exposure's own filter boxes are gone. `Extensions`, `McpServers` and `SkillsPanel` take the page's `query`.
- [x] SETTINGS › Tools · **tool rows**: USE and EDIT at rest; DELETE lives inside the editor a row opens, beside SAVE and CANCEL, so a row is not three buttons wide.
- [x] HOUSE › Automations · **rows**: the enable/disable switch and one MORE (`data-jv-more`) at rest; Run now, Edit and Delete are inside it. The editor's SAVE stays the screen's primary.
- [x] HOUSE › Dashboards · **one way into the layout editor**: `+ Widget` (the primary) opens it; `Edit layout` is gone from rest and the button reads DONE while editing. `dashboards.spec.ts` enters through `+ Widget`.
- [x] HOUSE › Areas · **rows**: the area is its own expander at rest; Rename and Delete are inside it, Delete last and red.
- [x] Every list row on the four destinations carries `data-jv-row`, which is what the per-row cap is measured on; the settings rows (`SettingPlain`, `SettingRaw`) carry it too so their SAVE/RESET pairs are rows, not duplicates.
- [x] `e2e/menus.spec.ts` reads the inventory and, for every screen: at most one primary control at rest; no two visible controls outside rows with the same name; no row over its cap; exactly the declared number of search boxes; on the tools page the one search empties every fold on a nonsense query and finds `get_state` by name.
- [x] HOUSE › Devices · **one control where one will do** on the entity rows (`EntityRow`): a cover offers OPEN or CLOSE (the move it can make from where it is) and STOP; a player PLAY or PAUSE beside previous and next; a lock LOCK or UNLOCK; a vacuum START or DOCK. The test ids follow the state (`open-…` while closed, `close-…` while open), so a spec presses what a person sees.
- [x] WORK › Tasks · a running card offers Cancel, a finished one Forget — not both on every card; the two filter boxes (Devices, Tasks) are marked through `Input`'s `filter` prop.
- [x] SETTINGS › Console · a paired phone or computer is a row (`token-…`, `paired-…`) so its REVOKE is the row's control, not a duplicate on the page.
- [x] `scripts/verify/m55-menus.sh` builds the console before any spec (the e2e server serves the build; a spec against a stale bundle measures the last change).

### M63 — the dashboard shows the house

The operator's "full functionality". A widget has a kind; the House is what the console opens
on; each kind's empty state says how the thing gets there. What changed on the screen:

- [x] DASHBOARDS · the `+ Widget` editor asks **what to show** first (`new-kind`: Graph, Entity, Readings, Camera, Sky, Moments, each with one sentence on when to use it) and then only the fields that kind needs — a graph's chart, source and series; a tile's entity id (`new-entity`, with a few of this house's ids as the hint); a camera's name (`new-camera`, blank for the only one); a room (`new-area`); how many moments (`new-limit`). A name that is not an entity id is refused with a sentence, not saved as a tile about nothing.
- [x] DASHBOARDS · an entity tile (`EntityTile`) is the **one control at rest** on the screen: TURN ON / TURN OFF, or LOCK / UNLOCK — the move the entity can make from where it is, exactly as its Devices row offers it, calling the same `call_service` and changing only on `state_changed`. The inventory row's per-row cap moves from 0 to 1 for it; the readings, a still, the sky and the moments carry no control, so a moment on the dashboard is not a link (the inbox on VOICE is where it is read and dismissed).
- [x] DASHBOARDS · the hero (the first widget in reading order) spends the accent on its live value — a tile's state, the sky's rise time, the newest reading, the newest moment's title — and nothing else on the page is cyan but that and a lit switch.
- [x] DASHBOARDS · a camera widget is a look. It shows the frame the vision integration handed back, or the refusal in the camera's own terms (`consent: never`, the rate limit, nobody answered) with a pointer to `vision.audit`; a house with no camera reads "No camera is configured. Add one under vision: cameras: …". No frame is ever kept on the page.
- [x] DASHBOARDS · the sky says "Not fetched yet — <the integration's reason>" before the elements or the ephemeris are downloaded, never a guessed time; every pass carries the age of the elements it was computed from.
- [x] DASHBOARDS · the shipped **House** opens first (its `order: 0` sorts it ahead of Homelab); its tile is the sun, which every install has, and its camera is unnamed, which is the only camera when there is one — a default that invents a light nobody owns is worse than one that controls nothing.
- [x] The mock backend serves the three commands, a House with one widget of every non-graph kind, a light that is on, a camera that refuses and one that answers, three readings in three rooms, and the sky; `e2e/dashboards.spec.ts` drives the tile's round trip, the refusal, a live moment, a live reading and the kind picker against it.

### The menu inventory

One row per leaf screen. **Rows are** is the `data-testid` prefix of the
list rows the screen draws (all carry `data-jv-row`); **per row at rest**
is the most controls a collapsed row may show (— when the screen has no
rows); **primary** is the one filled control on the screen, by `data-testid`
(— when a screen has none, and the voice tab's push-to-talk ring, which is
not a `<Button>`); **search** is how many search/filter boxes the screen
shows. `e2e/menus.spec.ts` reads this table; a screen that grows a second
primary, a fourth row control or a second search box fails it.

| Screen | Route | Rows are | Per row at rest | Primary | Search | Notes |
|---|---|---|---|---|---|---|
| VOICE | `/` | — | — | — | 0 | the ring is the control; the strip's rows are not controls |
| HOUSE › Devices | `/house/devices` | `device-` | 4 | — | 1 | the entity's own control and Edit — a switch; open/close and stop for a cover; lock/unlock as one; previous, play/pause and next for a player, which is the row that sets the cap; the editor's Save appears only open |
| HOUSE › Areas | `/house/areas` | `area-` | 1 | `create-area` | 0 | the row is its expander; Rename and Delete inside |
| DASHBOARDS | `/dashboards` | `widget-` | 1 | — | 0 | on an owned dashboard `+ Widget` (`dashboard-add`) is the one primary and the one way into the layout editor; a shipped one has none; widgets show their move and remove controls only while editing. The one control at rest is an entity tile's switch (M63): TURN ON / TURN OFF, or LOCK / UNLOCK — the move the entity can make from where it is, as its Devices row offers it; a graph, the readings, a still, the sky and the moments carry none |
| HOUSE › Automations | `/house/automations` | `automation-` | 2 | — | 0 | the switch and MORE; Save is primary only inside an open editor |
| WORK › Tasks | `/work/tasks` | `task-` | 3 | — | 1 | the task's title (a link to its page), the steps fold, and Cancel (running) or Forget (finished); Clear finished is the page's one action |
| WORK › Code | `/work/code` | `job-` | 3 | — | 0 | the steps fold, Cancel and the job's opener; Start is primary only inside the open form |
| KNOWLEDGE › Notes | `/knowledge/notes` | `note-` | 2 | — | 1 | the note itself and Delete; the editor's Save is primary only with a note open |
| KNOWLEDGE › Memory | `/knowledge/memory` | `memory-` | 2 | — | 1 | Pin and Forget are both what a memory is for |
| SETTINGS › Assistant | `/settings/assistant` | `plain-` | 3 | — | 0 | the control, SAVE and RESET per plain row |
| SETTINGS › Voice | `/settings/voice` | `plain-` | 3 | — | 0 | as Assistant; on the voice identity panel one row per enrolled person (`person-`) with its REMOVE, and ENROL, TEST and FORGET (everyone) outside them (M71) |
| SETTINGS › House | `/settings/house` | `plain-` | 3 | — | 0 | as Assistant; Rooms is a link to HOUSE › Areas |
| SETTINGS › Console | `/settings/console` | `token-` | 1 | — | 0 | a paired phone or computer is a row with REVOKE; text size is a segmented row; the event stream is a fold; its settings rows are behind EVERYTHING |
| SETTINGS › Tools | `/settings/tools` | `tool-` | 2 | — | 1 | USE and EDIT; the catalogue above the folds (M65) is a row per entry with one control at rest — INSTALL, or an INSTALLED tag that is not one — and one ADD BY URL on its MCP line; NEW SKILL in the Extensions fold stays the one lit control, because the shipped entries are installed already and writing a skill is what the page is for; the one search filters the catalogue and every fold |

## 4. What "clean" means here

Not a mood. Four things a check can ask about:

* **One primary action per surface.** If a screen has two things competing to
  be pressed, one of them is a section of its own or belongs behind an
  expander.
* **Progressive disclosure.** Advanced and rare controls are behind an
  expander, not crowding the main view. The extension row is the pattern:
  name, state and one switch on the row; the tool list, the permission scope
  and the provenance one click in.
* **Space over density.** Where the two conflict, spacing wins.
* **Understandable without explanation.** A first-time user should know what a
  screen is for from the screen. That is the one a test cannot check, and it is
  what the `docs/ui-review/` screenshots are for.

## 5. Motion, in service of the structure

The M44 tokens and primitives, applied so that motion answers "where did that
go" rather than decorating: sections of one destination are a shared-element
transition; destinations are the route transition; the reactor's clock is
`--jv-rx-*`; the graph's activity is `--jv-dur-pulse` in and `--jv-dur-slow`
out; everything stays inside the M44 frame budget and the reduced-motion path.

## 6. What stayed a raw `<button>`, and why

Eighty-four controls became `<Button>` under M48. What is left is not a button
in this library's sense: `ChatPanel`'s mic, send, chip and drawer;
`TaskCard`'s disclosure and act rows; `EntityRow`'s manage affordance;
`Extensions`' expander; one bespoke control each in `SkillsPanel`, `Moment`,
`Notifications`, `Toasts`, `ModeToggle`. Pass 2 re-skins each in place (or,
for `ModeToggle`, replaces it) and keeps the list here current.
