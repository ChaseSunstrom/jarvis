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
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import voice as voice_integration  # noqa: E402
from jarvis.voice.audio import wav_bytes  # noqa: E402
from jarvis.voice.pipeline import (  # noqa: E402
    ERROR_NOT_RECOGNISED,
    EVENT_SPEAKER_END,
    EVENT_SPEAKER_VERDICT,
    PipelineRun,
)
from jarvis.voice.speaker import (  # noqa: E402
    DEFAULT_LABEL,
    MODE_ENFORCE,
    MODE_OBSERVE,
    MODE_OFF,
    ON_REJECT_SILENT,
    SpeakerGate,
    VoiceProfile,
    embed,
    profiles_from_dict,
    profiles_to_dict,
)
from synth_voice import IMPOSTORS, OWNER, RATE  # noqa: E402

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


async def run_turn(
    speaker: SpeakerGate,
    pcm: bytes,
    end_stage: str = "tts",
    *,
    jarvis: Jarvis | None = None,
    agent: Any = None,
):
    agent = Recorder() if agent is None else agent
    tts = FakeTts()
    run = PipelineRun(
        jarvis,
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


# --- M71: who is speaking, and what the house does with it ------------------
#
# Everything above was written for one enrolled person. These settle the
# household: a second voice with a name, the verdict naming who, the agent
# being told, the bus carrying it for surfaces that did not run the turn, and
# the store and the API keeping people apart.

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "speaker_verdict.json")
    .read_text()
)

#: The second person. The soprano — far from the owner on purpose, so what
#: these tests settle is the plumbing (who is credited, who is told) and not
#: the verifier's margin, which `test_speaker.py` owns.
TED = IMPOSTORS[0]
#: The stranger: the baritone, the cast's hard case at the owner's pitch. A
#: two-person gate refuses it and lands it nearest the owner (measured 26 Aug:
#: 11.9 and 15.3 against a threshold of 9.0).
STRANGER = IMPOSTORS[1]
_TED = (
    {"seconds": 2.5, "seed": 30},
    {"seconds": 2.0, "seed": 31, "f0_scale": 1.08},
    {"seconds": 3.0, "seed": 32, "f0_scale": 0.94},
    {"seconds": 2.2, "seed": 33, "gain": 0.2},
    {"seconds": 2.6, "seed": 34, "f0_scale": 1.04},
)


@pytest.fixture(scope="module")
def ted() -> VoiceProfile:
    built = VoiceProfile.enrol([embed(TED.utterance(**kwargs)) for kwargs in _TED], label="Ted")
    built.threshold = built.suggested_threshold()
    return built


def household(profile: VoiceProfile, ted: VoiceProfile, **kwargs) -> SpeakerGate:
    """A gate holding the owner and Ted, each on their own copy."""
    return SpeakerGate(
        profiles=[
            VoiceProfile(samples=list(profile.samples), threshold=profile.threshold, label="owner"),
            VoiceProfile(samples=list(ted.samples), threshold=ted.threshold, label="Ted"),
        ],
        **kwargs,
    )


class NamedRecorder(Recorder):
    """A conversation agent that can be told who is speaking."""

    def __init__(self) -> None:
        super().__init__()
        self.speakers: list[str | None] = []

    async def __call__(
        self, text: str, conversation_id: str | None = None, *, speaker: str | None = None
    ) -> str:
        self.speakers.append(speaker)
        return await super().__call__(text, conversation_id)


# --- the verdict names who ----------------------------------------------------
def test_a_household_credits_each_voice_to_its_own_person(profile, ted):
    gate = household(profile, ted, mode=MODE_ENFORCE)
    owner = gate.check(OWNER.utterance(seconds=2.5, seed=404))
    assert owner.accepted and owner.label == "owner" and owner.nearest == "owner"
    voice = gate.check(TED.utterance(seconds=2.5, seed=77))
    assert voice.accepted and voice.label == "Ted" and voice.nearest == "Ted"


def test_a_stranger_is_nobody_and_the_verdict_says_who_they_were_nearest(profile, ted):
    """`label` is never the nearest miss. A consumer reading "label: owner" as
    "the owner spoke" must not be handed the closest stranger under that key."""
    gate = household(profile, ted, mode=MODE_ENFORCE)
    verdict = gate.check(STRANGER.utterance(seconds=2.5, seed=77))
    assert verdict.accepted is False
    assert verdict.label is None
    assert verdict.nearest in {"owner", "Ted"}
    assert verdict.as_dict()["label"] is None and verdict.as_dict()["nearest"] == verdict.nearest


