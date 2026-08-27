"""Fetch the synthetic user's voice. Run once; the file is gitignored.

Not vendored: it is 60 MB, and the repo already keeps its Wyoming models out of
git for the same reason (`.gitignore`, `jarvis-core/wyoming/`). Not fetched
lazily at test time either — a suite that downloads 60 MB on first run is a
suite that fails differently on a machine with no network, and the failure
lands in a scenario rather than here.

    python3 testing/live/fetch_voice.py           # fetch if missing
    python3 testing/live/fetch_voice.py --check   # exit 1 if missing
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VOICE_DIR = HERE / "voices"

#: The user's voice. `low` quality on purpose: it is 60 MB rather than 110 MB,
#: it synthesises in under a second on this box, and Whisper transcribes it
#: exactly — a better voice would only make the rig slower at proving the same
#: thing. It must not be Jarvis's `en_GB-alan-medium`.
VOICE = "en_US-amy-low"
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low"


def voice_path() -> Path:
    return VOICE_DIR / f"{VOICE}.onnx"


def present() -> bool:
    model = voice_path()
    return model.is_file() and model.stat().st_size > 1_000_000 and model.with_suffix(
        ".onnx.json"
    ).is_file()


def fetch() -> None:
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    for name in (f"{VOICE}.onnx", f"{VOICE}.onnx.json"):
        target = VOICE_DIR / name
        if target.is_file() and target.stat().st_size > 1000:
            continue
        print(f"fetching {name} …", flush=True)
        with urllib.request.urlopen(f"{BASE}/{name}", timeout=300) as response:
            target.write_bytes(response.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only report whether it is here")
    args = parser.parse_args(argv)
    if present():
        print(f"{voice_path()} ({voice_path().stat().st_size // 1_000_000} MB)")
        return 0
    if args.check:
        print(f"missing: {voice_path()} — run python3 testing/live/fetch_voice.py", file=sys.stderr)
        return 1
    fetch()
    print(f"{voice_path()} ({voice_path().stat().st_size // 1_000_000} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
