#!/usr/bin/env bash
# M81 — It knows what it can do.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M81" "it knows what it can do"

A=jarvis-core/jarvis/llm/agent.py
check "the rules say software is a coding job and forbid 'only a butler'" grep -q "You are not" "$A"
check "a denied capability a tool provides is caught and sent back for the call" grep -q "def denied_capability" "$A"
check "the nudge is wired beside the claimed-action one" grep -q "denied_capability(" "$A"
check "the form of address is one line the persona cannot override" grep -q "def address_rule" "$A"
check "the persona no longer says 'Sir or ma'am'" bash -c "! grep -q \"Sir or ma'am\" jarvis-core/config/prompts/jarvis.txt"
check "the config and the registry carry llm.address" bash -c 'grep -q "^  address: Sir" jarvis-core/config/configuration.yaml && grep -q "key=\"llm.address\"" jarvis-core/jarvis/settings.py'
check_pytest "the agent suite: the guard's cases and the address rule" 'cd jarvis-core && python3 -m pytest tests/test_llm.py -q --timeout=120 --timeout-method=signal'
check_pytest "packaging: the persona file is where the model looks, the new key is read" 'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "persona or silently_ignored or env_var"'

verify_end
