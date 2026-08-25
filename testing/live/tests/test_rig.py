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


# --- the routing table names real tools ---------------------------------------


def test_every_tool_in_the_routing_table_exists():
    """The table is how routing accuracy is measured, so a name that no tool
    has makes the measurement quietly wrong rather than loudly broken.

    It was: the table said `write_note`/`find_note`/`read_note` while the tools
    are `note_create`/`note_append`/`note_search`, and every
    note-taking turn was scored as "skills" because reading the house style
    guide was the only thing it recognised.
    """
    from testing.live.capability import TOOL_CAPABILITY

    core = REPO_ROOT / "jarvis-core" / "jarvis"
    registered = set()
    for path in core.rglob("*.py"):
        registered.update(
            match.group(1)
            for match in __import__("re").finditer(
                r'name="([a-z_]+)"', path.read_text(encoding="utf-8")
            )
        )
    missing = sorted(name for name in TOOL_CAPABILITY if name not in registered)
    assert not missing, f"the routing table names tools that do not exist: {missing}"


# --- the ground a scenario runs on --------------------------------------------
#
# M29: the suite talks to the containers the operator runs, not to a copy of
# them. What follows pins the parts of that which can be checked without a
# Docker daemon; the rest is checked by running it.


def test_scenarios_default_to_the_running_stack():
    from testing.live.scenario import load_all

    on_stack = [s.name for s in load_all() if s.ground == "stack"]
    assert len(on_stack) > 10, on_stack


def test_only_scenarios_that_need_our_own_web_leave_the_stack():
    """A scenario asks for the fixture ground for one reason, and says so."""
    from testing.live.scenario import load_all

    # `security` joined them with M43: a red-team probe's hostile page is one
    # this repository serves, and pointing an injection probe at the open web
    # would be testing somebody else's server. The channel probes are here for
    # the same reason — the message they send is one we wrote.
    allowed = {"research", "coding", "subagents", "skills", "security"}
    off_stack = {s.name: s.capability for s in load_all() if s.ground == "fixture"}
    strays = {name: cap for name, cap in off_stack.items() if cap not in allowed}
    assert not strays, f"these do not need the fixture web: {strays}"


def test_an_unknown_ground_is_a_loud_failure(tmp_path):
    from testing.live.scenario import load_scenario

    path = tmp_path / "x.yaml"
    path.write_text(
        "name: x\ncapability: house\nground: production\n"
        "turns:\n  - say: hello\n    expect: {}\n"
    )
    with __import__("pytest").raises(ValueError, match="unknown ground"):
        load_scenario(path)


def test_the_resilience_scenarios_exist_and_act_on_containers():
    """Both halves of what M29 asks for, asserted as fixtures rather than prose."""
    from testing.live.scenario import load_all

    by_name = {s.name: s for s in load_all()}
    restart = by_name["resilience-core-restart"]
    assert any(turn.restart for turn in restart.turns)
    # And it is a mid-conversation restart, not a restart before anything was
    # said — the promise is that the thread survives, which needs a thread.
    assert not restart.turns[0].restart

    stt = by_name["resilience-stt-down"]
    killed = [turn.kill for turn in stt.turns if turn.kill]
    assert killed == ["wyoming-whisper"], killed
    # The failure must be asserted as visible. A scenario that killed a service
    # and expected nothing would pass against a Jarvis that silently hung.
    assert "error" in stt.turns[0].expect
    # And a turn afterwards, or nothing proves the service came back.
    assert len(stt.turns) > 1


def test_error_records_are_grouped_before_the_allowlist_sees_them():
    """A traceback is one event spread over twenty lines.

    Line-at-a-time matching forced the allowlist to name
    `Task exception was never retrieved` — the useless line that introduces a
    reset connection — which would have hidden every async crash there is.
    """
    from testing.live.stack import _records

    lines = [
        "wyoming-piper  | ERROR:asyncio:Task exception was never retrieved",
        "wyoming-piper  | Traceback (most recent call last):",
        'wyoming-piper  |   File "/usr/src/handler.py", line 63, in handle_event',
        "wyoming-piper  |     await self.write_event(info)",
        "wyoming-piper  | ConnectionResetError: Connection lost",
        "jarvis-core  | ERROR:jarvis:the roof is on fire",
    ]
    found = _records(lines)
    assert len(found) == 1, found
    assert "roof is on fire" in found[0]
    assert "jarvis-core" in found[0]


def test_a_plain_error_line_is_not_swallowed():
    from testing.live.stack import _records

    assert _records(["jarvis-core  | ERROR:jarvis:cannot reach the model server"])


def test_the_snapshot_covers_the_directories_the_database_lives_in():
    """A snapshot with a hole in it restores a house missing its state."""
    from testing.live.ground import STACK_PATHS

    assert "jarvis-core/config" in STACK_PATHS  # jarvis.db, notes, memory
    assert ".storage" in STACK_PATHS  # the console's password hash


def test_threads_the_suite_opens_are_namespaced():
    from testing.live.ground import TEST_NAMESPACE

    # `test:` and not a bare prefix: an operator scrolling their own thread
    # list has to be able to tell at a glance which conversations were a test
    # run, and the sweep has to know what it may delete.
    assert TEST_NAMESPACE.endswith(":")


