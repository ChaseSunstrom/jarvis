"""Cross-device conversation on the desktop: message routing and presence.

Two properties carry the weight here, and both are about what happens when
things go wrong rather than when they go right:

* **Exactly one answer per ``message_id``.** The server escalates on anything
  that is not ``answered``, so a second, different answer for one id would
  send the same question to a second device — or resurrect one the user has
  already dealt with.
* **Nothing is ever silently dropped.** No display, no TTY, an unknown mode, a
  toolkit that raises — every one of them has to come back as a reported
  ``undeliverable`` so the server can try somewhere else.

Nothing in this file touches a display, a socket or a real clock.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_desktop.companion import (
    STATUS_ANSWERED,
    STATUS_DISMISSED,
    STATUS_TIMEOUT,
    STATUS_UNDELIVERABLE,
    TYPE_MESSAGE,
    TYPE_RESULT,
    AskOutcome,
    Asker,
    ChainAsker,
    CompanionHandler,
    CompanionMessage,
    Notifier,
    Speaker,
    TerminalAsker,
    UnavailableAsker,
    build_asker,
    render_question,
    result_frame,
)
from jarvis_desktop.presence import (
    ACTIVE_WITHIN_S,
    EVENT_PRESENCE,
    HEARTBEAT_S,
    MIN_INTERVAL_S,
    PresenceReporter,
    PresenceSampler,
    PresenceSignals,
    meaningful_change,
    should_send,
)


# --- doubles ----------------------------------------------------------------


class RecordingSender:
    """Stands in for the channel. Remembers every frame, can be made to fail."""

    def __init__(self, deliver: bool = True) -> None:
        self.frames: list[dict] = []
        self.deliver = deliver

    async def __call__(self, frame: dict) -> bool:
        self.frames.append(dict(frame))
        return self.deliver

    @property
    def statuses(self) -> list[str]:
        return [f.get("status") for f in self.frames]


class ScriptedAsker(Asker):
    """Answers with whatever it was handed, and counts the prompts."""

    name = "scripted"

    def __init__(self, *outcomes: AskOutcome, usable: bool = True) -> None:
        self._outcomes = list(outcomes) or [AskOutcome.answered("yes")]
        self._usable = usable
        self.prompts: list[CompanionMessage] = []

    def usable(self) -> bool:
        return self._usable

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        self.prompts.append(message)
        if len(self._outcomes) > 1:
            return self._outcomes.pop(0)
        return self._outcomes[0]


class ExplodingAsker(Asker):
    name = "exploding"

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        raise RuntimeError("the toolkit fell over")


class SlowAsker(Asker):
    """Never answers. The handler's backstop has to notice."""

    name = "slow"

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self, works: bool = True) -> None:
        self.works = works
        self.shown: list[tuple[str, str, str]] = []

    def notify(self, title: str, message: str, urgency: str = "normal") -> bool:
        self.shown.append((title, message, urgency))
        return self.works


class RecordingSpeaker(Speaker):
    name = "recording"

    def __init__(self, works: bool = True) -> None:
        self.works = works
        self.spoken: list[str] = []

    def usable(self) -> bool:
        return self.works

    async def speak(self, message: CompanionMessage) -> bool:
        self.spoken.append(message.text)
        return self.works


def message_frame(**overrides) -> dict:
    frame = {
        "type": TYPE_MESSAGE,
        "message_id": "a1b2c3",
        "kind": "ask",
        "mode": "ask",
        "text": "Deploy to production?",
        "options": ["yes", "no"],
        "conversation_id": "conv-7",
        "importance": "high",
        "timeout_s": 120,
    }
    frame.update(overrides)
    return frame


def build(**kwargs) -> tuple[CompanionHandler, RecordingSender]:
    send = RecordingSender()
    handler = CompanionHandler(
        send,
        notifier=kwargs.pop("notifier", RecordingNotifier()),
        asker=kwargs.pop("asker", ScriptedAsker(AskOutcome.answered("no"))),
        speaker=kwargs.pop("speaker", RecordingSpeaker()),
        **kwargs,
    )
    return handler, send


