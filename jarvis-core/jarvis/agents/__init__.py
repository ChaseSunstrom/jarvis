"""Subagents — drop a markdown file in, get a specialist. No code change.

An **agent definition** is a file under ``<config>/agents/<name>.md``: YAML
frontmatter, then the system prompt.

    ---
    name: researcher
    role: Finds things out on the web and reports what is actually there.
    tools: [web_search, web_fetch, note_create]
    model: ""                # "" means the house model
    max_tokens: 1200
    context_budget: 6000     # characters of instruction+task it may be given
    ---

    You are a researcher…

The shape is deliberately the one `skills/` already uses — the same
frontmatter-then-body format, the same "unknown keys are the format growing"
rule — because an operator who has written one should not have to learn a
second thing.

## What a definition may NOT do

* **It cannot grant itself a tool.** `tools:` NARROWS the lead's toolbox for
  that subagent; a name the lead does not have is dropped, and a Tier-3 tool
  never becomes Tier-1 by being listed here. `agents/` is a document folder,
  and a document does not get a vote on the tier system.
* **It cannot exceed its budget.** `context_budget` is enforced *before* the
  model call rather than trusted afterwards — see `llm/pool.py`. A subagent
  given a 40,000-character page to summarise is truncated, told so, and runs;
  the alternative is a call that fails after the tokens are spent.
* **It cannot spawn.** A subagent has no `delegate` tool, so the tree is one
  level deep by construction. Recursion is the failure mode that turns "look
  three things up" into forty model calls and a flat battery.

## Why files rather than a config block

The same reason as skills: a specialist is prose. `role:` and the body are
sentences somebody edits and re-reads, and putting a paragraph of system prompt
inside `configuration.yaml` makes both the paragraph and the YAML worse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "AgentDefinition",
    "DEFAULT_CONTEXT_BUDGET",
    "DEFAULT_MAX_TOKENS",
    "load_agents",
    "parse_agent",
]

#: How much instruction one subagent may be handed, in characters.
#:
#: Characters and not tokens, deliberately: the budget exists to stop a
#: subagent being handed a whole page, and "6,000 characters" is a thing an
#: operator can picture. The tokeniser's opinion is not available here without
#: importing the model client, and would change with the model.
DEFAULT_CONTEXT_BUDGET = 6000

#: What one subagent may say back. A specialist that returns 4,000 words has
#: not helped the lead, which has to read all of them.
DEFAULT_MAX_TOKENS = 1200

MAX_NAME = 60
MAX_ROLE = 200
#: A body longer than this is truncated with a marker. It is a system prompt,
#: and a system prompt the length of a novel is a design error, not a feature.
MAX_BODY = 8000


@dataclass(slots=True)
class AgentDefinition:
    """One specialist, as read from disk."""

    name: str
    role: str
    prompt: str
    #: Tool names this agent may use. Empty means "whatever the lead has",
    #: which is the honest default for a definition that did not say.
    tools: tuple[str, ...] = ()
    model: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    context_budget: int = DEFAULT_CONTEXT_BUDGET
    path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, prompt: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "tools": list(self.tools),
            "model": self.model,
            "max_tokens": self.max_tokens,
            "context_budget": self.context_budget,
            "path": str(self.path) if self.path else "",
        }
        if prompt:
            out["prompt"] = self.prompt
        return out

    def allowed(self, available: list[str]) -> list[str]:
        """The tools this agent actually gets, given what the lead has.

        Intersection, never union: a definition naming a tool the server does
        not register gets nothing for it, and cannot conjure one by asking.
        """
        if not self.tools:
            return list(available)
        have = set(available)
        return [name for name in self.tools if name in have]


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def parse_agent(text: str, path: Path | None = None) -> AgentDefinition:
    """Frontmatter and body, or a ValueError that says what is wrong."""
    raw = str(text or "")
    if not raw.lstrip().startswith("---"):
        raise ValueError("no YAML frontmatter (the file must start with ---)")
    rest = raw.lstrip()[3:]
    end = rest.find("\n---")
    if end == -1:
        raise ValueError("the frontmatter is never closed (expected a second ---)")
    front_text, body = rest[:end], rest[end + 4 :]

    try:
        front = yaml.safe_load(front_text) or {}
    except yaml.YAMLError as err:
        raise ValueError(f"the frontmatter is not valid YAML: {err}") from err
    if not isinstance(front, dict):
        raise ValueError("the frontmatter is not a mapping")

    name = _one_line(front.get("name"), MAX_NAME) or (path.stem if path else "")
    role = _one_line(front.get("role") or front.get("description"), MAX_ROLE)
    if not name:
        raise ValueError("the frontmatter has no `name`")
    if not role:
        # The lead picks a subagent by reading one line about each. A
        # definition with no role can never be chosen for the right reason.
        raise ValueError("the frontmatter has no `role`")

    tools = front.get("tools", front.get("allowed-tools"))
    if isinstance(tools, str):
        tools = [part.strip() for part in tools.split(",")]
    allowed = tuple(_one_line(t, MAX_NAME) for t in (tools or []) if _one_line(t, MAX_NAME))

    prompt = body.strip()[:MAX_BODY]
    if not prompt:
        raise ValueError("the body is empty, so the agent has no instructions")

    known = {"name", "role", "description", "tools", "allowed-tools", "model",
             "max_tokens", "context_budget"}
    metadata = {str(k): v for k, v in front.items() if k not in known}

    return AgentDefinition(
        name=name,
        role=role,
        prompt=prompt,
        tools=allowed,
        model=_one_line(front.get("model"), MAX_NAME),
        max_tokens=_bounded(front.get("max_tokens"), DEFAULT_MAX_TOKENS, 64, 8192),
        context_budget=_bounded(
            front.get("context_budget"), DEFAULT_CONTEXT_BUDGET, 500, 60_000
        ),
        path=path,
        metadata=metadata,
    )


def load_agents(directory: str | Path) -> dict[str, AgentDefinition]:
    """Every `*.md` in `directory`, by name. A broken one is skipped, loudly.

    Skipped rather than fatal: one malformed file must not stop the other three
    from loading, and a server that refuses to start because of a typo in a
    markdown file is a server nobody edits markdown files on.
    """
    root = Path(directory).expanduser()
    out: dict[str, AgentDefinition] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("."):
            continue
        try:
            agent = parse_agent(path.read_text(encoding="utf-8"), path)
        except (ValueError, OSError) as err:
            _LOGGER.warning("agents: ignoring %s — %s", path.name, err)
            continue
        if agent.name in out:
            _LOGGER.warning("agents: two definitions called %r; keeping the first", agent.name)
            continue
        out[agent.name] = agent
    return out
