"""Plan → act → verify, for a request that is more than one thing.

The conversation agent is a bounded act-and-observe loop: the model calls tools,
sees results, and answers. That is right for "turn the kitchen light on" and it
is thin for "tidy the garage lights, then tell me which ones never turn on" —
where a plan exists in the model's head, is never written down, and nothing
checks whether each part actually happened.

This adds the two missing phases without replacing the loop:

    plan     one call, in a fresh context: what are the steps?
    act      the ordinary conversation loop, per step
    verify   one call per step, given ONLY what the step did: is it done?
    replan   on a failed verification, one call: what should the rest be?

## Why verification is a separate call

Asking the model that acted whether it succeeded gets "yes" almost every time —
it has just spent a context window arguing for its own actions. The verifier is
given the step, the tool results, and nothing else: no plan, no persona, no
history. It answers `done` or `not done, because …`, and "because" is what the
replanner is handed.

## What is bounded, and why each bound exists

`MAX_STEPS`      a plan longer than this is not a plan, it is a program.
`MAX_REPLANS`    two. A third replan has never once been the one that worked;
                 it is the model rewriting the same failure in new words.
`MAX_ROUNDS`     per step, from the agent's own `max_tool_rounds`.

## The steps are the task's steps

A plan is written into the task registry as it is made, so `/tasks/<id>` shows
what Jarvis intends before it starts doing it, and the current step is the one
being acted on. That is the whole reason the plan is a first-class object rather
than a paragraph in a prompt: a plan nobody can see is indistinguishable from
guessing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "MAX_REPLANS",
    "MAX_STEPS",
    "Plan",
    "PlanStep",
    "Verdict",
    "needs_a_plan",
    "parse_plan",
    "parse_verdict",
    "plan_prompt",
    "verify_prompt",
]

#: A plan longer than this is not a plan, it is a program — and a model that
#: produces one has misunderstood the request rather than decomposed it.
MAX_STEPS = 8

#: How many times a failed verification may rewrite the remaining steps. Two: a
#: third has never once been the one that worked, it is the same failure in new
#: words, and each one costs a model call the user is waiting through.
MAX_REPLANS = 2

#: Requests this short are single actions; planning them costs a model call to
#: be told what was already obvious.
MIN_PLANNABLE_CHARS = 24

#: Words that mean "and then", which is what makes a request multi-step. Cheap
#: and deliberately conservative: the cost of missing one is the old behaviour,
#: and the cost of a false positive is a wasted call on every "turn on the light".
_SEQUENCE_HINTS = re.compile(
    r"\b(then|after that|afterwards|and then|first|second|finally|"
    r"followed by|once you|when you(?:'ve| have)|next,)\b",
    re.IGNORECASE,
)

#: Verbs that ask for work rather than an answer, in a sentence long enough to
#: contain more than one of them.
_WORK_HINTS = re.compile(
    r"\b(research|investigate|compare|audit|tidy|clean up|migrate|refactor|"
    r"go through|work out|figure out|find out|check (?:every|all|each))\b",
    re.IGNORECASE,
)


@dataclass
class PlanStep:
    """One step, and what happened to it."""

    title: str
    status: str = "queued"
    #: What the acting turn produced.
    outcome: str = ""
    #: The verifier's reason, when it said no.
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "status": self.status,
            "outcome": self.outcome,
            "reason": self.reason,
        }


@dataclass
class Plan:
    """What Jarvis intends to do, before it starts."""

    request: str
    steps: list[PlanStep] = field(default_factory=list)
    replans: int = 0

    @property
    def titles(self) -> list[str]:
        return [step.title for step in self.steps]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "replans": self.replans,
            "steps": [step.as_dict() for step in self.steps],
        }


@dataclass
class Verdict:
    """The verifier's answer about one step."""

    done: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"done": self.done, "reason": self.reason}


def needs_a_plan(request: str) -> bool:
    """Is this worth planning, or is it one action?

    Deliberately cheap and conservative. Planning a request that did not need it
    costs a model call and shows the user a one-step plan; NOT planning one that
    did is the behaviour that was there before, which is the safer failure.
    """
    text = str(request or "").strip()
    if len(text) < MIN_PLANNABLE_CHARS:
        return False
    if _SEQUENCE_HINTS.search(text):
        return True
    if _WORK_HINTS.search(text) and len(text.split()) >= 6:
        return True
    # Two or more imperative clauses joined by "and": "turn the lights off and
    # tell me which ones did not respond".
    return text.lower().count(" and ") >= 2


def plan_prompt(request: str, tools: list[str] | None = None) -> str:
    """The planning call, in a fresh context.

    Fresh on purpose: a planner that can see the conversation writes steps about
    the conversation. It is given the request and the names of the tools that
    exist, and nothing else.
    """
    available = ", ".join(sorted(tools or [])[:40]) or "none"
    return (
        "Break this request into the fewest steps that actually do it.\n\n"
        f"REQUEST: {request}\n\n"
        f"TOOLS AVAILABLE: {available}\n\n"
        "Rules:\n"
        f"- At most {MAX_STEPS} steps. Fewer is better.\n"
        "- Each step is one thing you can do and then check.\n"
        "- No step may be 'ask the user' — you cannot, mid-plan.\n"
        "- If this is really one action, answer with one step.\n\n"
        'Answer with JSON only: {"steps": ["...", "..."]}'
    )


