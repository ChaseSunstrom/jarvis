"""The rig's own arithmetic, tested without talking to anything.

A test suite that measures a system is a piece of software too, and a broken
WER function or a noise generator that quietly produces silence would make
every scenario pass for the wrong reason. None of this touches the model, the
voice services or the network — those are exercised by the scenarios
themselves, which is where a failure means something about Jarvis rather than
about the rig.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.live import audio  # noqa: E402
from testing.live.judge import _parse  # noqa: E402
from testing.live.report import normalise, summarise, wer  # noqa: E402
from testing.live.report import ScenarioResult, TurnResult  # noqa: E402
from testing.live.scenario import Expectation, load_all, load_scenario  # noqa: E402


# --- word error rate ---------------------------------------------------------


def test_a_perfect_transcript_is_zero():
    assert wer("turn on the hall light", "Turn on the hall light.") == 0.0


def test_one_wrong_word_in_four_is_not_a_rounding_error():
    # Character distance would call this 0.06 and flatter a recogniser that
    # changed the meaning; word distance calls it what it is.
    assert wer("turn on the light", "turn on the lights") == pytest.approx(0.25)


def test_nothing_heard_is_a_total_loss():
    assert wer("turn on the light", "") == 1.0


def test_a_doubled_transcript_is_scored_as_the_error_it_is():
    """faster-whisper repeats itself on some short utterances. That is a real
    defect a user hears, so it must not be normalised away."""
    assert wer("turn on the lights", "turn on the lights turn on the lights") == 1.0


def test_notation_and_dialect_are_not_recognition_errors():
    # The model is en_US and writes numerals; the house is British and the
    # scenario is written in words. Both are the same recognition.
    assert wer("set it to twenty one degrees", "set it to 21 degrees") == 0.0
    assert wer("my favourite colour", "my favorite color") == 0.0
    assert normalise("Twenty One")[0] == "21"


# --- audio -------------------------------------------------------------------


def test_noise_lands_on_the_signal_to_noise_ratio_it_was_asked_for():
    speech = audio.clip(audio.room_tone(1.0, level_db=-6), 1.0)
    for want in (20.0, 10.0, 0.0):
        noisy = audio.add_noise(speech, want, shape="white")
        assert audio.snr_of(speech, noisy) == pytest.approx(want, abs=0.5)


def test_noise_is_deterministic():
    """A scenario that fails at 5 dB must fail at 5 dB tomorrow."""
    speech = audio.room_tone(0.5, level_db=-6)
    assert audio.add_noise(speech, 5.0) == audio.add_noise(speech, 5.0)


def test_silence_is_actually_silent_and_room_tone_is_not():
    assert audio.rms(audio.silence(0.5)) == 0.0
    assert audio.rms(audio.room_tone(0.5)) > 0.0


def test_room_tone_is_quiet_enough_to_be_a_room():
    # -50 dBFS by default. Loud enough that a wake detector must reject it on
    # purpose rather than by hearing nothing at all.
    assert 0.0 < audio.rms(audio.room_tone(1.0)) < 32767 * 0.02


def test_clipping_clips_rather_than_wrapping():
    """A sample that wrapped instead of clipping would be loud noise, and the
    scenario would be testing something nobody's microphone does."""
    loud = audio.clip(audio.room_tone(0.2, level_db=-6), 20.0)
    assert audio.rms(loud) > 0
    # The rails of signed 16-bit, which are not symmetrical: -32768 is a real
    # sample value and abs() of it is 32768.
    assert all(-32768 <= v <= 32767 for v in audio._samples(loud))


# --- the fixture format ------------------------------------------------------


def test_a_typo_in_an_expectation_is_an_error_not_a_silent_pass():
    """The failure this prevents: `reply_contian:` — an assertion that never
    runs and a scenario that is green for nothing."""
    with pytest.raises(ValueError, match="unknown expectation"):
        Expectation({"reply_contian": "hello"})


def test_every_shipped_scenario_loads():
    scenarios = load_all()
    assert scenarios, "no scenarios"
    for scenario in scenarios:
        assert scenario.turns, f"{scenario.name} has no turns"
        assert scenario.intent, f"{scenario.name} does not say why it exists"


def test_a_gated_scenario_names_its_milestone():
    for scenario in load_all():
        if scenario.gated:
            assert scenario.gated_on.startswith("M"), scenario.name


def test_a_scenario_with_no_turns_is_refused(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("name: empty\ncapability: house\nturns: []\n")
    with pytest.raises(ValueError, match="no turns"):
        load_scenario(path)


def test_a_turn_that_says_nothing_and_plays_nothing_is_refused(tmp_path):
    path = tmp_path / "mute.yaml"
    path.write_text("name: mute\ncapability: house\nturns:\n  - expect: {}\n")
    with pytest.raises(ValueError, match="says nothing"):
        load_scenario(path)


# --- the judge's parser ------------------------------------------------------


def test_the_judge_is_understood_however_it_answers():
    assert _parse('{"ok": true, "why": "it does"}')[0] is True
    assert _parse('```json\n{"ok": false, "why": "it dodges"}\n```')[0] is False
    assert _parse("Yes — the reply confirms it.")[0] is True
    assert _parse("No, it never says so.")[0] is False


def test_an_unparseable_verdict_is_not_a_pass():
    """A judge that mumbled must not be read as agreement."""
    assert _parse("hmm, hard to say")[0] is None


# --- the scorecard -----------------------------------------------------------


def test_a_rate_over_no_samples_is_not_a_hundred_percent():
    result = ScenarioResult(name="x", capability="house", variant="text", turns=[])
    totals = summarise([result])
    assert totals["routing_accuracy"] is None
    assert totals["wer_mean"] is None


def test_the_summary_counts_what_actually_happened():
    passed = TurnResult(scenario="a", capability="house", variant="voice", index=0,
                        said="x", ok=True, wer=0.0, latency={"total": 1.0})
    failed = TurnResult(scenario="a", capability="house", variant="voice", index=1,
                        said="y", ok=False, failures=["no"], wer=0.5,
                        latency={"total": 3.0})
    result = ScenarioResult(name="a", capability="house", variant="voice", ok=False,
                            turns=[passed, failed])
    totals = summarise([result])
    assert totals["turns"] == 2
    assert totals["turns_passed"] == 1
    assert totals["scenarios_passed"] == 0
    assert totals["round_trip_median"] == 2.0
    assert totals["wer_mean"] == 0.25
