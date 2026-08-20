"""What a skill is, and how one is read off disk.

A skill is a folder with a `SKILL.md` in it: YAML frontmatter naming the skill
and saying when to use it, then markdown instructions. This is Anthropic's
Agent Skills format, implemented deliberately rather than approximately, so a
skill written for Claude works here and one written here works there.

    ---
    name: filing-receipts
    description: Use when the user wants a receipt filed. Covers the n8n
      workflow, the folder layout, and what to do about VAT.
    ---

    # Filing a receipt
    ...

## Why a skill rather than a longer system prompt

Because the prompt has to fit, and a local 8B model reading forty tools and
three pages of house rules picks worse than one reading eight names.

The whole value is **progressive disclosure**: the model always sees each
skill's `name` and `description` — one line each, a few hundred characters for
the lot — and sees the BODY only for the skill it chose. The instructions for
filing receipts do not compete for attention with the instructions for
debugging the boiler until somebody mentions a receipt.

That is also why `description` is the field that matters most and is validated
hardest. It is the only thing the model uses to decide, and a description that
says what the skill IS rather than WHEN TO USE IT is a skill that never fires.

## Frontmatter, and why the parser is strict about very little

`name` and `description` are required; `license`, `version`, `metadata` and
`allowed-tools` are read when present and otherwise ignored. Unknown keys are
kept rather than rejected — this format is shared with other tools, it gains
fields, and a skill that fails to load here because it carries a key some
other runtime understands would be a bad trade.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "SKILL_FILE",
    "Skill",
    "SkillError",
    "check_skill_name",
    "parse_skill",
    "render_skill",
]

SKILL_FILE = "SKILL.md"

#: A description is a line in a list the model reads every single turn. Long
#: ones crowd out the other skills, which is the opposite of the point.
MAX_DESCRIPTION_CHARS = 1024
MIN_DESCRIPTION_CHARS = 12
#: A body is loaded into one turn's context on demand. Big is fine; unbounded
#: is a downloaded file deciding how much of the window it takes.
MAX_BODY_CHARS = 60_000
MAX_NAME_CHARS = 64

#: Lowercase, because a skill name is a directory name and a case-insensitive
#: filesystem would let `PDF` and `pdf` be two entries and one folder.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Names that mean something else to a filesystem or to this package.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", "com1", "lpt1", ".", "..", "builtin", "skills"}
)

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


class SkillError(ValueError):
    """A skill that cannot be used, and the sentence saying why."""


def check_skill_name(name: Any) -> str:
    """"" if usable as a skill name, else why not."""
    text = str(name or "").strip()
    if not text:
        return "A skill needs a name."
    if len(text) > MAX_NAME_CHARS:
        return f"That name is {len(text)} characters; the limit is {MAX_NAME_CHARS}."
    if text != text.lower():
        return (
            "Use lowercase: a skill name is a directory, and some filesystems "
            "do not tell “PDF” from “pdf”."
        )
    if not _NAME_RE.match(text):
        return (
            "Use lowercase letters, digits, dot, dash and underscore, starting "
            "with a letter or digit. No spaces or slashes."
        )
    if ".." in text:
        return "A name may not contain “..”."
    if text in _RESERVED:
        return f"“{text}” is reserved."
    return ""


@dataclass
class Skill:
    """One skill, as loaded."""

    name: str
    description: str
    body: str
    #: Where it came from: `builtin`, `installed` (a forge), `authored`
    #: (Jarvis or the operator wrote it here).
    source: str = "authored"
    #: The folder, so the body can name a file beside it.
    path: str = ""
    license: str = ""
    version: str = ""
    #: Everything else the frontmatter carried. Kept, not rejected — this
    #: format is shared and it gains fields.
    extra: dict[str, Any] = field(default_factory=dict)
    #: An installed skill starts OFF. See `install.py` for why.
    enabled: bool = True

    @property
    def origin(self) -> str:
        return str(self.extra.get("origin") or "")

    def as_dict(self, *, with_body: bool = False) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "enabled": self.enabled,
            "license": self.license,
            "version": self.version,
            "origin": self.origin,
            "chars": len(self.body),
        }
        if with_body:
            row["body"] = self.body
        return row

    def catalogue_line(self, limit: int = 240) -> str:
        """The one line the model reads every turn.

        Truncated hard: this competes with every other skill for the model's
        attention, and a skill whose description runs to a paragraph is one
        that pushed three others out of the window.
        """
        said = " ".join(self.description.split())
        if len(said) > limit:
            said = said[: limit - 1].rstrip() + "…"
        return f"{self.name}: {said}"


def parse_skill(text: str, *, name_hint: str = "") -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into `(frontmatter, body)`. Raises `SkillError`.

    `name_hint` is the folder name, used only in error messages — the name in
    the frontmatter is the authority, because that is what a skill written
    elsewhere carries.
    """
    where = f"{name_hint}/{SKILL_FILE}" if name_hint else SKILL_FILE
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        raise SkillError(
            f"{where} has no YAML frontmatter. A skill starts with a `---` "
            "line, then `name:` and `description:`, then another `---`."
        )
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError as err:
        raise SkillError(f"{where}: the frontmatter is not valid YAML — {err}") from None
    if not isinstance(loaded, dict):
        raise SkillError(f"{where}: the frontmatter has to be a mapping.")
    return loaded, (text[match.end() :] or "").strip()


