"""Changing settings from the console.

The overlay has its own tests: they cover validation, storage and the merge.
What is proved here is the part the console depends on and the overlay alone
cannot do — that changing a setting marked `live` reaches the object already
running, rather than only the dict the *next* boot will be built from.

Every spec carries an `apply_hook` for exactly that, and until this API called
them nothing did: the console would report a model that nothing was using.
"""

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import common  # noqa: E402
from jarvis.api.common import ApiError  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.settings import APPLY_LIVE, SETTINGS, SETTINGS_BY_KEY  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.model = "old:7b"


class FakeAgent:
    """Stands in for the running LLM agent the apply hooks reach for."""

    def __init__(self) -> None:
        self.model = "old:7b"
        self.options: dict = {}
        self.max_tool_rounds = 3
        self.client = FakeClient()


@pytest.fixture
def jarvis(tmp_path):
    box = Jarvis(tmp_path)
    box.raw_config = {"llm": {"model": "file:7b"}, "jarvis": {"name": "From File"}}
    return box


@pytest.fixture
def agent(jarvis):
    fake = FakeAgent()
    # Where `_llm_agent` looks. Wired the way the integration wires it, so the
    # test breaks if that lookup moves.
    jarvis.data["llm"] = fake
    return fake


async def test_a_live_setting_reaches_the_running_agent(jarvis, agent):
    """The whole point: stored is not the same as in effect."""
    result = await common.async_set_setting(jarvis, {"key": "llm.model", "value": "qwen3:14b"})

    assert agent.model == "qwen3:14b"
    # Both, deliberately: the client keeps its own default and `chat()` falls
    # back to it, so setting only the agent leaves half the calls on the old
    # model — which looks like the setting working intermittently.
    assert agent.client.model == "qwen3:14b"
    assert result["applied"] is True
    assert result["restart_required"] is False


async def test_a_live_setting_with_nothing_to_apply_to_says_so(jarvis):
    """No agent configured: stored, but honest that it needs a restart."""
    result = await common.async_set_setting(jarvis, {"key": "llm.model", "value": "qwen3:14b"})

    assert result["applied"] is False
    assert result["restart_required"] is True, (
        "a setting that did not land anywhere live must not report itself as "
        "already in effect"
    )


async def test_a_restart_setting_is_honest_about_needing_one(jarvis, agent):
    result = await common.async_set_setting(jarvis, {"key": "llm.timeout", "value": 30})

    assert result["apply"] == "restart"
    assert result["restart_required"] is True


async def test_the_merged_config_updates_immediately(jarvis, agent):
    """`jarvis.config` is what everything reads; it must not lag the store."""
    await common.async_set_setting(jarvis, {"key": "jarvis.name", "value": "Workshop"})

    assert jarvis.config["jarvis"]["name"] == "Workshop"
    # And the file's own value is still remembered, for reset to fall back to.
    assert jarvis.raw_config["jarvis"]["name"] == "From File"


async def test_reset_falls_back_to_the_file_and_applies_that(jarvis, agent):
    await common.async_set_setting(jarvis, {"key": "llm.model", "value": "qwen3:14b"})
    assert agent.model == "qwen3:14b"

    result = await common.async_reset_setting(jarvis, {"key": "llm.model"})

    assert result["value"] == "file:7b"
    assert jarvis.config["llm"]["model"] == "file:7b"
    assert agent.model == "file:7b", (
        "reset dropped the override but left the old value running, so it "
        "appeared to work and changed nothing"
    )


async def test_a_bad_value_is_refused_with_the_reason(jarvis):
    with pytest.raises(ApiError) as err:
        await common.async_set_setting(jarvis, {"key": "llm.options.temperature", "value": 9})

    assert err.value.status == 400
    assert "between" in err.value.message
    assert "llm.options.temperature" not in jarvis.settings.values


async def test_an_unknown_key_is_a_404_not_a_400(jarvis):
    """A typo in the request and a bad value are different problems.

    The console shows them differently — one is a field to fix, the other is a
    bug — so they must not share a status.
    """
    for call, payload in (
        (common.async_set_setting, {"key": "llm.expose", "value": "x"}),
        (common.async_reset_setting, {"key": "llm.expose"}),
    ):
        with pytest.raises(ApiError) as err:
            await call(jarvis, payload)
        assert err.value.status == 404


