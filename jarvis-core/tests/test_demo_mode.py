"""M80 — demo mode is a setting.

"I can't unset the demo mode still, so it still has a bunch of default stuff
that doesn't exist": the fixture house had no switch. `demo: enabled: false`
leaves a real house with nothing fake in it, live — off removes every demo
entity through the one delete path — and on brings them back.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import demo  # noqa: E402
from jarvis.settings import SETTINGS_BY_KEY, SettingsError  # noqa: E402


def _demo_ids(jarvis: Jarvis) -> list[str]:
    return sorted(e.entity_id for e in jarvis.entities.entities.values() if e.platform == "demo")


async def test_off_removes_the_fixture_house_and_on_brings_it_back(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"demo": {}})
    try:
        before = _demo_ids(jarvis)
        assert len(before) > 10, before
        assert jarvis.states.get(before[0]) is not None

        removed = await demo.async_remove_all(jarvis)
        assert removed == len(before)
        assert _demo_ids(jarvis) == []
        assert all(jarvis.states.get(entity_id) is None for entity_id in before)

        await demo.async_setup(jarvis, {"enabled": True})
        assert len(_demo_ids(jarvis)) == len(before)
    finally:
        await jarvis.async_stop()


async def test_off_at_boot_clears_what_an_earlier_boot_registered(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"demo": {}})
    await jarvis.async_stop()
    again = Jarvis(tmp_path)
    await again.async_setup({"demo": {"enabled": False}})
    try:
        assert _demo_ids(again) == [], "a lamp that never was is still in the registry"
    finally:
        await again.async_stop()


async def test_the_setting_applies_live_through_the_hook(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"demo": {}})
    try:
        spec = SETTINGS_BY_KEY["demo.enabled"]
        assert spec.type == "boolean" and spec.group == "House" and spec.apply == "live"
        assert spec.validate("off") is False and spec.validate("on") is True
        try:
            spec.validate("maybe")
        except SettingsError:
            pass
        else:
            raise AssertionError("'maybe' became a switch position")
        assert spec.apply_hook is not None
        spec.apply_hook(jarvis, False)
        for _ in range(50):
            await asyncio.sleep(0.05)
            if not _demo_ids(jarvis):
                break
        assert _demo_ids(jarvis) == []
        spec.apply_hook(jarvis, True)
        for _ in range(50):
            await asyncio.sleep(0.05)
            if _demo_ids(jarvis):
                break
        assert _demo_ids(jarvis)
    finally:
        await jarvis.async_stop()
