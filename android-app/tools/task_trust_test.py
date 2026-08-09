#!/usr/bin/env python3
"""Executable spec for the TAINT model inside a Jarvis task run.

Mirrors `app/src/main/kotlin/ai/jarvis/app/automation/tasks/TaskRunner.kt`.

`task_vars_test.py` pins down what `{{var}}` expansion does with a string.
`policy_truth_table_test.py` pins down what the policy engine decides given a
trust level. Neither of them answers the question this file exists for:

    when does a step's dispatch actually GET the untrusted trust level?

That is the join, it lives in `TaskRunner`, and it is where the whole
"untrusted content can never cause an action on its own" property is either
true or quietly false. There are four channels by which somebody else's text
reaches a task, and a miss on any one of them is a laundering path:

  1. the TRIGGER — a notification body, a foreground-app change;
  2. the trigger PAYLOAD of an otherwise trusted trigger — jarvis-core firing
     `manual` with a data map its language model composed;
  3. an `ask_jarvis` reply — model output, and the model reads the web;
  4. an ACTION RESULT — `http_request`, `read_calendar`, `read_clipboard`,
     `read_file`, `read_screen`, `read_contacts`, `run_shell`. This is the one
     that was missing: the action layer publishes
     `ActionRegistry.producesUntrustedOutput(id)` for exactly this purpose, and
     a runner that ignores it lets a task park a web page in a variable and
     interpolate it into a Tier-1 action's parameters with no prompt at all.

And one channel that is not text at all:

  5. CONTROL FLOW — `if reply contains "yes" then <action>`. The action's own
     parameters can be constants, so interpolation taint alone sees nothing,
     while the injected text still chose what happened.

Run:  python3 android-app/tools/task_trust_test.py
  or: python3 -m pytest android-app/tools/task_trust_test.py -q
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/src/main/kotlin/ai/jarvis/app"
RUNNER = SRC / "automation/tasks/TaskRunner.kt"
CONDITIONS = SRC / "automation/tasks/Conditions.kt"
SAFETY = SRC / "automation/tasks/TaskSafety.kt"
STORE = SRC / "automation/tasks/TaskStore.kt"
ENGINE = SRC / "automation/tasks/TaskEngine.kt"
RECEIVER = SRC / "automation/triggers/SystemEventReceiver.kt"
MANUAL = SRC / "automation/triggers/ManualTrigger.kt"
TRIGGER_MANAGER = SRC / "automation/triggers/TriggerManager.kt"

TRUSTED, UNTRUSTED = "TRUSTED", "UNTRUSTED"
AUTO, NOTIFY, CONFIRM = "AUTO", "NOTIFY", "CONFIRM"
TIERS = [AUTO, NOTIFY, CONFIRM]
ALLOW, ASK, DENY = "ALLOW", "ASK", "DENY"


# --- the local action table, as the builtins declare it ----------------------


@dataclass(frozen=True)
class Action:
    tier: str
    untrusted_output: bool = False


ACTIONS = {
    "send_notification": Action(AUTO),
    "read_calendar": Action(AUTO, untrusted_output=True),
    "http_request": Action(NOTIFY, untrusted_output=True),
    "read_contacts": Action(NOTIFY, untrusted_output=True),
    "set_alarm": Action(NOTIFY),
    "send_sms": Action(CONFIRM),
}


def tier_of(action_id: str) -> str | None:
    a = ACTIONS.get(action_id)
    return a.tier if a else None


def produces_untrusted_output(action_id: str) -> bool:
    """`ActionRegistry.producesUntrustedOutput`. An id we do not know answers
    true: if we cannot say what an action returns, assume the worst."""
    a = ACTIONS.get(action_id)
    return a.untrusted_output if a else True


# --- PolicyEngine, only as much of it as this file needs ---------------------


def max_tier(a: str, b: str) -> str:
    return max(a, b, key=TIERS.index)


def decide(local: str, declared: str | None, user_policy: str, trust: str) -> str:
    if user_policy == "NEVER":
        return DENY
    effective = max_tier(local, declared or AUTO)
    if effective == CONFIRM:
        base = ASK
    elif effective == NOTIFY:
        base = ALLOW if user_policy == "ALLOW_ALWAYS" else ASK
    else:
        base = ALLOW
    # The rule this whole file is about.
    if trust == UNTRUSTED and base == ALLOW:
        return ASK
    return base


# --- the step grammar --------------------------------------------------------


@dataclass(frozen=True)
class ActionStep:
    action: str
    refs: frozenset[str] = frozenset()
    store_as: str | None = None
    declared_tier: str | None = None


@dataclass(frozen=True)
class AskJarvisStep:
    store_as: str


@dataclass(frozen=True)
class SetVariableStep:
    name: str
    refs: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IfStep:
    """`condition_refs` is `ConditionEvaluator.variableRoots` of the condition."""

    condition_refs: frozenset[str]
    then: tuple = ()
    otherwise: tuple = ()
    taken: bool = True


@dataclass(frozen=True)
class WaitForEventStep:
    store_as: str
    event_untrusted: bool


# --- the model of TaskRunner -------------------------------------------------


@dataclass
class Dispatch:
    action: str
    trust: str
    decision: str
    executed: bool


@dataclass
class Run:
    run_trust: str = TRUSTED
    tainted: set[str] = field(default_factory=set)
    dispatches: list[Dispatch] = field(default_factory=list)

    def set_variable(self, name: str | None, tainted: bool) -> None:
        if not name:
            return
        root = name.split(".", 1)[0]
        if tainted:
            self.tainted.add(root)
        else:
            self.tainted.discard(root)

    def note_condition_taint(self, refs) -> None:
        """`RunState.noteConditionTaint`. A condition that reads a tainted
        variable degrades the REST of the run: by the time the branch is picked
        the decision is already made, and everything after it is downstream."""
        if self.run_trust == UNTRUSTED or not self.tainted:
            return
        if any(r in self.tainted for r in refs):
            self.run_trust = UNTRUSTED


def start_run(trigger_untrusted: bool, payload_tainted: bool, payload_keys=()) -> Run:
    """`TaskRunner.run` up to the first step.

    Two separate flags. `untrusted` degrades the whole run; `dataTainted` only
    taints the payload's variables, which is the honest answer for a `manual`
    trigger fired by the server with a data map."""
    run = Run(run_trust=UNTRUSTED if trigger_untrusted else TRUSTED)
    if payload_tainted:
        run.tainted.add("trigger")
        run.tainted.update(payload_keys)
    return run


def run_steps(run: Run, steps, user_policy: str = "ALLOW_ALWAYS") -> Run:
    for step in steps:
        if isinstance(step, ActionStep):
            touched = any(r in run.tainted for r in step.refs)
            trust = UNTRUSTED if (run.run_trust == UNTRUSTED or touched) else TRUSTED
            local = tier_of(step.action) or CONFIRM
            decision = decide(local, step.declared_tier, user_policy, trust)
            executed = decision == ALLOW
            run.dispatches.append(Dispatch(step.action, trust, decision, executed))
            run.set_variable(
                step.store_as,
                trust == UNTRUSTED or produces_untrusted_output(step.action),
            )
        elif isinstance(step, AskJarvisStep):
            run.set_variable(step.store_as, True)  # always
        elif isinstance(step, SetVariableStep):
            run.set_variable(step.name, any(r in run.tainted for r in step.refs))
        elif isinstance(step, WaitForEventStep):
            run.set_variable(step.store_as, step.event_untrusted)
            if step.event_untrusted:
                run.run_trust = UNTRUSTED
        elif isinstance(step, IfStep):
            run.note_condition_taint(step.condition_refs)
            run_steps(run, step.then if step.taken else step.otherwise, user_policy)
        else:  # pragma: no cover - the grammar is closed
            raise AssertionError(f"unknown step {step!r}")
    return run


# --- channel 4: an action result is untrusted content ------------------------


def test_a_web_fetch_taints_the_variable_it_is_stored_in():
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("http_request", store_as="page"),
            ActionStep("send_notification", refs=frozenset({"page"})),
        ],
    )
    assert run.dispatches[1].trust == UNTRUSTED
    assert run.dispatches[1].decision == ASK
    assert not run.dispatches[1].executed


def test_calendar_text_cannot_be_laundered_into_an_auto_action():
    """The `docs/automations.md` claim, made executable: calendar text is
    written by whoever sent the invitation."""
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("read_calendar", store_as="cal"),
            ActionStep("send_notification", refs=frozenset({"cal"})),
        ],
    )
    assert run.dispatches[0].executed, "reading the calendar is Tier 1 and runs"
    assert run.dispatches[1].trust == UNTRUSTED
    assert not run.dispatches[1].executed


def test_an_action_that_returns_nothing_third_party_does_not_taint():
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("set_alarm", store_as="alarm"),
            ActionStep("send_notification", refs=frozenset({"alarm"})),
        ],
    )
    assert run.dispatches[1].trust == TRUSTED
    assert run.dispatches[1].executed


def test_an_unknown_action_id_is_assumed_to_return_untrusted_content():
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("some_future_reader", store_as="x"),
            ActionStep("send_notification", refs=frozenset({"x"})),
        ],
    )
    assert run.dispatches[1].trust == UNTRUSTED


def test_taint_from_a_result_is_contagious_through_set_variable():
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("http_request", store_as="page"),
            SetVariableStep("summary", refs=frozenset({"page"})),
            SetVariableStep("body", refs=frozenset({"summary"})),
            ActionStep("send_notification", refs=frozenset({"body"})),
        ],
    )
    assert run.dispatches[1].trust == UNTRUSTED


def test_overwriting_a_tainted_variable_with_a_constant_clears_it():
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("http_request", store_as="page"),
            SetVariableStep("page", refs=frozenset()),
            ActionStep("send_notification", refs=frozenset({"page"})),
        ],
    )
    assert run.dispatches[1].trust == TRUSTED, "a constant is a constant"


# --- channel 5: control flow -------------------------------------------------


def test_a_branch_on_tainted_text_degrades_the_rest_of_the_run():
    """The action's parameters are entirely constant. Interpolation taint sees
    nothing; the injected reply still chose what happened."""
    run = run_steps(
        start_run(False, False),
        [
            AskJarvisStep(store_as="reply"),
            IfStep(
                condition_refs=frozenset({"reply"}),
                then=(ActionStep("send_notification"),),
            ),
        ],
    )
    assert run.run_trust == UNTRUSTED
    assert run.dispatches[0].trust == UNTRUSTED
    assert not run.dispatches[0].executed


def test_the_degrade_outlives_the_branch():
    run = run_steps(
        start_run(False, False),
        [
            AskJarvisStep(store_as="reply"),
            IfStep(condition_refs=frozenset({"reply"}), then=()),
            ActionStep("send_notification"),
        ],
    )
    assert not run.dispatches[0].executed


def test_a_branch_on_untainted_state_does_not_degrade():
    run = run_steps(
        start_run(False, False),
        [
            IfStep(
                condition_refs=frozenset({"battery"}),
                then=(ActionStep("send_notification"),),
            )
        ],
    )
    assert run.run_trust == TRUSTED
    assert run.dispatches[0].executed


# --- channel 2: a server-supplied manual payload -----------------------------


def test_a_server_supplied_manual_payload_taints_its_variables():
    run = run_steps(
        start_run(False, True, payload_keys={"topic"}),
        [ActionStep("send_notification", refs=frozenset({"topic"}))],
    )
    assert run.dispatches[0].trust == UNTRUSTED


def test_a_manual_payload_does_not_degrade_a_step_that_ignores_it():
    run = run_steps(
        start_run(False, True, payload_keys={"topic"}),
        [ActionStep("send_notification")],
    )
    assert run.dispatches[0].trust == TRUSTED, (
        "tainting the payload's variables is not the same as poisoning the run; "
        "a step that never mentions them is unaffected"
    )


def test_a_local_tap_with_no_payload_is_trusted():
    run = run_steps(start_run(False, False), [ActionStep("send_notification")])
    assert run.dispatches[0].executed


# --- channels 1 and 3, and the invariants over the whole space ---------------


def test_an_untrusted_trigger_poisons_every_step():
    run = run_steps(
        start_run(True, True, payload_keys={"text"}),
        [ActionStep("send_notification"), ActionStep("set_alarm")],
    )
    assert all(d.trust == UNTRUSTED and not d.executed for d in run.dispatches)


def test_an_untrusted_event_mid_run_degrades_what_follows():
    run = run_steps(
        start_run(False, False),
        [
            ActionStep("send_notification"),
            WaitForEventStep(store_as="e", event_untrusted=True),
            ActionStep("send_notification"),
        ],
    )
    assert run.dispatches[0].executed
    assert not run.dispatches[1].executed


def _every_shape():
    """A small but complete cross product of the ways untrusted text enters and
    the ways it can reach a dispatch."""
    sources = {
        "trigger": ([], True, False),
        "payload": ([], False, True),
        "ask": ([AskJarvisStep(store_as="u")], False, False),
        "http": ([ActionStep("http_request", store_as="u")], False, False),
        "calendar": ([ActionStep("read_calendar", store_as="u")], False, False),
        "contacts": ([ActionStep("read_contacts", store_as="u")], False, False),
    }
    for name, (prefix, trig, payload) in sources.items():
        for action in ACTIONS:
            for policy in ("ASK", "ALLOW_ALWAYS"):
                for via in ("params", "branch", "indirect"):
                    yield name, prefix, trig, payload, action, policy, via


def test_no_untrusted_content_ever_reaches_an_auto_allowed_action():
    """The property, over the whole reachable space."""
    checked = 0
    for name, prefix, trig, payload, action, policy, via in _every_shape():
        root = "trigger" if name in ("trigger", "payload") else "u"
        if via == "params":
            tail = [ActionStep(action, refs=frozenset({root}))]
        elif via == "branch":
            tail = [
                IfStep(condition_refs=frozenset({root}), then=(ActionStep(action),))
            ]
        else:
            tail = [
                SetVariableStep("copy", refs=frozenset({root})),
                ActionStep(action, refs=frozenset({"copy"})),
            ]
        run = run_steps(
            start_run(trig, payload, payload_keys={"trigger"}),
            prefix + tail,
            user_policy=policy,
        )
        final = run.dispatches[-1]
        assert final.trust == UNTRUSTED, f"{name}/{via}/{action}: dispatched TRUSTED"
        assert not final.executed, f"{name}/{via}/{action}: auto-allowed"
        checked += 1
    assert checked > 100, f"the sweep only covered {checked} shapes"


def test_a_confirm_action_never_auto_executes_from_any_shape():
    for _, prefix, trig, payload, _, policy, _ in _every_shape():
        run = run_steps(
            start_run(trig, payload, payload_keys={"trigger"}),
            prefix + [ActionStep("send_sms")],
            user_policy=policy,
        )
        assert not run.dispatches[-1].executed


def test_a_declared_tier_can_only_raise():
    """`send_notification` is Tier 1 locally. A step declaring a higher tier is
    honoured through max(); one declaring AUTO changes nothing."""
    for declared in [None] + TIERS:
        run = run_steps(
            start_run(False, False),
            [ActionStep("send_notification", declared_tier=declared)],
            user_policy="ASK",
        )
        assert run.dispatches[0].executed is (declared in (None, AUTO)), declared

    # And a step cannot make send_sms cheaper by declaring AUTO.
    for declared in [None] + TIERS:
        run = run_steps(
            start_run(False, False),
            [ActionStep("send_sms", declared_tier=declared)],
            user_policy="ALLOW_ALWAYS",
        )
        assert not run.dispatches[0].executed, declared


# --- ConditionEvaluator.variableRoots ---------------------------------------


def variable_roots(spec: dict, depth: int = 0) -> set[str]:
    """Mirror of `ConditionEvaluator.variableRoots`."""
    out: set[str] = set()
    if depth > 8:
        return out
    if spec.get("type", "").strip().lower() == "variable":
        name = spec.get("name") or spec.get("variable")
        if name:
            root = str(name).strip().split(".", 1)[0]
            if root:
                out.add(root)
    for child in spec.get("conditions", []):
        out |= variable_roots(child, depth + 1)
    return out


def test_variable_roots_finds_a_leaf():
    assert variable_roots({"type": "variable", "name": "reply"}) == {"reply"}


def test_variable_roots_strips_the_path():
    assert variable_roots({"type": "variable", "name": "reply.body.0"}) == {"reply"}


def test_variable_roots_walks_into_combinators():
    spec = {
        "type": "all",
        "conditions": [
            {"type": "battery_above", "level": 30},
            {
                "type": "any",
                "conditions": [
                    {"type": "variable", "variable": "page.title"},
                    {"type": "screen_on"},
                ],
            },
        ],
    }
    assert variable_roots(spec) == {"page"}


def test_variable_roots_ignores_conditions_that_read_device_state():
    assert variable_roots({"type": "time_window", "start": "22:00"}) == set()


# --- TriggerMatch: a notification task must name its packages ----------------


def _as_string_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def notification_spec_matches(spec_params: dict, event_package: str) -> bool:
    """The `notification_posted` half of `TriggerMatch.matches`."""
    named = _as_string_list(spec_params.get("packages", spec_params.get("package")))
    if not named:
        return False
    if "*" in named:
        return True
    return any(n.lower() == event_package.lower() for n in named)


def test_a_notification_task_that_names_no_package_matches_nothing():
    """Otherwise it would silently receive every notification some OTHER task's
    package let through the listener's allow-list — a bank alert read by a task
    that never asked for it."""
    assert not notification_spec_matches({}, "com.bank")
    assert not notification_spec_matches({"contains": {"text": "delivered"}}, "com.bank")


def test_an_empty_package_list_names_nobody():
    assert not notification_spec_matches({"packages": []}, "com.bank")
    assert not notification_spec_matches({"packages": [""]}, "com.bank")


def test_a_named_package_matches_case_insensitively():
    assert notification_spec_matches({"packages": ["com.Bank"]}, "com.bank")
    assert not notification_spec_matches({"packages": ["com.other"]}, "com.bank")


def test_the_star_firehose_is_still_honoured():
    assert notification_spec_matches({"packages": ["*"]}, "com.anything")


def test_stopping_the_triggers_empties_the_notification_allow_list():
    """The listener service is bound by the system and outlives our foreground
    service, so it keeps reading messages unless the allow-list is cleared."""
    src = _read(SRC / "automation/triggers/TriggerManager.kt")
    stop = src[src.index("    fun stop() {") :]
    stop = stop[: stop.index("\n    private fun emit(")]
    assert "NotificationBus.updateAllowedPackages(emptySet())" in stop

    # …and the rebuild must refill it AFTER start(), which calls stop().
    service = _read(SRC / "automation/JarvisAutomationService.kt")
    rebuild = service[service.index("private suspend fun rebuild(") :]
    rebuild = rebuild[: rebuild.index("private fun teardownTriggers()")]
    assert rebuild.index("triggers.start(tasks)") < rebuild.index("engine.onTasksChanged()"), (
        "onTasksChanged fills the notification allow-list and triggers.start "
        "empties it, so the order is load-bearing"
    )


def test_the_matcher_enforces_it_in_kotlin():
    src = _read(SRC / "automation/triggers/TriggerEvent.kt")
    assert "TriggerIds.NOTIFICATION_POSTED" in src
    assert re.search(r"if \(named\.isEmpty\(\)\) return false", src), (
        "notification_posted must fail closed when a task names no package"
    )
    assert "import android." not in src
    assert "import org.json" not in src


# --- TaskSafety.effectiveEnabled --------------------------------------------


def effective_enabled(enabled, enabled_by_user, may_auto_enable, authored_locally):
    if authored_locally:
        return enabled
    if may_auto_enable:
        return enabled
    return enabled_by_user and enabled


def test_a_pushed_task_with_a_confirm_step_arrives_disabled():
    assert not effective_enabled(True, False, False, False)


def test_an_imported_file_cannot_claim_to_be_locally_authored():
    """A bundle is a document, and a document can say `"source": "LOCAL"`.
    `TaskStore.import` passes `authoredLocally = false` so that claim buys
    nothing."""
    assert not effective_enabled(True, False, False, authored_locally=False)


def test_the_editor_is_still_the_editor():
    assert effective_enabled(True, False, False, authored_locally=True)


def test_a_human_enablement_survives():
    assert effective_enabled(True, True, False, False)


# --- structural checks: the Kotlin still says all of this -------------------


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing Kotlin source: {path}")
    return path.read_text()


def test_the_runner_taints_untrusted_action_results():
    src = _read(RUNNER)
    assert "producesUntrustedOutput" in src, (
        "TaskRunner no longer consults ActionRegistry.producesUntrustedOutput, so "
        "a web/calendar/clipboard read stored in a variable would launder into a "
        "TRUSTED dispatch"
    )
    assert re.search(
        r"trust == TrustLevel\.UNTRUSTED\s*\|\|\s*\n?\s*registry\.producesUntrustedOutput",
        src,
    ), "the store_as taint is no longer the OR of the two reasons"


def test_the_runner_degrades_on_a_tainted_condition():
    src = _read(RUNNER)
    assert "fun noteConditionTaint" in src
    # Called from the per-step guard, the if, and the repeat-while.
    assert src.count("noteConditionTaint(") >= 4, (
        "noteConditionTaint must be called from executeOne's guard, runIf and "
        "runRepeat as well as being declared"
    )


def test_the_runner_audits_a_cancelled_run():
    src = _read(RUNNER)
    assert re.search(
        r"withContext\(NonCancellable\)\s*\{\s*\n\s*recordRun\(", src
    ), (
        "a cancelled run must still flush its audit lines; AuditLog.record "
        "suspends and would throw immediately in a cancelled coroutine"
    )


def test_the_runner_never_substitutes_the_action_id():
    src = _read(RUNNER)
    assert "val actionId = step.action?.trim()" in src, (
        "the action id must come straight from the task JSON — a variable may "
        "fill a parameter, never choose the action"
    )


def test_conditions_stayed_pure():
    src = _read(CONDITIONS)
    assert "fun variableRoots(" in src
    assert "import android." not in src
    assert "import org.json" not in src


def test_task_safety_stayed_pure_and_gained_the_flag():
    src = _read(SAFETY)
    assert "authoredLocally" in src
    assert "import android." not in src
    assert "import org.json" not in src


def test_the_store_screens_imports_and_keeps_local_tasks():
    src = _read(STORE)
    assert "authoredLocally = false" in src, (
        "TaskStore.import must not let a bundle's own source field skip screening"
    )
    assert re.search(
        r"fromServer && it\.source != TaskSource\.SERVER", src
    ), "a server sync must not delete the user's own tasks"


def test_run_now_enforces_the_tasks_conditions():
    src = _read(ENGINE)
    body = src[src.index("suspend fun runNow(") :]
    body = body[: body.index("\n    /** Cancel a run in flight")]
    assert "evaluateAll(task.conditions" in body, (
        "runNow must apply the task's own conditions; they are part of what the "
        "user approved, and the server can ask for a run"
    )
    assert "dataTrusted" in body


def test_the_engine_serialises_starts_per_task():
    src = _read(ENGINE)
    assert "startLocks" in src, (
        "two concurrent trigger events must not both start a SINGLE-mode task"
    )
    assert "startLocks.computeIfAbsent" in src, (
        "getOrPut is a non-atomic read-then-write on a ConcurrentHashMap; two "
        "racers would each get their own Mutex and the lock would guard nothing"
    )
    assert "getOrPut(task.id) { Mutex() }" not in src


def test_the_broadcast_door_is_an_allow_list():
    src = _read(RECEIVER)
    assert "ACCEPTED_BROADCASTS" in src
    assert re.search(r"action !in SystemEventBus\.ACCEPTED_BROADCASTS", src), (
        "the manifest copy of this receiver is exported, so any app can address "
        "it explicitly with an action of its choosing"
    )
    accepted = src[src.index("val ACCEPTED_BROADCASTS") :]
    accepted = accepted[: accepted.index("}")]
    assert "SYNTHETIC_BOOT" not in accepted, (
        "the synthetic boot action is app-private and unprotected; accepting it "
        "off a broadcast lets any installed app forge a boot_completed trigger"
    )


def test_the_manual_trigger_defaults_to_an_untrusted_payload():
    src = _read(MANUAL)
    assert re.search(r"dataTrusted: Boolean = false", src), (
        "ManualTriggers.fire is reachable from the server; its data map is model "
        "output unless a local tap says otherwise"
    )
    assert 'if (!dataTrusted) payload.put("untrusted", true)' in src


def test_a_payload_can_lower_its_trust_but_never_raise_it():
    src = _read(TRIGGER_MANAGER)
    assert "trust = trigger.trust," in src, "trust still comes from the trigger"
    assert re.search(
        r"dataTainted = trigger\.trust == TrustLevel\.UNTRUSTED \|\|\s*\n\s*data\[\"untrusted\"\] == true",
        src,
    ), (
        "the payload flag must be OR-ed with the trigger's own classification, so "
        "it can only ever add taint"
    )


# --- runner ------------------------------------------------------------------


def main() -> int:
    failures: list[str] = []
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - a raising test is a failing test
            failures.append(f"{t.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL  task_trust_test: {len(failures)} problem(s) in {len(tests)} tests")
        for f in failures:
            print("  -", f)
        return 1
    print(f"ok    task_trust_test: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
