# `$lib/ui` — the component library

Every primitive the console is built from. Import from the barrel:

```svelte
import { Button, Panel, ScreenState } from '$lib/ui';
```

The rules, enforced by `bash scripts/verify/m02-styleguide.sh`:

- **Tokens only.** `python3 scripts/verify/token_lint.py --require-clean
  jarvis-web/src/lib/ui` fails on any typed colour, size, radius, shadow or
  duration. Values come from `design/tokens.json` through `--jv-*`.
- **Self-documenting.** Each component starts with a `<!-- @component -->` block
  showing what it is for and how to call it; each has a `## Name` section here.
- **On the style guide.** `/styleguide` renders every component in every state.
- **Server-safe.** `ssr.test.ts` renders each one on the server: no timers
  armed, no `window` read at module scope.

## Button

The console's action. `variant`: `ghost` (default), `primary` (exactly one per
screen — the thing the screen is for), `danger` (destructive; the label says so
too). `disabled` always comes with a `title` saying why.

## IconButton

A button whose label is a glyph. `label` is required and is both the accessible
name and the tooltip — an icon-only control with no name cannot be described.

## Input

Single-line, or a textarea when `rows > 1`. `mono` for ids, JSON and anything
typed exactly. `invalid` marks it wrong; the message belongs on the `Field`.

## Select

A choice from a fixed list of `{ value, label }`.

## Toggle

On/off with its label and an optional `hint` saying what turning it on does. A
real checkbox underneath, so keyboard and screen readers get one.

## Field

A labelled control with room for a `hint` and an `error`. Wraps any control.

## Panel

A flat surface with a hairline edge and an optional head (`title`, `meta`,
`live`). Reactor II has no glass — depth is the hairline, not a shadow.

## Row

One line in a list: label left, value or controls right, hairline under.
`current` marks the one thing happening now (accent rule, brighter text).

## Pill

A small status word. `tone` — `neutral` · `live` · `ok` · `warn` · `danger` —
carries meaning, and the word says it too, so colour is never the only signal.

## Toolbar

The strip above a list: `children` at the start, `end` pushed right, wrapping
on a narrow screen rather than overflowing.

## Tabs

Reactor II's tab strip: uppercase labels on a hairline with a sliding accent
underline. A tab may carry a `count` and a `live` dot.

## Dialog

A modal question. Escape closes it; the backdrop is inert to a click, because a
dialog that vanishes when you brush past it loses an answer somebody meant to
give.

## SkeletonRows

Placeholder rows with the rhythm of the real ones, so the page does not jump
when data lands and an empty screen never flashes at somebody still connecting.

## EmptyState

Nothing here yet, and how something arrives. Never a bare blank.

## ErrorState

What went wrong (`title`, in the user's terms), the machine's words (`detail`),
and the one action that might fix it (`onretry`).

## OfflineState

The link is down, what is on screen is the last known state, and a Reconnect.

## ScreenState

The four states in one place — `loading` · `empty` · `error` · `offline` — plus
`ready`, which renders the screen. A screen declares one `status` and cannot
forget a state by not writing it. Required on every routed page by
`scripts/verify/web_states_check.py`.

## Reactor

The arc reactor as an instrument: bezel, blades, coil, level arc, lens with two
iris arcs. `level` fills the arc; `segments` (`{ done, running, total }`) groups
the blades into plan steps, which is what makes the task ring a progress ring;
`state` colours the core; `breathing={false}` (or reduced motion) stills it.
