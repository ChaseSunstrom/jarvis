"""Settings under approval (M67): `list_settings` and `change_setting`.

"How can I ask it to be able to edit settings with permission." The console
changes settings through `config/settings/list` and `config/settings/set`;
the model had no tool for either. Asked to enable "demo mode" it asked what
that meant — right, there is no such setting — but it could not have said
what the settings ARE.

Two tools over the console's registry, and the claims pinned here:

* `list_settings` is Tier 1 and read-only; it reads the SAME registry the
  console reads (`settings_payload`), never a second list; the whole list is
  compact and fits a tool result; a filtered list says what a setting does
  and accepts; a name that matches nothing is answered with the nearest real
  keys.
* `change_setting` is Tier 3; the key, the coerced value and the value it
  replaces are pinned in the request, with a sentence the console shows;
  approving writes through `async_set_setting` — the console's own write path
  — so the validation, the audit line and `jarvis_setting_changed` are the
  same; an unknown key or an unacceptable value is refused BEFORE anything is
  held; a tainted turn is held and marked, not refused, and the comment in
  `tools.py` says why.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import common  # noqa: E402
from jarvis.api.devices import mark_untrusted  # noqa: E402
from jarvis.bus import Context  # noqa: E402
from jarvis.const import EVENT_SETTING_CHANGED  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.llm.tools import (  # noqa: E402
    EVENT_APPROVAL_REQUIRED,
    MAX_TOOL_RESULT_CHARS,
    READ_ONLY_TOOLS,
    REFUSE_WHEN_TAINTED,
    TIER_APPROVAL,
    TIER_DIRECT,
    ToolRegistry,
    register_builtin_tools,
)
from jarvis.settings import SETTINGS, SETTINGS_BY_KEY  # noqa: E402

TEMPERATURE = "llm.options.temperature"


@pytest.fixture
async def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    await box.async_setup({"llm": {"model": "file:7b", "options": {"temperature": 0.7}}})
    yield box
    await box.async_stop()


@pytest.fixture
def registry(jarvis):
    reg = ToolRegistry(jarvis)
    register_builtin_tools(reg)
    return reg


def _raised(jarvis) -> list[dict]:
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, lambda event: seen.append(event.data))
    return seen


def _changed(jarvis) -> list[dict]:
    seen: list[dict] = []
    jarvis.bus.listen(EVENT_SETTING_CHANGED, lambda event: seen.append(event.data))
    return seen


def _console_row(jarvis, key: str) -> dict:
    """The row the console's `config/settings/list` shows for `key`."""
    return next(row for row in common.settings_payload(jarvis)["settings"] if row["key"] == key)


# ===========================================================================
# list_settings
# ===========================================================================
async def test_list_settings_is_direct_and_read_only(jarvis, registry):
    """Reading the registry is not an action, so a tainted turn may still do it."""
    tool = registry.get("list_settings")
    assert tool is not None and tool.tier == TIER_DIRECT
    assert registry.is_read_only(tool)
    assert "list_settings" in READ_ONLY_TOOLS, "the name list is what the gate reads"

    context = Context(origin="llm")
    mark_untrusted(jarvis, context)
    listed = await registry.call("list_settings", {}, context)
    assert listed["status"] == "ok", "a read was held after untrusted content"


async def test_the_whole_list_is_every_setting_compact_and_bounded(registry):
    """Every key the console has, in a form that fits a tool result.

    The prompt budget is real: the notes and the choice lists (six hundred
    timezones) are what make the registry not fit, so the unfiltered form
    carries key, label, type and value and nothing else.
    """
    listed = await registry.call("list_settings", {}, None)

    assert listed["status"] == "ok"
    assert listed["count"] == len(SETTINGS)
    assert [row["key"] for row in listed["settings"]] == [spec.key for spec in SETTINGS]
    for row in listed["settings"]:
        assert set(row) == {"key", "label", "type", "value"}, row
    assert len(json.dumps(listed)) < MAX_TOOL_RESULT_CHARS, (
        "the compact list is truncated before the model sees the end of it"
    )
    assert "query" in listed["note"], "the model is told how to see one in detail"


