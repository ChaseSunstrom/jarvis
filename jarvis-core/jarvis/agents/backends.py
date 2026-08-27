"""Where a delegated piece of work can actually go.

M20 gave the lead a set of specialists — small LLM agents with narrowed
toolboxes — and that is the right shape for "read these four pages and tell me
what they say". It is the wrong shape for "and fix the failing tests in the
scraper", which is not a conversation with a model but a coding job with a
sandbox, a branch and an approval gate.

So a plan's entries name a **backend**:

    agent          one of `config/agents/*.md`, the M20 path (the default)
    research       the research engine: several searches, pages read, cited
    code           a coding job, local agent
    code:claude-code   the same job, delegated (M41) — off unless configured

Everything a backend produces comes back as the same `Finding`, so the lead
rolls up one shape whatever ran. What differs is only who does the work.

## Why these run as TASKS rather than as calls

A research run is minutes and a coding job can be longer. Both already exist as
task-producing subsystems with their own progress, their own steps and their
own approval gates — so this waits on the task rather than reimplementing any
of it. The console draws the tree it already draws; an approval that stops a
coding job stops that child and nothing else, and the lead reports it as
stopped rather than pretending it finished.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: How a plan entry names something that is not a subagent.
BACKEND_AGENT = "agent"
BACKEND_RESEARCH = "research"
BACKEND_CODE = "code"

BACKENDS = (BACKEND_AGENT, BACKEND_RESEARCH, BACKEND_CODE)

#: How long to wait for a delegated TASK. Longer than a subagent's timeout
#: because the whole point of these is that they are the slow work; bounded
#: because a lead that never finishes is a task nobody can act on.
TASK_TIMEOUT = 1800.0

#: How often to look at a running task. Cheap — it is a dict lookup — and slow
#: enough not to spin.
POLL_SECONDS = 2.0


def split(name: str) -> tuple[str, str]:
    """`"code:claude-code"` -> `("code", "claude-code")`. Unknown -> the agent path."""
    raw = str(name or "").strip().lower()
    head, _, tail = raw.partition(":")
    if head in (BACKEND_RESEARCH, BACKEND_CODE):
        return head, tail
    return BACKEND_AGENT, raw


async def wait_for_task(jarvis: "Jarvis", task_id: str, timeout: float = TASK_TIMEOUT) -> Any:
    """Wait for a task to stop moving. Returns it, whatever state it ended in.

    "Stopped" includes `error` and `cancelled` — an approval nobody answered
    ends a coding job, and the lead has to report that rather than wait out the
    timeout on something that will never move again.
    """
    from ..tasks import STATUS_CANCELLED, STATUS_DONE, STATUS_ERROR

    finished = {STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED}
    deadline = time.monotonic() + timeout
    registry = jarvis.tasks
    while time.monotonic() < deadline:
        task = registry.get(task_id)
        if task is None:
            return None
        if str(getattr(task, "status", "")) in finished:
            return task
        await asyncio.sleep(POLL_SECONDS)
    return registry.get(task_id)


async def run_research(jarvis: "Jarvis", question: str, lead_task_id: str = "") -> dict[str, Any]:
    """One research run, waited out, reported as a finding's worth of text."""
    if not jarvis.services.has_service("research", "run"):
        return {"ok": False, "error": "the research integration is not set up"}
    answer = await jarvis.services.async_call(
        "research", "run", {"question": question, "parent_id": lead_task_id},
        blocking=True, return_response=True,
    )
    task_id = str((answer or {}).get("task_id") or "")
    if not task_id:
        return {"ok": False, "error": f"research did not start: {answer}"}
    task = await wait_for_task(jarvis, task_id)
    if task is None:
        return {"ok": False, "error": "the research task disappeared", "task_id": task_id}
    ok = str(getattr(task, "status", "")) == "done"
    return {
        "ok": ok,
        "task_id": task_id,
        "text": str(getattr(task, "result", "") or ""),
        "error": "" if ok else str(getattr(task, "error", "") or "the research run did not finish"),
    }


async def run_code(
    jarvis: "Jarvis",
    instruction: str,
    lead_task_id: str = "",
    backend: str = "",
    repo: str = "",
) -> dict[str, Any]:
    """One coding job, on whichever backend, waited out.

    The repository is not guessed when there is more than one: a coding job
    aimed at the wrong repository is the most expensive kind of helpfulness
    available here.
    """
    from ..integrations.code import async_start, get_config

    cfg = get_config(jarvis)
    if cfg is None or not cfg.repositories:
        return {"ok": False, "error": "no repositories are configured for coding jobs"}
    name = repo or (next(iter(cfg.repositories)) if len(cfg.repositories) == 1 else "")
    if not name:
        return {
            "ok": False,
            "error": (
                "which repository? there are several: " + ", ".join(cfg.repositories)
            ),
        }
    started = await async_start(
        jarvis, name, instruction, source="delegate", backend=backend
    )
    if isinstance(started, str):
        return {"ok": False, "error": started}
    task = await wait_for_task(jarvis, started.id)
    ok = str(getattr(task, "status", "")) == "done"
    return {
        "ok": ok,
        "task_id": started.id,
        "text": str(getattr(task, "result", "") or ""),
        "backend": backend or cfg.backend,
        "error": "" if ok else str(getattr(task, "error", "") or "the coding job did not finish"),
    }
