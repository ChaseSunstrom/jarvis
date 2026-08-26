"""The extension registry: one index, and a manifest that cannot lie quietly.

Three subsystems load extensible things — skills from folders, MCP servers over
the network, tool plugins from Python. This is the fourth thing, which reads
all three and answers "what is installed, what may it reach, is it working".

The claims worth testing hardest are not "the list has three rows in it":

* a manifest that does not validate is not loaded AT ALL, and for a skill that
  means it leaves the system prompt as well as the index — a half-loaded
  extension is one an operator believes is constrained and is not;
* the permission vocabulary is CLOSED, so a manifest cannot invent a permission
  that nothing enforces and have it accepted as a declaration;
* the tool allowlist and the declared permissions have to agree. A manifest
  listing `write_file` while declaring no `filesystem_write` describes
  something other than what it does, and the difference is exactly what
  somebody reading the manifest would rely on;
* the hand-written validator implements every keyword the schema uses. The
  real failure mode of a hand-rolled validator is a schema that grows a
  keyword nothing enforces, so the schema starts describing a stricter
  document than the one being accepted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.extensions import async_setup as extensions_setup  # noqa: E402
from jarvis.integrations.extensions.manifest import (  # noqa: E402
    KEYWORDS,
    PERMISSIONS,
    Manifest,
    ManifestError,
    schema,
    validate,
)
from jarvis.integrations.extensions.registry import (  # noqa: E402
    ExtensionRegistry,
    skill_manifest,
)
from jarvis.integrations.plugins import PluginTool, ToolPlugin  # noqa: E402
from jarvis.integrations.mcp.catalog import ServerSpec  # noqa: E402
from jarvis.integrations.skills import SkillStore  # noqa: E402

BUNDLED = Path(__file__).resolve().parents[1] / "jarvis/integrations/skills/bundled"


def good(**over) -> dict:
    raw = {
        "id": "example",
        "kind": "skill",
        "version": "1",
        "description": "An example.",
    }
    raw.update(over)
    return raw


# --- the schema and the validator -------------------------------------------
def test_the_validator_knows_every_keyword_the_schema_uses() -> None:
    """The failure mode of a hand-written validator, caught mechanically.

    Add `minimum` to the schema and nothing enforces it: the document says one
    thing and the code accepts another, silently, forever. This walks the
    schema and fails on the first keyword the validator does not implement.
    """
    used: set[str] = set()

    def walk(node) -> None:
        """Descend as a SCHEMA, not as a dict.

        Under `properties` the keys are field names, not keywords — walking
        them as keywords made this test fail on `id`, which is exactly the
        false alarm that teaches somebody to delete a test.
        """
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
    unknown = used - KEYWORDS
    assert not unknown, f"the schema uses keywords the validator ignores: {sorted(unknown)}"


def test_the_schema_on_disk_is_the_schema_that_is_enforced() -> None:
    doc = schema()
    assert doc["$schema"].startswith("https://json-schema.org/")
    assert set(doc["properties"]["permissions"]["items"]["enum"]) == set(PERMISSIONS)


def test_a_manifest_reports_every_problem_not_the_first() -> None:
    with pytest.raises(ManifestError) as caught:
        Manifest.from_raw({"id": "NOT OK", "kind": "wat", "version": "", "surprise": 1})
    problems = caught.value.problems
    assert len(problems) >= 4, problems
    joined = " ".join(problems)
    assert "id" in joined and "kind" in joined and "surprise" in joined


def test_an_unknown_permission_is_rejected_rather_than_ignored() -> None:
    """The closed vocabulary.

    An accepted-but-unenforced permission is worse than a rejected one: it
    reads as a declaration in a management surface and constrains nothing.
    """
    with pytest.raises(ManifestError) as caught:
        Manifest.from_raw(good(permissions=["read_state", "become_root"]))
    assert "become_root" in str(caught.value)


def test_an_unknown_key_is_rejected() -> None:
    with pytest.raises(ManifestError):
        Manifest.from_raw(good(escalate=True))


@pytest.mark.parametrize(
    "tool,permission",
    [("write_file", "filesystem_write"), ("web_search", "network"), ("remember", "memory_write")],
)
def test_a_tool_allowlist_cannot_out_reach_the_declared_permissions(tool, permission) -> None:
    with pytest.raises(ManifestError) as caught:
        Manifest.from_raw(good(tools=[tool], permissions=["read_state"]))
    assert permission in str(caught.value)
    # And declaring it is enough — this is a consistency check, not a ban.
    Manifest.from_raw(good(tools=[tool], permissions=["read_state", permission]))


def test_validate_does_not_raise_and_returns_all_problems() -> None:
    problems = validate({"id": 4}, {"type": "object", "properties": {"id": {"type": "string"}}})
    assert problems and "must be string" in problems[0]


# --- the skills that ship ----------------------------------------------------
def test_every_bundled_skill_has_a_manifest_that_validates() -> None:
    """The four first-party skills, held to the schema they ship with.

    A shipped skill that fails its own validator would be dropped at boot, so
    this is the difference between four skills and none.
    """
    store = SkillStore(Path("/nonexistent-skills"), bundled_root=BUNDLED)
    assert store.load() == 4, store.errors
    assert not store.errors
    for skill in store.skills.values():
        manifest = skill_manifest(skill)
        assert manifest.kind == "skill"
        assert manifest.description
        assert not manifest.under_declared()


def test_a_users_skill_overrides_a_bundled_one_of_the_same_name(tmp_path: Path) -> None:
    """The documented override, and it must not read as a collision.

    Overriding `diary` should mean writing one file, not finding where the
    shipped copy lives and deleting it.
    """
    (tmp_path / "diary").mkdir()
    (tmp_path / "diary" / "SKILL.md").write_text(
        "---\nname: diary\ndescription: Mine.\n---\n\nMine.\n", encoding="utf-8"
    )
    store = SkillStore(tmp_path, bundled_root=BUNDLED)
    store.load()
    assert store.skills["diary"].description == "Mine."
    assert store.errors == [], "an override must not be reported as a duplicate"
    assert len(store.skills) == 4, "the other three are still there"


def test_two_skills_of_the_same_name_in_one_root_is_still_a_collision(tmp_path: Path) -> None:
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "SKILL.md").write_text(
            "---\nname: same\ndescription: One of two.\n---\n\nBody.\n", encoding="utf-8"
        )
    store = SkillStore(tmp_path)
    store.load()
    assert len(store.skills) == 1
    assert store.errors and "already called" in store.errors[0]["error"]


# --- the registry ------------------------------------------------------------
class _Plugin(ToolPlugin):
    domain = "example"

    def tools(self):
        return [
            PluginTool("example_read", "Reads.", {}, lambda **_: None, read_only=True),
            PluginTool("example_write", "Writes.", {}, lambda **_: None),
        ]

    async def health(self):
        return {"ok": True, "detail": "fine"}


class _Manager:
    """Enough of `MCPManager` for the registry: the servers and the listing."""

    def __init__(self, specs) -> None:
        self.servers = {spec.name: spec for spec in specs}

    def listing(self):
        return [
            {**spec.as_dict(), "connected": spec.name == "notes", "tool_count": 3, "error": ""}
            for spec in self.servers.values()
        ]


def _with_mcp(jarvis, specs) -> None:
    from jarvis.integrations.mcp import DATA_MANAGER, DOMAIN as MCP_DOMAIN

    jarvis.data.setdefault(MCP_DOMAIN, {})[DATA_MANAGER] = _Manager(specs)


@pytest.mark.asyncio
async def test_the_registry_indexes_all_three_kinds(tmp_path: Path) -> None:
    jarvis = Jarvis(config_dir=str(tmp_path))
    store = SkillStore(Path("/nonexistent-skills"), bundled_root=BUNDLED)
    store.load()
    jarvis.data["skills"] = store

    from jarvis.integrations.plugins import get_registry as plugin_registry

    plugin_registry(jarvis).add(_Plugin(jarvis))
    _with_mcp(
        jarvis,
        [
            ServerSpec(name="notes", transport="http", url="https://notes.example/mcp"),
            ServerSpec(name="local", transport="stdio", command="/usr/bin/thing", tier=3),
        ],
    )

    registry = ExtensionRegistry(jarvis)
    assert registry.index() == 7  # four skills, one plugin, two servers
    keys = sorted(registry.records)
    assert "skill:diary" in keys and "plugin:example" in keys and "mcp:notes" in keys

    # A stdio server is a process Jarvis STARTS. The manifest says so rather
    # than leaving somebody to work it out from `command:`.
    stdio = registry.get("mcp:local")
    assert "run_process" in stdio.manifest.permissions
    assert stdio.manifest.network_needs is False
    http = registry.get("mcp:notes")
    assert "network" in http.manifest.permissions
    assert http.manifest.network_hosts == ("notes.example",)
    assert "run_process" not in http.manifest.permissions

    # The plugin's `act` is DERIVED from PluginTool.read_only rather than
    # declared, so the manifest cannot disagree with the taint gate.
    plugin = registry.get("plugin:example")
    assert "act" in plugin.manifest.permissions
    assert plugin.manifest.tools == ("example_read", "example_write")


@pytest.mark.asyncio
async def test_mcp_health_agrees_with_the_page_that_already_shows_it(tmp_path: Path) -> None:
    """One source for "is it connected", so two screens cannot disagree."""
    jarvis = Jarvis(config_dir=str(tmp_path))
    _with_mcp(
        jarvis,
        [
            ServerSpec(name="notes", transport="http", url="https://notes.example/mcp"),
            ServerSpec(name="down", transport="http", url="https://down.example/mcp"),
        ],
    )
    registry = ExtensionRegistry(jarvis)
    registry.index()
    health = await registry.health()
    assert health["mcp:notes"]["ok"] is True
    assert "3 tools" in health["mcp:notes"]["detail"]
    assert health["mcp:down"]["ok"] is False


@pytest.mark.asyncio
async def test_an_invalid_skill_manifest_leaves_the_system_prompt_too(tmp_path: Path) -> None:
    """Rejected rather than half-loaded, and that has to mean the prompt.

    Dropping it from the index alone would leave the model with a skill in its
    list that the operator has been told is not loaded.
    """
    skills = tmp_path / "skills"
    (skills / "bad").mkdir(parents=True)
    (skills / "bad" / "SKILL.md").write_text(
        "---\n"
        "name: bad\n"
        "description: Declares a permission nobody enforces.\n"
        "metadata:\n"
        "  permissions: [read_state, become_root]\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    jarvis = Jarvis(config_dir=str(tmp_path))
    store = SkillStore(skills)
    store.load()
    assert "bad" in store.skills, "it loads as a skill; the manifest is what rejects it"
    jarvis.data["skills"] = store

    registry = ExtensionRegistry(jarvis)
    registry.index()
    assert "skill:bad" not in registry.records
    assert "bad" not in store.skills, "still in the store: the model would still see it"
    assert "bad" not in store.index_block()
    assert registry.errors and "become_root" in registry.errors[0]["error"]


@pytest.mark.asyncio
async def test_health_never_raises_when_something_is_broken(tmp_path: Path) -> None:
    class _Sick(_Plugin):
        domain = "sick"

        async def health(self):
            raise RuntimeError("the far end is down")

    jarvis = Jarvis(config_dir=str(tmp_path))
    from jarvis.integrations.plugins import get_registry as plugin_registry

    plugin_registry(jarvis).add(_Sick(jarvis))
    registry = ExtensionRegistry(jarvis)
    registry.index()
    health = await registry.health()
    assert health["plugin:sick"]["ok"] is False
    assert "down" in health["plugin:sick"]["detail"]


@pytest.mark.asyncio
async def test_the_permission_scope_answers_the_inverse_question(tmp_path: Path) -> None:
    """Not "what can this skill do" but "everything here that can write memory"."""
    jarvis = Jarvis(config_dir=str(tmp_path))
    store = SkillStore(Path("/nonexistent-skills"), bundled_root=BUNDLED)
    store.load()
    jarvis.data["skills"] = store
    registry = ExtensionRegistry(jarvis)
    registry.index()
    scope = registry.permission_scope()
    assert "skill:note-taking" in scope["memory_write"]
    assert "skill:research-report" in scope["network"]
    assert "skill:homelab-status" not in scope.get("act", [])


@pytest.mark.asyncio
async def test_setup_registers_the_services_and_indexes_without_a_bus_start(tmp_path: Path) -> None:
    jarvis = Jarvis(config_dir=str(tmp_path))
    store = SkillStore(Path("/nonexistent-skills"), bundled_root=BUNDLED)
    store.load()
    jarvis.data["skills"] = store
    assert await extensions_setup(jarvis, None) is True

    listed = await jarvis.services.async_call(
        "extensions", "list", {}, blocking=True, return_response=True
    )
    assert listed["counts"]["skill"] == 4
    scope = await jarvis.services.async_call(
        "extensions", "permissions", {}, blocking=True, return_response=True
    )
    assert "memory_write" in scope["scope"]
    doc = await jarvis.services.async_call(
        "extensions", "schema", {}, blocking=True, return_response=True
    )
    assert doc["schema"]["properties"]["permissions"]["items"]["enum"]


# --- what the operator decided, and whether it bites -------------------------
async def _install(tmp_path: Path):
    """A Jarvis with one plugin, four shipped skills, and the registry up."""
    from jarvis.integrations.extensions import async_setup
    from jarvis.integrations.plugins import get_registry as plugin_registry
    from jarvis.llm.tools import ToolRegistry

    jarvis = Jarvis(config_dir=str(tmp_path))
    jarvis.data["llm_tools"] = ToolRegistry(jarvis)
    store = SkillStore(tmp_path / "skills", bundled_root=BUNDLED)
    store.load()
    jarvis.data["skills"] = store
    plugin = _Plugin(jarvis)
    plugin_registry(jarvis).add(plugin)
    plugin.register()
    assert await async_setup(jarvis, None) is True
    return jarvis


@pytest.mark.asyncio
async def test_disabling_a_plugin_takes_its_tools_off_the_model(tmp_path: Path) -> None:
    """"Not offered to the model at all" has to mean the tool registry.

    A plugin that is merely hidden from a list is one the model can still call,
    which is the difference between a management surface and a filter.
    """
    jarvis = await _install(tmp_path)
    tools = jarvis.data["llm_tools"]
    assert tools.get("example_write") is not None

    result = await jarvis.services.async_call(
        "extensions", "set", {"key": "plugin:example", "enabled": False},
        blocking=True, return_response=True,
    )
    assert result["extension"]["enabled"] is False
    assert tools.get("example_write") is None
    assert tools.get("example_read") is None
    assert "example_write" in result["removed"]

    # And back, without re-registering the ones that were withdrawn separately.
    back = await jarvis.services.async_call(
        "extensions", "set", {"key": "plugin:example", "enabled": True},
        blocking=True, return_response=True,
    )
    assert tools.get("example_write") is not None
    assert set(back["restored"]) == {"example_read", "example_write"}


@pytest.mark.asyncio
async def test_revoking_act_withdraws_only_the_tools_that_change_something(tmp_path: Path) -> None:
    """An edited permission scope, enforced on the very next call."""
    jarvis = await _install(tmp_path)
    tools = jarvis.data["llm_tools"]
    result = await jarvis.services.async_call(
        "extensions", "set", {"key": "plugin:example", "permissions": ["read_state"]},
        blocking=True, return_response=True,
    )
    assert result["extension"]["revoked"] == ["act"]
    assert tools.get("example_write") is None, "a writer survived its permission being revoked"
    assert tools.get("example_read") is not None, "a reader was withdrawn with it"


@pytest.mark.asyncio
async def test_a_permission_the_manifest_never_declared_cannot_be_granted(tmp_path: Path) -> None:
    """Narrowing only: the manifest is the statement people read."""
    jarvis = await _install(tmp_path)
    result = await jarvis.services.async_call(
        "extensions",
        "set",
        {"key": "plugin:example", "permissions": ["read_state", "act", "run_process"]},
        blocking=True,
        return_response=True,
    )
    assert "run_process" not in result["extension"]["granted"]

    refused = await jarvis.services.async_call(
        "extensions", "set", {"key": "plugin:example", "permissions": ["become_root"]},
        blocking=True, return_response=True,
    )
    assert "no such permission" in refused["error"]


@pytest.mark.asyncio
async def test_disabling_a_skill_takes_it_out_of_the_prompt(tmp_path: Path) -> None:
    """A skill owns no tools, so this is what disabling one can mean."""
    jarvis = await _install(tmp_path)
    store = jarvis.data["skills"]
    assert "diary" in store.index_block()

    await jarvis.services.async_call(
        "extensions", "set", {"key": "skill:diary", "enabled": False},
        blocking=True, return_response=True,
    )
    assert "diary" not in store.skills
    assert "diary" not in store.index_block()
    assert store.body_for("diary")["status"] == "error"


@pytest.mark.asyncio
async def test_turning_a_skill_back_on_actually_brings_it_back(tmp_path: Path) -> None:
    """The live suite found this: the switch worked exactly once.

    Disabling pops the skill out of the store, and nothing else ever put one
    back — so a skill turned off was gone until the next reload, while the
    console cheerfully showed it as on again.
    """
    jarvis = await _install(tmp_path)
    store = jarvis.data["skills"]

    await jarvis.services.async_call(
        "extensions", "set", {"key": "skill:diary", "enabled": False},
        blocking=True, return_response=True,
    )
    assert "diary" not in store.skills

    await jarvis.services.async_call(
        "extensions", "set", {"key": "skill:diary", "enabled": True},
        blocking=True, return_response=True,
    )
    assert "diary" in store.skills
    assert "diary" in store.index_block()
    # And the others were not disturbed by the reload that brought it back.
    assert sorted(store.skills) == ["diary", "homelab-status", "note-taking", "research-report"]


@pytest.mark.asyncio
async def test_a_reload_does_not_resurrect_a_skill_that_was_turned_off(tmp_path: Path) -> None:
    """The other half: bringing one back must not bring back all of them."""
    jarvis = await _install(tmp_path)
    store = jarvis.data["skills"]
    for name in ("diary", "note-taking"):
        await jarvis.services.async_call(
            "extensions", "set", {"key": f"skill:{name}", "enabled": False},
            blocking=True, return_response=True,
        )
    await jarvis.services.async_call(
        "extensions", "set", {"key": "skill:diary", "enabled": True},
        blocking=True, return_response=True,
    )
    assert "diary" in store.skills
    assert "note-taking" not in store.skills, "a reload brought back one that was off"


@pytest.mark.asyncio
async def test_a_decision_survives_a_restart(tmp_path: Path) -> None:
    jarvis = await _install(tmp_path)
    await jarvis.services.async_call(
        "extensions", "set", {"key": "plugin:example", "enabled": False},
        blocking=True, return_response=True,
    )
    again = await _install(tmp_path)
    assert again.data["llm_tools"].get("example_write") is None, "it came back after a restart"
    listed = await again.services.async_call(
        "extensions", "list", {}, blocking=True, return_response=True
    )
    row = [r for r in listed["extensions"] if r["key"] == "plugin:example"][0]
    assert row["enabled"] is False


@pytest.mark.asyncio
async def test_last_used_comes_from_the_events_that_already_fire(tmp_path: Path) -> None:
    jarvis = await _install(tmp_path)
    from jarvis.integrations.plugins import EVENT_PLUGIN_CALL

    state = jarvis.data["extensions_state"]
    assert state.last_used.get("plugin:example") is None
    await jarvis.bus.async_fire(EVENT_PLUGIN_CALL, {"plugin": "example", "tool": "example_read"})
    assert state.last_used.get("plugin:example") is not None


@pytest.mark.asyncio
async def test_a_skill_can_be_written_from_the_console_and_is_live_at_once(tmp_path: Path) -> None:
    """Guided creation: no YAML, and the result validates or nothing is written."""
    jarvis = await _install(tmp_path)
    made = await jarvis.services.async_call(
        "extensions",
        "scaffold",
        {
            "name": "bin-day",
            "description": "Which bin goes out, and on which night.",
            "tools": ["get_state", "web_search"],
        },
        blocking=True,
        return_response=True,
    )
    assert "bin-day" in made["skills"], made
    store = jarvis.data["skills"]
    assert "bin-day" in store.index_block()

    listed = await jarvis.services.async_call(
        "extensions", "list", {"kind": "skill"}, blocking=True, return_response=True
    )
    row = [r for r in listed["extensions"] if r["id"] == "bin-day"][0]
    # The permission the chosen tool requires was written for them: ticking
    # `web_search` and getting a file the validator then rejects is not guided.
    assert "network" in row["permissions"]
    assert row["origin"] == "user"


@pytest.mark.asyncio
async def test_scaffolding_refuses_a_name_that_is_nearly_a_path(tmp_path: Path) -> None:
    jarvis = await _install(tmp_path)
    for name in ("../escape", "Bin Day", "x"):
        refused = await jarvis.services.async_call(
            "extensions", "scaffold", {"name": name, "description": "x"},
            blocking=True, return_response=True,
        )
        assert "error" in refused, name
    clash = await jarvis.services.async_call(
        "extensions", "scaffold", {"name": "diary", "description": "Mine."},
        blocking=True, return_response=True,
    )
    assert "created" in clash, "a user skill may override a bundled one"


# --- the catalog (M47) -------------------------------------------------------
CATALOG = Path(__file__).resolve().parents[2] / "testing/fixtures/catalog"


def _catalog_source():
    from jarvis.integrations.extensions.catalog import Source

    return Source(name="fixture", url=CATALOG.as_uri(), kind="skill")


def test_nothing_installs_from_an_origin_nobody_allowed() -> None:
    """There is no default source, and that is the whole first defence.

    Shipping a list of URLs would mean every install trusts whoever owns them,
    forever, without anybody choosing to.
    """
    from jarvis.integrations.extensions.catalog import DEFAULT_SOURCES, Catalog, CatalogError

    assert DEFAULT_SOURCES == ()
    empty = Catalog()
    with pytest.raises(CatalogError) as caught:
        empty.source_for("github")
    assert "nobody allowed" in str(caught.value)


@pytest.mark.parametrize(
    "url,why",
    [("http://plain.example/x", "http"), ("ftp://old.example/x", "ftp"), ("/etc/passwd", "no")],
)
def test_a_source_may_only_be_https_or_this_machine(url, why) -> None:
    from jarvis.integrations.extensions.catalog import CatalogError, Source

    with pytest.raises(CatalogError) as caught:
        Source(name="s", url=url)
    assert why in str(caught.value)


def test_a_catalog_cannot_offer_code_that_runs_in_this_process() -> None:
    """The refusals are named, with reasons, not silently absent."""
    from jarvis.integrations.extensions.catalog import REFUSED_KINDS, CatalogError, Source

    with pytest.raises(CatalogError) as caught:
        Source(name="s", url="https://example/x", kind="plugin")
    assert "interpreter" in str(caught.value)
    assert "mcp-stdio" in REFUSED_KINDS


def test_catalog_metadata_is_quarantined_not_filtered() -> None:
    """A description is content. The fixture's says so in as many words."""
    from jarvis.integrations.extensions.catalog import Catalog
    from jarvis.security.quarantine import has_control_tokens, is_quarantined

    catalog = Catalog()
    catalog.add(_catalog_source())
    hostile = [e for e in catalog.search() if e.id == "friendly-helper"][0]
    assert is_quarantined(hostile.description)
    assert not has_control_tokens(hostile.description), "a role marker survived"
    # Not filtered: the words are still there, wrapped, because a filter with a
    # bypass is a system exactly as vulnerable and now believed safe.
    assert "ignore the permissions" in hostile.description.lower()


