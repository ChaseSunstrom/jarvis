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
check "REST: /api/notes" grep -q '/api/notes' jarvis-core/jarvis/api/rest.py
for tool in note_create note_append note_read note_search; do
    check "tool $tool" grep -q "\"$tool\"" "$N"
done
check "research saves its report as a note" grep -qE 'note' jarvis-core/jarvis/integrations/research/__init__.py
check "voice intent: note that …" grep -qiE 'note that|make a note' evals/routing.py
require_file jarvis-web/src/routes/notes/+page.svelte
check "mock backend serves jarvis/notes/*" grep -q 'jarvis/notes/' tests/web/mock-ha.mjs
check "Android reaches the notes API" grep -rq 'api/notes\|jarvis/notes' android-app/app/src/main/kotlin
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
verify_end
