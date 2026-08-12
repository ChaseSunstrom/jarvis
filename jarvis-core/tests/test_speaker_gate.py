"""The speaker gate in the pipeline, and the enrolment API in front of it.

`test_speaker.py` settles whether the verifier can tell two voices apart. This
file settles what the *system* does with that answer: when a turn is refused,
what a refused turn is allowed to reach, and what the API will and will not
hand out.

The security-relevant assertions are the negative ones — a refused turn must
never reach the conversation agent, and no endpoint may return the voiceprint —
so those are written against observable behaviour (was `converse` called? is
this number in the response body?) rather than against the code that is
supposed to prevent it.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import voice as voice_integration  # noqa: E402
from jarvis.voice.audio import wav_bytes  # noqa: E402
from jarvis.voice.pipeline import (  # noqa: E402
    ERROR_NOT_RECOGNISED,
    EVENT_SPEAKER_END,
    PipelineRun,
)
from jarvis.voice.speaker import (  # noqa: E402
    MODE_ENFORCE,
    MODE_OBSERVE,
    MODE_OFF,
    ON_REJECT_SILENT,
    SpeakerGate,
    VoiceProfile,
    embed,
)
from synth_voice import IMPOSTORS, OWNER, RATE, samples_for  # noqa: E402

_ENROL = (
    {"seconds": 2.5, "seed": 0},
    {"seconds": 2.0, "seed": 1, "f0_scale": 1.10},
    {"seconds": 3.0, "seed": 2, "f0_scale": 0.93},
    {"seconds": 2.2, "seed": 3, "gain": 0.18},
    {"seconds": 2.6, "seed": 4, "f0_scale": 1.05},
)


@pytest.fixture(scope="module")
def profile() -> VoiceProfile:
    built = VoiceProfile.enrol([embed(OWNER.utterance(**kwargs)) for kwargs in _ENROL])
    built.threshold = built.suggested_threshold()
    return built


def gate(profile: VoiceProfile, **kwargs) -> SpeakerGate:
    return SpeakerGate(profile=VoiceProfile(samples=list(profile.samples),
                                            threshold=profile.threshold), **kwargs)


# --- a harness that records what the turn reached ---------------------------
class Recorder:
    """Stands in for the conversation agent, and remembers if it was called."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, text: str, conversation_id: str | None = None) -> str:
        self.calls.append(text)
        return "as you wish"


class FakeStt:
    def __init__(self, text: str = "unlock the front door") -> None:
        self.text = text

    async def transcribe(self, audio_iter, rate=16000):
        async for _ in audio_iter:
            pass
        return self.text


class FakeTts:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def synthesize(self, text, voice=None):
        self.spoken.append(text)
        return (b"\x00\x00" * 100, 22050, 2, 1)


async def run_turn(speaker: SpeakerGate, pcm: bytes, end_stage: str = "tts"):
    agent = Recorder()
    tts = FakeTts()
    run = PipelineRun(
        stt=FakeStt(),
        tts=tts,
        speaker=speaker,
        converse=agent,
        end_stage=end_stage,
        tts_cache={},
    )
    queue: asyncio.Queue = asyncio.Queue()
    for offset in range(0, len(pcm), 3200):
        queue.put_nowait(pcm[offset : offset + 3200])
    queue.put_nowait(None)
    await run.execute(queue)
    return run, agent, tts


