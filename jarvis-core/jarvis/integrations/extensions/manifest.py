"""The manifest every extensible thing carries, and the schema it must meet.

Three subsystems already existed when this was written — `skills/` reads
`SKILL.md` folders, `mcp/` talks to servers, `plugins/` registers tools from
Python classes — and each described itself differently. Nothing could answer
"what is installed, what may it reach, and is it working", because the answer
lived in three shapes.

This is the fourth shape, and the only one anything outside those subsystems
uses. It is DERIVED from what each subsystem already has rather than stored
beside it: a skill's manifest comes out of its `SKILL.md` frontmatter, which is
the open Agent Skills format and has to stay portable — a skill written here
must still load in Claude Code, and one written there must still load here. A
second file next to `SKILL.md` would have broken that on the first day.

The schema is a real JSON Schema document (`manifest.schema.json`) so an author
can read it, and is enforced by [validate] below rather than by a library: this
package installs from wheels with no compiler and adding `jsonschema` to buy
the eleven keywords used here is not a trade worth making. [validate]
implements exactly those keywords and `test_manifest.py` holds the two in step
by checking every keyword the document actually uses is one it knows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The schema document, read once. Ships inside the package.
SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")

KIND_SKILL = "skill"
KIND_MCP = "mcp"
KIND_PLUGIN = "plugin"
KINDS = (KIND_SKILL, KIND_MCP, KIND_PLUGIN)

#: Every permission a manifest may declare. Closed on purpose — see the schema.
PERMISSIONS = (
    "read_state",
    "act",
    "memory_read",
    "memory_write",
    "network",
    "filesystem_read",
    "filesystem_write",
    "run_process",
)

#: Tools whose use REQUIRES a permission, beyond the read/act split below.
#:
#: The point of the mapping is under-declaration: a manifest that lists
#: `write_file` in its tool allowlist while declaring no `filesystem_write` is
#: describing something other than what it does, and the difference is exactly
#: what somebody reading the manifest would rely on.
TOOL_PERMISSIONS: dict[str, str] = {
    "web_search": "network",
    "web_fetch": "network",
    "web_browse": "network",
    "web_crawl": "network",
    "deep_research": "network",
    "list_files": "filesystem_read",
    "read_file": "filesystem_read",
    "search_files": "filesystem_read",
    "write_file": "filesystem_write",
    "recall": "memory_read",
    "remember": "memory_write",
    "forget": "memory_write",
    "note_search": "memory_read",
    "note_create": "memory_write",
    "note_append": "memory_write",
    "execute_command": "run_process",
    "run_script": "run_process",
    "code_task": "run_process",
    "start_coding_job": "run_process",
}


class ManifestError(ValueError):
    """A manifest that will not be loaded, and why.

    Carries every problem rather than the first, because an author fixing a
    manifest one message at a time is an author who stops reading them.
    """

    def __init__(self, problems: list[str], *, source: str = "") -> None:
        self.problems = list(problems)
        self.source = source
        where = f"{source}: " if source else ""
        super().__init__(where + "; ".join(self.problems))


# --- the validator ----------------------------------------------------------
#
# A JSON Schema subset: the keywords `manifest.schema.json` actually uses, and
# no others. An unknown keyword is a test failure rather than a silent pass,
# which is the failure mode a hand-written validator really has — the schema
# grows a `minimum`, nothing enforces it, and the schema starts describing a
# stricter document than the one being accepted.

KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "pattern",
        "minLength",
        "maxLength",
        "maxItems",
        "minItems",
        "uniqueItems",
    }
)

_TYPES: dict[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def schema() -> dict[str, Any]:
    """The schema document."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(instance: Any, spec: dict[str, Any], *, path: str = "") -> list[str]:
    """Every way `instance` fails `spec`, as readable sentences.

    Does NOT stop at the first problem, and does not raise: the caller decides
    whether a list of problems is fatal. It is, everywhere in this package.
    """
    problems: list[str] = []
    where = path or "the manifest"

    expected = spec.get("type")
    if expected:
        want = _TYPES.get(expected)
        # `True` is an `int` in Python and would pass an integer check.
        ok = isinstance(instance, want) and not (
            expected in ("integer", "number") and isinstance(instance, bool)
        )
        if not ok:
            got = type(instance).__name__
            return [f"{where} must be {expected}, not {got}"]

    if "enum" in spec and instance not in spec["enum"]:
        allowed = ", ".join(repr(v) for v in spec["enum"])
        return [f"{where} must be one of: {allowed} (got {instance!r})"]

    if isinstance(instance, str):
        pattern = spec.get("pattern")
        if pattern and not re.search(pattern, instance):
            problems.append(f"{where} does not match {pattern}")
        if "minLength" in spec and len(instance) < spec["minLength"]:
            problems.append(f"{where} is shorter than {spec['minLength']} characters")
        if "maxLength" in spec and len(instance) > spec["maxLength"]:
            problems.append(f"{where} is longer than {spec['maxLength']} characters")

    if isinstance(instance, list):
        if "maxItems" in spec and len(instance) > spec["maxItems"]:
            problems.append(f"{where} has more than {spec['maxItems']} entries")
        if "minItems" in spec and len(instance) < spec["minItems"]:
            problems.append(f"{where} has fewer than {spec['minItems']} entries")
        if spec.get("uniqueItems"):
            seen: list[Any] = []
            for item in instance:
                if item in seen:
                    problems.append(f"{where} repeats {item!r}")
                    break
                seen.append(item)
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for index, item in enumerate(instance):
                problems.extend(validate(item, item_spec, path=f"{where}[{index}]"))

    if isinstance(instance, dict):
        properties = spec.get("properties") or {}
        for name in spec.get("required") or []:
            if name not in instance:
                problems.append(f"{where} is missing {name!r}")
        if spec.get("additionalProperties") is False:
            for name in instance:
                if name not in properties:
                    problems.append(f"{where} has an unknown key {name!r}")
        for name, value in instance.items():
            sub = properties.get(name)
            if isinstance(sub, dict):
                label = f"{name}" if not path else f"{where}.{name}"
                problems.extend(validate(value, sub, path=label))

    return problems


