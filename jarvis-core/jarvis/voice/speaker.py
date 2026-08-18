"""Speaker verification: is the person talking the one Jarvis belongs to?

Jarvis can unlock doors, send messages and run shell commands. Until now the
only thing standing between a voice and all of that was possession of the room
— anyone within earshot of a satellite, or holding the phone, was the owner as
far as the pipeline was concerned. This module answers the other question, on
the audio itself, before the intent stage ever sees the words.

    profile = VoiceProfile.enrol([embed(wav) for wav in samples])
    verdict = profile.verify(embed(pcm))
    verdict.accepted   # -> False, and the turn stops here

## What this is, and what it is not

It is a **classical, text-independent verifier**: MFCC statistics and a pitch
distribution, compared against an enrolled profile with a per-dimension z-test.
It is not a neural speaker embedder. An ECAPA-TDNN trained on thousands of
speakers is markedly better at this and if you have somewhere to run one you
should — :class:`Embedder` is the seam, and a backend that returns a vector of
floats drops straight in.

What the classical route buys is that it runs in the process that is already
running, with no model file, no GPU and no new dependency, on the same Pi that
is already doing STT. That is the difference between a feature that is on and a
feature that is documented. It costs roughly 130 ms of CPU per second of
speech, which is why it runs in a worker thread alongside the STT round trip
rather than after it — see :meth:`PipelineRun._verify_speaker`. The budget is
pinned by ``tests/test_speaker.py::test_embedding_cost_is_within_budget``.

## Enrolment has to cover your range, and the UI has to make it

This is the single biggest thing between a verifier that works and one that
locks you out. Enrol five calm, identical-sounding phrases and the profile
learns that the owner *never* varies; then the first time you ask a question,
or give a clipped order, or have a cold, the pitch block alone reads several
standard deviations out and the turn is refused.

Measured on ``tests/synth_voice.py``: with five same-pitch enrolment samples
the owner's own held-out utterances overlap the nearest impostor's. With five
that vary in length, level and pitch — the same speaker, the same five
utterances' worth of effort — the owner's worst held-out score is 7.6 and the
nearest impostor's best is 9.3, and nothing overlaps. So
:data:`ENROLMENT_PROMPTS` is not decoration: the phrases are chosen to move
pitch and length, and both enrolment surfaces read them from here.

## Why a z-test and not a cosine threshold

Cosine similarity between two utterances of ordinary speech sits around 0.95
whoever is talking, so an absolute cosine threshold is a magic number that
means nothing until it is tuned per microphone, and silently means nothing
afterwards. The enrolment set already contains the answer: how much *this*
speaker varies between utterances, per dimension. So the score is the mean
squared z-score against that spread, and the threshold is in units of standard
deviations, which is a number that keeps its meaning when the room changes.

**Read the threat model before trusting it.** This raises the cost of talking
to Jarvis from "be in the room" to "sound like the owner to a spectral
matcher". It stops a house guest, a television and a stranger at the window. It
does **not** stop a recording of your voice, and it is not a second factor for
anything that matters — the tier system and its human approval gate are still
what stand in front of the dangerous verbs. See ``docs/security.md``.

Per-dimension variance from five enrolment samples is a noisy estimate, so it
is shrunk toward a prior estimated from the block it sits in (see
:func:`_block_scales`) with :data:`_PRIOR_WEIGHT` pseudo-observations. Without
that, a dimension that happened to be identical across five phrases gets a
near-zero denominator and one feature vetoes the whole match.

Deviations are *not* clipped, though the obvious robustification says they
should be. It was tried: clipping each dimension's squared z at 25 pulls the
owner's worst case down from 7.6 to 3.9 and the nearest impostor's best from
9.3 to 3.5 — it compresses the impostor harder than the owner and destroys the
separation it was meant to protect. An impostor differs on many dimensions at
once, which is exactly the signal a clip throws away.
"""

from __future__ import annotations

import logging
import math
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Protocol

from .audio import DEFAULT_RATE, DEFAULT_WIDTH, pcm_from_wav
from .dsp import (
    MEL_BANDS,
    N_FFT,
    autocorrelation,
    dct2,
    hann_window,
    log_mel,
    mel_filterbank,
    power_spectrum,
    pre_emphasis,
)

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "EMBEDDING_DIMS",
    "ENROLMENT_PROMPTS",
    "Embedder",
    "Embedding",
    "MAX_ENROLMENT_SAMPLES",
    "MIN_ENROLMENT_SAMPLES",
    "MIN_MEASURABLE_SAMPLES",
    "MIN_SPEECH_MS",
    "SpeakerError",
    "Verdict",
    "VoiceProfile",
    "embed",
    "embed_wav",
    "speech_ms",
]

# --- analysis geometry ------------------------------------------------------
HOP = N_FFT // 2  # 16 ms at 16 kHz

#: Cepstral coefficients kept from each frame, c0 included and then dropped.
#: c0 is total log energy — how loud you were and how far from the microphone,
#: which is the one thing about a frame that says nothing about who produced it.
_MFCC_COUNT = 20
_MFCC_DIMS = _MFCC_COUNT - 1

#: Log-F0 histogram bins, spanning :data:`_F0_MIN` to :data:`_F0_MAX`. Pitch is
#: the strongest single cue a classical verifier has, and a histogram (rather
#: than a mean) is what survives the fact that people's pitch moves while they
#: talk — a question ends higher than a statement, and that must not read as a
#: different person.
_PITCH_BINS = 8
#: The bottom of the range is bounded by the analysis window, not by anatomy:
#: the autocorrelation only reaches lag N_FFT/2, which at 16 kHz is 62.5 Hz.
#: Asking for 60 would give the lowest histogram bin no lag that could ever
#: fill it — a dimension that is structurally zero and reads as agreement with
#: everyone. Rounded up to 65 so the whole declared range is reachable.
_F0_MIN = 65.0
_F0_MAX = 400.0

EMBEDDING_DIMS = _MFCC_DIMS * 2 + _PITCH_BINS  # 19 + 19 + 8 = 46

#: Blocks of the embedding, for the priors and for human-readable diagnostics.
_BLOCKS = (
    ("timbre", 0, _MFCC_DIMS),
    ("variability", _MFCC_DIMS, _MFCC_DIMS * 2),
    ("pitch", _MFCC_DIMS * 2, EMBEDDING_DIMS),
)

