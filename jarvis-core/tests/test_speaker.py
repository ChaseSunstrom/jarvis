"""Speaker verification: the DSP, the profile, and whether it separates anyone.

No recordings and no network. `synth_voice.py` generates talkers from a
source-filter model, which is the verifier's own claim about what distinguishes
people written as a synthesiser — see that module for what this can and cannot
settle.

The separation numbers asserted below are the ones quoted in
`jarvis/voice/speaker.py`'s docstring and in `docs/voice-identity.md`. If a
change to the features moves them, three places have to move together, which is
the point.
"""

import math
import statistics
import sys
import time
from array import array
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis.voice.audio import wav_bytes  # noqa: E402
from jarvis.voice.dsp import (  # noqa: E402
    MEL_BANDS,
    N_FFT,
    autocorrelation,
    dct2,
    hann_window,
    mel_filterbank,
    power_spectrum,
    pre_emphasis,
    rfft,
)
from jarvis.voice.speaker import (  # noqa: E402
    DEFAULT_THRESHOLD,
    EMBEDDING_DIMS,
    ENROLMENT_PROMPTS,
    MAX_ENROLMENT_SAMPLES,
    MAX_THRESHOLD,
    MIN_ENROLMENT_SAMPLES,
    MIN_MEASURABLE_SAMPLES,
    MIN_PITCH_FRAMES,
    MIN_SPEECH_MS,
    Embedding,
    SpeakerError,
    VoiceProfile,
    embed,
    embed_wav,
    speech_ms,
)
from synth_voice import IMPOSTORS, OWNER, RATE, samples_for, whispering  # noqa: E402


# --- fixtures ---------------------------------------------------------------
#: Enrolment that covers the owner's range, as `ENROLMENT_PROMPTS` is designed
#: to make a real one do: different lengths, one louder, one higher, one lower.
_ENROL = (
    {"seconds": 2.5, "seed": 0},
    {"seconds": 2.0, "seed": 1, "f0_scale": 1.10},
    {"seconds": 3.0, "seed": 2, "f0_scale": 0.93},
    {"seconds": 2.2, "seed": 3, "gain": 0.18},
    {"seconds": 2.6, "seed": 4, "f0_scale": 1.05},
)

#: Held-out owner utterances: different words, lengths, levels and pitches from
#: anything enrolment saw, including one 18% above it.
_HELD_OUT = (
    {"seconds": 2.5, "start": 400},
    {"seconds": 1.8, "start": 100},
    {"seconds": 3.5, "start": 200, "gain": 0.15},
    {"seconds": 2.2, "start": 300, "f0_scale": 1.12},
    {"seconds": 2.2, "start": 500, "f0_scale": 0.92},
    {"seconds": 2.0, "start": 600, "f0_scale": 1.18},
)


@pytest.fixture(scope="module")
def owner_profile() -> VoiceProfile:
    """Enrolled, and carrying the threshold enrolment itself suggests — which
    is the one the running system uses, so the tests judge what ships."""
    profile = VoiceProfile.enrol([embed(OWNER.utterance(**kwargs)) for kwargs in _ENROL])
    profile.threshold = profile.suggested_threshold()
    return profile


@pytest.fixture(scope="module")
def owner_held_out() -> list[Embedding]:
    return [
        embed(sample)
        for kwargs in _HELD_OUT
        for sample in samples_for(OWNER, 3, **kwargs)
    ]


@pytest.fixture(scope="module")
def impostor_embeddings() -> dict[str, list[Embedding]]:
    out: dict[str, list[Embedding]] = {}
    for speaker in IMPOSTORS:
        out[speaker.name] = [
            embed(sample) for sample in samples_for(speaker, 4, start=50, seconds=2.5)
        ] + [
            embed(sample)
            for sample in samples_for(speaker, 2, start=80, seconds=2.0, f0_scale=1.1)
        ]
    return out


