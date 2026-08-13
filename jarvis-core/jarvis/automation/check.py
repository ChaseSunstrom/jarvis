"""Tell someone an automation is broken before three in the morning does.

## Why this exists

`authored.validate` checks shape and the trigger platform, and stops there —
deliberately, and its docstring says so: service names, entity ids and template
syntax are "decided at run time". That is true, and the consequence is that

    {"service": "lite.turn_on", "target": {"entity_id": "light.kitchn"}}

saves without complaint, lists in the console looking correct, and fails
silently forever. Two typos, no feedback, and the first sign of trouble is a
light that stopped coming on.

The engine cannot refuse it — a service registered by an integration that loads
later is legitimate, and an entity can appear at any time — so this is a
**report, not a gate**. It says what looks wrong, ranked by how sure it is, and
lets a human decide. Nothing here blocks a save and nothing here runs a step.

## What it will not do

It does not execute anything, so it cannot tell you an automation *works*. A
condition that is always false, a template that renders to the wrong entity, a
service that rejects its data — none of that is visible without running it, and
running it means actuating the house, which is exactly what the person asking
"is this right?" is trying to avoid.

So the promise is narrow and worth stating: **everything it reports is worth
looking at, and it does not claim to have found everything.**
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..helpers.template import is_template
from .reach import actions_of, describe_reach, part_of, service_calls

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: How much a finding is worth waking somebody for.
#:
#: `error` is "this cannot work as written" — a service that does not exist
#: cannot start existing by being called. `warning` is "this looks wrong and
#: might not be": an entity id that is not in the registry *yet* is the normal
#: state of a bulb that has not been plugged in.
ERROR = "error"
WARNING = "warning"
INFO = "info"


def check(jarvis: "Jarvis", config: Any) -> dict[str, Any]:
    """Static review of one automation config. Never runs a step.

    Returns `{"ok": bool, "findings": [...], "reach": str}` where `ok` is False
    only when something is an `error` — a warning is information, not a veto.
    """
    findings: list[dict[str, Any]] = []
    if not isinstance(config, dict):
        return {
            "ok": False,
            "findings": [_finding(ERROR, "config", "An automation must be an object.")],
            "reach": "",
        }

    actions = actions_of(config)
    findings.extend(_check_services(jarvis, actions))
    findings.extend(_check_entities(jarvis, config))
    findings.extend(_check_templates(config))

    if not part_of(config, "trigger"):
        findings.append(
            _finding(
                WARNING,
                "trigger",
                "No trigger, so nothing will ever start this on its own. That "
                "is fine for something you only run by hand or from a script.",
            )
        )

    return {
        "ok": not any(f["level"] == ERROR for f in findings),
        "findings": findings,
        # The same sentence the approval card uses, so "what does this touch"
        # has one answer everywhere it is asked.
        "reach": describe_reach(actions),
    }


def _finding(level: str, where: str, message: str) -> dict[str, str]:
    return {"level": level, "where": where, "message": message}


def _check_services(jarvis: "Jarvis", actions: Any) -> list[dict[str, Any]]:
    """Every `domain.service` the action list names, against what is registered.

    A misspelled service is the highest-value catch here: it is unambiguous,
    it is silent at run time, and `lite.turn_on` for `light.turn_on` is a typo
    nothing else in the system will ever mention.
    """
    out: list[dict[str, Any]] = []
    for call in service_calls(actions):
        if call == "?":
            continue  # templated or otherwise undecidable; not this module's business
        domain, _, service = call.partition(".")
        if not domain or not service:
            continue
        if jarvis.services.has_service(domain, service):
            continue
        known = sorted(jarvis.services.services.get(domain, {}))
        if known:
            hint = f"{domain} has: {', '.join(known[:8])}"
        else:
            hint = f"nothing is registered under {domain!r}"
        out.append(
            _finding(
                ERROR,
                "action",
                f"There is no service {call!r} — {hint}. If the integration "
                "that provides it loads later, this is a false alarm.",
            )
        )
    return out


def _check_entities(jarvis: "Jarvis", config: Any) -> list[dict[str, Any]]:
    """Entity ids named anywhere in the config, against the state machine.

    A warning rather than an error, and the distinction is the whole reason
    this module has levels: a bulb that has not been plugged in yet is a real
    and ordinary thing to write a rule for, and refusing it would make the
    report useless on a house that is still being set up.
    """
    out: list[dict[str, Any]] = []
    for entity_id in sorted(_entity_ids(config)):
        if is_template(entity_id) or "." not in entity_id:
            continue
        if jarvis.states.get(entity_id) is not None:
            continue
        out.append(
            _finding(
                WARNING,
                "entity_id",
                f"Nothing called {entity_id!r} exists right now. Check the "
                "spelling — or ignore this if the device is not connected yet.",
            )
        )
    return out


def _entity_ids(node: Any, found: set[str] | None = None) -> set[str]:
    """Every value that looks like an entity id, anywhere in the config."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("entity_id", "entity"):
                for item in value if isinstance(value, (list, tuple)) else [value]:
                    if isinstance(item, str) and item.strip():
                        found.add(item.strip())
            else:
                _entity_ids(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _entity_ids(item, found)
    return found


def _check_templates(config: Any) -> list[dict[str, Any]]:
    """Templates that will not compile.

    Only a syntax check. A template that parses and renders to the wrong thing
    is not findable without running it, and running it is what this exists to
    avoid.
    """
    from ..helpers.template import environment

    env = environment()
    out: list[dict[str, Any]] = []
    for text in sorted(_templates(config)):
        try:
            env.from_string(text)
        except Exception as exc:
            out.append(
                _finding(
                    ERROR,
                    "template",
                    f"This template will not compile: {text[:80]!r} — {exc}",
                )
            )
    return out


def _templates(node: Any, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(node, str):
        if is_template(node):
            found.add(node)
    elif isinstance(node, dict):
        for value in node.values():
            _templates(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _templates(item, found)
    return found


__all__ = ["ERROR", "INFO", "WARNING", "check"]
