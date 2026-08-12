"""Enrolling a voice, and asking what the gate thinks.

Four routes, all on the authenticated router:

    GET    /api/voice/speaker            what is enrolled, and how well it fits
    POST   /api/voice/speaker/enrol      add one sample (WAV or raw PCM)
    POST   /api/voice/speaker/verify     score a sample without enrolling it
    DELETE /api/voice/speaker            forget the voiceprint entirely

## What never crosses this boundary

The **vectors**. A voiceprint is biometric data about one person, and the
answer to "is somebody enrolled?" must not also be the answer to "what do they
sound like?" — so :meth:`VoiceProfile.summary` is what every response is built
from, and it carries counts, scores and timestamps only. `DELETE` is a real
delete: the store is overwritten, not tombstoned.

The **audio** is not stored at all. A sample is embedded in a worker thread and
the bytes are dropped when the request ends. Nothing here writes a recording to
disk, and there is no debug flag that makes it.

## Why enrol takes one sample at a time

Because the useful feedback is per sample. Enrolment has to cover the range of
how you actually sound (see `voice/speaker.py` — this is the difference between
a gate that works and one that locks you out), and the surface asking for the
phrases needs to be able to say "that one was too quiet, say it again" between
them. A batch endpoint can only fail the whole set.

Each response carries the running :meth:`VoiceProfile.summary`, so a client
watches `self_scores` and `suggested_threshold` settle as it goes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..voice.audio import DEFAULT_RATE, DEFAULT_WIDTH
from ..voice.speaker import (
    ENROLMENT_PROMPTS,
    MAX_ENROLMENT_SAMPLES,
    MIN_ENROLMENT_SAMPLES,
    MODES,
    SpeakerError,
    VoiceProfile,
    embed,
    embed_wav,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Biggest sample accepted, in bytes. 30 s of 16 kHz mono 16-bit plus slack for
#: a WAV header — well past useful, and a bound so an authenticated client
#: cannot hand the server an arbitrary amount of audio to hold in memory.
MAX_SAMPLE_BYTES = 16000 * 2 * 30 + 4096


class EnrolError(Exception):
    """The sample could not be used. Carries an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _gate(jarvis: "Jarvis") -> Any:
    from ..integrations.voice import get_voice_data

    data = get_voice_data(jarvis)
    if data is None:
        raise EnrolError("the voice integration is not set up", 503)
    return data.speaker


def status(jarvis: "Jarvis") -> dict[str, Any]:
    """What the console and the phone draw the enrolment screen from."""
    gate = _gate(jarvis)
    profile = gate.profile
    payload: dict[str, Any] = {
        "mode": gate.mode,
        "modes": list(MODES),
        "on_reject": gate.on_reject,
        "allow_unverifiable": gate.allow_unverifiable,
        "active": gate.active,
        # The phrases live in one place so the console and the phone cannot
        # drift apart on them, and they are load-bearing rather than decorative
        # — see voice/speaker.py.
        "prompts": list(ENROLMENT_PROMPTS),
        "min_samples": MIN_ENROLMENT_SAMPLES,
        "max_samples": MAX_ENROLMENT_SAMPLES,
    }
    if profile is None:
        payload.update({"enrolled": False, "samples": 0})
    else:
        payload.update(profile.summary())
    return payload


async def _embed(body: bytes, content_type: str, rate: int, width: int) -> Any:
    """Turn a request body into an embedding, off the event loop."""
    if not body:
        raise EnrolError("no audio in the request body")
    if len(body) > MAX_SAMPLE_BYTES:
        raise EnrolError(f"sample is larger than {MAX_SAMPLE_BYTES} bytes", 413)

    looks_like_wav = body[:4] == b"RIFF" or "wav" in content_type.lower()
    try:
        if looks_like_wav:
            embedding = await asyncio.to_thread(embed_wav, body)
        else:
            embedding = await asyncio.to_thread(embed, body, rate, width)
    except SpeakerError as err:
        raise EnrolError(str(err)) from err
    except Exception as err:  # a malformed WAV is a bad request, not a 500
        raise EnrolError(f"could not read the audio: {err}") from err

    if embedding is None:
        raise EnrolError(
            "not enough speech in that sample to enrol from — say the whole "
            "phrase, and closer to the microphone"
        )
    return embedding


async def async_enrol(
    jarvis: "Jarvis",
    body: bytes,
    content_type: str = "",
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
) -> dict[str, Any]:
    """Add one sample to the profile and report where enrolment now stands."""
    from ..integrations.voice import async_save_profile

    gate = _gate(jarvis)
    embedding = await _embed(body, content_type, rate, width)

    # A pitchless enrolment sample would teach the profile a pitch histogram
    # that is a placeholder, and every later turn would be measured against it.
    # Refusing here costs one retry; accepting it costs the whole profile.
    if not embedding.has_pitch:
        raise EnrolError(
            "that sample has no measurable pitch — it is too quiet, too "
            "breathy, or too far from the microphone to enrol from"
        )

    if gate.profile is None:
        gate.profile = VoiceProfile(samples=[embedding.vector])
    else:
        gate.profile.add(embedding)

    # The threshold follows the samples. Enrolment is the only place that has
    # the leave-one-out spread to work it out from, so it is recomputed on
    # every sample rather than left at whatever the first three implied.
    if gate.profile.enrolled:
        gate.profile.threshold = gate.profile.suggested_threshold()

    await async_save_profile(jarvis, gate.profile)
    payload = status(jarvis)
    payload["accepted"] = True
    payload["sample"] = embedding.as_dict() | {"vector": None}
    return payload


async def async_verify(
    jarvis: "Jarvis",
    body: bytes,
    content_type: str = "",
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
) -> dict[str, Any]:
    """Score a sample against the profile without changing anything.

    This is how you find your threshold without being locked out while you look
    for it: record yourself, and a friend, and read the two numbers. It is also
    what the console's "test my voice" button calls.
    """
    gate = _gate(jarvis)
    if gate.profile is None or not gate.profile.enrolled:
        raise EnrolError("nobody is enrolled yet", 409)
    embedding = await _embed(body, content_type, rate, width)
    verdict = gate.profile.verify(embedding)
    return {
        "verdict": verdict.as_dict(),
        "would_block": gate.blocks(verdict),
        "speech_ms": round(embedding.speech_ms, 1),
        "has_pitch": embedding.has_pitch,
    }


async def async_forget(jarvis: "Jarvis") -> dict[str, Any]:
    """Delete the voiceprint. A real delete, not a flag."""
    from ..integrations.voice import async_save_profile

    gate = _gate(jarvis)
    gate.profile = None
    await async_save_profile(jarvis, None)
    _LOGGER.info("Voiceprint deleted; the speaker gate is inert until re-enrolled")
    return status(jarvis)
