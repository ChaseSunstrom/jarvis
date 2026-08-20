"""Every skill Jarvis has, and the one line each gets in the prompt.

## The two lists

There are two, and confusing them is the whole failure mode this file exists
to avoid:

* the **catalogue** — every enabled skill's name and one-line description,
  injected into the system prompt on every turn. Bounded, because it competes
  with the persona, the entity list and the toolbox rule for a context window
  that a local 8B model fills quickly.
* the **body** — the actual instructions, loaded only when the model asks for
  that skill by name.

A model that had every body in front of it would be a model reading forty
pages to answer "turn the lights off". A model with no catalogue would never
know the skills existed. The split is the feature.

## Where they come from

    <config>/skills/<name>/SKILL.md     the operator's, and Jarvis's own
    jarvis/skills/builtin/<name>/       shipped with the image

Config wins on a name collision, so an operator can replace a shipped skill by
writing one with the same name — the same rule `configuration.yaml` follows
everywhere else.

## Enabled

Installed skills start disabled. A skill is INSTRUCTIONS THE MODEL FOLLOWS, so
one fetched off the internet is a stranger writing part of the system prompt;
`install.py` has the argument in full. The registry just honours the flag, and
keeps it in the store so it survives a restart.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .model import Skill, SkillError, load_skill

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "MAX_CATALOGUE_CHARS",
    "MAX_SKILLS",
    "STORE_KEY",
    "SkillRegistry",
    "builtin_dir",
]

STORE_KEY = "skills"
#: Enough for a large collection without crowding the persona. At ~120
#: characters a line this is roughly fifty skills, and a model choosing from
#: fifty descriptions is already at the edge of useful.
MAX_CATALOGUE_CHARS = 6000
MAX_SKILLS = 500


def builtin_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


class SkillRegistry:
    """The loaded skills, and what the prompt says about them."""

    def __init__(self, config_dir: Path, store: Any = None, config: Any = None) -> None:
        #: The integration's own settings, carried here because
        #: `jarvis.data["skills"]` is the REGISTRY — the agent duck-types on
        #: it having `.catalogue()`, the same way it does for `memory`. Two
        #: things wanted that key and the second one won silently.
        self.config = config
        self.config_dir = Path(config_dir)
        self.root = self.config_dir / "skills"
        self._store = store
        self.skills: dict[str, Skill] = {}
        #: name -> enabled, for names we have an explicit decision about.
        self._enabled: dict[str, bool] = {}
        #: Skills that would not load, kept so the console can say WHICH file
        #: is broken instead of silently showing a shorter list.
        self.broken: dict[str, str] = {}

    # --- loading ----------------------------------------------------------
    async def async_load(self) -> None:
        data = await self._store.load() if self._store is not None else None
        raw = (data or {}).get("enabled")
        self._enabled = {
            str(k): bool(v) for k, v in raw.items() if isinstance(raw, dict)
        } if isinstance(raw, dict) else {}
        self.reload()

    async def async_save(self) -> None:
        if self._store is None:
            return
        await self._store.save({"enabled": dict(self._enabled)})

    def reload(self) -> None:
        """Re-read both directories. Cheap enough to do on every change."""
        found: dict[str, Skill] = {}
        broken: dict[str, str] = {}

        for source, base in (("builtin", builtin_dir()), ("authored", self.root)):
            for folder in _folders(base):
                try:
                    skill = load_skill(folder, source=source)
                except SkillError as err:
                    broken[folder.name] = str(err)
                    _LOGGER.warning("skills: %s", err)
                    continue
                if len(found) >= MAX_SKILLS and skill.name not in found:
                    broken[folder.name] = f"more than {MAX_SKILLS} skills; not loaded"
                    continue
                # An installed skill records where it came from, and that is
                # what decides how it is described — not the directory it
                # happens to sit in.
                if skill.origin:
                    skill.source = "installed"
                # The operator's copy wins over a shipped one of the same
                # name, which is how every other override in this codebase
                # works.
                found[skill.name] = skill

        for name, skill in found.items():
            skill.enabled = self._enabled.get(name, _default_enabled(skill))

        self.skills = dict(sorted(found.items()))
        self.broken = broken

    # --- reading ----------------------------------------------------------
    def get(self, name: str) -> Skill | None:
        return self.skills.get(str(name or "").strip())

    def enabled_skills(self) -> list[Skill]:
        return [s for s in self.skills.values() if s.enabled]

    def listing(self) -> list[dict[str, Any]]:
        rows = [s.as_dict() for s in self.skills.values()]
        rows.extend(
            {
                "name": name,
                "description": "",
                "source": "broken",
                "enabled": False,
                "license": "",
                "version": "",
                "origin": "",
                "chars": 0,
                "problem": problem,
            }
            for name, problem in sorted(self.broken.items())
        )
        return rows

    def catalogue(self, limit: int = MAX_CATALOGUE_CHARS) -> str:
        """The block that goes in the system prompt, or "".

        Truncated by whole lines and SAYS it was: a list that silently stops
        at forty of sixty skills makes the model confidently certain the other
        twenty do not exist.
        """
        lines: list[str] = []
        used = 0
        skipped = 0
        for skill in self.enabled_skills():
            line = skill.catalogue_line()
            if used + len(line) + 1 > limit:
                skipped += 1
                continue
            lines.append(line)
            used += len(line) + 1
        if not lines and not skipped:
            return ""
        if skipped:
            # Said even when NOTHING fit. A silently empty catalogue tells the
            # model there are no skills, which is a confident wrong answer;
            # "there are twelve, ask by name" is a useful one.
            lines.append(
                f"({skipped} more not listed — ask for them by name if the user "
                "mentions something none of the above covers.)"
            )
        return "\n".join(lines)

    # --- changing ---------------------------------------------------------
    async def async_set_enabled(self, name: str, enabled: bool) -> tuple[bool, str]:
        skill = self.get(name)
        if skill is None:
            return False, f"There is no skill called {name!r}."
        self._enabled[skill.name] = bool(enabled)
        skill.enabled = bool(enabled)
        await self.async_save()
        return True, f"{skill.name} is {'on' if enabled else 'off'}."

    def folder_for(self, name: str) -> Path:
        """Where a skill of this name would be written. Confined to the root."""
        from ..integrations.files.paths import resolve_local

        return resolve_local(self.root, str(name or "").strip())


def _default_enabled(skill: Skill) -> bool:
    """A skill nobody has decided about.

    Shipped and locally-written skills are on — they are the operator's own.
    An INSTALLED one is off until a person has read it, because its body is
    part of the system prompt and its author is a stranger.
    """
    return not skill.origin


def _folders(base: Path) -> list[Path]:
    try:
        return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