# --- what a refused turn reaches --------------------------------------------
async def test_an_impostor_never_reaches_the_conversation_agent(profile):
    """The assertion that matters. Everything else here is about how the
    refusal is reported; this is about what it prevents."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    pcm = IMPOSTORS[0].utterance(seconds=2.5, seed=77)
    run, agent, _ = await run_turn(speaker, pcm)
    assert agent.calls == []
    assert run.error is not None
    assert run.error.code == ERROR_NOT_RECOGNISED


async def test_the_owner_gets_through(profile):
    speaker = gate(profile, mode=MODE_ENFORCE)
    run, agent, _ = await run_turn(speaker, OWNER.utterance(seconds=2.5, seed=404))
    assert agent.calls == ["unlock the front door"]
    assert run.error is None


async def test_observe_scores_but_never_blocks(profile):
    """The mode that makes this safe to turn on. Same verdict, same event, and
    the turn proceeds — otherwise you would have to find your threshold while
    locked out."""
    speaker = gate(profile, mode=MODE_OBSERVE)
    pcm = IMPOSTORS[0].utterance(seconds=2.5, seed=77)
    run, agent, _ = await run_turn(speaker, pcm)
    assert agent.calls == ["unlock the front door"]
    assert run.error is None
    events = [event for event in run.events if event.type == EVENT_SPEAKER_END]
    assert len(events) == 1
    payload = events[0].data["speaker_output"]
    assert payload["accepted"] is False
    assert payload["enforced"] is False


async def test_off_does_not_even_look(profile):
    speaker = gate(profile, mode=MODE_OFF)
    run, agent, _ = await run_turn(speaker, IMPOSTORS[0].utterance(seconds=2.5, seed=77))
    assert agent.calls == ["unlock the front door"]
    assert not [event for event in run.events if event.type == EVENT_SPEAKER_END]


async def test_no_gate_at_all_is_the_old_behaviour():
    run, agent, _ = await run_turn(SpeakerGate(), OWNER.utterance(seconds=2.0, seed=5))
    assert agent.calls == ["unlock the front door"]
    assert run.error is None


async def test_a_mode_set_without_anybody_enrolled_is_inert():
    """Asked for and impossible to honour. It must not block every turn —
    that is a phone that stops working when a config line is added."""
    speaker = SpeakerGate(profile=None, mode=MODE_ENFORCE)
    assert speaker.active is False
    run, agent, _ = await run_turn(speaker, OWNER.utterance(seconds=2.0, seed=6))
    assert agent.calls == ["unlock the front door"]


# --- how a refusal is reported ----------------------------------------------
async def test_a_refusal_is_spoken_by_default(profile):
    """An assistant that goes silent is indistinguishable from one that did not
    hear you, and a false reject is the failure this will actually produce."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    run, _, tts = await run_turn(speaker, IMPOSTORS[0].utterance(seconds=2.5, seed=77))
    assert tts.spoken == [speaker.refusal]
    assert run.tts_url is not None


async def test_silent_refusal_says_nothing_at_all(profile):
    speaker = gate(profile, mode=MODE_ENFORCE, on_reject=ON_REJECT_SILENT)
    run, _, tts = await run_turn(speaker, IMPOSTORS[0].utterance(seconds=2.5, seed=77))
    assert tts.spoken == []
    # Still reported: the console and the log must see it either way, or
    # "silent" would also mean "invisible".
    assert run.error.code == ERROR_NOT_RECOGNISED
    assert [event for event in run.events if event.type == EVENT_SPEAKER_END]


async def test_the_refusal_code_is_not_an_stt_code(profile):
    """"You are not who this belongs to" and "I could not make out what you
    said" are different events; a client that shows them the same way is lying
    to whichever one it is."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    run, _, _ = await run_turn(speaker, IMPOSTORS[0].utterance(seconds=2.5, seed=77))
    assert not run.error.code.startswith("stt")


# --- the unverifiable middle ------------------------------------------------
async def test_a_short_word_still_works(profile):
    """"Stop", "yes", "louder" are all under half a second. An assistant that
    refuses every short word is not usable, which is why allow_unverifiable
    defaults true."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    run, agent, _ = await run_turn(speaker, OWNER.utterance(seconds=0.3, seed=9))
    assert agent.calls == ["unlock the front door"]
    assert run.error is None


async def test_unverifiable_can_be_refused_when_you_want_it_to_be(profile):
    speaker = gate(profile, mode=MODE_ENFORCE, allow_unverifiable=False)
    run, agent, _ = await run_turn(speaker, OWNER.utterance(seconds=0.3, seed=9))
    assert agent.calls == []
    assert run.error.code == ERROR_NOT_RECOGNISED


