"""The registry of everything this machine can do, and the single door every
command must come through.

Nothing else in the agent may call :meth:`Action.run` directly. The order inside
:meth:`ActionRegistry.dispatch` **is** the security property:

    look up -> local tier -> raise by per-params tier -> raise by requested tier
    -> user policy -> PolicyEngine -> (maybe) human -> execute under a timeout
    -> re-check the store -> audit

The server's ``tier`` field is advisory and can only make things stricter; the
local table in :mod:`jarvis_desktop.actions.builtins` is the authority.

Mirrors ``android-app/.../automation/actions/ActionRegistry.kt``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from ..audit import AuditEntry, AuditLog
from ..consent import ApprovalRequest, ApprovalVerdict, ConsentGateway, DenyAllGateway
from ..policy import (
    ActionTier,
    Decision,
    PolicyEngine,
    PolicyProvider,
    PolicyRequest,
    TrustLevel,
    UserPolicy,
)
from .base import Action, ActionContext, ActionResult, Status

_LOGGER = logging.getLogger(__name__)

__all__ = ["ActionRegistry", "DispatchOutcome"]

#: Extra time the dispatcher waits past the prompt's own deadline before it
#: gives up and treats the answer as a timeout (= denied).
_APPROVAL_GRACE_S = 5.0


@dataclass
class DispatchOutcome:
    """The full record of one dispatch, for callers that want more than the
    wire result (the CLI, the trigger runner, tests)."""

    result: ActionResult
    tier: ActionTier
    decision: Decision
    note: str


class ActionRegistry:
    def __init__(
        self,
        ctx: ActionContext,
        policy: PolicyProvider,
        audit: AuditLog,
        consent: ConsentGateway | None = None,
    ) -> None:
        self.ctx = ctx
        self.policy = policy
        self.audit = audit
        self.consent: ConsentGateway = consent or DenyAllGateway()
        self._actions: dict[str, Action] = {}

    # --- registration -------------------------------------------------------

    def register(self, action: Action) -> "ActionRegistry":
        if not action.id:
            raise ValueError("action has no id")
        if action.id in self._actions:
            raise ValueError(f"duplicate action id: {action.id}")
        self._actions[action.id] = action
        return self

    def register_all(self, actions: Iterable[Action]) -> "ActionRegistry":
        for action in actions:
            self.register(action)
        return self

    def get(self, action_id: str) -> Action | None:
        return self._actions.get(action_id)

    def ids(self) -> list[str]:
        return list(self._actions)

    def __len__(self) -> int:
        return len(self._actions)

    # --- what we advertise to jarvis-core -----------------------------------

    def capabilities(self) -> list[str]:
        """Only capabilities that are actually usable right now."""
        return sorted(
            {
                a.capability
                for a in self._actions.values()
                if not a.unsupported and a.available(self.ctx)
            }
        )

    def manifest(self) -> list[dict[str, Any]]:
        """Full description of every action, for the server to turn into LLM
        tools. Unsupported ones are included and marked, so the model learns not
        to ask."""
        return [a.manifest_entry(self.ctx) for a in self._actions.values()]

    def tier_table(self) -> dict[str, str]:
        """``{action_id: TIER}`` — what ``--print-tiers`` shows the user."""
        return {a.id: a.tier.name for a in self._actions.values()}

    # --- dispatch -----------------------------------------------------------

    async def handle_command(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Run one ``device_command`` frame and return the ``device_result``.

        Provided so the WebSocket client never has to interpret the ``tier``
        field itself.
        """
        command_id = str(command.get("command_id") or "")
        action_id = str(command.get("action") or "")
        raw_params = command.get("params")
        params = dict(raw_params) if isinstance(raw_params, Mapping) else {}
        requested = ActionTier.from_wire(command.get("tier"))
        reason = command.get("reason")
        reason_text = reason if isinstance(reason, str) and reason.strip() else "(no reason given)"

        outcome = await self.dispatch(
            action_id,
            params,
            requested_tier=requested,
            reason=reason_text,
            command_id=command_id or None,
        )
        body = outcome.result.to_wire()
        return {"type": "device_result", "command_id": command_id, **body}

    async def dispatch(
        self,
        action_id: str,
        params: dict[str, Any],
        requested_tier: ActionTier | None = None,
        reason: str = "(no reason given)",
        command_id: str | None = None,
        trust: TrustLevel = TrustLevel.TRUSTED,
        source: str = "server",
    ) -> DispatchOutcome:
        """Run one action, subject to policy.

        :param requested_tier: the server's ``tier``, already parsed. Only raises.
        :param reason: the server's human-readable why. UNTRUSTED TEXT: it is
            displayed in the consent prompt and written to the audit log, and is
            never consulted for a decision.
        :param trust: ``UNTRUSTED`` for anything derived from page, notification,
            clipboard or screen content — such a request can never be
            auto-allowed.
        """
        started = time.monotonic()

        async def finish(
            result: ActionResult,
            tier: ActionTier,
            decision: Decision,
            note: str,
        ) -> DispatchOutcome:
            await self.audit.record_async(
                AuditEntry(
                    action_id=action_id,
                    tier=tier,
                    decision=decision,
                    status=_status_value(result),
                    ok=result.ok,
                    params=params,
                    error=result.error,
                    source=source,
                    command_id=command_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    note=note,
                )
            )
            return DispatchOutcome(result, tier, decision, note)

        action = self._actions.get(action_id)
        if action is None:
            # An action we have never heard of is CONFIRM, not "unknown": a typo
            # or an injected action name must not land in the auto-run bucket.
            return await finish(
                ActionResult.unsupported(f"unknown action: {action_id}"),
                ActionTier.CONFIRM,
                Decision.DENY,
                "not in the local action table",
            )

        # Honest "no" before any policy work, so these never prompt.
        if action.unsupported:
            return await finish(
                ActionResult.unsupported(
                    action.unsupported_reason or "not supported on this machine"
                ),
                action.tier,
                Decision.DENY,
                "action is declared unsupported",
            )
        if not action.available(self.ctx):
            return await finish(
                ActionResult.unsupported(
                    action.unavailable_reason(self.ctx)
                    or f"{action.id} is not available on this machine right now"
                ),
                action.tier,
                Decision.DENY,
                "action reported unavailable",
            )

        # LOCAL tier is the authority; tier_for() may only raise it further.
        local_tier = ActionTier.max_of(action.tier, self._safe_tier_for(action, params))
        effective = PolicyEngine.effective_tier(local_tier, requested_tier)
        request = PolicyRequest(
            action_id=action_id,
            local_tier=local_tier,
            requested_tier=requested_tier,
            user_policy=self.policy.policy_for(action_id),
            automation_enabled=self.policy.automation_enabled,
            panic=self.policy.panic,
            trust=trust,
        )
        decision = PolicyEngine.decide(request)
        explanation = PolicyEngine.explain(request, decision)

        if decision == Decision.DENY:
            return await finish(
                ActionResult.denied(PolicyEngine.deny_message(request)),
                effective,
                decision,
                explanation,
            )

        if decision == Decision.ASK:
            rememberable = PolicyEngine.can_remember(effective, trust)
            verdict = await self._ask_human(
                ApprovalRequest(
                    action_id=action_id,
                    description=action.description,
                    params=params,  # VERBATIM — the prompt must show the truth
                    tier=effective,
                    reason=reason,
                    command_id=command_id,
                    rememberable=rememberable,
                    timeout_s=self.ctx.config.consent_timeout_s,
                )
            )
            if not verdict.allows_execution:
                if verdict == ApprovalVerdict.TIMEOUT:
                    message = "no answer to the confirmation prompt"
                elif self.consent.unattended:
                    # Nobody was there to ask. Say so: "denied by the user" is a
                    # lie that sends whoever is debugging this looking for a
                    # person who never saw a prompt.
                    message = (
                        f"{action_id} needs a human to approve it and this machine "
                        "has no way to ask (no desktop session, no terminal, or "
                        "headless mode is on)"
                    )
                else:
                    message = "denied by the user"
                return await finish(
                    ActionResult.denied(message),
                    effective,
                    Decision.DENY,
                    f"{explanation}, approval={verdict.value}",
                )
            if verdict == ApprovalVerdict.APPROVED_ALWAYS and rememberable:
                try:
                    self.policy.remember(action_id, UserPolicy.ALLOW_ALWAYS, effective)
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("could not persist allow-always for %s", action_id)

            # A consent prompt can sit on screen for a minute, and the user may
            # spend that minute hitting panic, killing the master switch, or
            # blocking this action outright. Re-read the store and refuse if
            # anything now says no — an approval is consent to run, not a
            # licence that outlives the kill switch.
            fresh = replace(
                request,
                user_policy=self.policy.policy_for(action_id),
                automation_enabled=self.policy.automation_enabled,
                panic=self.policy.panic,
            )
            if PolicyEngine.decide(fresh) == Decision.DENY:
                return await finish(
                    ActionResult.denied(PolicyEngine.deny_message(fresh)),
                    effective,
                    Decision.DENY,
                    f"{explanation}, revoked while the prompt was up",
                )

        result = await self._execute(action, params)
        return await finish(result, effective, decision, explanation)

    # --- internals ----------------------------------------------------------

    async def _execute(self, action: Action, params: dict[str, Any]) -> ActionResult:
        try:
            if inspect.iscoroutinefunction(action.run):
                coro = action.run(self.ctx, params)
            else:
                coro = asyncio.to_thread(action.run, self.ctx, params)
            result = await asyncio.wait_for(coro, timeout=action.timeout_s)
        except asyncio.TimeoutError:
            return ActionResult.failed(
                f"{action.id} timed out after {action.timeout_s:g}s"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("action %s failed", action.id, exc_info=True)
            return ActionResult.failed(f"{type(exc).__name__}: {exc}")
        if not isinstance(result, ActionResult):
            return ActionResult.failed(
                f"{action.id} returned {type(result).__name__}, not an ActionResult"
            )
        return result

    async def _ask_human(self, request: ApprovalRequest) -> ApprovalVerdict:
        """No answer, a hung UI, or a crashed gateway all fail closed."""
        try:
            return await asyncio.wait_for(
                self.consent.request(request),
                timeout=request.timeout_s + _APPROVAL_GRACE_S,
            )
        except asyncio.TimeoutError:
            return ApprovalVerdict.TIMEOUT
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.warning("consent gateway failed for %s", request.action_id, exc_info=True)
            return ApprovalVerdict.TIMEOUT

    @staticmethod
    def _safe_tier_for(action: Action, params: Mapping[str, Any]) -> ActionTier:
        """A misbehaving action must not be able to lower its own tier by raising."""
        try:
            tier = action.tier_for(params)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("tier_for threw for %s; assuming CONFIRM", action.id, exc_info=True)
            return ActionTier.CONFIRM
        return tier if isinstance(tier, ActionTier) else ActionTier.CONFIRM


def _status_value(result: ActionResult) -> str:
    """The wire status of a result, even if an action mangled the field.

    A broken action must not be able to crash the dispatcher on its way to the
    audit log — the entry has to be written either way, and the channel's
    sanitiser turns anything unrecognised into ``error`` before it reaches the
    server.
    """
    status = result.status
    return status.value if isinstance(status, Status) else str(status)


def result_status(outcome: DispatchOutcome) -> Status:
    return outcome.result.status
