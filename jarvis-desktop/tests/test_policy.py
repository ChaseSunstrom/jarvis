"""The policy truth table — the executable spec for the whole safety model.

This is the desktop half of a spec that is written down twice: once here and
once in ``android-app/tools/policy_truth_table_test.py``. The phone and the
desktop must behave identically, so the TABLE below is copied from that file
verbatim and both are checked against it.

The table is written out BY HAND. A bug that lives in "the algorithm" therefore
cannot hide in both copies: the implementation is derived from the rules, and
the expectations are not.
"""

from __future__ import annotations

import itertools
import json

import pytest

from jarvis_desktop.policy import (
    ActionTier,
    Decision,
    InMemoryPolicyProvider,
    PolicyEngine,
    PolicyRequest,
    PolicyStore,
    TrustLevel,
    UserPolicy,
)

TIERS = [ActionTier.AUTO, ActionTier.NOTIFY, ActionTier.CONFIRM]
POLICIES = [UserPolicy.ALLOW_ALWAYS, UserPolicy.ASK, UserPolicy.NEVER]
#: None = the `tier` field absent, null, or garbage.
REQUESTED = [None, ActionTier.AUTO, ActionTier.NOTIFY, ActionTier.CONFIRM]

#: The spec, by hand. Keyed by (effective tier, user policy).
TABLE = {
    (ActionTier.AUTO, UserPolicy.ALLOW_ALWAYS): Decision.ALLOW,
    (ActionTier.AUTO, UserPolicy.ASK): Decision.ALLOW,
    (ActionTier.AUTO, UserPolicy.NEVER): Decision.DENY,
    (ActionTier.NOTIFY, UserPolicy.ALLOW_ALWAYS): Decision.ALLOW,
    (ActionTier.NOTIFY, UserPolicy.ASK): Decision.ASK,
    (ActionTier.NOTIFY, UserPolicy.NEVER): Decision.DENY,
    (ActionTier.CONFIRM, UserPolicy.ALLOW_ALWAYS): Decision.ASK,  # <- the critical invariant
    (ActionTier.CONFIRM, UserPolicy.ASK): Decision.ASK,
    (ActionTier.CONFIRM, UserPolicy.NEVER): Decision.DENY,
}


def decide(local, requested, policy, enabled=True, panic=False, trust=TrustLevel.TRUSTED):
    return PolicyEngine.decide(
        PolicyRequest(
            action_id="an_action",
            local_tier=local,
            requested_tier=requested,
            user_policy=policy,
            automation_enabled=enabled,
            panic=panic,
            trust=trust,
        )
    )


# --- the table --------------------------------------------------------------


def test_every_combination_matches_the_table():
    for local, requested, policy in itertools.product(TIERS, REQUESTED, POLICIES):
        effective = PolicyEngine.effective_tier(local, requested)
        expected = TABLE[(effective, policy)]
        actual = PolicyEngine.decide_parts("an_action", local, requested, policy)
        assert actual == expected, (
            f"decide(local={local.name}, requested={requested}, policy={policy.name}) "
            f"= {actual}, expected {expected}"
        )


def test_the_table_covers_every_combination():
    """36 (local x requested x policy) combinations, 288 with the switches."""
    assert len(TIERS) * len(REQUESTED) * len(POLICIES) == 36
    assert set(TABLE) == {(t, p) for t in TIERS for p in POLICIES}


# --- rule 4: the server may only raise --------------------------------------


def test_requested_tier_can_only_raise():
    for local, requested in itertools.product(TIERS, REQUESTED):
        got = PolicyEngine.effective_tier(local, requested)
        assert got >= local, f"requested={requested} LOWERED local={local.name} to {got.name}"
        if requested is not None:
            assert got >= requested


def test_a_server_claiming_tier_1_for_an_sms_still_gets_tier_3():
    """The headline case: the incoming tier is a hint from a machine that may
    have been prompt-injected."""
    assert PolicyEngine.effective_tier(ActionTier.CONFIRM, ActionTier.AUTO) == ActionTier.CONFIRM
    assert (
        PolicyEngine.decide_parts(
            "run_command", ActionTier.CONFIRM, ActionTier.AUTO, UserPolicy.ALLOW_ALWAYS
        )
        == Decision.ASK
    )


def test_garbage_requested_tier_changes_nothing():
    for local, policy in itertools.product(TIERS, POLICIES):
        baseline = PolicyEngine.decide_parts("a", local, None, policy)
        assert PolicyEngine.decide_parts("a", local, ActionTier.AUTO, policy) == baseline