async def test_a_missing_key_or_value_is_refused(jarvis):
    with pytest.raises(ApiError):
        await common.async_set_setting(jarvis, {"value": 1})
    with pytest.raises(ApiError) as err:
        # Distinct from "key is missing": omitting the value entirely must not
        # be read as setting it to null.
        await common.async_set_setting(jarvis, {"key": "jarvis.name"})
    assert "value" in err.value.message


def test_the_payload_describes_every_setting_with_its_source(jarvis):
    payload = common.settings_payload(jarvis)
    rows = {row["key"]: row for row in payload["settings"]}

    assert len(rows) == len(SETTINGS)
    assert rows["llm.model"]["value"] == "file:7b"
    assert rows["llm.model"]["source"] == "yaml"
    assert rows["jarvis.time_zone"]["source"] == "default"
    for row in rows.values():
        assert row["label"] and row["group"] and row["type"]


def test_choices_are_offered_where_a_spec_can_discover_them(jarvis):
    rows = {row["key"]: row for row in common.settings_payload(jarvis)["settings"]}
    assert rows["jarvis.unit_system"]["choices"] == ["metric", "imperial"]


def test_a_choices_hook_that_throws_does_not_break_the_page(jarvis, monkeypatch):
    """A settings screen you cannot open because Ollama is down is useless.

    It is the screen you would go to in order to fix the Ollama address.
    """
    def boom(_jarvis):
        raise RuntimeError("ollama unreachable")

    # A copy in the registry rather than a patched attribute: SettingSpec is a
    # frozen dataclass, which is the right thing for it to be.
    monkeypatch.setitem(
        SETTINGS_BY_KEY,
        "llm.model",
        dataclasses.replace(SETTINGS_BY_KEY["llm.model"], choices_hook=boom),
    )
    rows = {row["key"]: row for row in common.settings_payload(jarvis)["settings"]}
    assert "choices" not in rows["llm.model"]
    assert rows["llm.model"]["value"] == "file:7b"


async def test_a_package_owned_setting_is_reported_not_silently_lost(tmp_path):
    """An override the file wins over must say which file to edit instead."""
    box = Jarvis(tmp_path)
    await box.settings.async_set("jarvis.name", "Console Name")
    await box.async_install_config(
        {"jarvis": {"name": "Package Name"}}, {"jarvis": "house"}
    )

    rows = {row["key"]: row for row in common.settings_payload(box)["settings"]}
    row = rows["jarvis.name"]
    assert row["source"] == "unapplied"
    assert "packages/house.yaml" in row["unapplied_reason"]
    assert box.config["jarvis"]["name"] == "Package Name"


def test_every_live_setting_can_actually_be_applied_live():
    """`apply='live'` is a promise to the console; keep it checkable.

    A spec whose value is read once at setup but is labelled live would report
    `restart_required: false` for a change that needs one.
    """
    for spec in SETTINGS:
        if spec.apply != APPLY_LIVE:
            continue
        assert spec.apply_hook is not None or spec.path[0] == "jarvis", (
            f"{spec.key} claims to apply live but has no way to reach a "
            "running copy"
        )


def test_every_settings_route_is_wired_to_the_api():
    from jarvis.api import rest, websocket

    paths = {getattr(route, "path", "") for route in rest.api_router.routes}
    for verb in ("list", "set", "reset"):
        assert f"/api/config/settings/{verb}" in paths, verb
        assert f"config/settings/{verb}" in websocket.WebSocketHandler._HANDLERS, verb


