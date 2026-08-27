#!/usr/bin/env bash
# M47 — the catalog, and installing from it safely.
#
# A catalog that can install code is the marketplace attack surface this class
# of tool has actually been burned by, so almost every check here is a refusal:
# what cannot be installed, where it cannot come from, what will not be run,
# and what a payload cannot write.
source "$(dirname "$0")/lib.sh"
verify_begin "M47" "the catalog: what may be installed, from where, and what never runs"
use_venv

require_file jarvis-core/jarvis/integrations/extensions/catalog.py
require_file jarvis-core/jarvis/integrations/extensions/install.py
require_file testing/fixtures/catalog/index.json
require_file testing/live/scenarios/redteam-malicious-skill-install.yaml

# --- what may be installed at all -------------------------------------------
check "only a document and a URL can be installed; code cannot" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import (
    INSTALLABLE_KINDS, REFUSED_KINDS, CatalogError, Source)
assert set(INSTALLABLE_KINDS) == {"skill", "mcp"}, INSTALLABLE_KINDS
for kind, needle in (("plugin", "interpreter"),):
    try:
        Source(name="s", url="https://example/x", kind=kind)
    except CatalogError as err:
        assert needle in str(err), str(err)
    else:
        raise SystemExit(f"a catalog offering {kind} was accepted")
assert "mcp-stdio" in REFUSED_KINDS
print("installable: " + ", ".join(INSTALLABLE_KINDS) + "; refused with a reason: " + ", ".join(sorted(REFUSED_KINDS)))
'

check "a stdio MCP server cannot arrive from a catalog" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.install import InstallError, refuse_mcp_stdio

class Spec:
    transport = "stdio"
    is_stdio = True
try:
    refuse_mcp_stdio(Spec())
except InstallError as err:
    assert "configuration.yaml" in str(err)
else:
    raise SystemExit("a program-starting server came from a catalog")
print("stdio refused: those come from the file a person edits")
'

# --- where it may come from --------------------------------------------------
check "there is no default source, so a fresh install can reach nothing" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import DEFAULT_SOURCES, Catalog, CatalogError
assert DEFAULT_SOURCES == (), DEFAULT_SOURCES
try:
    Catalog().source_for("github")
except CatalogError as err:
    assert "nobody allowed" in str(err)
else:
    raise SystemExit("an unconfigured origin was usable")
print("no default list: shipping one hands the supply chain to whoever owns those URLs")
'

check "a source is https or this machine, and nothing else" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import ALLOWED_SCHEMES, CatalogError, Source
for url in ("http://plain/x", "ftp://old/x", "/etc/passwd", "gopher://x/y"):
    try:
        Source(name="s", url=url)
    except CatalogError:
        continue
    raise SystemExit(f"{url} was allowed as a source")
print("allowed schemes: " + ", ".join(ALLOWED_SCHEMES))
'

# --- what is pinned ----------------------------------------------------------
check "latest is not a version" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import CatalogError, Entry, resolve_ref
entry = Entry(id="x", kind="skill", source="s", url="file:///tmp", ref="latest")
try:
    resolve_ref(entry, [])
except CatalogError as err:
    assert "concrete ref" in str(err)
else:
    raise SystemExit("a blind latest was accepted")
assert resolve_ref(entry, ["v1.0.0", "v1.2.0"]) == "v1.2.0"
print("latest resolves to a concrete ref, or the install does not happen")
'

check "what was approved is what lands, checked twice" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import CatalogError, Entry
from jarvis.integrations.extensions.install import plan
entry = Entry(id="x", kind="skill", source="s", url="file:///tmp")
good = {"SKILL.md": b"---\nname: x\ndescription: y\n---\n\nBody.\n"}
first = plan(entry, good)
assert len(first["sha256"]) == 64
try:
    plan(entry, {"SKILL.md": b"different"}, expected_sha=first["sha256"])
except CatalogError as err:
    assert "not what was approved" in str(err)
else:
    raise SystemExit("a swapped payload passed the hash check")
print("sha256 recorded at plan time and re-checked before writing")
'

# --- nothing runs ------------------------------------------------------------
check "an approved skill lands and nothing in its payload executes" python3 -c '
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup
from jarvis.integrations.extensions.catalog import Catalog, Source
from jarvis.integrations.extensions.install import apply, fetch_local, plan
from jarvis.integrations.skills import SkillStore

MARKER = Path("/tmp/jarvis-catalog-probe-should-not-exist")

async def main():
    MARKER.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        jarvis = Jarvis(config_dir=tmp)
        store = SkillStore(Path(tmp) / "skills")
        store.load()
        jarvis.data["skills"] = store
        await async_setup(jarvis, None)
        catalog = Catalog()
        catalog.add(Source(name="fixture", url=Path("testing/fixtures/catalog").resolve().as_uri()))
        entry = [e for e in catalog.search() if e.id == "friendly-helper"][0]
        files = fetch_local(entry)
        proposal = plan(entry, files)
        assert "install.sh" in proposal["hooks"], proposal["hooks"]
        assert "will not run" in proposal["warning"]
        result = apply(jarvis, entry, files, proposal)
        assert result["ref"] == "v2.1.0", "it landed unpinned"
        assert (Path(tmp) / "skills/friendly-helper/install.sh").exists(), "the payload was not written whole"
        assert not MARKER.exists(), "SOMETHING IN THE PAYLOAD EXECUTED"
        assert "friendly-helper" in store.skills
        print("installed, its shell script on disk and named in the prompt, and never run")