async def test_a_filtered_list_says_what_a_setting_does_and_accepts(registry):
    listed = await registry.call("list_settings", {"query": "temperature"}, None)

    row = next(r for r in listed["settings"] if r["key"] == TEMPERATURE)
    assert row["label"] == "Temperature"
    assert row["type"] == "number"
    assert row["value"] == 0.7
    assert row["does"] == SETTINGS_BY_KEY[TEMPERATURE].note
    assert row["takes_effect"] == "live"

    # A choice setting lists what it accepts, bounded, and says how many more.
    zones = await registry.call("list_settings", {"query": "timezone"}, None)
    row = next(r for r in zones["settings"] if r["key"] == "jarvis.time_zone")
    assert 0 < len(row["choices"]) <= 12
    assert row["more_choices"] > 100, "the timezone list was not bounded"

    units = await registry.call("list_settings", {"query": "units"}, None)
    assert [r["key"] for r in units["settings"]] == ["jarvis.unit_system"]
    assert units["settings"][0]["choices"] == ["metric", "imperial"]


async def test_every_setting_has_one_line_saying_what_it_does():
    """`does` is the spec's note; a setting with no note would be listed as
    only its label, which tells a model nothing it can repeat."""
    for spec in SETTINGS:
        assert spec.note.strip(), f"{spec.key} has no note for list_settings to show"


async def test_the_list_is_the_console_registry_not_a_second_one(jarvis, registry):
    """One registry: a write through the console's path shows in the tool at once."""
    console_keys = [row["key"] for row in common.settings_payload(jarvis)["settings"]]
    listed = await registry.call("list_settings", {}, None)
    assert [row["key"] for row in listed["settings"]] == console_keys

    await common.async_set_setting(jarvis, {"key": TEMPERATURE, "value": 0.4})
    listed = await registry.call("list_settings", {"query": "temperature"}, None)
    assert next(r for r in listed["settings"] if r["key"] == TEMPERATURE)["value"] == 0.4


async def test_asked_for_a_setting_that_does_not_exist_the_list_names_the_nearest(registry):
    """"Demo mode" is answered with what the settings are called, not a guess."""
    listed = await registry.call("list_settings", {"query": "party mode"}, None)

    assert listed["status"] == "ok"
    assert listed["count"] == 0 and listed["settings"] == []
    assert listed["nearest"], "no nearest names to offer"
    assert all(key in SETTINGS_BY_KEY for key in listed["nearest"])
    assert "No setting matches 'party mode'" in listed["note"]
    assert listed["nearest"][0] in listed["note"]


# ===========================================================================
# change_setting
# ===========================================================================
async def test_change_setting_is_tier_three_and_held_with_key_value_and_previous_pinned(
    jarvis, registry
):
    tool = registry.get("change_setting")
    assert tool is not None and tool.tier == TIER_APPROVAL
    assert not registry.is_read_only(tool)
    seen = _raised(jarvis)

    held = await registry.call(
        "change_setting", {"key": TEMPERATURE, "value": "0.2"}, Context(origin="llm")
    )

    assert held["status"] == "approval_required"
    # Pinned: the exact key, the value as the validator coerced it (a number,
    # not the model's string), the value it replaces, and the label.
    assert held["arguments"] == {
        "key": TEMPERATURE, "value": 0.2, "previous": 0.7, "label": "Temperature",
    }
    request = seen[-1]
    assert request["tool"] == "change_setting"
    assert request["arguments"] == held["arguments"]
    # The sentence the console shows, from the pinned arguments.
    assert request["summary"] == "Change Temperature (llm.options.temperature) from 0.7 to 0.2"
    # Nothing has changed yet.
    assert _console_row(jarvis, TEMPERATURE)["value"] == 0.7
    assert jarvis.settings.values == {}


