#!/usr/bin/env bash
# M18 — the research engine: lead-following, cross-checking, confidence,
# quick/deep modes, live findings, markdown reports; proven by a scripted eval
# over a fixed question set — offline against recorded responses, and live
# against SearXNG when it is reachable (the Scripted claim).
source "$(dirname "$0")/lib.sh"
verify_begin "M18" "research engine"
use_venv
R=jarvis-core/jarvis/integrations/research/__init__.py
P=jarvis-core/jarvis/integrations/research/plan.py

require_file "$R"
check "follows leads (bounded depth)" grep -qiE 'lead_depth' "$R"
check "cross-checks claims across sources" grep -qiE 'cross_check' "$R"
# Asked of plan.py, not __init__.py: the decisions a run makes live in the pure
# module — `cross_check`, `MODES`, the report format — and the orchestration in
# __init__.py is deliberately thin. That split is the reason those decisions can
# be tested exhaustively without a search engine or a model.
check "confidence note per claim" grep -qi 'confidence' "$P"
check "two modes of one engine (quick, deep)" grep -qE '"quick"' "$P"
check "and the modes are budgets, not a second implementation" grep -q 'MODE_BUDGETS' "$P"
check "findings stream as task output events" grep -qE '\.output\(' "$R"
check "reports are written as markdown files" grep -qE '_write_report_file' "$R"
check "reports are saved as notes" grep -qE 'note_create|notes' "$R"
check_not "no cloud search fallback anywhere" grep -rniE 'duckduckgo|bing\.com|google\.com/search|serpapi|brave\.com/api' jarvis-core/jarvis/integrations/web jarvis-browser/jarvis_browser
check "SearXNG is a service in the stack" grep -qE '^\s*searxng:' jarvis-core/docker-compose.yml
# The tasks pages moved under WORK (M48): the detail page is routes/work/tasks/[id].
check "task detail shows the report" grep -rqiE 'report' jarvis-web/src/routes/work/tasks
require_file evals/research_questions.yaml
require_file evals/research_eval.py
check_sh "scripted eval, offline (recorded search/fetch): a report per question, >= min distinct cited sources, links checked" \
    'timeout 900 python3 evals/research_eval.py --backend fixture --out .verify/research 2>&1 | tail -6'
# The live backend needs the operator's SearXNG, which `jarvisdev` cannot start
# on this host (no Docker socket — BLOCKERS.md). What is checked here is that
# the Scripted claim is REAL: the same command runs the same questions against
# a live engine the moment `SEARXNG_URL` is set, and refuses clearly rather
# than pretending when it is not. Running it is the operator's step, and
# `docs/verification.md` records it as Scripted.
check_sh "the live backend exists and says exactly what it needs" \
    'SEARXNG_URL= timeout 120 python3 evals/research_eval.py --backend live --out .verify/research-live 2>&1 | tail -4; test ${PIPESTATUS[0]} -eq 2'
check "the live claim is recorded as Scripted, not as passing" \
    grep -q "research.*Scripted\|Scripted.*research" docs/verification.md
check "and the reason it cannot run here is written down" \
    grep -qi 'searxng' BLOCKERS.md
check_sh "research unit tests" 'cd jarvis-core && python3 -m pytest tests/test_research.py tests/test_research_plan.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel. Its scenarios are gated on this
# milestone, so they run here for the first time.
check_sh "the live scenarios for research" \
    'LIVE_CAPABILITY=research bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
