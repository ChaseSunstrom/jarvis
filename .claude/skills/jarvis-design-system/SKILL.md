---
name: jarvis-design-system
description: The Jarvis design system (Reactor II) — the token source of truth, the generated files every surface consumes, the token lint, and the four screen states every screen must implement. Use before writing or changing any UI on the web console, the desktop app or the Android app, and whenever a colour, size, font, radius, shadow or duration is about to be typed.
---

# Jarvis design system — Reactor II

One source of truth, generated everywhere, linted in `make verify-all`. The
direction is `docs/design/c2-reactor.html` (the chosen mockup); the system is
what this file describes. These are rules, not preferences.

## 1. Where a value is typed, and where it is not

- **`design/tokens.json`** is the only file in the repository where a colour,
  size, font, radius, shadow or duration is typed by a human. DTCG format:
  groups of `{ "$type", "$value", "$description" }`; `"{color.focus}"` is an
  alias.
- **`python3 design/build.py`** regenerates every consumer. Each output carries
  `@generated from design/tokens.json — DO NOT EDIT`; editing one is a bug the
  next `--check` reports:
  - `jarvis-web/src/lib/styles/tokens.css` — `--jv-*` custom properties
  - `jarvis-web/src/lib/tokens.ts` — `TOKENS`, `token()`, `cssVar()`, `tokenMs()`, `STATE_ACCENT`
  - `jarvis-desktop/jarvis_desktop/tokens.py` — `TOKENS` + the named palette `theme.py` imports
  - `android-app/…/ui/theme/JarvisTokens.kt` — `JarvisTokens.Color/Type/Space/Radius`; `JarvisUi` aliases them
  - `android-app/…/ui/theme/JarvisTheme.kt` — the Compose `MaterialTheme`
  - `android-app/app/src/main/res/values/tokens.xml` + `colors.xml` (aliases only)
