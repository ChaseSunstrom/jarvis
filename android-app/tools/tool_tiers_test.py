#!/usr/bin/env python3
"""Executable spec: the phone means the same thing by "tier" as the server.

`tests/contracts/tool_tiers.json` is the definition. The phone's policy engine
decides whether a call asks; the server's gate decides whether it is held; the
console draws the banner. Three surfaces, three chances to disagree — and they
did: the MCP config comment promised "2 = confirm first" while tier 2 ran
unprompted everywhere.

What this pins is the mapping between the contract's tiers and
`automation/policy/ActionTier.kt`, and the rule that a server may only ever
raise one.

Run:  python3 android-app/tools/tool_tiers_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent
CONTRACT = json.loads((REPO / "tests/contracts/tool_tiers.json").read_text(encoding="utf-8"))
TIER_KT = ANDROID / "app/src/main/kotlin/ai/jarvis/app/automation/policy/ActionTier.kt"
ENGINE_KT = ANDROID / "app/src/main/kotlin/ai/jarvis/app/automation/policy/PolicyEngine.kt"


def test_the_phone_has_one_tier_per_contract_tier() -> None:
    """Three names on the phone, three numbers on the wire, same order.

    The phone names its tiers (AUTO/NOTIFY/CONFIRM) and derives the wire number
    from the ordinal, which is why the enum's own comment says never to reorder
    them. The contract numbers them 1..3, so what has to agree is the COUNT and
    the direction, not the spelling.
    """
    src = TIER_KT.read_text(encoding="utf-8")
    body = re.search(r"enum class ActionTier \{(.*?)\}", src, re.S)
    assert body, "ActionTier is no longer an enum"
    names = [
        name
        for name in re.findall(r"^\s{4}([A-Z_]+)\s*[,;]", body.group(1), re.M)
    ]
    assert len(names) == len(CONTRACT["tiers"]), (
        f"the phone has {names} and the contract has {sorted(CONTRACT['tiers'])}"
    )
    assert names == ["AUTO", "NOTIFY", "CONFIRM"], names
    # The wire number is the ordinal plus one, which is what makes tier 3 on the
    # server the same idea as CONFIRM here.
    assert "ordinal + 1" in src, "the wire number is no longer derived from the order"


def test_only_the_third_tier_asks() -> None:
    """The bug this file exists for, on the surface that shows the prompt."""
    asks = {tier for tier, entry in CONTRACT["tiers"].items() if entry["asks_first"]}
    assert asks == {"3"}, f"the contract says {sorted(asks)} ask; the phone prompts only on 3"

    engine = ENGINE_KT.read_text(encoding="utf-8")
    # CONFIRM always asks, even when the user has said "always allow" — the
    # phone's own comment says so, and it is the tier-3 rule stated in Kotlin.
    assert "CONFIRM" in engine
    assert re.search(r"CONFIRM\s*->\s*Decision\.ASK|ActionTier\.CONFIRM.*ASK", engine, re.S), (
        "PolicyEngine no longer forces an ASK for the confirm tier"
    )


def test_the_phone_asks_once_on_tier_2_and_the_contract_says_so() -> None:
    """The contract's tier 2 is "runs immediately, NOT an approval" on the
    server; the phone's PolicyEngine asks for a NOTIFY action until the person
    chooses ALLOW_ALWAYS — its own consent for its own device (the Android
    audit, 27 Aug 2026). Both are true, and the contract now records the
    phone's variant instead of the mirror pinning a property the Kotlin never
    had."""
    entry = CONTRACT["tiers"]["2"]
    assert "phone" in entry and "ALLOW_ALWAYS" in entry["phone"], "the contract does not record the phone's ask-once rule"
    assert "phone_asks_once_on_tier_2" in CONTRACT["rules"]
    engine = ENGINE_KT.read_text(encoding="utf-8")
    assert "NOTIFY" in engine and "ALLOW_ALWAYS" in engine
    assert re.search(r"NOTIFY\s*->.*ALLOW_ALWAYS", engine, re.S), (
        "PolicyEngine no longer asks once on NOTIFY (ALLOW_ALWAYS is what makes it stop asking)"
    )


def test_a_server_may_only_raise() -> None:
    rule = CONTRACT["rules"]["a_server_may_only_raise"]
    assert "never lower" in rule
    engine = ENGINE_KT.read_text(encoding="utf-8")
    assert re.search(r"max\(|maxOf\(", engine), (
        "the phone no longer takes the MAXIMUM of the local and requested tiers, "
        "which is the only thing stopping a server from lowering one"
    )


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed "
          f"({len(CONTRACT['tiers'])} tiers, {len(CONTRACT['rules'])} rules)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