def test_verifying_against_one_named_person_ignores_the_others(profile, ted):
    gate = household(profile, ted)
    embedding = embed(TED.utterance(seconds=2.5, seed=78))
    assert gate.verify_embedding(embedding).label == "Ted"
    against_owner = gate.verify_embedding(embedding, "owner")
    assert against_owner.accepted is False and against_owner.nearest == "owner"
    assert gate.verify_embedding(embedding, "nobody").reason == "not-enrolled"


def test_adaptation_teaches_the_person_who_spoke_and_nobody_else(profile, ted):
    gate = household(profile, ted, mode=MODE_OBSERVE, adapt=True, adapt_margin=1.0,
                     adapt_min_interval=0.0)
    before = [len(p.samples) for p in gate.profiles]
    verdict = gate.check(TED.utterance(seconds=2.5, seed=79))
    assert verdict.label == "Ted"
    after = [len(p.samples) for p in gate.profiles]
    assert after[0] == before[0], "the owner's profile learned from Ted's voice"
    assert after[1] == before[1] + 1


# --- the pipeline, and what the agent is told --------------------------------
async def test_the_pipeline_names_who_spoke_to_an_agent_that_can_hear_it(profile, ted):
    gate = household(profile, ted, mode=MODE_ENFORCE)
    agent = NamedRecorder()
    run, _, _ = await run_turn(gate, OWNER.utterance(seconds=2.5, seed=404), agent=agent)
    assert run.speaker_label() == "owner"
    assert agent.speakers == ["owner"]
    agent = NamedRecorder()
    run, _, _ = await run_turn(gate, TED.utterance(seconds=2.5, seed=77), agent=agent)
    assert run.speaker_label() == "Ted"
    assert agent.speakers == ["Ted"]


async def test_an_agent_that_cannot_take_a_speaker_is_still_called(profile, ted):
    """The name is opt-in. The service bridge, the no-agent stand-in and every
    two-argument coroutine in these tests must keep working the day the gate
    first recognises somebody."""
    gate = household(profile, ted, mode=MODE_ENFORCE)
    run, agent, _ = await run_turn(gate, OWNER.utterance(seconds=2.5, seed=404))
    assert run.speaker_label() == "owner"
    assert agent.calls == ["unlock the front door"]


async def test_the_speaker_is_none_for_every_turn_the_gate_did_not_accept(profile):
    """"Unverified" and "stranger" are different claims. The agent is told a
    name or nothing — never "unrecognised" — so the owner with a cold, in
    `observe`, is not described to the model as an intruder."""
    agent = NamedRecorder()
    run, _, _ = await run_turn(
        gate(profile, mode=MODE_OBSERVE), IMPOSTORS[1].utterance(seconds=2.5, seed=77), agent=agent
    )
    assert run.speaker_label() is None and agent.speakers == [None]
    agent = NamedRecorder()
    run, _, _ = await run_turn(
        gate(profile, mode=MODE_ENFORCE), OWNER.utterance(seconds=0.3, seed=9), agent=agent
    )
    assert run.speaker_label() is None and agent.speakers == [None]
    agent = NamedRecorder()
    run, _, _ = await run_turn(
        gate(profile, mode=MODE_OFF), OWNER.utterance(seconds=2.5, seed=404), agent=agent
    )
    assert run.speaker_label() is None and agent.speakers == [None]


# --- the bus ------------------------------------------------------------------
@pytest.fixture
async def house(tmp_path):
    instance = Jarvis(tmp_path)
    await instance.async_start()
    try:
        yield instance
    finally:
        await instance.async_stop()


def _listen(house: Jarvis) -> list[dict]:
    seen: list[dict] = []
    house.bus.listen(EVENT_SPEAKER_VERDICT, lambda event: seen.append(dict(event.data)))
    return seen


async def test_the_verdict_goes_on_the_bus_in_the_contract_shape(profile, ted, house):
    """For surfaces that did not run the turn: the console's strip, the
    phone's. The fields are the contract's, and nothing biometric is among
    them."""
    seen = _listen(house)
    gate = household(profile, ted, mode=MODE_ENFORCE)
    run, _, _ = await run_turn(gate, TED.utterance(seconds=2.5, seed=77), jarvis=house)
    assert len(seen) == 1
    event = seen[0]
    assert sorted(event) == sorted(CONTRACT["required"])
    assert event["accepted"] is True and event["label"] == "Ted" and event["enforced"] is False
    assert event["run_id"] == run.run_id and event["mode"] == MODE_ENFORCE
    body = json.dumps(event)
    for key in CONTRACT["never"]:
        assert f'"{key}"' not in body, key
    for value in ted.mean[:6]:
        assert f"{value:.6f}" not in body


