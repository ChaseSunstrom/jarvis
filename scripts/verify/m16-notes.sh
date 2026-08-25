#!/usr/bin/env bash
# M16 — notes: first-class markdown notes with tags, full-text search and
# wiki-links, reachable from every surface and by voice, and an agent tool.
source "$(dirname "$0")/lib.sh"
verify_begin "M16" "notes: first-class, everywhere, an agent tool"
use_venv
N=jarvis-core/jarvis/integrations/notes/__init__.py

require_file "$N"
check "notes are markdown files with frontmatter" grep -qiE 'frontmatter|^---' "$N"
check "full-text search (SQLite FTS5)" grep -qiE 'fts5' "$N"
check "wiki links resolved" grep -qE '\[\[' "$N"
check "tags" grep -qi 'tags' "$N"
for cmd in jarvis/notes/list jarvis/notes/create jarvis/notes/append jarvis/notes/search; do
    check "WS command $cmd" grep -q "\"$cmd\"" jarvis-core/jarvis/api/websocket.py
done
# Asked of the route table: the routes are declared on `api_router`, which
# carries the `/api` prefix, so the literal "/api/notes" is nowhere in the
# source and a grep for it failed on routes that exist.
check "REST: /api/notes" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.api.rest import api_router
paths = {getattr(r, "path", "") for r in api_router.routes}
missing = [p for p in ("/api/notes", "/api/notes/{note_id}", "/api/notes/{note_id}/append") if p not in paths]
assert not missing, f"no route for: {missing}"
'
for tool in note_create note_append note_search; do
    check "tool $tool" grep -q "\"$tool\"" "$N"
done
# `note_search` reads one whole note when it is given an id. Three tools rather
# than four because every tool costs context on every turn — the ceiling is
# `jarvis-core/tests/test_prompt_budget.py`, and adding these went through it.
check "note_search reads one note when it is named" \
    grep -q 'args.get("id") or args.get("title")' "$N"
check "research saves its report as a note" grep -qE 'note' jarvis-core/jarvis/integrations/research/__init__.py
check "voice intent: note that …" grep -qiE 'note that|make a note' evals/routing.py
require_file jarvis-web/src/lib/sections/Notes.svelte
check "mock backend serves jarvis/notes/*" grep -q 'jarvis/notes/' tests/web/mock-ha.mjs
# The phone reaches notes the way it reaches every other management screen:
# the console's own page, in its authenticated WebView. That is the app's
# architecture — native only for what a web page cannot do (the microphone,
# permissions, the token) — and a second Kotlin notes client would be an
# untested parallel path to the same API. `console_parity_test.py` is what
# keeps the tab honest: it fails if the phone offers a section the console
# does not have, or misses one it does.
check "the phone offers NOTES, as a console section" \
    grep -qE 'NOTES\("NOTES", "/notes"\)' android-app/app/src/main/kotlin/ai/jarvis/app/ui/ConsoleTab.kt
check_sh "and the phone's nav still matches the console's" \
    'python3 android-app/tools/console_parity_test.py 2>&1 | tail -1'
check "the console route exists for it to open" test -f jarvis-web/src/lib/sections/Notes.svelte
check "desktop reaches the notes API" grep -rq 'jarvis/notes\|api/notes' jarvis-desktop/jarvis_desktop
require_file jarvis-core/tests/test_notes.py
require_file jarvis-core/tests/test_notes_voice.py
for t in create update append delete search tag_filter link; do
    check "test_notes.py covers: $t" grep -qE "def test_[a-z_]*$t" jarvis-core/tests/test_notes.py
done
check_sh "notes tests (API CRUD, search, tag filter, voice-intent fixture)" \
    'cd jarvis-core && python3 -m pytest tests/test_notes.py tests/test_notes_voice.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check "routing eval still passes" make -s eval-routing
require_file jarvis-web/e2e/notes.spec.ts
ensure_web_deps
ensure_web_build
run_playwright "notes UI e2e" e2e/notes.spec.ts
# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel. Its scenarios are gated on this
# milestone, so they run here for the first time.
check_sh "the live scenarios for notes" \
    'LIVE_CAPABILITY=notes bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
