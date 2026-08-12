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


@pytest.mark.parametrize("actions", [None, [], {}, [None], [123]])
def test_nothing_to_run_reaches_nothing(actions):
    assert gated_reach(actions) == set()
    assert needs_approval(actions) is False


@pytest.mark.parametrize("actions", ["nonsense", ["nonsense"], [{"service": "nonsense"}]])
def test_a_step_that_is_not_a_service_name_escalates(actions):
    """`"nonsense"` used to be filed under "nothing to run", and it is not.

    `ScriptRunner.async_run` does `as_list(sequence)`, so a scalar `action:` is
    genuinely run as one step, and a bare string step is rewritten to a service
    call. A word with no dot in it is therefore a call this walker cannot read
    — which is exactly what `"?"` means — rather than a no-op.
    """
    assert gated_reach(actions) == {"?"}
    assert needs_approval(actions) is True


# --- the shapes the runner executes and the walker could not see -------------
#
# `needs_approval` is the sole gate on `automation_control`, on
# `create_automation`, and on the console's "touches nothing that needs
# approval" label. Every case below was reported False before the fix, while
# the dict-form spelling of the identical automation was correctly True.
@pytest.mark.parametrize(
    "actions,why",
    [
        (["lock.unlock"], "a bare-string step is rewritten to a service call"),
        (["script.open_up"], "a script is opaque, so it escalates"),
        ([{"choose": [{"conditions": [], "sequence": ["script.open_up"]}]}],
         "nested in choose"),
        ([{"repeat": {"sequence": ["lock.unlock"]}}], "nested in repeat"),
        ([{"scene": "scene.come_home"}], "`- scene:` dispatches scene.turn_on"),
        ([{"event": "custom"}], "an event can trigger another automation"),
    ],
)
def test_the_step_shapes_the_runner_really_executes(actions, why):
    assert needs_approval(actions) is True, why


def test_the_two_walkers_do_not_disagree():
    """The differential check that would have caught the original bug.

    There are two analysers over the same action lists — this one, and
    `actions.collect_domains`, which the script integration trusts. They are
    kept separate on purpose (this one must answer "?" for indirection), but
    whenever `collect_domains` sees something gated or unreadable, this one
    must escalate. They drifted for exactly the two shapes above.
    """
    from jarvis.automation.actions import collect_domains
    from jarvis.automation.reach import GATED_DOMAINS, INDIRECT_DOMAINS

    corpus = [
        ["lock.unlock"],
        ["script.open_up"],
        [{"scene": "scene.come_home"}],
        [{"service": "lock.unlock"}],
        [{"service": "light.turn_on"}],
        [{"choose": [{"conditions": [], "sequence": ["lock.lock"]}]}],
        [{"repeat": {"count": 2, "sequence": [{"service": "switch.turn_on"}]}}],
    ]
    for actions in corpus:
        domains = set(collect_domains(actions) or [])
        risky = domains & (set(GATED_DOMAINS) | set(INDIRECT_DOMAINS))
        if risky:
            assert needs_approval(actions) is True, (
                f"collect_domains sees {sorted(risky)} in {actions!r} and the "
                "reach analyser does not — the two walkers have drifted, which "
                "is the shape of the bug this test exists for"
            )


def test_describe_says_why_a_human_was_asked():
    assert "lock" in describe_reach([{"service": "lock.unlock"}])
    assert "cannot read" in describe_reach([{"service": "script.x"}])
    assert describe_reach([{"service": "light.turn_on"}]) == (
        "touches nothing that needs approval"
    )


# --- the list the walker is handed ------------------------------------------
#
# Everything above tests what `_walk` does with a step. These test whether it
# is given the steps at all, which is the other half and the one that was
# actually broken: `needs_approval(None)` is False, so an automation the gate
# cannot read is an automation the gate waves through.


def test_the_plural_spelling_is_read_the_way_the_engine_reads_it():
    """`Automation.__init__` accepts `action` OR `actions`. The gate took the
    singular alone, so an automation written with the plural — which the engine
    deliberately supports — was analysed as empty and ran at tier 1 whatever it
    touched."""
    from jarvis.automation.reach import actions_of, needs_approval

    unlock = [{"service": "lock.unlock", "target": {"entity_id": "lock.front"}}]
    for key in ("action", "actions"):
        config = {"alias": "Front door", key: unlock}
        assert actions_of(config) == unlock, f"{key} was not read"
        assert needs_approval(actions_of(config)), (
            f"an automation written with `{key}:` unlocks a door without asking"
        )


def test_every_part_of_an_automation_has_both_spellings():
    from jarvis.automation.reach import part_of

    for singular, plural in (
        ("action", "actions"),
        ("trigger", "triggers"),
        ("condition", "conditions"),
    ):
        assert part_of({singular: ["x"]}, singular) == ["x"]
        assert part_of({plural: ["x"]}, singular) == ["x"]
    # The singular wins when both are present, exactly as the engine has it.
    assert part_of({"action": ["a"], "actions": ["b"]}, "action") == ["a"]


def test_a_config_that_is_not_a_mapping_reads_as_nothing():
    from jarvis.automation.reach import part_of

    for junk in (None, [], "action", 7):
        assert part_of(junk, "action") is None


def test_the_engine_and_the_gate_cannot_drift_apart():
    """The precedence has one definition, and the engine uses it. Two copies is
    how the gate came to read a different key from the thing it gates."""
    import inspect

    from jarvis.automation import engine

    source = inspect.getsource(engine.Automation.__init__)
    assert 'part_of(self.config, "action")' in source, (
        "the engine parses its actions with its own copy of the precedence again"
    )
