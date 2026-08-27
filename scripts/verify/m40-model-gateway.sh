#!/usr/bin/env bash
# M40 — one gateway, many providers, and a privacy guard. The four behaviours
# the brief names are proved against a real LiteLLM and a mock cloud provider:
# default local, override reaches it, an error falls back, and a tagged request
# is refused with that provider sitting there ready to answer.
source "$(dirname "$0")/lib.sh"
verify_begin "M40" "one gateway, many providers, and a privacy guard"
use_venv

require_file jarvis-core/gateway/config.yaml
require_file jarvis-core/gateway/privacy_guard.py
require_file testing/fixtures/mock_cloud.py
require_file testing/fixtures/gateway_probe.py

check "the gateway is in the stack" python3 -c '
import yaml
from pathlib import Path
compose = yaml.safe_load(Path("jarvis-core/docker-compose.yml").read_text())
service = compose["services"]["jarvis-gateway"]
assert "litellm" in service["image"], service["image"]
assert not service.get("profiles"), "the single internal endpoint is behind a profile"
print(service["image"])
'
check "local-only stays a complete configuration" python3 -c '
from pathlib import Path
text = Path("jarvis-core/gateway/config.yaml").read_text()
live = [line for line in text.splitlines()
        if line.strip().startswith("- model_name:") and not line.strip().startswith("#")]
assert len(live) >= 1, "no model is configured at all"
for line in live:
    assert "cloud" not in line.lower(), f"a cloud provider ships configured: {line.strip()}"
print(f"{len(live)} model(s) configured, none of them off-network")
'
check "routing, fallback and rate limits are policy in the config" python3 -c '
import yaml
from pathlib import Path
config = yaml.safe_load(Path("jarvis-core/gateway/config.yaml").read_text())
assert config["router_settings"]["fallbacks"], "no fallbacks"
assert any(m["litellm_params"].get("rpm") for m in config["model_list"]), "no rate limit"
print("fallbacks and per-model rpm")
'
check "the guard runs where it cannot be bypassed" python3 -c '
import yaml
from pathlib import Path
config = yaml.safe_load(Path("jarvis-core/gateway/config.yaml").read_text())
hook = (config.get("general_settings") or {}).get("custom_auth") or ""
assert hook.startswith("privacy_guard."), f"the guard is not wired: {hook!r}"
print(f"custom_auth: {hook}")
'
check "prompts are not logged by the proxy" \
    grep -q 'turn_off_message_logging: true' jarvis-core/gateway/config.yaml

check_pytest "both halves of the guard, and that they agree" 'cd jarvis-core && python3 -m pytest tests/test_privacy_gateway.py -q \
        --timeout=120 --timeout-method=signal'

# The four behaviours, against a real proxy and a provider that records what it
# was asked. "Refused" is proved by the mock having heard NOTHING, not by a log.
check_sh "default local, override to cloud, fallback on error, and a refusal" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 1800 python3 testing/fixtures/gateway_probe.py 2>&1 | tail -7'

check_sh "and the house still answers through it" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 1200 python3 -m testing.live.runner --full --only house-light-on,chat-context-retention \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -3'
verify_end
