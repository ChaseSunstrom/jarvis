#!/usr/bin/env bash
# M114 — every .env variable, set from the console, kept across a restart.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M114" "every .env variable, set from the console, kept"
use_venv
check_pytest "the catalogue names every variable .env.example does; overrides are kept in the store and applied at boot before configuration; secrets are masked; the file is never written" 'cd jarvis-core && python3 -m pytest tests/test_environment.py tests/test_packaging.py -q --timeout=120 --timeout-method=signal'
check "the image carries the catalogue" bash -c 'grep -q "\.env\.example" jarvis-core/Dockerfile && echo shipped'
ensure_web_build
run_playwright "SETTINGS › SYSTEM lists the variables with their why, masks a secret, SET keeps a value and says it applies on restart, CLEAR forgets it" e2e/environment.spec.ts
check "on the house: set a harmless variable, restart, read it back live; clear it, restart, gone" python3 scripts/verify/m114_env_probe.py
verify_end