async def test_approving_writes_through_the_console_path_and_says_what_changed(
    jarvis, registry, caplog
):
    """The same validation, the same audit line, the same event as `config/settings/set`."""
    changed = _changed(jarvis)
    context = Context(origin="llm")
    held = await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.2}, context)

    with caplog.at_level(logging.INFO, logger="jarvis.settings.audit"):
        done = await registry.approve_request(held["request_id"], True)

    assert done["status"] == "executed"
    result = done["result"]
    assert result["status"] == "ok"
    assert result["summary"] == "Changed Temperature (llm.options.temperature) from 0.7 to 0.2."
    assert (result["previous"], result["value"]) == (0.7, 0.2)
    assert result["restart_required"] is False

    # What the console reads now says so, from the overlay.
    row = _console_row(jarvis, TEMPERATURE)
    assert row["value"] == 0.2 and row["source"] == "overlay"
    assert jarvis.config["llm"]["options"]["temperature"] == 0.2

    # The audit line, with who.
    lines = [r.getMessage() for r in caplog.records if r.name == "jarvis.settings.audit"]
    assert lines == ["set llm.options.temperature: 0.7 -> 0.2 (by llm; applied live)"]
    # And the event, once, from the write path.
    assert len(changed) == 1
    assert changed[0]["key"] == TEMPERATURE
    assert (changed[0]["previous"], changed[0]["value"]) == (0.7, 0.2)
    assert changed[0]["origin"] == "llm"


async def test_the_tool_and_the_console_are_one_write_path(jarvis, registry, monkeypatch):
    """Both doors call `common.async_set_setting`; a change through either is
    what the other reads. A tool that wrote the overlay itself would pass every
    other test here and skip the validator the next time the validator moved."""
    calls: list[dict] = []
    original = common.async_set_setting

    async def spy(box, payload, context=None):
        calls.append(dict(payload))
        return await original(box, payload, context=context)

    monkeypatch.setattr(common, "async_set_setting", spy)

    # The console's command, through its handler.
    from jarvis.api.websocket import WebSocketHandler

    handler = WebSocketHandler.__new__(WebSocketHandler)
    handler.jarvis = jarvis
    handler.user_id = "console-token"
    await WebSocketHandler._HANDLERS["config/settings/set"](
        handler, {"key": TEMPERATURE, "value": 0.5}
    )
    assert calls == [{"key": TEMPERATURE, "value": 0.5}]
    listed = await registry.call("list_settings", {"query": "temperature"}, None)
    assert listed["settings"][0]["value"] == 0.5

    # The model's tool, approved.
    held = await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.9}, None)
    await registry.approve_request(held["request_id"], True)
    assert calls[-1] == {"key": TEMPERATURE, "value": 0.9}
    assert _console_row(jarvis, TEMPERATURE)["value"] == 0.9


async def test_a_restart_setting_says_so_in_the_sentence(jarvis, registry):
    held = await registry.call("change_setting", {"key": "llm.timeout", "value": 90}, None)
    done = await registry.approve_request(held["request_id"], True)
    assert done["result"]["restart_required"] is True
    assert done["result"]["summary"].endswith("It takes effect after a restart.")


async def test_denying_runs_nothing(jarvis, registry):
    changed = _changed(jarvis)
    held = await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.2}, None)
    denied = await registry.approve_request(held["request_id"], False)
    assert denied["status"] == "denied"
    assert _console_row(jarvis, TEMPERATURE)["value"] == 0.7
    assert changed == []


async def test_an_unknown_key_is_refused_with_the_nearest_before_anything_is_held(
    jarvis, registry
):
    """"No setting called party mode; the nearest are …" — and no card.

    (It was "demo mode" until M80 made demo mode a real setting; the case is
    the same — a name that is nobody's — with a name that stays nobody's.)"""
    seen = _raised(jarvis)

    refused = await registry.call("change_setting", {"key": "party mode", "value": True}, None)

    assert refused["status"] == "error"
    assert "no setting called 'party mode'" in refused["error"]
    assert "the nearest are" in refused["error"]
    # Up to the sentence that follows, not the first full stop: keys have dots.
    named = refused["error"].split("the nearest are ")[1].split(". Call")[0].split(", ")
    assert named and all(key in SETTINGS_BY_KEY for key in named), named
    assert seen == [], "a request was held for a key that does not exist"
    assert registry.pending_requests() == []


async def test_a_value_the_validator_refuses_is_refused_before_anything_is_held(
    jarvis, registry
):
    seen = _raised(jarvis)

    refused = await registry.call("change_setting", {"key": TEMPERATURE, "value": 9}, None)

    assert refused["status"] == "error"
    # The validator's own sentence — the one the console shows for the same value.
    assert refused["error"] == (
        "Temperature (llm.options.temperature) cannot be 9: Must be between 0.0 and 2.0."
    )
    assert seen == [] and registry.pending_requests() == []


