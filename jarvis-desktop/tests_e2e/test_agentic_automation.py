"""Jarvis planning and running a multi-step automation on a real desktop.

Everything else in this directory dispatches ONE action and asserts what
happened. This is the milestone's claim (M21): a model given a job that needs
several steps produces a plan, the plan runs against the real agent on this
machine, a Tier-3 step in the middle stops for a human, a failing step stops
the rest, and the whole thing is visible as task events — the same ones the
console draws.

What is real: the server, the agent, the policy engine, the tier arithmetic,
the consent gateway (answering from a file, and recording what it was asked),
the audit log, and the filesystem underneath. What is scripted: the model,
because the point is the *execution* of a plan rather than whether today's
model writes a good one — and a scripted plan is the only way to assert "the
third step never ran" without flakiness.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from support import DEVICE_ID

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

DISPATCH_TIMEOUT = 20.0


async def sequence(client: Any, steps: list[dict[str, Any]], reason: str = "the plan") -> dict:
    """`device_control.run_sequence`, the way the agent loop calls it."""
    response = await client.call_service_rest(
        "device_control",
        "run_sequence",
        {"steps": steps, "reason": reason},
        return_response=True,
    )
    return response["service_response"]


def step(action: str, params: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"device": DEVICE_ID, "action": action}
    if params is not None:
        out["params"] = params
    out.update(extra)
    return out


# ===========================================================================
# 1. a plan of three steps, carrying state, against the real machine
# ===========================================================================
async def test_a_three_step_plan_runs_and_carries_state(client, live):
    """Write a file, read it back, and check what came back.

    The third step is the one that matters: it is a *verification*, and it
    fails the sequence if the second step's result does not contain what the
    first one wrote. An automation that reports success without one is an
    automation that reports success.
    """
    live.control.set_consent("approved")
    target = live.workspace_path("plan-one.txt")

    outcome = await sequence(
        client,
        [
            step("write_file", {"path": str(target), "content": "written by a plan"}),
            step("read_file", {"path": str(target)}, save="file"),
            step(
                "read_file",
                {"path": str(target)},
                verify={"contains": "written by a plan"},
            ),
        ],
    )

    assert outcome["status"] == "ok", outcome
    assert outcome["completed"] == 3
    assert outcome["steps"][2]["verified"] is True
    # And the machine really has the file, which is the only assertion here
    # that a status string cannot fake.
    assert target.exists()
    assert "written by a plan" in target.read_text()


# ===========================================================================
# 2. a Tier-3 step in the middle stops for a human
# ===========================================================================
async def test_a_refused_step_stops_the_plan_and_the_rest_never_runs(client, live):
    """The security property, as a sequence rather than as a single call.

    A plan is one thing a person says yes to; the steps inside it are not. The
    delete is Tier 3, the answer is no, and what must be true afterwards is
    that the file is still there AND the step after it never happened.
    """
    victim = live.workspace_file("plan-victim.txt", "still here\n")
    after = live.workspace_path("plan-after.txt")
    live.control.set_consent("denied")
    prompts_before = len(live.control.prompts())

    outcome = await sequence(
        client,
        [
            step("get_system_state"),
            step("delete_file", {"path": str(victim)}),
            step("write_file", {"path": str(after), "content": "should never exist"}),
        ],
    )

    assert outcome["status"] == "denied", outcome
    assert outcome["failed_step"] == 2
    assert outcome["completed"] == 1
    assert outcome["steps"][2]["status"] == "skipped"
    # The two filesystem facts, which no status string can fake.
    assert victim.exists(), "a denied step deleted the file anyway"
    assert not after.exists(), "the plan carried on past a refusal"
    # And a human really was asked.
    assert len(live.control.prompts()) > prompts_before
    assert live.control.prompts()[-1]["action_id"] == "delete_file"


# ===========================================================================
# 3. an approved Tier-3 step lets the plan finish
# ===========================================================================
async def test_an_approved_step_lets_the_rest_run(client, live):
    victim = live.workspace_file("plan-approved.txt", "delete me\n")
    after = live.workspace_path("plan-continued.txt")
    live.control.set_consent("approved")

    outcome = await sequence(
        client,
        [
            step("delete_file", {"path": str(victim)}),
            step("write_file", {"path": str(after), "content": "the plan continued"}),
        ],
    )

    assert outcome["status"] == "ok", outcome
    assert not victim.exists()
    assert after.exists()
    assert live.control.prompts()[-1]["action_id"] in ("delete_file", "write_file")


# ===========================================================================
# 4. a step that fails on its own terms reports what did and did not run
# ===========================================================================
async def test_a_failing_step_is_reported_rather_than_glossed_over(client, live):
    live.control.set_consent("approved")
    after = live.workspace_path("plan-unreached.txt")

    outcome = await sequence(
        client,
        [
            step("read_file", {"path": str(live.workspace_path("does-not-exist.txt"))}),
            step("write_file", {"path": str(after), "content": "unreachable"}),
        ],
    )

    assert outcome["status"] != "ok"
    assert outcome["failed_step"] == 1
    assert not after.exists()
    # The sentence the model is given has to make it impossible to report this
    # as success without lying.
    assert "do not say it is done" in outcome["message"]


# ===========================================================================
# 5. the model plans it, and the task events say what happened
# ===========================================================================
async def test_the_model_plans_a_sequence_and_the_task_events_show_it(client, live, harness):
    """The whole loop: a spoken request, a planned sequence, and a live trail.

    The model is scripted — what is under test is that a plan the model writes
    reaches the device and that the console's own events describe it. The task
    events asserted here (`jarvis_task_tool_started`, `jarvis_task_updated`)
    are the ones `jarvis-web/src/lib/taskEvents.ts` reduces into the task
    detail page, read from `tests/contracts/task_events.json` by both suites.
    """
    live.control.set_consent("approved")
    target = live.workspace_path("model-plan.txt")
    harness.reset_ollama()
    harness.set_ollama_script(
        {
            "rules": [
                {
                    "name": "plan a desktop sequence",
                    "match": "tidy up",
                    "responses": [
                        {
                            "tool_calls": [
                                {
                                    "name": "run_device_sequence",
                                    "arguments": {
                                        "reason": "tidying up as asked",
                                        "steps": [
                                            {
                                                "device": DEVICE_ID,
                                                "action": "write_file",
                                                "params": {
                                                    "path": str(target),
                                                    "content": "tidied",
                                                },
                                            },
                                            {
                                                "device": DEVICE_ID,
                                                "action": "read_file",
                                                "params": {"path": str(target)},
                                                "verify": {"contains": "tidied"},
                                            },
                                        ],
                                    },
                                }
                            ]
                        },
                        {"say": "Done, Sir — written and checked."},
                    ],
                }
            ]
        }
    )

    seen: list[tuple[str, dict]] = []
    for event in ("jarvis_task_tool_started", "jarvis_task_updated"):
        stream = await client.subscribe_events(event)
        asyncio.ensure_future(_pump(stream, event, seen))

    answer = await client.conversation("Please tidy up my desktop notes.")
    speech = (
        ((answer.get("response") or {}).get("speech") or {}).get("plain") or {}
    ).get("speech", "")
    assert "written" in speech.lower() or "done" in speech.lower(), speech

    # The plan ran on the real machine.
    assert target.exists()
    assert target.read_text().strip() == "tidied"

    # And the model was actually given the tool, rather than the harness having
    # answered from its default script. Each recorded entry is
    # `{rule, payload, at}` — the payload is the `/api/chat` body, so the
    # toolbox is one level in.
    payloads = [entry.get("payload") or {} for entry in harness.ollama_requests()]
    tools = {
        tool.get("function", {}).get("name")
        for payload in payloads
        for tool in (payload.get("tools") or [])
    }
    assert "run_device_sequence" in tools, sorted(tools)

    await asyncio.sleep(0.5)
    kinds = {name for name, _ in seen}
    assert "jarvis_task_tool_started" in kinds or "jarvis_task_updated" in kinds, (
        f"no task events reached a client watching the turn: {kinds}"
    )


async def _pump(stream: Any, name: str, sink: list[tuple[str, dict]]) -> None:
    while True:
        try:
            event = await stream.next(timeout=30)
        except Exception:  # noqa: BLE001 - the socket closing ends the pump
            return
        if event is None:
            return
        sink.append((name, event.get("data") or {}))
