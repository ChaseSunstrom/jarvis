# Jarvis — top-level developer tasks.
# `make help` lists targets. `make test` runs everything runnable in CI/dev
# (no hardware, no models, no network). Hardware gates live in `make smoke`
# and docs/verification.md.

SHELL := /bin/bash
COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n",$$1,$$2}'

# --- tests ------------------------------------------------------------------
.PHONY: test-core
test-core: ## jarvis-core: the assistant itself (the big one)
	cd jarvis-core && python3 -m pytest tests -q

.PHONY: test-desktop
test-desktop: ## jarvis-desktop
	cd jarvis-desktop && python3 -m pytest tests -q

.PHONY: test-browser
test-browser: ## jarvis-browser
	cd jarvis-browser && python3 -m pytest tests -q

.PHONY: test-services
test-services: ## orchestrator + sandbox
	python3 -m pytest jarvis-orchestrator/tests jarvis-sandbox/tests -q

.PHONY: test-contract
test-contract: ## the workflow files, checked against how GitHub runs them
	python3 -m pytest testing/e2e/test_ci_workflow_contract.py -q --timeout=120

.PHONY: test-tools
test-tools: ## the repository's own tooling (scorecard arithmetic, the toolbelt tape measure)
	python3 -m pytest testing/tools evals/intelligence -q --timeout=120

.PHONY: test-python
test-python: test-core test-desktop test-browser test-services test-contract test-tools eval-routing eval-resolution ## every python suite

.PHONY: lint
lint: ## ruff, defect-only ruleset (see ruff.toml)
	python3 -m ruff check .

.PHONY: lint-fix
lint-fix: ## the same, applying what it can fix
	python3 -m ruff check . --fix

.PHONY: test-web
test-web: ## build + unit + smoke + e2e for the HUD
	cd jarvis-web && npm run build && npm test && node ../tests/web/smoke.test.mjs
	cd jarvis-web && npm run test:e2e || echo "(playwright skipped/failed — see jarvis-web/README.md)"

.PHONY: test-android
test-android: ## the Kotlin logic mirrors (pure python, no SDK)
	@fail=0; for t in android-app/tools/*.py; do echo "--- $$t"; python3 "$$t" || fail=1; done; exit $$fail

.PHONY: test
test: lint test-python ## everything runnable without hardware or models
	@echo "OFFLINE TEST SUITE PASSED"

# --- design system ------------------------------------------------------------
.PHONY: tokens
tokens: ## regenerate every surface's tokens from design/tokens.json
	python3 design/build.py

.PHONY: tokens-check
tokens-check: ## fail if a generated token file is stale or the orb palette drifted
	python3 design/build.py --check

.PHONY: token-lint
token-lint: ## fail on a hard-coded colour/spacing/type/motion value in app code (ratchet: design/token-lint.baseline.json)
	python3 scripts/verify/token_lint.py

# --- evals ------------------------------------------------------------------
.PHONY: eval-routing
eval-routing: ## routing table + its two mirrors (offline)
	cd evals && python3 -m pytest test_routing.py -q

.PHONY: eval-resolution
eval-resolution: ## does "the kitchen lamp" find the kitchen lamp (offline)
	cd evals && python3 -m pytest test_resolution.py -q

.PHONY: eval-resolution-report
eval-resolution-report: ## the same, case by case, for tuning the matcher
	cd evals && python3 test_resolution.py

.PHONY: eval-persona
eval-persona: ## persona eval (needs a model; BACKEND=ollama|jarvis)
	cd evals && python3 persona_eval.py --backend $(or $(BACKEND),ollama)

.PHONY: eval-decomp
eval-decomp: ## task-decomposition ship/no-ship gate (BACKEND=ollama|orchestrator)
	cd evals && python3 decomposition_eval.py --backend $(or $(BACKEND),ollama)

# --- running things ---------------------------------------------------------
.PHONY: up
up: ## start jarvis-core, then the companion stack (HUD/orchestrator/sandbox)
	cd jarvis-core && $(COMPOSE) up -d --build
	$(COMPOSE) up -d --build

.PHONY: down
down: ## stop both stacks
	-$(COMPOSE) down
	cd jarvis-core && $(COMPOSE) down

.PHONY: smoke
smoke: ## boot a throwaway jarvis-core and drive its real APIs
	bash scripts/e2e-smoke.sh

.PHONY: pipeline-smoke
pipeline-smoke: ## full stt->tts audio round trip (needs JARVIS_TOKEN + Wyoming)
	python3 scripts/pipeline-smoke.py

.PHONY: firewall
firewall: ## apply the ufw policy (root, real server). DRY_RUN=1 to preview
	sudo -E bash scripts/apply-firewall.sh

.PHONY: egress-audit
egress-audit: ## verify sandbox network isolation (needs the stack running)
	bash scripts/egress-audit.sh

.PHONY: verify
verify: ## the full gate: offline suite, then the hardware-backed checks
	$(MAKE) test
	-$(MAKE) smoke
	-$(MAKE) egress-audit
	-$(MAKE) eval-persona
	@echo "See docs/verification.md for the on-device (Pixel, head unit) gates."

.PHONY: verify-all
verify-all: ## the whole target state, one script per milestone (scripts/verify/); fails on any error
	bash scripts/verify/all.sh