# --- parsing ----------------------------------------------------------------


def test_parse_reads_the_documented_frame():
    message = CompanionMessage.parse(message_frame())
    assert message is not None
    assert message.message_id == "a1b2c3"
    assert message.kind == "ask"
    assert message.mode == "ask"
    assert message.text == "Deploy to production?"
    assert message.options == ("yes", "no")
    assert message.conversation_id == "conv-7"
    assert message.importance == "high"
    assert message.timeout_s == 120
    assert message.wants_answer
    assert message.sensitive


def test_parse_ignores_unknown_fields_and_never_grows_a_policy():
    message = CompanionMessage.parse(
        message_frame(skip_confirmation=True, policy="allow", tier=1, action="send_sms")
    )
    assert message is not None
    for forbidden in ("skip_confirmation", "policy", "tier", "action", "params"):
        assert not hasattr(message, forbidden), forbidden


def test_parse_needs_a_message_id():
    assert CompanionMessage.parse(message_frame(message_id="")) is None
    assert CompanionMessage.parse(message_frame(message_id=None)) is None
    assert CompanionMessage.parse({"type": "something_else", "message_id": "x"}) is None


def test_parse_falls_back_to_kind_when_the_mode_is_garbage():
    assert CompanionMessage.parse(message_frame(mode="SHOUT", kind="say")).mode == "speak"
    assert CompanionMessage.parse(message_frame(mode="", kind="notify")).mode == "notify"
    # Neither usable: the mode stays empty and the handler reports undeliverable.
    assert CompanionMessage.parse(message_frame(mode="?", kind="?")).mode == ""


def test_parse_clamps_the_hostile_edges():
    message = CompanionMessage.parse(
        message_frame(
            text="x" * 99_999,
            options=["a"] * 3 + ["b" * 500] + [f"o{i}" for i in range(50)] + [None, {}],
            timeout_s=99_999,
            importance="ULTRA",
        )
    )
    assert message is not None
    assert len(message.text) == 4000
    assert len(message.options) <= 8
    assert all(len(o) <= 80 for o in message.options)
    assert message.options[0] == "a" and message.options.count("a") == 1
    assert message.timeout_s == 600.0
    assert message.importance == "normal"


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 120.0), ("abc", 120.0), (0, 120.0), (-5, 120.0), (1, 5.0), (300, 300.0)],
)
def test_parse_clamps_the_timeout(raw, expected):
    message = CompanionMessage.parse(message_frame(timeout_s=raw))
    assert message is not None
    assert message.timeout_s == expected


def test_result_frame_refuses_to_invent_a_status():
    assert result_frame("x", "banana")["status"] == STATUS_UNDELIVERABLE
    assert result_frame("x", STATUS_ANSWERED, "yes") == {
        "type": TYPE_RESULT,
        "message_id": "x",
        "status": STATUS_ANSWERED,
        "answer": "yes",
    }
    # Only `answered` carries an answer; the others must not smuggle one.
    assert "answer" not in result_frame("x", STATUS_DISMISSED, "yes")


# --- routing per mode -------------------------------------------------------


async def test_ask_answered_reports_the_answer_once():
    handler, send = build(asker=ScriptedAsker(AskOutcome.answered("no")))
    frame = await handler.handle(message_frame())
    assert frame == {
        "type": TYPE_RESULT,
        "message_id": "a1b2c3",
        "status": STATUS_ANSWERED,
        "answer": "no",
    }
    assert send.statuses == [STATUS_ANSWERED]


async def test_ask_dismissed_reports_dismissed_so_the_server_escalates():
    handler, send = build(asker=ScriptedAsker(AskOutcome.dismissed()))
    frame = await handler.handle(message_frame())
    assert frame["status"] == STATUS_DISMISSED
    assert "answer" not in frame


