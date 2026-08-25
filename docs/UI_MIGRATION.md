# UI_MIGRATION.md — the console on C2, and fewer places to be

The console is being rebuilt on **C · Reactor II** (`docs/design/c2-reactor.html`;
the decision is in `docs/design/README.md`). This file is the checklist that
milestone is done against, and the first half of it is not styling.

## 1. The navigation, before anything moves

The console has **eleven** top-level destinations. That is the defect, not a
detail: the header wraps to two rows at 1280px because eleven tabs plus a brand
and a status readout no longer fit on one, and the two ways out of that are
both worse than the wrap — a horizontally scrolling nav hides SETTINGS behind a
scrollbar nobody can see, and shrinking the items is a fight the twelfth tab
wins anyway.

So the structure is reduced **first**, and pages are migrated into their new
homes rather than restyled where they are. A page restyled in place is a page
that has to move twice.

### The proposed structure: four, plus the HUD

| Destination | What it is | Why these belong together |
|---|---|---|
| **`/` (the HUD)** | Talking to Jarvis. Not a tab — it owns the viewport and paints its own chrome. | It is the product. Everything below is administration of it. |
| **HOUSE** | The physical home: what is on, where it is, what it has been doing, and the rules that run themselves. | One question — "what is my house doing" — currently answered by four tabs. Somebody checking a light and somebody checking last night's temperature are the same person, thirty seconds apart. |
| **WORK** | What Jarvis is doing or has done for you: tasks, research runs, coding jobs. | A coding job IS a task. They were two tabs because they were built in different months, not because they are different things. |
| **KNOWLEDGE** | What Jarvis knows: notes it has written, and what it remembers about you. | Notes and memory are the same question from two distances — "what did you write down" and "what do you know". Keeping them apart makes people search twice. |
| **SETTINGS** | Configuration and capability: the assistant's own settings, what it can call, what is installed, the machines it runs on, pairing. | Everything here is opened rarely and deliberately. That is the definition of the thing that does not belong in the top bar. |

Four tabs and a HUD. The verify script fails if the top-level nav has more than
five destinations, so this cannot quietly grow back.

### Where each current page ends up

Every route that exists today, and its home after the consolidation. Nothing is
deleted: a page becomes a **section** of its new home, reachable by its own
anchor, and its old path redirects.

| Today | After | How it appears |
|---|---|---|
| `/` | `/` | Unchanged. The HUD. |
| `/devices` | **HOUSE** | The default section. Entities by area, with their controls. |
| `/areas` | **HOUSE** | A section: the rooms voice resolves against, and what is in them. |
| `/dashboards` | **HOUSE** | A section: the graphs. Same page, one level in. |
| `/automations` | **HOUSE** | A section: the rules, their traces, the editor. |
| `/tasks` | **WORK** | The default section. |
| `/tasks/[id]` | **WORK** | A detail view, not a section — one task, opened from the list. |
| `/code` | **WORK** | A section: coding jobs, repositories, diffs. |
| `/notes` | **KNOWLEDGE** | The default section. |
| `/memory` | **KNOWLEDGE** | A section, beside the notes rather than a tab away. |
| `/settings` | **SETTINGS** | The default section. |
| `/tools` | **SETTINGS** | A section: what the model can call, and what it is allowed to call. |
| `/tools` → Extensions (M46/M47) | **SETTINGS** | A section: what is installed, its permissions, and the catalog. |
| `/desktop` | **SETTINGS** | A section: the machines running the desktop agent. |
| `/styleguide` | `/styleguide` | Unchanged, and still not in the nav. It is a developer surface. |

Components that are not routes keep their homes: `Approvals`, `Notifications`,
`CommandPalette`, `TaskDock`, `Toasts` and `BootSequence` are chrome, drawn by
the layout on every destination.

### What this costs, said plainly

* **A click for things that used to be a tab.** `/dashboards` is one level in
  now. That is the trade: eleven front doors, or four and a step.
* **The command palette matters more.** It already jumps to any page; with
  fewer tabs it is the fast path for people who know where they are going, and
  it must index sections, not just destinations.
* **Deep links have to keep working.** Every old path redirects to its section
  anchor. A bookmark, a link in a note, and the Android app's own tab strip all
  point at these.
