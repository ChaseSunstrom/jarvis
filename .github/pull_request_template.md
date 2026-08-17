<!--
Base this on `dev`. `main` is the release branch and only ever fast-forwards
from `dev`.
-->

## What changed, and why

<!-- The behaviour, not the diff. What was wrong or missing before this? -->

## How it was checked

<!-- Which suites you actually ran, and what you could not run here. -->

- [ ] `make test` (lint + every offline python suite + the evals)
- [ ] `make test-web` (build, unit, smoke, Playwright)
- [ ] Nothing below needs a rerun, or it is named here with why

## Anything a reviewer should look at twice

<!--
A security boundary, a wire contract another client parses, a default that
changes for existing installs, or a claim only real hardware can settle. If
none of those, say so.
-->

## Docs

<!--
`docs/verification.md` says what is proven and what is not; `DEVIATIONS.md`
records where this build knowingly differs from the plan. Update either if this
change moves them.
-->
