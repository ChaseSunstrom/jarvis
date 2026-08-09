"""What an action is, and what it may return.

Actions are small, single-purpose and stateless. They may assume policy has
already been satisfied — by the time :meth:`Action.run` is called the dispatcher
has consulted the local tier table, the user's policy store and, where required,
a human. They may NOT assume the machine can do the thing: every action
re-checks its own preconditions and returns an ``unsupported``/``error`` result
rather than raising.

Mirrors ``android-app/.../automation/actions/JarvisAction.kt``.
"""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TYPE_CHECKING

from ..policy import ActionTier

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Config
    from .paths import PathScope

__all__ = ["Status", "ActionResult", "ActionContext", "Action"]


class Status(str, Enum):
    """The four wire statuses. There is no fifth, and no "partial"."""

    OK = "ok"
    DENIED = "denied"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


@dataclass
class ActionResult:
    """The outcome of one action.

    ``status`` carries straight onto the wire as ``device_result.status``.
    """

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    status: Status = Status.OK

    def __post_init__(self) -> None:
        if not self.ok and self.status == Status.OK:
            self.status = Status.ERROR

    def to_wire(self) -> dict[str, Any]:
        """Ready-to-send ``device_result`` body (minus ``type``/``command_id``)."""
        status = self.status.value if isinstance(self.status, Status) else str(self.status)
        out: dict[str, Any] = {"status": status}
        if self.data is not None:
            out["result"] = self.data
        if self.error:
            out["error"] = self.error
        return out

    # --- constructors -------------------------------------------------------

    @staticmethod
    def success(data: dict[str, Any] | None = None, **kwargs: Any) -> "ActionResult":
        payload = dict(data or {})
        payload.update(kwargs)
        return ActionResult(True, payload, None, Status.OK)

    @staticmethod
    def failed(message: str) -> "ActionResult":
        return ActionResult(False, None, message, Status.ERROR)

    @staticmethod
    def denied(message: str) -> "ActionResult":
        return ActionResult(False, None, message, Status.DENIED)

    @staticmethod
    def unsupported(message: str) -> "ActionResult":
        return ActionResult(False, None, message, Status.UNSUPPORTED)

    @staticmethod
    def untrusted(data: dict[str, Any] | None = None, **kwargs: Any) -> "ActionResult":
        """Success carrying content this machine did not author.

        Page bodies, clipboard contents, screen text and command output are all
        DATA. The flag rides to the server so it knows which of its inputs were
        written by a stranger before it hands them to a model — and so nothing
        downstream mistakes them for instructions.
        """
        result = ActionResult.success(data, **kwargs)
        assert result.data is not None
        result.data["_untrusted"] = True
        return result


@dataclass
class ActionContext:
    """Everything an action is allowed to reach.

    Handed in by the registry. Actions never import :mod:`jarvis_desktop.config`
    or read process globals, so a test can build a context over a tmp_path and
    exercise the real code.
    """

    config: "Config"
    scope: "PathScope"
    #: Hosts exempt from the SSRF private-range block (the jarvis-core server).
    allowed_hosts: tuple[str, ...] = ()
    #: Overridable in tests so no action ever shells out for real.
    runner: Any = None

    @property
    def system(self) -> str:
        return platform.system()  # "Linux" | "Darwin" | "Windows"

    def which(self, *names: str) -> str | None:
        """First of ``names`` present on PATH, or None."""
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        return None


class Action:
    """One thing Jarvis can do to this machine."""

    #: Stable id used on the wire and as the key in the user's policy store.
    id: str = ""

    #: The LOCAL tier. This is the authority — the ``tier`` field of an incoming
    #: ``device_command`` can raise the enforced tier but never lower it.
    tier: ActionTier = ActionTier.CONFIRM

    #: One line, written for the LLM tool description and the consent prompt.
    description: str = ""

    # param name -> human/LLM-readable type + meaning. Shipped in the manifest.
    # A plain class attribute, not a dataclass field: Action is not a dataclass,
    # so `field(default_factory=dict)` here would leave every subclass that
    # forgot to override it holding a `Field` object instead of a mapping.
    params_schema: Mapping[str, str] = {}

    #: Coarse capability bucket advertised in ``jarvis/device/register``.
    capability: str = "system"

    #: Hard cap on execution, enforced by the dispatcher.
    timeout_s: float = 15.0

    #: True for actions that exist only so the server gets an honest "no".
    #: The dispatcher short-circuits them BEFORE policy, so they never prompt.
    unsupported: bool = False

    #: Why this cannot run — used both when :attr:`unsupported` is true and when
    #: :meth:`available` returns false, so the model gets an actionable sentence
    #: ("pip install pyautogui") instead of a shrug.
    unsupported_reason: str | None = None

    def tier_for(self, params: Mapping[str, Any]) -> ActionTier:
        """Per-invocation tier bump.

        Returning a HIGHER tier for dangerous parameters is allowed and
        honoured; returning a lower one is ignored (the dispatcher takes
        ``max(tier, tier_for(params))``), so this can only ever make things
        stricter.
        """
        return self.tier

    def available(self, ctx: ActionContext) -> bool:
        """True when this action can run on this machine right now."""
        return True

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        """Sentence shown when :meth:`available` is false."""
        return self.unsupported_reason

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        """Do the thing.

        Runs in a worker thread inside a timeout unless it is declared ``async``
        (the dispatcher awaits those directly). Must not raise for expected
        failures — return an error result instead.
        """
        raise NotImplementedError

    # --- param helpers ------------------------------------------------------

    @staticmethod
    def str_param(params: Mapping[str, Any], key: str, default: str | None = None) -> str | None:
        value = params.get(key, default)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        return None

    @staticmethod
    def int_param(params: Mapping[str, Any], key: str, default: int) -> int:
        value = params.get(key, default)
        try:
            if isinstance(value, bool):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def float_param(params: Mapping[str, Any], key: str, default: float) -> float:
        value = params.get(key, default)
        try:
            if isinstance(value, bool):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def bool_param(params: Mapping[str, Any], key: str, default: bool = False) -> bool:
        value = params.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return default

    def manifest_entry(self, ctx: ActionContext) -> dict[str, Any]:
        available = not self.unsupported and self.available(ctx)
        entry: dict[str, Any] = {
            "id": self.id,
            "tier": self.tier.wire,
            "tier_name": self.tier.name,
            "description": self.description,
            "params": dict(self.params_schema),
            "capability": self.capability,
            "available": available,
            "requires_confirmation": self.tier == ActionTier.CONFIRM,
        }
        if not available:
            entry["unsupported"] = True
            reason = self.unavailable_reason(ctx) or self.unsupported_reason
            if reason:
                entry["unsupported_reason"] = reason
        return entry
