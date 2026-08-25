"""Writing a new skill, from the console, without anybody editing YAML.

A management surface people have to edit a file behind is not a management
surface. The whole of "create a skill" is: pick a name, say what it is for,
tick the tools it may name — and get a `SKILL.md` that already validates
against the manifest schema, with the body left as a prompt to fill in rather
than as an empty file.

The name is the only part that is dangerous, because it becomes a directory:
everything here refuses rather than sanitises. A name that is *nearly* a path
traversal, quietly turned into something else, is how somebody ends up with a
skill they did not write in a folder they did not name.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .manifest import PERMISSIONS, TOOL_PERMISSIONS

#: What the console offers as a starting point. Deliberately not empty: an
#: empty body is a skill that says nothing and gets deleted, and the headings
#: are the ones the four shipped skills earned.
SKILL_TEMPLATE = """# {title}

## When this applies

<!-- The situation. A skill whose first line is "this skill helps with X" costs
     a model round trip to learn nothing. -->

## What to do

<!-- The instructions themselves. Numbered steps if the order matters. -->

## What not to do

<!-- The failure this skill exists to prevent. This section is usually the
     reason the skill is worth its place in the prompt. -->
"""

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class ScaffoldError(ValueError):
    """A skill that will not be written, and why."""


def scaffold_skill(
    root: Path | str,
    *,
    name: str,
    description: str,
    tools: list[str] | None = None,
    permissions: list[str] | None = None,
    body: str = "",
) -> Path:
    """Write `<root>/<name>/SKILL.md`. Returns the path.

    Refuses rather than overwrites: replacing a skill somebody wrote, because
    they typed a name that already existed, is not a thing a create button
    should be able to do.
    """
    folder_name = str(name or "").strip().lower()
    if not NAME_OK.match(folder_name):
        raise ScaffoldError(
            "a skill name is 2–64 characters of lowercase letters, digits and "
            f"hyphens, starting with a letter or digit — {name!r} is not"
        )
    summary = " ".join(str(description or "").split())
    if not summary:
        raise ScaffoldError("a skill needs a description: it is the only part the model sees first")
    if len(summary) > 600:
        raise ScaffoldError(f"the description is {len(summary)} characters; 600 is the limit")

    wanted_tools = [str(t).strip() for t in (tools or []) if str(t).strip()]
    wanted_permissions = {str(p).strip() for p in (permissions or []) if str(p).strip()}
    unknown = sorted(wanted_permissions - set(PERMISSIONS))
    if unknown:
        raise ScaffoldError(f"no such permission: {', '.join(unknown)}")
    # Add the permissions the chosen tools require, rather than writing a file
    # that the manifest validator will reject a second later. The operator
    # ticked the tool; the permission is what that means.
    for tool in wanted_tools:
        needed = TOOL_PERMISSIONS.get(tool)
        if needed:
            wanted_permissions.add(needed)
    if wanted_tools and "read_state" not in wanted_permissions:
        wanted_permissions.add("read_state")

    folder = Path(root) / folder_name
    target = folder / "SKILL.md"
    if target.exists():
        raise ScaffoldError(f"there is already a skill at {target}")

    front: dict[str, object] = {"name": folder_name, "description": summary}
    if wanted_tools:
        front["allowed-tools"] = wanted_tools
    metadata: dict[str, object] = {"author": "written in the console"}
    if wanted_permissions:
        metadata["permissions"] = sorted(wanted_permissions)
    front["metadata"] = metadata
    front["version"] = "1"

    title = folder_name.replace("-", " ").title()
    text = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True, default_flow_style=False)
        + "---\n\n"
        + (body.strip() + "\n" if body.strip() else SKILL_TEMPLATE.format(title=title))
    )
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
