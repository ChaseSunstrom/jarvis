#!/usr/bin/env bash
# scripts/verify/lib.sh — shared helpers for the per-milestone check scripts.
#
# Every milestone script sources this, declares its checks, and ends with
# `verify_end`. A failing check does NOT stop the script: the harness exists to
# say exactly which pieces of a milestone are missing, so every check runs and
# the summary names each failure. The exit status is non-zero if any failed.
#
# There is deliberately no "skip". A feature that cannot be verified on this
# host is a FAILURE that names what is missing (a JDK, a browser, a service),
# because a skipped check reads as green in a summary and green is the one
# thing this harness must never say by accident. If a check genuinely cannot
# apply, the milestone is wrong, not the check.
#
# Usage:
#   source "$(dirname "$0")/lib.sh"
#   verify_begin "M03" "web: redesign on the design system"
#   check     "tokens.css exists"        test -f jarvis-web/src/lib/styles/tokens.css
#   check_not "no raw hex in styles"     grep -rn '#[0-9a-f]\{6\}' jarvis-web/src --include='*.css'
#   check_sh  "vitest passes"            'cd jarvis-web && npx vitest run 2>&1 | tail -3'
#   verify_end

set -u
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"

_V_PASS=0
_V_FAIL=0
_V_FAILED=()
_V_ID=""
_V_TITLE=""

verify_begin() {
    _V_ID="$1"
    _V_TITLE="$2"
    # M91: a live slice run by this gate writes results-<gate>.json beside the shared file.
    export VERIFY_GATE="$(printf "%s" "$1" | tr "A-Z" "a-z")"
    printf '\n== %s — %s ==\n' "$_V_ID" "$_V_TITLE"
}

# On success the last line of the command's output is shown too, so a log can
# say "112 passed (1.4m)" rather than only "ok" — a count is a claim, a tick
# is not.
_v_ok() {
    _V_PASS=$((_V_PASS + 1))
    printf '  ok    %s\n' "$1"
    if [ -n "${2:-}" ]; then
        printf '%s\n' "$2" | grep -v '^\s*$' | tail -1 | cut -c1-160 | sed 's/^/        · /'
    fi
}

_v_fail() {
    _V_FAIL=$((_V_FAIL + 1))
    _V_FAILED+=("$1")
    printf '  FAIL  %s\n' "$1"
    if [ -n "${2:-}" ]; then
        printf '%s\n' "$2" | head -40 | sed 's/^/        | /'
    fi
}

# check "<label>" <command> [args...] — passes when the command exits 0.
check() {
    local label="$1"
    shift
    local out
    if out=$("$@" 2>&1); then
        _v_ok "$label" "$out"
    else
        _v_fail "$label" "$out"
    fi
}

# check_not "<label>" <command> [args...] — passes when the command exits
# NON-zero. The natural shape for "grep must find nothing".
check_not() {
    local label="$1"
    shift
    local out
    if out=$("$@" 2>&1); then
        _v_fail "$label" "$out"
    else
        _v_ok "$label"
    fi
}

# check_sh "<label>" '<shell snippet>' — passes when the snippet exits 0.
# For pipelines and cd's. pipefail is on so a failing left side fails the check.
check_sh() {
    local label="$1"
    local snippet="$2"
    local out
    if out=$(bash -o pipefail -c "$snippet" 2>&1); then
        _v_ok "$label" "$out"
    else
        _v_fail "$label" "$out"
    fi
}