# --- the on-device-transcription hole ---------------------------------------
async def test_a_transcript_from_a_microphone_is_refused_while_enforcing(profile):
    """The hole an owner opens by accident.

    A phone that transcribes locally sends WORDS: `start_stage: "intent"`, no
    audio, nothing for the gate to look at. With `mode: enforce` and on-device
    transcription both on, every turn used to walk straight past the check —
    and neither setting looks dangerous on its own.
    """
    speaker = gate(profile, mode=MODE_ENFORCE)
    agent = Recorder()
    run = PipelineRun(stt=FakeStt(), tts=FakeTts(), speaker=speaker, converse=agent,
                      start_stage="intent", end_stage="tts", audio_derived=True,
                      tts_cache={})
    await run.execute(None, text="unlock the front door")
    assert agent.calls == []
    assert run.error is not None
    assert run.error.code == ERROR_NOT_RECOGNISED


async def test_typed_text_is_not_touched_by_the_gate(profile):
    """The console's chat is authenticated by the bearer token somebody typed
    it with. This gate is about who is speaking in a room where the microphone
    is open to whoever is standing there — not about keyboards."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    agent = Recorder()
    run = PipelineRun(stt=FakeStt(), tts=FakeTts(), speaker=speaker, converse=agent,
                      start_stage="intent", end_stage="intent", tts_cache={})
    await run.execute(None, text="what is on my calendar")
    assert agent.calls == ["what is on my calendar"]
    assert run.error is None


async def test_an_audio_derived_transcript_is_fine_when_not_enforcing(profile):
    """`observe` still observes and still lets it through — it has nothing to
    score, but it must not start refusing turns that `off` would allow."""
    for mode in (MODE_OFF, MODE_OBSERVE):
        speaker = gate(profile, mode=mode)
        agent = Recorder()
        run = PipelineRun(stt=FakeStt(), tts=FakeTts(), speaker=speaker, converse=agent,
                          start_stage="intent", end_stage="intent", audio_derived=True,
                          tts_cache={})
        await run.execute(None, text="turn the kitchen light on")
        assert agent.calls == ["turn the kitchen light on"], mode
        assert run.error is None, mode


async def test_an_audio_derived_flag_does_nothing_when_nobody_is_enrolled():
    """A flag on a frame must not be able to refuse turns on a server that has
    no voiceprint — that would make an app update break an unconfigured
    install."""
    speaker = SpeakerGate(profile=None, mode=MODE_ENFORCE)
    agent = Recorder()
    run = PipelineRun(stt=FakeStt(), tts=FakeTts(), speaker=speaker, converse=agent,
                      start_stage="intent", end_stage="intent", audio_derived=True,
                      tts_cache={})
    await run.execute(None, text="hello")
    assert agent.calls == ["hello"]


async def test_a_normal_audio_turn_still_verifies_normally(profile):
    """The flag must not short-circuit the real check: a streamed turn carries
    audio AND could carry the flag, and the audio is what decides."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    run, agent, _ = await run_turn(speaker, OWNER.utterance(seconds=2.5, seed=414))
    assert agent.calls == ["unlock the front door"]
    assert run.error is None


# --- failing open, deliberately ---------------------------------------------
async def test_a_verifier_that_crashes_does_not_lock_the_owner_out(profile):
    """A bug is not a stranger. Treating a traceback as a refusal would lock
    somebody out of their own house on a stack trace, and the tier system still
    stands in front of anything dangerous."""

    class Exploding(SpeakerGate):
        def check(self, pcm, rate=16000, width=2):
            raise RuntimeError("the verifier fell over")

    speaker = Exploding(profile=profile, mode=MODE_ENFORCE)
    run, agent, _ = await run_turn(speaker, OWNER.utterance(seconds=2.0, seed=11))
    assert agent.calls == ["unlock the front door"]
    assert run.error is None


# --- the audio is not kept --------------------------------------------------
async def test_the_turn_audio_is_dropped_when_the_run_ends(profile):
    """A voice assistant that accumulated recordings in order to check who was
    talking would have given up more than the check buys back."""
    speaker = gate(profile, mode=MODE_ENFORCE)
    run, _, _ = await run_turn(speaker, OWNER.utterance(seconds=2.5, seed=13))
    assert run._verify_pcm == []
    assert run._verify_task is None