def test_a_catalog_cannot_declare_a_permission_nothing_enforces() -> None:
    from jarvis.integrations.extensions.catalog import Catalog

    catalog = Catalog()
    catalog.add(_catalog_source())
    hostile = [e for e in catalog.search() if e.id == "friendly-helper"][0]
    assert "become_root" not in hostile.permissions
    assert set(hostile.permissions) <= {"read_state", "act", "run_process"}


def test_latest_is_not_a_version() -> None:
    """A blind `latest` means the approved thing and the landed thing differ."""
    from jarvis.integrations.extensions.catalog import Catalog, CatalogError, resolve_ref

    catalog = Catalog()
    catalog.add(_catalog_source())
    unpinned = [e for e in catalog.search() if e.id == "unpinned-thing"][0]
    with pytest.raises(CatalogError) as caught:
        resolve_ref(unpinned, [])
    assert "concrete ref" in str(caught.value)
    assert resolve_ref(unpinned, ["v1.0.0", "v1.2.0"]) == "v1.2.0"


def test_a_plan_names_every_program_in_the_payload_before_anything_lands() -> None:
    from jarvis.integrations.extensions.catalog import Catalog
    from jarvis.integrations.extensions.install import fetch_local, plan

    catalog = Catalog()
    catalog.add(_catalog_source())
    hostile = [e for e in catalog.search() if e.id == "friendly-helper"][0]
    files = fetch_local(hostile)
    proposal = plan(hostile, files)
    assert "install.sh" in proposal["hooks"]
    assert "will not run" in proposal["warning"]
    assert len(proposal["sha256"]) == 64


