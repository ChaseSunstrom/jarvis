#!/usr/bin/env bash
# M45 — one registry over skills, MCP servers and plugins.
#
# The claim is not "there is a list". It is that every extensible thing carries
# a manifest saying what it may reach, that the manifest is validated against a
# schema an author can read, and that a manifest which does not validate takes
# the extension OUT OF SERVICE rather than loading it with the bad parts
# dropped.
source "$(dirname "$0")/lib.sh"
verify_begin "M45" "the extension registry: manifests, schema, and one index"
use_venv

require_file jarvis-core/jarvis/integrations/extensions/manifest.py
require_file jarvis-core/jarvis/integrations/extensions/manifest.schema.json
require_file jarvis-core/jarvis/integrations/extensions/registry.py

check "the schema is a real JSON Schema document, not a shape in someone's head" python3 -c '
import json, sys
doc = json.load(open("jarvis-core/jarvis/integrations/extensions/manifest.schema.json"))
assert doc["$schema"].startswith("https://json-schema.org/"), doc.get("$schema")
required = set(doc["required"])
assert {"id", "kind", "version", "description"} <= required, required
props = doc["properties"]
for field in ("author", "source_url", "permissions", "tools", "network", "filesystem"):
    assert field in props, f"the manifest cannot declare {field}"
closed = doc["additionalProperties"]
assert closed is False, "unknown keys are accepted"
print(f"{len(props)} fields, {len(required)} required, unknown keys rejected")
'

check "the validator implements every keyword the schema uses" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.manifest import KEYWORDS, schema
used = set()
def walk(node):
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        used.add(key)
        if key == "properties" and isinstance(value, dict):
            for sub in value.values():
                walk(sub)
        elif key == "items":
            walk(value)
        elif key in ("enum", "required"):
            continue
        elif isinstance(value, dict):
            walk(value)
walk(schema())
missing = used - KEYWORDS
assert not missing, f"schema keywords nothing enforces: {sorted(missing)}"
print(f"{len(used)} keywords used, all implemented")
'

check "the permission vocabulary is closed" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.manifest import PERMISSIONS, Manifest, ManifestError
base = {"id": "x", "kind": "skill", "version": "1", "description": "d"}
try:
    Manifest.from_raw({**base, "permissions": ["become_root"]})
except ManifestError as err:
    assert "become_root" in str(err)
else:
    raise SystemExit("a manifest invented a permission and was accepted")
print(f"{len(PERMISSIONS)} permissions: " + ", ".join(PERMISSIONS))
'

check "a manifest cannot list a tool it has not asked permission for" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.manifest import Manifest, ManifestError
base = {"id": "x", "kind": "skill", "version": "1", "description": "d"}
for tool, needed in (("write_file", "filesystem_write"), ("web_search", "network"), ("remember", "memory_write")):
    try:
        Manifest.from_raw({**base, "tools": [tool], "permissions": ["read_state"]})
    except ManifestError as err:
        assert needed in str(err), str(err)
    else:
        raise SystemExit(f"{tool} was allowed without {needed}")
    Manifest.from_raw({**base, "tools": [tool], "permissions": ["read_state", needed]})
print("under-declaration caught for 3 tools; declaring it is enough")
'

# --- rejected rather than half-loaded ---------------------------------------
check "an invalid manifest takes the skill out of the system prompt, not just out of the list" python3 -c '
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions.registry import ExtensionRegistry
from jarvis.integrations.skills import SkillStore

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "skills"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "SKILL.md").write_text(
        "---\nname: bad\ndescription: Asks for a permission nobody enforces.\n"
        "metadata:\n  permissions: [read_state, become_root]\n---\n\nBody.\n",
        encoding="utf-8",
    )
    jarvis = Jarvis(config_dir=tmp)
    store = SkillStore(root)
    store.load()
    assert "bad" in store.skills, "the SKILL.md itself parses; the manifest is what rejects it"
    jarvis.data["skills"] = store
    registry = ExtensionRegistry(jarvis)
    registry.index()
    assert "skill:bad" not in registry.records, "it was indexed anyway"
    assert "bad" not in store.skills, "still loaded: the model would still see it"
    assert "bad" not in store.index_block(), "still in the system prompt"
    assert registry.errors and "become_root" in registry.errors[0]["error"]
print("rejected: out of the index, out of the store, out of the prompt")
'

