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
check "skill scripts only run through the gated path (Tier 3 / sandbox)" grep -qE 'TIER_APPROVAL|run_command|sandbox' "$SK"
check "skills: config key" grep -qE '^\s*skills:' jarvis-core/config/configuration.yaml
check "WS: jarvis/skills/list" grep -q '"jarvis/skills/list"' jarvis-core/jarvis/api/websocket.py
check "REST: /api/skills" grep -q '/api/skills' jarvis-core/jarvis/api/rest.py
check "the console lists loaded skills" grep -qi 'skill' jarvis-web/src/routes/tools/+page.svelte
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
verify_end