async def test_ask_timeout_reports_timeout():
    handler, send = build(asker=ScriptedAsker(AskOutcome.timed_out()))
    assert (await handler.handle(message_frame()))["status"] == STATUS_TIMEOUT


async def test_ask_with_no_backend_is_undeliverable_not_a_crash():
    handler, send = build(asker=ChainAsker(UnavailableAsker()))
    frame = await handler.handle(message_frame())
    assert frame["status"] == STATUS_UNDELIVERABLE
    assert send.statuses == [STATUS_UNDELIVERABLE]


async def test_a_backend_that_raises_is_undeliverable_not_a_crash():
    handler, send = build(asker=ChainAsker(ExplodingAsker(), UnavailableAsker()))
    assert (await handler.handle(message_frame()))["status"] == STATUS_UNDELIVERABLE


async def test_the_chain_falls_through_to_the_next_usable_backend():
    unusable = ScriptedAsker(AskOutcome.answered("wrong"), usable=False)
    usable = ScriptedAsker(AskOutcome.answered("right"))
    handler, _ = build(asker=ChainAsker(unusable, usable, UnavailableAsker()))
    assert (await handler.handle(message_frame()))["answer"] == "right"
    assert unusable.prompts == []
    assert len(usable.prompts) == 1


async def test_a_backend_that_never_answers_times_out_at_the_backstop():
    """A backend whose own countdown never fires must not hang the message."""
    send = RecordingSender()
    handler = CompanionHandler(
        send,
        notifier=RecordingNotifier(),
        asker=SlowAsker(),
        speaker=RecordingSpeaker(),
        backstop_grace_s=0.02,
    )
    message = CompanionMessage(
        message_id="a1b2c3", kind="ask", mode="ask", text="Well?", timeout_s=0.02
    )
    frame = await handler.deliver(message)
    assert frame["status"] == STATUS_TIMEOUT
    assert send.statuses == [STATUS_TIMEOUT]


async def test_notify_mode_uses_the_notifier():
    notifier = RecordingNotifier()
    handler, send = build(notifier=notifier)
    frame = await handler.handle(
        message_frame(kind="notify", mode="notify", text="Backup finished.", options=[])
    )
    assert notifier.shown == [("Jarvis", "Backup finished.", "normal")]
    assert frame["status"] == STATUS_ANSWERED
    assert send.statuses == [STATUS_ANSWERED]


async def test_notify_that_cannot_be_shown_is_undeliverable():
    handler, send = build(notifier=RecordingNotifier(works=False))
    frame = await handler.handle(message_frame(kind="notify", mode="notify"))
    assert frame["status"] == STATUS_UNDELIVERABLE


async def test_critical_importance_raises_the_urgency():
    notifier = RecordingNotifier()
    handler, _ = build(notifier=notifier)
    await handler.handle(message_frame(mode="notify", importance="critical"))
    assert notifier.shown[0][2] == "critical"


async def test_speak_mode_speaks_when_there_is_audio():
    speaker = RecordingSpeaker(works=True)
    notifier = RecordingNotifier()
    handler, _ = build(speaker=speaker, notifier=notifier)
    frame = await handler.handle(
        message_frame(kind="say", mode="speak", text="The build failed.", options=[])
    )
    assert speaker.spoken == ["The build failed."]
    assert notifier.shown == []
    assert frame["status"] == STATUS_ANSWERED


async def test_speak_downgrades_to_a_notification_with_no_audio():
    speaker = RecordingSpeaker(works=False)
    notifier = RecordingNotifier()
    handler, _ = build(speaker=speaker, notifier=notifier)
    frame = await handler.handle(message_frame(kind="say", mode="speak", text="Hello."))
    assert notifier.shown == [("Jarvis", "Hello.", "normal")]
    assert frame["status"] == STATUS_ANSWERED