#: Floor under a block's prior standard deviation. These are *degeneracy
#: guards*, not calibration: they stop a dimension that came out identical
#: across every enrolment sample from dividing by zero and vetoing every
#: subsequent match on its own. The prior itself is estimated from the
#: enrolment set — see :func:`_block_scales` — because a hard-coded one is a
#: number tuned on somebody else's microphone, and this one is tuned on yours.
_PRIOR_FLOOR = {"timbre": 0.004, "variability": 0.002, "pitch": 0.004}
#: Pseudo-observations behind the prior. Three is deliberately comparable to the
#: five samples enrolment asks for: at that ratio the prior dominates a
#: dimension nobody has evidence about and yields to one where five samples
#: genuinely agree.
_PRIOR_WEIGHT = 3.0

#: Scale factor bringing raw MFCCs into roughly unit range, so a single prior
#: per block is meaningful. Orthonormal DCT-II of natural-log mel energies
#: gives coefficients around ±10.
_MFCC_SCALE = 0.1

#: A frame is speech if its energy is within this many dB of the loudest frame
#: in the utterance AND above the absolute floor. Relative alone lets a silent
#: recording's noise floor become "speech"; absolute alone fails on a quiet
#: talker or a distant microphone.
_RELATIVE_FLOOR_DB = 32.0
_ABSOLUTE_FLOOR_DB = -62.0

#: Frames actually analysed. 400 frames is 6.4 s of speech, well past the point
#: where more audio stops improving the estimate; a long dictation is sampled
#: across its whole length rather than truncated, so the profile is not built
#: from whatever the first six seconds happened to sound like.
_MAX_FRAMES = 400

#: Pitch is estimated on every Nth analysed frame. Autocorrelation costs a
#: second FFT per frame and F0 does not change meaningfully in 32 ms.
_PITCH_STRIDE = 3

#: The shortest utterance that produces a decision at all. Below this the
#: verdict is "unknown", never "accepted" — three frames of "yes" carry no
#: pitch distribution and barely a spectral average.
MIN_SPEECH_MS = 400.0

#: Enrolment samples required before a profile will verify anything. Fewer than
#: this and the per-dimension variance is guesswork wearing a decimal point.
MIN_ENROLMENT_SAMPLES = 3
MAX_ENROLMENT_SAMPLES = 20

#: Samples needed before the owner's own score can be MEASURED, as opposed to
#: merely verified against.
#:
#: One more than the minimum, and it is arithmetic rather than a policy: the
#: leave-one-out estimate scores each sample against a profile built from the
#: others, and that profile must itself clear :data:`MIN_ENROLMENT_SAMPLES` or
#: `verify` refuses to answer it. So four samples is the first number that
#: produces a real score, and at three the honest answer is "not yet" — not a
#: list of infinities, and not `DEFAULT_THRESHOLD` wearing the same clothes a
#: measurement would.
MIN_MEASURABLE_SAMPLES = MIN_ENROLMENT_SAMPLES + 1

#: What enrolment asks you to say, in order, and why each one is there.
#:
#: Not filler. The profile's denominator is how much the owner varies, so a
#: phrase set that does not vary teaches the profile that the owner never does
#: — see the module docstring for the measurement. Each line moves something:
#: length, pitch contour, or the clipped delivery of an actual command.
#:
#: Both enrolment surfaces (`/api/voice/speaker/enrol` callers, and the phone's
#: own screen) read this list, so the phrases cannot drift apart between them.
#: `tests/test_speaker.py` and `android-app/tools/voiceprint_parity_test.py`
#: both assert the Kotlin copy still matches.
ENROLMENT_PROMPTS: tuple[str, ...] = (
    # Ten, not five, and the order matters: the first five are the set this
    # shipped with, so an existing profile enrolled against them is still
    # enrolled against the same phrases at the same indexes.
    #
    # The additions are not filler. The score is a per-dimension z against the
    # spread of the enrolment set, so what the set has to contain is the RANGE
    # of the owner's ordinary speech — a profile built from five level,
    # similar-length sentences has a narrow spread, and every dimension is then
    # a hair-trigger that rejects the owner for having a cold or standing
    # further away. Each of these moves something specific:
    #
    #   question intonation, a rising contour the statements do not have;
    #   a long sentence, for sustained vowels and breath;
    #   a short clipped one, which is what most real commands actually are;
    #   counting, for steady prosody with no semantic stress;
    #   plosives and sibilants, which the spectral features respond to most;
    #   a quiet-register line, because people ask for the lights low quietly.
    "Good evening, Jarvis. Bring the house up, would you?",
    "What is on my calendar tomorrow morning?",
    "Lock the front door and turn everything off.",
    "One, two, three, four, five, six, seven, eight, nine, ten.",
    "It has been a long day and I would like the lights low, please.",
    "Is the garage still open?",
    "Play something quiet in the kitchen and turn the hallway light down to about a third.",
    "Stop.",
    "Pack the parcels, book the taxi, and set a timer for fifty-five minutes.",
    "Tell me the temperature upstairs, then remind me at six to close the blinds.",
)

#: Default accept threshold, in mean-squared-z. 4.0 is "two standard deviations
#: from the enrolled centre, averaged over 46 dimensions". Tune it in `observe`
#: mode against your own voice and your own room before turning enforcement on;
#: `docs/voice-identity.md` says how.
DEFAULT_THRESHOLD = 4.0
MIN_THRESHOLD = 1.0
MAX_THRESHOLD = 25.0


class SpeakerError(Exception):
    """Enrolment or verification could not be done at all."""


class Embedder(Protocol):
    """Anything that turns PCM into a fixed-length vector of floats.

    The seam for replacing the classical features with a neural embedder. A
    profile records the ``embedder`` that produced it and refuses to verify a
    vector from a different one, because comparing an ECAPA embedding against
    an MFCC profile produces a number rather than an error, and the number
    looks like a match roughly half the time.
    """

    name: str

    def __call__(self, pcm: bytes, rate: int = DEFAULT_RATE, width: int = DEFAULT_WIDTH) -> "Embedding | None":
        ...  # pragma: no cover - protocol


#: Frames that must have yielded a pitch before the pitch block counts as
#: measured. Below this the histogram is a handful of samples of a quantity
#: that moves constantly, which is noise wearing a distribution's shape.
MIN_PITCH_FRAMES = 5


