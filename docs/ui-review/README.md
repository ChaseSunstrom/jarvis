# ui-review — what the console looks like, at three widths

Sixteen screens × mobile (390), tablet (834) and desktop (1440), captured
headlessly against the mock backend. Regenerate with:

```bash
cd jarvis-web && UI_REVIEW=1 npx playwright test e2e/ui-review.spec.ts
```

These are not assertions. `states.spec.ts` proves every screen renders and
handles loading, empty, error and offline; `responsive.spec.ts` proves nothing
overflows or is crushed at five widths; `token_lint.py` proves no value was
typed by hand. What none of them can answer is M48's remaining question — is
the hierarchy obvious, is it clean, would somebody seeing it for the first time
know what the screen is for — and that is what these are for.

They earned their place on the first run: the Extensions panel's header was
rendering one letter per line at 390px, inside a flex row whose buttons took
their natural width. Nothing scrolled sideways, no test failed, and it was
plainly broken in the picture. `responsive.spec.ts` fails on crushed prose now.

**They are a snapshot, not a contract.** Nothing compares against them, so a
stale one is misleading rather than red — regenerate them when the console
changes shape.