async def test_nothing_is_buffered_when_the_gate_is_off(profile):
    """Not merely unused — not collected. `off` must cost nothing, in memory
    as well as in CPU."""
    speaker = gate(profile, mode=MODE_OFF)
    agent = Recorder()
    run = PipelineRun(stt=FakeStt(), tts=FakeTts(), speaker=speaker,
                      converse=agent, end_stage="intent", tts_cache={})
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(OWNER.utterance(seconds=2.0, seed=15))
    queue.put_nowait(None)
    await run.execute(queue)
    assert run._verify_bytes == 0


async def test_the_wake_stage_audio_is_not_used_to_identify_anyone(profile):
    """The wake leg is the room before anybody addressed us. Judging the turn
    on it would mean judging on whatever was playing when the word landed."""
    speaker = gate(profile, mode=MODE_ENFORCE)

    class FakeWake:
        async def detect(self, audio_iter):
            async for _ in audio_iter:
                pass
            return "hey_jarvis"

    run = PipelineRun(stt=FakeStt(), tts=FakeTts(), wake=FakeWake(), speaker=speaker,
                      converse=Recorder(), start_stage="wake", end_stage="intent",
                      timeout=10.0, tts_cache={})
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(IMPOSTORS[0].utterance(seconds=1.0, seed=21))
    # Two terminators, and the second is not a typo: each stage's audio stream
    # consumes the `None` that ends it, so a wake stage and an stt stage need
    # one each. Writing this test with a single one wedged the run against the
    # 300-second pipeline timeout — a slow test rather than a failing one,
    # which is the kind that gets ignored.
    queue.put_nowait(None)
    queue.put_nowait(None)
    await run.execute(queue)
    # An impostor spoke the wake leg and nothing was banked from it.
    assert run._verify_bytes == 0


# --- the API ----------------------------------------------------------------
@pytest.fixture
async def jarvis(tmp_path):
    instance = Jarvis(tmp_path)
    await instance.async_start()
    instance.data["voice_stt_client"] = FakeStt()
    instance.data["voice_tts_client"] = FakeTts()
    await voice_integration.async_setup(instance, {"speaker": {"mode": "observe"}})
    try:
        yield instance
    finally:
        await instance.async_stop()


async def enrol_all(jarvis) -> dict:
    from jarvis.api import speaker as speaker_api

    payload: dict = {}
    for kwargs in _ENROL:
        wav = wav_bytes(OWNER.utterance(**kwargs), RATE, 2, 1)
        payload = await speaker_api.async_enrol(jarvis, wav, "audio/wav")
    return payload


async def test_enrolment_accumulates_and_reports_as_it_goes(jarvis):
    from jarvis.api import speaker as speaker_api

    payload = await enrol_all(jarvis)
    assert payload["enrolled"] is True
    assert payload["samples"] == len(_ENROL)
    assert payload["suggested_threshold"] > 0
    assert speaker_api.status(jarvis)["active"] is True


async def test_enrolment_survives_a_restart(jarvis, tmp_path):
    """The profile is on disk, or every reboot is a re-enrolment."""
    await enrol_all(jarvis)
    fresh = Jarvis(tmp_path)
    await fresh.async_start()
    try:
        await voice_integration.async_setup(fresh, {"speaker": {"mode": "enforce"}})
        gate_after = voice_integration.get_voice_data(fresh).speaker
        assert gate_after.enrolled is True
        assert gate_after.active is True
    finally:
        await fresh.async_stop()


async def test_the_api_never_returns_the_voiceprint(jarvis):
    """The answer to "is somebody enrolled" must not also be the answer to
    "what do they sound like"."""
    from jarvis.api import speaker as speaker_api

    payload = await enrol_all(jarvis)
    body = json.dumps(payload)
    profile = voice_integration.get_voice_data(jarvis).speaker.profile
    for value in profile.mean[:6]:
        assert f"{value:.6f}" not in body
    assert "vector" not in json.dumps(speaker_api.status(jarvis))
    # The per-sample echo is explicitly blanked rather than merely absent.
    assert payload["sample"]["vector"] is None


