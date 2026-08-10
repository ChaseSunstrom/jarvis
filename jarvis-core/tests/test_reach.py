"""What running an automation can reach, and therefore what it costs.

`automation.trigger` is a tier-1-shaped hole straight through to whatever the
user put in the automation. If the tier came from the tool being called, "run
the bedtime routine" would unlock a door without anyone being asked. So the
tier comes from the action list, and this is the thing that reads it.

The interesting cases are all about being pessimistic in the right direction: a
wrong "safe" answer unlocks a door, a wrong "unsafe" answer costs one tap.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.automation.reach import (  # noqa: E402
    describe_reach,
    gated_reach,
    needs_approval,
    service_calls,
)


def test_a_plain_action_list_is_read():
    actions = [
        {"service": "light.turn_on", "target": {"entity_id": "light.hall"}},
        {"delay": "00:01:00"},
        {"service": "switch.turn_off"},
    ]
    assert service_calls(actions) == ["light.turn_on", "switch.turn_off"]
    assert gated_reach(actions) == set()
    assert needs_approval(actions) is False


def test_a_gated_call_is_found():
    actions = [{"service": "lock.unlock", "target": {"entity_id": "lock.front"}}]
    assert gated_reach(actions) == {"lock"}
    assert needs_approval(actions) is True


@pytest.mark.parametrize(
    "actions",
    [
        # A gated call is no less gated for being nested.
        [{"choose": [{"conditions": [], "sequence": [{"service": "lock.unlock"}]}]}],
        [{"if": [{"condition": "state"}], "then": [{"service": "notify.phone"}]}],
        [{"repeat": {"count": 3, "sequence": [{"service": "lock.lock"}]}}],
        [{"parallel": [{"service": "notify.all"}]}],
        [{"sequence": [{"sequence": [{"service": "lock.unlock"}]}]}],
    ],
)
def test_nesting_does_not_hide_a_gated_call(actions):
    assert needs_approval(actions) is True


def test_an_indirect_call_is_unknown_rather_than_safe():
    """A script is a different object with its own lifetime.

    Following into it would mean the answer is only true until somebody edits
    the script — and the edit would not touch this automation, so nothing would
    re-check. Unknown escalates instead.
    """
    for call in ("script.bedtime", "scene.away", "automation.other"):
        actions = [{"service": call}]
        assert gated_reach(actions) == {"?"}, call
        assert needs_approval(actions) is True, call


def test_a_templated_service_is_unknown():
    # Decided at run time, which is after the gate has already let it through.
    actions = [{"service": "{{ whichever }}"}]
    assert service_calls(actions) == ["?"]
    assert needs_approval(actions) is True


def test_the_newer_action_key_is_read_too():
    assert service_calls([{"action": "lock.unlock"}]) == ["lock.unlock"]


@pytest.mark.parametrize("actions", [None, [], {}, "nonsense", [None], [123]])
def test_nothing_to_run_reaches_nothing(actions):
    assert gated_reach(actions) == set()
    assert needs_approval(actions) is False


def test_describe_says_why_a_human_was_asked():
    assert "lock" in describe_reach([{"service": "lock.unlock"}])
    assert "cannot read" in describe_reach([{"service": "script.x"}])
    assert describe_reach([{"service": "light.turn_on"}]) == (
        "touches nothing that needs approval"
    )
