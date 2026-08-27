#!/usr/bin/env bash
# M65 — Something to browse.
#
# The operator's report, verbatim: "I cant browse the tools/mcp servers from
# the settings, no way to browse". Two things were true: the browse control
# sat inside a fold on the tools page, and what it opened was empty — M47
# ships no catalogue source, because a default list of URLs would hand the
# supply chain to whoever owns them. M65 keeps that refusal and ships the one
# source that is not a URL: the package's own skill folders, read from this
# machine through the same file:// path an operator's folder takes; puts the
# catalogue above the folds, filtered by the page's one search, each entry
# saying INSTALLED or offering one INSTALL through the existing plan-then-
# approve flow; and says in one line how an MCP server arrives (by URL, in the
# fold; a stdio program only in configuration.yaml). Every piece is checked
# here: the index against the SKILL.md files beside it, the source wiring,
# browse on a fresh Jarvis, the placement read off the Svelte, the mock, the
# inventory, the docs, the suites, the e2e, and the pictures.
#
# No `set -e`: lib.sh's contract is that a failing check does not stop the
# run, so the summary names every missing piece rather than the first.
. "$(dirname "$0")/lib.sh"
verify_begin "M65" "something to browse"
use_venv

require_file jarvis-core/jarvis/integrations/skills/bundled/index.json
require_file jarvis-web/src/lib/components/Catalogue.svelte
require_file jarvis-web/e2e/catalogue.spec.ts

# --- the index ---------------------------------------------------------------
check "every shipped entry parses through the hostile-input parser, names a shipped folder, and stays inside the catalogue" python3 -c '
import json, sys
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import bundled_source, entry_from_raw, read_local_catalog
from jarvis.integrations.skills import BUNDLED_ROOT
raw = json.loads((BUNDLED_ROOT / "index.json").read_text())["entries"]
assert raw, "the shipped index is empty"
source = bundled_source()
folders = sorted(p.name for p in BUNDLED_ROOT.iterdir() if (p / "SKILL.md").is_file())
ids = sorted(entry_from_raw(r, source).id for r in raw)
assert ids == folders, f"index {ids} vs folders {folders}"
entries = read_local_catalog(BUNDLED_ROOT, source)
assert len(entries) == len(raw), "an entry was skipped on the way in"
for e in entries:
    assert e.url == (BUNDLED_ROOT / e.id).as_uri(), e.url
    assert e.ref and e.ref.lower() != "latest", f"{e.id} is unpinned"
print(f"{len(entries)} entries, all inside {BUNDLED_ROOT.name}/: " + ", ".join(ids))
'
check "the index agrees with each SKILL.md beside it: description, version, author, permissions" python3 -c '
import json, sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.registry import skill_manifest
from jarvis.integrations.skills import BUNDLED_ROOT, parse_skill_md
for raw in json.loads((BUNDLED_ROOT / "index.json").read_text())["entries"]:
    path = BUNDLED_ROOT / raw["id"] / "SKILL.md"
    skill = parse_skill_md(path.read_text(), path)
    assert raw["description"] == skill.description, raw["id"]
    assert raw["version"] == skill.version, raw["id"]
    assert raw.get("author") == skill.metadata.get("author"), raw["id"]
    assert sorted(raw["permissions"]) == sorted(skill_manifest(skill).permissions), raw["id"]
print("held equal, entry for entry")
'