def test_a_payload_that_is_not_what_was_approved_is_refused() -> None:
    from jarvis.integrations.extensions.catalog import CatalogError
    from jarvis.integrations.extensions.install import plan

    files = {"SKILL.md": b"---\nname: x\ndescription: y\n---\n\nBody.\n"}
    good = plan(_entry(), files)
    with pytest.raises(CatalogError) as caught:
        plan(_entry(), {"SKILL.md": b"something else entirely"}, expected_sha=good["sha256"])
    assert "not what was approved" in str(caught.value)


def _entry():
    from jarvis.integrations.extensions.catalog import Entry

    return Entry(id="x", kind="skill", source="fixture", url="file:///tmp/x")


@pytest.mark.parametrize(
    "path", ["../escape/SKILL.md", "/etc/SKILL.md", ".git/config", "a/b/c/d/e/SKILL.md"]
)
def test_a_payload_cannot_write_outside_its_own_folder(path) -> None:
    from jarvis.integrations.extensions.install import InstallError, read_payload

    with pytest.raises(InstallError):
        read_payload({path: b"x", "SKILL.md": b"---\nname: x\ndescription: y\n---\n"})


@pytest.mark.asyncio
async def test_install_refuses_without_an_approval(tmp_path: Path) -> None:
    """The step between knowing and doing, and a test standing in it."""
    from jarvis.integrations.extensions.catalog import Catalog
    from jarvis.integrations.extensions.install import InstallError, apply, fetch_local

    jarvis = await _install(tmp_path)
    catalog = Catalog()
    catalog.add(_catalog_source())
    entry = [e for e in catalog.search() if e.id == "bin-day"][0]
    files = fetch_local(entry)
    with pytest.raises(InstallError) as caught:
        apply(jarvis, entry, files, {})
    assert "nothing was approved" in str(caught.value)
    assert not (tmp_path / "skills" / "bin-day").exists()