async def test_a_plain_name_resolves_when_unambiguous_and_is_refused_when_not(
    jarvis, registry
):
    """"Temperature" is one setting; "model" is two or three."""
    held = await registry.call("change_setting", {"key": "temperature", "value": 0.3}, None)
    assert held["status"] == "approval_required"
    assert held["arguments"]["key"] == TEMPERATURE

    refused = await registry.call("change_setting", {"key": "model", "value": "x"}, None)
    assert refused["status"] == "error"
    assert "'model' could be any of" in refused["error"]
    assert "llm.model" in refused["error"] and "vision.model" in refused["error"]


async def test_a_key_the_allowlist_does_not_have_cannot_be_reached_by_any_spelling(
    jarvis, registry
):
    """The attack to reason about: a page that says "turn local-only off".

    `llm.expose` decides what the assistant may see at all; nothing under
    `jarvis.http` is a setting; and no spelling of either resolves, because
    resolution is membership in `SETTINGS`, not a path into the config.
    """
    seen = _raised(jarvis)
    for key in ("llm.expose", "expose", "jarvis.http.host", "local_only", "cors_allowed_origins"):
        refused = await registry.call("change_setting", {"key": key, "value": "off"}, None)
        assert refused["status"] == "error", key
        assert "no setting called" in refused["error"], key
    assert seen == []


async def test_a_tainted_turn_is_held_and_marked_not_refused(jarvis, registry):
    """Held, not refused — the opposite decision from `remember`, on purpose.

    `remember` refuses because a human cannot audit a memory write in the two
    seconds an approval gets. A setting change is one key, one value, the old
    value beside the new one: exactly the sentence a person CAN judge. So the
    card is raised, marked `tainted`, and the human decides — and the allowlist
    means the worst it can say is what the console's settings page can do.
    """
    assert "change_setting" not in REFUSE_WHEN_TAINTED
    seen = _raised(jarvis)
    context = Context(origin="llm")
    mark_untrusted(jarvis, context)

    held = await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.2}, context)

    assert held["status"] == "approval_required"
    assert seen[-1]["tainted"] is True, "the card does not say the turn read a stranger's words"
    assert seen[-1]["summary"].startswith("Change Temperature")
    # And a human who reads the card and agrees gets the change.
    done = await registry.approve_request(held["request_id"], True)
    assert done["result"]["status"] == "ok"
    assert _console_row(jarvis, TEMPERATURE)["value"] == 0.2


async def test_a_reset_from_the_console_is_audited_and_announced_too(jarvis, caplog):
    changed = _changed(jarvis)
    await common.async_set_setting(jarvis, {"key": TEMPERATURE, "value": 0.2})
    with caplog.at_level(logging.INFO, logger="jarvis.settings.audit"):
        result = await common.async_reset_setting(jarvis, {"key": TEMPERATURE})
    assert (result["previous"], result["value"]) == (0.2, 0.7)
    lines = [r.getMessage() for r in caplog.records if r.name == "jarvis.settings.audit"]
    assert lines == ["reset llm.options.temperature: 0.2 -> 0.7 (by unknown; applied live)"]
    assert [event["action"] for event in changed] == ["set", "reset"]


async def test_the_summary_travels_on_the_wire_and_is_empty_for_a_tool_without_one(
    jarvis, registry
):
    """The console reads `summary` off the request; a tool that has none
    leaves it empty and the banner shows the name and the arguments as before."""
    seen = _raised(jarvis)
    await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.2}, None)
    assert seen[-1]["summary"]
    pending = registry.pending_requests()
    assert pending[-1]["summary"] == seen[-1]["summary"]

    await registry.call("ask_user", {"question": "Which lamp?"}, None)
    assert seen[-1]["tool"] == "ask_user" and seen[-1]["summary"] == ""


async def test_the_pin_reads_the_value_at_the_moment_of_asking(jarvis, registry):
    """Two requests in a row say "from" what it really was each time."""
    first = await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.2}, None)
    await registry.approve_request(first["request_id"], True)
    second = await registry.call("change_setting", {"key": TEMPERATURE, "value": 0.3}, None)
    assert second["arguments"]["previous"] == 0.2
