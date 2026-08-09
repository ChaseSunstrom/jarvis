#!/usr/bin/env python3
"""Executable spec for the Android policy engine.

The Kotlin in
`app/src/main/kotlin/ai/jarvis/app/automation/policy/PolicyEngine.kt` decides
whether a command from jarvis-core runs, asks, or is refused. That decision is
the whole safety story of the phone app, so it is written down twice: once in
Kotlin (which this container cannot compile) and once here, where it runs.

Two things are checked:

  1. The rules, re-implemented below, agree with an explicit truth TABLE for
     every combination of (localTier, requestedTier, userPolicy) and of the two
     global switches and the trust level. The TABLE is written out by hand, so
     a bug that lives in "the algorithm" cannot hide in both copies.
  2. The Kotlin source still contains those rules — a cheap structural check
     that catches someone editing one copy and not the other.

Run:  python3 android-app/tools/policy_truth_table_test.py
"""

from __future__ import annotations

import re
import sys
from itertools import product
from pathlib import Path

# --- the rules, mirrored from PolicyEngine.kt ------------------------------

TIERS = ["AUTO", "NOTIFY", "CONFIRM"]  # order IS severity; mirrors the enum
POLICIES = ["ALLOW_ALWAYS", "ASK", "NEVER"]
REQUESTED = [None, "AUTO", "NOTIFY", "CONFIRM"]  # None = field absent/garbage


def effective_tier(local: str, requested: str | None) -> str:
    """max(local, requested). The server can raise, never lower."""
    return max(local, requested or "AUTO", key=TIERS.index)


def decide(local: str, requested: str | None, policy: str) -> str:
    if policy == "NEVER":
        return "DENY"
    tier = effective_tier(local, requested)
    if tier == "CONFIRM":
        return "ASK"  # ALLOW_ALWAYS deliberately does NOT apply here
    if tier == "NOTIFY":
        return "ALLOW" if policy == "ALLOW_ALWAYS" else "ASK"
    return "ALLOW"


def decide_full(local, requested, policy, enabled=True, panic=False, trust="TRUSTED"):
    if panic or not enabled:
        return "DENY"
    outcome = decide(local, requested, policy)
    if trust == "UNTRUSTED" and outcome == "ALLOW":
        return "ASK"
    return outcome


def can_remember(effective: str, trust: str = "TRUSTED") -> bool:
    return effective != "CONFIRM" and trust == "TRUSTED"


def may_store(policy: str, explicit_tier: str | None, table_tier: str | None = None) -> bool:
    """May this standing answer be written to the policy store?

    ASK and NEVER always may — they only make things stricter. ALLOW_ALWAYS may
    only for a tier we KNOW is not CONFIRM; an unknown tier counts as CONFIRM.
    That last clause is the one that was missing: an omitted tier used to mean
    "skip the check", so `setPolicy(id, ALLOW_ALWAYS)` could write a standing
    yes for `send_sms`.
    """
    if policy != "ALLOW_ALWAYS":
        return True
    return can_remember(explicit_tier or table_tier or "CONFIRM")


# --- the spec, written out by hand -----------------------------------------

TABLE = {
    ("AUTO", "ALLOW_ALWAYS"): "ALLOW",
    ("AUTO", "ASK"): "ALLOW",
    ("AUTO", "NEVER"): "DENY",
    ("NOTIFY", "ALLOW_ALWAYS"): "ALLOW",
    ("NOTIFY", "ASK"): "ASK",
    ("NOTIFY", "NEVER"): "DENY",
    ("CONFIRM", "ALLOW_ALWAYS"): "ASK",  # <- the critical invariant
    ("CONFIRM", "ASK"): "ASK",
    ("CONFIRM", "NEVER"): "DENY",
}

KOTLIN_ENGINE = Path(__file__).resolve().parents[1] / (
    "app/src/main/kotlin/ai/jarvis/app/automation/policy/PolicyEngine.kt"
)
KOTLIN_TIERS = Path(__file__).resolve().parents[1] / (
    "app/src/main/kotlin/ai/jarvis/app/automation/policy/ActionTier.kt"
)


# --- tests ------------------------------------------------------------------


def test_every_combination_matches_the_table():
    for local, requested, policy in product(TIERS, REQUESTED, POLICIES):
        expected = TABLE[(effective_tier(local, requested), policy)]
        actual = decide(local, requested, policy)
        assert actual == expected, (
            f"decide(local={local}, requested={requested}, policy={policy}) "
            f"= {actual}, expected {expected}"
        )


def test_requested_tier_can_only_raise():
    for local, requested in product(TIERS, REQUESTED):
        got = effective_tier(local, requested)
        assert TIERS.index(got) >= TIERS.index(local), (
            f"requested={requested} LOWERED local={local} to {got}"
        )
        if requested is not None:
            assert TIERS.index(got) >= TIERS.index(requested)


def test_confirm_always_asks_even_when_allow_always():
    for local, requested in product(TIERS, REQUESTED):
        if effective_tier(local, requested) != "CONFIRM":
            continue
        for policy in ("ALLOW_ALWAYS", "ASK"):
            assert decide(local, requested, policy) == "ASK"
        assert decide(local, requested, "NEVER") == "DENY"
    # and a Tier-3 answer can never be remembered
    assert can_remember("CONFIRM") is False
    assert can_remember("CONFIRM", "UNTRUSTED") is False
    assert can_remember("NOTIFY") is True
    assert can_remember("NOTIFY", "UNTRUSTED") is False