@pytest.mark.asyncio
async def test_an_approved_skill_lands_and_nothing_in_it_runs(tmp_path: Path) -> None:
    """The acceptance criterion: it installs, and the hook never fires."""
    from jarvis.integrations.extensions.catalog import Catalog
    from jarvis.integrations.extensions.install import apply, fetch_local, plan

    marker = Path("/tmp/jarvis-catalog-probe-should-not-exist")
    marker.unlink(missing_ok=True)

    jarvis = await _install(tmp_path)
    catalog = Catalog()
    catalog.add(_catalog_source())
    entry = [e for e in catalog.search() if e.id == "friendly-helper"][0]
    files = fetch_local(entry)
    proposal = plan(entry, files)
    result = apply(jarvis, entry, files, proposal)

    assert result["installed"] == "friendly-helper"
    assert result["sha256"] == proposal["sha256"]
    assert result["ref"] == "v2.1.0", "it landed unpinned"
    assert "install.sh" in result["hooks"]
    # It is ON DISK, because it was in the payload...
    assert (tmp_path / "skills" / "friendly-helper" / "install.sh").exists()
    # ...and it never ran. This is the whole claim.
    assert not marker.exists(), "something in the payload executed"
    # And the skill itself loaded, so the install is real rather than inert.
    assert "friendly-helper" in jarvis.data["skills"].skills