# check_pytest "<label>" '<pytest command>' [allowed_skips]
#
# M91: a gate cannot pass on a skip. `check_sh` read only pytest's exit
# status, so a suite that skipped itself wholesale — the desktop e2e when
# the harness will not import, one `pytest.skip` inside an "Automated" row —
# passed as green (the quality audit, 27 Aug 2026). This reads the summary
# line and fails on `failed`, `error`, `no tests ran`, and on `skipped`
# beyond the number the gate says it expects (default none). The summary is
# what is printed, so the count is on the record.
check_pytest() {
    local label="$1"
    local snippet="$2"
    local allowed="${3:-0}"
    local out summary skipped
    out=$(bash -o pipefail -c "$snippet" 2>&1)
    local status=$?
    summary=$(printf '%s\n' "$out" | grep -E "^(=+ )?([0-9]+ (passed|failed|error|skipped|deselected|xfailed|xpassed|warning)s?(, )?)+|no tests ran|^ERROR" | tail -1)
    if [ -z "$summary" ]; then
        _v_fail "$label" "no pytest summary line in the output:
$(printf '%s\n' "$out" | tail -5)"
        return
    fi
    if [ $status -ne 0 ] || printf '%s' "$summary" | grep -qE "failed|error|no tests ran|^ERROR"; then
        _v_fail "$label" "$summary"
        return
    fi
    skipped=$(printf '%s' "$summary" | grep -oE "[0-9]+ skipped" | grep -oE "[0-9]+" || echo 0)
    if [ "${skipped:-0}" -gt "$allowed" ]; then
        _v_fail "$label" "$summary — $skipped skipped, $allowed allowed: a skip is not a pass"
        return
    fi
    _v_ok "$label" "$summary"
}

# check_sh_not "<label>" '<shell snippet>' — passes when the snippet exits non-zero.
check_sh_not() {
    local label="$1"
    local snippet="$2"
    local out
    if out=$(bash -o pipefail -c "$snippet" 2>&1); then
        _v_fail "$label" "$out"
    else
        _v_ok "$label"
    fi
}

require_file() { check "file exists: $1" test -f "$1"; }
require_dir()  { check "directory exists: $1" test -d "$1"; }
require_exec() { check "executable: $1" test -x "$1"; }
require_cmd()  { check "command on PATH: $1" command -v "$1"; }

# A file must contain a pattern (grep -E).
require_grep() {
    local label="$1" pattern="$2" file="$3"
    check "$label" grep -qE -- "$pattern" "$file"
}

# Put the repo venv first on PATH so `python3 -m ...` (the Makefile's
# convention) resolves to it. Returns 1, and fails a check, when it is missing.
use_venv() {
    if [ -x "$ROOT/.venv/bin/python" ]; then
        export VIRTUAL_ENV="$ROOT/.venv"
        export PATH="$ROOT/.venv/bin:$PATH"
        _v_ok "repo venv on PATH (.venv)"
        return 0
    fi
    _v_fail ".venv is missing — see CLAUDE.md, Environment" ""
    return 1
}

# ~/.local/bin holds user-installed tools (gh, and later a JDK/SDK shim);
# ~/.profile adds it on login but a non-login shell does not.
use_local_bin() {
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) export PATH="$HOME/.local/bin:$PATH" ;;
    esac
}

verify_end() {
    printf -- '-- %s: %d passed, %d failed\n' "$_V_ID" "$_V_PASS" "$_V_FAIL"
    if [ "$_V_FAIL" -gt 0 ]; then
        printf '   failed:\n'
        printf '   - %s\n' "${_V_FAILED[@]}"
        exit 1
    fi
    exit 0
}

# ---------------------------------------------------------------------------
# Web helpers. The Playwright suite must never point at the live HUD on :8199,
# so verify runs use E2E_PORT (playwright.config.ts honours it).
export E2E_PORT="${E2E_PORT:-8299}"

ensure_web_deps() {
    check "jarvis-web/node_modules present (cd jarvis-web && npm ci)" test -d jarvis-web/node_modules
}

# Build once per run: rebuild only when a source is newer than build/index.js.
ensure_web_build() {
    local newest=""
    if [ -f jarvis-web/build/index.js ]; then
        newest=$(find jarvis-web/src jarvis-web/static jarvis-web/package.json jarvis-web/svelte.config.js \
            -type f -newer jarvis-web/build/index.js 2>/dev/null | head -1)
    fi
    if [ ! -f jarvis-web/build/index.js ] || [ -n "$newest" ]; then
        check_sh "jarvis-web builds (npm run build)" 'cd jarvis-web && npm run build 2>&1 | tail -15'
    else
        _v_ok "jarvis-web build is current"
    fi
}

# run_playwright "<label>" [spec ...] — the whole suite when no spec is given.
run_playwright() {
    local label="$1"
    shift
    check_sh "$label" "cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test $* 2>&1 | tail -30"
}

# A generated file carries this marker; the scanners exempt such files.
GENERATED_MARK='@generated from design/tokens.json'
require_generated() {
    check "generated: $1" grep -qF "$GENERATED_MARK" "$1"
}