# --- the DSP ----------------------------------------------------------------
def test_rfft_matches_a_naive_dft():
    """The FFT is hand-written, so something has to check it against the
    definition rather than against itself."""
    import random

    rng = random.Random(11)
    frame = array("d", (rng.uniform(-1.0, 1.0) for _ in range(N_FFT)))
    real, imag = rfft(frame, N_FFT)
    for bin_index in (0, 1, 7, 64, 200, N_FFT // 2):
        expected_real = sum(
            frame[n] * math.cos(-2 * math.pi * bin_index * n / N_FFT) for n in range(N_FFT)
        )
        expected_imag = sum(
            frame[n] * math.sin(-2 * math.pi * bin_index * n / N_FFT) for n in range(N_FFT)
        )
        assert real[bin_index] == pytest.approx(expected_real, abs=1e-8)
        assert imag[bin_index] == pytest.approx(expected_imag, abs=1e-8)


def test_autocorrelation_matches_the_direct_sum():
    """Wiener-Khinchin gives the *circular* autocorrelation, which is what the
    pitch estimator is written against. Asserting it against the linear one
    would pass at low lags and quietly diverge exactly where F0 lives."""
    import random

    rng = random.Random(13)
    frame = array("d", (rng.uniform(-1.0, 1.0) for _ in range(N_FFT)))
    result = autocorrelation(power_spectrum(frame, N_FFT), N_FFT)
    assert len(result) == N_FFT // 2 + 1
    for lag in (0, 1, 40, 160, N_FFT // 2):
        direct = sum(frame[n] * frame[(n + lag) % N_FFT] for n in range(N_FFT))
        assert result[lag] == pytest.approx(direct, abs=1e-8)


def test_a_pure_tone_lands_in_the_right_bin():
    tone = array("d", (math.sin(2 * math.pi * 1000 * n / RATE) for n in range(N_FFT)))
    window = hann_window(N_FFT)
    windowed = array("d", (tone[n] * window[n] for n in range(N_FFT)))
    power = power_spectrum(windowed, N_FFT)
    peak = max(range(len(power)), key=lambda index: power[index])
    assert peak * RATE / N_FFT == pytest.approx(1000.0, abs=RATE / N_FFT)


def test_mel_filters_cover_the_band_without_gaps():
    filters = mel_filterbank(RATE, N_FFT, MEL_BANDS)
    assert len(filters) == MEL_BANDS
    # Every filter has some weight in it, and they march upward. A filterbank
    # with an empty low band is the classic symptom of rounding edges to
    # integer bins, and it silently deletes the bottom of the spectrum.
    for first, weights in filters:
        assert max(weights) > 0.0
    firsts = [first for first, _ in filters]
    assert firsts == sorted(firsts)


def test_dct_is_orthonormal():
    """Round-trips through the basis, so a change in MEL_BANDS cannot silently
    rescale every MFCC."""
    values = array("d", (math.sin(0.3 * n) + 0.5 for n in range(MEL_BANDS)))
    coefficients = dct2(values, MEL_BANDS)
    energy_in = sum(value * value for value in values)
    energy_out = sum(value * value for value in coefficients)
    assert energy_out == pytest.approx(energy_in, rel=1e-9)


def test_pre_emphasis_passes_the_first_sample_through():
    result = pre_emphasis(array("d", [1.0, 1.0, 1.0]), 0.97)
    assert result[0] == pytest.approx(1.0)
    assert result[1] == pytest.approx(0.03)


# --- embedding --------------------------------------------------------------
def test_embedding_has_the_declared_shape():
    result = embed(OWNER.utterance(seconds=2.0, seed=9))
    assert result is not None
    assert len(result.vector) == EMBEDDING_DIMS
    assert result.speech_ms >= MIN_SPEECH_MS
    assert all(math.isfinite(value) for value in result.vector)


@pytest.mark.parametrize(
    "pcm,reason",
    [
        (b"", "empty"),
        (b"\x00\x00" * 100, "shorter than one frame"),
        (b"\x00\x00" * RATE, "a second of digital silence"),
    ],
)
def test_unusable_audio_embeds_to_none(pcm, reason):
    """`None` is the answer for "not enough speech to judge". Anything that
    turned this into a zero vector would make silence match the owner."""
    assert embed(pcm) is None, reason


def test_too_short_to_judge_is_none():
    assert embed(OWNER.utterance(seconds=0.25, seed=3)) is None


def test_embedding_ignores_level():
    """c0 is dropped, and each frame's spectrum is normalised, precisely so
    that how loud you were — or how far from the microphone — does not change
    who you are."""
    loud = embed(OWNER.utterance(seconds=2.5, seed=21, gain=0.6))
    quiet = embed(OWNER.utterance(seconds=2.5, seed=21, gain=0.15))
    assert loud is not None and quiet is not None
    worst = max(abs(a - b) for a, b in zip(loud.vector, quiet.vector))
    assert worst < 0.02


def test_a_capture_too_quiet_to_measure_says_so_rather_than_guessing():
    """At the bottom of the 16-bit range, quantisation noise swamps the
    periodicity and no F0 comes out. The embedding still exists — timbre
    survives — but it must declare that its pitch block is a placeholder, or
    the flat histogram scores as *wrong* pitch instead of *no* pitch."""
    faint = embed(OWNER.utterance(seconds=2.5, seed=21, gain=0.06))
    assert faint is not None
    assert faint.has_pitch is False
    assert faint.pitch_frames < MIN_PITCH_FRAMES


def test_a_pitchless_utterance_is_scored_on_timbre_alone(owner_profile):
    """Scoring the placeholder histogram would read "I could not measure your
    pitch" as "your pitch is wrong"."""
    faint = embed(OWNER.utterance(seconds=3.0, seed=21, gain=0.06))
    assert faint.has_pitch is False
    verdict = owner_profile.verify(faint)
    # The pitch block is still reported — "it was the pitch" stays answerable —
    # but it is not what the score was computed from.
    assert "pitch" in verdict.blocks
    assert verdict.score < verdict.blocks["pitch"]


def test_a_pitchless_utterance_is_never_a_match(owner_profile):
    """Even the owner's own, and *especially* then: the owner whispering is
    the closest a pitchless utterance ever gets, so if anything is let through
    on timbre alone it is this. Dropping a block must not become a way through
    the gate — the measured bypass is in the docstring."""
    verdicts = [
        owner_profile.verify(embed(sample))
        for sample in samples_for(whispering(OWNER), 4, start=70, seconds=3.0)
    ]
    close = [verdict for verdict in verdicts if verdict.score <= verdict.threshold]
    assert close, "the owner whispering should still land near their own profile"
    for verdict in close:
        assert verdict.accepted is False
        # Distinguishable from a real mismatch, so the policy layer can treat
        # it the way it treats "too short to judge" rather than as an intruder.
        assert verdict.reason == "unverifiable-no-pitch"


def test_whispering_at_it_does_not_get_you_in(owner_profile):
    """The regression this rule exists for, named after the speaker that found
    it."""
    whisperer = next(speaker for speaker in IMPOSTORS if speaker.name == "whisperer")
    embeddings = [embed(sample) for sample in samples_for(whisperer, 4, start=50, seconds=2.5)]
    assert any(not item.has_pitch for item in embeddings), (
        "the breathy speaker is supposed to be the pitchless one; if this "
        "stops being true the test has stopped testing anything"
    )
    verdicts = [owner_profile.verify(item) for item in embeddings]
    assert not any(verdict.accepted for verdict in verdicts)
    # And at least one of them was close enough on timbre that only the
    # pitchless rule stopped it — which is the whole point.
    assert any(verdict.reason == "unverifiable-no-pitch" for verdict in verdicts)


def test_embed_wav_matches_embed_on_the_same_samples():
    pcm = OWNER.utterance(seconds=2.0, seed=31)
    assert embed_wav(wav_bytes(pcm, RATE, 2, 1)).vector == embed(pcm).vector


def test_stereo_is_downmixed_not_truncated():
    """Taking channel 0 is the cheap way and it turns a headset with the speech
    on the right into silence."""
    mono = OWNER.utterance(seconds=2.0, seed=41)
    values = array("h")
    values.frombytes(mono)
    stereo = array("h")
    for value in values:
        stereo.append(0)  # a dead left channel
        stereo.append(value)
    assert embed(stereo.tobytes(), RATE, 2, channels=2) is not None


def test_speech_ms_agrees_with_the_embedding():
    pcm = OWNER.utterance(seconds=2.0, seed=51)
    assert speech_ms(pcm) == pytest.approx(embed(pcm).speech_ms, rel=1e-9)


def test_embedding_cost_is_within_budget():
    """The verifier runs inside a turn. The docstring promises roughly 130 ms
    of CPU per second of speech; this fails if that stops being true, because
    the pipeline's decision to overlap it with STT rests on it."""
    pcm = OWNER.utterance(seconds=3.0, seed=61)
    start = time.perf_counter()
    embed(pcm)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0 * 0.45, f"{elapsed * 1000:.0f} ms for 3 s of audio"


def test_long_audio_is_sampled_across_its_whole_length():
    """The frame cap must not become "the first six seconds"; a profile built
    from a prefix is a profile of a throat-clear."""
    long_utterance = OWNER.utterance(seconds=20.0, seed=71)
    result = embed(long_utterance)
    assert result is not None
    assert result.voiced_frames <= 400
    # Same speaker, ordinary length: the two must still land on each other.
    short = embed(OWNER.utterance(seconds=2.5, seed=71))
    profile = VoiceProfile.enrol([embed(OWNER.utterance(**kwargs)) for kwargs in _ENROL])
    assert profile.verify(result).score < profile.verify(short).score * 3 + 3


# --- the profile ------------------------------------------------------------
def test_enrolment_demands_enough_samples():
    with pytest.raises(SpeakerError):
        VoiceProfile.enrol([embed(OWNER.utterance(seconds=2.0, seed=index)) for index in range(2)])


def test_enrolment_rejects_a_wrong_sized_vector():
    with pytest.raises(SpeakerError):
        VoiceProfile.enrol([(0.0, 1.0)] * MIN_ENROLMENT_SAMPLES)


def test_add_keeps_the_most_recent_samples():
    profile = VoiceProfile.enrol([embed(OWNER.utterance(**kwargs)) for kwargs in _ENROL])
    for index in range(MAX_ENROLMENT_SAMPLES + 5):
        profile.add(embed(OWNER.utterance(seconds=2.0, seed=900 + index)))
    assert len(profile.samples) == MAX_ENROLMENT_SAMPLES


def test_the_owner_is_accepted(owner_profile, owner_held_out):
    threshold = owner_profile.suggested_threshold()
    scores = [owner_profile.verify(item).score for item in owner_held_out]
    assert max(scores) <= threshold, (
        f"the owner's own worst utterance ({max(scores):.2f}) is above the "
        f"threshold enrolment suggested ({threshold:.2f})"
    )


@pytest.mark.parametrize("name", [speaker.name for speaker in IMPOSTORS])
def test_impostors_are_rejected(owner_profile, impostor_embeddings, name):
    """None of them gets in, by whichever route: scoring above the threshold,
    or being unverifiable and therefore refused."""
    profile = VoiceProfile(samples=list(owner_profile.samples),
                           threshold=owner_profile.suggested_threshold())
    verdicts = [profile.verify(item) for item in impostor_embeddings[name]]
    accepted = [verdict for verdict in verdicts if verdict.accepted]
    assert not accepted, (
        f"{name} got in {len(accepted)}/{len(verdicts)} times; best score "
        f"{min(verdict.score for verdict in verdicts):.2f} against threshold "
        f"{profile.threshold:.2f}"
    )


def test_the_separation_margin_is_what_the_docs_claim(
    owner_profile, owner_held_out, impostor_embeddings
):
    """The numbers quoted in the module docstring and in docs/voice-identity.md.

    Asserted as a floor rather than an equality — a change that *improves*
    separation should not fail — but the floor is close enough to the measured
    values that a regression cannot hide under it.
    """
    owner_worst = max(owner_profile.verify(item).score for item in owner_held_out)
    impostor_best = min(
        owner_profile.verify(item).score
        for items in impostor_embeddings.values()
        for item in items
        if item.has_pitch  # the rest are refused outright, not on their score
    )
    assert owner_worst < impostor_best, (
        f"owner worst {owner_worst:.2f} overlaps impostor best {impostor_best:.2f}"
    )
    assert impostor_best / owner_worst > 1.2


def test_the_nearest_impostor_is_the_one_we_think(owner_profile):
    """Documents which case is hard. Among the speakers whose pitch is actually
    measurable, the baritone — same pitch, different tract — is the one the
    score has to work for. If a feature change makes the soprano the close one,
    something has broken in pitch."""
    by_speaker = {}
    for speaker in IMPOSTORS:
        # Only utterances whose pitch was measurable: the rest are refused for
        # being unverifiable, so their score never decides anything.
        scores = [
            owner_profile.verify(item).score
            for item in (embed(sample) for sample in samples_for(speaker, 4, start=50, seconds=2.5))
            if item.has_pitch
        ]
        if scores:
            by_speaker[speaker.name] = min(scores)
    assert min(by_speaker, key=by_speaker.get) == "baritone"


def test_pitch_alone_does_not_decide_it(owner_profile):
    """A speaker who matches the owner's pitch and nothing else must still be
    refused — otherwise this is a pitch detector wearing a biometric's name."""
    scores = [
        owner_profile.verify(embed(sample))
        for sample in samples_for(IMPOSTORS[1], 4, start=50, seconds=2.5)
    ]
    assert all(not verdict.accepted for verdict in scores)
    assert statistics.mean(verdict.blocks["timbre"] for verdict in scores) > 5.0


# --- failing closed ---------------------------------------------------------
@pytest.mark.parametrize(
    "profile,candidate,reason",
    [
        (lambda p: p, None, "no-speech"),
        (lambda p: VoiceProfile(), (0.0,) * EMBEDDING_DIMS, "not-enrolled"),
    ],
)
def test_verify_fails_closed(owner_profile, profile, candidate, reason):
    verdict = profile(owner_profile).verify(candidate)
    assert verdict.accepted is False
    assert verdict.reason == reason
    assert verdict.confidence == 0.0


def test_a_wrong_sized_vector_is_refused_not_padded(owner_profile):
    verdict = owner_profile.verify((0.1,) * (EMBEDDING_DIMS - 1))
    assert verdict.accepted is False
    assert verdict.reason == "dimension-mismatch"


def test_a_vector_from_another_embedder_is_refused(owner_profile):
    """Comparing an ECAPA embedding against an MFCC profile yields a number,
    not an error, and the number looks like a match about half the time."""
    foreign = Embedding(
        vector=owner_profile.mean,
        speech_ms=1000.0,
        frames=60,
        voiced_frames=60,
        embedder="ecapa-tdnn-v2",
    )
    verdict = owner_profile.verify(foreign)
    assert verdict.accepted is False
    assert verdict.reason == "embedder-mismatch"


def test_the_centre_of_the_profile_is_accepted(owner_profile):
    """A sanity floor: whatever else changes, the enrolled centroid itself
    must match, or the arithmetic is upside-down."""
    verdict = owner_profile.verify(owner_profile.mean)
    assert verdict.accepted is True
    assert verdict.score < 1.0
    assert verdict.similarity > 0.99


# --- thresholds -------------------------------------------------------------
def test_the_suggested_threshold_clears_every_enrolment_sample(owner_profile):
    assert owner_profile.suggested_threshold() > max(owner_profile.self_scores())


def test_the_suggestion_is_built_from_the_worst_sample_not_the_mean(owner_profile):
    """The distinction the docstring argues for. A mean-based suggestion was
    measured refusing about one owner utterance in ten."""
    scores = owner_profile.self_scores()
    assert max(scores) > statistics.mean(scores)
    assert owner_profile.suggested_threshold() > statistics.mean(scores) * 1.25


@pytest.mark.parametrize("value", [0.0, -5.0, float("nan"), float("inf"), "banana", None, 10**9])
def test_a_nonsense_threshold_falls_back_to_something_bounded(value):
    profile = VoiceProfile.enrol(
        [embed(OWNER.utterance(**kwargs)) for kwargs in _ENROL], threshold=value
    )
    assert 0 < profile.threshold <= MAX_THRESHOLD


# --- persistence ------------------------------------------------------------
def test_a_profile_round_trips(owner_profile):
    restored = VoiceProfile.from_dict(owner_profile.as_dict())
    assert restored is not None
    assert restored.samples == owner_profile.samples
    assert restored.threshold == owner_profile.threshold
    assert restored.mean == owner_profile.mean


@pytest.mark.parametrize(
    "payload", [None, {}, {"samples": "nope"}, {"samples": []}, {"samples": [[1.0, 2.0]]}]
)
def test_a_corrupt_profile_loads_as_nothing_rather_than_as_a_match(payload):
    assert VoiceProfile.from_dict(payload) is None


def test_the_summary_never_carries_the_voiceprint(owner_profile):
    """A voiceprint is biometric data. "Is somebody enrolled" must not also
    answer "what do they sound like"."""
    summary = owner_profile.summary()
    flattened = repr(summary)
    assert "samples" in summary and summary["samples"] == len(owner_profile.samples)
    for value in owner_profile.mean[:5]:
        assert f"{value:.6f}" not in flattened


# --- enrolment prompts ------------------------------------------------------
def test_the_prompts_actually_vary():
    """They are load-bearing, not decoration: a phrase set that does not move
    pitch or length teaches the profile that the owner never varies."""
    assert len(ENROLMENT_PROMPTS) >= MIN_ENROLMENT_SAMPLES
    assert any(prompt.endswith("?") for prompt in ENROLMENT_PROMPTS), "no question"
    lengths = [len(prompt) for prompt in ENROLMENT_PROMPTS]
    assert max(lengths) > min(lengths) * 1.4, "every prompt is the same length"
    assert len(set(ENROLMENT_PROMPTS)) == len(ENROLMENT_PROMPTS)


def test_a_narrow_enrolment_is_measurably_worse_than_a_broad_one():
    """The measurement behind ENROLMENT_PROMPTS, kept as a test so the claim
    in the docstring cannot rot."""
    narrow = VoiceProfile.enrol(
        [embed(OWNER.utterance(seconds=2.5, seed=index)) for index in range(5)]
    )
    broad = VoiceProfile.enrol([embed(OWNER.utterance(**kwargs)) for kwargs in _ENROL])

    held = [embed(OWNER.utterance(seconds=2.2, seed=300 + i, f0_scale=1.12)) for i in range(4)]
    impostor = [embed(sample) for sample in samples_for(IMPOSTORS[1], 4, start=50, seconds=2.5)]

    def margin(profile: VoiceProfile) -> float:
        owner_worst = max(profile.verify(item).score for item in held)
        impostor_best = min(profile.verify(item).score for item in impostor)
        return impostor_best / owner_worst

    assert margin(broad) > margin(narrow)


# ---------------------------------------------------------------------------
# A threshold nobody measured must not look like one somebody did
# ---------------------------------------------------------------------------
def _profile_of(count: int) -> VoiceProfile:
    """`count` distinct enrolment samples, deterministically."""
    import random

    rng = random.Random(count * 7717)
    return VoiceProfile(
        samples=[
            tuple(rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIMS)) for _ in range(count)
        ]
    )


def test_a_profile_at_the_minimum_reports_no_self_scores():
    """Leave-one-out means rebuilding the profile from the OTHERS, and that
    rebuilt profile has to clear MIN_ENROLMENT_SAMPLES itself or `verify`
    refuses to answer it.

    So at exactly the advertised minimum there is nothing to measure with. The
    guard was `len(rest) < 2` — one short of what `verify` requires — so every
    leave-one-out ran against a two-sample profile, `verify` returned
    `Verdict(score=inf, reason="not-enrolled")`, and the honest "cannot measure
    this yet" came out as three infinities dressed as measurements.
    """
    profile = _profile_of(MIN_ENROLMENT_SAMPLES)
    assert profile.enrolled, "the minimum must still be enough to verify against"
    assert profile.self_scores() == [], (
        "a profile at the minimum reported leave-one-out scores it cannot have"
    )
    assert profile.summary()["worst_self_score"] is None
    assert profile.summary()["threshold_measured"] is False


def test_one_more_sample_makes_it_measurable():
    profile = _profile_of(MIN_MEASURABLE_SAMPLES)
    scores = profile.self_scores()
    assert len(scores) == MIN_MEASURABLE_SAMPLES
    assert all(math.isfinite(value) for value in scores)
    assert profile.summary()["threshold_measured"] is True


def test_no_self_score_is_ever_non_finite():
    """`inf` is not a measurement, it does not survive strict JSON, and every
    consumer of these numbers formats them as one — the phone printed "∞" and
    the console printed "Infinity" beside the word "scores"."""
    import json

    for count in range(MIN_ENROLMENT_SAMPLES, MIN_ENROLMENT_SAMPLES + 4):
        summary = _profile_of(count).summary()
        assert all(math.isfinite(v) for v in summary["self_scores"]), count
        # strict: `allow_nan=False` is what a real HTTP encoder does.
        json.dumps(summary, allow_nan=False)


def test_an_unmeasured_suggestion_is_labelled_as_the_default():
    """The suggestion falls back to DEFAULT_THRESHOLD when there is nothing to
    build one from — which is correct, and was indistinguishable from a
    measured one on both surfaces. The owner was told to read the scores and
    then enforce; `threshold_measured` is what makes that possible."""
    unmeasured = _profile_of(MIN_ENROLMENT_SAMPLES).summary()
    assert unmeasured["suggested_threshold"] == pytest.approx(DEFAULT_THRESHOLD)
    assert unmeasured["threshold_measured"] is False

    measured = _profile_of(MIN_MEASURABLE_SAMPLES).summary()
    assert measured["threshold_measured"] is True
    assert measured["suggested_threshold"] != pytest.approx(DEFAULT_THRESHOLD), (
        "the measured suggestion happens to equal the default; pick another seed"
    )


def test_the_leave_one_out_guard_is_the_same_bar_verify_applies():
    """Two defences cover this bug — the guard, and a finiteness filter on the
    way out — and either one alone hides a revert of the other. That is worth
    having and worth being explicit about: this pins the guard itself, so the
    filter cannot quietly become the only thing holding the invariant up.

    The bar has to be `verify`'s own, because `verify` is what is called on the
    trimmed profile one line later. `2` is the number it was, and one short of
    the requirement is the whole defect.
    """
    import inspect

    source = inspect.getsource(VoiceProfile.self_scores)
    assert "if len(rest) < MIN_ENROLMENT_SAMPLES:" in source, (
        "the leave-one-out guard no longer matches what `verify` requires, so "
        "it can hand `verify` a profile that is not enrolled and read the "
        "refusal back as a score"
    )
    assert "math.isfinite(score)" in source, (
        "nothing stops a non-finite verdict reaching a JSON response again"
    )


# --- adaptive enrolment, and the attack it is shaped around ------------------
#
# "Keep learning my voice every time I speak" is the request, and the whole
# difficulty is that the obvious implementation — add every accepted turn —
# is template poisoning with extra steps. Each of these pins one guard.

def _owner_gate(*, adapt=True, mode="enforce", threshold=None):
    from jarvis.voice.speaker import SpeakerGate

    profile = VoiceProfile.enrol([embed(pcm) for pcm in samples_for(OWNER, 5)])
    if threshold is not None:
        profile.threshold = threshold
    else:
        profile.threshold = max(profile.suggested_threshold(), DEFAULT_THRESHOLD)
    gate = SpeakerGate(profile=profile, mode=mode)
    gate.adapt = adapt
    gate.adapt_min_interval = 0.0
    return gate


def test_enrolment_marks_its_own_samples_as_anchors():
    profile = VoiceProfile.enrol([embed(pcm) for pcm in samples_for(OWNER, 5)])
    assert profile.anchors == 5
    assert profile.adapted_samples == 0


def test_a_confident_turn_is_learned_from():
    gate = _owner_gate()
    before = len(gate.profile.samples)
    verdict = gate.check(samples_for(OWNER, 6)[5], RATE, 2)
    assert verdict.accepted, f"the owner was not accepted: {verdict.as_dict()}"
    assert len(gate.profile.samples) == before + 1, "a confident turn taught nothing"
    assert gate.profile.adapted_samples == 1
    assert gate.profile_dirty, "the caller was never told to save the profile"


def test_adaptation_is_off_unless_asked_for():
    gate = _owner_gate(adapt=False)
    before = len(gate.profile.samples)
    gate.check(samples_for(OWNER, 6)[5], RATE, 2)
    assert len(gate.profile.samples) == before
    assert not gate.profile_dirty


def test_a_turn_that_merely_scraped_past_teaches_nothing():
    """The guard that separates adaptation from poisoning.

    A threshold generous enough to accept an impostor's best attempt must not
    also be generous enough to LEARN from it. Set the bar so the owner's own
    utterance is accepted but sits above the margin, and nothing may be added.
    """
    gate = _owner_gate()
    pcm = samples_for(OWNER, 6)[5]
    probe = gate.profile.verify(embed(pcm, RATE, 2))
    assert math.isfinite(probe.score)

    # Accepted (threshold above the score), but not confidently: the score is
    # above margin x threshold.
    gate.profile.threshold = probe.score * 1.5
    gate.adapt_margin = 0.5          # margin x threshold = 0.75 x score
    gate._last_adapt = 0.0
    before = len(gate.profile.samples)

    verdict = gate.check(pcm, RATE, 2)
    assert verdict.accepted, "the setup is wrong if this was refused"
    assert len(gate.profile.samples) == before, (
        "a turn that only just passed was added to the profile — that is the "
        "step an impostor repeats to walk the gate open"
    )


def test_a_refused_turn_never_teaches():
    gate = _owner_gate()
    impostor = samples_for(IMPOSTORS[0], 1)[0]
    before = len(gate.profile.samples)
    verdict = gate.check(impostor, RATE, 2)
    if verdict.accepted:
        pytest.skip("this impostor is not separable at the measured threshold")
    assert len(gate.profile.samples) == before


def test_nothing_is_learned_while_the_gate_is_off():
    """`off` means nobody is watching the scores, so acceptance means nothing."""
    gate = _owner_gate(mode="off")
    before = len(gate.profile.samples)
    gate.check(samples_for(OWNER, 6)[5], RATE, 2)
    assert len(gate.profile.samples) == before


def test_the_rate_limit_holds_a_burst_to_one_sample():
    gate = _owner_gate()
    gate.adapt_min_interval = 600.0
    gate._last_adapt = 0.0
    before = len(gate.profile.samples)
    for pcm in samples_for(OWNER, 9)[5:]:
        gate.check(pcm, RATE, 2)
    assert len(gate.profile.samples) == before + 1, (
        "the rate limit let a burst of turns become a burst of learning"
    )


def test_deliberate_enrolment_survives_a_lifetime_of_adaptation():
    """The anchor guarantee, driven past the cap.

    Without anchors, oldest-out eviction means a profile that keeps learning
    eventually contains not one sample a person actually read out.
    """
    profile = VoiceProfile.enrol([embed(pcm) for pcm in samples_for(OWNER, 5)])
    originals = list(profile.samples)
    for index in range(MAX_ENROLMENT_SAMPLES * 3):
        vector = tuple(float(index + dim) for dim in range(EMBEDDING_DIMS))
        profile.add(vector, anchor=False)

    assert len(profile.samples) <= MAX_ENROLMENT_SAMPLES
    assert profile.anchors == 5
    for vector in originals:
        assert vector in profile.samples, (
            "an enrolled sample was evicted by adaptation; the profile can now "
            "consist entirely of what it taught itself"
        )


def test_an_older_profile_treats_everything_in_it_as_deliberate():
    """Upgrading must not turn somebody's enrolment into evictable material."""
    legacy = {
        "samples": [list(vec) for vec in _profile_of(5).samples],
        "threshold": DEFAULT_THRESHOLD,
    }
    restored = VoiceProfile.from_dict(legacy)
    assert restored.anchors == 5
    assert restored.adapted_samples == 0


def test_anchors_survive_a_save_and_load():
    profile = VoiceProfile.enrol([embed(pcm) for pcm in samples_for(OWNER, 5)])
    profile.add(tuple(0.5 for _ in range(EMBEDDING_DIMS)), anchor=False)
    restored = VoiceProfile.from_dict(profile.as_dict())
    assert restored.anchors == 5
    assert restored.adapted_samples == 1


def test_block_limits_sit_between_the_threshold_and_the_old_ceiling(owner_profile):
    """M105, the nineteenth house: the veto line per block is the owner's own
    spread, never below the threshold and never above BLOCK_VETO × it."""
    from jarvis.voice.speaker import BLOCK_VETO

    from jarvis.voice.speaker import BLOCK_FLOOR, BLOCK_HEADROOM

    limits = owner_profile.block_limits()
    spreads = owner_profile.block_spreads()
    assert set(limits) == set(spreads) and len(limits) >= 2
    for name, limit in limits.items():
        assert owner_profile.threshold * BLOCK_FLOOR <= limit <= owner_profile.threshold * BLOCK_VETO, (name, limit)
        assert limit >= min(spreads[name] * BLOCK_HEADROOM, owner_profile.threshold * BLOCK_VETO) - 1e-9
    # In this cast the pitch line is tighter than the old fixed one: the owner's
    # pitch barely moves, so a pitch far out is refused sooner than 2× the threshold.
    assert limits["pitch"] < owner_profile.threshold * BLOCK_VETO


def test_the_owner_is_not_refused_by_their_own_spread(owner_profile):
    """Leave-one-out with the veto on must accept every owner sample the
    composite accepts: the limits are built from these very samples plus
    headroom, so a refusal here would be the gate refusing its owner."""
    from jarvis.voice.speaker import VoiceProfile

    samples = owner_profile.samples
    assert len(samples) >= 4
    for index, held_out in enumerate(samples):
        rest = samples[:index] + samples[index + 1 :]
        trimmed = VoiceProfile(samples=list(rest), threshold=owner_profile.threshold)
        plain = trimmed.verify(held_out, veto=False)
        with_veto = trimmed.verify(held_out)
        if plain.accepted:
            assert with_veto.accepted, (index, with_veto.reason, with_veto.blocks, trimmed.block_limits())


def test_an_impostor_inside_the_composite_but_outside_one_block_is_refused_by_that_block(owner_profile):
    """The nineteenth house's case, in the cast: an utterance whose mean of
    three blocks squeaks under the threshold while one block lies beyond
    anything the owner ever did. Built rather than found — the cast has no
    speaker that close — from the owner's own mean with one block pushed out."""
    from jarvis.voice.speaker import _block_of

    limits = owner_profile.block_limits()
    dims = len(owner_profile._mean)
    pitch_dims = [i for i in range(dims) if _block_of(i) == "pitch"]
    assert pitch_dims, "the cast's embedding has a pitch block"
    # Start from the owner's centre (score ~0) and push the pitch block out
    # until it scores between its limit and the old fixed line; the other two
    # blocks stay at zero, so the composite — a third of the pitch score —
    # sits under the threshold. Found by bisection, because a block's score
    # is not linear in the push.
    target = (limits["pitch"] + owner_profile.threshold * 2.0) / 2

    def pushed(scale: float) -> tuple[float, ...]:
        vector = list(owner_profile._mean)
        for i in pitch_dims:
            vector[i] += (owner_profile._std[i] or 1e-3) * scale
        return tuple(vector)

    low, high = 0.0, 64.0
    for _ in range(60):
        mid = (low + high) / 2
        if owner_profile.verify(pushed(mid), veto=False).blocks["pitch"] < target:
            low = mid
        else:
            high = mid
    vector = pushed(high)
    verdict = owner_profile.verify(vector)
    assert limits["pitch"] < verdict.blocks["pitch"] < owner_profile.threshold * 2.0, verdict.blocks
    assert verdict.score <= owner_profile.threshold, (verdict.score, owner_profile.threshold)
    assert not verdict.accepted and verdict.reason == "pitch-mismatch", verdict
    # The same vector with the veto off is exactly the nineteenth's false accept.
    assert owner_profile.verify(vector, veto=False).accepted