@dataclass(frozen=True, slots=True)
class Embedding:
    """One utterance, reduced to something comparable."""

    vector: tuple[float, ...]
    speech_ms: float
    frames: int
    voiced_frames: int
    #: How many frames actually produced an F0. Zero for a whisper, for an
    #: all-fricative utterance, and for a capture so quiet that quantisation
    #: noise has swamped the periodicity.
    pitch_frames: int = 0
    embedder: str = "jarvis-mfcc-v1"

    @property
    def has_pitch(self) -> bool:
        """Whether the pitch block carries evidence.

        The distinction that matters: "I could not measure your pitch" is not
        "your pitch is wrong". When this is False the flat fallback histogram
        in :attr:`vector` is a placeholder, and :meth:`VoiceProfile.verify`
        drops those dimensions rather than scoring them — otherwise whispering,
        or being recorded quietly, reads as a different person.

        It also never reads as the *right* person: an utterance with no
        measurable pitch is refused whatever its timbre score, because a block
        that can be switched off by whispering is a block an impostor switches
        off. See :meth:`VoiceProfile.verify`.
        """
        return self.pitch_frames >= MIN_PITCH_FRAMES

    def as_dict(self) -> dict[str, Any]:
        return {
            "vector": list(self.vector),
            "speech_ms": round(self.speech_ms, 1),
            "frames": self.frames,
            "voiced_frames": self.voiced_frames,
            "pitch_frames": self.pitch_frames,
            "has_pitch": self.has_pitch,
            "embedder": self.embedder,
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    """The answer, and enough of the working to argue with it."""

    accepted: bool
    #: Mean squared z-score against the profile. Lower is more like the owner.
    score: float
    #: Threshold this was compared against, in the same units.
    threshold: float
    #: Cosine similarity, for display only. Never gates anything — see the
    #: module docstring for why it cannot.
    similarity: float
    #: 0..1, monotone in the score. For a progress bar, not for a decision.
    confidence: float
    reason: str
    #: Per-block mean squared z, so "it was the pitch" is answerable.
    blocks: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe, which for this type means non-finite floats become null.

        `verify(None)` returns `score=inf` for anything under
        :data:`MIN_SPEECH_MS` — "stop", "yes", a cough — and this dict goes
        onto the pipeline event bus and out of the websocket as a
        `speaker-end` frame. `json.dumps` writes `Infinity` for that, which is
        not JSON: strict parsers reject the whole frame, and this API
        deliberately mimics `assist_pipeline/run`, so a third-party client
        with a strict parser is a legitimate client rather than a hypothetical
        one.

        The same invariant is already enforced on the way IN — the websocket
        layer refuses inbound frames containing the bare constants — so a
        server that emits them outbound was contradicting its own rule.
        """
        return {
            "accepted": self.accepted,
            "score": _finite(self.score),
            "threshold": _finite(self.threshold),
            "similarity": _finite(self.similarity),
            "confidence": _finite(self.confidence),
            "reason": self.reason,
            "blocks": {k: _finite(v) for k, v in self.blocks.items()},
        }


def _finite(value: float) -> float | None:
    """`round(value, 4)`, or None when the value is not a real number.

    None rather than a sentinel like -1 or 1e308: a client drawing a score has
    to be able to tell "there was no score" from "the score was enormous", and
    every JSON parser in the world agrees on null.
    """
    return round(value, 4) if math.isfinite(value) else None


# --- feature extraction -----------------------------------------------------
def _int16_samples(pcm: bytes, width: int) -> array:
    """PCM bytes to floats in roughly [-1, 1]."""
    if width == 2:
        values = array("h")
        usable = len(pcm) - (len(pcm) % 2)
        values.frombytes(bytes(pcm[:usable]))
        return array("d", (value / 32768.0 for value in values))
    if width == 1:
        return array("d", ((byte - 128) / 128.0 for byte in pcm))
    if width == 4:
        values = array("i")
        usable = len(pcm) - (len(pcm) % 4)
        values.frombytes(bytes(pcm[:usable]))
        return array("d", (value / 2147483648.0 for value in values))
    raise SpeakerError(f"unsupported sample width: {width}")


def _normalise(power: array) -> None:
    """Scale a frame's power spectrum to unit total energy, in place.

    This is what makes the embedding genuinely level-invariant, and dropping c0
    is not enough on its own. Multiplying the signal by *k* multiplies every mel
    band by *k²*, which adds a constant to every log-mel value and therefore
    lands entirely in c0 — so far so good. But :data:`~jarvis.voice.dsp._LOG_FLOOR`
    is an absolute floor: turn the gain down by 20 dB and the quietest bands
    slide under it, get clamped, and the *shape* of the log-mel vector changes.
    Then c1..c19 move too, and the same person at arm's length stops matching
    the same person at the microphone.

    Caught by ``test_embedding_ignores_level``, which measured a 0.94 shift
    across a 10x gain change before this existed — larger than the distance
    between two different people.
    """
    total = 0.0
    for value in power:
        total += value
    if total <= 0.0:
        return
    scale = 1.0 / total
    for index in range(len(power)):
        power[index] *= scale


def _frame_energy_db(frame: array) -> float:
    total = 0.0
    for value in frame:
        total += value * value
    if total <= 0.0:
        return -200.0
    return 10.0 * math.log10(total / len(frame) + 1e-20)


def _estimate_f0(power: array, rate: int) -> float:
    """Autocorrelation pitch for one frame, or 0.0 if it looks unvoiced.

    Wiener-Khinchin gives the whole autocorrelation for the price of one more
    FFT, which is cheaper and far more accurate at these lags than picking a
    peak out of a 31 Hz-resolution spectrum. The peak is parabolically
    interpolated because a lag is an integer and a pitch is not: at 200 Hz one
    sample of lag is 5 Hz, which would quantise the histogram into stripes.
    """
    r = autocorrelation(power, N_FFT)
    zero = r[0]
    if zero <= 0.0:
        return 0.0
    min_lag = max(2, int(rate / _F0_MAX))
    max_lag = min(N_FFT // 2 - 1, int(rate / _F0_MIN))
    if max_lag <= min_lag:
        return 0.0
    best_lag = 0
    best = 0.0
    for lag in range(min_lag, max_lag + 1):
        value = r[lag]
        if value > best:
            best = value
            best_lag = lag
    if best_lag == 0:
        return 0.0
    # A voiced frame repeats itself. 0.3 of the zero-lag energy is the usual
    # place to draw that line; below it the "period" is noise agreeing with
    # itself by chance, and feeding those into the histogram turns it into a
    # measurement of the room.
    if best < 0.30 * zero:
        return 0.0
    left = r[best_lag - 1]
    right = r[best_lag + 1] if best_lag + 1 <= max_lag else left
    denominator = left - 2.0 * best + right
    shift = 0.0 if denominator == 0.0 else 0.5 * (left - right) / denominator
    if not -1.0 < shift < 1.0:
        shift = 0.0
    return rate / (best_lag + shift)


def _pitch_bin_weights(f0: float) -> list[tuple[int, float]]:
    """Soft-assign an F0 to the log-spaced histogram.

    Soft rather than hard because a speaker whose pitch sits on a bin edge
    would otherwise have half their frames in each of two bins, and which half
    depends on the sentence. Linear interpolation between the two neighbouring
    bin centres keeps the histogram continuous in F0.
    """
    if f0 <= 0.0:
        return []
    low = math.log(_F0_MIN)
    high = math.log(_F0_MAX)
    position = (math.log(min(max(f0, _F0_MIN), _F0_MAX)) - low) / (high - low)
    exact = position * (_PITCH_BINS - 1)
    lower = int(math.floor(exact))
    upper = min(lower + 1, _PITCH_BINS - 1)
    fraction = exact - lower
    if lower == upper:
        return [(lower, 1.0)]
    return [(lower, 1.0 - fraction), (upper, fraction)]


def embed(
    pcm: bytes,
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    *,
    channels: int = 1,
) -> Embedding | None:
    """Reduce an utterance to a 46-dimensional vector, or `None` if it is not
    speech enough to reduce.

    `None` is a real answer and callers must handle it: it means "there was not
    enough voiced audio here to say anything about who was talking", which is
    the correct verdict for a cough, a door, or the word "no". It is never
    silently treated as a match.
    """
    if channels > 1:
        pcm = _downmix(pcm, width, channels)
    samples = _int16_samples(pcm, width)
    if len(samples) < N_FFT:
        return None

    emphasised = pre_emphasis(samples)
    window = hann_window(N_FFT)
    filters = mel_filterbank(rate, N_FFT, MEL_BANDS)

    # Pass one: frame energies, so the speech floor is relative to this
    # utterance rather than to a level somebody's microphone happens to hit.
    starts: list[int] = list(range(0, len(emphasised) - N_FFT + 1, HOP))
    if not starts:
        return None
    energies = [_frame_energy_db(emphasised[start : start + N_FFT]) for start in starts]
    peak = max(energies)
    floor = max(peak - _RELATIVE_FLOOR_DB, _ABSOLUTE_FLOOR_DB)
    voiced = [start for start, energy in zip(starts, energies) if energy >= floor]
    if not voiced:
        return None

    # Sample across the whole utterance rather than taking a prefix.
    if len(voiced) > _MAX_FRAMES:
        stride = len(voiced) / _MAX_FRAMES
        voiced = [voiced[int(index * stride)] for index in range(_MAX_FRAMES)]

    speech = len(voiced) * HOP * 1000.0 / rate
    if speech < MIN_SPEECH_MS:
        return None

    # Pass two: the features themselves, accumulated with Welford so a long
    # utterance never holds every frame's MFCCs in memory at once.
    count = 0
    means = [0.0] * _MFCC_DIMS
    m2 = [0.0] * _MFCC_DIMS
    pitch = [0.0] * _PITCH_BINS
    pitch_weight = 0.0
    pitch_frames = 0

    for index, start in enumerate(voiced):
        frame = array("d", emphasised[start : start + N_FFT])
        for position in range(N_FFT):
            frame[position] *= window[position]
        power = power_spectrum(frame, N_FFT)
        _normalise(power)
        mel = log_mel(power, filters)
        cepstrum = dct2(mel, _MFCC_COUNT)

        count += 1
        for dim in range(_MFCC_DIMS):
            value = cepstrum[dim + 1] * _MFCC_SCALE
            delta = value - means[dim]
            means[dim] += delta / count
            m2[dim] += delta * (value - means[dim])

        if index % _PITCH_STRIDE == 0:
            f0 = _estimate_f0(power, rate)
            weights = _pitch_bin_weights(f0)
            if weights:
                pitch_frames += 1
            for bin_index, weight in weights:
                pitch[bin_index] += weight
                pitch_weight += weight

    if count < 2:
        return None

    deviations = [math.sqrt(value / (count - 1)) for value in m2]
    if pitch_frames >= MIN_PITCH_FRAMES and pitch_weight > 0.0:
        pitch = [value / pitch_weight for value in pitch]
    else:
        # Not enough voiced frames to have measured anything — a whisper, a
        # fricative on its own, or a capture too quiet for the periodicity to
        # survive quantisation. Flat is a placeholder, and `has_pitch` is what
        # stops it being *scored* as a placeholder: see Embedding.has_pitch.
        pitch = [1.0 / _PITCH_BINS] * _PITCH_BINS

    vector = tuple(means) + tuple(deviations) + tuple(pitch)
    return Embedding(
        vector=vector,
        speech_ms=speech,
        frames=len(starts),
        voiced_frames=count,
        pitch_frames=pitch_frames,
    )


def _downmix(pcm: bytes, width: int, channels: int) -> bytes:
    """Average interleaved channels down to mono.

    Taking channel 0 instead would be cheaper and is what most code does; it
    is also how a stereo headset with the speech on the right becomes silence.
    """
    if width != 2:
        raise SpeakerError(f"cannot downmix {width}-byte samples")
    values = array("h")
    frame_bytes = 2 * channels
    usable = len(pcm) - (len(pcm) % frame_bytes)
    values.frombytes(bytes(pcm[:usable]))
    out = array("h", bytes(2 * (len(values) // channels)))
    for index in range(len(out)):
        total = 0
        base = index * channels
        for channel in range(channels):
            total += values[base + channel]
        out[index] = int(total / channels)
    return out.tobytes()


def embed_wav(wav: bytes) -> Embedding | None:
    """Embed a WAV container, whatever rate and width it declares."""
    pcm, rate, width, channels = pcm_from_wav(wav)
    return embed(pcm, rate, width, channels=channels)


def speech_ms(pcm: bytes, rate: int = DEFAULT_RATE, width: int = DEFAULT_WIDTH) -> float:
    """Voiced milliseconds in `pcm`, by the same floor :func:`embed` uses.

    Exposed so a caller can tell "too short to judge" from "judged and refused"
    before spending the rest of the embedding on it.
    """
    samples = _int16_samples(pcm, width)
    if len(samples) < N_FFT:
        return 0.0
    emphasised = pre_emphasis(samples)
    starts = list(range(0, len(emphasised) - N_FFT + 1, HOP))
    if not starts:
        return 0.0
    energies = [_frame_energy_db(emphasised[start : start + N_FFT]) for start in starts]
    peak = max(energies)
    floor = max(peak - _RELATIVE_FLOOR_DB, _ABSOLUTE_FLOOR_DB)
    return sum(1 for energy in energies if energy >= floor) * HOP * 1000.0 / rate


# --- the profile ------------------------------------------------------------
def _block_of(dimension: int) -> str:
    for name, start, end in _BLOCKS:
        if start <= dimension < end:
            return name
    return "timbre"  # pragma: no cover - unreachable while _BLOCKS spans dims


def _block_scales(variance: list[float], dims: int) -> dict[str, float]:
    """Typical within-speaker standard deviation for each block, from the
    enrolment set itself.

    This is the prior the per-dimension variance is shrunk toward, and
    estimating it here rather than hard-coding it is what makes the verifier
    portable. The three blocks have genuinely different natural scales — a
    cepstral mean, a cepstral spread and a normalised histogram bin are not
    comparable quantities — so one global number would either crush the pitch
    block or let the timbre block run away.

    The **median** rather than the mean: a single dimension that moved a lot in
    one enrolment sample would otherwise raise the prior for all nineteen of
    its neighbours, which is how one cough during enrolment makes the whole
    profile permissive.
    """
    scales: dict[str, float] = {}
    for name, start, end in _BLOCKS:
        deviations = sorted(
            math.sqrt(variance[dim]) for dim in range(start, min(end, dims))
        )
        floor = _PRIOR_FLOOR[name]
        if not deviations:
            scales[name] = floor
            continue
        middle = len(deviations) // 2
        median = (
            deviations[middle]
            if len(deviations) % 2
            else 0.5 * (deviations[middle - 1] + deviations[middle])
        )
        scales[name] = max(median, floor)
    return scales


@dataclass
class VoiceProfile:
    """The enrolled owner: a centre, a spread, and the samples behind both."""

    samples: list[tuple[float, ...]] = field(default_factory=list)
    threshold: float = DEFAULT_THRESHOLD
    label: str = "owner"
    embedder: str = "jarvis-mfcc-v1"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    #: How many leading samples were enrolled ON PURPOSE, by a person reading
    #: the prompts. They are never evicted.
    #:
    #: This is what stops adaptation (:attr:`SpeakerGate.adapt`) from becoming a
    #: way to walk the gate open. Eviction is oldest-first, so without anchors a
    #: profile that keeps learning eventually contains no deliberately enrolled
    #: sample at all — every one has been pushed out by a turn that merely
    #: scored well. Each individual step is small, which is exactly why the end
    #: state is reachable: this is template poisoning, and it does not need a
    #: single suspicious event to happen.
    #:
    #: A profile written before adaptation existed has no anchor count, and
    #: :meth:`from_dict` treats every sample in it as an anchor — everything
    #: enrolled back then was enrolled deliberately.
    anchors: int = 0

    _mean: tuple[float, ...] = field(default=(), repr=False)
    _std: tuple[float, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        self._recompute()

    # --- construction -------------------------------------------------------
    @classmethod
    def enrol(
        cls,
        embeddings: list[Embedding | tuple[float, ...]],
        *,
        threshold: float = DEFAULT_THRESHOLD,
        label: str = "owner",
    ) -> "VoiceProfile":
        vectors = [_vector_of(item) for item in embeddings]
        if len(vectors) < MIN_ENROLMENT_SAMPLES:
            raise SpeakerError(
                f"enrolment needs at least {MIN_ENROLMENT_SAMPLES} samples, got {len(vectors)}"
            )
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMS:
                raise SpeakerError(
                    f"enrolment vector has {len(vector)} dimensions, expected {EMBEDDING_DIMS}"
                )
        kept = vectors[-MAX_ENROLMENT_SAMPLES:]
        return cls(
            samples=kept,
            threshold=_sane_threshold(threshold),
            label=label,
            anchors=len(kept),
        )

    def add(self, embedding: Embedding | tuple[float, ...], *, anchor: bool = True) -> None:
        """Add one more sample, keeping the most recent
        :data:`MAX_ENROLMENT_SAMPLES`.

        Oldest-out rather than a running average, because a running average
        cannot be un-done: one enrolment recorded while you had a cold would
        otherwise be in the profile permanently.

        `anchor=False` is an ADAPTED sample — one the gate accepted confidently
        during an ordinary turn, rather than one a person read from the prompt
        list. Adapted samples are evicted first and anchors are never evicted,
        so a profile that has been learning for a year still contains the
        enrolment it started from. See :attr:`anchors`.
        """
        vector = _vector_of(embedding)
        if len(vector) != EMBEDDING_DIMS:
            raise SpeakerError(
                f"vector has {len(vector)} dimensions, expected {EMBEDDING_DIMS}"
            )
        anchors = min(self.anchors, len(self.samples))
        if anchor:
            # Deliberate samples sit at the front, so the anchor block stays
            # contiguous however many adapted ones are already present.
            self.samples.insert(anchors, vector)
            anchors += 1
        else:
            self.samples.append(vector)

        # Trim the ADAPTED tail only. An anchor block at the cap means a
        # profile that can no longer adapt, which is the correct outcome:
        # deliberate enrolment outranks learning.
        room = MAX_ENROLMENT_SAMPLES - anchors
        if room <= 0:
            del self.samples[anchors:]
            anchors = min(anchors, MAX_ENROLMENT_SAMPLES)
            del self.samples[MAX_ENROLMENT_SAMPLES:]
        elif len(self.samples) - anchors > room:
            # oldest adapted first
            drop = len(self.samples) - anchors - room
            del self.samples[anchors : anchors + drop]

        self.anchors = anchors
        self.updated = time.time()
        self._recompute()

    @property
    def adapted_samples(self) -> int:
        """Samples the gate learned by itself, rather than ones you read out."""
        return max(0, len(self.samples) - min(self.anchors, len(self.samples)))

    @property
    def enrolled(self) -> bool:
        return len(self.samples) >= MIN_ENROLMENT_SAMPLES

    @property
    def mean(self) -> tuple[float, ...]:
        return self._mean

    def _recompute(self) -> None:
        count = len(self.samples)
        if count == 0:
            self._mean = ()
            self._std = ()
            return
        dims = len(self.samples[0])
        mean = [0.0] * dims
        for vector in self.samples:
            for dim in range(dims):
                mean[dim] += vector[dim]
        mean = [value / count for value in mean]

        std = [0.0] * dims
        if count > 1:
            for vector in self.samples:
                for dim in range(dims):
                    delta = vector[dim] - mean[dim]
                    std[dim] += delta * delta
            std = [value / (count - 1) for value in std]

        # Shrink the sample variance toward a prior estimated from the block it
        # sits in. With five samples this is roughly a 5:3 blend; with three it
        # is nearly the prior, which is the honest weight for three
        # observations.
        scales = _block_scales(std, dims)
        shrunk: list[float] = []
        for dim in range(dims):
            prior = scales[_block_of(dim)]
            blended = (count * std[dim] + _PRIOR_WEIGHT * prior * prior) / (count + _PRIOR_WEIGHT)
            shrunk.append(math.sqrt(blended) if blended > 0 else prior)
        self._mean = tuple(mean)
        self._std = tuple(shrunk)

    # --- the decision -------------------------------------------------------
    def verify(self, embedding: Embedding | tuple[float, ...] | None) -> Verdict:
        """Compare one utterance against the profile.

        Fails closed everywhere it can fail: no profile, too few samples, a
        vector of the wrong length or from another embedder, or `None` because
        the audio was not speech, all return `accepted=False` with a reason
        that says which. A caller that wants "unknown" to be permissive has to
        ask for it explicitly by reading :attr:`Verdict.reason`, rather than
        getting it by forgetting to check.
        """
        if embedding is None:
            return Verdict(
                accepted=False,
                score=math.inf,
                threshold=self.threshold,
                similarity=0.0,
                confidence=0.0,
                reason="no-speech",
            )
        if isinstance(embedding, Embedding) and embedding.embedder != self.embedder:
            return Verdict(
                accepted=False,
                score=math.inf,
                threshold=self.threshold,
                similarity=0.0,
                confidence=0.0,
                reason="embedder-mismatch",
            )
        if not self.enrolled:
            return Verdict(
                accepted=False,
                score=math.inf,
                threshold=self.threshold,
                similarity=0.0,
                confidence=0.0,
                reason="not-enrolled",
            )
        vector = _vector_of(embedding)
        if len(vector) != len(self._mean):
            return Verdict(
                accepted=False,
                score=math.inf,
                threshold=self.threshold,
                similarity=0.0,
                confidence=0.0,
                reason="dimension-mismatch",
            )

        # A pitchless utterance is *scored* on timbre alone, and then refused
        # anyway. Both halves of that are deliberate and both were measured.
        #
        # Scoring it on timbre alone, because including the flat placeholder
        # histogram would score "I could not measure your pitch" as "your pitch
        # is wrong" — which is how whispering, or standing across the room,
        # becomes a different person.
        #
        # Refusing it anyway, because dropping a block is otherwise a way
        # through the gate. The synthetic cast has a breathy speaker whose
        # periodicity does not survive their own breath: five of their six
        # utterances yield no F0 at all, and on timbre alone they score 6.2
        # against a threshold of 9.0. They were accepted. A verifier that can
        # be defeated by whispering at it is not a verifier, so "I could not
        # measure your pitch" is its own answer — never a match — and the
        # policy layer decides what an unverifiable turn is allowed to do,
        # exactly as it does for a turn that was too short to judge.
        pitchless = isinstance(embedding, Embedding) and not embedding.has_pitch
        skipped = {"pitch"} if pitchless else set()

        totals: dict[str, list[float]] = {name: [0.0, 0.0] for name, _, _ in _BLOCKS}
        total = 0.0
        counted = 0
        for dim, value in enumerate(vector):
            block = _block_of(dim)
            z = (value - self._mean[dim]) / self._std[dim]
            squared = z * z
            bucket = totals[block]
            bucket[0] += squared
            bucket[1] += 1.0
            if block in skipped:
                continue
            total += squared
            counted += 1
        if not counted:  # pragma: no cover - only if every block were skipped
            return Verdict(
                accepted=False,
                score=math.inf,
                threshold=self.threshold,
                similarity=0.0,
                confidence=0.0,
                reason="nothing-measurable",
            )
        score = total / counted
        blocks = {name: (pair[0] / pair[1] if pair[1] else 0.0) for name, pair in totals.items()}
        close_enough = score <= self.threshold
        accepted = close_enough and not pitchless
        if not close_enough:
            reason = "mismatch"
        elif pitchless:
            reason = "unverifiable-no-pitch"
        else:
            reason = "match"
        return Verdict(
            accepted=accepted,
            score=score,
            threshold=self.threshold,
            similarity=_cosine(vector, self._mean),
            confidence=_confidence(score, self.threshold),
            reason=reason,
            blocks=blocks,
        )

    def self_scores(self) -> list[float]:
        """Leave-one-out score for each enrolment sample.

        What *this* speaker scores against their own profile, which is the only
        honest starting point for a threshold: set one below these and the
        first thing it rejects is the owner. The enrolment API returns them and
        the console draws them, because a biometric gate whose threshold was
        guessed is a gate that locks you out on the first cold morning.
        """
        if len(self.samples) < MIN_MEASURABLE_SAMPLES:
            return []
        scores: list[float] = []
        for index in range(len(self.samples)):
            held_out = self.samples[index]
            rest = self.samples[:index] + self.samples[index + 1 :]
            # The SAME bar `verify` applies, because `verify` is what is about
            # to be called on the trimmed profile. The guard used to be
            # `len(rest) < 2`, one short of it, and one short is the whole bug:
            # at exactly the advertised minimum of three samples every
            # leave-one-out left two behind, `trimmed.enrolled` was False, and
            # `verify` answered `Verdict(score=inf, reason="not-enrolled")`
            # rather than refusing to answer.
            #
            # So a profile with three samples reported `self_scores` of
            # `[inf, inf, inf]`, a `worst_self_score` of `inf`, and — because
            # `suggested_threshold` falls back on an empty-or-useless list —
            # DEFAULT_THRESHOLD, presented in the console and on the phone
            # exactly as a measured number would be. The owner then turned
            # enforcement on against a threshold nothing had measured.
            if len(rest) < MIN_ENROLMENT_SAMPLES:
                continue
            trimmed = VoiceProfile(samples=list(rest), threshold=self.threshold)
            score = trimmed.verify(held_out).score
            # Nothing non-finite may leave here. It is not a measurement, it
            # does not survive JSON, and every consumer treats a float as one.
            if math.isfinite(score):
                scores.append(score)
        return scores

    def self_score(self) -> float:
        """Mean leave-one-out score — the headline number, for display."""
        scores = self.self_scores()
        return sum(scores) / len(scores) if scores else math.inf

    def suggested_threshold(self, headroom: float = 1.25) -> float:
        """A threshold with room for a day the owner sounds different.

        Built from the **worst** leave-one-out sample rather than the mean, and
        that is the whole point: the mean says how the owner usually sounds,
        and a gate is not troubled by the usual case. The worst sample is the
        one legitimate utterance that landed furthest from the centre, and the
        threshold has to clear it or that kind of utterance never gets through.

        On the synthetic cast the mean is 2.5 and the worst is 7.1, so a
        mean-based suggestion would have sat at 5.0 and refused about one owner
        utterance in ten. ``worst * 1.25`` lands at 8.8, between the owner's
        worst (7.6) and the nearest impostor's best (9.3).

        `headroom` is small because the base is already a worst case. The
        asymmetry behind erring wide: a false reject means Jarvis ignores you,
        which you notice immediately and resent, and a false accept is still
        bounded by the tier system, which asks a human before anything
        irreversible.
        """
        scores = self.self_scores()
        if not scores:
            return DEFAULT_THRESHOLD
        return _sane_threshold(max(max(scores) * headroom, MIN_THRESHOLD))

    # --- persistence --------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": [list(vector) for vector in self.samples],
            "anchors": self.anchors,
            "threshold": self.threshold,
            "label": self.label,
            "embedder": self.embedder,
            "created": self.created,
            "updated": self.updated,
        }

    def summary(self) -> dict[str, Any]:
        """What the console and the phone may see. Never the vectors.

        A voiceprint is biometric data. It does not leave the server on a
        status endpoint, so the answer to "is somebody enrolled" cannot also be
        the answer to "what do they sound like".
        """
        self_score = self.self_score()
        scores = self.self_scores()
        return {
            "enrolled": self.enrolled,
            "samples": len(self.samples),
            #: Split out so a screen can say "5 you read, 4 it learned" rather
            #: than a single number that quietly grew.
            "anchor_samples": min(self.anchors, len(self.samples)),
            "adapted_samples": self.adapted_samples,
            "min_samples": MIN_ENROLMENT_SAMPLES,
            # What it takes to measure a threshold rather than inherit one. The
            # console and the phone both drew `suggested_threshold` as the
            # number enrolment had arrived at; with three samples that number
            # was DEFAULT_THRESHOLD and nothing said so, so "enrol, read the
            # scores, then enforce" was advice the screen could not support.
            "measure_samples": MIN_MEASURABLE_SAMPLES,
            "max_samples": MAX_ENROLMENT_SAMPLES,
            "threshold": round(self.threshold, 3),
            "self_score": None if not math.isfinite(self_score) else round(self_score, 3),
            "self_scores": [round(value, 3) for value in scores],
            "worst_self_score": round(max(scores), 3) if scores else None,
            "suggested_threshold": round(self.suggested_threshold(), 3),
            #: False means `suggested_threshold` is DEFAULT_THRESHOLD — a
            #: starting point, not something this owner's voice produced.
            "threshold_measured": bool(scores),
            "label": self.label,
            "embedder": self.embedder,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "VoiceProfile | None":
        if not isinstance(payload, dict):
            return None
        raw = payload.get("samples")
        if not isinstance(raw, list):
            return None
        samples: list[tuple[float, ...]] = []
        for item in raw:
            if not isinstance(item, (list, tuple)):
                continue
            try:
                vector = tuple(float(value) for value in item)
            except (TypeError, ValueError):
                continue
            if len(vector) == EMBEDDING_DIMS:
                samples.append(vector)
        if not samples:
            return None
        profile = cls(
            samples=samples[-MAX_ENROLMENT_SAMPLES:],
            threshold=_sane_threshold(payload.get("threshold", DEFAULT_THRESHOLD)),
            label=str(payload.get("label") or "owner"),
            embedder=str(payload.get("embedder") or "jarvis-mfcc-v1"),
        )
        for key in ("created", "updated"):
            try:
                setattr(profile, key, float(payload.get(key) or time.time()))
            except (TypeError, ValueError):
                pass
        # A profile written before adaptation existed has no anchor count, and
        # every sample in it was read off the prompt list by a person. Treating
        # them as anchors is both true and the safe direction to be wrong in:
        # the alternative makes an upgrade quietly turn the whole of somebody's
        # deliberate enrolment into evictable material.
        raw_anchors = payload.get("anchors")
        try:
            anchors = len(profile.samples) if raw_anchors is None else int(raw_anchors)
        except (TypeError, ValueError):
            anchors = len(profile.samples)
        profile.anchors = max(0, min(anchors, len(profile.samples)))
        return profile


# --- the policy -------------------------------------------------------------
#: What a gate can be set to.
#:
#: `observe` is not a nicety, it is the only responsible way to turn a biometric
#: gate on. The threshold that suits your voice, your microphone and your room
#: is not knowable from here, and the failure mode of guessing it is that Jarvis
#: stops answering you — which you will read as "the wake word broke" rather
#: than "the threshold is 0.4 too low". So: enrol, run in `observe` for a few
#: days, read the scores off the console, then enforce.
MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
MODES = (MODE_OFF, MODE_OBSERVE, MODE_ENFORCE)

#: What a refused turn does. `speak` is the default and the choice is not
#: obvious, so: an assistant that silently ignores you is indistinguishable
#: from one that did not hear you, and a false reject is the failure this
#: feature will actually produce. Saying so out loud is what makes it
#: debuggable by the person it is locking out. `silent` exists for anyone who
#: would rather a stranger learn nothing, and is a supported choice.
ON_REJECT_SPEAK = "speak"
ON_REJECT_SILENT = "silent"

DEFAULT_REFUSAL = "I'm sorry, I don't recognise that voice."


@dataclass
class SpeakerGate:
    """Profile plus policy: the thing the pipeline actually consults."""

    profile: VoiceProfile | None = None
    mode: str = MODE_OFF
    on_reject: str = ON_REJECT_SPEAK
    refusal: str = DEFAULT_REFUSAL
    #: Below this, a turn is *unverifiable* rather than refused. Whether an
    #: unverifiable turn runs is :attr:`allow_unverifiable`.
    min_speech_ms: float = MIN_SPEECH_MS
    #: What to do with a turn too short, too quiet or too breathy to judge.
    #:
    #: True by default, and this is the single most consequential default in
    #: the file. "Stop", "yes", "louder", "no, the other one" are all under
    #: half a second, and an assistant that refuses every short word is not
    #: usable. The exposure it buys back is bounded: an attacker who can only
    #: pass unverifiable audio can only say things too short to carry a
    #: sentence, and everything dangerous is still behind the tier system's
    #: human approval. Set it false if that trade is wrong for you.
    allow_unverifiable: bool = True

    #: Keep learning the owner's voice from ordinary turns.
    #:
    #: OFF by default, and that is not timidity — it is that switching it on
    #: changes what a biometric gate will accept tomorrow, and a default that
    #: does that on upgrade is a default nobody consented to. One line of YAML
    #: turns it on; `docs/voice-identity.md` says what it costs.
    #:
    #: What it buys: a profile enrolled once in a quiet room slowly stops
    #: matching the same person in the kitchen with the extractor running, and
    #: the failure looks like "Jarvis stopped answering me". Adapting from
    #: confident matches tracks the microphone, the room and the voice as they
    #: drift.
    #:
    #: What it risks, and what the three guards below are for: every adaptive
    #: system can be walked. An impostor who scores just inside the threshold
    #: gets added to the profile, moving it a little toward them, which lets
    #: them in a little more easily next time. So —
    adapt: bool = False
    #: 1. **A much stricter bar than acceptance.** Adaptation requires a score
    #:    at or under this FRACTION of the threshold, so a turn that merely
    #:    scraped past teaches nothing. At the default an accepted-but-marginal
    #:    turn at 0.9x threshold is ignored; only a turn deep inside the
    #:    owner's own distribution counts.
    adapt_margin: float = 0.5
    #: 2. **A rate limit.** At most one adapted sample per this many seconds,
    #:    so a burst of attempts cannot become a burst of learning. Ten minutes
    #:    is far below the timescale of a cold or a new microphone and far
    #:    above the timescale of somebody standing at the door trying voices.
    adapt_min_interval: float = 600.0
    #: 3. **Anchors**, on the profile itself: the samples a person deliberately
    #:    enrolled are never evicted, so the profile can never consist entirely
    #:    of what it taught itself. See :attr:`VoiceProfile.anchors`.

    #: Set when :meth:`check` changed the profile, so the caller knows to
    #: persist it. The caller clears it; this class never writes to disk.
    profile_dirty: bool = False
    _last_adapt: float = 0.0

    @property
    def enrolled(self) -> bool:
        return self.profile is not None and self.profile.enrolled

    @property
    def active(self) -> bool:
        """Whether this gate does anything at all this turn."""
        return self.mode in (MODE_OBSERVE, MODE_ENFORCE) and self.enrolled

    def check(self, pcm: bytes, rate: int = DEFAULT_RATE, width: int = DEFAULT_WIDTH) -> Verdict:
        """Verify one utterance, and learn from it when that is safe.

        Blocking — call it in a thread.
        """
        if self.profile is None:
            return Verdict(False, math.inf, 0.0, 0.0, 0.0, "not-enrolled")
        embedding = embed(pcm, rate, width)
        verdict = self.profile.verify(embedding)
        if self._should_adapt(verdict, embedding):
            self.profile.add(embedding, anchor=False)
            self._last_adapt = time.time()
            self.profile_dirty = True
            _LOGGER.info(
                "speaker: learned from a turn scoring %.2f (threshold %.2f); "
                "profile now %d enrolled + %d adapted",
                verdict.score,
                self.profile.threshold,
                min(self.profile.anchors, len(self.profile.samples)),
                self.profile.adapted_samples,
            )
        return verdict

    def _should_adapt(self, verdict: Verdict, embedding: Any) -> bool:
        """Every condition that has to hold before a turn may teach.

        Written as one list on purpose. Each clause is a guard somebody could
        reasonably think redundant, and the ones that look redundant are the
        ones that matter: `accepted` alone admits a marginal impostor, and the
        margin alone would admit them the moment the mode is `off` and nothing
        is being enforced at all.
        """
        if not self.adapt or self.profile is None or embedding is None:
            return False
        # Never while the gate is doing nothing. In `off` there is no
        # enforcement and nobody is watching the scores, so "it accepted this"
        # carries no weight — and an unattended house would spend that mode
        # quietly learning whoever talks in it.
        if not self.active:
            return False
        if not verdict.accepted:
            return False
        # Deep inside the owner's distribution, not merely inside the gate.
        if not math.isfinite(verdict.score):
            return False
        if verdict.score > self.profile.threshold * max(0.0, self.adapt_margin):
            return False
        # An utterance with no measurable pitch is refused as a MATCH by
        # `verify`, so it cannot reach here — but it must never become an
        # enrolment sample either, and saying so costs one line.
        if not getattr(embedding, "has_pitch", False):
            return False
        if time.time() - self._last_adapt < max(0.0, self.adapt_min_interval):
            return False
        return True

    def blocks(self, verdict: Verdict) -> bool:
        """Whether this verdict stops the turn.

        Only ``enforce`` ever blocks; ``observe`` produces the same verdict,
        emits the same event and lets the turn through, which is what makes it
        safe to leave on while you find your threshold.
        """
        if self.mode != MODE_ENFORCE:
            return False
        if verdict.accepted:
            return False
        if self.allow_unverifiable and verdict.reason in _UNVERIFIABLE:
            return False
        return True


#: Reasons that mean "could not judge", as opposed to "judged and it was not
#: you". Kept as one set because the difference decides whether a turn runs,
#: and it must not be re-derived by eye at each call site.
_UNVERIFIABLE = frozenset({"no-speech", "unverifiable-no-pitch"})


# --- helpers ----------------------------------------------------------------
def _vector_of(item: Embedding | tuple[float, ...] | list[float]) -> tuple[float, ...]:
    if isinstance(item, Embedding):
        return item.vector
    return tuple(float(value) for value in item)


def _sane_threshold(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    if not math.isfinite(number):
        return DEFAULT_THRESHOLD
    return min(max(number, MIN_THRESHOLD), MAX_THRESHOLD)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    dot = norm_left = norm_right = 0.0
    for a, b in zip(left, right):
        dot += a * b
        norm_left += a * a
        norm_right += b * b
    if norm_left <= 0.0 or norm_right <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_left * norm_right)


def _confidence(score: float, threshold: float) -> float:
    """Map a squared-z score onto 0..1 for a progress bar.

    Deliberately not a probability. Calling it one would invite somebody to
    multiply it by something.
    """
    if not math.isfinite(score):
        return 0.0
    if threshold <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - score / (2.0 * threshold)))
