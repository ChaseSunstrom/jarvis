"""Signal processing for the speaker verifier: FFT, mel filterbank, cepstrum.

Split out of :mod:`jarvis.voice.speaker` because the maths and the policy are
different kinds of thing, and only the maths has to be mirrored byte-for-byte
in Kotlin (``android-app/.../voice/Dsp.kt``). Anything that changes a number
this module returns breaks that mirror, and
``tests/test_speaker_parity.py`` / ``android-app/tools/voiceprint_parity_test.py``
both fail on the same fixture when it does.

Stdlib only, on purpose, for the reason ``audio.py`` gives: jarvis-core installs
from wheels with no compiler on a Pi, and pulling numpy in for one feature
would end that. The cost is that everything here is written for a Python
interpreter rather than for a vector unit:

* twiddle factors and filterbanks are cached per (size, rate) — they are
  recomputed once per process, not once per frame;
* the real-input FFT packs N reals into an N/2-point complex transform, which
  is the single biggest win available and roughly halves the work;
* the mel filterbank is stored as (offset, weights) runs so a band only ever
  touches the bins it actually covers, not all 257 of them.

A 3-second utterance costs about 40 ms of CPU this way, which is why the
verifier can run inside a turn without being noticed. See
``tests/test_speaker.py::test_embedding_cost_is_within_budget``.
"""

from __future__ import annotations

import math
from array import array

__all__ = [
    "MEL_BANDS",
    "N_FFT",
    "autocorrelation",
    "dct2",
    "hann_window",
    "log_mel",
    "mel_filterbank",
    "power_spectrum",
    "pre_emphasis",
    "rfft",
]

#: FFT size, and the analysis frame length in samples. 512 at 16 kHz is a
#: 32 ms window: long enough that the lowest mel band has something in it and
#: that a 60 Hz pitch period fits, short enough to be stationary.
N_FFT = 512

#: Mel bands. 26 is the long-standing default for 16 kHz speech; the DCT that
#: follows only keeps the first 20 coefficients, so more bands buy resolution
#: the cepstrum immediately throws away.
MEL_BANDS = 26

#: The mel filterbank's span. The bottom is above mains hum and rumble; the
#: top is below the Nyquist edge, where a resampler's anti-alias filter lives
#: and the energy is whatever the resampler left behind rather than the
#: speaker.
MEL_LOW_HZ = 60.0
MEL_HIGH_HZ = 7600.0

#: Floor under a mel band's energy before the log, in units of full-scale
#: power. Without it a digitally silent band is log(0), and one -inf poisons
#: every downstream mean.
_LOG_FLOOR = 1e-10

_twiddle_cache: dict[int, tuple[array, array]] = {}
_window_cache: dict[int, array] = {}
_filterbank_cache: dict[tuple[int, int, int, float, float], list[tuple[int, array]]] = {}
_dct_cache: dict[tuple[int, int], list[array]] = {}


# --- windows ----------------------------------------------------------------
def hann_window(size: int) -> array:
    """Periodic Hann window of `size` samples, cached.

    Periodic (``/ size``) rather than symmetric (``/ (size - 1)``) because this
    window is used for spectral analysis of a continuous stream, not for
    designing an FIR filter. The two differ by one sample of asymmetry, which
    is inaudible and is exactly the kind of difference that makes a Kotlin
    mirror disagree in the fourth decimal place, so it is stated here rather
    than left to whichever convention a reimplementation reaches for.
    """
    cached = _window_cache.get(size)
    if cached is not None:
        return cached
    window = array("d", (0.5 - 0.5 * math.cos(2.0 * math.pi * i / size) for i in range(size)))
    _window_cache[size] = window
    return window


def pre_emphasis(samples: array, coefficient: float = 0.97) -> array:
    """First-order high-pass: ``y[n] = x[n] - a*x[n-1]``.

    Speech falls off at roughly 6 dB per octave, so without this the first
    couple of mel bands carry most of the energy and the log-mel vector is
    mostly a measure of how close the mouth was to the microphone. The first
    sample is passed through unchanged (there is no ``x[-1]``), which matters
    only because the mirror has to make the same choice.
    """
    out = array("d", bytes(8 * len(samples)))
    if not samples:
        return out
    out[0] = float(samples[0])
    previous = float(samples[0])
    for index in range(1, len(samples)):
        current = float(samples[index])
        out[index] = current - coefficient * previous
        previous = current
    return out


