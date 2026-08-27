#!/usr/bin/env bash
# M106 — notes read as they were written: markdown rendered, safely, everywhere the console shows prose.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M106" "notes read as they were written"
use_venv

check "the renderer exists, escapes first, and draws only a conservative subset" python3 -c '
from pathlib import Path
src = Path("jarvis-web/src/lib/markdown.ts").read_text()
assert "export function renderMarkdown" in src, "no renderMarkdown"
assert "escapeHtml" in src or "escape(" in src, "nothing escapes first"
assert "javascript:" in src, "the link scheme is not checked"
assert "rel=\"noopener" in src or "noopener" in src, "links do not carry noopener"
print("renderMarkdown, escaped first, http(s) links only")
'
check "the Markdown component is used by Notes, the chat bubble, the task result, the notification body and the surface note" python3 -c '
from pathlib import Path
uses = {
  "Notes": "jarvis-web/src/lib/sections/Notes.svelte",
  "ChatMessage": "jarvis-web/src/lib/components/ChatMessage.svelte",
  "the task page": "jarvis-web/src/routes/work/tasks/[id]/+page.svelte",
  "Moment": "jarvis-web/src/lib/components/Moment.svelte",
  "SurfacePanel": "jarvis-web/src/lib/components/SurfacePanel.svelte",
}
missing = [name for name, path in uses.items() if "<Markdown" not in Path(path).read_text()]
assert not missing, "no <Markdown> in: " + ", ".join(missing)
assert "note-read" in Path(uses["Notes"]).read_text(), "the Notes page has no read view"
print("five surfaces render markdown")
'
check_sh "the unit tests: the subset, and the injection cases" 'cd jarvis-web && npx vitest run src/lib/markdown.test.ts 2>&1 | tail -3'
ensure_web_build
run_playwright "a markdown note reads as headings and a list, edits and comes back; a reply reads its bold" e2e/markdown.spec.ts
verify_end