# --- the source wiring -------------------------------------------------------
check "the bundled source is the package folder, resolved from the package, and the M47 refusal stands" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions import _build_catalog
from jarvis.integrations.extensions.catalog import DEFAULT_SOURCES, bundled_source
from jarvis.integrations.skills import BUNDLED_ROOT
assert DEFAULT_SOURCES == (), "a default REMOTE list appeared"
s = bundled_source()
assert s.url == BUNDLED_ROOT.as_uri() and s.url.startswith("file://"), s.url
fresh = _build_catalog(None)
assert list(fresh.sources) == ["bundled"], list(fresh.sources)
off = _build_catalog({"sources": [{"name": "bundled", "url": "file:///nowhere", "enabled": False}]})
assert off.sources["bundled"].enabled is False and off.sources["bundled"].url == "file:///nowhere"
print(f"bundled -> {s.url}; DEFAULT_SOURCES == (); the operator\x27s own `bundled` line wins")
'
check "browse on a fresh Jarvis answers the shipped skills, INSTALLED, with no error" python3 -c '
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup
from jarvis.integrations.skills import BUNDLED_ROOT, SkillStore

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        jarvis = Jarvis(config_dir=tmp)
        store = SkillStore(Path(tmp) / "skills", bundled_root=BUNDLED_ROOT)
        store.load()
        jarvis.data["skills"] = store
        await async_setup(jarvis, None)
        out = await jarvis.services.async_call("extensions", "browse", {}, blocking=True, return_response=True)
        assert "error" not in out, out
        assert out["sources"] == ["bundled"] and out["errors"] == [], out
        ids = sorted(e["id"] for e in out["entries"])
        assert ids == sorted(store.skills), (ids, sorted(store.skills))
        assert all(e["installed"] for e in out["entries"]), out["entries"]
        print(f"{len(ids)} entries from bundled, every one installed: " + ", ".join(ids))
asyncio.run(main())
'
check "the shipped configuration says what bundled is, and how to turn it off" python3 -c '
from pathlib import Path
text = Path("jarvis-core/config/configuration.yaml").read_text()
block = text[text.index("# Extensions — one index"):text.index("# MCP — anybody")]
for needle in ("`bundled`", "REPOSITORY\x27S OWN CODE", "enabled: false", "No shipped list of REMOTE sources".lower()):
    assert needle.lower() in block.lower(), f"configuration.yaml does not say: {needle}"
print("said, beside the key")
'

# --- the surface -------------------------------------------------------------
check "the catalogue is mounted above the first fold on the tools page, and takes the one query" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/sections/Tools.svelte").read_text()
# Comments may name the hook (a CSS comment does); markup counts.
markup = re.sub(r"/\*.*?\*/|<!--.*?-->", "", src, flags=re.S)
mount = markup.index("<Catalogue")
first_fold = markup.index("{@render fold(")
assert mount < first_fold, "the catalogue is not above the folds"
tag = markup[mount:markup.index("/>", mount)]
for prop in ("{conn}", "{query}", "offline=", "onaddmcp=", "oninstalled="):
    assert prop in tag, f"<Catalogue> is missing {prop}"
assert markup.count("data-jv-filter") == 1, "a second search box"
prose = " ".join(src.split())
assert "NEW SKILL" in prose and "stays the page\x27s one filled primary" in prose, "the primary decision is not written down"
print("above the folds, on the one query, the primary decided in a comment")
'
check "the catalogue has the four states, the MCP line, and no filled control at rest" python3 -c '
import re
from pathlib import Path
src = Path("jarvis-web/src/lib/components/Catalogue.svelte").read_text()
# Prose wraps across lines in the source and the browser collapses it; the
# sentence is checked the way a person reads it, not the way it is indented.
prose = " ".join(src.split())
for needed, why in (("SkeletonRows", "loading"), ("catalogue-empty", "empty"), ("catalogue-error", "error"), ("catalogue-offline", "offline")):
    assert needed in src, f"no {why} state"