async def test_speak_with_no_audio_and_no_notifier_is_undeliverable():
    handler, _ = build(
        speaker=RecordingSpeaker(works=False), notifier=RecordingNotifier(works=False)
    )
    frame = await handler.handle(message_frame(kind="say", mode="speak"))
    assert frame["status"] == STATUS_UNDELIVERABLE


async def test_an_unknown_mode_is_undeliverable():
    handler, send = build()
    frame = await handler.handle(message_frame(mode="teleport", kind="teleport"))
    assert frame["status"] == STATUS_UNDELIVERABLE
    assert send.statuses == [STATUS_UNDELIVERABLE]


async def test_an_empty_question_is_undeliverable():
    asker = ScriptedAsker(AskOutcome.answered("yes"))
    handler, _ = build(asker=asker)
    frame = await handler.handle(message_frame(text="   "))
    assert frame["status"] == STATUS_UNDELIVERABLE
    assert asker.prompts == []


async def test_a_frame_with_no_message_id_answers_nothing():
    handler, send = build()
    assert await handler.handle(message_frame(message_id="")) is None
    assert send.frames == []


# --- exactly once -----------------------------------------------------------


async def test_a_redelivery_replays_the_same_answer_and_never_reprompts():
    asker = ScriptedAsker(AskOutcome.answered("no"))
    handler, send = build(asker=asker)

    first = await handler.handle(message_frame())
    second = await handler.handle(message_frame())

    assert first == second
    assert len(asker.prompts) == 1, "a redelivery must not ask the human again"
    assert send.statuses == [STATUS_ANSWERED, STATUS_ANSWERED]
    assert {tuple(sorted(f.items())) for f in send.frames} == {
        tuple(sorted(first.items()))
    }, "every reply for one id must be byte-identical"


async def test_two_concurrent_deliveries_produce_one_prompt_and_one_answer():
    started = asyncio.Event()
    release = asyncio.Event()

    class GatedAsker(Asker):
        name = "gated"

        def __init__(self) -> None:
            self.prompts = 0

        async def ask(self, message: CompanionMessage) -> AskOutcome:
            self.prompts += 1
            started.set()
            await release.wait()
            return AskOutcome.answered("yes")

    asker = GatedAsker()
    handler, send = build(asker=asker)

    first = asyncio.create_task(handler.handle(message_frame()))
    await started.wait()
    second = await handler.handle(message_frame())
    release.set()
    resolved = await first

    assert asker.prompts == 1
    assert second is None, "a delivery that arrives mid-prompt must not answer"
    assert resolved["status"] == STATUS_ANSWERED
    assert send.statuses == [STATUS_ANSWERED]


async def test_different_ids_are_answered_independently():
    handler, send = build(
        asker=ScriptedAsker(AskOutcome.answered("one"), AskOutcome.answered("two"))
    )
    await handler.handle(message_frame(message_id="m1"))
    await handler.handle(message_frame(message_id="m2"))
    assert [f["message_id"] for f in send.frames] == ["m1", "m2"]
    assert [f["answer"] for f in send.frames] == ["one", "two"]


async def test_an_unknown_id_reports_undeliverable_exactly_once():
    handler, send = build()
    first = await handler.report_unknown("ghost")
    assert first is not None and first["status"] == STATUS_UNDELIVERABLE
    await handler.report_unknown("ghost")
    assert send.statuses == [STATUS_UNDELIVERABLE], "one reply per id, always"


async def test_report_unknown_does_not_override_a_real_answer():
    handler, send = build(asker=ScriptedAsker(AskOutcome.answered("no")))
    await handler.handle(message_frame())
    await handler.report_unknown("a1b2c3")
    assert send.statuses == [STATUS_ANSWERED]


async def test_report_unknown_ignores_an_empty_id():
    handler, send = build()
    assert await handler.report_unknown("  ") is None
    assert send.frames == []


async def test_the_ledger_is_bounded():
    handler, send = build(asker=ScriptedAsker(AskOutcome.answered("ok")), max_remembered=4)
    for index in range(10):
        await handler.handle(message_frame(message_id=f"m{index}"))
    assert handler.settled_count == 4
    assert handler.status_of("m9") == STATUS_ANSWERED
    assert handler.status_of("m0") is None


