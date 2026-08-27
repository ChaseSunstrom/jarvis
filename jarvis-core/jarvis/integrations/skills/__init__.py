"""skills — drop a folder in, teach Jarvis something. No code change.

A **skill** is a directory under ``<config>/skills/`` containing a ``SKILL.md``
in the open `Agent Skills <https://code.claude.com/docs/en/skills>`_ format:
YAML frontmatter, then a markdown body.

    skills/roasting/
      SKILL.md            <- frontmatter + the instructions
      references/         <- longer material the body can point at
      scripts/            <- programs, which this integration NEVER runs
      assets/

    ---
    name: roasting
    description: How this house roasts coffee — times, temperatures, the log.
    allowed-tools: [get_state, remember]
    metadata:
      owner: kitchen
    ---

    ## Roasting

    Preheat to 210 °C. …

Configuration (every key optional)::

    skills:
      path: skills          # relative to the config directory
      max_body_chars: 8000  # a skill body longer than this is truncated
      enabled: [roasting]   # load only these; omit for "all of them"

## Progressive disclosure, and why it is not optional

Every loaded skill contributes **one line** to the system prompt — its name and
its description — and nothing else. The body arrives only when the model calls
``use_skill``.

That is not a nicety. Twelve skills of two thousand words each is twenty-four
thousand words in front of every "turn the lights off": the context window
fills with instructions about coffee, the house summary falls off the end, and
the assistant gets worse at everything in exact proportion to how much you have
taught it. The index costs about fifteen words per skill.

## What a skill may NOT do

* **It cannot run anything.** `scripts/` beside a SKILL.md is material for a
  human or for the gated coding path; this integration reads files and never
  executes one. A skill that could run a program would be a shell script
  installed by dropping a markdown file in a folder.
* **It cannot grant itself tools.** ``allowed-tools`` NARROWS what the model
  may use while that skill's body is in play; it can never widen it, and it can
  never reach a Tier-3 tool. The tier system decides that in code
  (``llm/tools.py``), and a document in a folder does not get a vote.
* **It cannot become structure.** The body is fenced when it is returned to the
  model, and the index line is one line by construction: `name` and
  `description` are collapsed and clipped, so a description containing newlines
  and a fake "## System" heading cannot forge a prompt section. This is the
  same rule `memory` applies to notes, for the same reason — with one
  difference that matters: a skill is written by the operator, not by a web
  page, so the danger is a mistake rather than an attack.

Services
    ``skills.list``   → ``{"skills": [...]}``
    ``skills.reload`` → re-reads the directory; ``{"loaded": n, "errors": [...]}``
    ``skills.get``    (name) → the whole skill, body included

LLM tool: ``use_skill(name)`` — the body, on demand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "skills"
DEPENDENCIES = ["llm"]

#: Where skills live, relative to the config directory.
DEFAULT_PATH = "skills"
#: How much of a body is handed to the model in one go.
DEFAULT_MAX_BODY = 8000
#: How much of a description survives into the index line.
MAX_DESCRIPTION = 240
MAX_NAME = 64
#: A guard against a directory somebody dropped a repository into.
MAX_SKILLS = 64

DATA_STORE = "skills"

#: The one file that makes a directory a skill.
SKILL_FILE = "SKILL.md"

#: Where the shipped skills live: inside the package, so the path is right
#: wherever the package is — `/srv/jarvis/...` in the image, the checkout on a
#: bare host. Named here rather than computed twice: the extension catalogue
#: (M65) offers this same folder as its built-in source, and two places
#: spelling the path is how the catalogue would offer one folder while the
#: store loaded another.
BUNDLED_ROOT = Path(__file__).with_name("bundled")


@dataclass
class Skill:
    """One folder, read but never run."""

    name: str
    description: str
    body: str
    path: Path
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    #: Directories beside SKILL.md, so a surface can say what came with it.
    resources: tuple[str, ...] = ()

    def as_dict(self, body: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "metadata": dict(self.metadata),
            "version": self.version,
            "resources": list(self.resources),
            "path": str(self.path),
            "body_chars": len(self.body),
        }
        if body:
            out["body"] = self.body
        return out

    @property
    def index_line(self) -> str:
        return f"- {self.name}: {self.description}"


def one_line(text: Any, limit: int) -> str:
    """Collapse to a single clipped line.

    The index is a bullet list in the *system prompt*. A description with a
    newline in it could close the list and open a section of its own, which is
    how a helpful heading in a YAML file becomes an instruction the model
    treats as coming from the operator.
    """
    flat = " ".join(str(text or "").split())
    return flat[:limit].strip()


def parse_skill_md(text: str, path: Path | None = None) -> Skill:
    """Frontmatter and body, or a ValueError that says what is wrong.

    Strict about the two fields the index needs and forgiving about everything
    else: a skill with no `name` cannot be called and a skill with no
    `description` will never be chosen, so both are errors — while an unknown
    key is the format growing, and is kept in `metadata` rather than refused.
    """
    raw = str(text or "")
    if not raw.lstrip().startswith("---"):
        raise ValueError("no YAML frontmatter (the file must start with ---)")
    stripped = raw.lstrip()
    rest = stripped[3:]
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

    name = one_line(front.get("name"), MAX_NAME)
    description = one_line(front.get("description"), MAX_DESCRIPTION)
    if not name:
        raise ValueError("the frontmatter has no `name`")
    if not description:
        raise ValueError("the frontmatter has no `description`")

    tools = front.get("allowed-tools", front.get("allowed_tools"))
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",")]
    allowed = tuple(one_line(t, MAX_NAME) for t in (tools or []) if one_line(t, MAX_NAME))

    metadata = front.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    for key, value in front.items():
        if key not in ("name", "description", "allowed-tools", "allowed_tools",
                       "metadata", "version"):
            metadata.setdefault(str(key), value)

    directory = path.parent if path is not None else Path(".")
    resources = tuple(
        sorted(
            child.name
            for child in (directory.iterdir() if directory.is_dir() else [])
            if child.is_dir() and not child.name.startswith(".")
        )
    )
    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        path=path if path is not None else Path(SKILL_FILE),
        allowed_tools=allowed,
        metadata=metadata,
        version=one_line(front.get("version"), 32),
        resources=resources,
    )


class SkillStore:
    """Every skill in the directory, and the index the prompt gets."""

    def __init__(
        self,
        root: Path,
        max_body_chars: int = DEFAULT_MAX_BODY,
        enabled: list[str] | None = None,
        bundled_root: Path | None = None,
    ) -> None:
        self.root = Path(root)
        #: Skills that ship with Jarvis, read BEFORE the operator's directory.
        #:
        #: A same-named skill in `root` replaces the bundled one rather than
        #: colliding with it, which is the whole point: overriding
        #: `note-taking` should mean editing one file, not finding where the
        #: shipped copy lives and deleting it. The registry reports which is
        #: which, so an operator can see that they have overridden something.
        self.bundled_root = Path(bundled_root) if bundled_root else None
        self.max_body_chars = int(max_body_chars)
        self.enabled = [str(name) for name in (enabled or [])]
        self.skills: dict[str, Skill] = {}
        #: Files that could not be read, kept so a surface can show WHY a skill
        #: somebody dropped in is not there. A silent skip is how a typo in
        #: frontmatter becomes an afternoon.
        self.errors: list[dict[str, str]] = []

    # --- loading ----------------------------------------------------------
    def load(self) -> int:
        self.skills.clear()
        self.errors.clear()
        loaded = 0
        if self.bundled_root and self.bundled_root.is_dir():
            loaded += self._load_root(self.bundled_root, bundled=True)
        if not self.root.is_dir():
            _LOGGER.debug("skills: %s does not exist; %d bundled loaded", self.root, loaded)
            return loaded
        return self._load_root(self.root, bundled=False)

    def _load_root(self, root: Path, *, bundled: bool) -> int:
        for skill_md in sorted(root.glob(f"*/{SKILL_FILE}")):
            if len(self.skills) >= MAX_SKILLS:
                self.errors.append(
                    {"path": str(skill_md), "error": f"more than {MAX_SKILLS} skills"}
                )
                break
            try:
                skill = parse_skill_md(skill_md.read_text(encoding="utf-8"), skill_md)
            except (ValueError, OSError) as err:
                _LOGGER.warning("skills: %s could not be loaded: %s", skill_md, err)
                self.errors.append({"path": str(skill_md), "error": str(err)})
                continue
            if self.enabled and skill.name not in self.enabled:
                continue
            existing = self.skills.get(skill.name)
            if existing is not None:
                # Two in the SAME root is a mistake and is reported. The
                # operator's copy silently replacing a bundled one is the
                # documented override, and is not.
                if bundled or (self.bundled_root is None) or not str(
                    existing.path
                ).startswith(str(self.bundled_root)):
                    self.errors.append(
                        {
                            "path": str(skill_md),
                            "error": f"another skill is already called {skill.name!r}",
                        }
                    )
                    continue
                _LOGGER.info("skills: %s overrides the bundled skill", skill_md)
            self.skills[skill.name] = skill
        if not bundled:
            _LOGGER.info(
                "skills: %d loaded from %s%s%s",
                len(self.skills),
                self.root,
                f" (+ bundled from {self.bundled_root})" if self.bundled_root else "",
                f" ({len(self.errors)} could not be read)" if self.errors else "",
            )
        return len(self.skills)

    # --- reading ----------------------------------------------------------
    def get(self, name: str) -> Skill | None:
        return self.skills.get(one_line(name, MAX_NAME))

    def listing(self) -> list[dict[str, Any]]:
        return [skill.as_dict() for skill in sorted(self.skills.values(), key=lambda s: s.name)]

    def index_block(self) -> str:
        """The whole contribution to the system prompt: one line per skill.

        Returns "" when there are none, so the caller appends unconditionally.

        The last sentence of the header is there because of a measurement. The
        intelligence eval (M26) caught the model reading `house-style` — a
        skill whose description is "how Jarvis should answer in this house" —
        before answering "which room is the coffee machine in?", which is a
        model round trip added to every single turn. It was obeying the header
        it had: a style guide "covers" every answer. Saying that reading costs
        something is what separates "this is relevant" from "this is worth a
        turn".
        """
        if not self.skills:
            return ""
        lines = [
            "Skills available (instructions this house has written down). These are "
            "names and summaries only — call use_skill with the name to read one "
            "before doing anything it covers. Reading one costs a round trip, so "
            "read a skill when the request is ABOUT what it covers, and answer "
            "directly when it is not:",
        ]
        lines += [skill.index_line for skill in sorted(self.skills.values(), key=lambda s: s.name)]
        return "\n".join(lines)

    def body_for(self, name: str) -> dict[str, Any]:
        """What `use_skill` returns."""
        skill = self.get(name)
        if skill is None:
            return {
                "status": "error",
                "error": f"there is no skill called {one_line(name, MAX_NAME)!r}",
                "available": sorted(self.skills),
            }
        body = skill.body[: self.max_body_chars]
        return {
            "status": "ok",
            "name": skill.name,
            "description": skill.description,
            "allowed_tools": list(skill.allowed_tools),
            "truncated": len(skill.body) > self.max_body_chars,
            # Fenced, and labelled as instructions from the operator rather
            # than from whoever is talking: a skill is trusted, but the model
            # should still be able to tell the two apart.
            "instructions": body,
            "resources": list(skill.resources),
        }


def _register_services(jarvis: "Jarvis", store: SkillStore) -> None:
    async def service_list(call: Any) -> Any:
        return {"skills": store.listing(), "errors": list(store.errors)}

    async def service_reload(call: Any) -> Any:
        loaded = store.load()
        return {"loaded": loaded, "errors": list(store.errors)}

    async def service_get(call: Any) -> Any:
        name = str((call.data or {}).get("name") or "")
        skill = store.get(name)
        if skill is None:
            return {"error": f"no skill called {name!r}"}
        return {"skill": skill.as_dict(body=True)}

    jarvis.services.register(DOMAIN, "list", service_list, supports_response=True)
    jarvis.services.register(DOMAIN, "reload", service_reload, supports_response=True)
    jarvis.services.register(DOMAIN, "get", service_get, supports_response=True)


def _register_tools(jarvis: "Jarvis", store: SkillStore) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("skills: no LLM tool registry; services registered without a tool")
        return
    from ...llm.tools import schema_object

    async def tool_use_skill(args: dict[str, Any], context: Any = None) -> Any:
        return store.body_for(str(args.get("name") or args.get("skill") or ""))

    registry.register(
        name="use_skill",
        description=(
            "Read the full instructions for one of the skills listed in your "
            "system prompt. Call this BEFORE doing anything a skill covers — "
            "the summary in the list is not the instructions."
        ),
        parameters=schema_object(
            {"name": {"type": "string", "description": "The skill's name."}},
            required=["name"],
        ),
        handler=tool_use_skill,
        # Tier 1: reading a file the operator wrote changes nothing. Anything a
        # skill tells the model to DO still goes through that action's own
        # tier — a skill cannot lower one, and `scripts/` beside a SKILL.md is
        # never run from here at all.
        domain=DOMAIN,
    )


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    root = Path(str(cfg.get("path") or DEFAULT_PATH))
    if not root.is_absolute():
        root = Path(jarvis.config_dir) / root
    # Shipped skills live in the package, so they are present on a fresh
    # install with an empty `config/skills/` — the alternative was a first run
    # where the feature exists and has nothing in it.
    bundled: Path | None = BUNDLED_ROOT
    if cfg.get("bundled") is False:
        bundled = None
    store = SkillStore(
        root,
        max_body_chars=int(cfg.get("max_body_chars") or DEFAULT_MAX_BODY),
        enabled=list(cfg.get("enabled") or []),
        bundled_root=bundled,
    )
    store.load()
    jarvis.data[DATA_STORE] = store
    _register_services(jarvis, store)
    _register_tools(jarvis, store)
    return True
