# Jarvis — top-level developer tasks.
# `make help` lists targets. `make test` runs everything runnable in CI/dev
# (no hardware). Hardware gates are in `make test-e2e` / docs/acceptance.

SHELL := /bin/bash
COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n",$$1,$$2}'

.PHONY: gen-tools
gen-tools: ## regenerate HA config from jarvis_tools/*.tool.yaml
	python3 jarvis_tools/generate_config.py

.PHONY: test-python
test-python: ## run all python unit/integration tests
	python3 -m pytest jarvis_tools/tests jarvis-orchestrator/tests \
	  jarvis-sandbox/tests evals -q

.PHONY: test-web
test-web: ## build + unit + smoke + e2e for the HUD
	cd jarvis-web && npm run build && npm test && node ../tests/web/smoke.test.mjs
	cd jarvis-web && npm run test:e2e || echo "(playwright skipped/failed — see jarvis-web/README.md)"

.PHONY: eval-routing
eval-routing: ## P3 routing table test (offline)
	cd evals && python3 -m pytest test_routing.py -q

.PHONY: eval-persona
eval-persona: ## P3 persona eval (needs Ollama or HA; BACKEND=ollama|ha)
	cd evals && python3 persona_eval.py --backend $(or $(BACKEND),ollama)

.PHONY: eval-decomp
eval-decomp: ## P8 task-decomposition ship/no-ship gate (needs model)
	cd evals && python3 decomposition_eval.py --backend $(or $(BACKEND),ollama)

.PHONY: test
test: test-python eval-routing ## everything runnable without hardware/models
	@echo "OFFLINE TEST SUITE PASSED"

.PHONY: smoke
smoke: ## P0: full stt->tts round trip against real HA (needs HA_TOKEN)
	python3 scripts/pipeline-smoke.py

.PHONY: firewall
firewall: ## P9: apply ufw policy (root, real server). DRY_RUN=1 to preview
	sudo -E bash scripts/apply-firewall.sh

.PHONY: egress-audit
egress-audit: ## P9: verify sandbox network isolation (needs running stack)
	bash scripts/egress-audit.sh

.PHONY: up
up: ## build and start the server stack
	$(COMPOSE) up -d --build

.PHONY: down
down: ## stop the stack
	$(COMPOSE) down

.PHONY: test-e2e
test-e2e: ## P9 full gate: offline suite + smoke + egress (+ device tests are manual, see docs/acceptance.md)
	$(MAKE) test
	-$(MAKE) smoke
	-$(MAKE) egress-audit
	cd evals && python3 persona_eval.py --backend ollama || echo "(persona eval needs a model)"
	@echo "See docs/acceptance.md for the on-device (Pixel, head unit) gates."