# ---------------------------------------------------------------------------
# Choices: a text box for something with a knowable set of answers is a typo
# waiting to happen, and every one of these typos fails silently.
# ---------------------------------------------------------------------------
def test_every_setting_with_a_knowable_answer_offers_the_answers():
    """The rule, rather than a list of the fields that happen to have one.

    A free-text timezone fires every time trigger at the wrong hour. A free-text
    Piper voice makes the first reply a download, or a failure on a box with no
    internet. A free-text wake word stops your name working. None of the three
    says anything when it is wrong, which is exactly the class of field that has
    to be a dropdown.
    """
    by_key = {spec.key: spec for spec in SETTINGS}
    for key in (
        "jarvis.time_zone",
        "jarvis.unit_system",
        "jarvis.currency",
        "jarvis.country",
        "jarvis.language",
        "jarvis.log_level",
        "llm.model",
        "voice.language",
        "voice.tts_voice",
        "voice.wake_word",
    ):
        spec = by_key[key]
        assert spec.type == "choice", f"{key} is still a {spec.type} field"
        assert spec.choices_hook is not None, f"{key} has no way to offer choices"


def test_the_static_choice_lists_are_not_empty_and_contain_the_defaults():
    box = Jarvis(Path("/nonexistent"))
    by_key = {spec.key: spec for spec in SETTINGS}
    for key, expected in (
        ("jarvis.currency", "GBP"),
        ("jarvis.country", "GB"),
        ("jarvis.language", "en"),
        ("voice.language", "en"),
        ("jarvis.unit_system", "metric"),
        ("jarvis.log_level", "info"),
    ):
        choices = by_key[key].choices_hook(box)
        assert choices, f"{key} offers nothing"
        assert expected in choices, (
            f"{key} does not offer {expected!r}, which is what the shipped "
            "configuration.yaml sets — the console would show the current value "
            "as an unknown extra"
        )


def test_the_timezone_list_is_real_and_contains_the_shipped_default():
    box = Jarvis(Path("/nonexistent"))
    by_key = {spec.key: spec for spec in SETTINGS}
    zones = by_key["jarvis.time_zone"].choices_hook(box)
    assert len(zones) > 100, "that is not the IANA database"
    assert "Europe/London" in zones
    assert "America/New_York" in zones


def test_voice_choices_come_from_the_running_services_and_survive_their_absence():
    """The two that cannot be a static list: they depend on what is running.

    And the degradation matters as much as the happy path — a settings screen
    that will not render because Piper is restarting is one you cannot use to
    point Jarvis at a different Piper.
    """
    box = Jarvis(Path("/nonexistent"))
    by_key = {spec.key: spec for spec in SETTINGS}
    voices = by_key["voice.tts_voice"]
    words = by_key["voice.wake_word"]

    # Nothing configured at all.
    assert voices.choices_hook(box) == []
    assert words.choices_hook(box) == []

    class _Voice:
        catalogue = {
            "tts_voices": ["en_GB-alan-medium", "en_US-lessac-medium"],
            "wake_words": ["hey_jarvis", "ok_nabu"],
        }

    box.data["voice"] = _Voice()
    assert voices.choices_hook(box) == ["en_GB-alan-medium", "en_US-lessac-medium"]
    assert words.choices_hook(box) == ["hey_jarvis", "ok_nabu"]

    # A voice object that has never been probed, and one holding junk.
    class _Unprobed:
        catalogue: dict = {}

    box.data["voice"] = _Unprobed()
    assert voices.choices_hook(box) == []
    box.data["voice"] = object()
    assert voices.choices_hook(box) == []


async def test_the_catalogue_is_read_from_a_wyoming_describe():
    """Mapped off the shape the services actually answer with."""
    from jarvis.integrations.voice import VoiceData
    from jarvis.voice.pipelines import PipelineStore

    box = Jarvis(Path("/nonexistent"))
    data = VoiceData(jarvis=box, pipelines=PipelineStore())

    async def fake_info():
        return {
            "tts": {"tts": [{"name": "piper", "voices": [
                {"name": "en_GB-alan-medium"}, {"name": "en_US-lessac-medium"},
            ]}]},
            "wake": {"wake": [{"name": "openWakeWord", "models": [
                {"name": "hey_jarvis"}, {"name": "alexa"},
            ]}]},
            "stt": {"error": "connection refused"},
        }

    data.async_info = fake_info  # type: ignore[method-assign]
    catalogue = await data.async_refresh_catalogue()

    assert catalogue["tts_voices"] == ["en_GB-alan-medium", "en_US-lessac-medium"]
    assert catalogue["wake_words"] == ["alexa", "hey_jarvis"]
    # A service that is down contributes nothing rather than an error string.
    assert catalogue["stt_models"] == []