async def test_a_refusal_on_the_bus_names_nobody_and_says_it_was_enforced(profile, ted, house):
    seen = _listen(house)
    gate = household(profile, ted, mode=MODE_ENFORCE)
    await run_turn(gate, STRANGER.utterance(seconds=2.5, seed=77), jarvis=house)
    (event,) = seen
    assert event["accepted"] is False and event["label"] is None
    assert event["nearest"] in {"owner", "Ted"}
    assert event["enforced"] is True
    assert event["reason"] not in CONTRACT["unverifiable_reasons"]


async def test_an_unverifiable_turn_is_on_the_bus_as_unverifiable_not_as_a_stranger(profile, house):
    seen = _listen(house)
    await run_turn(gate(profile, mode=MODE_ENFORCE), OWNER.utterance(seconds=0.3, seed=9), jarvis=house)
    (event,) = seen
    assert event["accepted"] is False and event["enforced"] is False
    assert event["reason"] in CONTRACT["unverifiable_reasons"]


async def test_an_on_device_transcript_refused_while_enforcing_is_on_the_bus_too(profile, house):
    seen = _listen(house)
    run = PipelineRun(house, stt=FakeStt(), tts=FakeTts(), speaker=gate(profile, mode=MODE_ENFORCE),
                      converse=Recorder(), start_stage="intent", end_stage="tts",
                      audio_derived=True, tts_cache={})
    await run.execute(None, text="unlock the front door")
    (event,) = seen
    assert sorted(event) == sorted(CONTRACT["required"])
    assert event["reason"] == "unverifiable-transcript" and event["enforced"] is True


async def test_nothing_is_fired_when_the_gate_is_off_or_absent(profile, house):
    seen = _listen(house)
    await run_turn(gate(profile, mode=MODE_OFF), OWNER.utterance(seconds=2.5, seed=404), jarvis=house)
    await run_turn(SpeakerGate(), OWNER.utterance(seconds=2.0, seed=5), jarvis=house)
    assert seen == []


# --- the store ------------------------------------------------------------------
def test_a_store_from_before_names_loads_as_the_owner(profile):
    """Version 1 was one profile's dict at the top level. An upgrade must keep
    whoever was enrolled, under the name the phone and the console were
    enrolling them as."""
    people = profiles_from_dict(profile.as_dict())
    assert [p.label for p in people] == [DEFAULT_LABEL]
    assert people[0].samples == profile.samples
    assert people[0].threshold == profile.threshold


def test_the_store_round_trips_a_household(profile, ted):
    payload = profiles_to_dict([profile, ted])
    assert payload["version"] == 2 and len(payload["people"]) == 2
    people = profiles_from_dict(json.loads(json.dumps(payload)))
    assert [p.label for p in people] == ["owner", "Ted"]
    assert people[1].samples == ted.samples and people[1].threshold == ted.threshold


def test_a_store_with_the_same_name_twice_keeps_the_first(profile, ted):
    twice = profiles_to_dict([profile, VoiceProfile(samples=list(ted.samples), label="OWNER")])
    people = profiles_from_dict(twice)
    assert len(people) == 1 and people[0].samples == profile.samples


def test_a_store_with_nothing_recognisable_is_nobody():
    assert profiles_from_dict({}) == []
    assert profiles_from_dict({"people": "not a list"}) == []
    assert profiles_from_dict("junk") == []


# --- the threshold from the config --------------------------------------------
@pytest.fixture
async def pinned(tmp_path):
    """A house whose operator typed `threshold: 8.8`."""
    instance = Jarvis(tmp_path)
    await instance.async_start()
    instance.data["voice_stt_client"] = FakeStt()
    instance.data["voice_tts_client"] = FakeTts()
    await voice_integration.async_setup(
        instance, {"speaker": {"mode": "observe", "threshold": 8.8}}
    )
    try:
        yield instance
    finally:
        await instance.async_stop()