@pytest.mark.asyncio
async def test_a_skill_that_does_not_validate_is_removed_again(tmp_path: Path) -> None:
    """Whole, or not at all: half-installed is in the prompt and not in the index."""
    from jarvis.integrations.extensions.install import apply, plan

    jarvis = await _install(tmp_path)
    entry = _entry()
    entry.id = "broken"
    body = (
        b"---\nname: broken\ndescription: Asks for a permission nobody enforces.\n"
        b"metadata:\n  permissions: [become_root]\n---\n\nBody.\n"
    )
    files = {"SKILL.md": body}
    proposal = plan(entry, files)
    with pytest.raises(Exception):
        apply(jarvis, entry, files, proposal)
    assert not (tmp_path / "skills" / "broken").exists(), "a rejected skill was left on disk"
    assert "broken" not in jarvis.data["skills"].skills


@pytest.mark.asyncio
async def test_the_services_refuse_an_install_that_skipped_the_plan(tmp_path: Path) -> None:
    jarvis = await _install(tmp_path)
    from jarvis.integrations.extensions.catalog import Catalog

    catalog = Catalog()
    catalog.add(_catalog_source())
    jarvis.data["extension_catalog"] = catalog

    listed = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert {e["id"] for e in listed["entries"]} >= {"bin-day", "friendly-helper"}

    refused = await jarvis.services.async_call(
        "extensions", "install", {"source": "fixture", "id": "bin-day"},
        blocking=True, return_response=True,
    )
    assert "extensions.plan" in refused["error"]

    proposal = await jarvis.services.async_call(
        "extensions", "plan", {"source": "fixture", "id": "bin-day"},
        blocking=True, return_response=True,
    )
    assert proposal["plan"]["ref"] == "v1.0.0"
    done = await jarvis.services.async_call(
        "extensions", "install",
        {"source": "fixture", "id": "bin-day", "approved": proposal["plan"]},
        blocking=True, return_response=True,
    )
    assert done["installed"] == "bin-day"


