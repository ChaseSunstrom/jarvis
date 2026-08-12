#!/usr/bin/env python3
"""Executable spec for the Android action DISPATCHER.

`policy_truth_table_test.py` pins down `PolicyEngine.decide`. That is the truth
table, but it is not the whole gate: the property that matters — "a Tier-3
action never runs without a human approving THIS invocation" — lives in the
*ordering* inside `ActionRegistry.dispatch`, not in the truth table. A dispatch
that consulted the engine and then executed anyway would pass every test in the
other file.

So this file models the dispatcher as a state machine, runs the whole reachable
state space through it, and asserts the invariants on what actually executed.
It also structurally checks that `ActionRegistry.kt` still performs the steps in
the order modelled here.

Run:  python3 android-app/tools/dispatch_spec_test.py
      python3 -m pytest android-app/tools/dispatch_spec_test.py -q
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

TIERS = ["AUTO", "NOTIFY", "CONFIRM"]
POLICIES = ["ALLOW_ALWAYS", "ASK", "NEVER"]
REQUESTED = [None, "AUTO", "NOTIFY", "CONFIRM"]
TRUSTS = ["TRUSTED", "UNTRUSTED"]
VERDICTS = ["APPROVED", "APPROVED_ALWAYS", "DENIED", "TIMEOUT"]

REGISTRY = Path(__file__).resolve().parents[1] / (
    "app/src/main/kotlin/ai/jarvis/app/automation/actions/ActionRegistry.kt"
)


def _max_tier(a: str, b: str) -> str:
    return max(a, b, key=TIERS.index)


def decide(local, requested, policy, enabled=True, panic=False, trust="TRUSTED"):
    """PolicyEngine.decide(PolicyRequest) — mirrored from the other spec."""
    if panic or not enabled:
        return "DENY"
    if policy == "NEVER":
        return "DENY"
    tier = _max_tier(local, requested or "AUTO")
    if tier == "CONFIRM":
        base = "ASK"
    elif tier == "NOTIFY":
        base = "ALLOW" if policy == "ALLOW_ALWAYS" else "ASK"
    else:
        base = "ALLOW"
    if trust == "UNTRUSTED" and base == "ALLOW":
        return "ASK"
    return base


def can_remember(effective, trust="TRUSTED"):
    return effective != "CONFIRM" and trust == "TRUSTED"


# --- the model --------------------------------------------------------------


@dataclass
class Store:
    """PolicyProvider. `during_prompt` is what the user does while it is up."""

    policy: str = "ASK"
    enabled: bool = True
    panic: bool = False

    def remember(self, effective, value):
        # PolicyStore.remember / InMemoryPolicyProvider.remember
        if value == "ALLOW_ALWAYS" and not can_remember(effective):
            return
        self.policy = value


@dataclass
class Action:
    tier: str = "AUTO"
    per_call_tier: str | None = None      # tierFor(params)
    unsupported: bool = False
    available: bool = True
    tier_for_throws: bool = False
    #: requiredPermissions has something this device does not hold.
    needs_permission: bool = False
    #: resolvePermissions has something this device does not hold — contacts,
    #: for the two actions that turn a name into a number before the prompt.
    resolve_needs_permission: bool = False


@dataclass
class Trace:
    status: str = ""
    executed: bool = False
    prompted: bool = False
    verdict: str | None = None
    audited: int = 0
    enforced_tier: str | None = None
    remembered: bool = False
    #: Which permission dialogs were raised: "resolve", "execute", or neither.
    dialogs: list[str] = field(default_factory=list)
    #: True when a permission the action MIGHT need was still missing at the
    #: moment execute was called. Not a refusal — see step 7.
    permission_missing: bool = False
    events: list[str] = field(default_factory=list)


def dispatch(
    action: Action | None,
    requested_tier: str | None,
    store: Store,
    verdict: str = "DENIED",
    trust: str = "TRUSTED",
    *,
    panic_during_prompt: bool = False,
    never_during_prompt: bool = False,
    disable_during_prompt: bool = False,
    panic_during_resolve: bool = False,
    grant_permission: bool = True,
) -> Trace:
    """ActionRegistry.dispatch, step for step."""
    t = Trace()
    waited = False

    # 1. look up
    if action is None:
        t.status, t.audited, t.enforced_tier = "unsupported", 1, "CONFIRM"
        t.events.append("unknown-action")
        return t

    # 2. honest no, BEFORE any policy work, so these never prompt
    if action.unsupported or not action.available:
        t.status, t.audited, t.enforced_tier = "unsupported", 1, action.tier
        t.events.append("unsupported")
        return t

    # 3. the standing bans, before anything is resolved or asked for.
    #
    # Panic, the master switch and a standing NEVER do not depend on the tier,
    # so they can be decided without the resolved parameters — and they have to
    # be, because resolution can now raise an Android permission dialog and
    # nothing arriving from a server should be able to do that on a phone whose
    # owner has hit panic. In the Kotlin this asks PolicyEngine at
    # CONFIRM/CONFIRM, which is DENY for exactly these three and nothing else.
    if decide("CONFIRM", "CONFIRM", store.policy, store.enabled, store.panic, trust) == "DENY":
        t.status, t.audited, t.enforced_tier = "denied", 1, action.tier
        t.events.append("denied-by-standing-ban")
        return t

    # 4. the resolver's own permission, then resolve. "Text Sam" needs contacts
    #    BEFORE the consent prompt, because the prompt has to show the number
    #    rather than the name — so this one grant is asked for ahead of the gate.
    if action.resolve_needs_permission:
        waited = True
        t.dialogs.append("resolve")
    if panic_during_resolve:
        store.panic = True

    # 5. local tier is the authority; tierFor may only raise; a throw = CONFIRM
    per_call = "CONFIRM" if action.tier_for_throws else (action.per_call_tier or action.tier)
    local = _max_tier(action.tier, per_call)
    effective = _max_tier(local, requested_tier or "AUTO")
    t.enforced_tier = effective

    # 6. decide. Still reachable as DENY: the store is re-read here, and the
    #    resolution between step 3 and now takes real time.
    decision = decide(local, requested_tier, store.policy, store.enabled, store.panic, trust)

    if decision == "DENY":
        t.status, t.audited = "denied", 1
        t.events.append("denied-by-policy")
        return t

    if decision == "ASK":
        rememberable = can_remember(effective, trust)
        t.prompted = True
        t.verdict = verdict
        t.events.append("prompted")
        if verdict not in ("APPROVED", "APPROVED_ALWAYS"):
            t.status, t.audited = "denied", 1
            t.events.append("denied-by-human")
            return t
        if verdict == "APPROVED_ALWAYS" and rememberable:
            store.remember(effective, "ALLOW_ALWAYS")
            t.remembered = True
        waited = True

    # 7. the Android permission, AFTER the human. Asking first would put an OS
    #    dialog in front of somebody about to refuse the command, and would hand
    #    a server that sends nonsense a dialog-spam primitive.
    #
    #    It ASKS and does not GATE. Which of an action's declared permissions a
    #    given call actually needs is the action's question — `get_location`
    #    declares both location grants and answers a coarse question from the
    #    coarse one alone — so refusing here on anything still missing turned
    #    "Approximate" into a permanent failure of a command that worked.
    #    execute() runs either way and refuses for itself.
    if action.needs_permission:
        waited = True
        t.dialogs.append("execute")
        if not grant_permission:
            t.permission_missing = True
            t.events.append("permission-still-missing")

    # 8. re-read the store: the kill switch outlives an approval AND a
    #    permission dialog. Either can sit on screen for a minute.
    if waited:
        if panic_during_prompt:
            store.panic = True
        if never_during_prompt:
            store.policy = "NEVER"
        if disable_during_prompt:
            store.enabled = False
        fresh = decide(
            local, requested_tier, store.policy, store.enabled, store.panic, trust
        )
        if fresh == "DENY":
            t.status, t.audited = "denied", 1
            t.events.append("revoked-while-prompt-was-up")
            return t

    # 9. execute
    t.executed = True
    t.status, t.audited = "ok", 1
    t.events.append("executed")
    return t


# --- the invariants ---------------------------------------------------------


def _space():
    """Every reachable dispatch, as (kwargs, action, store)."""
    for tier, per_call, req, pol, trust, verdict in product(
        TIERS, [None] + TIERS, REQUESTED, POLICIES, TRUSTS, VERDICTS
    ):
        yield Action(tier=tier, per_call_tier=per_call), req, pol, trust, verdict


def test_tier3_never_executes_without_a_fresh_approval():
    for action, req, pol, trust, verdict in _space():
        store = Store(policy=pol)
        t = dispatch(action, req, store, verdict, trust)
        if t.enforced_tier != "CONFIRM":
            continue
        if t.executed:
            assert t.prompted, f"CONFIRM executed without a prompt: {action} {req} {pol}"
            assert t.verdict in ("APPROVED", "APPROVED_ALWAYS"), (
                f"CONFIRM executed on verdict={t.verdict}"
            )


def test_tier3_approval_is_never_remembered():
    for action, req, pol, trust, verdict in _space():
        store = Store(policy=pol)
        t = dispatch(action, req, store, verdict, trust)
        if t.enforced_tier == "CONFIRM":
            assert not t.remembered, "a Tier-3 answer was remembered"
            assert store.policy != "ALLOW_ALWAYS" or pol == "ALLOW_ALWAYS", (
                "dispatch wrote ALLOW_ALWAYS for a Tier-3 action"
            )


def test_a_standing_allow_always_cannot_run_tier3():
    """The nastiest shape: the user allowed Tier 2, the server raises to Tier 3."""
    for tier, req in product(TIERS, REQUESTED):
        store = Store(policy="ALLOW_ALWAYS")
        t = dispatch(Action(tier=tier), req, store, verdict="DENIED")
        if _max_tier(tier, req or "AUTO") == "CONFIRM":
            assert not t.executed
            assert t.prompted
            assert t.status == "denied"


def test_the_server_can_only_raise():
    for tier, req in product(TIERS, REQUESTED):
        t = dispatch(Action(tier=tier), req, Store(policy="ASK"), "APPROVED")
        assert TIERS.index(t.enforced_tier) >= TIERS.index(tier), (
            f"requested={req} lowered local={tier} to {t.enforced_tier}"
        )
    # and specifically: a Tier-3 action a hostile server calls "tier 1"
    t = dispatch(Action(tier="CONFIRM"), "AUTO", Store(policy="ALLOW_ALWAYS"), "DENIED")
    assert t.enforced_tier == "CONFIRM"
    assert t.prompted and not t.executed


def test_per_call_tier_can_only_raise_and_a_throw_means_confirm():
    # http_request POST, get_location fine: tierFor raises, never lowers
    t = dispatch(Action(tier="NOTIFY", per_call_tier="CONFIRM"), None, Store(policy="ALLOW_ALWAYS"))
    assert t.enforced_tier == "CONFIRM" and not t.executed
    # a tierFor that tries to LOWER is ignored
    t = dispatch(Action(tier="CONFIRM", per_call_tier="AUTO"), None, Store(policy="ALLOW_ALWAYS"))
    assert t.enforced_tier == "CONFIRM" and not t.executed
    # a tierFor that throws is treated as the most dangerous tier
    t = dispatch(
        Action(tier="AUTO", tier_for_throws=True), None, Store(policy="ALLOW_ALWAYS")
    )
    assert t.enforced_tier == "CONFIRM" and not t.executed


def test_denied_and_timed_out_execute_nothing():
    for action, req, pol, trust, verdict in _space():
        if verdict in ("APPROVED", "APPROVED_ALWAYS"):
            continue
        t = dispatch(action, req, Store(policy=pol), verdict, trust)
        if t.prompted:
            assert not t.executed, f"executed after verdict={verdict}"
            assert t.status == "denied"


def test_panic_or_never_during_the_prompt_revokes_the_approval():
    for flag in ("panic_during_prompt", "never_during_prompt", "disable_during_prompt"):
        for tier, verdict in product(TIERS, ("APPROVED", "APPROVED_ALWAYS")):
            store = Store(policy="ASK")
            t = dispatch(
                Action(tier=tier), "CONFIRM", store, verdict, **{flag: True}
            )
            assert t.prompted
            assert not t.executed, f"{flag} did not revoke the approval"
            assert "revoked-while-prompt-was-up" in t.events


def test_untrusted_content_never_executes_without_a_prompt():
    for action, req, pol, _trust, verdict in _space():
        t = dispatch(action, req, Store(policy=pol), verdict, "UNTRUSTED")
        if t.executed:
            assert t.prompted, (
                f"untrusted request ran with no human: tier={action.tier} policy={pol}"
            )
    # and consent given under untrusted framing never becomes a standing rule
    store = Store(policy="ASK")
    t = dispatch(Action(tier="NOTIFY"), None, store, "APPROVED_ALWAYS", "UNTRUSTED")
    assert t.executed and not t.remembered
    assert store.policy == "ASK"


def test_unsupported_actions_never_prompt_and_never_run():
    for unsupported, available in ((True, True), (False, False), (True, False)):
        t = dispatch(
            Action(tier="CONFIRM", unsupported=unsupported, available=available),
            "CONFIRM",
            Store(policy="ALLOW_ALWAYS"),
            "APPROVED",
        )
        assert not t.prompted and not t.executed
        assert t.status == "unsupported"


def test_an_unknown_action_is_refused_at_tier3_and_audited():
    t = dispatch(None, "AUTO", Store(policy="ALLOW_ALWAYS"), "APPROVED")
    assert not t.executed and not t.prompted
    assert t.status == "unsupported"
    assert t.enforced_tier == "CONFIRM"
    assert t.audited == 1


def test_every_path_writes_exactly_one_audit_line():
    seen = set()
    for action, req, pol, trust, verdict in _space():
        for needs, grant in ((False, True), (True, True), (True, False)):
            action.needs_permission = needs
            t = dispatch(action, req, Store(policy=pol), verdict, trust, grant_permission=grant)
            assert t.audited == 1, f"{t.events} wrote {t.audited} audit lines"
            seen.update(t.events)
        action.needs_permission = False
    for path in (
        "executed",
        "denied-by-standing-ban",
        "denied-by-human",
        "permission-still-missing",
        "prompted",
    ):
        assert path in seen, f"the state space never reached {path}"
    assert dispatch(None, None, Store()).audited == 1
    assert dispatch(Action(unsupported=True), None, Store()).audited == 1
    # The two paths the ordinary space cannot reach, because they need the
    # store to change mid-dispatch.
    revoked = dispatch(
        Action(tier="AUTO", resolve_needs_permission=True),
        None,
        Store(policy="ASK"),
        panic_during_resolve=True,
    )
    assert revoked.audited == 1 and "denied-by-policy" in revoked.events
    late = dispatch(Action(tier="NOTIFY"), None, Store(policy="ASK"), "APPROVED",
                    panic_during_prompt=True)
    assert late.audited == 1 and "revoked-while-prompt-was-up" in late.events


def test_tier1_still_runs_without_asking():
    t = dispatch(Action(tier="AUTO"), None, Store(policy="ASK"), "DENIED")
    assert t.executed and not t.prompted
    # ...unless the master switch or panic is set
    assert not dispatch(Action(tier="AUTO"), None, Store(panic=True)).executed
    assert not dispatch(Action(tier="AUTO"), None, Store(enabled=False)).executed


def test_tier2_remembers_only_after_a_human_said_always():
    store = Store(policy="ASK")
    t = dispatch(Action(tier="NOTIFY"), None, store, "APPROVED")
    assert t.executed and not t.remembered and store.policy == "ASK"

    store = Store(policy="ASK")
    t = dispatch(Action(tier="NOTIFY"), None, store, "APPROVED_ALWAYS")
    assert t.executed and t.remembered and store.policy == "ALLOW_ALWAYS"
    # ...and the next one runs straight through
    assert dispatch(Action(tier="NOTIFY"), None, store, "DENIED").executed


# --- the Android permission step --------------------------------------------


def test_a_refused_permission_does_not_stop_the_action():
    """The invariant that replaced its own opposite.

    This file used to assert that a still-missing permission stopped the
    dispatch. That was wrong, and it cost a working feature: `get_location`
    declares COARSE and FINE, serves a coarse request from COARSE alone, and
    was therefore refused forever by anyone who answered the system dialog with
    "Approximate" — with `PermissionBridge`'s don't-ask-again memo making it
    instant and silent from the second time on.

    Only the action knows what THIS call needs. It re-checks in `execute` and
    returns the honest `permission … not granted` when it really cannot
    proceed, which it did all along. The dispatcher asks, records, and gets out
    of the way.
    """
    for tier, pol in product(TIERS, POLICIES):
        t = dispatch(
            Action(tier=tier, needs_permission=True),
            None,
            Store(policy=pol),
            "APPROVED",
            grant_permission=False,
        )
        if "denied-by-standing-ban" in t.events:
            continue
        assert t.executed, (
            f"the dispatcher refused for the action: {tier} {pol}. Only execute() "
            "knows which of the declared permissions this call needed."
        )
        assert t.permission_missing, "the outstanding grant was not recorded"


def test_the_dispatcher_never_answers_missing_permission_itself():
    """Structural, because the model above cannot see a `return` that is not
    there. `ActionResult.missingPermission` belongs to the actions; a dispatcher
    that can produce one has taken the decision back."""
    src = _registry_code()
    assert "ActionResult.missingPermission" not in src, (
        "ActionRegistry is deciding a permission outcome again — that is the "
        "all-or-nothing gate that broke get_location"
    )


def test_no_dialog_is_raised_for_a_dispatch_the_standing_bans_deny():
    """Panic must beat a permission dialog as thoroughly as it beats an action.

    Without the pre-gate at step 3 a server could raise "Allow Jarvis to access
    your contacts?" on a phone whose owner had switched automation off — the
    resolver's grant is asked for before the tier is even known.
    """
    for panic, enabled, pol in product((True, False), (True, False), POLICIES):
        if not panic and enabled and pol != "NEVER":
            continue  # not a standing ban
        t = dispatch(
            Action(tier="CONFIRM", needs_permission=True, resolve_needs_permission=True),
            "CONFIRM",
            Store(policy=pol, enabled=enabled, panic=panic),
            "APPROVED",
        )
        assert t.dialogs == [], f"raised {t.dialogs} under panic={panic} enabled={enabled} {pol}"
        assert not t.prompted and not t.executed


def test_the_permission_dialog_comes_after_the_human():
    """Asking the OS first would put "may Jarvis send SMS?" in front of somebody
    who is about to say no to sending one, and would let a server that sends
    nonsense spam dialogs at the cost of one message each."""
    t = dispatch(
        Action(tier="CONFIRM", needs_permission=True),
        None,
        Store(policy="ASK"),
        "DENIED",
    )
    assert t.prompted and t.dialogs == [], "the OS was asked about a refused command"
    t = dispatch(
        Action(tier="CONFIRM", needs_permission=True),
        None,
        Store(policy="ASK"),
        "APPROVED",
    )
    assert t.prompted and t.dialogs == ["execute"] and t.executed


def test_the_resolvers_permission_is_asked_for_before_the_prompt():
    """The one grant that has to come first, and why: the consent prompt shows
    the resolved number, so the lookup that produces it runs ahead of the
    prompt — and a lookup with no contacts permission just refuses."""
    t = dispatch(
        Action(tier="CONFIRM", resolve_needs_permission=True, needs_permission=True),
        None,
        Store(policy="ASK"),
        "APPROVED",
    )
    assert t.dialogs == ["resolve", "execute"], t.dialogs


def test_panic_during_the_permission_dialog_revokes_the_approval():
    """The reason the re-check moved out of the ASK branch. A Tier-1 action
    needing a grant waits on a dialog with no consent prompt anywhere, and the
    old code only re-read the store after a prompt."""
    for tier in TIERS:
        store = Store(policy="ALLOW_ALWAYS")
        t = dispatch(
            Action(tier=tier, needs_permission=True),
            None,
            store,
            "APPROVED",
            panic_during_prompt=True,
        )
        assert not t.executed, f"{tier} executed after panic during the permission dialog"
        assert "revoked-while-prompt-was-up" in t.events


def test_an_action_needing_nothing_never_sees_a_dialog():
    for action, req, pol, trust, verdict in _space():
        t = dispatch(action, req, Store(policy=pol), verdict, trust)
        assert t.dialogs == [], "a dialog for an action that declared no permissions"


# --- drift check against the Kotlin ----------------------------------------


def _registry_code() -> str:
    """ActionRegistry.kt with its comments gone, flattened to one line.

    Comments stripped, and not for tidiness. Each step below is anchored with
    `str.index`, which finds the FIRST occurrence — so a comment quoting one of
    these needles moves the anchor, and the ordering assertion then holds
    between a comment and a line of code rather than between two lines of code.
    An audit demonstrated it: moving the standing-ban pre-gate to AFTER the
    resolver's permission ask, and leaving behind the sort of pointer comment a
    refactor leaves ("the standing bans used to be decided here, with
    if (PolicyEngine.decide(standing) == Decision.DENY)"), kept this file at
    23/23 while the shipped behaviour became "a server command can raise a
    contacts dialog on a phone whose owner has hit panic".

    This file's own docstring calls that ordering "the property that matters".
    """
    src = REGISTRY.read_text()
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    return re.sub(r"\s+", " ", src)


def test_the_kotlin_dispatcher_still_does_these_steps_in_this_order():
    assert REGISTRY.is_file(), f"missing {REGISTRY}"
    src = _registry_code()
    ordered = [
        # unsupported / unavailable short-circuit BEFORE any policy work
        "if (safeUnsupported(action))",
        "if (!safeAvailable(action))",
        # the standing bans — panic, the master switch, a standing NEVER —
        # before anything is resolved or any dialog is raised
        "if (PolicyEngine.decide(standing) == Decision.DENY)",
        # the resolver's own grant, which has to come before the resolver
        "val forResolve = safeResolvePermissionsFor(action, live)",
        # fuzzy parameters become concrete BEFORE a human is shown them, so the
        # prompt cannot say "Mum" while the message goes to a number nobody saw
        "when (val resolution = safeResolve(action, live))",
        # local table is the authority, tierFor folded in with max() — and it
        # reads the RESOLVED params, so a resolver can only ever raise a tier
        "val localTier = ActionTier.max(action.tier, safeTierFor(action, live))",
        "val effective = PolicyEngine.effectiveTier(localTier, requestedTier)",
        "val decision = PolicyEngine.decide(request)",
        # the human
        "val rememberable = PolicyEngine.canRemember(effective, trust)",
        "if (!verdict.allowsExecution)",
        "if (verdict == ApprovalVerdict.APPROVED_ALWAYS && rememberable)",
        # the Android permission, after the human and before the re-validation,
        # so a panic hit while the OS dialog is up still wins
        "val stillMissing = safeRequestPermissions(actionId, absent)",
        # re-validate after both waits
        "if (PolicyEngine.decide(fresh) == Decision.DENY)",
        # only then execute
        "withTimeout(action.timeoutMs) { action.execute(appContext, live) }",
    ]
    positions = []
    for needle in ordered:
        flat = re.sub(r"\s+", " ", needle)
        assert flat in src, f"ActionRegistry.kt no longer contains: {needle}"
        positions.append(src.index(flat))
    assert positions == sorted(positions), (
        "the steps in ActionRegistry.dispatch are no longer in the spec's order"
    )


def test_the_dispatcher_passes_the_verbatim_params_to_the_prompt():
    src = REGISTRY.read_text()  # the marker comment IS the assertion here
    assert "params = live, // VERBATIM" in src, (
        "the consent prompt must be shown the exact params that will execute"
    )


def test_resolution_happens_before_the_prompt_and_fails_closed():
    """`resolve` runs ahead of every gate, so a throw cannot mean "never mind".

    If a resolver could fail soft, the human would be shown the name it could
    not resolve and `execute` would then decide for itself what that name
    meant — which is the one thing the verbatim-params rule exists to stop.
    """
    src = _registry_code()
    assert "ResolveResult.Failed -> return finish(" in src, (
        "a failed resolution must return, not fall through to the prompt"
    )
    assert "Log.w(TAG, \"resolve threw for ${action.id}; refusing\", t)" in src, (
        "safeResolve no longer refuses when a resolver throws"
    )
    # ...and a cancellation is still a cancellation, not a refusal.
    resolve_body = src.split("private suspend fun safeResolve(", 1)
    assert len(resolve_body) == 2, "safeResolve is gone"
    assert "catch (t: CancellationException) { throw t }" in resolve_body[1][:600], (
        "safeResolve must let cancellation propagate; abandoning a turn is not "
        "the same as a resolver saying no"
    )


def test_a_failing_approval_gateway_fails_closed():
    src = _registry_code()
    assert "return verdict ?: ApprovalVerdict.TIMEOUT" in src
    assert "ApprovalVerdict.TIMEOUT" in src
    gateway = REGISTRY.parent / "ApprovalGateway.kt"
    text = re.sub(r"\s+", " ", gateway.read_text())
    # anything the UI says that we do not recognise is a denial
    assert "else -> DENIED" in text


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
    combos = len(TIERS) * (len(TIERS) + 1) * len(REQUESTED) * len(POLICIES) * len(TRUSTS) * len(VERDICTS)
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed ({combos} dispatches modelled)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
