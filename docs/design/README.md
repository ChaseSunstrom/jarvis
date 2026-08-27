# docs/design — visual directions for Jarvis

Three deliberately divergent directions for the redesign, each mocked as static HTML with its
tokens inlined and no framework, on the three signature screens: the chat/voice view, the live
task-execution view, and a dashboard.

| Direction | Files | In one line |
|---|---|---|
| **A · Instrument** | `a-instrument-{chat,task,dashboard}.html` | a flight-deck of hairline panels, readouts and brackets; cyan only on what is live |
| **B · Ledger** | `b-ledger-{chat,task,dashboard}.html` | a typographic record — columns, marginalia, figures — with no panels at all |
| **C · Reactor** | `c-reactor-{chat,task,dashboard}.html` | composition around the orb; glass panels, radial progress, cyan as emitted light |
| **C · Reactor II** | `c2-reactor.html` (`?view=chat|task|dashboard`, keys 1/2/3) | the chosen direction, revised: the reactor as an instrument, flat hairline panels, sliding-underline tabs, real motion |

`index.html` shows all nine side by side with a paragraph of rationale each; `shots/` holds the
renders (`contact-sheet.png`, `strip-<direction>.png`, and one PNG per screen).

Reactor II renders (stills mid-animation plus two WebM clips of the motion): `node docs/design/screenshot-c2.mjs`.

Regenerate the renders headlessly (needs `jarvis-web/node_modules` and Playwright's Chromium):

```bash
node docs/design/screenshot.mjs
```

The fonts under `fonts/` are latin-subset woff2 files from Google Fonts, all under the SIL Open
Font License: Barlow Condensed, IBM Plex Sans, IBM Plex Mono, JetBrains Mono, Space Grotesk.

Decision: **C · Reactor**, revised as Reactor II (`c2-reactor.html`). Its `:root` block seeds
`design/tokens.json` (M01); its panels, tabs, reactor and cards seed `src/lib/ui` (M02).
