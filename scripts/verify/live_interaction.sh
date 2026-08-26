#!/usr/bin/env bash
# scripts/verify/live_interaction.sh — talk to Jarvis and see what happens.
#
# Two modes, and the difference between them is the whole point:
#
#   --implemented-only   every scenario that is NOT gated on an unfinished
#                        milestone. Must exit 0. Appended to every milestone's
#                        verification from M24 onward, so a capability does not
#                        count as done until it also works out loud.
#   --full               everything, including the scenarios written against
#                        capabilities that do not exist yet, plus the
#                        thresholds from the brief (intent ≥ 95 %, WER ≤ 10 %,
#                        routing ≥ 90 %, median round trip ≤ 2 s). Required at
#                        final integration (M23) and nowhere else.
#
# What it talks to: the containers this host actually runs. `docker compose
# up -d --wait` is the first step, every container must be healthy before a
# word is spoken, and the run fails at the end if any of them logged an
# ERROR-level record while it was going on — the two failures that survived two
# days on this host were both of that shape and no assertion in this repository
# could see them. `--target harness` opts out, for a machine with no stack.
#
# Nothing is faked, which is why this is slow (minutes, not seconds) and why it
# proves something the rest of the suite cannot.
#
#   bash scripts/verify/live_interaction.sh --implemented-only
#   bash scripts/verify/live_interaction.sh --full --report
#   LIVE_ONLY=house-light-on bash scripts/verify/live_interaction.sh --implemented-only
source "$(dirname "$0")/lib.sh"

MODE="--implemented-only"
REPORT=""
for arg in "$@"; do
    case "$arg" in
        --implemented-only|--full) MODE="$arg" ;;
        --report) REPORT="1" ;;
        *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

verify_begin "LIVE" "interaction suite (${MODE#--})"
use_venv

# The rig speaks to the model server and the voice services the operator runs.
# `.env` is where their addresses live, and it is gitignored — so this is read
# rather than assumed, and its absence is a failure with a name.
# A git worktree must never bring the stack up: its compose project is the
# production project (the name comes from the directory), and twice in one
# night an agent's worktree re-created the house's containers from its own
# checkout. `.git` is a file in a worktree and a directory in the main one.
if [ -f "$ROOT/.git" ] && [ -z "${JARVIS_ALLOW_WORKTREE_COMPOSE:-}" ]; then
    echo "live_interaction.sh: refusing to run from a git worktree ($ROOT) — it would" >&2
    echo "re-create the production containers from this checkout. Run it from the main checkout." >&2
    exit 3
fi
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
    _v_ok "read .env (LLM_URL, LLM_MODEL)"
else
    _v_fail ".env is missing — the live rig needs LLM_URL and LLM_MODEL" ""
fi

# The stack, first, and healthy — `up -d --wait` returns non-zero if any
# service's healthcheck never passes, so this single line is also the
# "no container is unhealthy at the start" gate.
LIVE_TARGET="${LIVE_TARGET:-stack}"
if [ "$LIVE_TARGET" = "stack" ]; then
    check_sh "the stack is up and every container is healthy" '
docker compose -f jarvis-core/docker-compose.yml up -d --wait >/dev/null 2>&1 || {
    docker compose -f jarvis-core/docker-compose.yml ps; exit 1; }
docker compose -f docker-compose.yml up -d --wait >/dev/null 2>&1 || {
    docker compose -f docker-compose.yml ps; exit 1; }
docker ps --format "{{.Names}} {{.Status}}" | sed "s/^/  /"'
fi

check "the synthetic user has a voice" python3 testing/live/fetch_voice.py --check
check "piper-tts is installed" python3 -c 'import piper'
check "the scenario suite parses" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
scenarios = load_all()
gated = [s for s in scenarios if s.gated]
print(f"{len(scenarios)} scenario(s), {len(gated)} gated")
'
check "every gated scenario names a real milestone" python3 -c '
import re, sys; sys.path.insert(0, ".")
from pathlib import Path
from testing.live.scenario import load_all
known = set(re.findall(r"\*\*(M[0-9]{2}) ", Path("MILESTONES.md").read_text()))
bad = [(s.name, s.gated_on) for s in load_all() if s.gated and s.gated_on not in known]
assert not bad, f"gated on milestones that do not exist: {bad}"
'
# Every capability the milestones promise must have at least one scenario, or
# the suite grows a hole exactly where a capability was skipped.
check "every capability has a scenario" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
want = {"house", "answer", "voice", "conversation", "task", "memory", "notes",
        "research", "coding", "subagents", "interactions", "safety", "skills",
        "resilience"}
have = {s.capability for s in load_all()}
missing = sorted(want - have)
assert not missing, f"no live scenario covers: {missing}"
print(f"{len(have)} capabilities covered")
'

ARGS=("$MODE" "--target" "$LIVE_TARGET")
[ -n "${LIVE_ONLY:-}" ] && ARGS+=("--only" "$LIVE_ONLY")
# One capability's scenarios. A milestone that builds a capability runs exactly
# the scenarios written for it — including the ones gated on that milestone,
# which is why those calls pass --full.
[ -n "${LIVE_CAPABILITY:-}" ] && ARGS+=("--capability" "$LIVE_CAPABILITY")
[ -n "${LIVE_NO_BROWSER:-}" ] && ARGS+=("--no-browser")
[ -n "$REPORT" ] && ARGS+=("--write-report")

check_sh "the scenarios run against a real Jarvis" \
    "timeout ${LIVE_TIMEOUT:-5400} python3 -m testing.live.runner ${ARGS[*]} 2>&1 | grep -v pthread_setaffinity | tail -25"

verify_end
