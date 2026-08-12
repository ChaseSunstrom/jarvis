"""What an automation would touch if you ran it.

Running an automation is a way to reach every service in its action list, so
"trigger the bedtime routine" is only as safe as the routine. If the tier were
decided by the *tool* being called, `automation.trigger` would look like a
tier-1 action while quietly locking or unlocking a door — the exact escalation
the tier system exists to stop.

So the tier is decided by what the actions reach, and this is the part that
works that out. Deliberately shallow and deliberately pessimistic:

  * A direct `service: lock.unlock` is seen.
  * A nested `choose` / `if` / `repeat` / `parallel` block is walked, because a
    gated call is no less gated for being two levels down.
  * A `script.x` or `scene.x` call is treated as **unknown**, not as safe. This
    module does not follow into another entity's definition — that is a
    different object with its own lifetime, and something that resolves today
    may not tomorrow. Unknown escalates.
  * A templated service name (`service: "{{ whatever }}"`) is unknown for the
    same reason: it is decided at run time, after the gate.

Being wrong in the safe direction costs a confirmation prompt. Being wrong in
the other direction costs an unlocked door.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..const import GATED_DOMAINS

#: Keys whose values hold more action steps.
_NESTED_KEYS = ("sequence", "then", "else", "default", "actions", "parallel")

#: Domains whose calls run somebody else's action list, which this module does
#: not follow into. See the module docstring.
INDIRECT_DOMAINS = frozenset({"script", "scene", "automation"})

#: The parts of an automation, and the plural spelling the engine also accepts.
#:
#: `Automation.__init__` reads `action` and falls back to `actions`, and the
#: same for triggers and conditions. Every reader outside the engine used to
#: take the singular key alone.
_PLURALS = {"action": "actions", "trigger": "triggers", "condition": "conditions"}


def part_of(config: Any, part: str) -> Any:
    """One part of an automation config, read the way the ENGINE reads it.

    ## Why this exists

    An approval gate that cannot see an automation's actions does not fail
    closed — it decides there is nothing to approve. `needs_approval(None)` is
    `False`, so an automation the gate reads as empty runs at tier 1 whatever it
    touches.

    And the gate read the wrong key. `Automation.__init__` accepts either
    spelling::

        self.actions = as_list(
            self.config.get("action")
            if self.config.get("action") is not None
            else self.config.get("actions")
        )

    while `llm/tools.py` asked for `config["action"]` and nothing else. So an
    automation written with the plural — the newer spelling, which the engine
    deliberately supports and which the docs use — was analysed as having no
    actions at all::

        automations:
          - alias: Front door
            triggers: [{platform: time, at: "21:00:00"}]
            actions: [{service: lock.unlock, target: {entity_id: lock.front}}]

    The engine parses that, `async_trigger` really calls `lock.unlock`, and the
    gate said "touches nothing that needs approval". A model turn calling
    `automation_control {action: "run", name: "Front door"}` unlocked the front
    door with no human in the loop. The console's automation list showed
    `action: []` beside it, for the same reason.

    This is the caller-side twin of the walker bug fixed alongside it: no step
    SHAPE escapes `_walk` any more, and the whole LIST could still arrive as
    `None`. Both are the same lesson — the analysis is only as good as what it
    is handed — which is why the precedence now lives in one function that the
    engine uses too.
    """
    if not isinstance(config, dict):
        return None
    singular = config.get(part)
    if singular is not None:
        return singular
    plural = _PLURALS.get(part)
    return config.get(plural) if plural else None


def actions_of(config: Any) -> Any:
    """The action list of an automation config. See [part_of]."""
    return part_of(config, "action")


def service_calls(actions: Any) -> list[str]:
    """Every `domain.service` an action list would call, as flat strings.

    A call this cannot read statically appears as `"?"`, so callers can tell
    "reaches nothing dangerous" apart from "cannot tell".
    """
    found: list[str] = []
    _walk(actions, found)
    return found


def _walk(node: Any, found: list[str]) -> None:
    if isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, found)
        return

    # A BARE STRING IS A CALL. `ScriptRunner._async_run_step` rewrites
    # `- light.turn_on` as `{"service": "light.turn_on"}` before dispatching
    # it, so a step written that way runs exactly like the dict form.
    #
    # This walker used to fall straight through it, and the consequence was
    # not cosmetic: `needs_approval` is the only gate on `automation_control`,
    # on `create_automation`, and on the console's "touches nothing that needs
    # approval" label. An automation whose action list was
    # `["script.open_up"]` was labelled safe, ran at tier 1 from a model turn,
    # and unlocked a door — while the identical automation written
    # `[{"service": "script.open_up"}]` was correctly held for a human. Four
    # characters of YAML inverted the repo's own tested contract.
    if isinstance(node, str):
        found.append(_name_of(node))
        return

    if not isinstance(node, dict):
        return

    raw = node.get("service") or node.get("action")
    if raw is not None:
        found.append(_name_of(raw))

    # `- scene: scene.evening` dispatches scene.turn_on, and `- event: x` can
    # fire an event that triggers another automation. Both are the "different
    # object with its own lifetime" case this module exists to escalate, and
    # `scene` is already in INDIRECT_DOMAINS so naming it yields "?" rather
    # than a false sense of having read it.
    for shorthand in ("scene", "event"):
        if shorthand in node:
            found.append("?")

    # `choose` is a list of {conditions, sequence}; the others hold steps
    # directly. Both shapes are just more nodes to walk.
    for key in ("choose", *_NESTED_KEYS):
        if key in node:
            _walk(node[key], found)

    # A service call can also name its target through `target`/`entity_id`
    # without a service, which is not a call and needs no handling — but an
    # `if` block's branches do.
    for key in ("if", "repeat"):
        if key in node:
            _walk(node[key], found)


def _name_of(raw: Any) -> str:
    text = str(raw).strip()
    if not text or "{" in text or "." not in text:
        # Templated, or not a `domain.service` at all: decided at run time.
        return "?"
    return text.lower()


def gated_reach(actions: Any) -> set[str]:
    """The gated domains an action list can reach, or `{"?"}` if unknowable.

    An empty set means "statically proven to touch nothing gated".
    """
    reach: set[str] = set()
    for call in service_calls(actions):
        if call == "?":
            reach.add("?")
            continue
        domain = call.split(".", 1)[0]
        if domain in INDIRECT_DOMAINS:
            reach.add("?")
        elif domain in GATED_DOMAINS:
            reach.add(domain)
    return reach


def needs_approval(actions: Any) -> bool:
    """True when running this action list must be held for a human."""
    return bool(gated_reach(actions))


def describe_reach(actions: Any) -> str:
    """One sentence for the approval card, so the human knows why they were asked."""
    reach = gated_reach(actions)
    if not reach:
        return "touches nothing that needs approval"
    known = sorted(d for d in reach if d != "?")
    parts: list[str] = []
    if known:
        parts.append("can " + " and ".join(known))
    if "?" in reach:
        parts.append("calls something this cannot read ahead of time")
    return "; ".join(parts)


def configs_by_entity(jarvis: Any) -> dict[str, dict[str, Any]]:
    """`automation.<id>` -> its config, for every automation the engine holds."""
    manager = jarvis.data.get("automation")
    if manager is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for automation in _all(manager):
        entity_id = getattr(automation, "entity_id", "")
        if entity_id:
            out[entity_id] = getattr(automation, "config", {}) or {}
    return out


def _all(manager: Any) -> Iterable[Any]:
    try:
        return manager.all()
    except Exception:  # pragma: no cover - defensive
        return []


__all__ = [
    "INDIRECT_DOMAINS",
    "configs_by_entity",
    "describe_reach",
    "gated_reach",
    "needs_approval",
    "service_calls",
]
