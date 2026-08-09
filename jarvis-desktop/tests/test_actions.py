"""The dispatcher and the built-in action table.

The dispatcher tests are the ones that matter: they check the *order* of the
gate — local tier, then the server's raise, then the user's policy, then a
human, then execute, then re-check — because every one of those steps is only
worth anything if it happens before the action runs.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_desktop.actions.base import Action, ActionResult, Status
from jarvis_desktop.actions.builtins import TIER_TABLE, all_actions, build_registry
from jarvis_desktop.consent import ApprovalVerdict
from jarvis_desktop.policy import ActionTier, Decision, TrustLevel, UserPolicy

from .conftest import RecordingAction, ScriptedConsent


# --- the local table --------------------------------------------------------


def test_the_flat_tier_table_matches_the_actions():
    """Two copies of the table, so an action added at the wrong tier shows up as
    a failing test rather than as a surprise."""
    built = {action.id: action.tier for action in all_actions()}
    assert built == TIER_TABLE


def test_action_ids_are_unique():
    ids = [action.id for action in all_actions()]
    assert len(ids) == len(set(ids))


def test_every_action_has_a_description_and_a_schema():
    for action in all_actions():
        assert action.id
        assert action.description.strip()
        assert isinstance(action.params_schema, dict)
        assert action.capability
        assert action.timeout_s > 0


@pytest.mark.parametrize(
    "action_id",
    ["run_command", "delete_file", "type_text", "click", "move_mouse", "lock_screen", "sleep"],
)
def test_the_dangerous_actions_are_tier_three(action_id):
    assert TIER_TABLE[action_id] == ActionTier.CONFIRM


@pytest.mark.parametrize(
    "action_id", ["get_system_state", "read_file", "list_dir", "list_windows", "open_url", "notify"]
)
def test_the_read_only_actions_are_tier_one(action_id):
    assert TIER_TABLE[action_id] == ActionTier.AUTO


@pytest.mark.parametrize(
    "action_id",
    ["write_file", "read_clipboard", "write_clipboard", "http_request", "screenshot", "focus_window"],
)
def test_the_recoverable_actions_are_tier_two(action_id):
    assert TIER_TABLE[action_id] == ActionTier.NOTIFY


def test_the_desktop_tiers_agree_with_the_phone_where_the_actions_overlap():
    """The user should not have to learn Jarvis's manners twice."""
    shared_with_android = {
        "read_file": ActionTier.AUTO,
        "write_file": ActionTier.NOTIFY,
        "list_dir": ActionTier.AUTO,  # `list_files` on the phone
        "delete_file": ActionTier.CONFIRM,
        "read_clipboard": ActionTier.NOTIFY,
        "write_clipboard": ActionTier.NOTIFY,
        "http_request": ActionTier.NOTIFY,
        "open_url": ActionTier.AUTO,
        "launch_app": ActionTier.AUTO,
        "set_volume": ActionTier.AUTO,
        "run_command": ActionTier.CONFIRM,  # `run_shell` on the phone
    }
    for action_id, tier in shared_with_android.items():
        assert TIER_TABLE[action_id] == tier, action_id


def test_http_request_raises_itself_to_confirm_for_writes():
    from jarvis_desktop.actions.net import HttpRequest

    action = HttpRequest()
    assert action.tier_for({"method": "GET"}) == ActionTier.NOTIFY
    assert action.tier_for({}) == ActionTier.NOTIFY
    assert action.tier_for({"method": "HEAD"}) == ActionTier.NOTIFY
    for method in ("POST", "PUT", "PATCH", "DELETE", "post"):
        assert action.tier_for({"method": method}) == ActionTier.CONFIRM, method


def test_the_manifest_is_serialisable(config, policy, audit):
    import json

    registry = build_registry(config, policy, audit)
    entries = registry.manifest()
    json.dumps(entries)  # must not raise
    assert len(entries) == len(TIER_TABLE)
    for entry in entries:
        assert entry["tier"] in (1, 2, 3)
        assert entry["requires_confirmation"] == (entry["tier"] == 3)


