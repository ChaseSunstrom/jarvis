"""What a tier means, checked against the one place that says so.

Three surfaces decide things from a tier — jarvis-core's gate, the console's
banner, the phone's policy engine — and they had three ideas about it. The MCP
config comment promised "2 = confirm first" while the code ran tier 2
unprompted, so somebody reading it and installing a server got tools that ran
without asking.

`tests/contracts/tool_tiers.json` is the definition now. This is jarvis-core's
half of reading it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.const import GATED_DOMAINS, GATED_SERVICES
from jarvis.llm.tools import (
    TIER_APPROVAL,
    TIER_BACKGROUND,
    TIER_DIRECT,
    PendingRequest,
    Tool,
)

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/tool_tiers.json").read_text()
)


def test_the_three_tiers_are_the_three_the_code_has():
    assert set(CONTRACT["tiers"]) == {"1", "2", "3"}
    assert TIER_DIRECT == 1 and TIER_BACKGROUND == 2 and TIER_APPROVAL == 3


@pytest.mark.parametrize("tier", ["1", "2", "3"])
def test_every_tier_says_what_it_means_and_whether_it_asks(tier):
    entry = CONTRACT["tiers"][tier]
    assert entry["means"], f"tier {tier} has no meaning written down"
    assert isinstance(entry["asks_first"], bool)


def test_only_tier_three_asks():
    """The bug the contract exists for: tier 2 does not confirm, and never did."""
    assert CONTRACT["tiers"]["1"]["asks_first"] is False
    assert CONTRACT["tiers"]["2"]["asks_first"] is False
    assert CONTRACT["tiers"]["3"]["asks_first"] is True


def test_the_shipped_config_no_longer_claims_tier_two_confirms():
    """The claim, not the words about the claim.

    The comment now explains that it USED to say "2 = confirm first" and why
    that was wrong, so a substring search for the phrase finds its own
    correction — the same trap `test_llm.py` fell into with "do NOT tell them".
    What must not appear is the promise: a line that states tier 2 confirms.
    """
    config = (Path(__file__).resolve().parents[1] / "config/configuration.yaml").read_text()
    for line in config.splitlines():
        text = line.strip().lstrip("#").strip().lower()
        if not text.startswith(("1 =", "2 =", "3 =", "2  ", "tier 2")):
            continue
        if text.startswith(("2 =", "2  ", "tier 2")):
            assert "confirm" not in text and "approval" not in text, (
                f"the config promises a confirmation tier 2 has never given: {line!r}"
            )


def test_the_gated_domains_the_contract_names_are_the_ones_the_code_gates():
    named = set(CONTRACT["rules"]["gated_domains"].lower().replace("`", "").split())
    for domain in GATED_DOMAINS:
        assert any(domain in word for word in named), f"{domain} is gated but not written down"


def test_every_tier_three_example_has_a_service_twin_or_is_not_a_service():
    """`GATED_SERVICES` closes the automation back door; the contract says so."""
    assert GATED_SERVICES, "nothing is gated at the service layer"
    for example in CONTRACT["tiers"]["3"]["examples"]:
        if "." not in example:
            continue
        # A dotted tier-3 example is a service call, and an automation must not
        # be able to make it at tier 1.
        assert example in GATED_SERVICES or example.split(".")[0] in GATED_DOMAINS, (
            f"{example} is a tier-3 service with no twin in GATED_SERVICES"
        )


def test_mcp_defaults_to_the_tier_the_contract_states():
    config = (Path(__file__).resolve().parents[1] / "config/configuration.yaml").read_text()
    assert f"default_tier: {CONTRACT['default_for_mcp']['value']}" in config


def test_a_held_request_carries_its_sentence_on_the_field_the_contract_names():
    """M67: the console reads `summary` off the request; this is the server's
    half of the same rule. A request with no summariser carries the field
    empty rather than missing, so a surface can tell "none" from "an older
    core" without a version check."""
    rule = CONTRACT["rules"]["held_summary"]
    assert "PINNED" in rule["means"]
    request = PendingRequest(
        id="x", tool="lock_control", arguments={}, tier=TIER_APPROVAL,
        created=0.0, expires_at=1.0, context=None,
    )
    assert request.as_dict()[rule["field"]] == ""
    # And the hook a tool composes it with is declared on the Tool, per tool.
    assert Tool.__dataclass_fields__["summarise"].default is None
