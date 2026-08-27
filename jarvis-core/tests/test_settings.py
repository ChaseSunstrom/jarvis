"""The settings overlay: what it accepts, what it refuses, and what it survives.

The overlay is the thing standing between "a JSON file on disk" and "the
configuration every integration is built from", so most of what is worth
testing here is refusal.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.settings import (  # noqa: E402
    SETTINGS,
    SETTINGS_BY_KEY,
    SettingsError,
    SettingsOverlay,
    spec_for,
)


async def _overlay(tmp_path, values: dict) -> SettingsOverlay:
    overlay = SettingsOverlay(tmp_path)
    for key, value in values.items():
        overlay.values[key] = value
    await overlay._async_save()
    fresh = SettingsOverlay(tmp_path)
    await fresh.async_load()
    return fresh


async def test_a_hostile_settings_file_is_filtered_on_load(tmp_path, caplog):
    """The store is untrusted input.

    Write access to one JSON file must not be a way around the allowlist the
    API enforces, so every stored entry goes back through the spec table and
    its validator on the way in.
    """
    overlay = SettingsOverlay(tmp_path)
    overlay.values = {
        # Keys the allowlist does not contain, chosen because each one would
        # matter: who may talk to the API, what the assistant can see at all,
        # and where the server binds.
        "jarvis.cors_allowed_origins": ["*"],
        "llm.expose": {"include": ["lock.front_door"]},
        "jarvis.http.port": 9999,
        # A key that IS editable, with a value its validator refuses.
        "llm.approval_ttl": "abc",
        "jarvis.time_zone": "Mars/Olympus_Mons",
        # And one that is fine, so the test proves filtering rather than
        # refusing everything.
        "llm.model": "qwen3:14b",
    }
    await overlay._async_save()

    fresh = SettingsOverlay(tmp_path)
    with caplog.at_level("WARNING"):
        loaded = await fresh.async_load()

    assert loaded == {"llm.model": "qwen3:14b"}
    for dropped in ("cors_allowed_origins", "expose", "http.port", "approval_ttl", "Olympus"):
        assert dropped in caplog.text, f"{dropped} was dropped silently"


async def test_apply_never_raises_when_the_yaml_parent_is_gone(tmp_path):
    """An ordinary YAML edit must not make the box unbootable.

    `apply` runs inside Jarvis.async_setup, before there is an API to fix
    anything from. Someone commenting out the body of `voice:` leaves
    `voice: null`; that has to be a dropped entry and a note, not an exception.
    """
    overlay = await _overlay(tmp_path, {"voice.tts_voice": "en_GB-alan-medium"})

    for raw in ({}, {"voice": None}, {"voice": "a string"}, {"voice": []}):
        merged, unapplied = overlay.apply(copy.deepcopy(raw))
        assert isinstance(merged, dict)
        if raw.get("voice") in (None, {}) or "voice" not in raw:
            # An absent or null section is created; that is a usable config.
            assert merged["voice"]["tts_voice"] == "en_GB-alan-medium"
            assert unapplied == []
        else:
            # A section that is the wrong shape is reported, not walked into.
            assert len(unapplied) == 1
            assert unapplied[0].key == "voice.tts_voice"
            assert "not a section" in unapplied[0].reason


async def test_apply_refuses_a_key_a_package_supplied(tmp_path):
    """The file the user edits wins, and the reason names it.

    Overlaying a package-supplied key would mean their edit under `packages/`
    quietly stops taking effect, with nothing anywhere to explain why.
    """
    overlay = await _overlay(tmp_path, {"llm.model": "qwen3:14b"})
    raw = {"llm": {"url": "http://127.0.0.1:11434", "model": "qwen3:8b"}}

    merged, unapplied = overlay.apply(raw, {"llm.model": "brain"})

    assert merged["llm"]["model"] == "qwen3:8b"  # the package's value stands
    assert len(unapplied) == 1
    assert "packages/brain.yaml" in unapplied[0].reason

    # And a package that supplied the whole block is caught too, at the other
    # granularity merge_packages records.
    merged, unapplied = overlay.apply(raw, {"llm": "brain"})
    assert merged["llm"]["model"] == "qwen3:8b"
    assert len(unapplied) == 1


async def test_apply_does_not_mutate_its_argument(tmp_path):
    """The caller keeps `raw` as the record of what is in the file.

    That record is what the console's "reset" shows, and what `describe`
    compares against to decide whether a value came from the overlay.
    """
    overlay = await _overlay(tmp_path, {"llm.model": "qwen3:14b"})
    raw = {"llm": {"model": "qwen3:8b"}}
    before = copy.deepcopy(raw)

    merged, _ = overlay.apply(raw)

    assert raw == before
    assert merged["llm"]["model"] == "qwen3:14b"


async def test_describe_says_where_each_value_came_from(tmp_path):
    """The whole point of the row: where do I go to change this?"""
    overlay = await _overlay(tmp_path, {"llm.model": "qwen3:14b"})
    raw = {"llm": {"model": "qwen3:8b"}, "jarvis": {"name": "Jarvis"}}
    overlay.apply(raw, {"voice.tts_voice": "audio"})

    rows = {row["key"]: row for row in overlay.describe(raw, {"voice.tts_voice": "audio"})}

    assert rows["llm.model"]["source"] == "overlay"
    assert rows["llm.model"]["value"] == "qwen3:14b"
    assert rows["llm.model"]["yaml_value"] == "qwen3:8b"

    assert rows["jarvis.name"]["source"] == "yaml"
    assert rows["voice.tts_voice"]["source"] == "package"
    assert rows["voice.tts_voice"]["package"] == "audio"
    assert rows["llm.timeout"]["source"] == "default"

    # Every row says how it lands, because the console prints it verbatim.
    assert rows["llm.timeout"]["apply"] == "restart"
    assert rows["jarvis.latitude"]["apply"] == "split"
    assert rows["llm.model"]["apply"] == "live"


async def test_a_choice_written_as_a_yaml_boolean_shows_its_word_and_an_absent_switch_its_default(tmp_path):
    """`voice: speaker: mode: off` is `False` by the time YAML is done with it,
    and a choice row against [off, observe, enforce] then matched nothing;
    `demo.enabled` absent from the YAML showed `null` for a fixture house that
    was up (the server audit, 27 Aug 2026)."""
    overlay = await _overlay(tmp_path, {})
    raw = {"voice": {"speaker": {"mode": False}}, "jarvis": {"name": "Jarvis"}}
    rows = {row["key"]: row for row in overlay.describe(raw, {})}
    assert rows["voice.speaker.mode"]["value"] == "off"
    assert rows["voice.speaker.mode"]["source"] == "yaml"
    assert rows["demo.enabled"]["value"] is True
    assert rows["demo.enabled"]["source"] == "default"


async def test_the_allowlist_is_membership_not_a_prefix(tmp_path):
    """`llm.model` is editable; `llm.expose` decides what the model can see."""
    assert spec_for("llm.model").key == "llm.model"
    for refused in ("llm.expose", "llm", "jarvis.cors_allowed_origins", "llm.model.x"):
        with pytest.raises(SettingsError):
            spec_for(refused)


async def test_every_spec_validates_and_says_how_it_lands():
    """A spec with no validator is a hole in the allowlist.

    The allowlist bounds *which* keys may be written; the validator bounds what
    may be written into them. A key with neither is a free write to the
    configuration under a friendly label.
    """
    for spec in SETTINGS:
        assert spec.validate is not None, f"{spec.key} accepts anything"
        assert spec.apply in ("live", "restart", "split"), spec.key
        assert spec.path, spec.key
        assert spec.key == ".".join(spec.path), f"{spec.key} does not match its path"
        assert spec.label and spec.group, spec.key
    assert len(SETTINGS_BY_KEY) == len(SETTINGS), "two specs share a key"


async def test_a_live_setting_that_needs_a_hook_has_one():
    """Anything claiming to apply live to a cached object must push it there.

    `llm` and `voice` both snapshot their configuration into objects at setup.
    A key in those sections labelled `live` with no apply_hook would change the
    config dict, change nothing anyone reads, and report success.
    """
    for spec in SETTINGS:
        if spec.apply != "live":
            continue
        if spec.path[0] in ("llm", "voice"):
            assert spec.apply_hook is not None, (
                f"{spec.key} claims to apply live, but {spec.path[0]} caches its "
                "configuration at setup and nothing pushes the new value in"
            )


async def test_a_validator_rejects_what_it_says_it_rejects(tmp_path):
    overlay = SettingsOverlay(tmp_path)

    with pytest.raises(SettingsError):
        await overlay.async_set("llm.options.temperature", 5)
    with pytest.raises(SettingsError):
        await overlay.async_set("llm.options.temperature", "hot")
    with pytest.raises(SettingsError):
        await overlay.async_set("jarvis.log_level", "chatty")
    with pytest.raises(SettingsError):
        await overlay.async_set("jarvis.time_zone", "Mars/Olympus_Mons")
    with pytest.raises(SettingsError):
        await overlay.async_set("jarvis.name", "  ")

    assert await overlay.async_set("llm.options.temperature", "0.7") == 0.7
    assert await overlay.async_set("jarvis.time_zone", "America/New_York") == "America/New_York"
    assert overlay.values["llm.options.temperature"] == 0.7


async def test_reset_forgets_the_override_and_says_whether_it_did(tmp_path):
    overlay = SettingsOverlay(tmp_path)
    await overlay.async_set("llm.model", "qwen3:14b")

    assert await overlay.async_reset("llm.model") is True
    assert await overlay.async_reset("llm.model") is False
    assert overlay.values == {}

    reloaded = SettingsOverlay(tmp_path)
    assert await reloaded.async_load() == {}


# ---------------------------------------------------------------------------
# the wiring: does an overlaid setting reach the thing that reads it?
# ---------------------------------------------------------------------------
async def test_an_overlaid_setting_reaches_the_integration_at_boot(tmp_path):
    """The bug this commit exists to prevent, tested where it lives.

    Integrations are constructed from the dict handed to
    `async_setup_integrations`, not from `jarvis.config`. An overlay applied
    only to the attribute would leave the assistant running the file's model
    while the console reported the overlay's — and a test that inspected
    `jarvis.config` would pass throughout. So this asserts on the object the
    conversation actually uses.
    """
    from jarvis.core import Jarvis

    overlay = SettingsOverlay(tmp_path)
    await overlay.async_set("llm.model", "qwen3:14b")
    await overlay.async_set("llm.max_tool_rounds", 3)

    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"llm": {"model": "qwen3:8b", "url": "http://127.0.0.1:11434"}})

    agent = jarvis.data.get("llm")
    assert agent is not None, "the llm integration did not set up"
    assert agent.model == "qwen3:14b"
    assert agent.max_tool_rounds == 3
    # And the client's own default, which `chat()` falls back to whenever a
    # caller does not pass a model. Setting only the agent leaves half the
    # calls on the old model, which reads as the setting working sometimes.
    assert agent.client.model == "qwen3:14b"

    await jarvis.async_stop()


async def test_the_raw_config_is_kept_so_reset_can_show_what_it_reverts_to(tmp_path):
    from jarvis.core import Jarvis

    overlay = SettingsOverlay(tmp_path)
    await overlay.async_set("jarvis.name", "Friday")

    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"jarvis": {"name": "Jarvis"}})

    assert jarvis.config["jarvis"]["name"] == "Friday"
    assert jarvis.raw_config["jarvis"]["name"] == "Jarvis"

    await jarvis.async_stop()


async def test_a_dropped_overlay_entry_does_not_stop_startup(tmp_path, caplog):
    """The unbootable-box case, end to end."""
    from jarvis.core import Jarvis

    overlay = SettingsOverlay(tmp_path)
    await overlay.async_set("voice.tts_voice", "en_GB-alan-medium")

    jarvis = Jarvis(tmp_path)
    with caplog.at_level("WARNING"):
        # `voice:` present but not a section — what commenting out its body
        # leaves behind.
        await jarvis.async_setup({"jarvis": {"name": "Jarvis"}, "voice": "oops"})

    assert jarvis.config["jarvis"]["name"] == "Jarvis"  # it booted
    assert [entry.key for entry in jarvis.settings.unapplied] == ["voice.tts_voice"]
    assert "voice.tts_voice not applied" in caplog.text

    await jarvis.async_stop()


def test_the_voice_pace_is_a_setting_that_says_where_the_real_knob_is():
    """M70: the pace the house speaks at is on Settings › Voice as a number,
    marked restart — Piper takes its length scale at start, from
    PIPER_LENGTH_SCALE — with the note naming that variable and the container
    to restart, so the row cannot promise a change the next reply will not
    make. Bounds keep it a pace a person can follow."""
    from jarvis.settings import APPLY_RESTART, SETTINGS_BY_KEY, SettingsError

    spec = SETTINGS_BY_KEY["voice.tts.length_scale"]
    assert spec.path == ("voice", "tts", "length_scale")
    assert spec.group == "Voice" and spec.type == "number"
    assert spec.apply == APPLY_RESTART
    assert "PIPER_LENGTH_SCALE" in spec.note and "wyoming-piper" in spec.note
    assert spec.validate is not None
    assert spec.validate("0.9") == 0.9
    for bad in ("0.2", "3", "fast"):
        with pytest.raises(SettingsError):
            spec.validate(bad)
