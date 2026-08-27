#!/usr/bin/env bash
# M105 — the gate says no to a voice that is not yours.
#
# On 27 Aug 2026 the operator's own speaker profile accepted the rig's
# synthetic Piper voice at 4.15 against a threshold of 4.93 — timbre and
# variability (38 dimensions) outvoted a pitch block at 9.35 (8 dimensions).
# The blocks are equal votes now, and one block far beyond the threshold is
# a veto that names itself. The last check enrols the rig's voice on the
# running house as its own person and asserts the synthetic voice is that
# person only, never the operator.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M105 — the gate says no to a voice that is not yours"
use_venv

check "the composite scores the three blocks as equals, and one block far out is a veto" python3 -c '
import inspect
from jarvis.voice import speaker
src = inspect.getsource(speaker.SpeakerProfile.verify)
assert "BLOCK_VETO" in src, "no per-block veto in verify()"
assert "pitch-mismatch" in src or "-mismatch" in src, "the veto does not name its block"
assert hasattr(speaker, "BLOCK_VETO") and 1.5 <= float(speaker.BLOCK_VETO) <= 3.0, getattr(speaker, "BLOCK_VETO", None)
print("BLOCK_VETO =", speaker.BLOCK_VETO)
'
check_pytest "the speaker suite: an impostor with the right timbre and the wrong pitch is refused, the owner on a cold morning is not" 'cd jarvis-core && python3 -m pytest tests/test_speaker_gate.py -q --timeout=120 --timeout-method=signal -k "impostor or cold_morning or block"'
check_pytest "the whole speaker suite still holds" 'cd jarvis-core && python3 -m pytest tests/test_speaker_gate.py -q --timeout=120 --timeout-method=signal'
check "on the house: the rig's synthetic voice, enrolled as Rig, is accepted as Rig only — never as the operator" python3 scripts/verify/m105_voice_probe.py
verify_end
