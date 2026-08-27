#!/usr/bin/env bash
# M108 — browse and install skills and MCP servers from the registries, in the UI.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M108" "browse and install from the registries"
use_venv

check "two registry readers exist, read only their own hosts, and refuse anything that runs on this host" bash -c 'cd jarvis-core && python3 -c "
from jarvis.integrations.extensions import registries
assert hasattr(registries, \"read_github_skills\") and hasattr(registries, \"read_mcp_registry\")
assert registries.ALLOWED_HOSTS >= {\"api.github.com\", \"raw.githubusercontent.com\", \"registry.modelcontextprotocol.io\"}, registries.ALLOWED_HOSTS
print(\"readers:\", sorted(registries.ALLOWED_HOSTS))
"'
check_pytest "the readers, on recorded replies: entries, refusals, a hostile description" 'cd jarvis-core && python3 -m pytest tests/test_registries.py -q --timeout=120 --timeout-method=signal'
check "configuration.yaml names the two registries as sources the operator chose" python3 -c '
import yaml
from pathlib import Path
from urllib.parse import urlparse
loader = yaml.SafeLoader
loader.add_multi_constructor("!", lambda l, s, n: None)
cfg = yaml.load(Path("jarvis-core/config/configuration.yaml").read_text(), Loader=loader)
sources = ((cfg.get("extensions") or {}).get("catalog") or {}).get("sources") or []
hosts = {urlparse(str(s.get("url") or "")).hostname for s in sources}
assert "api.github.com" in hosts and "registry.modelcontextprotocol.io" in hosts, hosts
print(len(sources), "sources:", ", ".join(s.get("name", "?") for s in sources))
'
ensure_web_build
run_playwright "the catalogue lists the registries entries, searches them, and INSTALL is a held action that says what it writes" e2e/registries.spec.ts
check "on the house: both registries answer, and the catalogue lists a skill from Anthropic and a server from the MCP registry" python3 scripts/verify/m108_registry_probe.py
verify_end
