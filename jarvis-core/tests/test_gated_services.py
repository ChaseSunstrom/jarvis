"""A dangerous verb is dangerous whichever door it is reached through.

## Why this exists

The tier system decides a *tool's* tier in code. `execute_command`,
`apply_code_task` and a writing `web_browse` batch are Tier 3, so calling one
from a model turn is held for a human.

But each of those verbs is *also* a registered service, and an automation's
action list can name a service directly. `reach.gated_reach` — the only thing
standing between `automation_control` / `create_automation` and an action list
— compared a call's **domain** against `GATED_DOMAINS`, which holds `lock` and
`notify`. `orchestrator` is not an entity domain, so it matched nothing:

    automations:
      - alias: Tidy up
        triggers: [{platform: time, at: "03:00:00"}]
        actions: [{service: orchestrator.execute, data: {command: "..."}}]

`create_automation` wrote that at Tier 1. `automation_control` ran it at Tier 1.
The model could author a shell command and then run it with no human in the
loop, while the identical command sent to `execute_command` was correctly held.
Worse, `async_execute` forwards the approval secret to the orchestrator
*because* "the human has already said yes" — a guarantee that only ever held
for the tool path.

This is the third time this shape has been found in this module (see the
plural-key note in `reach.part_of` and the bare-string note in `reach._walk`),
and all three are one lesson: **two ways to say one thing means one of them
gets forgotten.**

## What is pinned here

1. Every gated service escalates through every route into an action list —
   direct, nested, bare-string, and the shorthands.
2. Every Tier-3 tool has a recorded decision about whether it has a service
   twin. A new Tier-3 tool cannot be added without confronting this file, which
   is the part that stops a fourth occurrence.
"""

from __future__ import annotations

import pytest

from jarvis.automation.reach import describe_reach, gated_reach, needs_approval
from jarvis.const import GATED_DOMAINS, GATED_SERVICES


# ---------------------------------------------------------------------------
# 1. the gate itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("service", sorted(GATED_SERVICES))
def test_a_gated_service_is_held_however_it_is_written(service: str) -> None:
    """Every shape `ScriptRunner` accepts for a step must reach the gate.

    The bare string form matters most: `ScriptRunner._async_run_step` rewrites
    `- orchestrator.execute` into `{"service": ...}` before dispatch, so the
    two forms run identically and must gate identically. A previous bug in this
    module let four characters of YAML invert the contract.
    """
    shapes: list[object] = [
        [{"service": service}],
        [{"action": service}],
        [service],
        [{"sequence": [{"service": service}]}],
        [{"choose": [{"conditions": [], "sequence": [{"service": service}]}]}],
        [{"if": "{{ true }}", "then": [{"service": service}]}],
        [{"repeat": {"count": 2, "sequence": [{"service": service}]}}],
        [{"parallel": [{"service": service}]}],
    ]
    for actions in shapes:
        assert needs_approval(actions), f"{service} escaped the gate as {actions!r}"
        assert service in gated_reach(actions)


def test_the_gated_service_is_named_to_the_human() -> None:
    """The approval card says which verb held it, not just that something did."""
    sentence = describe_reach([{"service": "orchestrator.execute"}])
    assert "orchestrator.execute" in sentence

    # A domain and a service read differently and must not be run together into
    # one list: "can lock and orchestrator.execute" is neither sentence.
    both = describe_reach(
        [{"service": "lock.unlock"}, {"service": "orchestrator.execute"}]
    )
    assert "can lock" in both
    assert "calls orchestrator.execute" in both


def test_a_neighbouring_service_in_a_gated_domain_is_not_held() -> None:
    """Gating the whole `orchestrator` domain would hold a status poll.

    This is why `GATED_SERVICES` names calls and `GATED_DOMAINS` names domains:
    an entity domain is uniformly dangerous, an integration domain is not.
    """
    assert not needs_approval([{"service": "orchestrator.code_status"}])
    assert not needs_approval([{"service": "orchestrator.delegate"}])
    assert gated_reach([{"service": "orchestrator.code_status"}]) == set()


def test_gated_domains_still_gate() -> None:
    """The new check must not have displaced the old one."""
    assert needs_approval([{"service": "lock.unlock"}])
    assert needs_approval([{"service": "notify.send"}])
    assert gated_reach([{"service": "lock.unlock"}]) == {"lock"}


def test_nothing_gated_is_still_nothing() -> None:
    assert not needs_approval([{"service": "light.turn_on"}])
    assert gated_reach([{"service": "light.turn_on"}]) == set()
    assert describe_reach([{"service": "light.turn_on"}]) == (
        "touches nothing that needs approval"
    )


def test_a_gated_service_name_is_a_full_call_not_a_domain() -> None:
    """A bare domain in `GATED_SERVICES` would silently gate the whole domain.

    `gated_reach` checks the full call name against this set before it splits
    the domain off, so an entry without a dot would never match a call and
    would look like it was working while gating nothing.
    """
    for name in GATED_SERVICES:
        assert "." in name, f"{name!r} is not a domain.service"
        assert name == name.lower(), f"{name!r} must be lowercase to match"
        assert name.split(".", 1)[0] not in GATED_DOMAINS, (
            f"{name!r} is inside an already-gated domain, so the entry is dead "
            "weight that suggests the domain is not covered"
        )