@dataclass
class Manifest:
    """What is installed, and what it says it needs.

    Built only by [from_raw], so a `Manifest` in hand has already met the
    schema — there is no half-valid one to check for downstream.
    """

    id: str
    kind: str
    version: str
    description: str
    author: str = ""
    source_url: str = ""
    permissions: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    network_needs: bool = False
    network_hosts: tuple[str, ...] = ()
    fs_read: tuple[str, ...] = ()
    fs_write: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Unique across kinds: two subsystems may each have a `calendar`."""
        return f"{self.kind}:{self.id}"

    @classmethod
    def from_raw(cls, raw: Any, *, source: str = "") -> "Manifest":
        """Validate, then build. Raises [ManifestError] with every problem.

        There is no partial success: a manifest that fails here is not loaded
        at all, rather than loaded with the bad keys dropped. Dropping them is
        how a `tools` list with one unparseable entry becomes an extension with
        a shorter allowlist than its author wrote and a wider one than they
        meant.
        """
        if not isinstance(raw, dict):
            raise ManifestError([f"a manifest must be an object, not {type(raw).__name__}"], source=source)
        problems = validate(raw, schema())
        if problems:
            raise ManifestError(problems, source=source)
        network = raw.get("network") or {}
        filesystem = raw.get("filesystem") or {}
        manifest = cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            version=str(raw["version"]),
            description=str(raw["description"]),
            author=str(raw.get("author") or ""),
            source_url=str(raw.get("source_url") or ""),
            permissions=tuple(raw.get("permissions") or ()),
            tools=tuple(raw.get("tools") or ()),
            network_needs=bool(network.get("needs", False)),
            network_hosts=tuple(network.get("hosts") or ()),
            fs_read=tuple(filesystem.get("read") or ()),
            fs_write=tuple(filesystem.get("write") or ()),
        )
        under = manifest.under_declared()
        if under:
            raise ManifestError(
                [
                    "declares tools it has not asked permission for: "
                    + ", ".join(f"{tool} needs {perm}" for tool, perm in under)
                ],
                source=source,
            )
        return manifest

    def under_declared(self) -> list[tuple[str, str]]:
        """Tools in the allowlist whose permission is not declared.

        The check the schema cannot do: the schema says a permission list is
        well formed, this says it is honest. Read-only tools are not listed
        here individually — `act` covers everything that changes something, and
        the caller passes the read-only set in via [needs_act].
        """
        missing: list[tuple[str, str]] = []
        declared = set(self.permissions)
        for tool in self.tools:
            needed = TOOL_PERMISSIONS.get(tool)
            if needed and needed not in declared:
                missing.append((tool, needed))
        return missing

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "source_url": self.source_url,
            "permissions": list(self.permissions),
            "tools": list(self.tools),
            "network": {"needs": self.network_needs, "hosts": list(self.network_hosts)},
            "filesystem": {"read": list(self.fs_read), "write": list(self.fs_write)},
        }


def needs_act(tools: tuple[str, ...] | list[str], read_only: frozenset[str]) -> bool:
    """True when any listed tool changes something.

    Kept out of [Manifest] because the read-only set belongs to `llm.tools` and
    importing it here would make a data model depend on the tool registry.
    """
    return any(tool not in read_only for tool in tools)


@dataclass
class Record:
    """One extension as the registry holds it: the manifest, plus live state."""

    manifest: Manifest
    #: `bundled` (ships with Jarvis), `user` (the operator's directory or
    #: config) or `remote` (installed from a catalog — M47).
    origin: str = "user"
    enabled: bool = True
    #: Where it came from on disk or on the network, for a person reading a list.
    location: str = ""
    #: Filled by the registry's health pass. `{}` means "not asked yet".
    health: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = self.manifest.as_dict()
        out.update(
            {
                "origin": self.origin,
                "enabled": self.enabled,
                "location": self.location,
                "health": dict(self.health),
            }
        )
        return out
