#!/usr/bin/env bash
# M28 — the compose stack is a described, pinned, healthy runtime.
#
# Two halves, and the second is the one that matters: the file is checked
# statically (every image pinned, every service healthchecked and bounded), and
# then the stack is actually brought up and every container has to report
# healthy. A compose file that parses is not a runtime that runs — this host
# had `photon` restarting 2,699 times and `jarvis-web` reporting unhealthy for
# two days while every suite in the repository was green.
source "$(dirname "$0")/lib.sh"
verify_begin "M28" "the compose stack: pinned, healthy, described"
use_venv
CORE=jarvis-core/docker-compose.yml
ROOT=docker-compose.yml

require_cmd docker
check "docker is reachable (not just installed)" docker info
check_sh "both compose files parse" \
    'docker compose -f jarvis-core/docker-compose.yml config >/dev/null && docker compose -f docker-compose.yml config >/dev/null && echo ok'

# --- pinned ----------------------------------------------------------------
check_not "no image is on :latest" grep -nE '^\s*image:.*:latest' "$CORE" "$ROOT"
check "the three voice services are pinned to what the numbers were measured on" python3 -c '
import re
from pathlib import Path
text = Path("jarvis-core/docker-compose.yml").read_text()
want = {
    "rhasspy/wyoming-whisper": "3.5.0",
    "rhasspy/wyoming-piper": "2.3.1",
    "rhasspy/wyoming-openwakeword": "2.1.0",
}
for image, version in want.items():
    assert f"image: {image}:{version}" in text, f"{image} is not pinned to {version}"
print(", ".join(f"{k.split(chr(47))[-1]} {v}" for k, v in want.items()))
'

# --- healthchecked and bounded ---------------------------------------------
check "every service has a healthcheck" python3 -c '
import subprocess, sys, yaml
missing = []
for path in ("jarvis-core/docker-compose.yml", "docker-compose.yml"):
    raw = subprocess.run(
        ["docker", "compose", "-f", path, "--profile", "*", "config"],
        capture_output=True, text=True,
    )
    if raw.returncode:
        raw = subprocess.run(
            ["docker", "compose", "-f", path, "config"], capture_output=True, text=True, check=True
        )
    data = yaml.safe_load(raw.stdout) or {}
    for name, service in (data.get("services") or {}).items():
        # A one-shot init container exits; there is nothing to keep healthy.
        if name.endswith("-init") or name == "jarvis-sandbox":
            continue
        if not service.get("healthcheck"):
            missing.append(f"{path}:{name}")
assert not missing, f"no healthcheck on: {missing}"
print("every long-running service")
'
check "every service is bounded" python3 -c '
import subprocess, yaml
missing = []
for path in ("jarvis-core/docker-compose.yml", "docker-compose.yml"):
    raw = subprocess.run(
        ["docker", "compose", "-f", path, "config"], capture_output=True, text=True, check=True
    )
    data = yaml.safe_load(raw.stdout) or {}
    for name, service in (data.get("services") or {}).items():
        if name.endswith("-init"):
            continue
        if not service.get("mem_limit") and not (service.get("deploy") or {}).get("resources"):
            missing.append(f"{path}:{name}")
assert not missing, f"no memory limit on: {missing}"
print("mem_limit and cpus on every service")
'
check "the geocoder cannot restart-loop by default" grep -qE 'profiles: \[geocode\]' "$CORE"
check "and it says which region to fetch" grep -q 'PHOTON_REGION' "$CORE"
check "the console healthcheck does not use localhost" \
    grep -qE 'wget.*127\.0\.0\.1:8199/healthz' "$ROOT"
check_not "nothing probes localhost in a healthcheck" \
    grep -nE 'test:.*localhost' "$CORE" "$ROOT"
check "a code change can reach a running container" grep -q 'develop:' "$CORE"

# --- described --------------------------------------------------------------
require_file docs/RUNBOOK.md
for word in "up -d --wait" "down --volumes" "PHOTON_REGION" "tar czf" "compose watch"; do
    check "the runbook covers: $word" grep -qF -- "$word" docs/RUNBOOK.md
done
check "and where every piece of state lives" grep -q 'mosquitto-data' docs/RUNBOOK.md

# --- and it actually comes up ----------------------------------------------
check_sh "the stack comes up healthy from wherever it is now" \
    'timeout 600 docker compose -f jarvis-core/docker-compose.yml up -d --wait 2>&1 | tail -3 && timeout 600 docker compose -f docker-compose.yml up -d --wait 2>&1 | tail -3'
check_sh "no container is unhealthy or restarting" '
docker ps --format "{{.Names}}\t{{.Status}}" | tee /dev/stderr | grep -qiE "unhealthy|Restarting" && exit 1
echo "all healthy"'
verify_end