assert "added by URL in the MCP servers fold below" in prose, "the MCP line is missing"
assert "allow_stdio" in prose and "cannot offer a server that runs on this machine" in prose, "stdio is not explained"
assert "catalogue-add-mcp" in src, "the MCP line has no control"
assert "catalog-installed-" in src and "catalog-install-" in src, "INSTALLED / INSTALL are not both drawn"
markup = re.sub(r"<!--.*?-->", "", src, flags=re.S)
body = markup[:markup.index("<Dialog")]
assert "variant=\"primary\"" not in body, "a filled control on the catalogue at rest"
dialog = markup[markup.index("<Dialog"):]
assert dialog.count("variant=\"primary\"") == 1, "the approval dialog has one primary"
assert "data-jv-row" in src, "entries are not rows the inventory can measure"
print("loading, empty, error, offline; INSTALLED or one ghost INSTALL; the MCP line with its control")
'
check_not "one way to the catalogue: the Extensions fold no longer has a browse button" grep -q "extensions-browse" jarvis-web/src/lib/components/Extensions.svelte
check "the mock answers browse as the server does: installed, sources, errors" python3 -c '
from pathlib import Path
src = Path("tests/web/mock-ha.mjs").read_text()
case = src[src.index("case \x27jarvis/extensions/browse\x27"):src.index("case \x27jarvis/test/catalog_mode\x27")]
for key in ("installed:", "sources: [\x27bundled\x27", "errors:", "no catalog source is configured"):
    assert key in case, f"the mock\x27s browse answer lacks {key}"
assert "jarvis/test/extensions_reset" in src
print("installed, sources, errors, and a reset")
'
check "the inventory row allows the catalogue's one control at rest and names NEW SKILL as the lit one" python3 -c '
from pathlib import Path
doc = Path("docs/UI_MIGRATION.md").read_text()
row = [l for l in doc.splitlines() if l.startswith("| SETTINGS › Tools |")][0]
cells = [c.strip() for c in row.strip("|").split("|")]
assert cells[3] == "2" and cells[4] == "—" and cells[5] == "1", cells
assert "catalogue" in cells[6] and "NEW SKILL" in cells[6], cells[6]
assert "the catalogue first" in doc, "the page map does not say where the catalogue is"
print("per row at rest 2, primary —, search 1; the notes say why")
'
check "the docs carry it: DEVIATIONS §21, the threat model, clients.md, configuration.md, MILESTONES, CHANGELOG, verification" python3 -c '
from pathlib import Path
checks = {
    "DEVIATIONS.md": "## 21. The catalogue ships one source, and it is not a URL (M65)",
    "docs/THREAT_MODEL.md": "`bundled`, M65",
    "jarvis-core/docs/clients.md": "`installed` (M65)",
    "jarvis-core/docs/configuration.md": "## `extensions:`",
    "MILESTONES.md": "**M65 — Something to browse**",
    "CHANGELOG.md": "**M65 — something to browse.**",
    "docs/verification.md": "### Something to browse (M65)",
    "docs/OVERHAUL_PLAN.md": "| M65 | **Something to browse**",
}
for path, needle in checks.items():
    assert needle in Path(path).read_text(), f"{path} lacks: {needle}"
print(f"{len(checks)} documents")
'

# --- the suites --------------------------------------------------------------
check "ruff" python3 -m ruff check jarvis-core/jarvis/integrations/extensions jarvis-core/jarvis/integrations/skills jarvis-core/tests/test_extensions.py
check_pytest "core: the catalogue, the skills and the packaging pins" 'cd jarvis-core && python3 -m pytest tests/test_extensions.py tests/test_skills.py tests/test_packaging.py -q --timeout=120 --timeout-method=signal'
check "token lint: no new hard-coded value" python3 scripts/verify/token_lint.py
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
ensure_web_deps
ensure_web_build
check_sh "svelte-check finds nothing" 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | tail -1'
check_sh "the console's unit tests" 'cd jarvis-web && npx vitest run 2>&1 | tail -3'
run_playwright "the catalogue, the extensions fold and the MCP form, in a browser" catalogue.spec.ts extensions.spec.ts mcp.spec.ts
run_playwright "the menu inventory still holds on the tools page" menus.spec.ts -g '"inventory names|Tools|TOOLS"'
check_sh "three pictures of it, at three widths" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Tools" 2>&1 | tail -2 && cd .. && test -f docs/ui-review/settings-tools/desktop.png && test -f docs/ui-review/settings-tools/tablet.png && test -f docs/ui-review/settings-tools/mobile.png && echo "docs/ui-review/settings-tools: desktop, tablet, mobile"'

verify_end
