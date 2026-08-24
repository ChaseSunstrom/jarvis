"""Plan → act → verify, against a real jarvis-core.

`jarvis-core/tests/test_agent_loop.py` pins the pieces in isolation, with no
model and no server. This drives the whole thing end to end: a real core, a real
task registry and engine, a real websocket client watching, and a scripted model
that answers the planning call, the acting calls and the verification calls
differently — which is the part that cannot be tested any other way, because the
loop's correctness IS which prompt gets which answer.

The failure it exists to catch: a "plan" that is a paragraph inside one prompt,
never written down, whose steps nobody can see and whose outcomes nothing
checks. Here the plan has to arrive on the task before the work starts, and a
verifier that says "no" has to change what happens next.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.e2e

#: The request the assistant is asked to take on. It has to survive
#: `needs_a_plan` ("then" makes it multi-step) and be distinctive enough to
#: match on without also matching the planner's own prompt, which quotes it.
REQUEST = "Check the hall light and then tell me whether it answered"

PLAN = json.dumps({"steps": ["check the hall light", "report what it found"]})


def script(verify_responses: list[dict], extra_rules: list[dict] | None = None) -> dict:
    """The scripted brain for one test.

    Order matters and is the whole trick: the planner's prompt QUOTES the
    request, so the rule that turns the request into a background task has to
    come after the rules that recognise the planning and verifying prompts, or
    the planner would be answered with a tool call.
    """
    rules: list[dict] = [
        {
            "name": "plan",
            "match": "Break this request into the fewest steps",
            "scope": "all",
            "repeat": True,
            "say": PLAN,
        },
        *(extra_rules or []),
        {
            "name": "verify",
            "match": "Judge ONLY whether it is actually done",
            "scope": "all",
            "responses": verify_responses,
        },
        {
            "name": "hand it to the background",
            "match": "hall light and then tell me",
            "scope": "user",
            "responses": [
                {
                    "tool_calls": [
                        {
                            "name": "run_background_task",
                            "arguments": {"description": REQUEST},
                        }
                    ]
                },
                {"say": "On it, Sir."},
            ],
        },
    ]
    return {
        "models": ["qwen3:8b"],
        "rules": rules,
        # Every acting call lands here: one step, one plain answer.
        "default": {"say": "The hall light is off and it answered."},
    }


async def test_a_multi_step_request_is_planned_acted_on_and_verified(harness, client):
    """The plan lands on the task, and every step is judged rather than assumed."""
    harness.reset_ollama()
    harness.set_ollama_script(script([{"say": json.dumps({"done": True})}]))
    try:
        answer = await client.conversation(REQUEST)
        task = await _settle(client, await _task_from(client, answer))

        assert task["status"] == "done", task
        titles = [step["title"] for step in task["steps"]]
        # The plan IS the step list — not a preamble in front of one.
        assert titles == ["check the hall light", "report what it found"], titles
        assert all(step["status"] == "done" for step in task["steps"]), task["steps"]

        prompts = _prompts(harness)
        assert "Break this request into the fewest steps" in prompts
        # Verification is a separate call with its own context. If it were part
        # of the acting turn the model would be marking its own homework.
        assert "Judge ONLY whether it is actually done" in prompts
    finally:
        harness.set_ollama_script(None)


async def test_a_step_that_fails_verification_is_replanned(harness, client):
    """A verifier that can say "no" is the only thing that makes a plan honest."""
    harness.reset_ollama()
    harness.set_ollama_script(
        script(
            [
                {"say": json.dumps({"done": False, "reason": "the light is still on"})},
                {"say": json.dumps({"done": True})},
            ],
            extra_rules=[
                {
                    "name": "replan",
                    "match": "Rewrite the REMAINING steps",
                    "scope": "all",
                    "repeat": True,
                    "say": json.dumps({"steps": ["turn the hall light off properly"]}),
                }
            ],
        )
    )
    try:
        answer = await client.conversation(REQUEST)
        task = await _settle(client, await _task_from(client, answer))

        titles = [step["title"] for step in task["steps"]]
        assert "turn the hall light off properly" in titles, titles

        prompts = _prompts(harness)
        assert "Rewrite the REMAINING steps" in prompts
        # The replanner was told WHY it failed — which is the only thing that
        # makes a rewrite different from a retry of the same step.
        assert "still on" in prompts
    finally:
        harness.set_ollama_script(None)


def _prompts(harness) -> str:
    """Everything the model was actually sent, as one searchable blob."""
    return "\n".join(json.dumps(entry.get("payload", {})) for entry in harness.ollama_requests())


async def _task_from(client, answer) -> str:
    """The id of the background task the turn started.

    Read off the task list rather than the reply: the assistant's sentence is
    the model's, and a test that parsed it would be testing the script.
    """
    for _ in range(80):
        listing = await client.command("jarvis/tasks/list")
        tasks = listing.get("tasks") if isinstance(listing, dict) else None
        for task in tasks or []:
            if task.get("kind") == "background" and REQUEST[:20] in task.get("title", ""):
                return task["id"]
        await asyncio.sleep(0.25)
    raise AssertionError(f"no background task was created for the turn: {answer}")


async def _settle(client, task_id: str, tries: int = 200) -> dict:
    """Poll until the task finishes — the engine runs it out of band."""
    task: dict = {}
    for _ in range(tries):
        answer = await client.command("jarvis/tasks/get", task_id=task_id)
        task = answer["task"] if isinstance(answer, dict) and "task" in answer else answer
        if task.get("finished") or task.get("status") in {"done", "error", "cancelled"}:
            return task
        await asyncio.sleep(0.25)
    raise AssertionError(f"task {task_id} never finished: {task}")