def test_capabilities_only_list_what_works(config, policy, audit):
    registry = build_registry(config, policy, audit)
    caps = registry.capabilities()
    assert "system" in caps
    assert "files" in caps
    # Input automation is off in the test config, so it is not advertised.
    assert "ui_automation" not in caps


# --- dispatch ordering ------------------------------------------------------


async def test_a_tier_one_action_runs_without_a_prompt(make_registry):
    action = RecordingAction("peek", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("peek", {"value": 1}, ActionTier.AUTO, "why")

    assert outcome.decision == Decision.ALLOW
    assert outcome.result.status == Status.OK
    assert action.calls == [{"value": 1}]
    assert consent.seen == []


async def test_a_tier_two_action_asks_once_then_remembers(make_registry, policy):
    action = RecordingAction("tweak", ActionTier.NOTIFY)
    consent = ScriptedConsent(answers=[ApprovalVerdict.APPROVED_ALWAYS])
    registry = make_registry([action], consent=consent)

    first = await registry.dispatch("tweak", {}, ActionTier.NOTIFY, "why")
    assert first.result.ok
    assert len(consent.seen) == 1
    assert consent.seen[0].rememberable is True
    assert policy.policy_for("tweak") == UserPolicy.ALLOW_ALWAYS

    second = await registry.dispatch("tweak", {}, ActionTier.NOTIFY, "why")
    assert second.decision == Decision.ALLOW
    assert len(consent.seen) == 1, "it asked again after being told always"
    assert len(action.calls) == 2


async def test_a_tier_three_action_asks_every_time_and_never_remembers(make_registry, policy):
    action = RecordingAction("nuke", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED_ALWAYS)
    registry = make_registry([action], consent=consent)

    for _ in range(3):
        outcome = await registry.dispatch("nuke", {}, ActionTier.CONFIRM, "why")
        assert outcome.result.ok

    assert len(consent.seen) == 3, "a Tier-3 action stopped asking"
    assert all(seen.rememberable is False for seen in consent.seen)
    assert policy.policy_for("nuke") == UserPolicy.ASK, "a Tier-3 answer was remembered"


async def test_an_action_can_raise_its_own_tier_from_its_params(make_registry):
    class Escalating(RecordingAction):
        def tier_for(self, params):
            return ActionTier.CONFIRM if params.get("dangerous") else ActionTier.AUTO

    action = Escalating("maybe", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    safe = await registry.dispatch("maybe", {}, ActionTier.AUTO, "why")
    assert safe.result.ok
    assert consent.seen == []

    risky = await registry.dispatch("maybe", {"dangerous": True}, ActionTier.AUTO, "why")
    assert risky.result.status == Status.DENIED
    assert len(consent.seen) == 1
    assert consent.seen[0].tier == ActionTier.CONFIRM


async def test_an_action_cannot_lower_its_own_tier(make_registry):
    class Sneaky(RecordingAction):
        def tier_for(self, params):
            return ActionTier.AUTO  # ignored: max() only goes up

    action = Sneaky("sneaky", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("sneaky", {}, None, "why")
    assert action.calls == []
    assert outcome.tier == ActionTier.CONFIRM


async def test_a_tier_for_that_raises_is_treated_as_confirm(make_registry):
    class Broken(RecordingAction):
        def tier_for(self, params):
            raise RuntimeError("kaboom")

    action = Broken("broken", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("broken", {}, None, "why")
    assert action.calls == []
    assert outcome.tier == ActionTier.CONFIRM


async def test_the_store_is_re_read_after_the_prompt_returns(make_registry, policy):
    """Panic hit while the prompt was on screen must still stop the action."""
    action = RecordingAction("slow_confirm", ActionTier.CONFIRM)

    class PanicMidPrompt(ScriptedConsent):
        async def request(self, request):
            self.seen.append(request)
            policy.panic = True  # the user slams the kill switch
            return ApprovalVerdict.APPROVED

    consent = PanicMidPrompt()
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("slow_confirm", {}, ActionTier.CONFIRM, "why")

    assert action.calls == [], "an approval outlived the kill switch"
    assert outcome.result.status == Status.DENIED
    assert "revoked while the prompt was up" in outcome.note


async def test_untrusted_requests_are_never_auto_allowed(make_registry):
    action = RecordingAction("peek", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch(
        "peek", {}, ActionTier.AUTO, "a web page suggested this", trust=TrustLevel.UNTRUSTED
    )

    # A Tier-1 action that would normally run silently put a prompt on screen
    # instead, because the request came from content a stranger wrote.
    assert len(consent.seen) == 1
    assert consent.seen[0].rememberable is False
    assert action.calls == []
    assert outcome.result.status == Status.DENIED


async def test_an_untrusted_approval_is_never_remembered(make_registry, policy):
    action = RecordingAction("tweak", ActionTier.NOTIFY)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED_ALWAYS)
    registry = make_registry([action], consent=consent)

    await registry.dispatch(
        "tweak", {}, ActionTier.NOTIFY, "why", trust=TrustLevel.UNTRUSTED
    )
    assert policy.policy_for("tweak") == UserPolicy.ASK


async def test_an_unavailable_action_says_so_without_prompting(make_registry):
    class Missing(RecordingAction):
        def available(self, ctx):
            return False

        def unavailable_reason(self, ctx):
            return "install the thing first"

    action = Missing("missing", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("missing", {}, None, "why")
    assert outcome.result.status == Status.UNSUPPORTED
    assert "install the thing first" in (outcome.result.error or "")
    assert consent.seen == []


async def test_a_slow_action_is_timed_out(make_registry):
    class Slow(RecordingAction):
        def run(self, ctx, params):
            import time

            self.calls.append(params)
            time.sleep(5)
            return ActionResult.success()

    action = Slow("slow", ActionTier.AUTO)
    action.timeout_s = 0.1
    registry = make_registry([action])

    outcome = await registry.dispatch("slow", {}, None, "why")
    assert outcome.result.status == Status.ERROR
    assert "timed out" in (outcome.result.error or "")


async def test_an_async_action_is_awaited_directly(make_registry):
    class Async(Action):
        id = "async_thing"
        tier = ActionTier.AUTO
        description = "an async action"
        params_schema: dict = {}
        capability = "test"
        timeout_s = 5.0

        def __init__(self):
            self.calls = []

        async def run(self, ctx, params):
            await asyncio.sleep(0)
            self.calls.append(params)
            return ActionResult.success(async_ran=True)

    action = Async()
    registry = make_registry([action])
    outcome = await registry.dispatch("async_thing", {}, None, "why")
    assert outcome.result.ok
    assert outcome.result.data["async_ran"] is True


async def test_an_action_returning_the_wrong_type_is_an_error(make_registry):
    class Wrong(RecordingAction):
        def run(self, ctx, params):
            return "not an ActionResult"

    registry = make_registry([Wrong("wrong", ActionTier.AUTO)])
    outcome = await registry.dispatch("wrong", {}, None, "why")
    assert outcome.result.status == Status.ERROR
    assert "not an ActionResult" in (outcome.result.error or "")


# --- everything is audited --------------------------------------------------


async def test_every_outcome_lands_in_the_audit_log(make_registry, audit, policy):
    allowed = RecordingAction("peek", ActionTier.AUTO)
    blocked = RecordingAction("blocked", ActionTier.AUTO)
    confirmed = RecordingAction("nuke", ActionTier.CONFIRM)
    policy.set_policy("blocked", UserPolicy.NEVER)
    registry = make_registry(
        [allowed, blocked, confirmed], consent=ScriptedConsent(default=ApprovalVerdict.DENIED)
    )

    await registry.dispatch("peek", {}, None, "why", command_id="c-1")
    await registry.dispatch("blocked", {}, None, "why", command_id="c-2")
    await registry.dispatch("nuke", {}, None, "why", command_id="c-3")
    await registry.dispatch("ghost", {}, None, "why", command_id="c-4")

    entries = {e.action_id: e for e in audit.read()}
    assert set(entries) == {"peek", "blocked", "nuke", "ghost"}
    assert entries["peek"].status == "ok"
    assert entries["blocked"].status == "denied"
    assert entries["nuke"].status == "denied"
    assert entries["ghost"].status == "unsupported"
    assert entries["ghost"].tier == ActionTier.CONFIRM
    assert entries["nuke"].command_id == "c-3"
    assert all(e.note for e in entries.values())


async def test_the_audit_log_records_the_reason_the_tier_was_raised(make_registry, audit):
    action = RecordingAction("peek", ActionTier.AUTO)
    registry = make_registry([action], consent=ScriptedConsent(default=ApprovalVerdict.DENIED))
    await registry.dispatch("peek", {}, ActionTier.CONFIRM, "the server insisted")
    (entry,) = audit.read()
    assert "raised by server" in (entry.note or "")
    assert entry.tier == ActionTier.CONFIRM


async def test_the_audit_log_redacts_but_the_prompt_did_not(make_registry, audit):
    action = RecordingAction("nuke", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    await registry.dispatch("nuke", {"token": "sk-live-1", "path": "x"}, None, "why")

    assert consent.seen[0].params["token"] == "sk-live-1"  # the human sees the truth
    (entry,) = audit.read()
    assert entry.params["token"] == "[redacted]"  # the file does not
    assert entry.params["path"] == "x"


# --- registration errors ----------------------------------------------------


def test_duplicate_ids_are_refused(make_registry):
    with pytest.raises(ValueError, match="duplicate"):
        make_registry([RecordingAction("x", ActionTier.AUTO), RecordingAction("x", ActionTier.AUTO)])


def test_an_action_without_an_id_is_refused(make_registry):
    with pytest.raises(ValueError):
        make_registry([RecordingAction("", ActionTier.AUTO)])


def test_the_default_consent_gateway_denies(ctx, policy, audit):
    """A registry built without a gateway must not fall open."""
    from jarvis_desktop.actions.registry import ActionRegistry
    from jarvis_desktop.consent import DenyAllGateway

    registry = ActionRegistry(ctx, policy, audit)
    assert isinstance(registry.consent, DenyAllGateway)


# --- an action must not outlive the dispatcher's cap ------------------------
#
# Found by adversarial review. `asyncio.wait_for` around `asyncio.to_thread`
# cancels the *wrapper*; the worker thread runs on regardless. So an action
# whose own deadline sits above `timeout_s` gets reported to the server as a
# timeout while it is still doing the thing — and the command slot has already
# been freed for a second copy of it.


def test_every_action_deadline_sits_below_the_dispatchers_cap():
    """The rule `shell.py` states and three other actions used to break: the
    subprocess deadline must be under the action's own timeout."""
    from jarvis_desktop.actions.apps import ListWindows
    from jarvis_desktop.actions.system import Notify, SetVolume

    # (action, the longest deadline it hands to a subprocess)
    for action, longest_child_deadline in (
        (SetVolume(), 20.0),  # the Windows mixer nudge
        (Notify(), 15.0),  # the PowerShell toast
        (ListWindows(), 20.0),  # the PowerShell window list
    ):
        assert action.timeout_s > longest_child_deadline, action.id


def test_type_text_finishes_inside_its_budget(ctx, monkeypatch):
    """4000 characters at the maximum interval would be over an hour of typing
    under a 120s cap: the keystrokes would carry on long after the server had
    been told the action timed out."""
    from jarvis_desktop.actions import inputauto

    typed: dict[str, float] = {}

    class FakeGui:
        FAILSAFE = False
        PAUSE = 0.0

        def write(self, text, interval):
            typed["chars"] = len(text)
            typed["interval"] = interval

        def press(self, key):
            pass

    import dataclasses

    from jarvis_desktop.config import InputConfig

    monkeypatch.setattr(inputauto, "_pyautogui", lambda: FakeGui())
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=True))

    action = inputauto.TypeText()
    result = action.run(ctx, {"text": "x" * 4000, "interval_s": 1.0})

    assert result.ok
    assert typed["chars"] * typed["interval"] < action.timeout_s
