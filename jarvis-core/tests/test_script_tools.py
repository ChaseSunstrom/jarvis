"""A script with a description is a tool the model can actually call.

## What was claimed, and what was true

Six places said it: `docs/configuration.md`, `config/scripts.yaml`,
`config/examples/house/scripts.yaml`, `config/examples/README.md`,
`docs/migrating-from-ha.md`, and the script integration's own module
docstring. All six said a script with a `description:` and `fields:` is
offered to the LLM as a tool automatically.

Nothing did it. `jarvis.data["scripts"]` was filled with `as_tool_dict()`
"for the tool/LLM layer to enumerate" and no code read it; `as_tool_dict` had
one definition, one call site and no consumer. The model got a single generic
`run_script`, which takes `{name, entity_id}`, passes no arguments, and — via
`script.turn_on` — discards whatever the script returned. So a script's
`fields:` were unreachable and `stop:` with a `response_variable:` returned
its data to nobody.

## Why this is worth more than a docstring fix

It is the cheap way to make a repeated job fast and consistent. Six service
calls the model reasons out afresh every time become one tool call, in an
order somebody tuned, with a name the household can say. That is the whole
argument, and it did not work.

## What is pinned here

That a described script is offered and an undescribed one is not; that its
`fields:` become a real schema with real types; that its response comes back;
and — the one that matters — that its **tier is its own reach**, so a
goodnight script that locks the front door is held for a human and one that
dims the lounge is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.script import TOOL_PREFIX  # noqa: E402
from jarvis.llm.tools import TIER_APPROVAL, TIER_DIRECT  # noqa: E402


async def boot(tmp_path: Path, scripts: dict) -> Jarvis:
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"script": scripts})
    return jarvis


def registry(jarvis: Jarvis):
    return jarvis.data["llm_tools"]


def offered(jarvis: Jarvis) -> set[str]:
    return {t["function"]["name"] for t in registry(jarvis).as_openai_schema()}


DIM = {
    "alias": "Dim",
    "description": "Dim the lounge for the evening.",
    "sequence": [{"service": "light.turn_on", "target": {"entity_id": "light.lounge"}}],
}
GOODNIGHT = {
    "alias": "Good night",
    "description": "Shut the house down for the night.",
    "sequence": [
        {"service": "light.turn_off", "target": {"entity_id": "light.hall"}},
        {"service": "lock.lock", "target": {"entity_id": "lock.front_door"}},
    ],
}


# ---------------------------------------------------------------------------
# offered, or deliberately not
# ---------------------------------------------------------------------------
async def test_a_described_script_is_offered_to_the_model(tmp_path: Path):
    jarvis = await boot(tmp_path, {"dim": DIM})
    try:
        assert f"{TOOL_PREFIX}dim" in offered(jarvis)
    finally:
        await jarvis.async_stop()


async def test_a_script_with_no_description_stays_private(tmp_path: Path):
    """`description:` is the opt-in. Promoting every script in the file would
    put the model's attention on internals nobody wrote for it — and they are
    all still reachable through `run_script`."""
    jarvis = await boot(
        tmp_path,
        {"internal": {"alias": "Internal", "sequence": [{"delay": 1}]}},
    )
    try:
        assert f"{TOOL_PREFIX}internal" not in offered(jarvis)
        assert jarvis.services.has_service("script", "internal")
        assert "run_script" in offered(jarvis)
    finally:
        await jarvis.async_stop()


async def test_the_tool_is_prefixed_so_it_cannot_shadow_a_builtin(tmp_path: Path):
    """A script called `search` or `remember` would otherwise take over a
    built-in tool — silently, with the operator's YAML winning, which is the
    worst way round for something nobody would think to check."""
    jarvis = await boot(
        tmp_path,
        {"remember": {"alias": "R", "description": "Not the memory tool.",
                      "sequence": [{"delay": 1}]}},
    )
    try:
        names = offered(jarvis)
        assert f"{TOOL_PREFIX}remember" in names
        # The real one is untouched.
        assert registry(jarvis).get("remember") is None or (
            "memory" in (registry(jarvis).get("remember").description or "").lower()
        )
    finally:
        await jarvis.async_stop()


# ---------------------------------------------------------------------------
# the tier is the script's own reach
# ---------------------------------------------------------------------------
async def test_a_script_that_locks_a_door_is_held_for_a_human(tmp_path: Path):
    """An operator's YAML is not a reason to skip a gate the same call would
    hit anywhere else. One fixed tier for every script would either hold the
    trivial ones or wave the locks through."""
    jarvis = await boot(tmp_path, {"goodnight": GOODNIGHT})
    try:
        tool = registry(jarvis).get(f"{TOOL_PREFIX}goodnight")
        assert tool.tier == TIER_APPROVAL
        # And it says WHY, so the approval card is not a mystery.
        assert "can lock" in tool.description
    finally:
        await jarvis.async_stop()


async def test_a_script_that_only_dims_a_light_is_not(tmp_path: Path):
    jarvis = await boot(tmp_path, {"dim": DIM})
    try:
        assert registry(jarvis).get(f"{TOOL_PREFIX}dim").tier == TIER_DIRECT
    finally:
        await jarvis.async_stop()


async def test_a_script_calling_a_gated_service_is_held_too(tmp_path: Path):
    """`GATED_SERVICES` is separate from `GATED_DOMAINS` and both have to
    count — a script wrapping `orchestrator.execute` is the obvious way round
    the gate if only domains were checked."""
    jarvis = await boot(
        tmp_path,
        {"sneaky": {"alias": "S", "description": "Run a thing.",
                    "sequence": [{"service": "orchestrator.execute",
                                  "data": {"command": "rm -rf /"}}]}},
    )
    try:
        assert registry(jarvis).get(f"{TOOL_PREFIX}sneaky").tier == TIER_APPROVAL
    finally:
        await jarvis.async_stop()


async def test_a_script_whose_reach_cannot_be_read_statically_is_held(tmp_path: Path):
    """A templated service name is unknowable before it runs. `collect_domains`
    answers "?" and `needs_approval` fails closed, which is the only safe
    direction."""
    jarvis = await boot(
        tmp_path,
        {"dynamic": {"alias": "D", "description": "Call whatever.",
                     "sequence": [{"service": "{{ chosen_service }}"}]}},
    )
    try:
        assert registry(jarvis).get(f"{TOOL_PREFIX}dynamic").tier == TIER_APPROVAL
    finally:
        await jarvis.async_stop()


# ---------------------------------------------------------------------------
# fields, and the response
# ---------------------------------------------------------------------------
async def test_fields_become_a_schema_with_real_types(tmp_path: Path):
    """A model given no type sends a string for a number every time, and
    `example:` is the only type information a script field carries."""
    jarvis = await boot(
        tmp_path,
        {"announce": {
            "alias": "Announce",
            "description": "Say something out loud.",
            "fields": {
                "message": {"description": "what to say", "required": True},
                "delay_minutes": {"description": "wait first", "example": 5},
                "volume": {"description": "how loud", "example": 0.4},
                "urgent": {"description": "interrupt", "example": True},
            },
            "sequence": [{"delay": 1}],
        }},
    )
    try:
        schema = next(
            t["function"]["parameters"]
            for t in registry(jarvis).as_openai_schema()
            if t["function"]["name"] == f"{TOOL_PREFIX}announce"
        )
        types = {k: v["type"] for k, v in schema["properties"].items()}
        assert types == {
            "message": "string",
            "delay_minutes": "integer",
            "volume": "number",
            "urgent": "boolean",
        }
        assert schema["required"] == ["message"]
        # The example is kept in the description, where a model will read it.
        assert "e.g. 5" in schema["properties"]["delay_minutes"]["description"]
    finally:
        await jarvis.async_stop()


async def test_a_script_with_no_fields_takes_no_arguments(tmp_path: Path):
    jarvis = await boot(tmp_path, {"dim": DIM})
    try:
        schema = next(
            t["function"]["parameters"]
            for t in registry(jarvis).as_openai_schema()
            if t["function"]["name"] == f"{TOOL_PREFIX}dim"
        )
        assert schema["properties"] == {}
    finally:
        await jarvis.async_stop()


async def test_the_response_variable_reaches_the_model(tmp_path: Path):
    """The other half of the broken claim. `run_script` goes through
    `script.turn_on`, which is fire-and-forget — so a script that ended in
    `stop:` with a `response_variable:` returned its data to nobody, and a
    script could act but never answer."""
    jarvis = await boot(
        tmp_path,
        {"house_status": {
            "alias": "House status",
            "description": "Report the state of the house.",
            "sequence": [
                {"variables": {"summary": {"locked": True, "lights_on": 2}}},
                {"stop": "done", "response_variable": "summary"},
            ],
        }},
    )
    try:
        got = await registry(jarvis).call(f"{TOOL_PREFIX}house_status", {}, None)
        assert got["status"] == "ok"
        assert got["result"] == {"locked": True, "lights_on": 2}
    finally:
        await jarvis.async_stop()


async def test_arguments_actually_reach_the_sequence(tmp_path: Path):
    """`run_script` passes `{}`. A script with fields it could never be given
    is a tool that looks configurable and is not."""
    jarvis = await boot(
        tmp_path,
        {"echo": {
            "alias": "Echo",
            "description": "Give back what it was told.",
            "fields": {"message": {"description": "anything", "required": True}},
            "sequence": [
                {"variables": {"out": {"said": "{{ message }}"}}},
                {"stop": "done", "response_variable": "out"},
            ],
        }},
    )
    try:
        got = await registry(jarvis).call(
            f"{TOOL_PREFIX}echo", {"message": "the kettle is on"}, None
        )
        assert got["result"]["said"] == "the kettle is on"
    finally:
        await jarvis.async_stop()


async def test_a_script_that_throws_is_a_tool_result_not_a_crash(tmp_path: Path):
    jarvis = await boot(
        tmp_path,
        {"broken": {
            "alias": "Broken",
            "description": "Call something that is not there.",
            "sequence": [{"service": "nosuch.service"}],
        }},
    )
    try:
        got = await registry(jarvis).call(f"{TOOL_PREFIX}broken", {}, None)
        assert got["status"] == "error"
        assert got["error"]
    finally:
        await jarvis.async_stop()


# ---------------------------------------------------------------------------
# it does not break what was there
# ---------------------------------------------------------------------------
async def test_run_script_still_works_for_everything(tmp_path: Path):
    """The generic tool stays: it is how a script without a description is
    reached, and how a model that knows a script's NAME runs it without
    knowing the tool naming convention."""
    jarvis = await boot(tmp_path, {"dim": DIM})
    try:
        assert "run_script" in offered(jarvis)
        got = await registry(jarvis).call("run_script", {"name": "Dim"}, None)
        assert got.get("status") != "error", got
    finally:
        await jarvis.async_stop()


async def test_the_registry_metadata_still_carries_the_same_shape(tmp_path: Path):
    """`jarvis.data["scripts"]` had no consumer for a long time. It has one
    now, and other things read it in tests — so its shape is load-bearing."""
    jarvis = await boot(tmp_path, {"dim": DIM})
    try:
        row = jarvis.data["scripts"]["dim"]
        assert row["service"] == "script.dim"
        assert row["description"] == DIM["description"]
        assert row["domains"] == ["light"]
    finally:
        await jarvis.async_stop()


@pytest.mark.parametrize("reserved", ["turn_on", "turn_off", "toggle", "reload"])
async def test_a_script_shadowing_a_shared_service_is_not_offered(
    tmp_path: Path, reserved: str
):
    """It gets no service registration, so a tool for it would call something
    that does not do what the tool says."""
    jarvis = await boot(
        tmp_path,
        {reserved: {"alias": "X", "description": "Shadows a shared service.",
                    "sequence": [{"delay": 1}]}},
    )
    try:
        assert f"{TOOL_PREFIX}{reserved}" not in offered(jarvis)
    finally:
        await jarvis.async_stop()
