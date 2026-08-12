#!/usr/bin/env python3
"""Executable spec for when Jarvis decides you started and stopped talking.

The reported symptom, twice, in opposite directions:

  * at thresholds of 0.02 / 0.01 — "I feel like I have to be right next to the
    mic/screaming for it to detect";
  * after lowering them ten times, to 0.002 / 0.001 — "I was talking to it and
    it wasnt really able to hear me".

Both complaints are real and both are consequences of the same mistake: a fixed
pair of numbers cannot describe "louder than this room". MicStreamer reports a
0..1 normalised RMS, so 0.002 is -54 dBFS and 0.001 is -60 dBFS — below the
noise floor of an ordinary room with a fan or a laptop in it.

What that costs is not obvious from the numbers, which is why it survived:

  * The hangover is the only VAD path that ends a turn, and it only runs while
    the level is BELOW the end edge. With the floor above that edge it never
    runs, so every turn lasted the full 30-second cap.
  * Thirty seconds is the worst possible length to hand a Whisper backend: it
    exactly fills the model's window with ~28 seconds of room noise, which
    produces empty text or invention. "The STT doesn't work that well."
  * And the start edge latched on the room itself, which disarmed the
    inactivity timeout — deleting the diagnostic that tells a dead microphone
    from a quiet one.

So the room is the reference. The floor tracks the quietest the room has
recently been and both edges are multiples of it, which gives a silent study
and a kitchen with an extractor fan the same ratio of speech to background.

Run:  python3 android-app/tools/voice_activity_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

KOTLIN = "app/src/main/kotlin/ai/jarvis/app/assist/VoiceActivity.kt"

START_RATIO = 4.0
END_RATIO = 2.0
MIN_START = 0.004
MIN_END = 0.002
FLOOR_RISE_PER_CHUNK = 0.0001
START_DEBOUNCE_MS = 200
MIN_SPEECH_MS = 300
END_SILENCE_MS = 900
SUSTAIN_RATIO = 0.40
SEED_WINDOW_MS = 1500

#: MicStreamer emits one buffer every 64 ms.
CHUNK_MS = 64

QUIET, STARTED, SPEAKING, ENDED = "QUIET", "STARTED", "SPEAKING", "ENDED"


class VoiceActivity:
    """Mirrors VoiceActivity.kt."""

    def __init__(self, speech_already_underway: bool = False):
        # See VoiceActivity.seeded. A wake word means the user IS mid-sentence,
        # so the first buffer is speech and must not be taken for the room.
        self.seeded = not speech_already_underway
        self.floor = 0.0
        self.peak = 0.0
        self.speaking = False
        self.above_since = 0
        self.candidate_peak = 0.0
        self.last_voice_at = 0
        self.started_at = 0
        self.quietest = float("inf")
        self.first_unseeded_at = 0

    @property
    def start_edge(self) -> float:
        return max(MIN_START, self.floor * START_RATIO)

    @property
    def end_edge(self) -> float:
        return max(MIN_END, self.floor * END_RATIO)

    def on_level(self, now_ms: int, level: float) -> str:
        if level > self.peak:
            self.peak = level

        edge = self.start_edge
        if not self.seeded:
            # Waiting for a buffer that could plausibly BE the room. Until one
            # arrives the edges sit at their absolute minimums, which is what
            # lets a command already in progress latch at all.
            if self.first_unseeded_at == 0 or now_ms < self.first_unseeded_at:
                self.first_unseeded_at = now_ms
            if level < self.quietest:
                self.quietest = level

            if level <= MIN_START:
                # A genuinely silent buffer. That IS the room.
                self.floor = level
                self.seeded = True
            elif now_ms - self.first_unseeded_at >= SEED_WINDOW_MS:
                # Rule 1 needs a room at or below 0.004, which is a recording
                # studio. Above it — which is most rooms — a wake-word turn used
                # to stay unseeded forever and the end edge could only arrive by
                # the slow climb below: seconds, before the 900 ms hangover even
                # started. So infer the room from the QUIETEST buffer heard.
                #
                # Not capped at a fraction of the loudest, which was the first
                # attempt: that cap fires just as hard on a window made entirely
                # of room as on one made entirely of speech, seeding the floor
                # BELOW the room and leaving the end edge under it — the
                # never-ends failure this file's noisy-room checks exist for.
                # Level cannot separate steady room from steady speech; the fact
                # that speech is not steady can. Its quietest buffer is a
                # syllable boundary, near the room. A room's quietest IS the
                # room.
                self.floor = self.quietest
                self.seeded = True
            else:
                # The backstop for the first second and a half, before there is
                # enough of a sample to seed from. Runs while speech is latched
                # too, unlike the ordinary case.
                self.floor = min(self.floor + FLOOR_RISE_PER_CHUNK, level)
        elif self.floor <= 0.0:
            self.floor = level
        elif level <= edge:
            self.floor = level if level < self.floor else min(self.floor + FLOOR_RISE_PER_CHUNK, level)

        if level > edge:
            if self.above_since == 0:
                self.above_since = now_ms
                self.candidate_peak = level
            if level > self.candidate_peak:
                self.candidate_peak = level
            if now_ms < self.above_since:
                self.above_since = now_ms
            if not self.speaking and now_ms - self.above_since >= START_DEBOUNCE_MS:
                # Sustained, or merely loud once? A transient decays to a small
                # fraction of its own peak over the window; speech does not.
                if level >= self.candidate_peak * SUSTAIN_RATIO:
                    self.speaking = True
                    self.started_at = now_ms
                    self.last_voice_at = now_ms
                    return STARTED
                self.above_since = now_ms
                self.candidate_peak = level
            if self.speaking:
                self.last_voice_at = now_ms
                return SPEAKING
            return QUIET

        self.above_since = 0
        self.candidate_peak = 0.0
        if not self.speaking:
            return QUIET

        if level >= self.end_edge:
            self.last_voice_at = now_ms
            return SPEAKING
        if now_ms < self.last_voice_at:
            self.last_voice_at = now_ms
            return SPEAKING
        if now_ms - self.started_at > MIN_SPEECH_MS and now_ms - self.last_voice_at > END_SILENCE_MS:
            self.speaking = False
            self.above_since = 0
            return ENDED
        return SPEAKING

    def new_turn(self):
        self.speaking = False
        self.above_since = 0
        self.candidate_peak = 0.0
        self.last_voice_at = 0
        self.started_at = 0


def run(levels: list[float], start: int = 0) -> tuple[VoiceActivity, list[str]]:
    vad = VoiceActivity()
    out = []
    for i, lvl in enumerate(levels):
        out.append(vad.on_level(start + i * CHUNK_MS, lvl))
    return vad, out


def steady(seconds: float, level: float) -> list[float]:
    return [level] * int(seconds * 1000 / CHUNK_MS)


# --- the two field reports, as tests ---------------------------------------


def check_a_quiet_room_hears_ordinary_speech() -> int:
    """The FIRST complaint: having to shout, or sit on top of the phone.

    Conversational speech through an unprocessed phone mic at arm's length
    smooths to roughly 0.005-0.02. The old start edge was 0.02 — the very top
    of that range.
    """
    failures = 0
    for speech in (0.006, 0.010, 0.020):
        _, verdicts = run(steady(2, 0.0008) + steady(2, speech))
        if STARTED not in verdicts:
            print(f"FAIL  speech at {speech} in a quiet room was never heard")
            failures += 1
    return failures


def ramp(seconds: float, target: float) -> list[float]:
    """MicStreamer's one-pole smoother, started from zero.

    A step would hide the bug this models: the first buffer would be small
    enough to be a plausible room. The real first buffer is 0.3 of the level,
    which is large enough to poison the floor and small enough to sit under the
    edge that poisoning creates.
    """
    out, level = [], 0.0
    for _ in range(int(seconds * 1000 / CHUNK_MS)):
        level += (target - level) * 0.3
        out.append(level)
    return out


def check_a_wake_word_turn_hears_speech_already_in_progress() -> int:
    """The THIRD field report: "it wasn't really able to hear me".

    The microphone opens while the user is ALREADY talking — "Hey Jarvis, turn
    the kitchen lights off" is one breath, and capture starts inside it. Taking
    that first buffer for the room put the floor at speech level and the start
    edge at FOUR TIMES speech level, so nothing said afterwards could cross it.
    The turn then ran to its inactivity timeout and blamed the microphone.

    `SyntheticSpeech` emits from sample zero, so `ConversationE2ETest` had the
    same fault in front of it.
    """
    failures = 0
    for speech in (0.02, 0.05, 0.085, 0.2):
        vad = VoiceActivity(speech_already_underway=True)
        verdicts = [
            vad.on_level(i * CHUNK_MS, lvl) for i, lvl in enumerate(ramp(2, speech))
        ]
        if STARTED not in verdicts:
            print(
                f"FAIL  a wake-word turn never heard speech at {speech} that was "
                "already under way when the microphone opened"
            )
            failures += 1
    return failures


def check_a_wake_word_turn_still_ends() -> int:
    """And it has to STOP, which is the half that makes the rest safe.

    A turn that latched on the absolute minimums must still end when the talking
    does — otherwise this trades "cannot hear you" for "records you for twelve
    seconds", which is the failure the ratios were introduced to prevent.
    """
    failures = 0
    for speech in (0.02, 0.085):
        vad = VoiceActivity(speech_already_underway=True)
        levels = ramp(1.5, speech) + steady(3, 0.0008)
        verdicts = [vad.on_level(i * CHUNK_MS, lvl) for i, lvl in enumerate(levels)]
        if STARTED not in verdicts:
            print(f"FAIL  speech at {speech} was not heard at all")
            failures += 1
        elif ENDED not in verdicts:
            print(f"FAIL  a wake-word turn at {speech} never ended when the talking did")
            failures += 1
    return failures


def check_a_wake_word_turn_ends_promptly_after_the_talking() -> int:
    """The user-facing complaint, as a number.

    Reported as *"I say something, and it takes a while for it to stop
    listening and hear what I said."* Ending eventually is not enough — the
    whole turn is dead air to the person waiting for an answer, and the
    recogniser gets seconds of room tacked onto the command.

    Before the seed window, a wake-word turn in an ordinary room could only get
    its end edge from the slow floor climb — 1.9 s at 0.006, 3.8 s at 0.012,
    6.4 s at 0.02 — and the 900 ms hangover began only after that. Now the room
    is inferred at SEED_WINDOW_MS and the hangover runs from the moment the
    talking stops.

    The budget below is the hangover plus the seed window plus a little slack,
    which is the honest floor: a turn cannot end sooner than it takes to be
    sure the silence is silence.
    """
    budget_ms = SEED_WINDOW_MS + END_SILENCE_MS + 400
    failures = 0
    for room in (0.006, 0.012, 0.02):
        for speech in (0.05, 0.12):
            vad = VoiceActivity(speech_already_underway=True)
            # Mid-sentence when the mic opens, then the room, as it really goes.
            talking = ramp(1.2, speech)
            after = steady(6, room)
            levels = talking + after
            stopped_at = len(talking) * CHUNK_MS
            ended_at = None
            for i, lvl in enumerate(levels):
                if vad.on_level(i * CHUNK_MS, lvl) == ENDED:
                    ended_at = i * CHUNK_MS
                    break
            if ended_at is None:
                print(
                    f"FAIL  room {room}, speech {speech}: the turn never ended "
                    "after the talking stopped"
                )
                failures += 1
                continue
            waited = ended_at - stopped_at
            if waited > budget_ms:
                print(
                    f"FAIL  room {room}, speech {speech}: {waited} ms of dead air "
                    f"after the user stopped talking, budget {budget_ms} ms"
                )
                failures += 1
    return failures


def check_a_wake_word_turn_in_a_noisy_room_does_not_run_forever() -> int:
    """The cost of trusting the wake word, bounded.

    Nothing can tell a room humming at 0.012 from somebody talking at 0.012
    using level alone, so a wake-word turn opened in a loud room may well latch
    on the room. What it must not do is stay latched: the floor keeps creeping
    up while unseeded — speech included — so the end edge climbs until the room
    falls under it. The turn ends by itself instead of running to MAX_TURN_MS.
    """
    failures = 0
    for noise in (0.006, 0.012, 0.03):
        vad = VoiceActivity(speech_already_underway=True)
        verdicts = [
            vad.on_level(i * CHUNK_MS, lvl) for i, lvl in enumerate(steady(11, noise))
        ]
        if STARTED in verdicts and ENDED not in verdicts:
            print(
                f"FAIL  a wake-word turn latched on a {noise} room and never ended; "
                "it would run to the turn cap and hand the recogniser 12s of noise"
            )
            failures += 1
    return failures


def check_a_tap_to_speak_turn_still_measures_the_room_first() -> int:
    """Nothing above changes the button path.

    Tapping the orb is not a wake word: no speech has happened yet, the first
    buffer really is the room, and the ratios should apply from the start.
    """
    vad = VoiceActivity()
    for i, lvl in enumerate(steady(2, 0.012)):
        vad.on_level(i * CHUNK_MS, lvl)
    if abs(vad.floor - 0.012) > 0.001:
        print(f"FAIL  a tap-to-speak turn no longer measures the room (floor={vad.floor:.4f})")
        return 1
    return 0


def check_a_noisy_room_still_ends_the_turn() -> int:
    """The SECOND complaint, and the expensive one.

    A room at 0.003 sits above the old END_THRESHOLD of 0.001, so the hangover
    could never elapse and every turn ran to the 30-second cap — handing
    Whisper a window packed with room noise.
    """
    noise = 0.003
    vad, verdicts = run(steady(3, noise) + steady(1.5, 0.02) + steady(3, noise))
    failures = 0
    if STARTED not in verdicts:
        print("FAIL  speech over a noisy room was not heard at all")
        failures += 1
    if ENDED not in verdicts:
        print(
            f"FAIL  the turn never ended over a {noise} noise floor — this is the "
            "30-second buffer that makes Whisper return nothing"
        )
        failures += 1
    return failures


def check_the_room_alone_never_starts_a_turn() -> int:
    """Silence must stay silence, at every plausible noise floor."""
    failures = 0
    for noise in (0.0005, 0.001, 0.002, 0.003, 0.006, 0.012):
        _, verdicts = run(steady(20, noise))
        if STARTED in verdicts:
            print(f"FAIL  a steady room at {noise} latched a turn on its own")
            failures += 1
    return failures


def check_a_rising_room_is_tracked() -> int:
    """A fan switched on must not permanently arm the start edge."""
    ramp = [0.0005 + 0.004 * (i / 200) for i in range(200)]
    vad, verdicts = run(ramp + steady(10, 0.0045))
    if STARTED in verdicts:
        print("FAIL  a room getting louder was mistaken for somebody talking")
        return 1
    if vad.floor < 0.003:
        print(f"FAIL  the floor did not follow the room up (floor={vad.floor:.4f})")
        return 1
    return 0


def check_a_quietening_room_is_tracked_at_once() -> int:
    """Down instantly, up slowly — or a lorry deafens Jarvis for a minute."""
    vad, _ = run(steady(3, 0.02) + steady(1, 0.0006))
    if vad.floor > 0.001:
        print(f"FAIL  the floor stayed high after the room went quiet ({vad.floor:.4f})")
        return 1
    return 0


def check_the_floor_is_frozen_during_speech() -> int:
    """Otherwise a long sentence drags the floor up behind it.

    The speaker would talk themselves over their own end edge and the turn
    would cut off mid-word.
    """
    vad, verdicts = run(steady(1, 0.0008) + steady(6, 0.015))
    if ENDED in verdicts:
        print("FAIL  a six-second sentence ended itself while still being spoken")
        return 1
    if vad.floor > 0.002:
        print(f"FAIL  sustained speech dragged the floor up to {vad.floor:.4f}")
        return 1
    return 0


def check_a_transient_is_not_speech() -> int:
    """A door, a cough, a plate.

    MicStreamer smooths at alpha 0.3 per 64 ms buffer, so one loud buffer stays
    above the edge for about 384 ms. The old 120 ms debounce rejected nothing.
    """
    quiet = steady(2, 0.0008)
    # A single loud buffer, then its smoothed decay.
    tail = [0.05 * (0.7 ** i) for i in range(10)]
    _, verdicts = run(quiet + tail + steady(2, 0.0008))
    if STARTED in verdicts:
        print("FAIL  a single transient latched a turn")
        return 1
    return 0


def check_a_pause_between_words_does_not_end_the_turn() -> int:
    """The dead band between the edges is what makes this possible."""
    quiet = steady(1, 0.0008)
    word = steady(0.5, 0.015)
    gap = steady(0.4, 0.0025)  # between the edges: ambiguous, not silence
    _, verdicts = run(quiet + word + gap + word + steady(2, 0.0008))
    if verdicts.index(ENDED) if ENDED in verdicts else None:
        first_end = verdicts.index(ENDED)
        # It must end AFTER the second word, not in the gap.
        gap_end = len(quiet) + len(word) + len(gap)
        if first_end < gap_end:
            print("FAIL  the turn ended in the pause between two words")
            return 1
    if ENDED not in verdicts:
        print("FAIL  the turn never ended after the speaker stopped")
        return 1
    return 0


def check_the_edges_always_have_hysteresis() -> int:
    """start > end at every floor, or a turn starts and ends on one level."""
    vad = VoiceActivity()
    for floor in (0.0, 0.0005, 0.001, 0.003, 0.01, 0.05, 0.2):
        vad.floor = floor
        if not vad.start_edge > vad.end_edge:
            print(f"FAIL  no hysteresis at floor={floor}")
            return 1
    return 0


def check_a_backwards_clock_does_not_wedge_it() -> int:
    vad = VoiceActivity()
    # A quiet room first, so there is a floor to speak over. Without it the very
    # first buffer becomes the floor and a constant tone is — correctly — the
    # room rather than a voice.
    for i in range(40):
        vad.on_level(10_000 + i * CHUNK_MS, 0.0008)
    for i in range(40):
        vad.on_level(12_560 + i * CHUNK_MS, 0.02)
    # Time jumps back.
    verdicts = [vad.on_level(100 + i * CHUNK_MS, 0.02) for i in range(40)]
    if STARTED not in verdicts and SPEAKING not in verdicts:
        print("FAIL  a clock that went backwards wedged the detector")
        return 1
    return 0


def check_the_dead_mic_case_survives() -> int:
    """Digital silence must never look like a quiet room that heard nothing."""
    vad, verdicts = run(steady(10, 0.0))
    if STARTED in verdicts:
        print("FAIL  a dead microphone started a turn")
        return 1
    if vad.peak != 0.0:
        print("FAIL  the peak is not zero for a dead microphone")
        return 1
    return 0


def check_kotlin_agrees(android: Path) -> int:
    path = android / KOTLIN
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    src = path.read_text(encoding="utf-8")
    failures = 0
    for const, value in (
        ("START_RATIO", "4.0f"),
        ("END_RATIO", "2.0f"),
        ("MIN_START", "0.004f"),
        ("MIN_END", "0.002f"),
        ("FLOOR_RISE_PER_CHUNK", "0.0001f"),
        ("START_DEBOUNCE_MS", "200L"),
        ("MIN_SPEECH_MS", "300L"),
        ("END_SILENCE_MS", "900L"),
    ):
        if f"const val {const} = {value}" not in src:
            print(f"FAIL  VoiceActivity.{const} is no longer {value}")
            failures += 1

    if "level <= edge ->" not in src or "floor = if (level < floor)" not in src:
        print("FAIL  the floor is no longer frozen while a level might be speech")
        failures += 1
    if "const val SUSTAIN_RATIO = 0.40f" not in src:
        print("FAIL  the transient test is gone; a door bang latches a turn again")
        failures += 1
    if "level >= candidatePeak * SUSTAIN_RATIO" not in src:
        print("FAIL  the candidate is no longer checked for being sustained")
        failures += 1
    if "maxOf(minStart, floor * startRatio)" not in src:
        print("FAIL  the start edge is no longer relative to the room")
        failures += 1
    if "private var seeded = !speechAlreadyUnderway" not in src:
        print(
            "FAIL  VoiceActivity no longer knows whether speech was already under "
            "way, so a wake-word turn measures the room from the user's own voice"
        )
        failures += 1
    # The flag is useless unless the wake paths actually set it, and both are
    # easy to lose in a refactor: one is a service, the other a full-screen
    # Activity, and neither reads like a voice path from its call site.
    android_src = android / "app/src/main/kotlin/ai/jarvis/app"
    for path, why in (
        ("assist/WakeWordService.kt", "the overlay conversation after a wake word"),
        ("JarvisAssistActivity.kt", "the wake word's full-screen fallback on a locked phone"),
    ):
        text = (android_src / path).read_text(encoding="utf-8")
        if "speechAlreadyUnderway = true" not in text:
            print(
                f"FAIL  {path} does not tell the detector that speech is already "
                f"under way ({why}); the command after the name is never heard"
            )
            failures += 1
    conv = (android_src / "assist/JarvisConversation.kt").read_text(encoding="utf-8")
    if "VoiceActivity(speechAlreadyUnderway = speechAlreadyUnderway)" not in conv:
        print("FAIL  JarvisConversation accepts the flag but never passes it on")
        failures += 1
    if "maxOf(minEnd, floor * endRatio)" not in src:
        print("FAIL  the end edge is no longer relative to the room")
        failures += 1

    # The conversation must actually use it, or this file is a spec for nothing.
    convo = android / "app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt"
    if convo.is_file():
        text = convo.read_text(encoding="utf-8")
        if "VoiceActivity(" not in text:
            print("FAIL  JarvisConversation does not use VoiceActivity")
            failures += 1
        for gone in ("START_THRESHOLD", "END_THRESHOLD"):
            if f"const val {gone}" in text:
                print(f"FAIL  JarvisConversation still declares a fixed {gone}")
                failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_a_quiet_room_hears_ordinary_speech()
        + check_a_wake_word_turn_hears_speech_already_in_progress()
        + check_a_wake_word_turn_still_ends()
        + check_a_wake_word_turn_ends_promptly_after_the_talking()
        + check_a_wake_word_turn_in_a_noisy_room_does_not_run_forever()
        + check_a_tap_to_speak_turn_still_measures_the_room_first()
        + check_a_noisy_room_still_ends_the_turn()
        + check_the_room_alone_never_starts_a_turn()
        + check_a_rising_room_is_tracked()
        + check_a_quietening_room_is_tracked_at_once()
        + check_the_floor_is_frozen_during_speech()
        + check_a_transient_is_not_speech()
        + check_a_pause_between_words_does_not_end_the_turn()
        + check_the_edges_always_have_hysteresis()
        + check_a_backwards_clock_does_not_wedge_it()
        + check_the_dead_mic_case_survives()
        + check_kotlin_agrees(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        "voice activity: ordinary speech is heard in a quiet room, a wake-word "
        "turn ends within a second of the talking, the turn still ends over a "
        "fan, and the room alone never starts one"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
