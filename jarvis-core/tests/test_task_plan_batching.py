"""The task planner batches read-only steps (M60).

Three lookups are one question to the model, not three turns each paying to
prefill the prompt. Pure control flow: `run_plan` with fakes, no model.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.llm.plan import Plan, PlanStep, Verdict, parse_plan, run_plan  # noqa: E402

pytestmark = pytest.mark.asyncio


def test_the_planner_marks_a_step_that_only_looks():
    plan = parse_plan(
        '{"steps": [{"title": "read the hall temperature", "reads_only": true}, '
        '{"title": "read the study temperature", "reads_only": true}, '
        '{"title": "turn the heating up", "reads_only": false}, "say what changed"]}',
        "warm the house",
    )
    assert [s.read_only for s in plan.steps] == [True, True, False, False]
    assert plan.steps[3].title == "say what changed", "a plain string step still parses"
    assert plan.steps[0].as_dict()["read_only"] is True


async def test_read_only_steps_run_as_one_round():
    plan = Plan(
        request="warm the house",
        steps=[
            PlanStep(title="read the hall temperature", read_only=True),
            PlanStep(title="read the study temperature", read_only=True),
            PlanStep(title="turn the heating up"),
            PlanStep(title="read the hall temperature again", read_only=True),
        ],
    )
    acted: list[str] = []
    batches: list[list[str]] = []

    async def act(step: PlanStep) -> str:
        acted.append(step.title)
        return f"did: {step.title}"

    async def act_many(steps: list[PlanStep]) -> list[str]:
        batches.append([s.title for s in steps])
        return ["1. hall 19\n2. study 21"] * len(steps)

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return Verdict(done=True, reason="")

    done = await run_plan(plan, act=act, verify=verify, act_many=act_many)
    assert batches == [["read the hall temperature", "read the study temperature"]], batches
    # The action is never batched, and a lone read-only step is a plain act.
    assert acted == ["turn the heating up", "read the hall temperature again"]
    assert [s.status for s in done.steps] == ["done"] * 4
    assert done.steps[0].outcome == done.steps[1].outcome


async def test_a_failed_member_of_a_batch_stops_the_plan_where_it_would_have_unbatched():
    plan = Plan(
        request="x",
        steps=[
            PlanStep(title="a", read_only=True),
            PlanStep(title="b", read_only=True),
            PlanStep(title="c"),
        ],
    )

    async def act(step: PlanStep) -> str:
        return "did"

    async def act_many(steps: list[PlanStep]) -> list[str]:
        return ["one answer"] * len(steps)

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return Verdict(done=step.title != "b", reason="" if step.title != "b" else "no reading")

    async def replan(plan: Plan, failed: PlanStep) -> list[str]:
        return []  # the model gives up: the rest is dropped

    done = await run_plan(plan, act=act, verify=verify, replan=replan, act_many=act_many)
    assert [s.status for s in done.steps] == ["done", "error"]
    assert done.steps[1].reason == "no reading"


async def test_without_act_many_nothing_changes():
    plan = Plan(request="x", steps=[PlanStep(title="a", read_only=True), PlanStep(title="b", read_only=True)])
    acted: list[str] = []

    async def act(step: PlanStep) -> str:
        acted.append(step.title)
        return "did"

    async def verify(step: PlanStep, outcome: str) -> Verdict:
        return Verdict(done=True, reason="")

    await run_plan(plan, act=act, verify=verify)
    assert acted == ["a", "b"]