@pytest.mark.asyncio
async def test_an_unconfigured_source_is_refused_by_the_service(tmp_path: Path) -> None:
    jarvis = await _install(tmp_path)
    from jarvis.integrations.extensions.catalog import Catalog

    jarvis.data["extension_catalog"] = Catalog()
    out = await jarvis.services.async_call(
        "extensions", "plan", {"source": "github", "id": "anything"},
        blocking=True, return_response=True,
    )
    assert "not a configured source" in out["error"]


def test_the_catalog_field_is_not_the_protocols_message_id() -> None:
    """`id` is the websocket envelope's message id, and was mine too.

    Reading the entry out of `msg["id"]` worked in every test that called the
    service directly and failed the moment a browser sent a real frame, because
    by then `id` was the integer the protocol uses to match a reply to its
    request. The browser test is what caught it.
    """
    from jarvis.api.common import _entry_id

    assert _entry_id({"id": 42, "entry": "bin-day"}) == "bin-day"
    assert _entry_id({"id": 42}) == "", "the message id was read as an entry id"
    assert _entry_id({"entry_id": "bin-day"}) == "bin-day"


# --- the shipped catalogue (M65) ---------------------------------------------
#
# "I can't browse the tools from the settings": the catalogue had no source
# by default, so there was nothing to browse. The one source that ships is
# this package's own skill folders — not a URL, not a default remote list —
# and these hold the index honest against the SKILL.md files beside it.


