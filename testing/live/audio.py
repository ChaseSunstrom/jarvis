"""Making the audio harder, on purpose.

A rig that only ever feeds a clean 16 kHz studio recording into Whisper proves
that Whisper works on studio recordings. The interesting failures are the ones
a room produces: a fan, a television, somebody else talking, a microphone that
clipped, and — the one that matters most for a wake word — nothing at all.

All arithmetic on PCM. No service, no file, no network.
"""

from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass

#: Signed 16-bit.
_MAX = 32767
_MIN = -32768


def _samples(pcm: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) // 2 * 2]))


def _pcm(samples: list[int]) -> bytes:
    return struct.pack(
        f"<{len(samples)}h", *(max(_MIN, min(_MAX, int(round(s)))) for s in samples)
    )


def rms(pcm: bytes) -> float:
    values = _samples(pcm)
    if not values:
        return 0.0
    return math.sqrt(sum(float(v) * v for v in values) / len(values))


@dataclass
class Noise:
    """A named noise, so a scenario can ask for one by name."""

    name: str
    #: Called with (index, rate) and returns a sample in [-1, 1].
    shape: str = "white"

    def sample(self, index: int, rate: int, rng: random.Random) -> float:
        if self.shape == "white":
            return rng.uniform(-1.0, 1.0)
        if self.shape == "hum":
            # A 50 Hz mains hum with its third harmonic — the sound of a room
            # with a fridge in it.
            t = index / rate
            return 0.7 * math.sin(2 * math.pi * 50 * t) + 0.3 * math.sin(2 * math.pi * 150 * t)
        if self.shape == "fan":
            # Brown-ish: white through a one-pole low pass, which is what a fan
            # or an extractor actually sounds like at a distance.
            self._last = getattr(self, "_last", 0.0) * 0.96 + rng.uniform(-1.0, 1.0) * 0.04
            return self._last * 8
        raise ValueError(f"unknown noise shape: {self.shape}")


NOISES = {
    "white": Noise("white", "white"),
    "hum": Noise("hum", "hum"),
    "fan": Noise("fan", "fan"),
}


def add_noise(pcm: bytes, snr_db: float, rate: int = 16000, shape: str = "white",
              seed: int = 7) -> bytes:
    """Mix noise in at a given signal-to-noise ratio.

    `snr_db` is measured against the *speech* RMS, so 20 dB is a quiet room and
    0 dB is noise as loud as the voice. Deterministic for a given seed: a
    scenario that fails at 5 dB must fail at 5 dB again tomorrow, or the
    threshold means nothing.
    """
    speech = rms(pcm)
    if speech <= 0:
        return pcm
    target = speech / (10 ** (snr_db / 20.0))
    rng = random.Random(seed)
    noise_source = NOISES.get(shape) or NOISES["white"]
    raw = [noise_source.sample(i, rate, rng) for i in range(len(pcm) // 2)]
    noise_rms = math.sqrt(sum(v * v for v in raw) / max(len(raw), 1)) or 1.0
    gain = target / noise_rms
    return _pcm([s + n * gain for s, n in zip(_samples(pcm), raw)])


def silence(seconds: float, rate: int = 16000) -> bytes:
    """Digital silence. The wake word must NOT fire on this."""
    return b"\x00\x00" * int(seconds * rate)


def room_tone(seconds: float, rate: int = 16000, level_db: float = -50.0,
              shape: str = "fan", seed: int = 11) -> bytes:
    """Not-quite-silence: an empty room with something running in it.

    A wake-word negative that is digital silence is too easy — every detector
    passes it. This is the one that catches a detector with its threshold set
    by hope.
    """
    amplitude = _MAX * (10 ** (level_db / 20.0))
    rng = random.Random(seed)
    noise = NOISES.get(shape) or NOISES["fan"]
    return _pcm([noise.sample(i, rate, rng) * amplitude for i in range(int(seconds * rate))])


def clip(pcm: bytes, factor: float = 4.0) -> bytes:
    """Overdrive the signal until it clips — a microphone gain set far too high."""
    return _pcm([s * factor for s in _samples(pcm)])


def concat(*parts: bytes) -> bytes:
    return b"".join(parts)


def snr_of(speech: bytes, noisy: bytes) -> float:
    """Measured SNR, for the report — never assumed from the request."""
    speech_rms = rms(speech)
    residual = _pcm([n - s for s, n in zip(_samples(speech), _samples(noisy))])
    noise_rms = rms(residual) or 1e-9
    return 20 * math.log10(speech_rms / noise_rms)