# --- FFT --------------------------------------------------------------------
def _twiddles(half: int) -> tuple[array, array]:
    """cos/sin tables for a `half`-point complex FFT, cached per size."""
    cached = _twiddle_cache.get(half)
    if cached is not None:
        return cached
    cos_table = array("d", bytes(8 * half))
    sin_table = array("d", bytes(8 * half))
    for index in range(half):
        angle = -2.0 * math.pi * index / half
        cos_table[index] = math.cos(angle)
        sin_table[index] = math.sin(angle)
    _twiddle_cache[half] = (cos_table, sin_table)
    return cos_table, sin_table


def _fft_in_place(real: array, imag: array) -> None:
    """Iterative radix-2 decimation-in-time complex FFT, in place.

    `real` and `imag` must be the same power-of-two length. Written out rather
    than pulled from a library because there is no stdlib FFT and the mirror
    has to reproduce it; the bit-reversal permutation and the butterfly order
    below are the parts a reimplementation gets subtly wrong.
    """
    size = len(real)
    if size <= 1:
        return

    # Bit-reversal permutation.
    j = 0
    for i in range(1, size):
        bit = size >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            real[i], real[j] = real[j], real[i]
            imag[i], imag[j] = imag[j], imag[i]

    cos_table, sin_table = _twiddles(size)
    length = 2
    while length <= size:
        step = size // length
        half = length >> 1
        for start in range(0, size, length):
            angle_index = 0
            for offset in range(start, start + half):
                partner = offset + half
                wr = cos_table[angle_index]
                wi = sin_table[angle_index]
                pr = real[partner]
                pi = imag[partner]
                tr = pr * wr - pi * wi
                ti = pr * wi + pi * wr
                real[partner] = real[offset] - tr
                imag[partner] = imag[offset] - ti
                real[offset] += tr
                imag[offset] += ti
                angle_index += step
        length <<= 1


