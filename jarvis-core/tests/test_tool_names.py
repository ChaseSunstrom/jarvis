"""One tool name, one meaning.

## What this is for

`ToolRegistry.register` refuses a re-registration that WEAKENS a tool — that
guard exists because `device_control` shipped its own Tier-1 `ask_user` and
silently replaced the Tier-3 one for the life of the product. It closes the
dangerous direction, and only that direction.

The direction it cannot close is two integrations meaning different things by
one name at the SAME strength, or at increasing strength. Both are ordering
accidents:

  * the STRONGER one loading second replaces the other silently, so a verb
    somebody configured stops existing and nothing says so;
  * the WEAKER one loading second raises, `async_setup_integrations` logs and
    skips that integration, and a feature disappears because of a name.

This nearly happened the day Jarvis Code was written: `orchestrator` registers
`code_task` for the remote coding service, and the new local one was about to
register `code_task` too. Whichever loaded second decided which coding agent
existed. It is now `start_coding_job`, and this file is what would have said so
before the fact rather than after.

## How it reads the tools

Statically, and it says so. It looks for `registry.register(name="x")` in every
integration and pairs each name with the package that claims it. A tool whose
name is computed would escape it — MCP's are, deliberately, and they are
namespaced `mcp_<server>_<tool>` for exactly this reason, which is the same
answer arrived at from the other end.
"""

from __future__ import annotations

import re
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[1] / "jarvis" / "integrations"

#: Names an integration may register even though something else already has.
#:
#: Empty, and that is the point: every entry here is a place where two things
#: share a name and somebody has decided which wins. If you add one, say why
#: and pass `replaces=` at the registration so the intent is in the diff too.
DELIBERATE_SHARING: dict[str, str] = {
    # `agents` runs specialists in this process — definitions in a folder,
    # child tasks, one pool in front of the model (M20). `orchestrator` has a
    # tool of the same name that forwards the work to a separate service, and
    # it now registers only when no `agents:` block is configured, so the two
    # can never both exist. The core one passes `replaces=` as well, which is
    # the same decision written at the registration.
    "delegate_to_agents": "core runs the specialists; the orchestrator forwards",
}


def _claims() -> dict[str, set[str]]:
    """name -> the integration packages that register it."""
    # The closing paren may be indented: a registration inside an `if` is
    # still a registration, and matching only column 4 quietly stopped seeing
    # the orchestrator's tools the day one of them grew a condition.
    call = re.compile(r"registry\.register\((?P<body>.*?)\n\s*\)", re.S)
    named = re.compile(r'name="(?P<name>[a-z0-9_]+)"')
    out: dict[str, set[str]] = {}
    for path in sorted(INTEGRATIONS.rglob("*.py")):
        package = path.relative_to(INTEGRATIONS).parts[0]
        source = re.sub(r"#[^\n]*", "", path.read_text(encoding="utf-8"))
        for match in call.finditer(source):
            name = named.search(match.group("body"))
            if name:
                out.setdefault(name.group("name"), set()).add(package)
    return out


def test_the_reader_finds_the_tools_it_is_meant_to_check():
    """A static reader that matches nothing passes every assertion below."""
    claims = _claims()
    assert len(claims) > 10, sorted(claims)
    # Two known ones from opposite ends of the tree, so a change to either the
    # pattern or the layout is caught here rather than by silence.
    assert "execute_command" in claims
    assert "start_coding_job" in claims


def test_no_two_integrations_claim_one_tool_name():
    shared = {
        name: sorted(packages)
        for name, packages in _claims().items()
        if len(packages) > 1 and name not in DELIBERATE_SHARING
    }
    assert not shared, (
        f"two integrations register the same tool name: {shared}. Whichever "
        "loads second decides what that name means — or the registry refuses "
        "it and the integration is skipped with a log line nobody reads. "
        "Rename one, or add it to DELIBERATE_SHARING with `replaces=` at the "
        "registration."
    )


def test_the_two_coding_agents_have_different_names():
    """The specific clash this file was written for.

    `orchestrator.code_task` is the remote coding service; `start_coding_job`
    is the local one. They take different arguments, run in different
    processes, and are at different tiers — so sharing a name would not be a
    near-miss, it would be one of them silently not existing.
    """
    claims = _claims()
    assert claims.get("code_task") == {"orchestrator"}
    assert claims.get("start_coding_job") == {"code"}


def test_an_integration_does_not_shadow_a_built_in():
    """A built-in tool replaced by an integration's is the `ask_user` bug.

    `register` catches the weakening case. This catches the rest of it: an
    integration silently taking over a built-in name at equal or greater
    strength still means the built-in's behaviour is gone, and the test that
    covers the built-in still passes because it builds a registry without any
    integrations in it.
    """
    from jarvis.llm.tools import ToolRegistry, register_builtin_tools

    registry = ToolRegistry(jarvis=None)
    register_builtin_tools(registry)
    builtins = set(registry.tools)
    assert len(builtins) > 15, "registry looks empty; this check would be vacuous"

    clashes = {
        name: sorted(packages)
        for name, packages in _claims().items()
        if name in builtins and name not in DELIBERATE_SHARING
    }
    assert not clashes, (
        f"integration tool(s) shadow a built-in: {clashes}. The built-in's "
        "tier, gate and `answerable` go with it, and the built-in's own tests "
        "keep passing because they never compose the integration."
    )
