"""Plan → act → verify.

The conversation agent was a bounded act-and-observe loop: the model called
tools, saw results, and answered. Right for "turn the kitchen light on"; thin
for "tidy the garage lights, then tell me which ones never turn on", where a
plan exists in the model's head, is never written down, and nothing checks
whether each part actually happened.

Two things are pinned hardest here, because both are the difference between a
verification step and decoration:

* the verifier is a SEPARATE call given only the step and its outcome — a model
  asked whether its own argument succeeded says yes;
* an unparseable verdict is NOT done, because a verifier that cannot say clearly
  that something worked has not said it worked.
"""

from __future__ import annotations

import pytest

from jarvis.llm.plan import (
    MAX_REPLANS,
    MAX_STEPS,
    Plan,
    PlanStep,
    Verdict,
    needs_a_plan,
    parse_plan,
    parse_verdict,
    plan_prompt,
    run_plan,
    verify_prompt,
)


# --- when to plan at all -----------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    [
        "Turn the kitchen light on",
        "what's the temperature",
        "mute",
        "lights off",
    ],
)
def test_one_action_is_not_planned(request_text):
    """Planning it costs a model call to be told what was already obvious."""
    assert needs_a_plan(request_text) is False


@pytest.mark.parametrize(
    "request_text",
    [
        "Turn the garage lights off and then tell me which ones did not respond",
        "First check the disk, then purge the recorder if it is nearly full",
        "Research whisper-large-v3-turbo on CPU and summarise what you find",
        "Compare the two coding models and tell me which is faster on this box",
    ],
)
def test_a_request_with_more_than_one_thing_in_it_is_planned(request_text):
    assert needs_a_plan(request_text) is True


# --- planning ----------------------------------------------------------------


def test_a_plan_is_parsed_from_json_however_it_arrives():
    """Models put JSON inside prose, inside fences, and inside both."""
    for raw in (
        '{"steps": ["one", "two"]}',
        'Sure!\n```json\n{"steps": ["one", "two"]}\n```\nHope that helps.',
        'Here is the plan: {"steps": ["one", "two"]} — let me know.',
    ):
        assert parse_plan(raw, "req").titles == ["one", "two"]


def test_a_plan_is_capped():
    raw = {"steps": [f"step {i}" for i in range(40)]}
    import json

    assert len(parse_plan(json.dumps(raw), "req").steps) == MAX_STEPS


def test_prose_instead_of_a_plan_falls_back_to_the_request_itself():
    """The loop that follows is the one that ran before planning existed, so
    falling back to it costs nothing and raising would cost the turn."""
    made = parse_plan("I'll just get on with it.", "tidy the garage")
    assert made.titles == ["tidy the garage"]


def test_the_planning_prompt_names_the_tools_that_exist():
    prompt = plan_prompt("do a thing", ["light.turn_on", "web_search"])
    assert "light.turn_on" in prompt and "web_search" in prompt
    assert str(MAX_STEPS) in prompt
    # A step the model cannot act on is a plan that stalls.
    assert "ask the user" in prompt


# --- verifying ---------------------------------------------------------------


def test_the_verifier_is_given_the_step_and_the_outcome_and_nothing_else():
    prompt = verify_prompt("turn the garage lights off", "light.turn_on returned ok")
    assert "turn the garage lights off" in prompt
    assert "light.turn_on returned ok" in prompt
    # No persona, no plan, no history: a verifier that can see the argument for
    # an action agrees with it.
    assert "Jarvis" not in prompt
    assert "plan" not in prompt.lower().replace("what happened", "")


def test_a_clear_yes_is_done_and_a_clear_no_carries_its_reason():
    assert parse_verdict('{"done": true}').done is True
    verdict = parse_verdict('{"done": false, "reason": "two lights are still on"}')
    assert verdict.done is False
    assert "still on" in verdict.reason


def test_an_unparseable_verdict_is_not_done():
    """A verifier that cannot say clearly that something worked has not said it
    worked, and treating silence as success makes verification decoration."""
    for raw in ("", "hmm", "I think so?", "{oh dear"):
        assert parse_verdict(raw).done is False


def test_a_bare_yes_is_accepted_because_models_answer_in_words():
    assert parse_verdict("yes").done is True
    assert parse_verdict("Done.").done is True


# --- the loop ----------------------------------------------------------------


async def test_every_step_is_acted_on_then_verified_in_order():
    order: list[str] = []
    plan = Plan(request="r", steps=[PlanStep(title="a"), PlanStep(title="b")])

    async def act(step: PlanStep) -> str:
        order.append(f"act:{step.title}")
        return "did it"

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        order.append(f"verify:{step.title}")
        assert outcome == "did it"
        return Verdict(done=True)

    await run_plan(plan, act=act, verify=verify)
    assert order == ["act:a", "verify:a", "act:b", "verify:b"]
    assert [step.status for step in plan.steps] == ["done", "done"]


async def test_a_failed_verification_rewrites_what_is_left():
    plan = Plan(request="r", steps=[PlanStep(title="a"), PlanStep(title="b")])
    verdicts = iter([Verdict(done=False, reason="two are still on"), Verdict(done=True)])

    async def act(step: PlanStep) -> str:
        return "tried"

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return next(verdicts, Verdict(done=True))

    async def replan(made: Plan, failed: PlanStep) -> list[str]:
        assert "still on" in failed.reason, "the replanner is not told why"
        return ["turn off the two that are still on"]

    await run_plan(plan, act=act, verify=verify, replan=replan)
    assert plan.replans == 1
    assert plan.titles == ["a", "turn off the two that are still on"]


async def test_replanning_is_bounded():
    plan = Plan(request="r", steps=[PlanStep(title="a")])

    async def act(step: PlanStep) -> str:
        return "tried"

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return Verdict(done=False, reason="no")

    async def replan(made: Plan, failed: PlanStep) -> list[str]:
        return ["try again"]

    await run_plan(plan, act=act, verify=verify, replan=replan)
    # A third replan is the model rewriting the same failure in new words.
    assert plan.replans == MAX_REPLANS


async def test_a_replanner_that_gives_up_stops_the_plan():
    """An empty rewrite is the model saying it cannot be done; marching through
    steps it has just disowned would be worse than stopping."""
    plan = Plan(
        request="r", steps=[PlanStep(title="a"), PlanStep(title="b"), PlanStep(title="c")]
    )

    async def act(step: PlanStep) -> str:
        return "tried"

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return Verdict(done=False, reason="cannot")

    async def replan(made: Plan, failed: PlanStep) -> list[str]:
        return []

    await run_plan(plan, act=act, verify=verify, replan=replan)
    assert plan.titles == ["a"]


async def test_the_caller_is_told_about_each_step_twice():
    """Once when it starts, once when it is judged — which is what lets the
    task's steps show 'running' rather than jumping from queued to done."""
    seen: list[tuple[int, str]] = []
    plan = Plan(request="r", steps=[PlanStep(title="a")])

    async def act(step: PlanStep) -> str:
        return "ok"

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return Verdict(done=True)

    async def on_step(index: int, step: PlanStep) -> None:
        seen.append((index, step.status))

    await run_plan(plan, act=act, verify=verify, on_step=on_step)
    assert seen == [(0, "running"), (0, "done")]
