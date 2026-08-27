"""The lock that says only one thing at a time may talk to the voice services.

Its own module because two callers need it — the scenario suite and the
intelligence eval — and a second copy of this lock would not be a lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import time
from pathlib import Path

from . import LiveError

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / ".verify" / "live"


@contextlib.contextmanager
def the_rig(timeout: float = 3600.0):
    """Only one live run at a time, on this box.

    Not tidiness: the rig shares the machine's *real* Whisper, Piper and model
    server with anything else using them, and two harnesses at once means two
    conversations against one recogniser. The symptom is not an error — it is a
    scenario failing with an empty transcript, which reads as a defect in
    Jarvis and is not one. This was observed: a milestone's live check run
    beside the whole suite failed on a turn that had passed minutes earlier.

    Waits rather than refuses, because `verify-all` runs its scripts in order
    and the right behaviour for the second one is to take its turn.
    """
    lock_path = OUT_DIR / "rig.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")  # noqa: SIM115 - held for the block
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                "live: another live run holds the rig (the voice services are "
                "shared); waiting for it to finish",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise LiveError(
                            f"another live run still holds {lock_path} after "
                            f"{timeout:g}s"
                        ) from None
                    time.sleep(2.0)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
