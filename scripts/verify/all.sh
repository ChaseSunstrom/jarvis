#!/usr/bin/env bash
# scripts/verify/all.sh — what `make verify-all` runs.
#
# Runs every milestone check script (scripts/verify/mNN-*.sh) in order, never
# stops early unless asked, prints one table, and exits non-zero if ANY script
# failed. Each script's full output goes to .verify/<name>.log; the table shows
# the pass/fail counts and the names of the failed checks.
#
#   make verify-all                 everything
#   make verify-all ONLY=m03        one milestone (or ONLY="m03 m05", or
#                                   ONLY=live for the interaction suite alone)
#   make verify-all FAIL_FAST=1     stop at the first failing milestone
#   VERIFY_TIMEOUT=2400             per-script timeout, seconds (default 40 min)
#
# The runner has no opinion about what a milestone checks; that lives in each
# script. It has one opinion about honesty: there is no way for a script to
# report "skipped", and a timeout is a failure.

set -u
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"

LOGDIR="$ROOT/.verify"
mkdir -p "$LOGDIR"
TIMEOUT="${VERIFY_TIMEOUT:-2400}"

mapfile -t scripts < <(ls scripts/verify/m[0-9][0-9]-*.sh 2>/dev/null | sort)
# The live interaction suite runs once, at the end, over every scenario that is
# not gated on an unfinished milestone. The milestone scripts each run their own
# slice of it; this is the whole-system pass, and it is what `make verify-all`
# means by "somebody can talk to it".
scripts+=("scripts/verify/live_interaction.sh")
if [ "${#scripts[@]}" -eq 0 ]; then
    echo "no milestone scripts found under scripts/verify/ (expected mNN-*.sh)" >&2
    exit 1
fi

if [ -n "${ONLY:-}" ]; then
    selected=()
    for s in "${scripts[@]}"; do
        base=$(basename "$s")
        for want in $ONLY; do
            case "$base" in "$want"*) selected+=("$s") ;; esac
        done
    done
    scripts=("${selected[@]}")
    if [ "${#scripts[@]}" -eq 0 ]; then
        echo "ONLY=$ONLY matched no script" >&2
        exit 1
    fi
fi

printf 'verify-all: %d milestone script(s), logs in %s\n\n' "${#scripts[@]}" "$LOGDIR"
printf '%-34s %-14s %7s  %s\n' "milestone" "status" "time" "checks"
printf '%-34s %-14s %7s  %s\n' "---------" "------" "----" "------"

overall=0
t_start=$(date +%s)
for s in "${scripts[@]}"; do
    name=$(basename "$s" .sh)
    log="$LOGDIR/$name.log"
    t0=$(date +%s)
    if timeout --foreground "$TIMEOUT" bash "$s" >"$log" 2>&1; then
        status=PASS
    else
        rc=$?
        status=FAIL
        [ "$rc" -eq 124 ] && status="FAIL(timeout)"
        overall=1
    fi
    dt=$(( $(date +%s) - t0 ))
    summary=$(grep -E '^-- ' "$log" | tail -1 | sed 's/^-- [^:]*: //')
    [ -z "$summary" ] && summary="(no summary line — script crashed? see log)"
    printf '%-34s %-14s %6ss  %s\n' "$name" "$status" "$dt" "$summary"
    if [ "$status" != PASS ]; then
        grep -E '^  FAIL' "$log" | head -25 | sed 's/^  FAIL  /      ✗ /'
    fi
    if [ "$status" != PASS ] && [ "${FAIL_FAST:-0}" = 1 ]; then
        echo "FAIL_FAST=1: stopping."
        break
    fi
done

dt_all=$(( $(date +%s) - t_start ))
echo
if [ "$overall" -eq 0 ]; then
    printf 'VERIFY-ALL PASSED in %ss\n' "$dt_all"
else
    printf 'VERIFY-ALL FAILED in %ss — see %s/<milestone>.log\n' "$dt_all" "$LOGDIR"
fi
exit "$overall"