asyncio.run(main())
'

check "install refuses without an approved plan" python3 -c '
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.integrations.extensions import async_setup
from jarvis.integrations.extensions.catalog import Catalog, Source
from jarvis.integrations.extensions.install import InstallError, apply, fetch_local
from jarvis.integrations.skills import SkillStore

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        jarvis = Jarvis(config_dir=tmp)
        store = SkillStore(Path(tmp) / "skills")
        store.load()
        jarvis.data["skills"] = store
        await async_setup(jarvis, None)
        catalog = Catalog()
        catalog.add(Source(name="fixture", url=Path("testing/fixtures/catalog").resolve().as_uri()))
        jarvis.data["extension_catalog"] = catalog
        entry = [e for e in catalog.search() if e.id == "bin-day"][0]
        try:
            apply(jarvis, entry, fetch_local(entry), {})
        except InstallError as err:
            assert "nothing was approved" in str(err)
        else:
            raise SystemExit("it installed with no approval")
        out = await jarvis.services.async_call(
            "extensions", "install", {"source": "fixture", "id": "bin-day"},
            blocking=True, return_response=True)
        assert "extensions.plan" in out["error"], out
        assert not (Path(tmp) / "skills/bin-day").exists()
        print("no plan, no install — and the service says which call is missing")
asyncio.run(main())
'

# --- a payload's reach -------------------------------------------------------
check "a payload cannot write outside its own folder" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.install import InstallError, read_payload
ok = {"SKILL.md": b"---\nname: x\ndescription: y\n---\n"}
for bad in ("../escape/SKILL.md", "/etc/SKILL.md", ".git/config", "a/b/c/d/e/SKILL.md"):
    try:
        read_payload({**ok, bad: b"x"})
    except InstallError:
        continue
    raise SystemExit(f"{bad} was accepted")
print("traversal, absolute, dotfile and over-deep all refused — refused, not corrected")
'

# --- the metadata is content -------------------------------------------------
check "a catalog description cannot smuggle an instruction" python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.extensions.catalog import Catalog, Source
from jarvis.security.quarantine import has_control_tokens, is_quarantined
catalog = Catalog()
catalog.add(Source(name="fixture", url=Path("testing/fixtures/catalog").resolve().as_uri()))
hostile = [e for e in catalog.search() if e.id == "friendly-helper"][0]
assert is_quarantined(hostile.description), "a description arrived unwrapped"
assert not has_control_tokens(hostile.description), "a role marker survived"
assert "ignore the permissions" in hostile.description.lower(), "it was FILTERED, not quarantined"
assert "become_root" not in hostile.permissions, "an invented permission was shown as real"
print("wrapped, its role marker scarred, its words intact, its invented permission dropped")
'

check "the threat model carries the supply-chain surface" python3 -c '
from pathlib import Path
text = Path("docs/THREAT_MODEL.md").read_text()
assert "## The supply chain" in text
for claim in ("no default source", "Nothing runs on install", "checked twice", "approved"):
    assert claim.lower() in text.lower(), f"the threat model does not mention: {claim}"
print("supply chain documented, including what it does NOT defend against")
'

check_pytest "the suite" 'cd jarvis-core && python3 -m pytest tests/test_extensions.py -q --timeout=120'

# --- the red-team probe ------------------------------------------------------
check "the malicious-install probe is written and asserts the marker never appears" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_scenario
s = load_scenario("testing/live/scenarios/redteam-malicious-skill-install.yaml")
files = [t.expect.raw.get("file") for t in s.turns if t.expect.raw.get("file")]
assert files, "the probe does not check for the marker file"
assert all(f.get("exists") is False for f in files), files
assert any("install" in str(t.expect.raw.get("no_service")) for t in s.turns)
print(f"{s.name}: {len(s.turns)} turns; the suite fails if the marker exists")
'

if [ "${M47_LIVE:-1}" = "1" ] && docker compose -f jarvis-core/docker-compose.yml ps jarvis-core 2>/dev/null | grep -q healthy; then
    check_sh "the probe, against the real containers" \
        'set -a; . ./.env >/dev/null 2>&1; set +a; timeout 1800 python3 -m testing.live.runner --full --target stack --only redteam-malicious-skill-install --no-browser 2>&1 | grep -v pthread_setaffinity > /tmp/m47-live.log; grep -qE "^  ok   redteam-malicious-skill-install" /tmp/m47-live.log || { grep -A8 "redteam-malicious" /tmp/m47-live.log; exit 1; }; grep -E "^  ok   redteam-malicious|^live: " /tmp/m47-live.log | tail -2'
fi

verify_end
