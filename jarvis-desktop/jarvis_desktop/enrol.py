"""Teaching Jarvis your voice from the desktop.

The console can do this from a browser tab, which is the easy case: a browser
has `getUserMedia`. A desktop agent has no microphone API, and this app's whole
dependency story is *"the only hard dependency is the websocket client"* — so
adding `sounddevice` or `pyaudio`, both of which want native libraries, for a
thing somebody does three times in the life of an install, is the wrong trade.

So: **the operating system already has a recorder**, and if it does not, a WAV
file is a perfectly good way to hand over audio.

    python -m jarvis_desktop enrol                 # find a recorder and use it
    python -m jarvis_desktop enrol --from-file a.wav
    python -m jarvis_desktop enrol --list-recorders

## What actually has to be right

jarvis-core wants **raw little-endian 16-bit mono PCM** at a rate it is told,
not a WAV file — the header would be embedded as if it were audio, and the
first 44 bytes of a voice profile would be the letters "RIFF". So a recorded or
supplied WAV is parsed with `wave` from the standard library and its frames are
sent, with the rate it really has rather than the rate that was asked for: a
recorder given `-r 16000` on a device that cannot do 16 kHz may hand back
48 kHz, and a profile built from audio at the wrong declared rate matches
nobody.

Stereo is downmixed rather than refused, because the default input on a laptop
is very often stereo and "your microphone is stereo" is not a thing to make
somebody solve.
"""

from __future__ import annotations

import array
import logging
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "RATE",
    "Recorder",
    "EnrolError",
    "RECORDERS",
    "find_recorder",
    "read_wav",
    "record_wav",
]

#: What jarvis-core's speaker model was built around. Sent as a query
#: parameter, so a different rate is legal — but this is the one to ask for.
RATE = 16_000
WIDTH = 2

#: Longest one sample may be. Matches the console's own cap.
MAX_SECONDS = 12
MIN_SECONDS = 1.0


class EnrolError(RuntimeError):
    """Something the user can act on. Printed, never a traceback."""


@dataclass(frozen=True)
class Recorder:
    """One way to get `seconds` of audio into a WAV file."""

    name: str
    #: Built with the output path last, so the caller cannot get the order wrong.
    args: tuple[str, ...]
    #: What to tell somebody who does not have it.
    install: str

    def command(self, path: Path, seconds: int) -> list[str]:
        return [a.format(seconds=seconds, path=str(path), rate=RATE) for a in self.args]


#: In preference order. `arecord` is on every Linux desktop with ALSA;
#: `ffmpeg` is everywhere but needs to be told which input layer to use;
#: `sox` is the portable one; `afrecord` ships with macOS.
RECORDERS: tuple[Recorder, ...] = (
    Recorder(
        name="arecord",
        args=("-q", "-f", "S16_LE", "-c", "1", "-r", "{rate}", "-d", "{seconds}", "{path}"),
        install="apt install alsa-utils",
    ),
    Recorder(
        name="rec",  # sox
        args=("-q", "-c", "1", "-r", "{rate}", "-b", "16", "{path}", "trim", "0", "{seconds}"),
        install="apt install sox / brew install sox",
    ),
    Recorder(
        name="afrecord",  # macOS
        args=("-d", "LEI16@{rate}", "-c", "1", "-t", "{seconds}", "{path}"),
        install="ships with macOS",
    ),
    Recorder(
        name="ffmpeg",
        args=(
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "{input}", "-i", "{device}",
            "-t", "{seconds}", "-ac", "1", "-ar", "{rate}", "{path}",
        ),
        install="apt install ffmpeg / brew install ffmpeg",
    ),
)

#: ffmpeg needs to be told which capture layer to use, and it differs per OS.
#: Kept out of `Recorder.args` because it is the one field that is not the
#: same sentence on every platform.
_FFMPEG_INPUT = {
    "linux": ("alsa", "default"),
    "darwin": ("avfoundation", ":0"),
    "win32": ("dshow", "audio=default"),
}


def find_recorder(which: str = "", *, exists=shutil.which) -> Recorder | None:
    """The first recorder on this machine, or the one asked for.

    `exists` is injected so a test can decide what is installed without
    installing anything.
    """
    for recorder in RECORDERS:
        if which and recorder.name != which:
            continue
        if exists(recorder.name):
            return recorder
    return None


