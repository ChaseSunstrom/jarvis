"""Enrolling a voice from the desktop, without a microphone or a server.

The recorder is a fake `subprocess.run`, the audio is a WAV built in memory,
and the HTTP is a fake opener — so what is under test is the two things that
are genuinely easy to get wrong here:

  * **the audio format.** jarvis-core wants raw little-endian 16-bit mono PCM.
    Sending a WAV means the first 44 bytes of somebody's voice profile are the
    letters "RIFF", and nothing errors.
  * **the rate.** A recorder asked for 16 kHz on a device that cannot do it
    hands back 48 kHz. A profile built from audio at a declared rate it is not
    at matches nobody, and again nothing errors.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis_desktop.config import Config  # noqa: E402
from jarvis_desktop.enrol import (  # noqa: E402
    RATE,
    RECORDERS,
    EnrolError,
    Sample,
    find_recorder,
    read_wav,
    record_wav,
)
from jarvis_desktop.enrol_client import (  # noqa: E402
    SpeakerClient,
    SpeakerError,
    http_base,
)


def wav_bytes(
    *, rate: int = RATE, channels: int = 1, width: int = 2, seconds: float = 2.0,
    frames: bytes | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(
            frames if frames is not None else b"\x01\x00" * int(rate * seconds) * channels
        )
    return buffer.getvalue()


# --- finding a recorder ----------------------------------------------------------

def test_the_first_installed_recorder_wins():
    installed = {"ffmpeg"}
    found = find_recorder(exists=lambda name: name in installed)
    assert found is not None and found.name == "ffmpeg"


def test_preference_order_is_honoured():
    installed = {"ffmpeg", "arecord"}
    assert find_recorder(exists=lambda n: n in installed).name == "arecord"


def test_one_can_be_asked_for_by_name():
    installed = {"ffmpeg", "arecord"}
    assert find_recorder("ffmpeg", exists=lambda n: n in installed).name == "ffmpeg"
    assert find_recorder("sox", exists=lambda n: n in installed) is None


def test_none_installed_is_none_rather_than_a_guess():
    assert find_recorder(exists=lambda _n: False) is None


def test_every_recorder_says_how_to_install_it():
    # A missing tool with no instruction is a dead end for somebody who has
    # never heard of `arecord`.
    for recorder in RECORDERS:
        assert recorder.install


def test_each_recorder_is_told_the_length_and_the_path(tmp_path):
    for recorder in RECORDERS:
        if recorder.name == "ffmpeg":
            continue  # its arguments are built per-platform; covered below
        argv = recorder.command(tmp_path / "a.wav", 7)
        assert "7" in argv
        assert str(tmp_path / "a.wav") in argv
        # Joined, because `afrecord` carries the rate inside a format string
        # (`LEI16@16000`) rather than as an argument of its own.
        assert str(RATE) in " ".join(argv)


# --- recording -------------------------------------------------------------------

def fake_run(*, code: int = 0, writes: bytes | None = None, stderr: bytes = b""):
    seen: list[list[str]] = []

    def run(argv, capture_output=False, timeout=None):
        seen.append(list(argv))
        if writes is not None:
            Path(argv[-1] if argv[-1].endswith(".wav") else argv[-2]).write_bytes(writes)
        return subprocess.CompletedProcess(argv, code, b"", stderr)

    run.seen = seen  # type: ignore[attr-defined]
    return run


def test_a_recording_comes_back_as_the_files_bytes(tmp_path):
    recorder = find_recorder(exists=lambda n: n == "arecord")
    run = fake_run(writes=wav_bytes())
    raw = record_wav(recorder, 3, run=run, tmpdir=str(tmp_path))
    assert raw.startswith(b"RIFF")
    assert run.seen[0][0] == "arecord"


def test_a_recorder_that_failed_says_what_it_said(tmp_path):
    recorder = find_recorder(exists=lambda n: n == "arecord")
    run = fake_run(code=1, stderr=b"arecord: main:830: audio open error: No such device")
    with pytest.raises(EnrolError, match="No such device"):
        record_wav(recorder, 3, run=run, tmpdir=str(tmp_path))


def test_a_recorder_that_produced_nothing_is_not_silently_accepted(tmp_path):
    # A zero-byte or header-only file would otherwise become "0.0s of audio"
    # further down, which reads like a microphone problem rather than a
    # recorder problem.
    recorder = find_recorder(exists=lambda n: n == "arecord")
    run = fake_run(writes=b"RIFF" + b"\x00" * 20)
    with pytest.raises(EnrolError, match="no audio"):
        record_wav(recorder, 3, run=run, tmpdir=str(tmp_path))


def test_a_recorder_that_hangs_does_not_hang_the_terminal(tmp_path):
    recorder = find_recorder(exists=lambda n: n == "arecord")

    def run(argv, capture_output=False, timeout=None):
        raise subprocess.TimeoutExpired(argv, timeout or 0)

    with pytest.raises(EnrolError, match="microphone"):
        record_wav(recorder, 3, run=run, tmpdir=str(tmp_path))


def test_a_recorder_that_is_not_there_says_how_to_get_it(tmp_path):
    recorder = find_recorder(exists=lambda n: n == "arecord")

    def run(argv, capture_output=False, timeout=None):
        raise FileNotFoundError(argv[0])

    with pytest.raises(EnrolError, match="alsa-utils"):
        record_wav(recorder, 3, run=run, tmpdir=str(tmp_path))


def test_the_length_is_capped(tmp_path):
    recorder = find_recorder(exists=lambda n: n == "arecord")
    run = fake_run(writes=wav_bytes())
    record_wav(recorder, 9999, run=run, tmpdir=str(tmp_path))
    assert "9999" not in run.seen[0]


# --- reading the audio ------------------------------------------------------------

def test_the_wav_header_never_reaches_the_server():
    """The bug this closes writes "RIFF" into somebody's voice profile.

    And nothing errors: the header is 44 bytes of perfectly valid int16, so it
    embeds as audio and simply makes the profile slightly wrong for ever.
    """
    sample = read_wav(wav_bytes())
    assert not sample.pcm.startswith(b"RIFF")
    assert b"WAVE" not in sample.pcm[:64]


def test_the_data_chunk_is_found_rather_than_a_fixed_offset_assumed():
    """A 44-byte header is the common case, not the format.

    A file with a LIST chunk has a longer one, and `raw[44:]` puts metadata
    into the middle of the audio.
    """
    plain = wav_bytes(seconds=1)
    # Splice a LIST chunk in after the fmt chunk, the way a recorder that
    # writes metadata does.
    at = plain.index(b"data")
    extra = b"LIST" + (12).to_bytes(4, "little") + b"INFOISFT" + b"x\x00\x00\x00"
    padded = plain[:at] + extra + plain[at:]
    # Fix the RIFF size so it is a real file.
    size = len(padded) - 8
    padded = padded[:4] + size.to_bytes(4, "little") + padded[8:]

    sample = read_wav(padded)
    assert b"LIST" not in sample.pcm
    assert b"INFO" not in sample.pcm


def test_the_real_rate_travels_with_the_audio():
    """A recorder asked for 16 kHz may hand back 48 kHz.

    A profile built from audio at a declared rate it is not at matches nobody,
    and there is no error anywhere to notice it by.
    """
    sample = read_wav(wav_bytes(rate=48_000, seconds=2))
    assert sample.rate == 48_000
    assert sample.seconds == pytest.approx(2.0, abs=0.05)


def test_stereo_is_downmixed_rather_than_refused():
    # The default input on a laptop is very often stereo, and that is not a
    # problem to hand back to somebody.
    stereo = wav_bytes(channels=2, seconds=2)
    sample = read_wav(stereo)
    assert sample.seconds == pytest.approx(2.0, abs=0.05)
    assert len(sample.pcm) == RATE * 2 * 2  # mono, 16-bit, two seconds


def test_downmixing_averages_rather_than_dropping_a_channel():
    frames = b"".join(
        int(left).to_bytes(2, "little", signed=True) + int(right).to_bytes(2, "little", signed=True)
        for left, right in [(100, 200), (-100, -200), (0, 0)]
    )
    padded = frames * (RATE // 2)  # long enough to pass the length floor
    sample = read_wav(wav_bytes(channels=2, frames=padded))
    first = int.from_bytes(sample.pcm[:2], "little", signed=True)
    assert first == 150


def test_eight_bit_audio_says_how_to_convert_it():
    with pytest.raises(EnrolError, match="16-bit"):
        read_wav(wav_bytes(width=1))


def test_something_that_is_not_a_wav_is_refused():
    with pytest.raises(EnrolError, match="WAV"):
        read_wav(b"this is an mp3, honestly")


def test_a_tap_is_refused_before_it_is_sent():
    with pytest.raises(EnrolError, match="whole phrase"):
        read_wav(wav_bytes(seconds=0.2))


# --- the HTTP ----------------------------------------------------------------------

def test_the_http_address_is_derived_from_the_socket_one():
    # A second setting for "the HTTP address" is a second thing to get wrong,
    # and getting it wrong means enrolling into a Jarvis that is not the one
    # this agent talks to.
    assert http_base("ws://jarvis.lan:8080/api/websocket") == "http://jarvis.lan:8080"
    assert http_base("wss://jarvis.example:443/api/websocket") == "https://jarvis.example:443"


def test_an_unusable_server_url_says_so():
    with pytest.raises(SpeakerError, match="server_url"):
        http_base("")
    with pytest.raises(SpeakerError):
        http_base("not a url")


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def opener_for(payload: dict, seen: list):
    def opener(request, timeout=None):
        seen.append(request)
        return FakeResponse(payload)

    return opener


def test_a_sample_is_sent_as_raw_pcm_with_its_real_rate():
    seen: list = []
    client = SpeakerClient(
        base="http://jarvis.lan:8080",
        token="t",
        opener=opener_for({"samples": 1}, seen),
    )
    sample = Sample(pcm=b"\x01\x00" * 100, rate=48_000, seconds=1.0)
    client.enrol(sample)

    request = seen[0]
    assert request.get_method() == "POST"
    # The rate that was MEASURED, not the one that was asked for.
    assert "rate=48000" in request.full_url
    assert "width=2" in request.full_url
    assert request.data == sample.pcm
    assert request.get_header("Content-type") == "application/octet-stream"
    assert request.get_header("Authorization") == "Bearer t"


def test_the_servers_own_refusal_is_what_gets_shown():
    """jarvis-core writes these for a person to act on.

    "that sample has no measurable pitch, it is too quiet" is the actionable
    part; "HTTP 400" is not.
    """
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(json.dumps({"detail": "no measurable pitch — too quiet"}).encode()),
        )

    client = SpeakerClient(base="http://x", token="t", opener=opener)
    with pytest.raises(SpeakerError, match="too quiet"):
        client.status()


def test_a_rejected_token_says_where_to_fix_it():
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"nope"))

    client = SpeakerClient(base="http://x", token="t", opener=opener)
    with pytest.raises(SpeakerError, match="config.json"):
        client.status()


def test_a_server_that_is_not_jarvis_says_so():
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "no", {}, io.BytesIO(b""))

    client = SpeakerClient(base="http://x", token="t", opener=opener)
    with pytest.raises(SpeakerError, match="jarvis-core"):
        client.status()


def test_an_unreachable_server_is_a_sentence_not_a_traceback():
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    client = SpeakerClient(base="http://x", token="t", opener=opener)
    with pytest.raises(SpeakerError, match="could not reach"):
        client.status()


def test_no_token_is_caught_before_anything_is_recorded():
    with pytest.raises(SpeakerError, match="token"):
        SpeakerClient.from_config(Config(server_url="ws://x:8080/api/websocket", token=""))


def test_a_client_is_built_from_the_agents_own_settings():
    client = SpeakerClient.from_config(
        Config(server_url="ws://jarvis.lan:8080/api/websocket", token="secret")
    )
    assert client.base == "http://jarvis.lan:8080"
    assert client.token == "secret"


def test_an_absurdly_large_sample_is_refused_before_it_is_uploaded():
    seen: list = []
    client = SpeakerClient(base="http://x", token="t", opener=opener_for({}, seen))
    with pytest.raises(SpeakerError):
        client.enrol(Sample(pcm=b"\x00" * 5_000_000, rate=RATE, seconds=150))
    assert seen == []
