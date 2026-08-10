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


def service_calls(actions: Any) -> list[str]:
    """Every `domain.service` an action list would call, as flat strings.

    A call this cannot read statically appears as `"?"`, so callers can tell
    "reaches nothing dangerous" apart from "cannot tell".
    """
    found: list[str] = []
    _walk(actions, found)
    return found


def _walk(node: Any, found: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, found)
        return
    if not isinstance(node, dict):
        return

    raw = node.get("service") or node.get("action")
    if raw is not None:
        found.append(_name_of(raw))

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
