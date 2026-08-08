"""PCM audio helpers: WAV containers, loudness, resampling, chunking.

Deliberately stdlib-only (`wave`, `array`, `math`) — voice runs on the same
box as everything else and we are not shipping numpy for three functions.
All PCM here is little-endian signed integers (except 8-bit, which WAV
defines as unsigned), interleaved by channel.
"""

from __future__ import annotations

import io
import math
import wave
from array import array
from collections.abc import Iterator

__all__ = [
    "chunk_pcm",
    "duration_seconds",
    "is_silence",
    "pcm_from_wav",
    "resample",
    "rms",
    "wav_bytes",
]

DEFAULT_RATE = 16000
DEFAULT_WIDTH = 2
DEFAULT_CHANNELS = 1

# 20 ms of 16 kHz mono 16-bit audio — what the Wyoming containers like.
DEFAULT_CHUNK_MS = 20

_TYPECODES = {1: "b", 2: "h", 4: "i"}


def _typecode(width: int) -> str:
    try:
        code = _TYPECODES[int(width)]
    except KeyError:
        raise ValueError(f"unsupported sample width: {width}") from None
    if array(code).itemsize != int(width):  # pragma: no cover - exotic platforms
        raise ValueError(f"no native array type for width {width}")
    return code


def _samples(pcm: bytes, width: int) -> array:
    """Signed sample array (8-bit WAV data is unsigned, so it gets re-centred)."""
    code = _typecode(width)
    usable = len(pcm) - (len(pcm) % int(width))
    values = array(code)
    values.frombytes(bytes(pcm[:usable]))
    if width == 1:
        # `b` already reads signed bytes; WAV 8-bit is unsigned 0..255.
        values = array("h", (v + 128 if v < 0 else v - 128 for v in values))
    return values


def wav_bytes(
    pcm: bytes,
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    channels: int = DEFAULT_CHANNELS,
) -> bytes:
    """Wrap raw PCM in a valid WAV container."""
    if rate <= 0:
        raise ValueError("rate must be positive")
    if int(width) not in (1, 2, 4):
        raise ValueError(f"unsupported sample width: {width}")
    if channels <= 0:
        raise ValueError("channels must be positive")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(int(channels))
        wav_file.setsampwidth(int(width))
        wav_file.setframerate(int(rate))
        wav_file.writeframes(bytes(pcm))
    return buffer.getvalue()


def pcm_from_wav(data: bytes) -> tuple[bytes, int, int, int]:
    """Inverse of :func:`wav_bytes` — returns (pcm, rate, width, channels)."""
    with wave.open(io.BytesIO(bytes(data)), "rb") as wav_file:
        return (
            wav_file.readframes(wav_file.getnframes()),
            wav_file.getframerate(),
            wav_file.getsampwidth(),
            wav_file.getnchannels(),
        )


def rms(pcm: bytes, width: int = DEFAULT_WIDTH) -> float:
    """Root-mean-square level of a PCM buffer, in raw sample units."""
    values = _samples(pcm, width)
    if not values:
        return 0.0
    total = 0
    for value in values:
        total += value * value
    return math.sqrt(total / len(values))


def is_silence(pcm: bytes, threshold: float = 200.0, width: int = DEFAULT_WIDTH) -> bool:
    """Cheap energy VAD: True when the buffer is below `threshold` RMS."""
    return rms(pcm, width) < threshold


def duration_seconds(
    pcm: bytes,
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    channels: int = DEFAULT_CHANNELS,
) -> float:
    frame_size = max(int(width) * int(channels), 1)
    return len(pcm) / frame_size / max(int(rate), 1)


def chunk_pcm(
    pcm: bytes,
    chunk_ms: int = DEFAULT_CHUNK_MS,
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    channels: int = DEFAULT_CHANNELS,
) -> Iterator[bytes]:
    """Split PCM into frame-aligned chunks of roughly `chunk_ms` milliseconds."""
    frame_size = max(int(width) * int(channels), 1)
    frames = max(int(rate * chunk_ms / 1000), 1)
    size = frames * frame_size
    for offset in range(0, len(pcm), size):
        chunk = pcm[offset : offset + size]
        if chunk:
            yield chunk


def resample(
    pcm: bytes,
    from_rate: int,
    to_rate: int,
    width: int = DEFAULT_WIDTH,
    channels: int = DEFAULT_CHANNELS,
) -> bytes:
    """Linear-interpolation resampler.

    Good enough to feed 16 kHz models from whatever a microphone or a TTS
    voice happens to produce; not good enough to master a record with.
    """
    from_rate, to_rate = int(from_rate), int(to_rate)
    width, channels = int(width), int(channels)
    if from_rate <= 0 or to_rate <= 0:
        raise ValueError("sample rates must be positive")
    if from_rate == to_rate or not pcm:
        return bytes(pcm)

    code = _typecode(width)
    values = _samples(pcm, width)
    frames = len(values) // channels
    if frames == 0:
        return b""

    out_frames = max(int(round(frames * to_rate / from_rate)), 1)
    ratio = frames / out_frames
    limit = (1 << (width * 8 - 1)) - 1
    out = array(code, bytes(out_frames * channels * width))

    for index in range(out_frames):
        position = index * ratio
        left = int(position)
        right = min(left + 1, frames - 1)
        weight = position - left
        base_out = index * channels
        base_left = left * channels
        base_right = right * channels
        for channel in range(channels):
            a = values[base_left + channel]
            b = values[base_right + channel]
            sample = int(round(a + (b - a) * weight))
            out[base_out + channel] = max(-limit - 1, min(limit, sample))

    if width == 1:
        # Convert back to unsigned 8-bit as WAV expects.
        return bytes((value + 128) & 0xFF for value in out)
    return out.tobytes()
