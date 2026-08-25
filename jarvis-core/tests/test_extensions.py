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