def _shipped_index() -> list[dict]:
    import json

    raw = json.loads((BUNDLED / "index.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict) and isinstance(raw.get("entries"), list)
    return raw["entries"]


def test_the_shipped_catalogue_is_the_package_folder_wherever_the_package_is() -> None:
    """Resolved from the skills package, so /srv/jarvis and a checkout both work.

    A path typed into the source would be right in exactly one of the two
    places the package lives, and the wrong one is the deployed image.
    """
    from jarvis.integrations.extensions.catalog import (
        BUNDLED_SOURCE,
        DEFAULT_SOURCES,
        bundled_source,
    )
    from jarvis.integrations.skills import BUNDLED_ROOT

    source = bundled_source()
    assert source.name == BUNDLED_SOURCE == "bundled"
    assert source.kind == "skill" and source.enabled
    assert source.url == BUNDLED_ROOT.as_uri() == BUNDLED.as_uri()
    assert source.url.startswith("file://")
    # The M47 refusal stands: nothing REMOTE is trusted by default.
    assert DEFAULT_SOURCES == ()


def test_every_shipped_entry_parses_and_names_a_shipped_folder() -> None:
    """Every entry goes through the same hostile-input parser a stranger's does."""
    from jarvis.integrations.extensions.catalog import bundled_source, entry_from_raw

    source = bundled_source()
    folders = sorted(p.name for p in BUNDLED.iterdir() if (p / "SKILL.md").is_file())
    raws = _shipped_index()
    assert raws, "the shipped index is empty"
    ids = []
    for raw in raws:
        entry = entry_from_raw(raw, source)  # raises CatalogError if not
        assert entry.kind == "skill"
        assert entry.source == "bundled"
        assert entry.ref and entry.ref.lower() != "latest", f"{entry.id} is unpinned"
        assert entry.permissions, f"{entry.id} declares no permission at all"
        ids.append(entry.id)
    assert sorted(ids) == folders, "the index and the folders beside it disagree"


def test_every_shipped_entry_points_inside_the_catalogue() -> None:
    """A relative url resolves against the index and stays under it."""
    from jarvis.integrations.extensions.catalog import bundled_source, read_local_catalog

    entries = read_local_catalog(BUNDLED, bundled_source())
    assert len(entries) == len(_shipped_index()), "an entry was skipped on the way in"
    for entry in entries:
        assert entry.url == (BUNDLED / entry.id).as_uri(), entry.url
        assert (BUNDLED / entry.id / "SKILL.md").is_file()


@pytest.mark.parametrize("raw", _shipped_index(), ids=lambda r: r["id"])
def test_the_shipped_index_agrees_with_the_skill_md_beside_it(raw: dict) -> None:
    """Description, version, author and permissions copied, and held equal.

    The index is what a person reads before installing; the SKILL.md is what
    the model reads after. A description that flatters, or a permission list
    shorter than the manifest's, is the catalogue lying about its own code.
    """
    from jarvis.integrations.skills import parse_skill_md

    path = BUNDLED / raw["id"] / "SKILL.md"
    skill = parse_skill_md(path.read_text(encoding="utf-8"), path)
    manifest = skill_manifest(skill)
    assert raw["description"] == skill.description
    assert raw["version"] == skill.version
    assert raw.get("author") == skill.metadata.get("author")
    assert sorted(raw["permissions"]) == sorted(manifest.permissions)


@pytest.mark.asyncio
async def test_a_fresh_install_browses_the_shipped_skills_with_no_error(tmp_path: Path) -> None:
    """The operator's complaint, as a test: browse answers something, at once.

    No configuration, no URL — and every shipped skill is already loaded, so
    the answer says INSTALLED rather than offering to install what is in the
    prompt already.
    """
    jarvis = await _install(tmp_path)
    out = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert "error" not in out, out
    assert out["sources"] == ["bundled"]
    assert out["errors"] == []
    ids = sorted(e["id"] for e in out["entries"])
    assert ids == sorted(r["id"] for r in _shipped_index())
    assert all(e["installed"] is True for e in out["entries"]), out["entries"]
    assert all(e["source"] == "bundled" for e in out["entries"])

    sources = await jarvis.services.async_call(
        "extensions", "sources", {}, blocking=True, return_response=True
    )
    assert [s["name"] for s in sources["sources"]] == ["bundled"]
    assert "own skills" in sources["note"] and "no default remote source" in sources["note"]


@pytest.mark.asyncio
async def test_the_catalogue_says_which_shipped_skills_are_not_loaded(tmp_path: Path) -> None:
    """`skills: bundled: false` turns the skills off; the catalogue still offers them.

    `installed` is the difference, and installing one from here lands a copy
    in the operator's folder — which is what "use the shipped diary skill
    after turning the bundle off" actually means.
    """
    from jarvis.llm.tools import ToolRegistry

    jarvis = Jarvis(config_dir=str(tmp_path))
    jarvis.data["llm_tools"] = ToolRegistry(jarvis)
    store = SkillStore(tmp_path / "skills", bundled_root=None)
    store.load()
    jarvis.data["skills"] = store
    assert await extensions_setup(jarvis, None) is True

    out = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert "error" not in out
    assert {e["id"]: e["installed"] for e in out["entries"]}["diary"] is False

    proposal = await jarvis.services.async_call(
        "extensions", "plan", {"source": "bundled", "id": "diary"},
        blocking=True, return_response=True,
    )
    assert proposal["plan"]["ref"] == "v1"
    assert proposal["plan"]["hooks"] == [], "a shipped skill ships no program"
    assert proposal["plan"]["permissions"] == ["read_state", "act"]
    done = await jarvis.services.async_call(
        "extensions", "install",
        {"source": "bundled", "id": "diary", "approved": proposal["plan"]},
        blocking=True, return_response=True,
    )
    assert done["installed"] == "diary"
    assert (tmp_path / "skills" / "diary" / "SKILL.md").is_file()

    again = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert {e["id"]: e["installed"] for e in again["entries"]}["diary"] is True


@pytest.mark.asyncio
async def test_installing_a_shipped_skill_lands_the_copy_that_overrides_it(tmp_path: Path) -> None:
    """With the bundle on, INSTALL is still a real action: the operator's copy wins."""
    from jarvis.integrations.extensions.install import apply, fetch_local, plan, prepare

    jarvis = await _install(tmp_path)
    catalog = jarvis.data["extension_catalog"]
    entry = prepare(catalog, "bundled", "note-taking")
    files = fetch_local(entry)
    assert set(files) == {"SKILL.md"}
    result = apply(jarvis, entry, files, plan(entry, files))
    assert result["installed"] == "note-taking"
    store = jarvis.data["skills"]
    assert str(store.get("note-taking").path).startswith(str(tmp_path)), "the shipped copy still wins"
    registry = jarvis.data["extensions"]
    registry.index()
    assert registry.get("skill:note-taking").origin == "user"


def test_the_operator_can_turn_the_bundled_source_off_or_replace_it() -> None:
    """A source called `bundled` in configuration.yaml is theirs, not ours.

    No second key: `enabled: false` on that line is the off switch, and a
    different url on it is a replacement. The built-in never overrides a
    person's line.
    """
    from jarvis.integrations.extensions import _build_catalog

    default = _build_catalog(None)
    assert list(default.sources) == ["bundled"] and default.sources["bundled"].enabled

    off = _build_catalog(
        {"sources": [{"name": "bundled", "url": "file:///nowhere", "enabled": False}]}
    )
    assert off.sources["bundled"].enabled is False
    assert off.sources["bundled"].url == "file:///nowhere", "the built-in overrode the operator"

    theirs = _build_catalog({"sources": [{"name": "mine", "url": "file:///home/x/skills"}]})
    assert sorted(theirs.sources) == ["bundled", "mine"], "their source did not keep the bundle"


@pytest.mark.asyncio
async def test_browse_with_the_bundle_off_says_nothing_is_configured(tmp_path: Path) -> None:
    from jarvis.integrations.extensions.catalog import Catalog, Source

    jarvis = await _install(tmp_path)
    catalog = Catalog()
    catalog.add(Source(name="bundled", url="file:///nowhere", enabled=False))
    jarvis.data["extension_catalog"] = catalog
    out = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert out["error"] == "no catalog source is configured"
    assert out["entries"] == [] and out["sources"] == []


@pytest.mark.asyncio
async def test_a_source_that_cannot_be_read_is_reported_not_swallowed(tmp_path: Path) -> None:
    """The console draws the reason, never "nothing matched", for a broken source."""
    from jarvis.integrations.extensions.catalog import Catalog, Source

    jarvis = await _install(tmp_path)
    catalog = Catalog()
    catalog.add(Source(name="gone", url=(tmp_path / "missing").as_uri()))
    jarvis.data["extension_catalog"] = catalog
    entries, errors = catalog.read()
    assert entries == []
    assert errors and errors[0]["source"] == "gone" and "no catalog index" in errors[0]["error"]

    out = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert out["entries"] == [] and out["sources"] == ["gone"]
    assert out["errors"] == errors
    assert "gone:" in out["error"] and "no catalog index" in out["error"]

    # One broken source beside a working one is a warning, not an error.
    catalog.add(_catalog_source())
    both = await jarvis.services.async_call(
        "extensions", "browse", {}, blocking=True, return_response=True
    )
    assert "error" not in both
    assert [err["source"] for err in both["errors"]] == ["gone"]
    assert {e["id"] for e in both["entries"]} >= {"bin-day", "friendly-helper"}
    assert all(e["installed"] is False for e in both["entries"])