def skill_from_text(
    text: str, *, source: str = "authored", path: str = "", name_hint: str = ""
) -> Skill:
    """One `SKILL.md`, validated. Raises `SkillError` with a usable sentence."""
    front, body = parse_skill(text, name_hint=name_hint)

    name = str(front.get("name") or name_hint or "").strip()
    problem = check_skill_name(name)
    if problem:
        raise SkillError(f"{name_hint or 'skill'}: {problem}")

    description = " ".join(str(front.get("description") or "").split())
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise SkillError(
            f"{name}: `description` is what the model reads to decide whether "
            "to use this skill, so it cannot be empty or a couple of words. "
            "Say WHEN to use it, not just what it is."
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillError(
            f"{name}: that description is {len(description)} characters; the "
            f"limit is {MAX_DESCRIPTION_CHARS}. It is one line in a list the "
            "model reads every turn."
        )
    if not body.strip():
        raise SkillError(f"{name}: there are no instructions under the frontmatter.")
    if len(body) > MAX_BODY_CHARS:
        raise SkillError(
            f"{name}: that body is {len(body)} characters; the limit is "
            f"{MAX_BODY_CHARS}. Move the detail into files beside SKILL.md and "
            "point at them."
        )

    known = {"name", "description", "license", "version"}
    return Skill(
        name=name,
        description=description,
        body=body,
        source=source,
        path=path,
        license=str(front.get("license") or "").strip()[:200],
        version=str(front.get("version") or "").strip()[:40],
        extra={k: v for k, v in front.items() if k not in known},
    )


def load_skill(folder: Path, *, source: str = "authored") -> Skill:
    """Read one skill folder. Raises `SkillError`."""
    target = folder / SKILL_FILE
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        raise SkillError(f"could not read {target}: {err}") from None
    return skill_from_text(
        text, source=source, path=str(folder), name_hint=folder.name
    )


def render_skill(
    name: str, description: str, body: str, *, license: str = "", version: str = ""
) -> str:
    """A `SKILL.md`, from parts. Used when Jarvis writes one.

    Round-trips through `skill_from_text` — a generator that emitted something
    its own parser rejects would be a skill nobody can load, discovered on the
    next restart rather than at the moment it was written.
    """
    front: dict[str, Any] = {"name": name, "description": description}
    if license:
        front["license"] = license
    if version:
        front["version"] = version
    # `sort_keys=False` so `name` leads, which is how every published skill
    # reads and how a person expects to find it.
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n\n{body.strip()}\n"
