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

The console has **four destinations plus the voice screen**, decided under M48
and kept. What changes in pass 2 is that the voice screen stops being a
different kind of place: in C2 (`c2-reactor.html`, `?view=chat`) the top bar is
the same on every screen and VOICE is its first tab, with the sliding accent
underline. Today the HUD paints its own chrome and reaches the console through
a floating CONSOLE pill, and the console reaches the HUD through the wordmark —
two screens that do not look like one product.

### The structure: five tabs in one bar, everywhere

| Tab | Path | What it is | Why it belongs in the bar |
|---|---|---|---|
| **VOICE** | `/` | Talking to Jarvis: the reactor, the exchange, the transcript, this turn's stages, the dock. | It is the product. C2 draws it under the same bar as everything else. |
| **HOUSE** | `/house` | The physical home: what is on, where it is, what it has been doing, the rules that run themselves. | One question — "what is my house doing" — that four tabs used to answer. |
| **WORK** | `/work` | What Jarvis is doing or has done: tasks, research runs, coding jobs. | A coding job IS a task. |
| **KNOWLEDGE** | `/knowledge` | What Jarvis knows: the notes it has written and what it remembers about you — drawn as one graph. | "What did you write down" and "what do you know" are the same question from two distances. |
| **SETTINGS** | `/settings` | Configuration and capability: the assistant, its tools, what is installed, the machines it runs on, pairing. | Everything here is opened rarely and deliberately. |

Five is the cap `scripts/verify/m48-webui-c2.sh` enforces, and this uses all of
it. The bar is C2's: brand at the left (`JARVIS · v0.1 · local`), the tabs
centred with one sliding underline (`--jv-dur-base`), the status readout at the
right (link · model · stt · tts, or per destination: `2 running · 1 held`).

**Sections** inside a destination are a second, lighter strip under the page
title — C2's segmented control (`.seg`: hairline box, the active segment on
`--jv-surface-2`), not a second row of tabs. Moving between sections is a
shared-element transition; moving between tabs is the route transition.

**The phone** keeps its native strip of the four console front doors
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
| `/dashboards` | `/house/dashboards` | A section, on C2's dashboard cards. |
| `/automations` | `/house/automations` | A section. |
| `/tasks` | `/work/tasks` | WORK's default section. |
| `/tasks/[id]` | `/work/tasks/[id]` | A detail view on C2's task layout: ring, plan, tool calls, output, approval. |
| `/code` | `/work/code` | A section. |
| `/notes` | `/knowledge/notes` | KNOWLEDGE's default section, beside the graph. |
| `/memory` | `/knowledge/memory` | A section, beside the graph. |
| `/settings` | `/settings/assistant` | SETTINGS' default section. |
| `/tools` | `/settings/tools` | A section: callables, exposure, extensions, catalog — behind expanders. |
| `/desktop` | `/settings/desktop` | A section. |
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

- [ ] `/` VOICE · **old** · GLSL `Orb.svelte` sphere, grid, brackets, tagline, pill PTT, mono readout → C2 chat view: `Reactor` (instrument) centred with the level arc on real audio amplitude; idle → listening → thinking → speaking as distinct states on the `--jv-rx-*` clock; caption line (`listening · hands-free · kitchen`); the exchange under it (Barlow question, Space Grotesk reply with caret, tool-call line); transcript panel left; this-turn panel right (stages bar, wake/transcribe/first-token/speak ms, tool calls); the dock (mic ring, "Say or type", VOICE | CHAT, key hints). Loading = boot; empty = "Say something"; error = the turn's; offline = the link's.
- [ ] `Reactor` (`lib/ui`) · **partial** · C2 geometry, but state only tints the rim; needs per-state palette from `color.orb.*`, an amplitude-driven level, thinking's dashed inner ring, speaking's cadence, error, and a `figure` slot for the task ring and dashboard hero.
- [ ] `Orb.svelte`, `orb-shader.spec.ts` · **old** → deleted once nothing references them; `design/build.py --check` and `reactor_orb_test.py` re-pointed at the instrument's geometry table (`tests/contracts/reactor_geometry.json`).
- [ ] `BootSequence` · **partial** · re-staged on the instrument: bezel → blades → coil → level → core, subsystems named as they come up.
- [ ] `ModeToggle` · **old** (pill) → the dock's VOICE | CHAT underline pair.
- [ ] `ChatPanel` / `ChatMessage` · **partial** · pills and mono body → the chat mode as the same C2 view with the transcript expanded and the exchange as a list.

### M50 — the console

Chrome, drawn on every destination:

- [ ] Layout shell: header, tabs, status readout, skip link · **partial** · pill tabs and a glowing wordmark → C2 top bar with the sliding underline; VOICE joins the bar; the console link on the HUD and the MOMENTS pill go.
- [ ] `DestinationNav` (section strip) · **partial** → C2 segmented control.
- [ ] `CommandPalette` · **partial**
- [ ] `Approvals` · **partial** → C2 approval bar (amber inset rule, `Held · tier 3`, APPROVE primary).
- [ ] `Notifications` / `Moment` · **partial**
- [ ] `TaskDock` / `TaskBar` / `TaskCard` · **partial**
- [ ] `Toasts` · **partial**
- [ ] `ToolActivity` · **partial** → the `.calls` line: dot, mono name, args, `ok`, ms.
- [ ] `+error.svelte` · **partial**

