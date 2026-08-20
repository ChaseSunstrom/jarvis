"""Did the workflow Jarvis wrote actually work?

## The gap this closes

Today the story ends at "created". Jarvis writes a workflow, says "connect
Gmail and switch it on in n8n", and then has no way of ever answering a
question about it again. Ask *"did that expense thing run?"* and the honest
answer is that Jarvis does not know — even though the instance has been
recording every execution the whole time.

That is the difference between "wrote a file" and "automation".

## What it joins

Three things that already exist here and have never been asked together:

    needed_connections()   what is still unattached
    workflow["active"]     is it even switched on
    client.executions()    has it run, and did it fail

Each on its own is a half-answer. "Active" with nothing attached is a workflow
that errors every time it fires. "Connected and active" with zero runs in a
week is the interesting case — it means the trigger never fired, which is
usually a webhook nobody called or a schedule in the wrong timezone, and it is
invisible from anywhere else.

## What it must never read

**Execution DATA.** `GET /executions` takes `includeData=true`, and that
payload is the body of the user's actual emails, invoices and documents. This
module reads status, timing and mode, and there is a test asserting the
parameter is never sent. A "did it work" check that exfiltrated the contents
of everything that went through the workflow would be a spectacular own goal.

## One n8n quirk worth knowing

`GET /executions` **excludes running executions** unless you explicitly pass
`status=running`. So "is it running right now" is a second call, and a health
check that only made the first one would report "never run" about a workflow
that is running as you read it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client import N8nError
from .workflows import needed_connections

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

__all__ = ["Health", "assess", "async_health"]

#: How many executions to look at. Enough to see a pattern, few enough that
#: this is one small request.
RUNS_READ = 20

#: n8n's own status vocabulary for a finished run that did not finish well.
BAD_STATUSES = frozenset({"error", "crashed", "failed"})
GOOD_STATUSES = frozenset({"success"})


@dataclass
class Health:
    """One workflow's state of health, as findings and one sentence."""

    workflow_id: str
    name: str
    active: bool
    healthy: bool
    summary: str
    unattached: list[dict[str, str]]
    runs: int = 0
    failures: int = 0
    running_now: int = 0
    last_status: str = ""
    last_run: str = ""
    next_step: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "active": self.active,
            "healthy": self.healthy,
            "summary": self.summary,
            "connections_needed": self.unattached,
            "runs": self.runs,
            "failures": self.failures,
            "running_now": self.running_now,
            "last_status": self.last_status,
            "last_run": self.last_run,
            "next_step": self.next_step,
        }


def assess(
    workflow: dict[str, Any],
    runs: list[dict[str, Any]],
    running: list[dict[str, Any]] | None = None,
) -> Health:
    """Join the three answers into one. Pure, so the interesting cases are
    testable without a live instance."""
    workflow_id = str(workflow.get("id") or "")
    name = str(workflow.get("name") or "")
    active = bool(workflow.get("active"))
    unattached = needed_connections(workflow)

    finished = [r for r in runs if isinstance(r, dict)]
    failures = [r for r in finished if str(r.get("status") or "").lower() in BAD_STATUSES]
    latest = finished[0] if finished else {}
    running_now = len(running or [])

    healthy = True
    summary = ""
    next_step = ""

    if unattached:
        healthy = False
        asked = ", ".join(f"{c['credential_type']} for {c['node']!r}" for c in unattached)
        summary = f"Not connected yet: it still needs {asked}."
        next_step = (
            "In n8n: Credentials -> New, make the credential, then open the "
            "workflow and attach it to that node."
        )
        if not active:
            summary += " It is also switched off."
    elif not active:
        healthy = False
        summary = "Everything is connected, but the workflow is switched off."
        next_step = "Open it in n8n and switch it on."
    elif failures:
        healthy = False
        summary = (
            f"Switched on, and {len(failures)} of the last {len(finished)} runs "
            f"failed ({_status_words(failures)})."
        )
        next_step = "Open the failed execution in n8n; it shows which node threw."
    elif not finished and not running_now:
        # The interesting one, and the reason this module is worth having.
        healthy = False
        summary = (
            "Switched on and connected, but it has never run. Either nothing "
            "has triggered it yet, or the trigger is not firing."
        )
        next_step = (
            "If it is on a schedule, check the timezone in the workflow's "
            "settings. If it is a webhook, check that whatever calls it is "
            "using the production URL rather than the test one."
        )
    else:
        summary = (
            f"Working: switched on, connected, and the last {len(finished)} "
            f"run{'' if len(finished) == 1 else 's'} succeeded."
            if finished
            else "Switched on, connected, and running right now."
        )

    if running_now and not unattached and active:
        summary += f" {running_now} running right now."

    return Health(
        workflow_id=workflow_id,
        name=name,
        active=active,
        healthy=healthy,
        summary=summary,
        unattached=unattached,
        runs=len(finished),
        failures=len(failures),
        running_now=running_now,
        last_status=str(latest.get("status") or ""),
        last_run=str(latest.get("startedAt") or latest.get("started_at") or ""),
        next_step=next_step,
    )


def _status_words(failures: list[dict[str, Any]]) -> str:
    seen: list[str] = []
    for run in failures:
        status = str(run.get("status") or "").lower()
        if status and status not in seen:
            seen.append(status)
    return ", ".join(seen) or "error"


async def async_health(jarvis: "Jarvis", workflow_id: str) -> dict[str, Any]:
    """Read the three things and report. Raises `N8nError` if n8n will not
    answer at all, because "cannot reach n8n" is not a health verdict."""
    from . import _require

    _cfg, client = _require(jarvis)
    workflow = await client.get_workflow(workflow_id)
    # Metadata only. `includeData` is never passed — see the module docstring.
    runs = await client.executions(workflow_id=workflow_id, limit=RUNS_READ)
    try:
        running = await client.executions(
            workflow_id=workflow_id, limit=RUNS_READ, status="running"
        )
    except N8nError:
        # Older instances reject the filter. A missing "running" count is a
        # smaller lie than a failed health check.
        running = []
    return assess(workflow, runs, running).as_dict()
