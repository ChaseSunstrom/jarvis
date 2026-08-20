"""`skills` integration — procedures Jarvis can look up, install and write.

    skills:
      sources:                 # repositories skills may be installed from
        - anthropics/skills
      install_enabled: false   # an installed skill starts OFF regardless

## What a skill is for

A skill is a folder with a `SKILL.md` in it: frontmatter naming it and saying
WHEN to use it, then markdown instructions. It is Anthropic's Agent Skills
format, implemented rather than approximated, so a skill written for Claude
works here.

The reason to have them is the context window. A local 8B model reading forty
tool descriptions and three pages of house rules picks worse than one reading
eight skill names. So the model always sees the **catalogue** — one line per
skill — and reads a **body** only for the skill it chose, through
`open_skill`. That is progressive disclosure, and it is the whole point:
instructions for filing receipts do not compete with instructions for the
boiler until somebody mentions a receipt.

## The three ways a skill arrives

**Shipped.** `jarvis/skills/builtin/` — the procedures for Jarvis's own
capabilities, written here so the persona does not have to carry them.

**Written.** `create_skill` (Tier 3) puts one in `<config>/skills/`. This is
how Jarvis learns a procedure the household repeats: it writes it down, and
the next turn that matches gets it. Tier 3 because a skill persists and shapes
every later turn — that is a bigger thing than one action.

**Installed.** `install_skill` (Tier 3) fetches one from a repository on the
allow-list, and it arrives **disabled**. See `install.py`: a skill cannot be
fenced the way a web page is, because following it is the point, so the
control is a person reading it before it is switched on.

## What is deliberately missing

No tool deletes a skill — same rule as repositories and workflows. `forget`
exists on the API, for a person.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...skills.install import (
    DEFAULT_SOURCES,
    SkillSource,
    install_from_github,
    parse_reference,
    permits,
)
from ...skills.model import SkillError, check_skill_name, render_skill, skill_from_text
from ...skills.registry import STORE_KEY, SkillRegistry

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...services import ServiceCall

_LOGGER = logging.getLogger(__name__)

DOMAIN = "skills"

__all__ = [
    "DOMAIN",
    "SkillsConfig",
    "async_setup",
    "get_config",
    "get_registry",
    "listing_payload",
]


@dataclass
class SkillsConfig:
    sources: list[SkillSource] = field(default_factory=list)
    #: Whether an installed skill is switched on the moment it lands. Off, and
    #: an operator who sets it true has said they trust the allow-list.
    install_enabled: bool = False

    @classmethod
    def from_config(cls, config: Any) -> "SkillsConfig":
        data = config if isinstance(config, dict) else {}
        raw = data.get("sources")
        entries = raw if isinstance(raw, list) else list(DEFAULT_SOURCES)
        sources: list[SkillSource] = []
        for entry in entries:
            if isinstance(entry, dict):
                owner = str(entry.get("owner") or "").strip()
                repo = str(entry.get("repo") or "").strip()
                branch = str(entry.get("branch") or "main").strip()
                project = f"{owner}/{repo}" if owner and repo else ""
            else:
                project, _, branch = str(entry or "").strip().partition("@")
                branch = branch.strip() or "main"
            parts = [p for p in project.split("/") if p]
            if len(parts) != 2:
                _LOGGER.warning("skills: ignoring source %r — not owner/repo", entry)
                continue
            sources.append(SkillSource(owner=parts[0], repo=parts[1], branch=branch))
        return cls(
            sources=sources,
            install_enabled=bool(data.get("install_enabled")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": [
                {"project": s.project, "branch": s.branch} for s in self.sources
            ],
            "install_enabled": self.install_enabled,
        }


def get_registry(jarvis: "Jarvis") -> SkillRegistry | None:
    registry = jarvis.data.get(DOMAIN)
    return registry if isinstance(registry, SkillRegistry) else None


def get_config(jarvis: "Jarvis") -> SkillsConfig | None:
    registry = get_registry(jarvis)
    cfg = getattr(registry, "config", None)
    return cfg if isinstance(cfg, SkillsConfig) else None


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    from ...store import Store

    cfg = SkillsConfig.from_config(config)
    registry = SkillRegistry(
        jarvis.config_dir, Store(jarvis.config_dir, STORE_KEY), config=cfg
    )
    await registry.async_load()
    # `jarvis.data["skills"]` is the REGISTRY, not a settings dict:
    # `agent.skills_block` duck-types on `.catalogue()`, the same shape
    # `memory` uses. The config rides on the registry because both wanted this
    # key and the second assignment won without saying so.
    jarvis.data[DOMAIN] = registry

    _register_services(jarvis)
    _register_tools(jarvis)

    enabled = len(registry.enabled_skills())
    _LOGGER.info(
        "skills ready: %d loaded (%d on)%s",
        len(registry.skills),
        enabled,
        f", {len(registry.broken)} would not load" if registry.broken else "",
    )
    return True


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------
def listing_payload(jarvis: "Jarvis") -> dict[str, Any]:
    registry = get_registry(jarvis)
    cfg = get_config(jarvis) or SkillsConfig()
    return {
        "skills": registry.listing() if registry else [],
        "catalogue_chars": len(registry.catalogue()) if registry else 0,
        **cfg.as_dict(),
    }


async def async_create(
    jarvis: "Jarvis", name: str, description: str, body: str, *, license: str = ""
) -> tuple[dict[str, Any] | None, str]:
    """Write a skill into `<config>/skills/`. Returns `(row, "")` or `(None, why)`."""
    registry = get_registry(jarvis)
    if registry is None:
        return None, "the skills integration is not set up on this server"

    problem = check_skill_name(name)
    if problem:
        return None, problem
    wanted = str(name).strip()

    text = render_skill(wanted, str(description or ""), str(body or ""), license=license)
    try:
        # Parsed before it is written: a generator that emits something its own
        # loader rejects would leave a skill nobody can load, discovered at the
        # next restart rather than now.
        skill_from_text(text, name_hint=wanted)
    except SkillError as err:
        return None, str(err)

    existing = registry.get(wanted)
    if existing is not None and existing.source == "builtin":
        return None, (
            f"{wanted!r} is a skill that ships with Jarvis. Pick another name, "
            "or edit the file in <config>/skills/ to override it deliberately."
        )

    try:
        folder = registry.folder_for(wanted)
    except Exception as err:  # noqa: BLE001 - the path resolver's refusal
        return None, f"that name is not allowed here: {err}"

    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
    except OSError as err:
        return None, f"could not write {folder}: {err}"

    registry.reload()
    await registry.async_set_enabled(wanted, True)
    _LOGGER.info("skills: wrote %s", folder)
    skill = registry.get(wanted)
    return (skill.as_dict() if skill else {"name": wanted}), ""


async def async_install(
    jarvis: "Jarvis", reference_text: str, *, transport: Any = None
) -> tuple[dict[str, Any] | None, str]:
    """Fetch a skill from an allow-listed repository. It arrives disabled."""
    registry = get_registry(jarvis)
    cfg = get_config(jarvis)
    if registry is None or cfg is None:
        return None, "the skills integration is not set up on this server"

    try:
        reference = parse_reference(reference_text)
    except SkillError as err:
        return None, str(err)

    if not permits(cfg.sources, reference):
        allowed = ", ".join(s.project for s in cfg.sources) or "nothing yet"
        return None, (
            f"{reference.project} is not on the skill allow-list. Permitted: "
            f"{allowed}. Add it under `skills: sources:` in configuration.yaml."
        )

    source = next(
        (s for s in cfg.sources if s.project.lower() == reference.project.lower()), None
    )
    try:
        folder = registry.folder_for(reference.wanted_name)
    except Exception as err:  # noqa: BLE001
        return None, f"that name is not allowed here: {err}"
    if folder.exists():
        return None, (
            f"There is already a skill folder at {folder}. Remove it first if "
            "you want to reinstall."
        )

    try:
        installed = await install_from_github(
            reference,
            folder,
            branch=(source.branch if source else "main"),
            transport=transport,
        )
    except SkillError as err:
        _remove_tree(folder)
        return None, str(err)

    # Recorded in the skill's own file so the registry can tell an installed
    # skill from a written one after a restart, without a second store.
    _stamp_origin(folder, reference.project)
    registry.reload()
    if cfg.install_enabled:
        await registry.async_set_enabled(installed.name, True)

    skill = registry.get(installed.name)
    row = skill.as_dict() if skill else {"name": installed.name}
    row.update(
        {
            "files": installed.files,
            "strategy": installed.strategy,
            "caveat": installed.caveat,
        }
    )
    _LOGGER.info(
        "skills: installed %s from %s (%s)",
        installed.name,
        reference.project,
        installed.strategy,
    )
    return row, ""


def _stamp_origin(folder: Path, project: str) -> None:
    """Record where a skill came from, in its own frontmatter."""
    target = folder / "SKILL.md"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
        skill = skill_from_text(text, name_hint=folder.name)
    except (OSError, SkillError):  # pragma: no cover - just installed and parsed
        return
    if skill.origin:
        return
    body_start = text.index("---", 3)
    head = text[: body_start].rstrip()
    rest = text[body_start:]
    try:
        target.write_text(f"{head}\norigin: {project}\n{rest}", encoding="utf-8")
    except OSError:  # pragma: no cover
        _LOGGER.warning("skills: could not stamp the origin of %s", folder)


def _remove_tree(folder: Path) -> None:
    import shutil

    try:
        shutil.rmtree(folder)
    except OSError:  # pragma: no cover - best effort
        pass


async def async_forget(jarvis: "Jarvis", name: str) -> tuple[bool, str]:
    """Remove a skill Jarvis wrote or installed. Never a shipped one."""
    registry = get_registry(jarvis)
    if registry is None:
        return False, "the skills integration is not set up on this server"
    skill = registry.get(str(name or ""))
    if skill is None:
        return False, f"There is no skill called {name!r}."
    if skill.source == "builtin":
        return False, (
            f"{skill.name} ships with Jarvis and cannot be removed. Switch it "
            "off instead."
        )
    _remove_tree(Path(skill.path))
    registry.reload()
    return True, f"Removed {skill.name}."


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------
def _register_services(jarvis: "Jarvis") -> None:
    async def handle_list(call: "ServiceCall") -> dict[str, Any]:
        return {"status": "ok", **listing_payload(jarvis)}

    async def handle_open(call: "ServiceCall") -> dict[str, Any]:
        registry = get_registry(jarvis)
        skill = registry.get(str(call.get("name") or "")) if registry else None
        if skill is None:
            return {"status": "error", "error": f"no skill called {call.get('name')!r}"}
        return {"status": "ok", "skill": skill.as_dict(with_body=True)}

    async def handle_create(call: "ServiceCall") -> dict[str, Any]:
        row, why = await async_create(
            jarvis,
            str(call.get("name") or ""),
            str(call.get("description") or ""),
            str(call.get("body") or ""),
        )
        return {"status": "ok", "skill": row} if row else {"status": "error", "error": why}

    async def handle_install(call: "ServiceCall") -> dict[str, Any]:
        row, why = await async_install(jarvis, str(call.get("reference") or ""))
        return {"status": "ok", "skill": row} if row else {"status": "error", "error": why}

    async def handle_enable(call: "ServiceCall") -> dict[str, Any]:
        registry = get_registry(jarvis)
        if registry is None:
            return {"status": "error", "error": "no skills registry"}
        ok, note = await registry.async_set_enabled(
            str(call.get("name") or ""), bool(call.get("enabled"))
        )
        return {"status": "ok" if ok else "error", "message": note}

    jarvis.services.register(
        DOMAIN, "list", handle_list, supports_response=True,
        description="Every skill, with whether it is on and where it came from.",
    )
    jarvis.services.register(
        DOMAIN, "open", handle_open, supports_response=True,
        description="One skill's full instructions.",
        fields={"name": {"description": "The skill's name.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN, "create", handle_create, supports_response=True,
        description="Write a new skill into the config directory.",
        fields={
            "name": {"description": "lowercase, becomes a directory", "required": True},
            "description": {"description": "WHEN to use it", "required": True},
            "body": {"description": "the instructions, markdown", "required": True},
        },
    )
    jarvis.services.register(
        DOMAIN, "install", handle_install, supports_response=True,
        description="Install a skill from an allow-listed repository. Arrives off.",
        fields={"reference": {"description": "owner/repo/path", "required": True}},
    )
    jarvis.services.register(
        DOMAIN, "set_enabled", handle_enable, supports_response=True,
        description="Switch a skill on or off.",
        fields={
            "name": {"description": "The skill's name.", "required": True},
            "enabled": {"description": "true or false."},
        },
    )


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
def _register_tools(jarvis: "Jarvis") -> None:
    registry_obj = jarvis.data.get("llm_tools")
    if registry_obj is None or not hasattr(registry_obj, "register"):
        _LOGGER.debug("skills: no LLM tool registry; the services still work")
        return

    from ...llm.tools import TIER_APPROVAL, TIER_DIRECT, schema_object

    async def tool_open(args: dict[str, Any], context: Any = None) -> Any:
        skills = get_registry(jarvis)
        wanted = str(args.get("name") or "").strip()
        skill = skills.get(wanted) if skills else None
        if skill is None:
            names = ", ".join(s.name for s in skills.enabled_skills()) if skills else ""
            return {
                "status": "error",
                "error": f"There is no skill called {wanted!r}. There is: {names or 'none'}",
            }
        if not skill.enabled:
            return {
                "status": "error",
                "error": (
                    f"{skill.name} is switched off. It is in the console's "
                    "SKILLS page if the user wants it on."
                ),
            }
        wanted_file = str(args.get("file") or "").strip()
        if wanted_file:
            try:
                return {
                    "status": "ok",
                    "name": skill.name,
                    "file": wanted_file,
                    "contents": skill.read(wanted_file),
                }
            except SkillError as err:
                return {"status": "error", "error": str(err)}

        files = skill.files()
        result = {
            "status": "ok",
            "name": skill.name,
            "instructions": skill.body,
            "files_at": skill.path,
        }
        if files:
            # Named rather than only pointed at. `files_at` was a path the
            # model had no way to read, which made the advice in
            # `MAX_BODY_CHARS` — "move the detail into files beside SKILL.md" —
            # a promise nothing kept.
            result["files"] = files
            result["note"] = (
                "This skill has reference files. Call open_skill again with "
                f"`file` set to one of: {', '.join(files)}."
            )
        return result

    registry_obj.register(
        name="open_skill",
        description=(
            "Read one skill's full instructions, by name. The skill list in "
            "your prompt gives only names and when to use them — this is how "
            "you get the actual procedure. Call it BEFORE doing the kind of "
            "work a skill covers, and then follow what it says. Some skills "
            "name reference files; pass `file` to read one of those."
        ),
        parameters=schema_object(
            {
                "name": {"type": "string", "description": "the skill's name"},
                "file": {
                    "type": "string",
                    "description": (
                        "a reference file beside the skill, from the `files` "
                        "list a plain open returns"
                    ),
                },
            },
            ["name"],
        ),
        handler=tool_open,
        # Reading a procedure the operator installed. If this needed approval
        # the model would stop asking and guess, which is the failure the whole
        # feature exists to prevent.
        tier=TIER_DIRECT,
    )

    async def tool_create(args: dict[str, Any], context: Any = None) -> Any:
        row, why = await async_create(
            jarvis,
            str(args.get("name") or ""),
            str(args.get("description") or ""),
            str(args.get("body") or ""),
        )
        if row is None:
            return {"status": "error", "error": why}
        return {
            "status": "ok",
            "skill": row,
            "message": (
                f"Wrote the skill {row.get('name')!r}. It is on, and it will be "
                "in your skill list from the next turn."
            ),
        }

    registry_obj.register(
        name="create_skill",
        description=(
            "Write down a procedure as a reusable skill, so it is available on "
            "every later turn without being explained again. Use this when the "
            "user teaches you how they want something done, or when you work "
            "out a multi-step procedure worth keeping. `description` must say "
            "WHEN to use the skill — it is the only thing you will see later "
            "when deciding whether to open it."
        ),
        parameters=schema_object(
            {
                "name": {
                    "type": "string",
                    "description": "lowercase, e.g. `filing-receipts`; becomes a folder",
                },
                "description": {
                    "type": "string",
                    "description": "WHEN to use this skill, in one or two sentences",
                },
                "body": {
                    "type": "string",
                    "description": "the instructions themselves, in markdown",
                },
            },
            ["name", "description", "body"],
        ),
        handler=tool_create,
        # Tier 3: a skill persists and shapes every later turn. That is a
        # bigger thing than one action, and it is the one place a model can
        # write into its own future prompt.
        tier=TIER_APPROVAL,
    )

    async def tool_install(args: dict[str, Any], context: Any = None) -> Any:
        row, why = await async_install(jarvis, str(args.get("reference") or ""))
        if row is None:
            return {"status": "error", "error": why}
        note = (
            "It is installed but SWITCHED OFF — a skill is instructions, and "
            "somebody has to read it before it can affect a turn. The user "
            "turns it on in the console's SKILLS page."
        )
        if row.get("enabled"):
            note = "It is installed and on."
        return {"status": "ok", "skill": row, "message": f"{row.get('name')}: {note}"}

    registry_obj.register(
        name="install_skill",
        description=(
            "Install a skill from a permitted repository, given as "
            "`owner/repo/path/to/skill`. Use it when the user asks for a "
            "capability somebody has already written down. It arrives switched "
            "off until a person reads it."
        ),
        parameters=schema_object(
            {
                "reference": {
                    "type": "string",
                    "description": "owner/repo/path, e.g. anthropics/skills/skills/pdf",
                }
            },
            ["reference"],
        ),
        handler=tool_install,
        # Tier 3: this fetches text that will become part of a later system
        # prompt. See skills/install.py.
        tier=TIER_APPROVAL,
    )