* **The phone mirrors this.** `android-app/tools/console_parity_test.py` binds
  the console's sections to `ConsoleTab.kt`; they move in the same change or
  the mirror goes red.

## 2. The inventory

One row per page, view and modal, found by walking `src/routes` and the
component tree rather than remembered. **The milestone is not done while a box
is unchecked.**

Each row is done when: it is built on the C2 tokens and the shared component
library, it has no hard-coded style value (`scripts/verify/token_lint.py`), it
implements loading, empty, error and offline as real states, it renders at
mobile, tablet and desktop widths, its motion comes from the M44 primitives,
and a headless screenshot of it lands in `docs/ui-review/<page>/<breakpoint>.png`.

### Destinations

- [ ] `/` — the voice HUD
- [ ] **HOUSE** — shell, section switching, deep-link anchors
- [ ] **WORK** — shell, section switching, deep-link anchors
- [ ] **KNOWLEDGE** — shell, section switching, deep-link anchors
- [ ] **SETTINGS** — shell, section switching, deep-link anchors

### Sections

- [ ] HOUSE · Devices (from `/devices`)
- [ ] HOUSE · Areas (from `/areas`)
- [ ] HOUSE · Dashboards (from `/dashboards`)
- [ ] HOUSE · Automations (from `/automations`)
- [ ] WORK · Tasks (from `/tasks`)
- [ ] WORK · Task detail (from `/tasks/[id]`)
- [ ] WORK · Code (from `/code`)
- [ ] KNOWLEDGE · Notes (from `/notes`)
- [ ] KNOWLEDGE · Memory (from `/memory`)
- [ ] SETTINGS · Assistant (from `/settings`)
- [ ] SETTINGS · Tools (from `/tools`)
- [ ] SETTINGS · Extensions and catalog (M46, M47)
- [ ] SETTINGS · Desktop (from `/desktop`)
- [ ] SETTINGS · Pairing and devices (`Pairing.svelte`)

### Chrome, drawn on every destination

- [ ] Layout shell: header, nav, status readout, skip link
- [ ] `CommandPalette` — and it must index sections, not only destinations
- [ ] `Approvals`
- [ ] `Notifications`
- [ ] `TaskDock` / `TaskBar` / `TaskCard`
- [ ] `Toasts`
- [ ] `BootSequence`
- [ ] `Orb` / `Reactor`

### Views and modals

- [ ] `ChatPanel` / `ChatMessage`
- [ ] `ModeToggle`
- [ ] `EntityRow`
- [ ] `TaskTimeline` / `TaskOutput` / `ToolActivity`
- [ ] `CodeDiff`
- [ ] `ScheduledJobs`
- [ ] `SkillsPanel`
- [ ] `McpServers`
- [ ] `Extensions` (the row, the scope editor, the catalog and install dialogs)
- [ ] `EnrolVoice`
- [ ] `Moment`
- [ ] `Pairing`
- [ ] `/styleguide` — every token group and every state, on the new structure

## 3. What "clean" means here

Not a mood. Four things a check can ask about:

* **One primary action per surface.** If a screen has two things competing to
  be pressed, one of them is a section of its own or belongs behind an
  expander.
* **Progressive disclosure.** Advanced and rare controls are behind an
  expander, not crowding the main view. The M46 extension row is the pattern:
  name, state and one switch on the row; the tool list, the permission scope
  and the provenance one click in.
* **Space over density.** Where the two conflict, spacing wins. A console
  somebody reads at a glance beats one that fits more.
* **Understandable without explanation.** A first-time user should know what a
  screen is for from the screen. That is the one a test cannot check, and it is
  what the `docs/ui-review/` screenshots are for.

## 4. Motion, in service of the structure

The M44 tokens and primitives, applied so that motion answers "where did that
go" rather than decorating:

* moving between sections of one destination is a **shared-element**
  transition — the section stays, its content changes, and the eye is told
  which;
* moving between destinations is the existing route transition;
* everything stays inside the M44 frame budget and the reduced-motion path,
  both of which are already measured.

**Non-negotiable**: this refines structure and polish. It does not restyle. The
C2 look and its tokens are the constraint, not the subject.