@pytest.mark.parametrize(
    "raw", [0, 4, 99, -1, None, "", "three", "allow", True, False, 1.5, [], {}, "0"]
)
def test_unparseable_wire_tiers_become_none_not_low(raw):
    parsed = ActionTier.from_wire(raw)
    assert parsed is None or parsed in TIERS
    # Whatever it parsed to, it cannot pull a CONFIRM down.
    assert PolicyEngine.effective_tier(ActionTier.CONFIRM, parsed) == ActionTier.CONFIRM


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, ActionTier.AUTO),
        (2, ActionTier.NOTIFY),
        (3, ActionTier.CONFIRM),
        ("3", ActionTier.CONFIRM),
        ("CONFIRM", ActionTier.CONFIRM),
    ],
)
def test_valid_wire_tiers_parse(raw, expected):
    assert ActionTier.from_wire(raw) == expected


# --- rule 5: CONFIRM always asks -------------------------------------------


def test_confirm_always_asks_even_when_allow_always():
    for local, requested in itertools.product(TIERS, REQUESTED):
        if PolicyEngine.effective_tier(local, requested) != ActionTier.CONFIRM:
            continue
        for policy in (UserPolicy.ALLOW_ALWAYS, UserPolicy.ASK):
            assert PolicyEngine.decide_parts("a", local, requested, policy) == Decision.ASK
        assert (
            PolicyEngine.decide_parts("a", local, requested, UserPolicy.NEVER) == Decision.DENY
        )


def test_a_tier_three_answer_can_never_be_remembered():
    assert PolicyEngine.can_remember(ActionTier.CONFIRM) is False
    assert PolicyEngine.can_remember(ActionTier.CONFIRM, TrustLevel.UNTRUSTED) is False
    assert PolicyEngine.can_remember(ActionTier.NOTIFY) is True
    assert PolicyEngine.can_remember(ActionTier.NOTIFY, TrustLevel.UNTRUSTED) is False
    assert PolicyEngine.can_remember(ActionTier.AUTO) is True


def test_the_provider_refuses_to_store_allow_always_for_tier_three():
    provider = InMemoryPolicyProvider()
    provider.remember("run_command", UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM)
    assert provider.policy_for("run_command") == UserPolicy.ASK
    provider.remember("write_file", UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY)
    assert provider.policy_for("write_file") == UserPolicy.ALLOW_ALWAYS
    # NEVER is always storable, at any tier.
    provider.remember("run_command", UserPolicy.NEVER, ActionTier.CONFIRM)
    assert provider.policy_for("run_command") == UserPolicy.NEVER


# --- rule 3: NEVER wins -----------------------------------------------------


def test_never_always_denies():
    for local, requested in itertools.product(TIERS, REQUESTED):
        for enabled, panic, trust in itertools.product(
            (True, False), (True, False), (TrustLevel.TRUSTED, TrustLevel.UNTRUSTED)
        ):
            assert (
                decide(local, requested, UserPolicy.NEVER, enabled, panic, trust) == Decision.DENY
            )


def test_never_beats_a_previously_stored_allow_always():
    provider = InMemoryPolicyProvider({"set_volume": UserPolicy.ALLOW_ALWAYS})
    assert decide(ActionTier.AUTO, None, provider.policy_for("set_volume")) == Decision.ALLOW
    provider.remember("set_volume", UserPolicy.NEVER, ActionTier.AUTO)
    assert decide(ActionTier.AUTO, None, provider.policy_for("set_volume")) == Decision.DENY


# --- rules 1 and 2: the global switches -------------------------------------


def test_panic_and_master_switch_deny_everything():
    for local, requested, policy in itertools.product(TIERS, REQUESTED, POLICIES):
        assert decide(local, requested, policy, panic=True) == Decision.DENY
        assert decide(local, requested, policy, enabled=False) == Decision.DENY
        assert decide(local, requested, policy, enabled=False, panic=True) == Decision.DENY


# --- rule 8: untrusted content is never auto-allowed ------------------------


def test_untrusted_content_is_never_auto_allowed():
    for local, requested, policy in itertools.product(TIERS, REQUESTED, POLICIES):
        outcome = decide(local, requested, policy, trust=TrustLevel.UNTRUSTED)
        assert outcome != Decision.ALLOW, f"untrusted {local}/{requested}/{policy} was auto-allowed"
        baseline = PolicyEngine.decide_parts("a", local, requested, policy)
        assert outcome == (Decision.ASK if baseline == Decision.ALLOW else baseline)


# --- rule 7 ------------------------------------------------------------------


