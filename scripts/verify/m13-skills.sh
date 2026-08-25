#!/usr/bin/env bash
# M13 — skills: drop-in SKILL.md folders in the open Agent Skills format,
# loaded with progressive disclosure, scripts never run outside the gate.
source "$(dirname "$0")/lib.sh"
verify_begin "M13" "skills: SKILL.md loader (Agent Skills format)"
use_venv
SK=jarvis-core/jarvis/integrations/skills/__init__.py

require_file "$SK"
check "loads SKILL.md files" grep -q 'SKILL.md' "$SK"
check "parses YAML frontmatter (name, description)" grep -qE 'frontmatter|^---|"---"' "$SK"
check "honours allowed-tools" grep -q 'allowed-tools' "$SK"
check "progressive disclosure: index in the prompt, body on demand" grep -qE 'use_skill|load_skill' "$SK"
# Stronger than the original check, which looked for the gate: the loader does
# not run a skill's scripts through the gated path either, it does not run them
# at all. So what is asserted is the absence of every execution primitive — a
# feature installed by dropping a file in a folder must not be able to execute
# one.
check_not "the loader cannot execute anything" \
    grep -nE '\b(subprocess|os\.system|os\.exec|popen|shell=True|exec\(|eval\()' "$SK"
check "and a test says so" \
    grep -q 'def test_scripts_beside_a_skill_are_listed_and_never_gated_off' jarvis-core/tests/test_skills.py
check "a skill cannot lower a tool's tier" \
    grep -q 'def test_a_gated_tool_stays_gated_whatever_a_skill_says' jarvis-core/tests/test_skills.py
check "skills: config key" grep -qE '^\s*skills:' jarvis-core/config/configuration.yaml
check "WS: jarvis/skills/list" grep -q '"jarvis/skills/list"' jarvis-core/jarvis/api/websocket.py
# Asked of the route table, not of the file's text: the routes are declared on
# `api_router`, which carries the `/api` prefix, so the literal string
# "/api/skills" appears nowhere in the source and a grep for it failed on a
# route that exists.
check "REST: /api/skills" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.api.rest import api_router
paths = {getattr(r, "path", "") for r in api_router.routes}
missing = [p for p in ("/api/skills", "/api/skills/{name}", "/api/skills/reload") if p not in paths]
assert not missing, f"no route for: {missing}"
'
check "the /api prefix is where the console looks" \
    grep -qE 'api_router *= *APIRouter\(prefix="/api"' jarvis-core/jarvis/api/rest.py
check "the console lists loaded skills" grep -qi 'skill' jarvis-web/src/lib/sections/Tools.svelte
check "mock backend serves jarvis/skills/list" grep -q 'jarvis/skills/list' tests/web/mock-ha.mjs
check_sh "an example skill ships in the format" \
    'f=$(ls jarvis-core/config/examples/skills/*/SKILL.md 2>/dev/null | head -1); [ -n "$f" ] && head -1 "$f" | grep -q "^---" && grep -qE "^name:" "$f" && grep -qE "^description:" "$f"'
require_file jarvis-core/docs/skills.md
check "docs name the open format" grep -qi 'agent skills' jarvis-core/docs/skills.md
require_file jarvis-core/tests/test_skills.py
for t in frontmatter invalid on_demand gated; do
    check "test_skills.py covers: $t" grep -qE "def test_[a-z_]*$t" jarvis-core/tests/test_skills.py
done
check_sh "skills tests" 'cd jarvis-core && python3 -m pytest tests/test_skills.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel. Its scenarios are gated on this
# milestone, so they run here for the first time.
check_sh "the live scenarios for skills" \
    'LIVE_CAPABILITY=skills bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
