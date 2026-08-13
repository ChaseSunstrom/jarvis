"""The toolbox as it exists after everything has loaded — not one piece of it.

## Why this exists

Every other tool suite builds the registry it needs and tests that. That is the
right shape for a unit test and it is blind to exactly one thing: what happens
when two integrations want the same name.

`ToolRegistry.register` was `self._tools[tool.name] = tool`. Integrations load
in dependency order, so the last registration of a name won, silently.
`device_control` — a CORE integration, so every install — registered an
`ask_user` at Tier 1, and it loaded after the built-in Tier-3 one. From then on:

  * the gate was gone, so a question ran without a human;
  * `answerable` was gone with it, and `_bridge_questions_to_the_phone` keys on
    `answerable`, so the whole provenance path went too. A turn that had just
    read an attacker's web page could compose a sentence and have it rendered
    verbatim on a lock screen with no `UNTRUSTED_PREFIX` in front of it.

And `test_ask_user_is_tier_three_and_stays_there` — a test written specifically
to pin that tier, whose docstring is "a question that could run without a human
is not a question" — passed the whole time, because it builds a registry from
the built-ins and never loads the integration that overwrote it.

A suite that cannot see a composition cannot see a composition bug. This file
is the composition.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import CORE_INTEGRATIONS  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    TIER_APPROVAL,
    TIER_DIRECT,
    ToolRegistry,
    register_builtin_tools,
)


@pytest.fixture
async def composed(tmp_path):
    """A Jarvis with every core integration up, which is what an install is."""
    instance = Jarvis(tmp_path)
    await instance.async_setup({name: {} for name in CORE_INTEGRATIONS})
    yield instance
    await instance.async_stop()


# ---------------------------------------------------------------------------
# the invariant
# ---------------------------------------------------------------------------
async def test_no_tool_loses_its_tier_to_a_later_registration(composed):
    """A tool's tier after composition is the tier it was written with.

    This is the general form of the `ask_user` bug: whatever a built-in
    declares, no integration loading later may quietly weaken it. A deliberate
    supersede is still possible — `register(replaces=...)` — and would fail
    here, which is the point: lowering a gate is a decision that should have to
    be argued for in a diff.
    """
    builtin = ToolRegistry(composed)
    register_builtin_tools(builtin)
    live = composed.data["llm_tools"]

    weakened = {
        name: (tool.tier, live.get(name).tier)
        for name, tool in builtin.tools.items()
        if live.get(name) is not None and live.get(name).tier < tool.tier
    }
    assert not weakened, (
        f"tool(s) lost tier during composition: {weakened}. A later "
        "registration has replaced a gated tool with a weaker one."
    )


async def test_registering_a_weaker_tool_over_a_gated_one_raises(composed):
    """The guard itself, on the live registry — exactly the `ask_user` shape.

    Before this, the second registration returned normally and the first tool
    ceased to exist. The failure mode was silence, which is why it lasted.
    """
    live = composed.data["llm_tools"]

    with pytest.raises(ValueError, match="weaken"):
        live.register(
            name="ask_user",
            description="a quieter way to ask",
            handler=lambda args, context: {"status": "ok"},
            tier=TIER_DIRECT,
        )

    assert live.get("ask_user").tier == TIER_APPROVAL, "the original survived"


async def test_losing_answerable_counts_as_weakening(composed):
    """Not only the tier. `answerable` is what the phone bridge keys on.

    A replacement that kept Tier 3 but dropped `answerable` would still stop
    held questions reaching the device the user is at — the same outcome by a
    quieter route.
    """
    live = composed.data["llm_tools"]

    with pytest.raises(ValueError, match="answerable"):
        live.register(
            name="ask_user",
            description="still gated, but no longer a question",
            handler=lambda args, context: {"status": "ok"},
            tier=TIER_APPROVAL,
        )


async def test_an_equal_or_stronger_registration_is_allowed(composed):
    """A reload re-registers its own tools, and that must keep working.

    The invariant is the direction, not the count. Refusing every second
    registration would have broken `async_setup` being called twice — which
    `test_setting_trace_up_again_replaces_the_old_recorder` relies on — to
    catch a bug that was never about repetition.
    """
    live = composed.data["llm_tools"]

    live.register(
        name="tell_user",
        description="re-registered by a reload",
        handler=lambda args, context: {"status": "ok"},
        tier=TIER_DIRECT,
    )
    assert live.get("tell_user").description == "re-registered by a reload"

    # And strengthening is always fine.
    live.register(
        name="tell_user",
        description="now gated",
        handler=lambda args, context: {"status": "ok"},
        tier=TIER_APPROVAL,
    )
    assert live.get("tell_user").tier == TIER_APPROVAL


async def test_a_deliberate_supersede_is_still_possible(composed):
    """`replaces=` is the escape hatch, and it has to name the tool it takes over.

    Without one, an integration that genuinely needed to weaken another's tool
    would have no route except deleting it first — and the guard would be a
    wall rather than a check.
    """
    live = composed.data["llm_tools"]

    live.register(
        name="ask_user",
        description="superseded on purpose",
        handler=lambda args, context: {"status": "ok"},
        tier=TIER_DIRECT,
        replaces="ask_user",
    )
    assert live.get("ask_user").description == "superseded on purpose"


# ---------------------------------------------------------------------------
# the specific thing that went wrong, stated as itself
# ---------------------------------------------------------------------------
async def test_ask_user_is_tier_three_after_everything_has_loaded(composed):
    """The named contract, asserted where it was actually broken.

    `test_ask_user.py` makes this claim about a registry built from the
    built-ins. This makes it about the registry the product runs.
    """
    tool = composed.data["llm_tools"].get("ask_user")

    assert tool is not None
    assert tool.tier == TIER_APPROVAL
    assert tool.answerable == "answer", (
        "the phone bridge delivers a held request only when it names an "
        "answerable argument; without it a question never leaves the console"
    )


async def test_every_gated_builtin_is_still_gated_after_composition(composed):
    """Not just `ask_user` — the whole gated set survives the load order."""
    builtin = ToolRegistry(composed)
    register_builtin_tools(builtin)
    live = composed.data["llm_tools"]

    for name, tool in builtin.tools.items():
        if tool.tier < TIER_APPROVAL and tool.gate is None and tool.domain is None:
            continue
        alive = live.get(name)
        if alive is None:
            continue
        assert alive.tier >= tool.tier, f"{name} was weakened to tier {alive.tier}"
        assert (alive.gate is not None) >= (tool.gate is not None), (
            f"{name} lost its gate"
        )
        assert alive.domain == tool.domain, (
            f"{name} lost its domain, and with it any GATED_DOMAINS escalation"
        )


async def test_the_composed_registry_is_not_empty(composed):
    """Guard against every check above passing because nothing loaded."""
    live = composed.data["llm_tools"]
    assert len(live.tools) > 15, sorted(live.tools)
    # The three that only exist once device_control and companion are up.
    for name in ("control_device", "list_my_devices", "tell_user"):
        assert name in live.tools, f"{name} missing; composition did not happen"