def verify_prompt(step: str, outcome: str) -> str:
    """The verification call, given the step and what happened — nothing else.

    No plan, no persona, no history: a verifier that can see the argument for an
    action agrees with it. This one can only see what the action produced.
    """
    return (
        "A step was attempted. Judge ONLY whether it is actually done, from the "
        "evidence below. Do not be generous: 'it probably worked' is not done.\n\n"
        f"STEP: {step}\n\n"
        f"WHAT HAPPENED:\n{outcome[:2000]}\n\n"
        'Answer with JSON only: {"done": true} or {"done": false, "reason": "..."}'
    )


def replan_prompt(plan: Plan, failed: PlanStep, remaining: list[str]) -> str:
    return (
        "A step failed verification. Rewrite the REMAINING steps so the request "
        "still gets done, or return an empty list if it cannot be.\n\n"
        f"REQUEST: {plan.request}\n"
        f"FAILED STEP: {failed.title}\n"
        f"WHY: {failed.reason or 'the verifier said it was not done'}\n"
        f"REMAINING STEPS WERE: {json.dumps(remaining)}\n\n"
        'Answer with JSON only: {"steps": ["...", "..."]}'
    )


def _json_object(raw: str) -> dict[str, Any]:
    """The first JSON object in a model's answer, or {}.

    Models put JSON inside prose, inside fences, and inside both. This is the
    same tolerance `llm/toolcalls.py` applies to tool calls, for the same
    reason: refusing to parse a good answer because it arrived with a sentence
    in front of it is a bug, not a standard.
    """
    text = str(raw or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, dict) else {}
        start = text.find("{", start + 1)
    return {}


def parse_plan(raw: str, request: str = "") -> Plan:
    """A model's answer to `plan_prompt`, as a Plan.

    A model that answers with prose instead of JSON gets a one-step plan of the
    request itself rather than an exception: the loop that follows is the same
    loop that ran before planning existed, so falling back to it costs nothing.
    """
    payload = _json_object(raw)
    steps = payload.get("steps")
    titles: list[str] = []
    if isinstance(steps, list):
        for entry in steps:
            title = str(entry).strip() if not isinstance(entry, dict) else str(
                entry.get("title") or entry.get("step") or ""
            ).strip()
            if title:
                titles.append(title[:200])
    if not titles and request:
        titles = [request[:200]]
    return Plan(request=request, steps=[PlanStep(title=title) for title in titles[:MAX_STEPS]])


def parse_verdict(raw: str) -> Verdict:
    """A model's answer to `verify_prompt`.

    An unparseable answer is NOT done: a verifier that cannot say clearly that
    something worked has not said it worked, and treating silence as success is
    how a verification step becomes decoration.
    """
    payload = _json_object(raw)
    if "done" not in payload:
        text = str(raw or "").strip().lower()
        if text.startswith(("yes", "done", "true")):
            return Verdict(done=True)
        return Verdict(done=False, reason=str(raw or "")[:200] or "no verdict was given")
    done = bool(payload.get("done"))
    return Verdict(done=done, reason=str(payload.get("reason") or "")[:200] if not done else "")


async def run_plan(
    plan: Plan,
    *,
    act: Callable[[PlanStep], Awaitable[str]],
    verify: Callable[[PlanStep, str], Awaitable[Verdict]],
    replan: Callable[[Plan, PlanStep], Awaitable[list[str]]] | None = None,
    on_step: Callable[[int, PlanStep], Awaitable[None]] | None = None,
) -> Plan:
    """Act on each step, verify it, and replan when a verification fails.

    Pure control flow: the three callbacks do the model calls, which is what
    makes this testable without one.
    """
    index = 0
    while index < len(plan.steps):
        step = plan.steps[index]
        step.status = "running"
        if on_step is not None:
            await on_step(index, step)

        step.outcome = await act(step)
        verdict = await verify(step, step.outcome)
        step.status = "done" if verdict.done else "error"
        step.reason = verdict.reason
        if on_step is not None:
            await on_step(index, step)

        if not verdict.done and replan is not None and plan.replans < MAX_REPLANS:
            plan.replans += 1
            rewritten = await replan(plan, step)
            if rewritten:
                # Keep what is settled, replace what is left.
                plan.steps = plan.steps[: index + 1] + [
                    PlanStep(title=title[:200]) for title in rewritten[: MAX_STEPS - index - 1]
                ]
                index += 1
                continue
            # An empty rewrite is the model saying it cannot be done. Stop
            # rather than marching through steps it has just disowned.
            del plan.steps[index + 1 :]
            break
        index += 1
    return plan
