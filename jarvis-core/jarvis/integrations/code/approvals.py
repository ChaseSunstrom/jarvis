"""Held actions inside a coding job, and the human who releases them.

The tool registry's gate ends the model's turn: a Tier-3 call returns
``approval_required`` and the conversation moves on, because a chat turn that
blocked for four minutes waiting on a phone would be a chat turn nobody could
have. A coding job is the opposite shape — it is already a background task with
a progress bar, and the only sensible thing for it to do while waiting for
"yes" is *wait*.

So this is a second gate with the same vocabulary and a different behaviour:

    decision = await approvals.ask(task_id=…, kind="command", summary="pytest -q")
    if not decision.approved:
        return decision.refusal        # the model is told, and does not retry

It fires the same two events (``jarvis_approval_required`` /
``jarvis_approval_resolved``) carrying the same keys, so the console's existing
approval surface shows a coding job's held edit without knowing anything about
coding jobs, and `jarvis/approve` resolves it through the same command the
phone already sends.

What it deliberately does not do: approve itself. There is no path here that
resolves a request without a human calling `resolve()`, and an unanswered
request expires as a **denial** — the direction that fails safe.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: The same event names the tool registry's gate fires. Not imported from
#: `llm.tools` on purpose — this module must not drag the whole toolbox in — but
#: pinned to it by `tests/test_code_approvals.py`.
EVENT_APPROVAL_REQUIRED = "jarvis_approval_required"
EVENT_APPROVAL_RESOLVED = "jarvis_approval_resolved"

#: How long a held action waits for an answer before it is refused.
#:
#: Ten minutes rather than the chat gate's five: a coding job's approval arrives
#: while somebody is reading a diff, and a diff takes longer to read than "may I
#: unlock the door" takes to decide.
DEFAULT_TTL = 600.0

#: What the model is told when a human says no. Phrased as a decision that has
#: already happened, because a model told "not right now" tries again.
REFUSED = (
    "The user declined this. It has NOT run and must not be attempted again, "
    "in this form or any other. Carry on with what you can do without it, or "
    "stop and say what you would have needed."
)

EXPIRED = (
    "Nobody answered the request to run this, so it has NOT run. Do not retry "
    "it. Carry on with what you can do without it."
)


@dataclass(slots=True)
class Decision:
    approved: bool
    request_id: str
    reason: str = ""

    @property
    def refusal(self) -> str:
        return "" if self.approved else (self.reason or REFUSED)


@dataclass(slots=True)
class Held:
    id: str
    task_id: str
    kind: str
    summary: str
    detail: str
    created: float
    expires_at: float
    future: "asyncio.Future[Decision]" = field(compare=False, repr=False, default=None)  # type: ignore[assignment]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.id,
            "task_id": self.task_id,
            # `tool` and `tier` so the console's existing approval row renders
            # this without a special case: it is a Tier-3 action either way.
            "tool": f"code.{self.kind}",
            "tier": 3,
            "kind": self.kind,
            "arguments": {"summary": self.summary},
            "summary": self.summary,
            "detail": self.detail,
            "created": self.created,
            "expires_at": self.expires_at,
        }


class CodeApprovals:
    """Every coding job's held actions on this server."""

    def __init__(self, jarvis: "Jarvis", ttl: float = DEFAULT_TTL) -> None:
        self.jarvis = jarvis
        self.ttl = max(10.0, float(ttl))
        self._held: dict[str, Held] = {}

    # --- asking -----------------------------------------------------------
    async def ask(
        self,
        *,
        task_id: str,
        kind: str,
        summary: str,
        detail: str = "",
        timeout: float | None = None,
    ) -> Decision:
        """Hold one action until a human answers, or until it expires."""
        loop = asyncio.get_running_loop()
        now = time.time()
        wait = float(timeout if timeout is not None else self.ttl)
        held = Held(
            id=uuid.uuid4().hex[:12],
            task_id=task_id,
            kind=str(kind),
            summary=str(summary)[:400],
            detail=str(detail)[:8000],
            created=now,
            expires_at=now + wait,
            future=loop.create_future(),
        )
        self._held[held.id] = held
        payload = held.as_dict()
        payload["description"] = f"Jarvis Code wants to {kind}: {held.summary}"
        self._fire(EVENT_APPROVAL_REQUIRED, payload)
        # On the task as well as on the bus. The bus reaches a console that is
        # open right now; the task record is what somebody finds when they come
        # back to a job that has been sitting there for six minutes.
        self._note(task_id, f"waiting for approval: {held.summary}")
        _LOGGER.info("Code approval required for %s (%s)", kind, held.id)
        try:
            return await asyncio.wait_for(held.future, timeout=wait)
        except asyncio.TimeoutError:
            self._held.pop(held.id, None)
            self._fire(
                EVENT_APPROVAL_RESOLVED, {**held.as_dict(), "approved": False, "expired": True}
            )
            self._note(task_id, f"approval expired: {held.summary}")
            return Decision(False, held.id, EXPIRED)
        finally:
            self._held.pop(held.id, None)

    # --- answering --------------------------------------------------------
    def resolve(self, request_id: str, approved: bool) -> bool:
        """Release or refuse one held action. Single use; True if it was ours."""
        held = self._held.pop(str(request_id or ""), None)
        if held is None:
            return False
        decision = Decision(bool(approved), held.id, "" if approved else REFUSED)
        if held.future is not None and not held.future.done():
            held.future.set_result(decision)
        self._fire(EVENT_APPROVAL_RESOLVED, {**held.as_dict(), "approved": bool(approved)})
        self._note(
            held.task_id,
            ("approved: " if approved else "declined: ") + held.summary,
        )
        return True

    def pending(self, task_id: str = "") -> list[dict[str, Any]]:
        rows = [held.as_dict() for held in self._held.values()]
        if task_id:
            rows = [row for row in rows if row["task_id"] == task_id]
        return sorted(rows, key=lambda row: row["created"])

    def cancel_task(self, task_id: str) -> None:
        """A job that is stopping does not leave a request nobody can answer."""
        for held in list(self._held.values()):
            if held.task_id == task_id:
                self.resolve(held.id, False)

    # --- plumbing ---------------------------------------------------------
    def _fire(self, event: str, payload: dict[str, Any]) -> None:
        """`fire`, not `async_fire`.

        `async_fire` is a coroutine that awaits every listener; calling it
        without awaiting produced a RuntimeWarning and, far worse, an event
        that was never delivered — so a held action was invisible to every
        console and to the live rig, which sat waiting for a request that had
        already been made. `fire` schedules coroutine listeners and returns,
        which is what a gate wants: it is about to block on the answer.
        """
        bus = getattr(self.jarvis, "bus", None)
        if bus is None:  # pragma: no cover - core always has one
            return
        try:
            bus.fire(event, payload)
        except Exception:  # pragma: no cover - a gate must not die on its own event
            _LOGGER.exception("could not announce %s", event)

    def _note(self, task_id: str, text: str) -> None:
        registry = getattr(self.jarvis, "tasks", None)
        if registry is None or not task_id:
            return
        try:
            registry.output(task_id, text + "\n", stream="stdout")
        except Exception:  # pragma: no cover - a note is never load-bearing
            _LOGGER.debug("could not write the approval note", exc_info=True)