def test_records_survive_containers_interleaving_their_output():
    """`docker compose logs` threads every container's output together.

    A grouper that treated any following line as the end of the record cut
    every traceback off before the line that names its exception, so the
    allowlist could only ever match `Task exception was never retrieved` —
    which is true of every async failure there is. Two runs of the live suite
    failed on exactly this before it was fixed.
    """
    from testing.live.stack import _records

    lines = [
        "wyoming-piper  | ERROR:asyncio:Task exception was never retrieved",
        "jarvis-core  | 2026-08-25 INFO an ordinary line arriving in between",
        "wyoming-piper  | Traceback (most recent call last):",
        "jarvis-core  | 2026-08-25 INFO and another",
        'wyoming-piper  |   File "/usr/src/handler.py", line 63, in handle_event',
        "wyoming-piper  | ConnectionResetError: Connection lost",
        "jarvis-core  | ERROR:jarvis:the roof is on fire",
    ]
    found = _records(lines)
    assert len(found) == 1, found
    assert "roof is on fire" in found[0]


def test_the_stack_ground_refuses_a_house_with_nothing_in_it():
    """A fresh Jarvis controls nothing, and that is correct.

    Running the suite against one made every house scenario fail on a missing
    entity, which reads like a broken assistant rather than an empty house. The
    rig says which file to drop in instead.
    """
    import asyncio

    from testing.live import LiveError
    from testing.live.runner import Runner

    class _Bare:
        async def command(self, name, **kwargs):
            return [{"entity_id": "sun.sun", "state": "above_horizon"}]

    runner = Runner([])
    try:
        asyncio.run(runner._house_exists(_Bare()))
    except LiveError as err:
        assert "packages-demo-house.yaml" in str(err)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("an empty house was accepted")


# --- the one browser (M31) -------------------------------------------------
def _borrower(monkeypatch, *, health="healthy", token="t0ken", docker=True):
    """A `SharedBrowser` whose container and docker are whatever the test says."""
    from testing.live import browser_service

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(browser_service, "docker_available", lambda: docker)
    monkeypatch.setattr(browser_service, "browser_token", lambda: token)
    borrower = browser_service.SharedBrowser()
    monkeypatch.setattr(borrower.stack, "health_of", lambda _s: health)
    monkeypatch.setattr(
        borrower.stack, "recreate",
        lambda service, env=None, **_k: calls.append((service, dict(env or {}))),
    )
    return borrower, calls


def test_borrowing_the_browser_exempts_only_the_fixture_hosts(monkeypatch):
    """The SSRF guard is not weakened — two loopback addresses are exempted."""
    from testing.live.browser_service import FIXTURE_HOSTS

    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch)
    assert borrower.start().endswith(":8210")
    (service, env), = calls
    assert service == "jarvis-browser"
    assert env["BROWSER_LAN_ALLOWLIST"] == ",".join(FIXTURE_HOSTS)
    assert set(FIXTURE_HOSTS) == {"127.0.0.2", "127.0.0.3"}


def test_giving_it_back_takes_the_exemption_off(monkeypatch):
    """An exemption left behind is a guard quietly weakened for good."""
    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch)
    borrower.start()
    borrower.stop()
    assert calls[-1] == ("jarvis-browser", {"BROWSER_LAN_ALLOWLIST": ""})
    # And twice is not an error: `stop()` runs in a `finally` that may already
    # have run.
    borrower.stop()
    assert len(calls) == 2


def test_it_will_not_borrow_a_browser_that_is_not_healthy(monkeypatch):
    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch, health="unhealthy")
    assert borrower.start() == ""
    assert "unhealthy" in borrower.why
    assert calls == []


def test_it_will_not_mint_itself_a_token(monkeypatch):
    """The operator's token or nothing: a suite must not issue its own key."""
    monkeypatch.delenv("LIVE_SHARED_BROWSER", raising=False)
    borrower, calls = _borrower(monkeypatch, token="")
    assert borrower.start() == ""
    assert "JARVIS_BROWSER_TOKEN" in borrower.why
    assert calls == []


def test_the_stand_in_can_be_asked_for_on_purpose(monkeypatch):
    """So that "this scenario fails without a real browser" can be proven."""
    monkeypatch.setenv("LIVE_SHARED_BROWSER", "0")
    borrower, calls = _borrower(monkeypatch)
    assert borrower.start() == ""
    assert "LIVE_SHARED_BROWSER=0" in borrower.why
    assert calls == []


def test_script_source_is_not_page_text():
    """It is not text, and in a page a model reads it is an injection surface.

    Also the difference between a browser test that means something and one
    that reads the answer out of a <script> the fetcher never ran.
    """
    from testing.live.fixture_search import _text

    html = (
        "<!doctype html><title>T</title><p>visible</p>"
        "<script>const secret = 'immersion heater 3 kW';</script>"
        "<style>.x { content: 'css'; }</style>"
    )
    text = _text(html)
    assert "visible" in text
    assert "immersion" not in text and "css" not in text
