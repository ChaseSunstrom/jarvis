"""`create_tool` — the tool the model uses to write itself a tool.

## Why this exists

`create_tool` is the one capability that lets Jarvis extend itself, and it
could not succeed once. The schema the model was shown declared::

    "service": {"type": "string", "description": "The service to call, domain.name."},
    "fields":  {"type": "object", "description": "Parameters, by name."},

while `authored_tools.validate` has always required `service` to be an OBJECT
containing a `url`, and rejects any top-level key outside
`{name, description, tier, domain, service}`. A model following its own schema
was therefore refused twice over — "A tool needs a `service` block saying what
it calls." and "Unknown field(s): fields." — for every manifest it ever wrote.

Nothing caught it. `test_tool_api.py` covers `async_create_tool` thoroughly, but
every case there hand-builds a correct nested `GOOD` spec, so it exercised the
API and never the schema the model actually reads. The console was fine for the
same reason: `toolDraft.ts` builds the nested shape itself. The only broken path
was the one with no test — the model's.

## What is pinned here

That **the advertised schema and the validator describe the same object**, by
building a manifest out of the schema and running it through the real handler.
A future edit to either side that does not touch the other fails here rather
than at 2am on someone's house.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.authored_tools import validate  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    TIER_APPROVAL,
    ToolRegistry,
    register_builtin_tools,
)


@pytest.fixture
def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    registry = ToolRegistry(box)
    register_builtin_tools(registry)
    box.data["llm_tools"] = registry
    box.data["llm_client"] = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    )
    return box


def _schema_of(jarvis: Jarvis, tool: str) -> dict[str, Any]:
    return jarvis.data["llm_tools"].get(tool).parameters


async def _create_as_a_human_would(jarvis: Jarvis, manifest: dict[str, Any]) -> Any:
    """Call `create_tool` and approve it, which is the only way it ever runs.

    `create_tool` is Tier 3 unconditionally, so calling it returns
    `approval_required` and does nothing. Driving the gate here rather than
    reaching past it to the handler is deliberate: the manifest a human is shown
    is the pinned one, and a test that skipped the gate would not prove the
    manifest survives it.
    """
    registry = jarvis.data["llm_tools"]
    held = await registry.call("create_tool", manifest, context=None)
    if held.get("status") != "approval_required":
        return held
    return await registry.approve_request(held["request_id"])


# ---------------------------------------------------------------------------
# the manifest a model writes by reading the schema
# ---------------------------------------------------------------------------
#: Exactly what an obedient model produces from `create_tool`'s schema: a
#: nested `service` carrying the url, and no top-level `fields`.
AS_ADVERTISED = {
    "name": "bin_day",
    "description": "Which bin goes out this week.",
    "tier": 1,
    "service": {
        "method": "GET",
        "url": "http://192.168.1.5/bins?street={{ street }}",
        "fields": {"street": {"type": "string", "description": "The street name."}},
    },
}


async def test_a_manifest_written_from_the_schema_is_accepted(jarvis):
    """The regression itself. This failed 100% of the time before the fix."""
    registry = jarvis.data["llm_tools"]

    outcome = await _create_as_a_human_would(jarvis, dict(AS_ADVERTISED))

    assert outcome["status"] == "executed", outcome
    assert outcome["result"]["status"] != "error", outcome["result"].get("error")
    assert "bin_day" in registry.names(), (
        "accepted but never registered — the model cannot call what it just wrote"
    )


async def test_the_tool_it_wrote_actually_runs(jarvis):
    """Accepting a manifest is not the claim; the tool being callable is."""
    registry = jarvis.data["llm_tools"]
    await _create_as_a_human_would(jarvis, dict(AS_ADVERTISED))

    out = await registry.call("bin_day", {"street": "Acacia Avenue"})

    assert out["status"] == "ok"
    # Somebody else's bytes, so fenced like every other such tool.
    assert out["content_is_untrusted"] is True


# ---------------------------------------------------------------------------
# schema <-> validator, mechanically
# ---------------------------------------------------------------------------
def test_the_schema_and_the_validator_describe_one_object(jarvis):
    """Every top-level property the schema offers must be one `validate` allows.

    This is the check that would have caught the original bug on the day it was
    written: `fields` was offered at the top level and had nowhere to go.
    """
    from jarvis.llm.authored_tools import ALLOWED_FIELDS, ALLOWED_SERVICE_FIELDS

    schema = _schema_of(jarvis, "create_tool")
    offered = set(schema["properties"])
    stray = offered - ALLOWED_FIELDS
    assert not stray, (
        f"create_tool offers top-level {sorted(stray)}, which validate() "
        "rejects as an unknown field. Every manifest naming one is refused."
    )

    service = schema["properties"]["service"]
    assert service["type"] == "object", (
        "validate() requires `service` to be a block containing a url; a "
        "schema calling it a string asks the model for something that is "
        "always refused"
    )
    offered_service = set(service.get("properties", {}))
    stray_service = offered_service - ALLOWED_SERVICE_FIELDS
    assert not stray_service, (
        f"create_tool's service block offers {sorted(stray_service)}, which "
        "validate() rejects."
    )


def test_the_schema_requires_what_the_validator_requires(jarvis):
    """`url` is mandatory to `validate`, so the schema must say so.

    A required field the schema calls optional is a manifest the model writes
    without it and has refused.
    """
    schema = _schema_of(jarvis, "create_tool")
    assert "service" in schema.get("required", []), (
        "validate() refuses a manifest with no service block"
    )
    assert "url" in schema["properties"]["service"].get("required", []), (
        "validate() refuses a service block with no url"
    )


def test_the_example_in_the_description_is_a_valid_manifest(jarvis):
    """The worked example the model copies must survive the validator.

    A description that shows a shape the validator refuses teaches the model to
    fail — which is precisely how this tool spent its whole life.
    """
    import json
    import re

    description = jarvis.data["llm_tools"].get("create_tool").description
    match = re.search(r"Example:\s*(\{.*\})", description, re.S)
    assert match, "the description no longer carries a worked example"

    example = json.loads(match.group(1))
    validate(example)  # raises AuthoredToolError if the example is a lie


def test_create_tool_is_still_tier_three(jarvis):
    """Fixing the schema must not have made self-extension unattended.

    A tier, not a gate: nothing about the arguments may turn writing a new
    capability into something that happens without a human.
    """
    assert _tier(jarvis, "create_tool") == TIER_APPROVAL


def _tier(jarvis: Jarvis, name: str) -> int:
    return jarvis.data["llm_tools"].get(name).tier


# ---------------------------------------------------------------------------
# the old shape must fail loudly, not silently
# ---------------------------------------------------------------------------
async def test_the_old_flat_shape_is_refused_before_it_reaches_a_human(jarvis):
    """There is deliberately no leniency for the shape that used to be advertised.

    Two accepted spellings is how the first one got forgotten.

    Note *where* the refusal happens: `service` is declared an object, a string
    cannot be coerced to one, so `ToolRegistry.call` turns it back at the
    schema check — before the Tier-3 gate raises anything. That is the right
    end. A manifest that cannot possibly validate should not become a prompt on
    somebody's phone asking them to approve it, and before argument checking
    existed that is exactly what it did: the human was asked, said yes, and
    *then* it failed.
    """
    registry = jarvis.data["llm_tools"]

    result = await registry.call(
        "create_tool",
        {
            "name": "bin_day",
            "description": "Which bin goes out this week.",
            "service": "http.get",
            "fields": {"street": {"type": "string"}},
        },
        context=None,
    )

    assert result["status"] == "error"
    assert result["error"], "a refusal with no message teaches the model nothing"
    assert "service" in result["error"], "the message must name the argument at fault"
    assert not registry.pending_requests(), (
        "a manifest that cannot validate was still put in front of a human"
    )
    assert "bin_day" not in registry.names()