# ---------------------------------------------------------------------------
# 2. the guard that stops a fourth occurrence
# ---------------------------------------------------------------------------
#: Every Tier-3 (or gate-carrying) tool, and the service an action list could
#: use to reach the same verb without going through the tool.
#:
#: `None` means "this verb has no service form, so an automation cannot reach
#: it". Adding a Tier-3 tool means adding a row here, and the test below fails
#: until you do — which is the whole point. Do not add a row without checking:
#: `grep -rn 'services.register' jarvis/integrations/<the integration>`.
TIER_THREE_TOOLS_AND_THEIR_SERVICE_TWINS: dict[str, str | None] = {
    # --- reachable as a service, and therefore in GATED_SERVICES ---------
    "execute_command": "orchestrator.execute",
    "apply_code_task": "orchestrator.code_apply",
    "web_browse": "web.browse",
    "write_file": "files.write",
    # Jarvis Code. Edits a real repository — on its own branch, but on the
    # operator's disk — and runs that repository's check commands.
    "start_coding_job": "code.run",
    # --- no service form: the tool is the only door ----------------------
    # Registry-level tools with no `services.register` counterpart. An
    # automation cannot call these at all.
    "create_tool": None,
    "ask_user": None,
    # `lock_control` is Tier 3 *and* `domain="lock"`, so the service form
    # (`lock.lock` / `lock.unlock`) is already covered by GATED_DOMAINS.
    "lock_control": None,
}


def test_every_gated_service_belongs_to_a_tier_three_tool() -> None:
    """`GATED_SERVICES` may not grow a member nobody can explain.

    An entry here holds a real automation for a real human every time it fires.
    If it does not correspond to a verb the tool layer also holds, either the
    tool is under-tiered or this entry is wrong — and both are worth finding.
    """
    claimed = {
        service
        for service in TIER_THREE_TOOLS_AND_THEIR_SERVICE_TWINS.values()
        if service is not None
    }
    assert set(GATED_SERVICES) == claimed, (
        "GATED_SERVICES and the tool table disagree. Every gated service must "
        "be the service form of a Tier-3 tool, and every Tier-3 tool with a "
        "service form must be gated."
    )


def test_the_tool_table_covers_every_built_in_tier_three_tool() -> None:
    """A new Tier-3 built-in must decide whether it has a service twin.

    Builds the real registry the way the app does, so "is this reachable from
    an automation?" is answered when the tool is written rather than after
    something has run unapproved.
    """
    from jarvis.llm.tools import TIER_APPROVAL, ToolRegistry, register_builtin_tools

    registry = ToolRegistry(jarvis=None)
    register_builtin_tools(registry)
    assert len(registry.tools) > 15, "registry looks empty; this check would be vacuous"

    gated_tools = {
        name
        for name, tool in registry.tools.items()
        if tool.tier >= TIER_APPROVAL or tool.domain in GATED_DOMAINS
    }
    _assert_declared(gated_tools)


def test_the_tool_table_covers_every_tier_three_tool_in_an_integration() -> None:
    """The same check for tools an INTEGRATION registers — the ones that bit.

    All three verbs behind the escalation this file exists for — `execute_command`,
    `apply_code_task`, `web_browse` — are registered by integrations, inside an
    `async_setup` that needs a live `Jarvis`. Building that here would make this
    check depend on the orchestrator being configured, which is exactly the
    state in which the bug was invisible.

    So this reads the source instead. It is a static reader and says so: it
    finds `registry.register(name=..., ..., tier=TIER_APPROVAL)` and nothing
    cleverer. A tool whose tier is computed at runtime would escape it — no
    such tool exists today, and `test_a_gated_service_is_held_however_it_is_written`
    is what holds the line if one ever does.
    """
    import re
    from pathlib import Path

    integrations = Path(__file__).resolve().parents[1] / "jarvis" / "integrations"
    assert integrations.is_dir(), integrations

    # `registry.register(` ... `name="x"` ... `tier=TIER_APPROVAL` before the
    # call closes at the matching dedent. Non-greedy, bounded, comments stripped.
    call = re.compile(
        r"registry\.register\((?P<body>.*?)\n    \)", re.S
    )
    named = re.compile(r'name="(?P<name>[a-z0-9_]+)"')

    found: set[str] = set()
    for path in sorted(integrations.rglob("*.py")):
        source = re.sub(r"#[^\n]*", "", path.read_text(encoding="utf-8"))
        for match in call.finditer(source):
            body = match.group("body")
            name = named.search(body)
            if not name:
                continue
            if "TIER_APPROVAL" in body or re.search(r"tier\s*=\s*3\b", body):
                found.add(name.group("name"))

    assert found, (
        "found no Tier-3 tools in any integration — the pattern this test "
        "matches has changed, and it is now checking nothing"
    )
    # The verbs behind this whole file must be among them, or the reader broke.
    assert {"execute_command", "apply_code_task"} <= found, sorted(found)
    _assert_declared(found)


def _assert_declared(tools: set[str]) -> None:
    undeclared = tools - set(TIER_THREE_TOOLS_AND_THEIR_SERVICE_TWINS)
    assert not undeclared, (
        f"Tier-3 tool(s) {sorted(undeclared)} have no row in "
        "TIER_THREE_TOOLS_AND_THEIR_SERVICE_TWINS. Add one saying which "
        "service an automation could use to reach the same verb, or `None` if "
        "there is no service form. If there is one, it belongs in "
        "const.GATED_SERVICES too."
    )