async def test_a_configured_threshold_survives_every_enrolment_sample(pinned, tmp_path):
    """It used to be written INTO the profile, and enrolment rewrote it from
    the next sample's leave-one-out spread — so a person typed 8.8, read one
    more phrase, and was gated at 5.1 until the next restart put it back."""
    from jarvis.api import speaker as speaker_api

    payload = await enrol_all(pinned)
    assert payload["threshold"] == 8.8
    assert payload["configured_threshold"] == 8.8
    gate_now = voice_integration.get_voice_data(pinned).speaker
    assert gate_now.profile.threshold == 8.8
    # The profile's own measurement is what is STORED, so the day the config
    # line is removed the gate falls back to what enrolment worked out.
    stored = json.loads((tmp_path / ".storage" / "voice_profile.json").read_text())["data"]
    assert round(stored["people"][0]["threshold"], 3) == payload["suggested_threshold"]
    assert stored["people"][0]["threshold"] != 8.8
    assert speaker_api.status(pinned)["threshold"] == 8.8


async def test_a_configured_threshold_is_reapplied_on_restart(pinned, tmp_path):
    await enrol_all(pinned)
    fresh = Jarvis(tmp_path)
    await fresh.async_start()
    try:
        await voice_integration.async_setup(fresh, {"speaker": {"mode": "enforce", "threshold": 7.5}})
        assert voice_integration.get_voice_data(fresh).speaker.profile.threshold == 7.5
    finally:
        await fresh.async_stop()


async def test_no_configured_threshold_means_each_profile_keeps_its_own(jarvis):
    from jarvis.api import speaker as speaker_api

    payload = await enrol_all(jarvis)
    assert payload["configured_threshold"] is None
    assert payload["threshold"] == payload["suggested_threshold"]
    assert speaker_api.status(jarvis)["configured_threshold"] is None


# --- the API, with names --------------------------------------------------------
async def enrol_ted(jarvis) -> dict:
    from jarvis.api import speaker as speaker_api

    payload: dict = {}
    for kwargs in _TED:
        wav = wav_bytes(TED.utterance(**kwargs), RATE, 2, 1)
        payload = await speaker_api.async_enrol(jarvis, wav, "audio/wav", label="Ted")
    return payload


