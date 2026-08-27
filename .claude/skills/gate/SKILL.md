---
name: gate
description: Run this repo's offline test gate the way CI runs it — bootstrap .venv if needed, ruff, every Python suite with CI's timeout flags, both evals, the Android mirrors and CI's static checks; `web` adds the jarvis-web suite. Use before pushing, after any non-trivial change, or when asked to run the tests.
---

# /gate — the offline gate, as CI runs it

Arguments: `$ARGUMENTS`. Empty runs everything offline. `web` adds the jarvis-web suite. A suite name (`lint`, `core`, `desktop`, `browser`, `services`, `evals`, `contract`, `android`, `static`) runs only that one.

A `claude/*` branch gets no CI, so this is the only gate. Nothing below needs hardware, models or the network, except a first `npm ci`.

## 0. Environment

Every shell is fresh: start each command with `. .venv/bin/activate`. If `.venv/bin/ruff` is missing, recreate the venv with the commands under "Environment" in CLAUDE.md. For `web`, run `cd jarvis-web && npm ci` if `node_modules/` is missing.

## 1. Run, in this order, each in its own subshell

CI's `--timeout=120 --timeout-method=signal` is not belt-and-braces: two jarvis-core voice tests hang forever on 3.12 without it, and a hang must surface as one named failure, not a stalled run.

```bash
python3 -m ruff check .                                                                        # lint
( cd jarvis-core    && python3 -m pytest tests -q --timeout=120 --timeout-method=signal )      # core
( cd jarvis-desktop && python3 -m pytest tests -q --timeout=120 --timeout-method=signal )      # desktop
( cd jarvis-browser && python3 -m pytest tests -q --timeout=120 --timeout-method=signal )      # browser
python3 -m pytest jarvis-orchestrator/tests jarvis-sandbox/tests -q --timeout=120 --timeout-method=signal          # services
( cd evals && python3 -m pytest test_routing.py test_resolution.py -q --timeout=120 --timeout-method=signal )      # evals
python3 -m pytest testing/e2e/test_ci_workflow_contract.py -q --timeout=120 --timeout-method=signal                # contract
make test-android                                                                              # android mirrors
```

Static checks — CI's `static` job, minus the contract suite already run above. The marker pattern is written with brackets so this file could never trip the scan it describes.

```bash
find . -name '*.sh' -not -path './node_modules/*' -not -path './.git/*' -not -path './.venv/*' -print0 | xargs -0 -n1 bash -n
python3 -m compileall -q jarvis-core/jarvis jarvis-desktop jarvis-browser jarvis-orchestrator/app jarvis-sandbox evals scripts android-app/tools
! grep -rnIiE '\bM[U]TANT\b|\bDELIBERATELY BR[O]KEN\b' \
    --include='*.py' --include='*.kt' --include='*.kts' --include='*.ts' --include='*.js' --include='*.svelte' \
    --include='*.sh' --include='*.yml' --include='*.yaml' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=build --exclude-dir=.svelte-kit \
    --exclude-dir=__pycache__ --exclude-dir=.venv . | grep -v '^\./\.github/workflows/ci\.yml:'
for f in docker-compose.yml jarvis-core/docker-compose.yml; do docker compose -f "$f" config -q; done
```

`web` (only when asked — it is slow and needs `node_modules`; Playwright additionally needs `npx playwright install chromium` once):

```bash
( cd jarvis-web && npm run build && npm test && node ../tests/web/smoke.test.mjs )
( cd jarvis-web && npx playwright test )
```

Unlike `make test-web`, do not swallow a Playwright failure.

## 2. Report

- A table, one row per suite: passed / failed / skipped, runtime — the shape of "Suite sizes" in `docs/verification.md`, so the numbers can be compared and, via `/claims`, re-measured into it.
- Everything that was **not** run, and why (no Docker daemon, no `node_modules`, no chromium).
- A failing test by name with its assertion text. Do not summarise it away, and do not re-run until green and call it flaky without saying so.
- Never weaken a test, add a skip, or leave a marker to get through. If a failure is already present on the base branch, show that (run the one test against the base commit) and say so.
