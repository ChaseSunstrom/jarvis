"""The arguments a model actually sends, against the schema it was shown.

## Why this exists

`ToolRegistry.call` took whatever arrived and handed it straight to the
handler. `required` and `type` were decorative for every built-in tool: nothing
in `jarvis-core` imported `jsonschema` and nothing consulted `tool.parameters`.

That is survivable right up until it is not, because the failure surfaces three
layers from the mistake. `turn_on({"entity": "lamp"})` — the wrong key, the
obvious intent — resolved no targets and came back as
*"nothing here matches None"*, a sentence about the house. The model has no way
to act on that: it reads as "the lamp does not exist", so the next round asks
`list_entities`, spends the context window on the whole house, and still does
not know it typed `entity` for `name`.

## Coercion, not validation

A strict validator would be the wrong instrument. A local model writes `"50"`
where the schema says integer and `"true"` where it says boolean about as often
as it gets them right, and refusing those would burn one of five rounds on a
call everyone can see is correct. So anything unambiguous is converted in
silence, and only two things are refused: a missing **required** argument, and a
value that cannot be reached from what arrived.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.llm.tools import (  # noqa: E402
    Tool,
    ToolRegistry,
    coerce_arguments,
    schema_object,
)


def _tool(properties: dict, required: list[str] | None = None) -> Tool:
    return Tool(
        name="demo",
        description="",
        parameters=schema_object(properties, required=required or []),
        handler=lambda args, context: {"status": "ok", "args": args},
    )


# ---------------------------------------------------------------------------
# what is quietly fixed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "wanted,given,expected",
    [
        ("integer", "50", 50),
        ("integer", 50, 50),
        ("integer", "50.0", 50),  # written by something that thinks in floats
        ("number", "21.5", 21.5),
        ("number", 21, 21),
        ("boolean", "true", True),
        ("boolean", "TRUE", True),
        ("boolean", "yes", True),
        ("boolean", "1", True),
        ("boolean", "false", False),
        ("boolean", "off", False),
        ("boolean", False, False),
        ("string", 42, "42"),
        ("string", "kitchen lamp", "kitchen lamp"),
        ("array", "a, b, c", ["a", "b", "c"]),
        ("array", "solo", ["solo"]),
        ("array", ["already"], ["already"]),
    ],
)
def test_a_value_that_can_only_mean_one_thing_is_converted(wanted, given, expected):
    """These are not mistakes worth spending a round on."""
    tool = _tool({"x": {"type": wanted}})
    out, complaint = coerce_arguments(tool, {"x": given})
    assert complaint == "", complaint
    assert out["x"] == expected


def test_a_bool_is_not_quietly_accepted_as_an_integer():
    """`bool` is a subclass of `int` in Python.

    An `isinstance(value, int)` check ordered before the boolean branch lets
    `{"brightness": true}` through as the integer 1 — a lamp set to 1/255,
    which looks off. The ordering in `_coerce` is the fix and this is what
    pins it.
    """
    tool = _tool({"brightness": {"type": "integer"}})
    out, complaint = coerce_arguments(tool, {"brightness": True})
    assert complaint, "True was accepted as an integer"


def test_unknown_keys_are_passed_through_untouched():
    """Several handlers read arguments their schema does not declare.

    `area_id` beside `area`, and the `input` fallback `parse_arguments`
    produces for an unparseable blob. Dropping them here would break working
    tools to enforce a tidiness nobody asked for.
    """
    tool = _tool({"name": {"type": "string"}})
    out, complaint = coerce_arguments(tool, {"name": "lamp", "area_id": "kitchen"})
    assert complaint == ""
    assert out["area_id"] == "kitchen"


def test_a_tool_with_no_declared_properties_accepts_anything():
    """Not every tool declares a schema, and one that does not is not broken."""
    tool = Tool(name="demo", description="", parameters={}, handler=None)
    out, complaint = coerce_arguments(tool, {"whatever": 1})
    assert complaint == ""
    assert out == {"whatever": 1}


# ---------------------------------------------------------------------------
# what is refused, and how it reads
# ---------------------------------------------------------------------------
def test_a_missing_required_argument_is_named():
    tool = _tool({"question": {"type": "string"}}, required=["question"])
    out, complaint = coerce_arguments(tool, {})
    assert "question" in complaint
    assert "demo" in complaint


def test_an_empty_string_counts_as_missing():
    """A model writes `""` for "I have no value for this" more often than null."""
    tool = _tool({"question": {"type": "string"}}, required=["question"])
    _, complaint = coerce_arguments(tool, {"question": "   "})
    assert "question" in complaint


def test_a_value_that_cannot_be_a_number_says_which_argument():
    """The message is the whole point — it is what the next round reads."""
    tool = _tool({"brightness_pct": {"type": "integer"}})
    _, complaint = coerce_arguments(tool, {"brightness_pct": "quite bright"})
    assert "brightness_pct" in complaint
    assert "integer" in complaint


def test_a_fractional_value_is_not_rounded_on_the_models_behalf():
    """`"50.5"` is a different number, and guessing which way is not ours."""
    tool = _tool({"x": {"type": "integer"}})
    _, complaint = coerce_arguments(tool, {"x": "50.5"})
    assert complaint


def test_a_word_that_is_not_a_boolean_is_refused():
    tool = _tool({"on": {"type": "boolean"}})
    _, complaint = coerce_arguments(tool, {"on": "maybe"})
    assert "on" in complaint


# ---------------------------------------------------------------------------
# through the registry, which is where it matters
# ---------------------------------------------------------------------------
async def test_the_registry_refuses_before_the_handler_sees_it():
    """And the error names the argument rather than describing the house."""
    registry = ToolRegistry(jarvis=None)
    seen: list[dict] = []

    def handler(args, context):
        seen.append(args)
        return {"status": "ok"}

    registry.register(
        name="set_it",
        description="",
        parameters=schema_object(
            {"value": {"type": "integer"}}, required=["value"]
        ),
        handler=handler,
    )

    result = await registry.call("set_it", {})

    assert result["status"] == "error"
    assert "value" in result["error"]
    assert seen == [], "the handler ran on arguments that do not satisfy its schema"
    # The schema comes back, so a model that misread it once can re-read it
    # without spending a round on `list_entities`.
    assert "value" in result["expected"]


async def test_the_registry_coerces_before_the_handler_sees_it():
    registry = ToolRegistry(jarvis=None)
    seen: list[dict] = []

    registry.register(
        name="set_it",
        description="",
        parameters=schema_object({"value": {"type": "integer"}}),
        handler=lambda args, context: (seen.append(args), {"status": "ok"})[1],
    )

    await registry.call("set_it", {"value": "42"})

    assert seen == [{"value": 42}], "the handler was handed a string"


async def test_a_bad_call_never_becomes_an_approval_prompt():
    """A Tier-3 call that cannot possibly work must not reach a human.

    Before argument checking existed it did: the person was asked, said yes,
    and only then did the tool fail on its own arguments. An approval is a
    demand on somebody's attention and it should only ever be spent on a call
    that could actually run.
    """
    registry = ToolRegistry(jarvis=None)
    registry.register(
        name="dangerous",
        description="",
        parameters=schema_object({"target": {"type": "string"}}, required=["target"]),
        handler=lambda args, context: {"status": "ok"},
        tier=3,
    )

    result = await registry.call("dangerous", {})

    assert result["status"] == "error"
    assert not registry.pending_requests()