def test_the_store_refuses_a_standing_yes_it_cannot_justify():
    # Tier 3, however the tier reaches the store.
    assert may_store("ALLOW_ALWAYS", "CONFIRM") is False
    assert may_store("ALLOW_ALWAYS", None, "CONFIRM") is False
    # An action of unknown tier is treated as Tier 3, not as "no check".
    assert may_store("ALLOW_ALWAYS", None, None) is False
    # The tier actually enforced wins over the table's static one, both ways.
    assert may_store("ALLOW_ALWAYS", "CONFIRM", "NOTIFY") is False
    assert may_store("ALLOW_ALWAYS", "NOTIFY", "CONFIRM") is True
    # Tier 1 and 2 remain rememberable.
    for tier in ("AUTO", "NOTIFY"):
        assert may_store("ALLOW_ALWAYS", tier) is True
        assert may_store("ALLOW_ALWAYS", None, tier) is True
    # ASK and NEVER are storable everywhere, including for an unknown action.
    for policy in ("ASK", "NEVER"):
        for tier in (None, "AUTO", "NOTIFY", "CONFIRM"):
            assert may_store(policy, tier) is True


def test_never_always_denies():
    for local, requested in product(TIERS, REQUESTED):
        for enabled, panic, trust in product((True, False), (True, False), ("TRUSTED", "UNTRUSTED")):
            assert decide_full(local, requested, "NEVER", enabled, panic, trust) == "DENY"


def test_panic_and_master_switch_deny_everything():
    for local, requested, policy in product(TIERS, REQUESTED, POLICIES):
        assert decide_full(local, requested, policy, panic=True) == "DENY"
        assert decide_full(local, requested, policy, enabled=False) == "DENY"


def test_untrusted_content_is_never_auto_allowed():
    for local, requested, policy in product(TIERS, REQUESTED, POLICIES):
        outcome = decide_full(local, requested, policy, trust="UNTRUSTED")
        assert outcome != "ALLOW", (
            f"untrusted {local}/{requested}/{policy} was auto-allowed"
        )
        expected = decide(local, requested, policy)
        assert outcome == ("ASK" if expected == "ALLOW" else expected)


def test_auto_tier_runs_without_asking():
    assert decide("AUTO", None, "ASK") == "ALLOW"
    assert decide("AUTO", "AUTO", "ALLOW_ALWAYS") == "ALLOW"
    # but a server raise turns the same action into a prompt
    assert decide("AUTO", "NOTIFY", "ASK") == "ASK"
    assert decide("AUTO", "CONFIRM", "ALLOW_ALWAYS") == "ASK"


def test_garbage_requested_tier_changes_nothing():
    for local, policy in product(TIERS, POLICIES):
        assert decide(local, None, policy) == decide(local, "AUTO", policy)


def test_kotlin_source_still_encodes_these_rules():
    """Cheap drift check: the rules must still be visible in the Kotlin."""
    assert KOTLIN_ENGINE.is_file(), f"missing {KOTLIN_ENGINE}"
    src = re.sub(r"\s+", " ", KOTLIN_ENGINE.read_text())
    required = [
        "if (userPolicy == UserPolicy.NEVER) return Decision.DENY",
        "ActionTier.max(localTier, requestedTier ?: ActionTier.AUTO)",
        "ActionTier.CONFIRM -> Decision.ASK",
        "if (userPolicy == UserPolicy.ALLOW_ALWAYS) Decision.ALLOW else Decision.ASK",
        "ActionTier.AUTO -> Decision.ALLOW",
        "if (request.panic) return Decision.DENY",
        "if (!request.automationEnabled) return Decision.DENY",
        "effectiveTier != ActionTier.CONFIRM && trust == TrustLevel.TRUSTED",
        "if (request.trust == TrustLevel.UNTRUSTED && base == Decision.ALLOW) return Decision.ASK",
        # an unknown tier must fall through to CONFIRM, not skip the check
        "return canRemember(explicitTier ?: tableTier ?: ActionTier.CONFIRM)",
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"PolicyEngine.kt no longer contains: {needle}"


def test_kotlin_tier_enum_order_matches():
    """max() relies on ordinal order, so AUTO < NOTIFY < CONFIRM must hold."""
    assert KOTLIN_TIERS.is_file(), f"missing {KOTLIN_TIERS}"
    src = KOTLIN_TIERS.read_text()
    body = src.split("enum class ActionTier {", 1)[1].split("}", 1)[0]
    order = [c.strip().rstrip(",;") for c in body.split("\n") if c.strip() and not c.strip().startswith("//")]
    order = [c for c in order if c in TIERS]
    assert order == TIERS, f"ActionTier order is {order}, expected {TIERS}"

    policy_body = src.split("enum class UserPolicy {", 1)[1]
    for name in POLICIES:
        assert name in policy_body, f"UserPolicy is missing {name}"
    for name in ("ALLOW", "ASK", "DENY"):
        assert re.search(rf"\b{name}\b", src.split("enum class Decision {", 1)[1]), name


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    combos = len(TIERS) * len(REQUESTED) * len(POLICIES)
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({combos} tier/policy combinations, "
        f"{combos * 2 * 2 * 2} including switches and trust level)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
