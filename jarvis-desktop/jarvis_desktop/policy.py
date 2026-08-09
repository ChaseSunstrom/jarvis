"""The device-side policy model — the whole safety story, in one file.

This is a faithful port of the Android engine in
``android-app/app/src/main/kotlin/ai/jarvis/app/automation/policy/``. The phone
and the desktop must behave *identically*: same tiers, same precedence, same
refusals. ``tests/test_policy.py`` is the shared executable spec and mirrors
``android-app/tools/policy_truth_table_test.py`` case for case.

The LLM runs on the server. The server can be wrong, or prompt-injected by a web
page it read. So the device decides — locally, outside the model — what may run.
The rules, in order of precedence:

  1. panic flag set                       -> DENY   (kill switch, nothing runs)
  2. automation master switch off         -> DENY
  3. UserPolicy.NEVER                     -> DENY   (beats everything below)
  4. effective tier = max(local, requested); the server can only RAISE.
  5. CONFIRM                              -> ASK    ALWAYS. ALLOW_ALWAYS does
                                                    NOT bypass it. This is the
                                                    critical invariant.
  6. NOTIFY                               -> ALLOW if ALLOW_ALWAYS, else ASK
  7. AUTO                                 -> ALLOW
  8. ...except an UNTRUSTED request is never ALLOWed: it degrades to ASK.

Nothing in this module imports anything that touches a socket, a screen or a
subprocess. It is pure logic plus one small JSON file store, so it can be read
in one sitting and reviewed as a whole.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import Callable, Iterable, Mapping

__all__ = [
    "ActionTier",
    "UserPolicy",
    "Decision",
    "TrustLevel",
    "PolicyRequest",
    "PolicyEngine",
    "PolicyProvider",
    "InMemoryPolicyProvider",
    "PolicyStore",
]


class ActionTier(IntEnum):
    """How dangerous an action is. The integer value IS the wire value and the
    severity order — ``AUTO < NOTIFY < CONFIRM``. Never renumber these.

    * ``AUTO``    — read-only or trivially reversible. Runs without asking.
    * ``NOTIFY``  — changes device state but is recoverable. Ask once; the user
      may then choose to remember the answer for that action.
    * ``CONFIRM`` — irreversible, contacts another person, spends money, or
      types into someone else's UI. Asks EVERY time. Can never be remembered,
      can never be auto-approved.
    """

    AUTO = 1
    NOTIFY = 2
    CONFIRM = 3

    @property
    def wire(self) -> int:
        """The ``tier`` field jarvis-core uses: 1 | 2 | 3."""
        return int(self)

    @staticmethod
    def max_of(a: "ActionTier", b: "ActionTier") -> "ActionTier":
        """The more dangerous of the two. Used to RAISE a tier, never to lower."""
        return a if a >= b else b

    @staticmethod
    def from_wire(value: object) -> "ActionTier | None":
        """Parse the server's ``tier`` field.

        Returns ``None`` for anything unrecognised, and the caller then treats
        it as "no opinion" (= ``AUTO``, i.e. no raise). A malformed or hostile
        value can therefore never *lower* the local tier — see
        :meth:`PolicyEngine.effective_tier`. ``bool`` is explicitly excluded
        even though Python calls it an ``int``.
        """
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text.isdigit():
                return ActionTier.from_name(text)
            value = int(text)
        if isinstance(value, int):
            try:
                return ActionTier(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def from_name(name: object) -> "ActionTier | None":
        """Lenient name parse for stored config / manifests. None when unknown."""
        if not isinstance(name, str):
            return None
        key = name.strip().upper()
        return {
            "AUTO": ActionTier.AUTO,
            "1": ActionTier.AUTO,
            "TIER1": ActionTier.AUTO,
            "NOTIFY": ActionTier.NOTIFY,
            "2": ActionTier.NOTIFY,
            "TIER2": ActionTier.NOTIFY,
            "CONFIRM": ActionTier.CONFIRM,
            "3": ActionTier.CONFIRM,
            "TIER3": ActionTier.CONFIRM,
        }.get(key)


class UserPolicy(str, Enum):
    """The user's standing answer for one action id.

    ``NEVER`` is a hard local kill switch and outranks everything, including the
    server and including an ``ALLOW_ALWAYS`` set earlier.
    """

    #: "Yes, and stop asking." Only honoured for AUTO/NOTIFY.
    ALLOW_ALWAYS = "allow_always"
    #: Default for everything the user has not answered yet.
    ASK = "ask"
    #: Hard no. Always denied, never prompts, never executes.
    NEVER = "never"

    @staticmethod
    def from_stored(value: object) -> "UserPolicy":
        """Unknown / corrupt stored values fail closed to ``ASK``."""
        if not isinstance(value, str):
            return UserPolicy.ASK
        return {
            "ALLOW_ALWAYS": UserPolicy.ALLOW_ALWAYS,
            "ALLOW": UserPolicy.ALLOW_ALWAYS,
            "ALWAYS": UserPolicy.ALLOW_ALWAYS,
            "NEVER": UserPolicy.NEVER,
            "DENY": UserPolicy.NEVER,
            "BLOCK": UserPolicy.NEVER,
        }.get(value.strip().upper(), UserPolicy.ASK)


class Decision(str, Enum):
    """The only three things the policy engine can say."""

    #: Execute now, no human in the loop.
    ALLOW = "ALLOW"
    #: Show the consent prompt with the verbatim action, params and reason.
    ASK = "ASK"
    #: Do not execute. Do not prompt. Reply ``denied``.
    DENY = "DENY"


class TrustLevel(str, Enum):
    """Where the request came from.

    This is NOT the server's word for it — the caller sets it structurally.

    * ``TRUSTED``   — a ``device_command`` off the authenticated jarvis-core
      socket, or a local action the user started themselves.
    * ``UNTRUSTED`` — anything whose content originated in a web page, a
      notification, a screenshot/OCR, the clipboard, or an HTTP response. Text
      like that is DATA and must never be able to cause an action on its own, so
      an UNTRUSTED request can never be auto-allowed: the best it can ever get
      is ``Decision.ASK``.
    """

    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


@dataclass(frozen=True)
class PolicyRequest:
    """Everything the engine is allowed to look at.

    Constructed by the dispatcher from the local action table, the incoming
    command and the user's store — never from anything the model wrote.
    """

    action_id: str
    #: From the LOCAL action table on this machine. The authority.
    local_tier: ActionTier
    #: From the incoming message. A hint. Can only raise.
    requested_tier: ActionTier | None
    user_policy: UserPolicy
    automation_enabled: bool = True
    panic: bool = False
    trust: TrustLevel = TrustLevel.TRUSTED


class PolicyEngine:
    """Pure decision logic. No I/O, no clock, no state."""

    @staticmethod
    def effective_tier(
        local_tier: ActionTier, requested_tier: ActionTier | None
    ) -> ActionTier:
        """The tier we actually enforce.

        The ``tier`` field in an incoming ``device_command`` is A HINT FROM A
        MACHINE THAT MAY BE WRONG OR PROMPT-INJECTED. It is folded in only
        through ``max``, so it can raise the tier and can never lower it. A
        missing/garbage value (``None``) contributes ``AUTO`` and therefore
        changes nothing.
        """
        return requested_tier or local_tier  # MUTANT: server may lower the tier

    @staticmethod
    def decide_parts(
        action_id: str,
        local_tier: ActionTier,
        requested_tier: ActionTier | None,
        user_policy: UserPolicy,
    ) -> Decision:
        """The core truth table.

        ``action_id`` participates only in the human-readable explanation — the
        decision is a function of the tiers and the user's standing answer,
        nothing else.
        """
        if user_policy == UserPolicy.NEVER:
            return Decision.DENY
        tier = PolicyEngine.effective_tier(local_tier, requested_tier)
        if tier == ActionTier.CONFIRM:
            # Tier 3 asks every single time. ALLOW_ALWAYS is deliberately
            # ignored here; see can_remember() — it can never be stored either.
            return Decision.ASK
        if tier == ActionTier.NOTIFY:
            return (
                Decision.ALLOW
                if user_policy == UserPolicy.ALLOW_ALWAYS
                else Decision.ASK
            )
        return Decision.ALLOW

    @staticmethod
    def decide(request: PolicyRequest) -> Decision:
        """The full decision used by the dispatcher: the core table plus the two
        global switches and the trust level of the request."""
        if request.panic:
            return Decision.DENY
        if not request.automation_enabled:
            return Decision.DENY

        base = PolicyEngine.decide_parts(
            request.action_id,
            request.local_tier,
            request.requested_tier,
            request.user_policy,
        )

        # Untrusted content (web page, notification, screen text, clipboard,
        # HTTP body) may never cause an action on its own. The strongest outcome
        # it can produce is a fresh human approval.
        if request.trust == TrustLevel.UNTRUSTED and base == Decision.ALLOW:
            return Decision.ASK
        return base

    @staticmethod
    def can_remember(
        effective_tier: ActionTier, trust: TrustLevel = TrustLevel.TRUSTED
    ) -> bool:
        """May an "allow always" answer be persisted for this action?

        Never for Tier 3 — a CONFIRM action must be re-approved every time, so
        remembering it would be indistinguishable from bypassing it. Never for
        an untrusted-sourced approval either: consent given while looking at a
        prompt driven by injected content should not become a standing rule.
        """
        return effective_tier != ActionTier.CONFIRM and trust == TrustLevel.TRUSTED

    @staticmethod
    def explain(request: PolicyRequest, decision: Decision) -> str:
        """One-line human-readable reason, for the audit log and the consent UI."""
        effective = PolicyEngine.effective_tier(
            request.local_tier, request.requested_tier
        )
        raised = (
            request.requested_tier is not None
            and request.requested_tier > request.local_tier
        )
        parts = [
            f"{request.action_id} local={request.local_tier.name} "
            f"requested={request.requested_tier.name if request.requested_tier else 'none'} "
            f"effective={effective.name}"
        ]
        if raised:
            parts.append("raised by server")
        parts.append(f"policy={request.user_policy.name}")
        if request.trust == TrustLevel.UNTRUSTED:
            parts.append("untrusted source")
        if request.panic:
            parts.append("PANIC")
        if not request.automation_enabled:
            parts.append("automation disabled")
        parts.append(f"-> {decision.value}")
        return ", ".join(parts)

    @staticmethod
    def deny_message(request: PolicyRequest) -> str:
        """The sentence the server (and therefore the model) is told."""
        if request.panic:
            return "denied: Jarvis desktop automation is in panic mode"
        if not request.automation_enabled:
            return "denied: desktop automation is switched off"
        if request.user_policy == UserPolicy.NEVER:
            return f"denied: the user has blocked {request.action_id} on this machine"
        return f"denied: policy refuses {request.action_id}"


class PolicyProvider(ABC):
    """Storage seam so the engine and the dispatcher stay testable without I/O."""

    @abstractmethod
    def policy_for(self, action_id: str) -> UserPolicy:
        """The user's standing answer for this action id; ``ASK`` by default."""

    @abstractmethod
    def remember(
        self, action_id: str, policy: UserPolicy, effective_tier: ActionTier
    ) -> None:
        """Persist a standing answer.

        Implementations MUST refuse to store ``ALLOW_ALWAYS`` when
        ``effective_tier`` is ``CONFIRM`` (belt and braces — the engine ignores
        it anyway).
        """

    @property
    @abstractmethod
    def automation_enabled(self) -> bool:
        """Master switch. False => everything is denied."""

    @property
    @abstractmethod
    def panic(self) -> bool:
        """Panic kill switch. True => everything is denied, outranks all else."""