def rfft(frame: array, size: int = N_FFT) -> tuple[array, array]:
    """Spectrum bins 0..size/2 of a real frame, zero-padded to `size`.

    The real input is packed two-samples-to-a-complex-bin and transformed at
    half length, then unpacked — the standard real-FFT trick. It is worth the
    fiddly unpacking: this is the inner loop of the whole verifier and doing it
    the naive way doubles the cost of every utterance.

    Returns ``(real, imag)``, each ``size/2 + 1`` long. Both
    :func:`power_spectrum` and :func:`autocorrelation` are thin wrappers, which
    is the point — there is exactly one FFT in this file for a reimplementation
    to get wrong.
    """
    half = size // 2
    real = array("d", bytes(8 * half))
    imag = array("d", bytes(8 * half))
    usable = min(len(frame), size)
    for index in range(0, usable, 2):
        real[index >> 1] = frame[index]
    for index in range(1, usable, 2):
        imag[index >> 1] = frame[index]

    _fft_in_place(real, imag)

    out_real = array("d", bytes(8 * (half + 1)))
    out_imag = array("d", bytes(8 * (half + 1)))
    # DC and Nyquist fall out of the packing directly and have no partner bin.
    out_real[0] = real[0] + imag[0]
    out_real[half] = real[0] - imag[0]
    cos_table, sin_table = _twiddles(size)
    for k in range(1, half // 2 + 1):
        mirror = half - k
        # Even/odd decomposition of the packed transform.
        er = 0.5 * (real[k] + real[mirror])
        ei = 0.5 * (imag[k] - imag[mirror])
        orr = 0.5 * (imag[k] + imag[mirror])
        oi = -0.5 * (real[k] - real[mirror])
        wr = cos_table[k]
        wi = sin_table[k]
        tr = orr * wr - oi * wi
        ti = orr * wi + oi * wr
        out_real[k] = er + tr
        out_imag[k] = ei + ti
        if mirror != k:
            # X[size/2 - k] is the conjugate-symmetric partner of the above.
            out_real[mirror] = er - tr
            out_imag[mirror] = -(ei - ti)
    return out_real, out_imag


def power_spectrum(frame: array, size: int = N_FFT) -> array:
    """|X[k]|^2 for k in 0..size/2, from a real frame."""
    real, imag = rfft(frame, size)
    out = array("d", bytes(8 * len(real)))
    for index in range(len(real)):
        out[index] = real[index] * real[index] + imag[index] * imag[index]
    return out


def autocorrelation(power: array, size: int = N_FFT) -> array:
    """Autocorrelation lags 0..size/2 of the frame whose power spectrum is
    `power`.

    By Wiener-Khinchin this is the inverse transform of the power spectrum.
    The power spectrum is real and even, so its transform is real and even too
    — which means the forward real FFT computes it, scaled by 1/size, and the
    imaginary part it returns is rounding error. Going through
    :func:`rfft` rather than a full complex transform of the symmetric
    extension halves the cost of pitch, which is a third of the verifier's
    total.

    Returned unnormalised: the caller compares peaks within one frame, and
    dividing by ``r[0]`` would only cost a branch for the digitally-silent
    frames that are already filtered out.
    """
    half = size // 2
    extended = array("d", bytes(8 * size))
    extended[0] = power[0]
    extended[half] = power[half]
    for k in range(1, half):
        extended[k] = power[k]
        extended[size - k] = power[k]
    real, _ = rfft(extended, size)
    scale = 1.0 / size
    for index in range(len(real)):
        real[index] *= scale
    return real


# --- mel --------------------------------------------------------------------
def hz_to_mel(hz: float) -> float:
    """O'Shaughnessy's mel scale — the one HTK, librosa's `htk=True` and every
    MFCC tutorial use. Named because the *other* common scale (Slaney's) differs
    by enough to move every coefficient."""
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def mel_filterbank(
    rate: int,
    size: int = N_FFT,
    bands: int = MEL_BANDS,
    low_hz: float = MEL_LOW_HZ,
    high_hz: float = MEL_HIGH_HZ,
) -> list[tuple[int, array]]:
    """Triangular mel filters as (first_bin, weights) runs, cached.

    Storing each filter as the slice it actually covers rather than as a full
    257-long row is what makes the filterbank cheap: a band near the bottom of
    the scale is three bins wide, and multiplying it by 254 zeros every frame
    is most of the cost of a naive implementation.
    """
    top = min(high_hz, rate / 2.0)
    key = (int(rate), int(size), int(bands), float(low_hz), float(top))
    cached = _filterbank_cache.get(key)
    if cached is not None:
        return cached

    bins = size // 2 + 1
    low_mel = hz_to_mel(low_hz)
    high_mel = hz_to_mel(top)
    points = [
        mel_to_hz(low_mel + (high_mel - low_mel) * index / (bands + 1))
        for index in range(bands + 2)
    ]
    # Bin centres, kept as floats: rounding the edges to integers first is the
    # usual shortcut and it collapses the narrow low bands into each other.
    edges = [hz * size / rate for hz in points]

    filters: list[tuple[int, array]] = []
    for band in range(bands):
        left, centre, right = edges[band], edges[band + 1], edges[band + 2]
        first = max(0, int(math.floor(left)))
        last = min(bins - 1, int(math.ceil(right)))
        weights = array("d")
        for index in range(first, last + 1):
            if index <= left or index >= right:
                weight = 0.0
            elif index <= centre:
                weight = (index - left) / (centre - left) if centre > left else 1.0
            else:
                weight = (right - index) / (right - centre) if right > centre else 1.0
            weights.append(weight)
        filters.append((first, weights))

    _filterbank_cache[key] = filters
    return filters


def log_mel(power: array, filters: list[tuple[int, array]]) -> array:
    """Natural log of the energy in each mel band."""
    out = array("d", bytes(8 * len(filters)))
    for index, (first, weights) in enumerate(filters):
        total = 0.0
        for offset, weight in enumerate(weights):
            if weight:
                total += power[first + offset] * weight
        out[index] = math.log(total if total > _LOG_FLOOR else _LOG_FLOOR)
    return out


# --- cepstrum ---------------------------------------------------------------
def _dct2_basis(length: int, count: int) -> list[array]:
    """Cached DCT-II basis rows: ``cos(pi*(n+0.5)*k/length)``."""
    key = (length, count)
    cached = _dct_cache.get(key)
    if cached is not None:
        return cached
    basis = [
        array(
            "d",
            (math.cos(math.pi * (n + 0.5) * k / length) for n in range(length)),
        )
        for k in range(count)
    ]
    _dct_cache[key] = basis
    return basis


def dct2(values: array, count: int) -> array:
    """First `count` DCT-II coefficients of `values`, orthonormally scaled.

    Orthonormal so the coefficients keep the units of the input and a change
    in the number of mel bands does not silently rescale every feature.
    """
    length = len(values)
    basis = _dct2_basis(length, count)
    out = array("d", bytes(8 * count))
    if length == 0:
        return out
    first_scale = math.sqrt(1.0 / length)
    rest_scale = math.sqrt(2.0 / length)
    for k in range(count):
        row = basis[k]
        total = 0.0
        for n in range(length):
            total += values[n] * row[n]
        out[k] = total * (first_scale if k == 0 else rest_scale)
    return out