- **`python3 design/build.py --check`** fails if any output is stale or the two
  pinned files (`SiriPalette.kt`, `Orb.svelte`'s palette) drift from `color.orb.*`.
- **`python3 scripts/verify/token_lint.py`** fails on any hard-coded colour,
  spacing, type or motion value in app code (web `.svelte/.css/.ts`, Android
  `.kt`, desktop `.py`). `design/token-lint.baseline.json` is a ratchet: legacy
  files may keep their counted hits until their milestone clears them; a new
  file or a grown count fails. Never edit the baseline to make a check pass —
  `--update-baseline` is for a milestone that legitimately removed hits.
  Documented exceptions (the GLSL orb, its renderer, `SiriPalette.kt`, the QR
  encoder) are listed in the baseline with the reason.

Adding a token: add the leaf to `tokens.json` with a `$description` that says
what it is *for*, run `design/build.py`, use the generated name. Never add a
token for one use — use the closest existing step; if none fits, the scale is
wrong and that is a conversation, not a new number.

## 2. The vocabulary

Names are the console's `--jv-*` set (`design/tokens.json` `$meta.naming`):

| Need | Token | Notes |
|---|---|---|
| Ground | `--jv-bg` | everything sits on it; `--jv-bg-raised` one step up |
| Panel | `--jv-panel` (= `--jv-panel-solid`) | flat, opaque, `border: 1px solid var(--jv-line-hair)`, `border-radius: var(--jv-radius-md)` — **no blur, no glass** |
| Raised control / row | `--jv-surface-2` | hovered row, active segment |
| Inset | `--jv-surface-sunken` | diffs, console output |
| Input ground | `--jv-field` | |
| The accent | `--jv-accent` | **spent, not spread**: the current step, the live value, the active tab underline, the one primary control per screen |
| Accent at rest | `--jv-accent-deep` | idle orb, iris arcs, a secondary rule |
| Ink on accent | `--jv-accent-ink` | text on a filled control, never on the ground |
| Tint / light | `--jv-wash`, `--jv-wash-strong` / `--jv-glow` | glow is budgeted: reactor core, current step, push-to-talk ring |
| Semantic | `--jv-ok` · `--jv-warn` (held) · `--jv-danger` (broke) · `--jv-danger-text` | semantic colour is not the accent |
| Orb states | `--jv-amber` (thinking) · `--jv-gold` (speaking) | `STATE_ACCENT` in tokens.ts |
| Text | `--jv-text-bright` (the one line to read first) · `--jv-text` · `--jv-text-dim` · `--jv-text-faint` | all clear AA on the ground |
| Marks | `--jv-tick` | ticks, dashed coils, decorative dots — **never text** (below AA on purpose) |
| Rules | `--jv-line` (edge) · `--jv-line-soft` (divider) · `--jv-line-hair` (between rows) | |
| Type | `--jv-font-body` (Barlow — UI) · `--jv-font-display` (Space Grotesk 300 — the reply line, big figures, titles) · `--jv-font-chrome` (JetBrains Mono — data, timestamps, ids) | sizes `--jv-fs-2xs…display`, weights `--jv-weight-*`, tracking `--jv-track-*`; uppercase labels get `--jv-track-wide` |
| Space | `--jv-space-1…7` | `0` and a `1px` hairline are the only literals allowed |
| Radius | `--jv-radius-sm/md/lg/pill` | `md` is Reactor II's radius; `lg` only the dock and a floating sheet |
| Elevation | `--jv-elev-panel/float`, `--jv-glow-sm/md/lg` | |
| Motion | `--jv-dur-fast` (hover) · `--jv-dur-base` (a tab sliding, a panel opening) · `--jv-dur-enter` (first paint) · `--jv-ease-*` · `--jv-stagger-step/cap` · `--jv-rx-*` (the reactor's clock) | everything collapses under `prefers-reduced-motion` |

Android: `JarvisUi.Type.*` (sp) and `JarvisUi.Space.*` (dp) are the phone's
scales; `JarvisTokens.Color.*` the palette; Compose uses `JarvisTheme { }`.
Desktop: `from .tokens import BG, ACCENT, …`.

## 3. The look, in rules

- Near-black, one cyan, dense but calm. Panels are flat hairline surfaces; the
  only glow is on the reactor, the current step and the push-to-talk ring; the
  only filled cyan control on a screen is its primary action (APPROVE).
- Tabs: uppercase `--jv-font-body` labels on a hairline with a sliding
  `--jv-accent` underline (`--jv-dur-base`); no pill bars.
- Live things move on purpose: a running dot pulses, a current step breathes,
  charts draw in, figures count up, panels enter with a `--jv-stagger-step`
  stagger. Nothing moves for decoration; nothing moves under reduced motion.
- No purple, no gradients as decoration, no rounded-everything, no emoji as
  markers. Structure encodes information: a number only where there is a sequence.
- Density comes from information, not chrome: hairlines over boxes, tabular
  numerals (`font-variant-numeric: tabular-nums`) wherever digits line up.

## 4. The four screen states — every screen, no exceptions

Every routed screen implements **loading, empty, error, offline**, each as a
real moment on the design system — never a blank, never a toast with a log line:

- **loading** — `Skeleton` rows in the shape of the content, `role="status"`.
- **empty** — a title that says what would be here and one sentence on how it
  gets here (`No tasks have run today. Ask Jarvis for something, or schedule one.`).
- **error** — what went wrong and what to do (`Couldn't load tasks. The backend
  answered 500. Retry, or check docker compose logs jarvis-core.`), `role="alert"`, a Retry.
- **offline** — the link is down, reconnecting, and what you see is the last
  known state; a Reconnect now.

From M02 the `<ScreenState>` component (`$lib/ui`) owns all four so a screen
cannot forget one; `scripts/verify/web_states_check.py` and `e2e/states.spec.ts`
enforce it. Every visible control does something (`web_dead_controls.mjs`);
every screen holds at 360/768/1024/1440 with no horizontal overflow.

## 5. Before you finish any UI change

```bash
python3 design/build.py --check           # generated files current, orb palette not drifted
python3 scripts/verify/token_lint.py      # no new hard-coded value, no grown count
bash scripts/verify/m01-design-tokens.sh  # the design-system milestone's own gate
```

`make verify-all` runs all three. The style guide is `/styleguide` in the
console (`jarvis-web/src/routes/styleguide/+page.svelte`); a new token or
component appears there in the same change.