async def test_a_failed_send_still_settles_the_id():
    """The socket dying must not turn into a second, different answer later."""
    send = RecordingSender(deliver=False)
    handler = CompanionHandler(
        send,
        notifier=RecordingNotifier(),
        asker=ScriptedAsker(AskOutcome.answered("no")),
        speaker=RecordingSpeaker(),
    )
    await handler.handle(message_frame())
    await handler.handle(message_frame())
    assert send.statuses == [STATUS_ANSWERED, STATUS_ANSWERED]
    assert send.frames[0] == send.frames[1]


async def test_cancelling_a_question_leaves_the_id_answerable_again():
    started = asyncio.Event()

    class HangingAsker(Asker):
        name = "hanging"

        def __init__(self) -> None:
            self.prompts = 0

        async def ask(self, message: CompanionMessage) -> AskOutcome:
            self.prompts += 1
            started.set()
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    asker = HangingAsker()
    handler, send = build(asker=asker)
    task = asyncio.create_task(handler.handle(message_frame()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert send.frames == [], "a cancelled question must not report a decision"
    assert handler.in_flight == 0

    handler.asker = ScriptedAsker(AskOutcome.answered("late"))
    frame = await handler.handle(message_frame())
    assert frame["status"] == STATUS_ANSWERED


async def test_answering_marks_a_local_interaction():
    seen: list[int] = []
    handler, _ = build(
        asker=ScriptedAsker(AskOutcome.answered("yes")),
        on_interaction=lambda: seen.append(1),
    )
    await handler.handle(message_frame())
    assert seen == [1]

    handler2, _ = build(
        asker=ScriptedAsker(AskOutcome.dismissed()),
        on_interaction=lambda: seen.append(2),
    )
    await handler2.handle(message_frame())
    assert seen == [1], "a dismissal is not the user being present"


async def test_handle_background_answers_without_blocking_the_caller():
    handler, send = build(asker=ScriptedAsker(AskOutcome.answered("yes")))
    handler.handle_background(message_frame())
    assert send.frames == []
    for _ in range(20):
        await asyncio.sleep(0)
        if send.frames:
            break
    assert send.statuses == [STATUS_ANSWERED]
    await handler.close()


# --- the terminal backend ---------------------------------------------------


class FakeTty:
    def __init__(self, line: str, tty: bool = True) -> None:
        self._line = line
        self._tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        return self._line

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        pass


async def test_terminal_backend_matches_an_option_by_name_and_by_number():
    message = CompanionMessage.parse(message_frame(timeout_s=5))
    assert message is not None

    for typed, expected in (("no\n", "no"), ("2\n", "no"), ("YES\n", "yes")):
        asker = TerminalAsker(stream=FakeTty(typed), out=FakeTty("", tty=False))
        outcome = await asker.ask(message)
        assert outcome == AskOutcome.answered(expected), typed


async def test_terminal_backend_treats_an_empty_line_as_a_dismissal():
    message = CompanionMessage.parse(message_frame(timeout_s=5))
    asker = TerminalAsker(stream=FakeTty("\n"), out=FakeTty("", tty=False))
    assert (await asker.ask(message)).status == STATUS_DISMISSED


async def test_terminal_backend_is_unusable_without_a_tty():
    assert not TerminalAsker(stream=FakeTty("y\n", tty=False)).usable()


def test_the_rendered_question_shows_the_options_and_the_clock():
    message = CompanionMessage.parse(message_frame(timeout_s=30))
    text = render_question(message)
    assert "Deploy to production?" in text
    assert "1) yes" in text and "2) no" in text
    assert "30s" in text


def test_build_asker_headless_reaches_nobody_and_says_so():
    asker = build_asker(headless=True)
    assert asker.unattended
    assert build_asker().name == "chain"


# --- presence: change detection --------------------------------------------


def base_signals(**overrides) -> PresenceSignals:
    values = dict(
        screen_on=True,
        locked=False,
        last_interaction=1_000_000.0,
        idle_s=5.0,
        audio_available=True,
        muted=False,
        battery=80,
        charging=False,
    )
    values.update(overrides)
    return PresenceSignals(**values)


def test_the_first_report_always_goes_out():
    assert should_send(None, base_signals(), 0.0) == "first report"


def test_idle_creeping_upward_is_not_a_change():
    before = base_signals(idle_s=5.0, last_interaction=1_000_000.0)
    after = base_signals(idle_s=41.0, last_interaction=1_000_000.0)
    assert meaningful_change(before, after) is None
    assert should_send(before, after, 30.0) is None


def test_going_idle_past_the_active_threshold_is_a_change():
    before = base_signals(idle_s=10.0)
    after = base_signals(idle_s=ACTIVE_WITHIN_S + 1)
    assert meaningful_change(before, after) == "active"
    assert should_send(before, after, 30.0) == "active"


def test_coming_back_is_a_change():
    before = base_signals(idle_s=900.0)
    after = base_signals(idle_s=1.0)
    assert should_send(before, after, 30.0) == "active"


@pytest.mark.parametrize(
    "field,value",
    [
        ("locked", True),
        ("screen_on", False),
        ("audio_available", False),
        ("muted", True),
        ("driving", True),
        ("charging", True),
        ("zone", "home"),
    ],
)
def test_every_tracked_flag_is_worth_a_frame(field, value):
    before = base_signals()
    after = base_signals(**{field: value})
    assert meaningful_change(before, after) == field


def test_battery_needs_a_real_step():
    before = base_signals(battery=80)
    assert meaningful_change(before, base_signals(battery=78)) is None
    assert meaningful_change(before, base_signals(battery=74)) == "battery"
    # Learning a battery level for the first time is worth saying.
    assert meaningful_change(base_signals(battery=None), base_signals(battery=74)) == "battery"


def test_an_unknown_idle_time_does_not_flap_the_active_edge():
    unknown = base_signals(idle_s=None)
    assert unknown.active is None
    assert meaningful_change(unknown, base_signals(idle_s=None)) is None


def test_the_heartbeat_fires_with_nothing_changed():
    signals = base_signals()
    assert should_send(signals, signals, HEARTBEAT_S - 0.1) is None
    assert should_send(signals, signals, HEARTBEAT_S) == "heartbeat"


def test_the_rate_floor_suppresses_even_a_real_change():
    before = base_signals()
    after = base_signals(locked=True)
    assert should_send(before, after, MIN_INTERVAL_S - 0.1) is None
    assert should_send(before, after, MIN_INTERVAL_S) == "locked"


def test_the_event_payload_uses_the_servers_field_names():
    data = base_signals(zone="home", battery=42, charging=True).as_event()
    assert data["screen_on"] is True
    assert data["locked"] is False
    assert data["audio_available"] is True
    assert data["muted"] is False
    assert data["driving"] is False
    assert data["battery"] == 42
    assert data["charging"] is True
    assert data["zone"] == "home"
    assert data["last_interaction"] == 1_000_000.0


def test_unknown_fields_are_omitted_rather_than_sent_as_null():
    data = PresenceSignals().as_event()
    for absent in ("battery", "charging", "zone", "idle_s", "last_interaction"):
        assert absent not in data, absent


def test_the_payload_matches_what_the_server_understands():
    """The keys have to be the ones ``DevicePresence.update`` recognises."""
    known = {
        "screen_on", "locked", "last_interaction", "audio_available", "driving",
        "zone", "battery", "charging", "muted", "jarvis_foreground", "last_seen",
        "connected", "idle_s",
    }
    assert set(base_signals(zone="home").as_event()) <= known


# --- presence: sampling and the reporter ------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def sampler_for(state: dict) -> PresenceSampler:
    return PresenceSampler(
        idle_probe=lambda: state.get("idle"),
        lock_probe=lambda: state.get("locked"),
        display_probe=lambda: state.get("display", True),
        audio_probe=lambda: state.get("audio", True),
        mute_probe=lambda: state.get("muted", False),
        battery_probe=lambda: (state.get("battery"), state.get("charging")),
        wall=lambda: state.get("wall", 1_000_000.0),
    )


def test_the_sampler_turns_idle_seconds_into_a_wall_clock_interaction():
    state = {"idle": 30.0, "wall": 1_000_000.0, "locked": False}
    signals = sampler_for(state).sample()
    assert signals.idle_s == 30.0
    assert signals.last_interaction == 999_970.0
    assert signals.active is True


def test_an_unknown_lock_state_reports_unlocked_and_is_not_probed_again():
    calls = []

    def lock_probe():
        calls.append(1)
        return None

    sampler = PresenceSampler(
        idle_probe=lambda: 1.0,
        lock_probe=lock_probe,
        display_probe=lambda: True,
        audio_probe=lambda: True,
        mute_probe=lambda: None,
        battery_probe=lambda: (None, None),
    )
    assert sampler.sample().locked is False
    sampler.sample()
    sampler.sample()
    assert len(calls) == 1, "a probe this machine cannot answer is asked once"


def test_a_probe_that_raises_does_not_take_the_sample_down():
    def boom():
        raise OSError("no such tool")

    sampler = PresenceSampler(
        idle_probe=boom,
        lock_probe=boom,
        display_probe=boom,
        audio_probe=boom,
        mute_probe=boom,
        battery_probe=boom,
    )
    signals = sampler.sample()
    assert signals.idle_s is None
    assert signals.locked is False
    assert signals.screen_on is False


def test_a_noted_interaction_beats_a_probe_that_cannot_see_it():
    """A tkinter dialog is not keyboard input, but it is very much presence."""
    state = {"idle": 900.0, "wall": 1_000_000.0}
    sampler = sampler_for(state)
    sampler.note_interaction(999_999.0)
    signals = sampler.sample()
    assert signals.last_interaction == 999_999.0
    assert signals.idle_s == 1.0
    assert signals.active is True


def test_an_explicit_mute_override_beats_the_system():
    state = {"idle": 1.0, "muted": False}
    sampler = sampler_for(state)
    assert sampler.sample().muted is False
    sampler.muted_override = True
    assert sampler.sample().muted is True
    sampler.muted_override = None
    assert sampler.sample().muted is False


class RecordingEmitter:
    def __init__(self, deliver: bool = True) -> None:
        self.events: list[tuple[str, dict]] = []
        self.deliver = deliver

    async def __call__(self, event: str, data: dict) -> bool:
        self.events.append((event, dict(data)))
        return self.deliver


async def test_the_reporter_sends_once_then_stays_quiet():
    state = {"idle": 5.0, "locked": False}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)

    assert await reporter.poll() == "first report"
    assert emit.events[0][0] == EVENT_PRESENCE

    clock.advance(5.0)
    state["idle"] = 10.0
    assert await reporter.poll() is None
    assert reporter.sends == 1


async def test_the_reporter_sends_on_a_real_change():
    state = {"idle": 5.0, "locked": False}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()

    clock.advance(5.0)
    state["locked"] = True
    assert await reporter.poll() == "locked"
    assert emit.events[-1][1]["locked"] is True
    assert reporter.sends == 2


async def test_the_reporter_honours_the_heartbeat():
    state = {"idle": 5.0, "locked": False}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()

    clock.advance(HEARTBEAT_S - 1)
    assert await reporter.poll() is None
    clock.advance(1.0)
    assert await reporter.poll() == "heartbeat"
    assert reporter.sends == 2


async def test_a_burst_of_changes_is_not_a_firehose():
    state = {"idle": 5.0, "locked": False}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()

    for index in range(20):
        clock.advance(0.05)
        state["locked"] = index % 2 == 0
        await reporter.poll()
    assert reporter.sends == 1, "the rate floor has to hold under a flapping signal"


async def test_a_change_suppressed_by_the_floor_still_goes_out_afterwards():
    state = {"idle": 5.0, "locked": False}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()

    clock.advance(0.5)
    state["locked"] = True
    assert await reporter.poll() is None

    clock.advance(5.0)
    assert await reporter.poll() == "locked"
    assert emit.events[-1][1]["locked"] is True


async def test_a_dropped_frame_is_retried_rather_than_recorded_as_sent():
    state = {"idle": 5.0, "locked": False}
    emit = RecordingEmitter(deliver=False)
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)

    assert await reporter.poll() is None
    assert reporter.last_sent is None
    assert reporter.sends == 0

    emit.deliver = True
    assert await reporter.poll() == "first report"
    assert reporter.sends == 1