Destinations and sections:

- [ ] HOUSE shell · **partial**
- [ ] HOUSE · Devices · **partial** · pill controls, mono names → hairline rows, Barlow names, one control per row lit.
- [ ] HOUSE · Areas · **partial**
- [ ] HOUSE · Dashboards · **partial** → C2 dashboard: title in Space Grotesk, range segmented control, `+ WIDGET` primary, cards with count-up figures, area charts with gradient fills that draw in, bars, sparkline tables, a hero with the mini reactor.
- [ ] HOUSE · Automations · **partial**
- [ ] WORK shell · **partial**
- [ ] WORK · Tasks · **partial** → the day strip (C2 `.strip`) over the list.
- [ ] WORK · Task detail · **partial** → C2 task view: progress ring (blades as plan steps), plan panel with the current step washed, tool calls live, output pane, approval bar, CANCEL / DIFF.
- [ ] WORK · Code · **partial**
- [ ] KNOWLEDGE shell · **partial** → the graph is the hero: notes and memory entries as nodes, `[[links]]` and back-links as edges, force-laid, drawn in with the stagger; a node lights and its edges pulse when a turn's `memory_used` names it or a note tool touches it (`jarvis_tool_*` events), and settles again on `--jv-dur-slow`.
- [ ] KNOWLEDGE · Notes · **partial** → list + editor beside the graph, selected node ↔ open note.
- [ ] KNOWLEDGE · Memory · **partial** → entries as the graph's other node kind; edit / pin / forget in a side panel.
- [ ] SETTINGS shell · **partial**
- [ ] SETTINGS · Assistant · **partial**
- [ ] SETTINGS · Tools · **partial** · seven panels stacked, `EXPOSED` pills on every row → sections behind expanders (Callables · Exposure · Extensions · MCP · Skills · Catalog), one primary action.
- [ ] SETTINGS · Extensions and catalog (`Extensions` + its three dialogs) · **partial**
- [ ] SETTINGS · Desktop · **partial**
- [ ] SETTINGS · Pairing (`Pairing`) and `EnrolVoice` · **partial**
- [ ] `/styleguide` · **partial** · every primitive and every state re-rendered on the new look; the reactor's states and the graph documented.

Views that are not routes:

- [ ] `EntityRow` · **partial**
- [ ] `TaskTimeline` / `TaskOutput` · **partial**
- [ ] `CodeDiff` · **partial**
- [ ] `ScheduledJobs` · **partial**
- [ ] `SkillsPanel` · **partial**
- [ ] `McpServers` · **partial**
- [ ] `Chart` (dashboards) · **partial** → area fill, draw-in, tabular axis.

The library (`src/lib/ui`), re-skinned once so the pages inherit:

- [ ] `Button` (default · primary · quiet · approve · danger · pressed) · **partial** → C2 `.btn`: uppercase Barlow, hairline, 6px, primary is the one filled control.
- [ ] `Pill` · **old** shape → a hairline tag (radius `md`); status dots stay round.
- [ ] `Tabs` · **partial** → the C2 sliding underline, shared with the top bar.
- [ ] `Toggle`, `Input`, `Select`, `Field`, `Toolbar`, `Panel`, `Row`, `Dialog`, `IconButton` · **partial**
- [ ] `SkeletonRows`, `EmptyState`, `ErrorState`, `OfflineState`, `ScreenState` · **partial**
- [ ] New: `TopBar`, `SectionStrip`, `StatusReadout`, `StagesBar`, `CallLine`, `DayStrip`, `ProgressRing` (a `Reactor` preset), `Graph` (knowledge), `Figure` (count-up) — each with a `@component` block, a README section, tests, and a style-guide entry.
- [ ] `chrome.css` · **old** · the grid, the brackets, the `.jv-*` pill classes and the mono defaults deleted once nothing references them; what remains is layout.

### M51 — the phone, on the same look

- [ ] HUD (`MainActivity`, `JarvisOrbView` / `ReactorOrb.kt` / `SiriOrbView`) · **old** · GLSL sphere, pill PAIR / SETTINGS → the instrument drawn on Canvas from the same geometry table, the same four states from `color.orb.*`, the same dock.
- [ ] Console frame strip (`ConsoleFrame.kt`) · **partial** → C2 tabs with the underline.
- [ ] Approval (`ApprovalActivity`, `ApprovalBridge` banner) · **partial** → the C2 approval bar.
- [ ] Task overlay (`ToolActivityView`, task frames) · **partial**
- [ ] PHONE settings (`SettingsActivity`), `SystemCheckActivity`, `VoiceIdentityActivity`, `PermissionRequestActivity`, `CompanionAskActivity`, `CrashLogActivity` · **partial** · `JarvisUi` pills and brackets → hairline panels, Barlow, one primary.
- [ ] Boot (`JarvisBootAnimation`, `BootTimeline`) · **partial** → the staged instrument.
- [ ] `JarvisUi.kt` `pill()` / `ghost()` / `CornerBrackets` · **old** → replaced; Roborazzi goldens re-rendered; `docs/ANDROID_DEVICE_TESTS.md` gains what only a device can confirm.
- [ ] Desktop app (`jarvis-desktop-app`) · loads the console build — nothing to restyle beyond the tray icon; verified by its existing Playwright `_electron` run.

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