async def test_a_sample_with_no_speech_is_refused_with_a_reason(jarvis):
    from jarvis.api import speaker as speaker_api

    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_enrol(jarvis, b"\x00\x00" * RATE, "")
    assert "not enough speech" in str(caught.value)


async def test_a_pitchless_sample_is_refused(jarvis):
    """Accepting one would teach the profile a placeholder histogram that every
    later turn is then measured against."""
    from jarvis.api import speaker as speaker_api

    faint = wav_bytes(OWNER.utterance(seconds=3.0, seed=21, gain=0.06), RATE, 2, 1)
    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_enrol(jarvis, faint, "audio/wav")
    assert "pitch" in str(caught.value)


async def test_an_oversized_sample_is_refused_before_it_is_decoded(jarvis):
    from jarvis.api import speaker as speaker_api

    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_enrol(jarvis, b"\x00" * (speaker_api.MAX_SAMPLE_BYTES + 1), "")
    assert caught.value.status == 413


async def test_verify_scores_without_enrolling(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    before = speaker_api.status(jarvis)["samples"]
    owner = wav_bytes(OWNER.utterance(seconds=2.5, seed=808), RATE, 2, 1)
    result = await speaker_api.async_verify(jarvis, owner, "audio/wav")
    assert result["verdict"]["accepted"] is True
    assert speaker_api.status(jarvis)["samples"] == before


async def test_verify_tells_you_what_would_have_happened(jarvis):
    """How you find your threshold without being locked out while you look."""
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    voice_integration.get_voice_data(jarvis).speaker.mode = MODE_ENFORCE
    stranger = wav_bytes(IMPOSTORS[0].utterance(seconds=2.5, seed=909), RATE, 2, 1)
    result = await speaker_api.async_verify(jarvis, stranger, "audio/wav")
    assert result["verdict"]["accepted"] is False
    assert result["would_block"] is True


async def test_verify_before_enrolment_is_a_conflict_not_a_crash(jarvis):
    from jarvis.api import speaker as speaker_api

    owner = wav_bytes(OWNER.utterance(seconds=2.0, seed=1), RATE, 2, 1)
    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_verify(jarvis, owner, "audio/wav")
    assert caught.value.status == 409


async def test_forgetting_is_a_real_delete(jarvis, tmp_path):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    payload = await speaker_api.async_forget(jarvis)
    assert payload["enrolled"] is False
    stored = (tmp_path / ".storage" / "voice_profile.json").read_text()
    assert "samples" not in json.loads(stored).get("data", {})
    assert voice_integration.get_voice_data(jarvis).speaker.active is False


async def test_raw_pcm_and_wav_enrol_identically(jarvis):
    """The phone has the samples as PCM already; wrapping them to send them
    back would be ceremony."""
    from jarvis.api import speaker as speaker_api

    pcm = OWNER.utterance(seconds=2.5, seed=31)
    from_pcm = await speaker_api.async_enrol(jarvis, pcm, "application/octet-stream")
    await speaker_api.async_forget(jarvis)
    from_wav = await speaker_api.async_enrol(jarvis, wav_bytes(pcm, RATE, 2, 1), "audio/wav")
    assert from_pcm["sample"]["speech_ms"] == from_wav["sample"]["speech_ms"]


async def test_a_bad_mode_in_the_config_leaves_the_gate_off(tmp_path):
    """A typo must not lock somebody out of their own house."""
    instance = Jarvis(tmp_path)
    await instance.async_start()
    try:
        await voice_integration.async_setup(instance, {"speaker": {"mode": "enfroce"}})
        assert voice_integration.get_voice_data(instance).speaker.mode == MODE_OFF
    finally:
        await instance.async_stop()


async def test_the_prompts_are_served_so_both_surfaces_agree(jarvis):
    from jarvis.api import speaker as speaker_api
    from jarvis.voice.speaker import ENROLMENT_PROMPTS

    assert speaker_api.status(jarvis)["prompts"] == list(ENROLMENT_PROMPTS)