async def test_enrolling_with_a_name_adds_a_second_person(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    payload = await enrol_ted(jarvis)
    assert payload["label"] == "Ted" and payload["person_enrolled"] is True
    assert payload["samples"] == len(_TED)
    status = speaker_api.status(jarvis)
    assert [person["label"] for person in status["people"]] == ["owner", "Ted"]
    assert status["enrolled"] is True and status["active"] is True
    # The top level with no label describes the FIRST person, as it always
    # did, so a client that knows nothing about names keeps working.
    assert status["label"] == "owner" and status["samples"] == len(_ENROL)


async def test_status_for_one_person_is_case_insensitive_and_honest_about_absence(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    ted_status = speaker_api.status(jarvis, "ted")
    assert ted_status["label"] == "Ted" and ted_status["person_enrolled"] is True
    nobody = speaker_api.status(jarvis, "Nobody")
    assert nobody["person_enrolled"] is False and nobody["samples"] == 0
    # ...while the gate as a whole is still enrolled: "does the gate do
    # anything" is a different question from "is this person in it".
    assert nobody["enrolled"] is True


async def test_forgetting_one_person_keeps_the_others(jarvis, tmp_path):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    payload = await speaker_api.async_forget(jarvis, "Ted")
    assert [person["label"] for person in payload["people"]] == ["owner"]
    assert voice_integration.get_voice_data(jarvis).speaker.active is True
    stored = json.loads((tmp_path / ".storage" / "voice_profile.json").read_text())["data"]
    assert [person["label"] for person in stored["people"]] == ["owner"]
    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_forget(jarvis, "Ted")
    assert caught.value.status == 404


async def test_forgetting_everyone_is_still_all_or_nothing(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    payload = await speaker_api.async_forget(jarvis)
    assert payload["people"] == [] and payload["enrolled"] is False


async def test_verify_says_who_it_was(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    voice = wav_bytes(TED.utterance(seconds=2.5, seed=78), RATE, 2, 1)
    result = await speaker_api.async_verify(jarvis, voice, "audio/wav")
    assert result["verdict"]["accepted"] is True and result["verdict"]["label"] == "Ted"
    owner = wav_bytes(OWNER.utterance(seconds=2.5, seed=808), RATE, 2, 1)
    result = await speaker_api.async_verify(jarvis, owner, "audio/wav")
    assert result["verdict"]["label"] == "owner"


async def test_verify_against_one_person_compares_with_that_person_only(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    voice = wav_bytes(TED.utterance(seconds=2.5, seed=78), RATE, 2, 1)
    result = await speaker_api.async_verify(jarvis, voice, "audio/wav", label="owner")
    assert result["verdict"]["accepted"] is False and result["verdict"]["nearest"] == "owner"
    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_verify(jarvis, voice, "audio/wav", label="Nobody")
    assert caught.value.status == 404


async def test_a_name_that_cannot_be_one_is_refused(jarvis):
    from jarvis.api import speaker as speaker_api

    wav = wav_bytes(TED.utterance(**_TED[0]), RATE, 2, 1)
    for bad in ("x" * 41, "Ted\x00", "Ted\x1b[2J"):
        with pytest.raises(speaker_api.EnrolError) as caught:
            await speaker_api.async_enrol(jarvis, wav, "audio/wav", label=bad)
        assert caught.value.status == 400, bad
    # A line-feed is whitespace: it collapses, so a name can never write a
    # second line into a log or a prompt.
    payload = await speaker_api.async_enrol(jarvis, wav, "audio/wav", label="Ted\nowner")
    assert payload["label"] == "Ted owner"
    # Whitespace alone is not a name; it is the default person, as before names.
    payload = await speaker_api.async_enrol(jarvis, wav, "audio/wav", label="   ")
    assert payload["label"] == DEFAULT_LABEL


async def test_the_house_holds_at_most_max_people(jarvis, monkeypatch):
    """Full is a 409 that says to forget somebody — never a quiet eviction
    of whoever was enrolled longest."""
    from jarvis.api import speaker as speaker_api
    from jarvis.voice import speaker as speaker_module

    monkeypatch.setattr(speaker_module, "MAX_PEOPLE", 2)
    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    wav = wav_bytes(IMPOSTORS[2].utterance(seconds=2.5, seed=90), RATE, 2, 1)
    with pytest.raises(speaker_api.EnrolError) as caught:
        await speaker_api.async_enrol(jarvis, wav, "audio/wav", label="Third")
    assert caught.value.status == 409 and "forget" in str(caught.value)
    assert [p["label"] for p in speaker_api.status(jarvis)["people"]] == ["owner", "Ted"]


async def test_nobody_in_the_people_list_carries_a_vector(jarvis):
    from jarvis.api import speaker as speaker_api

    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    body = json.dumps(speaker_api.status(jarvis))
    assert "vector" not in body and "samples_data" not in body
    for profile in voice_integration.get_voice_data(jarvis).speaker.profiles:
        for value in profile.mean[:6]:
            assert f"{value:.6f}" not in body


async def test_a_household_survives_a_restart_with_everyone_named(jarvis, tmp_path):
    await enrol_all(jarvis)
    await enrol_ted(jarvis)
    fresh = Jarvis(tmp_path)
    await fresh.async_start()
    try:
        await voice_integration.async_setup(fresh, {"speaker": {"mode": "enforce"}})
        after = voice_integration.get_voice_data(fresh).speaker
        assert [p.label for p in after.profiles] == ["owner", "Ted"]
        assert after.active is True
    finally:
        await fresh.async_stop()


# --- the security posture: enrolment is a REST write and nothing else ---------
async def test_no_tool_and_no_websocket_command_can_enrol(tmp_path):
    """An enrolment is a durable write about a person — it changes whose
    voice Jarvis answers until somebody deletes it. It is reachable only over
    REST with a credential a person holds (the bearer token or the console
    password); there is no tool for the model and no socket command, so no
    turn — and in particular no turn that has read untrusted content — can
    enrol anybody. `docs/security.md` says why that is the tier."""
    from jarvis.api.rest import api_router, open_router
    from jarvis.api.websocket import WebSocketHandler
    from jarvis.llm.tools import Exposure, ToolRegistry, register_builtin_tools

    instance = Jarvis(tmp_path)
    await instance.async_start()
    try:
        registry = ToolRegistry(instance, exposure=Exposure.from_config(None))
        register_builtin_tools(registry, None)
        for name, tool in registry.tools.items():
            # "speaker" alone would match the media tool's "control a speaker
            # or tv"; the words below are the ones that mean THIS feature.
            haystack = f"{name} {getattr(tool, 'description', '')}".lower()
            for word in ("enrol", "voiceprint", "voice identity", "speaker gate"):
                assert word not in haystack, f"a tool can reach enrolment: {name}"
            assert "speaker" not in name, f"a tool is named after the gate: {name}"
    finally:
        await instance.async_stop()
    for command in WebSocketHandler._HANDLERS:
        assert "speaker" not in command and "enrol" not in command, command
    gated = {route.path for route in api_router.routes if "/voice/speaker" in route.path}
    assert gated == {"/api/voice/speaker", "/api/voice/speaker/enrol", "/api/voice/speaker/verify"}
    assert not [route for route in open_router.routes if "speaker" in route.path]
