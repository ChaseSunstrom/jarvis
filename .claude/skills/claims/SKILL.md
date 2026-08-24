---
name: claims
description: After a change, reconcile docs/verification.md (the claims register) and DEVIATIONS.md with what the diff actually changed — find the affected rows, re-run their proof commands, re-measure counts, and never promote a claim without a demonstrating command. Use before a PR, or when asked whether the docs still hold.
---

# /claims — keep the claims register honest

`docs/verification.md` is a claims register: every row says what is proven, at which level (Automated / Containerised / Scripted / Manual / Unproven) and which command demonstrates it. `DEVIATIONS.md` records the judgement calls behind it. The PR template asks whether a change moves either; this answers with evidence rather than a guess.

Arguments: `$ARGUMENTS` — optional base ref to diff against (default `claude/jarvis-ai-assistant-nbqf1p`).

## 1. What changed

`git diff --stat <base>...HEAD`, then list: touched modules and test files; new, renamed or deleted tests; new tools, services or config keys; any change to `docker-compose.yml`, `.github/workflows/*`, `scripts/*.sh`, `tests/contracts/*.json`.

## 2. Which claims it touches

Grep `docs/verification.md` for every touched test file, module, service and script name, and read the rows for the component under "The matrix". Also check the "Suite sizes" table, "Known failures", "Closing the gaps", and the test-count sentence in `README.md`.

## 3. Re-run the proof

For each affected row, run the command printed beside it (`. .venv/bin/activate` first). The file's own "Maintaining this document" rules apply:

- Never promote a row without a command that demonstrates it. "Probably fine" is Unproven.
- Re-measure counts (`python3 -m pytest … -q` totals) rather than editing them by hand, and update the "measured on" date of the table you re-measured.
- A row that needs hardware (Manual) cannot be promoted from here. Leave it and say so.

## 4. Edit

- Update the level, the test file and the command. Keep the register's voice: terse, concrete, no softening.
- New capability with a test → new Automated row. New capability without one → new **Unproven** row, named, not omitted.
- `DEVIATIONS.md` gets a new numbered section only when the build now knowingly differs from what was planned, something is genuinely impossible and something else is done instead, or something could not be run here. Same voice as the existing entries; say what is done instead.
- Do not touch rows the diff does not affect.

## 5. Report

Each row changed (before → after), each command run with its result, and every claim you could not re-check and why.