def _ffmpeg_args(recorder: Recorder, path: Path, seconds: int) -> list[str]:
    layer, device = _FFMPEG_INPUT.get(sys.platform, ("alsa", "default"))
    return [
        a.format(seconds=seconds, path=str(path), rate=RATE, input=layer, device=device)
        for a in recorder.args
    ]


def record_wav(
    recorder: Recorder,
    seconds: int,
    *,
    run=subprocess.run,
    tmpdir: str | None = None,
) -> bytes:
    """Record and hand back the WAV bytes. Raises `EnrolError` on any failure."""
    seconds = max(1, min(int(seconds), MAX_SECONDS))
    with tempfile.TemporaryDirectory(dir=tmpdir) as folder:
        path = Path(folder) / "sample.wav"
        argv = (
            _ffmpeg_args(recorder, path, seconds)
            if recorder.name == "ffmpeg"
            else recorder.command(path, seconds)
        )
        try:
            result = run(
                [recorder.name, *argv],
                capture_output=True,
                # A recorder that hangs is a terminal that never comes back.
                # The bound is the duration plus room for it to start.
                timeout=seconds + 15,
            )
        except FileNotFoundError as err:
            raise EnrolError(f"{recorder.name} is not installed ({recorder.install})") from err
        except subprocess.TimeoutExpired as err:
            raise EnrolError(f"{recorder.name} did not finish; is a microphone connected?") from err
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", "replace").strip()
            raise EnrolError(
                f"{recorder.name} failed: {detail.splitlines()[-1] if detail else 'no detail'}"
            )
        if not path.exists() or path.stat().st_size <= 44:
            raise EnrolError(f"{recorder.name} produced no audio")
        return path.read_bytes()


@dataclass(frozen=True)
class Sample:
    """Raw PCM ready to send, and the rate it is really at."""

    pcm: bytes
    rate: int
    seconds: float


def read_wav(raw: bytes) -> Sample:
    """WAV bytes -> 16-bit mono PCM, at whatever rate the file really is.

    Three things this does that a `raw[44:]` would not:

    * **finds the data chunk properly.** A 44-byte header is the common case,
      not the format: a file with a `LIST` chunk has a longer one, and slicing
      a fixed offset puts metadata into the middle of the audio.
    * **reports the REAL rate.** A recorder asked for 16 kHz on a device that
      cannot do it hands back 48 kHz, and a profile built from audio at a
      declared rate it is not at matches nobody.
    * **downmixes stereo** rather than refusing. The default input on a laptop
      is very often stereo, and that is not a problem to hand back to somebody.
    """
    import io

    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError) as err:
        raise EnrolError(f"that is not a WAV file this can read: {err}") from err

    if width != WIDTH:
        raise EnrolError(
            f"the audio is {width * 8}-bit; Jarvis needs 16-bit. Re-record it, or "
            "convert it with `sox in.wav -b 16 out.wav`."
        )
    if channels > 1:
        frames = _downmix(frames, channels)
    seconds = len(frames) / (WIDTH * rate) if rate else 0.0
    if seconds < MIN_SECONDS:
        raise EnrolError(
            f"that is only {seconds:.1f}s of audio; say the whole phrase"
        )
    return Sample(pcm=frames, rate=rate, seconds=seconds)


def _downmix(frames: bytes, channels: int) -> bytes:
    """Average the channels into one, in the standard library.

    `audioop` would do this in one call and was removed in Python 3.13, so it
    is written out — the arithmetic is four lines and outliving a deprecation
    is worth more than the brevity.
    """
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - (len(frames) % (WIDTH * channels))])
    if sys.byteorder == "big":
        # The file is little-endian; `array` read it in host order.
        samples.byteswap()
    mono = array.array("h", [0] * (len(samples) // channels))
    for i in range(len(mono)):
        block = samples[i * channels : (i + 1) * channels]
        mono[i] = int(sum(block) / channels)
    if sys.byteorder == "big":
        mono.byteswap()
    return mono.tobytes()
