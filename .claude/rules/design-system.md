---
paths:
  - "design/**"
  - "jarvis-web/src/**"
  - "jarvis-desktop/jarvis_desktop/**"
  - "jarvis-desktop-app/**"
  - "android-app/app/src/main/**"
---

# Design system applies here

Load the `jarvis-design-system` skill before styling anything in these paths.
The rules that matter most while editing:

- Never type a colour, size, font, radius, shadow or duration. Use a `--jv-*`
  token (web), `JarvisTokens`/`JarvisUi.Type`/`JarvisUi.Space` (Android) or
  `jarvis_desktop.tokens` (desktop). `design/tokens.json` is the only place a
  value is typed; `python3 design/build.py` regenerates the rest.
- Every generated file says `@generated from design/tokens.json` — do not edit it.
- Every screen implements loading, empty, error and offline as real states.
- Run `python3 scripts/verify/token_lint.py` before you finish; a new file
  with a hard-coded value, or a baselined file whose count grew, fails
  `make verify-all`.