def test_auto_tier_runs_without_asking():
    assert PolicyEngine.decide_parts("a", ActionTier.AUTO, None, UserPolicy.ASK) == Decision.ALLOW
    assert (
        PolicyEngine.decide_parts("a", ActionTier.AUTO, ActionTier.AUTO, UserPolicy.ALLOW_ALWAYS)
        == Decision.ALLOW
    )
    # ...but a server raise turns the same action into a prompt.
    assert (
        PolicyEngine.decide_parts("a", ActionTier.AUTO, ActionTier.NOTIFY, UserPolicy.ASK)
        == Decision.ASK
    )
    assert (
        PolicyEngine.decide_parts(
            "a", ActionTier.AUTO, ActionTier.CONFIRM, UserPolicy.ALLOW_ALWAYS
        )
        == Decision.ASK
    )


# --- explanations and messages ----------------------------------------------


def test_explain_mentions_a_server_raise():
    request = PolicyRequest("open_url", ActionTier.AUTO, ActionTier.CONFIRM, UserPolicy.ASK)
    text = PolicyEngine.explain(request, Decision.ASK)
    assert "raised by server" in text
    assert "effective=CONFIRM" in text
    assert "local=AUTO" in text


def test_explain_does_not_claim_a_raise_when_the_server_agreed():
    request = PolicyRequest("run_command", ActionTier.CONFIRM, ActionTier.CONFIRM, UserPolicy.ASK)
    assert "raised by server" not in PolicyEngine.explain(request, Decision.ASK)


def test_deny_message_names_the_reason():
    panic = PolicyRequest("a", ActionTier.AUTO, None, UserPolicy.ASK, panic=True)
    assert "panic" in PolicyEngine.deny_message(panic).lower()
    off = PolicyRequest("a", ActionTier.AUTO, None, UserPolicy.ASK, automation_enabled=False)
    assert "switched off" in PolicyEngine.deny_message(off)
    blocked = PolicyRequest("send_sms", ActionTier.AUTO, None, UserPolicy.NEVER)
    assert "send_sms" in PolicyEngine.deny_message(blocked)


# --- the persistent store ---------------------------------------------------


def test_store_round_trips(tmp_path):
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    assert store.policy_for("anything") == UserPolicy.ASK
    assert store.automation_enabled is True
    assert store.panic is False

    store.set_policy("write_file", UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY)
    store.set_policy("run_command", UserPolicy.NEVER, ActionTier.CONFIRM)
    store.panic = True

    reopened = PolicyStore(path)
    assert reopened.policy_for("write_file") == UserPolicy.ALLOW_ALWAYS
    assert reopened.policy_for("run_command") == UserPolicy.NEVER
    assert reopened.panic is True
    assert reopened.automation_live is False


def test_store_refuses_allow_always_for_tier_three(tmp_path):
    store = PolicyStore(tmp_path / "policy.json")
    store.remember("run_command", UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM)
    assert store.policy_for("run_command") == UserPolicy.ASK
    assert PolicyStore(tmp_path / "policy.json").policy_for("run_command") == UserPolicy.ASK


def test_store_picks_up_an_external_edit(tmp_path):
    """The dispatcher re-reads the store after a prompt returns, so hitting
    panic *while the prompt is up* has to stop the action."""
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    store.set_policy("set_volume", UserPolicy.ALLOW_ALWAYS, ActionTier.AUTO)
    assert store.policy_for("set_volume") == UserPolicy.ALLOW_ALWAYS

    payload = json.loads(path.read_text())
    payload["panic"] = True
    payload["policies"]["set_volume"] = "never"
    path.write_text(json.dumps(payload))

    assert store.panic is True
    assert store.policy_for("set_volume") == UserPolicy.NEVER


def test_corrupt_store_fails_closed(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text("{not json at all")
    store = PolicyStore(path)
    assert store.policy_for("run_command") == UserPolicy.ASK
    # A corrupt file must not read as "panic off, everything allowed forever";
    # it reads as defaults, and every default is ASK.
    assert store.all_policies() == {}


def test_unknown_stored_values_fall_back_to_ask(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps({"policies": {"a": "yolo", "b": "ALLOW", "c": 7}, "panic": "maybe"})
    )
    store = PolicyStore(path)
    assert store.policy_for("a") == UserPolicy.ASK
    assert store.policy_for("b") == UserPolicy.ALLOW_ALWAYS
    assert store.policy_for("c") == UserPolicy.ASK
    # Only a literal `true` turns panic on.
    assert store.panic is False


def test_store_file_is_not_world_readable(tmp_path):
    import os
    import stat

    if os.name == "nt":  # pragma: no cover - POSIX modes only
        pytest.skip("POSIX permissions only")
    path = tmp_path / "policy.json"
    store = PolicyStore(path)
    store.set_policy("a", UserPolicy.NEVER)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0, f"policy store is readable by others: {oct(mode)}"