async def test_an_emit_that_raises_does_not_break_the_loop():
    async def boom(event, data):
        raise RuntimeError("socket died")

    reporter = PresenceReporter(boom, sampler_for({"idle": 5.0}), clock=FakeClock())
    assert await reporter.poll() is None
    assert reporter.sends == 0


async def test_force_sends_regardless_of_the_throttle():
    state = {"idle": 5.0}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()
    assert await reporter.poll(force=True) == "forced"
    assert reporter.sends == 2


async def test_set_muted_is_reported_on_the_next_poll():
    state = {"idle": 5.0, "muted": False}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()

    clock.advance(5.0)
    reporter.set_muted(True)
    assert await reporter.poll() == "muted"
    assert emit.events[-1][1]["muted"] is True


async def test_the_run_loop_stops_when_asked():
    emit = RecordingEmitter()
    reporter = PresenceReporter(
        emit, sampler_for({"idle": 5.0}), poll_interval_s=1.0, clock=FakeClock()
    )
    stop = asyncio.Event()
    task = asyncio.create_task(reporter.run(stop))
    for _ in range(50):
        await asyncio.sleep(0)
        if reporter.sends:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert reporter.sends == 1


async def test_start_and_stop_are_idempotent():
    reporter = PresenceReporter(RecordingEmitter(), sampler_for({"idle": 5.0}))
    await reporter.start()
    await reporter.start()
    await reporter.stop()
    await reporter.stop()


