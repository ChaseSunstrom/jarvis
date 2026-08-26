#!/usr/bin/env bash
# M72 — A coding job can create a repository.
#
# "Could not create the workspace /jarvis/workspaces: Permission denied":
# inside the image `~` is `/`. The config names /workspace, compose mounts the
# ../jarvis-workspace crossover there — on jarvis-core and on the config-init
# one-shot that chowns it for uid 10003 — and the container can write it.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M72" "a writable coding workspace"

check "the config names /workspace" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.config import load_config
assert str(load_config("jarvis-core/config")["code"]["workspace"]) == "/workspace"
print("code.workspace = /workspace")
'
check "jarvis-core mounts ../jarvis-workspace at /workspace" bash -c 'awk "/^  jarvis-core:/{f=1} /^  wyoming-/{f=0} f" jarvis-core/docker-compose.yml | grep -q -- "- ../jarvis-workspace:/workspace"'
check "the config-init one-shot mounts it and chowns it" bash -c 'awk "/^  jarvis-config-init:/{f=1} /^  jarvis-core:/{f=0} f" jarvis-core/docker-compose.yml | grep -q -- "- ../jarvis-workspace:/workspace" && awk "/^  jarvis-config-init:/{f=1} /^  jarvis-core:/{f=0} f" jarvis-core/docker-compose.yml | grep -q "chown.*/workspace"'
check "../jarvis-workspace exists in the checkout (its .gitkeep is tracked)" bash -c 'test -d jarvis-workspace && git ls-files jarvis-workspace/.gitkeep | grep -q gitkeep'
check_sh "packaging pins the three" \
    'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "workspace" 2>&1 | tail -1'
check "uid 10003 can write /workspace in the running core (rebuilt after M72)" bash -c \
    'cd jarvis-core && docker compose exec -T jarvis-core python -c "import os, pathlib; p = pathlib.Path(\"/workspace/.m72-probe\"); p.mkdir(exist_ok=True); (p / \"ok\").write_text(\"ok\"); import shutil; shutil.rmtree(p); print(\"wrote and removed /workspace/.m72-probe as uid\", os.geteuid())"'
check_sh "the code integration's tests still pass" \
    'cd jarvis-core && python3 -m pytest tests/test_code_workspace.py tests/test_code_repos.py -q --timeout=120 2>&1 | tail -1'
check "the image installs git (the step after the workspace)" grep -qE -- '--no-install-recommends.* git( |$)' jarvis-core/Dockerfile
check "git is on the running core's PATH (rebuilt after M72)" bash -c \
    'cd jarvis-core && docker compose exec -T jarvis-core git --version'
# The operator's own request, replayed: create_repository over the REST API.
# The probe repository m72-probe stays in the workspace and the listing on
# purpose — there is no service that removes one, and deleting the folder
# behind the running core's back would leave a listing with no disk. A second
# run finds it already there and checks the same thing: a .git on the host
# side of the crossover, at the path the core reports.
check "create_repository through the running core lands a git repository in jarvis-workspace/" bash -c '
TOKEN=$(grep "^JARVIS_TOKEN=" jarvis-core/.env | cut -d= -f2-)
OUT=$(curl -s -m 60 -X POST "http://127.0.0.1:8080/api/services/code/create_repository?return_response=1" \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d "{\"name\":\"m72-probe\",\"description\":\"M72 gate probe\"}")
echo "$OUT" | head -c 300; echo
echo "$OUT" | grep -q "\"status\": *\"ok\"" || echo "$OUT" | grep -qi "already\|taken\|exists" || exit 1
test -d jarvis-workspace/m72-probe/.git'

verify_end
