# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Jarvis is a self-hosted voice/home assistant. `jarvis-core/` (Python) is the hub; `jarvis-web/` (SvelteKit) the console; `android-app/` (Kotlin) the phone; `jarvis-desktop/`, `jarvis-browser/`, `jarvis-orchestrator/`, `jarvis-sandbox/` are Python side services. One repo, no workspace tool — each Python tree has its own `tests/`. The long-form docs (`docs/`, `DEVIATIONS.md`, `jarvis-core/docs/`) are kept accurate; check them before inferring behaviour from code.

## Environment

Nothing is installed system-wide (no pip, no `python3-venv`, no sudo). Python deps live in the repo-local `.venv` (gitignored). The Makefile calls `python3 -m …` and every shell you open is fresh, so prefix each command:

```bash
. .venv/bin/activate && make test
```

Recreate `.venv` if it is missing (`python3 -m venv .venv` fails here — no ensurepip):

```bash
python3 -m venv --without-pip .venv
curl -sSf https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && .venv/bin/python /tmp/get-pip.py
.venv/bin/python -m pip install pytest pytest-asyncio pytest-timeout "ruff==0.16.3" \
  -r jarvis-core/requirements.txt -r testing/requirements.txt \
  -r jarvis-browser/requirements.txt -r jarvis-orchestrator/requirements.txt -e jarvis-desktop
```

Local Python is 3.11; CI runs 3.12. The web console needs `cd jarvis-web && npm ci` once.

## Commands

- `make test` — the offline gate: ruff + every Python suite + both evals. `make help` lists the rest.
- `make test-web` — build, `svelte-check`, vitest, smoke, Playwright. It swallows Playwright failures (`|| echo`); CI does not.
- `make test-android` — runs `android-app/tools/*.py`, the executable spec of the Kotlin. The Kotlin itself builds here too: `export JAVA_HOME=$HOME/.local/jdk ANDROID_HOME=$HOME/Android/Sdk PATH=$HOME/.local/jdk/bin:$HOME/.local/gradle/bin:$PATH`, then from `android-app/` `gradle :app:assembleDebug :app:testDebugUnitTest --no-daemon -q` (1–3 min a task, 3 GB heap — one gradle at a time beside a pytest run, and in the background with a log; the M61/M64/M71 gates do this). A changed golden is re-recorded with `:app:recordRoborazziDebug` and committed under `android-app/app/src/test/screenshots/`.
- One suite, with CI's flags: `cd jarvis-core && python3 -m pytest tests -q --timeout=120 --timeout-method=signal` (two voice tests hang forever on 3.12 without `--timeout`). One test: add `-k name`.
- `/gate` runs all of the above the way CI does; `/claims` reconciles `docs/verification.md` afterwards; `/pr` opens the pull request.
- Stack: `make up` / `make down`. Two compose files — `jarvis-core/docker-compose.yml` first, then the root one. Orchestrator + sandbox are behind `--profile agents` on purpose (a command broker must not start by default); SearXNG is `--profile search`, MQTT `--profile mqtt`. This checkout is a dev box: restarting containers is fine.

## Conventions that differ from the defaults

- Lint is ruff with a deliberately defect-only ruleset (`ruff.toml`: F, E9, B006/B008/B023). Do not add style rules, run a formatter, or "fix" quote style, import order or line length in passing — the file's header says why.
- Python: `from __future__ import annotations`, PEP 604/585 types, double quotes, 100 columns, `_LOGGER = logging.getLogger(__name__)`, Sphinx `#:` attribute comments. Svelte/TS: tabs, single quotes, semicolons. Nothing enforces either; match the file you are in.
- Comments say *why* and name the failure they prevent; docstrings say what a guarantee does **not** cover. That is the house register — a bare "what" comment is below the bar.
- Logic that exists in two languages (Kotlin ↔ `android-app/tools/*.py`, server ↔ console) is bound by a shared table in `tests/contracts/*.json` that both suites read, never by a "keep in step" comment. Change the Kotlin → change its mirror and run it.
- The strings `MUTANT` and `DELIBERATELY BROKEN` in any `.py/.kt/.kts/.ts/.js/.svelte/.sh/.yml/.yaml` fail CI's `static` job (a mutation stub once reached `main`). Never leave one in the tree, even briefly.
- `jarvis-core/tests/test_packaging.py` pins `docker-compose.yml` ↔ `configuration.yaml` ↔ `.env.example` agreement (`TZ`, `PIPER_VOICE`, `WAKE_WORD`, the `!env_var` names). Change one side, change the others.
- `tests/web/mock-ha.mjs` is the console's mock backend. A new server payload key must be added there too, or the console tests pass while the real console breaks.

## Git

- Work on the `claude/*` lineage: branch from and merge into `claude/jarvis-ai-assistant-nbqf1p`. (`dev`/`main` and the PR template's "base on dev" describe the upstream model, not this checkout.)
- CI runs only on push/PR to `main`/`dev` or `workflow_dispatch` — a `claude/*` branch gets none. Run `/gate` before pushing.
- Commit subjects are sentence-style with no conventional-commit prefix and describe the behaviour change (`Close three ways a coding job could reach the host`). Bodies are long: what was wrong, how it was reproduced, what the change does *not* fix, suite counts last. Keep the `Co-Authored-By` / `Claude-Session` trailers.
- If a change moves a claim in `docs/verification.md` (Automated / Containerised / Scripted / Manual / Unproven) or a judgement call in `DEVIATIONS.md`, update it in the same change. Never promote a row without the command that demonstrates it; re-measure counts rather than editing them.

## Gotchas

- The only precious local state is `.env` and `jarvis-core/.env` — real tokens, gitignored. Never overwrite them from the `.env.example` files. Never `git clean -xfd`: it also deletes ~200 MB of untracked models (`jarvis-core/wyoming/`, `jarvis-core/config/models/`) and `update.sh`. Everything else under `jarvis-core/config/` is ordinary config — edit it as features need.
- `jarvis-sandbox` must never get host or LAN networking (`network_mode: none`, pinned by tests and `scripts/egress-audit.sh`). The Jarvis Code sandbox has its own invariants: `.claude/rules/jarvis-code-sandbox.md` loads when you touch `jarvis-core/jarvis/integrations/code/`.
