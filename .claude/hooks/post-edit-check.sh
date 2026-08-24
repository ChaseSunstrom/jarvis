#!/usr/bin/env bash
# Claude Code post-edit check — the PostToolUse hook on Write|Edit wired in
# .claude/settings.json. Reads the tool's JSON payload on stdin and checks the
# one file it just wrote the way CI would, so the finding arrives while the
# edit is still in hand rather than in a red build later:
#
#   1. the mutation-stub marker scan from ci.yml's `static` job (same regex,
#      same suffix set). A deliberately weakened stub once reached main because
#      nobody re-ran the tests before committing; the marker is banned outright.
#   2. `ruff check` on a .py with the repo's defect-only ruleset (ruff.toml).
#      Never a formatter, never --fix.
#   3. `bash -n` on a .sh, as the `static` job does for every script.
#
# Exit 2 hands stderr back to the model as feedback; exit 0 is silent. A file
# outside the repository, or under a vendored or generated tree, is ignored.
#
# The marker patterns are written with one bracketed letter so this file does
# not itself contain the strings it bans: the CI scan reads every .sh in the
# tree, and a scanner that trips on its own definition is a scanner somebody
# adds to the skip list.

set -u

file=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null || true)
[ -n "$file" ] && [ -f "$file" ] || exit 0

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
case "$file" in
    "$root"/*) rel=${file#"$root"/} ;;
    *) exit 0 ;;
esac
case "/$rel/" in
    */node_modules/*|*/.venv/*|*/venv/*|*/.git/*|*/build/*|*/.svelte-kit/*|*/__pycache__/*) exit 0 ;;
esac

fail=0
report=""

# 1. Marker scan. ci.yml exempts exactly one file: itself, which names the
#    marker in order to ban it.
case "$rel" in
    *.py|*.kt|*.kts|*.ts|*.js|*.svelte|*.sh|*.yml|*.yaml)
        if [ "$rel" != ".github/workflows/ci.yml" ]; then
            hits=$(grep -nIiE '\bM[U]TANT\b|\bDELIBERATELY BR[O]KEN\b' -- "$file" || true)
            if [ -n "$hits" ]; then
                fail=1
                report+="Mutation-stub marker in $rel — CI's static job fails the build on this:"$'\n'"$hits"$'\n'
            fi
        fi ;;
esac

# 2. ruff, the venv's pinned version, config discovered upward from the file
#    (ruff.toml at the root). --force-exclude honours extend-exclude for an
#    explicitly named file, so a vendored tree is skipped here exactly as
#    `ruff check .` skips it. A missing ruff is reported rather than skipped:
#    a check that silently does nothing is worse than no check.
case "$rel" in
    *.py)
        ruff="$root/.venv/bin/ruff"
        if [ -x "$ruff" ]; then
            if ! out=$("$ruff" check --no-fix --force-exclude -- "$file" 2>&1); then
                fail=1
                report+="ruff check $rel:"$'\n'"$out"$'\n'
            fi
        else
            fail=1
            report+="ruff not run: $ruff is missing. Recreate the venv (CLAUDE.md, Environment)."$'\n'
        fi ;;
esac

# 3. Shell syntax.
case "$rel" in
    *.sh)
        if ! out=$(bash -n -- "$file" 2>&1); then
            fail=1
            report+="bash -n $rel:"$'\n'"$out"$'\n'
        fi ;;
esac

if [ "$fail" -ne 0 ]; then
    printf '%s' "$report" >&2
    exit 2
fi
exit 0