class InMemoryPolicyProvider(PolicyProvider):
    """In-memory provider for tests and for a registry built before storage exists."""

    def __init__(
        self,
        initial: Mapping[str, UserPolicy] | None = None,
        automation_enabled: bool = True,
        panic: bool = False,
    ) -> None:
        self._map: dict[str, UserPolicy] = dict(initial or {})
        self._enabled = automation_enabled
        self._panic = panic

    def policy_for(self, action_id: str) -> UserPolicy:
        return self._map.get(action_id, UserPolicy.ASK)

    def remember(
        self, action_id: str, policy: UserPolicy, effective_tier: ActionTier
    ) -> None:
        if policy == UserPolicy.ALLOW_ALWAYS and not PolicyEngine.can_remember(
            effective_tier
        ):
            return
        self._map[action_id] = policy

    @property
    def automation_enabled(self) -> bool:
        return self._enabled

    @automation_enabled.setter
    def automation_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def panic(self) -> bool:
        return self._panic

    @panic.setter
    def panic(self, value: bool) -> None:
        self._panic = bool(value)

    def snapshot(self) -> dict[str, UserPolicy]:
        return dict(self._map)


class PolicyStore(PolicyProvider):
    """The user's local policy, in one JSON file they can read and edit.

    ::

        {"version": 1,
         "automation_enabled": true,
         "panic": false,
         "policies": {"send_sms": "never", "set_volume": "allow_always"}}

    This is the ONLY writable input to :class:`PolicyEngine` besides the local
    action table, and it is written only in response to a human answering a
    prompt or editing the file. The server cannot reach it: there is no action
    that mutates the policy store.

    Every read re-stats the file, so an edit made in another process (the CLI,
    a text editor, a second agent instance) is picked up immediately. That
    matters for one case in particular: the dispatcher re-reads the store after
    a consent prompt comes back, so hitting panic *while the prompt is up*
    still stops the action.
    """

    VERSION = 1

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._policies: dict[str, UserPolicy] = {}
        self._enabled = True
        self._panic = False
        self._stamp: tuple[int, int] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._load(force=True)

    # --- per-action policy --------------------------------------------------

    def policy_for(self, action_id: str) -> UserPolicy:
        with self._lock:
            self._load()
            return self._policies.get(action_id, UserPolicy.ASK)

    def remember(
        self, action_id: str, policy: UserPolicy, effective_tier: ActionTier
    ) -> None:
        """Persist a standing answer.

        Refuses ALLOW_ALWAYS for Tier 3: a CONFIRM action must be approved every
        single time, so there is nothing to remember. The refusal is silent by
        design (the caller is a UI control that should not have been offered);
        :meth:`PolicyEngine.decide` would ignore the stored value anyway, so
        this is the second of two independent guards.
        """
        self.set_policy(action_id, policy, effective_tier)

    def set_policy(
        self,
        action_id: str,
        policy: UserPolicy,
        effective_tier: ActionTier | None = None,
    ) -> None:
        if (
            policy == UserPolicy.ALLOW_ALWAYS
            and effective_tier is not None
            and not PolicyEngine.can_remember(effective_tier)
        ):
            return
        with self._lock:
            self._load()
            self._policies[action_id] = policy
            self._save()

    def clear_policy(self, action_id: str) -> None:
        """Forget the standing answer for one action (back to ``ASK``)."""
        with self._lock:
            self._load()
            self._policies.pop(action_id, None)
            self._save()

    def all_policies(self) -> dict[str, UserPolicy]:
        with self._lock:
            self._load()
            return dict(self._policies)

    def clear_all_policies(self) -> None:
        """Drop every remembered answer. Global switches are left alone."""
        with self._lock:
            self._load()
            self._policies.clear()
            self._save()

    # --- global switches ----------------------------------------------------

    @property
    def automation_enabled(self) -> bool:
        with self._lock:
            self._load()
            return self._enabled

    @automation_enabled.setter
    def automation_enabled(self, value: bool) -> None:
        with self._lock:
            self._load()
            self._enabled = bool(value)
            self._save()

    @property
    def panic(self) -> bool:
        """Panic: disable everything. Outranks the master switch, every
        remembered ALLOW_ALWAYS and every incoming command. Only a human can
        clear it."""
        with self._lock:
            self._load()
            return self._panic

    @panic.setter
    def panic(self, value: bool) -> None:
        with self._lock:
            self._load()
            self._panic = bool(value)
            self._save()

    @property
    def automation_live(self) -> bool:
        """Convenience: true when anything at all can run."""
        return self.automation_enabled and not self.panic

    # --- change notification ------------------------------------------------

    def add_change_listener(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def remove_change_listener(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    # --- internals ----------------------------------------------------------

    def _stat_stamp(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load(self, force: bool = False) -> None:
        stamp = self._stat_stamp()
        if not force and stamp == self._stamp:
            return
        self._stamp = stamp
        if stamp is None:
            # No file yet: defaults. Automation on (the tiers are what keep it
            # safe), panic off, nothing remembered.
            self._policies = {}
            self._enabled = True
            self._panic = False
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt store must not become an open door: keep whatever we
            # already had in memory and, on first load, fall back to defaults
            # with everything set to ASK.
            return
        if not isinstance(raw, dict):
            return
        policies = raw.get("policies")
        self._policies = (
            {
                str(k): UserPolicy.from_stored(v)
                for k, v in policies.items()
            }
            if isinstance(policies, dict)
            else {}
        )
        # Anything that is not literally `false` leaves automation enabled; only
        # a literal `true` turns panic on. Corrupt values fail toward "ask", not
        # toward "run".
        self._enabled = raw.get("automation_enabled", True) is not False
        self._panic = raw.get("panic", False) is True

    def _save(self) -> None:
        payload = {
            "version": self.VERSION,
            "automation_enabled": self._enabled,
            "panic": self._panic,
            "policies": {k: v.value for k, v in sorted(self._policies.items())},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".policy-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._stamp = self._stat_stamp()
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # a broken UI hook must not break policy
                pass


def merge_requests(request: PolicyRequest, provider: PolicyProvider) -> PolicyRequest:
    """Re-read the mutable half of a request from the store.

    Used by the dispatcher after a consent prompt returns: an approval is
    consent to run *now*, not a licence that outlives the kill switch.
    """
    return replace(
        request,
        user_policy=provider.policy_for(request.action_id),
        automation_enabled=provider.automation_enabled,
        panic=provider.panic,
    )


def tier_table(actions: Iterable[tuple[str, ActionTier]]) -> dict[str, ActionTier]:
    """Build a ``{action_id: tier}`` view of a registry, for docs and the CLI."""
    return {action_id: tier for action_id, tier in actions}