def test_describe_says_something_useful_before_and_after():
    reporter = PresenceReporter(RecordingEmitter(), sampler_for({"idle": 5.0}))
    assert "nothing reported yet" in reporter.describe()
    reporter.last_sent = base_signals(locked=True, muted=True)
    text = reporter.describe()
    assert "locked" in text and "muted" in text


# --- the two halves together ------------------------------------------------


async def test_answering_a_question_feeds_the_presence_reporter():
    state = {"idle": 900.0, "wall": 1_000_000.0}
    emit = RecordingEmitter()
    clock = FakeClock()
    reporter = PresenceReporter(emit, sampler_for(state), clock=clock)
    await reporter.poll()
    assert emit.events[-1][1]["idle_s"] == 900.0

    handler = CompanionHandler(
        RecordingSender(),
        notifier=RecordingNotifier(),
        asker=ScriptedAsker(AskOutcome.answered("yes")),
        speaker=RecordingSpeaker(),
        on_interaction=reporter.note_interaction,
    )
    await handler.handle(message_frame())

    clock.advance(5.0)
    assert await reporter.poll() == "active"
    assert emit.events[-1][1]["idle_s"] == 0.0


def test_this_module_cannot_reach_the_action_layer():
    """Structural, not stylistic: a proactive message must not be able to run
    anything, and the cheapest way to guarantee that is to have no import."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "jarvis_desktop" / "companion.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in (
        "from .actions.registry",
        "from .policy import",
        "ActionRegistry",
        "handle_command",
        "dispatch(",
    ):
        assert forbidden not in text, f"companion.py must not reach {forbidden}"