# --- the first-party skills -------------------------------------------------
check "four skills ship, and every one of them validates" python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.registry import skill_manifest
from jarvis.integrations.skills import SkillStore
bundled = Path("jarvis-core/jarvis/integrations/skills/bundled")
store = SkillStore(Path("/nonexistent"), bundled_root=bundled)
count = store.load()
assert count == 4, f"{count} bundled skills, expected 4: {sorted(store.skills)}"
assert not store.errors, store.errors
names = []
for skill in store.skills.values():
    manifest = skill_manifest(skill)
    assert manifest.description, f"{manifest.id} has no description"
    assert not manifest.under_declared(), manifest.under_declared()
    names.append(f"{manifest.id}({len(manifest.tools)} tools)")
print(", ".join(sorted(names)))
'

check "the homelab skill has a tool that can actually read the measurements" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.llm.tools import READ_ONLY_TOOLS
import jarvis.integrations.dashboards as dashboards
assert "metrics_query" in READ_ONLY_TOOLS, "metrics_query must be read-only"
assert hasattr(dashboards, "_register_metrics_tool")
src = open("jarvis-core/jarvis/integrations/skills/bundled/homelab-status/SKILL.md").read()
assert "metrics_query" in src
print("metrics_query registered by dashboards, read-only, named by the skill")
'

check "a shipped skill is a document a person can read, not a program" python3 -c '
import sys
from pathlib import Path
bundled = Path("jarvis-core/jarvis/integrations/skills/bundled")
files = sorted(p for p in bundled.rglob("*") if p.is_file())
extras = [p.name for p in files if p.name != "SKILL.md"]
assert not extras, f"something other than a SKILL.md ships: {extras}"
words = sum(len(p.read_text(encoding="utf-8").split()) for p in files)
print(f"{len(files)} SKILL.md files, {words} words, nothing executable")
'

# --- the index --------------------------------------------------------------
check "one index answers what is installed, what it may reach, and whether it works" python3 -c '
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup
from jarvis.integrations.plugins import PluginTool, ToolPlugin, get_registry
from jarvis.integrations.skills import SkillStore

class Demo(ToolPlugin):
    domain = "demo"
    def tools(self):
        return [PluginTool("demo_read", "Reads.", {}, lambda **_: None, read_only=True)]
    async def health(self):
        return {"ok": True, "detail": "fine"}

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        jarvis = Jarvis(config_dir=tmp)
        store = SkillStore(Path("/nonexistent"), bundled_root=Path("jarvis-core/jarvis/integrations/skills/bundled"))
        store.load()
        jarvis.data["skills"] = store
        get_registry(jarvis).add(Demo(jarvis))
        assert await async_setup(jarvis, None) is True
        listed = await jarvis.services.async_call("extensions", "list", {}, blocking=True, return_response=True)
        scope = await jarvis.services.async_call("extensions", "permissions", {}, blocking=True, return_response=True)
        health = await jarvis.services.async_call("extensions", "health", {}, blocking=True, return_response=True)
        assert listed["counts"]["skill"] == 4 and listed["counts"]["plugin"] == 1
        row = [r for r in listed["extensions"] if r["id"] == "research-report"][0]
        assert row["network"]["needs"] is True and "network" in row["permissions"]
        assert row["origin"] == "bundled" and row["enabled"] is True
        assert "skill:note-taking" in scope["scope"]["memory_write"]
        assert health["health"]["plugin:demo"]["ok"] is True
        total = sum(listed["counts"].values())
        permissions = len(scope["scope"])
        answered = len(health["health"])
        print(f"{total} indexed; {permissions} permissions in use; health answered for {answered}")
asyncio.run(main())
'

# Measured rather than grepped: an earlier version of this check looked for
# the word DEPENDENCIES and passed on the sentence in the docstring that
# explains why there are none.
check "an install with nothing installed still gets a registry, and grows nothing" python3 -c '
import asyncio, sys, tempfile
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        jarvis = Jarvis(config_dir=tmp)
        before = set(jarvis.data)
        assert await async_setup(jarvis, None) is True
        # The registry owns its catalog and its enabled/granted state since
        # M47 (f8235aa); those two are the registry, not a subsystem started.
        # What must not appear is MCP, plugins or skills coming up on their own.
        grown = set(jarvis.data) - before - {"extensions", "extension_catalog", "extensions_state"}
        assert not grown, f"setting up the registry started {sorted(grown)}"
        listed = await jarvis.services.async_call("extensions", "list", {}, blocking=True, return_response=True)
        assert listed["extensions"] == [], listed
        print("empty install: registry present, subsystems untouched")
asyncio.run(main())
'

check_pytest "the suite" 'cd jarvis-core && python3 -m pytest tests/test_extensions.py tests/test_skills.py -q --timeout=120'

verify_end
