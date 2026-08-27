"""The rig's restore puts the house's STATE back and leaves the operator's files alone.

A run that ends while configuration.yaml is being edited must not put an hour
of a person's work back the way it found it — it did, on 27 Aug 2026, to a
`narrate:` block added while `make verify-all` ran its live slices.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from testing.live.stack import OPERATOR_FILES, restore_script  # noqa: E402


def test_the_operators_files_are_neither_swept_nor_extracted():
    script = restore_script("jarvis-core_config.tgz")
    for name in ("configuration.yaml", "automations.yaml", "scenes.yaml", "secrets.yaml", "packages/", "agents/"):
        assert name in OPERATOR_FILES
        assert f"-e '^{name}'" in script, f"{name} can be swept"
        assert f"--exclude='./{name.rstrip('/')}'" in script, f"{name} can be extracted over"


def test_the_houses_state_is_still_restored():
    script = restore_script("x.tgz")
    for name in (".storage", "jarvis.db", "notes"):
        assert f"'^{name}" not in script and f"--exclude='./{name}'" not in script
    assert "tar xzf /in/x.tgz -C /v --overwrite" in script
    assert "comm -13 /tmp/keep /tmp/have" in script
