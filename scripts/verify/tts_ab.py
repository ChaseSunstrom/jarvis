#!/usr/bin/env python3
"""Two speech engines, the same five replies, and the numbers that cannot decide it.

M35 asks whether Piper should stay. The measurable half is here: how long each
engine takes, how much of that is real time, and whether the words survive a
round trip through the real Whisper. The unmeasurable half is the voice, so
this writes both engines' audio to `docs/tts-review/` for somebody to listen to.

    python3 scripts/verify/tts_ab.py --out docs/tts-review

Needs the Piper container the stack runs (`:10200`). Kokoro is optional —
`docker compose --profile kokoro up -d jarvis-tts` — and without it this
measures Piper alone and says so, rather than pretending there was a comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
import urllib.request
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO, REPO / "jarvis-core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from jarvis.voice.wyoming import WyomingTtsClient  # noqa: E402
from testing.live.report import wer  # noqa: E402
from testing.live.voice import Ears  # noqa: E402

#: Replies Jarvis actually gives. A benchmark on "the quick brown fox" measures
#: the wrong sentences: what matters is a confirmation, a refusal, a number, an
#: acknowledgement and a question, because those are the five shapes it speaks.
SENTENCES = [
    "The ceiling lights are on, Sir.",
    "I'm afraid there's no boiler pressure sensor in the house, so I can't tell you the reading.",
    "It's sixteen degrees outside, and the kitchen window is still open.",
    "I've queued that research and I'll tell you when it's done.",
    "Front door or garage door?",
]


def to_wav(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels or 1)
        handle.setsampwidth(width or 2)
        handle.setframerate(rate or 22050)
        handle.writeframes(pcm)
    return buffer.getvalue()


def seconds_of(data: bytes) -> float:
    """Duration from the BYTES, not the header.

    A streamed WAV's header is written before the audio exists; Kokoro's claims
    89 478 seconds. The bytes after the 44-byte header are the audio.
    """
    with wave.open(io.BytesIO(data)) as handle:
        rate, width, channels = handle.getframerate(), handle.getsampwidth(), handle.getnchannels()
    return max(0, len(data) - 44) / max(1, rate * width * channels)


def kokoro(url: str, text: str, voice: str) -> tuple[float, bytes]:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/audio/speech",
        data=json.dumps(
            {"model": "kokoro", "input": text, "voice": voice, "response_format": "wav"}
        ).encode(),
        headers={"content-type": "application/json"},
    )
    started = time.perf_counter()
    data = urllib.request.urlopen(request, timeout=300).read()
    return time.perf_counter() - started, data


async def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ears = Ears()
    piper = WyomingTtsClient(args.piper_host, args.piper_port, voice=args.piper_voice)

    have_kokoro = True
    try:
        urllib.request.urlopen(f"{args.kokoro_url.rstrip('/')}/audio/voices", timeout=5).read()
    except Exception as err:  # noqa: BLE001 - absent is a result, not a crash
        print(f"no speech service at {args.kokoro_url} ({err}); measuring Piper alone")
        have_kokoro = False

    rows = []
    for index, text in enumerate(SENTENCES, 1):
        started = time.perf_counter()
        pcm, rate, width, channels = await piper.synthesize(text)
        piper_took = time.perf_counter() - started
        piper_audio = to_wav(pcm, rate, width, channels)
        (out / f"{index}-piper.wav").write_bytes(piper_audio)
        piper_heard = await ears.hear_wav(piper_audio)
        row = {
            "text": text,
            "piper": {
                "synth_s": round(piper_took, 2),
                "audio_s": round(seconds_of(piper_audio), 2),
                "rtf": round(piper_took / max(0.01, seconds_of(piper_audio)), 2),
                "wer": round(wer(text, piper_heard), 3),
                "heard": piper_heard,
            },
        }
        if have_kokoro:
            took, data = kokoro(args.kokoro_url, text, args.kokoro_voice)
            (out / f"{index}-kokoro.wav").write_bytes(data)
            heard = await ears.hear_wav(data)
            row["kokoro"] = {
                "synth_s": round(took, 2),
                "audio_s": round(seconds_of(data), 2),
                "rtf": round(took / max(0.01, seconds_of(data)), 2),
                "wer": round(wer(text, heard), 3),
                "heard": heard,
            }
        rows.append(row)
        line = f"{index}. piper {row['piper']['synth_s']:5.2f}s {row['piper']['rtf']:.2f}x wer {row['piper']['wer']:.2f}"
        if have_kokoro:
            line += (
                f"   |   kokoro {row['kokoro']['synth_s']:5.2f}s "
                f"{row['kokoro']['rtf']:.2f}x wer {row['kokoro']['wer']:.2f}"
            )
        print(line, flush=True)

    print()
    for name in ("piper", "kokoro"):
        measured = [row[name] for row in rows if name in row]
        if not measured:
            continue
        print(
            f"{name:8} median synth {statistics.median(m['synth_s'] for m in measured):5.2f}s   "
            f"RTF {statistics.mean(m['rtf'] for m in measured):.2f}x   "
            f"mean WER {statistics.mean(m['wer'] for m in measured):.3f}"
        )
    (out / "measurements.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"\nwritten: {out}/measurements.json, and the audio beside it")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/tts-review")
    parser.add_argument("--piper-host", default="127.0.0.1")
    parser.add_argument("--piper-port", type=int, default=10200)
    parser.add_argument("--piper-voice", default="en_GB-alan-medium")
    parser.add_argument("--kokoro-url", default="http://127.0.0.1:8880/v1")
    parser.add_argument("--kokoro-voice", default="bm_george")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
