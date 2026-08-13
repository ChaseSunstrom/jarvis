"""When an automation breaks at three in the morning, somebody has to be able to tell.

## Why this exists

`Automation._async_execute` caught every exception from its action list, called
`_LOGGER.exception(...)` and returned `None`. That was the whole of it, and
three things followed:

  * `last_triggered` had already been written **before** the actions ran, so
    the entity said "ran at 03:00" for a run that failed at 03:00;
  * the entity's state stayed `on`, which means "enabled", and nothing on it
    said otherwise;
  * no event fired, so nothing could react and nothing could tell you.

On a headless box a log line is not a notification. The 3am automation is the
one everybody writes and nobody watches, and the first sign of trouble was
noticing weeks later that something had quietly stopped happening.

A failure now lands somewhere a person **or a rule** can reach: two attributes
on the entity, and `automation_failed` on the bus — which is itself a trigger,
so "tell me when an automation breaks" is now an automation you can write.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.const import EVENT_AUTOMATION_FAILED  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402


@pytest.fixture
async def jarvis(tmp_path):
    instance = Jarvis(tmp_path)
    await instance.async_setup({"automation": []})
    yield instance
    await instance.async_stop()


async def _automation(jarvis: Jarvis, actions: list) -> object:
    """One automation with no trigger, attached and ready to be run by hand."""
    manager = jarvis.data["automation"]
    return await manager.async_add(
        {"id": "boom", "alias": "Nightly tidy", "trigger": [], "action": actions}
    )


async def _explode(jarvis: Jarvis) -> object:
    async def _raise(call):
        raise RuntimeError("the printer is on fire")

    jarvis.services.register("demo", "explode", _raise)
    return await _automation(jarvis, [{"service": "demo.explode"}])


# ---------------------------------------------------------------------------
async def test_a_failure_is_recorded_on_the_entity(jarvis):
    """Not only in a log file. The entity is what the console lists."""
    automation = await _explode(jarvis)

    await automation.async_trigger(wait=True)

    state = jarvis.states.get(automation.entity_id)
    assert state is not None
    assert state.attributes["last_error"], "the failure left no mark"
    assert "the printer is on fire" in state.attributes["last_error"]
    assert state.attributes["last_error_at"], "no time recorded"


async def test_a_failure_fires_an_event_something_can_trigger_on(jarvis):
    """Which is what makes "tell me when a rule breaks" writable as a rule."""
    seen: list = []
    jarvis.bus.listen(EVENT_AUTOMATION_FAILED, lambda event: seen.append(event.data))

    automation = await _explode(jarvis)
    await automation.async_trigger(wait=True)

    assert seen, "nothing was told"
    assert seen[0]["entity_id"] == automation.entity_id
    assert seen[0]["name"] == "Nightly tidy"
    assert "printer" in seen[0]["error"]


async def test_a_failure_does_not_stop_the_automation_running_again(jarvis):
    """Fail-soft, deliberately.

    A rule that disabled itself on one bad night would be a rule that silently
    stops working after a transient error — the same disappearance this file
    exists to prevent, arrived at from the other direction.
    """
    automation = await _explode(jarvis)

    await automation.async_trigger(wait=True)
    await automation.async_trigger(wait=True)

    assert jarvis.states.get(automation.entity_id).state == "on"


async def test_a_clean_run_clears_the_mark(jarvis):
    """A warning that never goes away is a warning nobody reads.

    One bad night must not leave a permanent red flag on an automation that
    has worked every day since.
    """
    calls: list = []

    async def _sometimes(call):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")

    jarvis.services.register("demo", "sometimes", _sometimes)
    automation = await _automation(jarvis, [{"service": "demo.sometimes"}])

    await automation.async_trigger(wait=True)
    assert jarvis.states.get(automation.entity_id).attributes["last_error"]

    await automation.async_trigger(wait=True)
    attributes = jarvis.states.get(automation.entity_id).attributes
    assert "last_error" not in attributes
    assert "last_error_at" not in attributes


async def test_an_automation_that_has_never_failed_carries_no_mark(jarvis):
    """Absent, not present-and-null.

    The entity layer strips None-valued attributes — `description` behaves the
    same way — so "never failed" reads as the key not being there. Following
    the existing convention rather than inventing a second one for this pair
    is the whole of the decision.
    """
    jarvis.services.register("demo", "fine", lambda call: None)
    automation = await _automation(jarvis, [{"service": "demo.fine"}])

    await automation.async_trigger(wait=True)

    attributes = jarvis.states.get(automation.entity_id).attributes
    assert "last_error" not in attributes
    assert "last_error_at" not in attributes


# ---------------------------------------------------------------------------
# the sun trigger: "30 minutes before sunset"
# ---------------------------------------------------------------------------
async def test_the_rule_everybody_writes_first_is_expressible(jarvis):
    """`platform: sun` with an offset.

    The *condition* side has parsed `"sunset - 00:30"` since the beginning, and
    `SunData.next()` has always existed — there was simply no trigger platform,
    so the archetypal home automation could only be approximated by a `state`
    trigger on `sun.sun` (no offset, fires up to a minute late) or by polling
    with `time_pattern` and filtering with the condition that already knew how.
    """
    from jarvis.automation.triggers import TRIGGER_PLATFORMS

    assert "sun" in TRIGGER_PLATFORMS


async def test_a_sun_trigger_without_the_sun_integration_is_inert_not_fatal(jarvis):
    """It cannot fire, and it must not take the other rules down either.

    Same posture as an unknown platform: warn, attach nothing, let the rest of
    the automation file load.
    """
    from jarvis.automation.triggers import async_attach_trigger

    fired: list = []
    unsub = await async_attach_trigger(
        jarvis, {"platform": "sun", "event": "sunset"}, lambda t: fired.append(t)
    )

    assert callable(unsub)
    unsub()
    assert fired == []


def test_the_offset_is_written_the_way_the_condition_side_writes_it():
    """One concept, one spelling.

    The condition side says `"sunset - 00:30"`. A trigger that wanted a number
    of seconds instead would be the same idea spelled two ways in one config
    file, which is how somebody ends up with a rule firing half an hour late
    and no idea why.
    """
    from jarvis.automation.triggers import _sun_offset

    assert _sun_offset("-00:30").total_seconds() == -1800
    assert _sun_offset("00:45").total_seconds() == 2700
    assert _sun_offset("+01:00").total_seconds() == 3600
    assert _sun_offset(None).total_seconds() == 0
    # Unreadable is no offset rather than a crash: the rule still fires at
    # sunset, which is far better than an automation file that will not load.
    assert _sun_offset("half past").total_seconds() == 0
