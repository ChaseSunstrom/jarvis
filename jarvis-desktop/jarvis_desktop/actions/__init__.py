"""Actions: what the desktop agent can be asked to do, and the door they come through."""

from .base import Action, ActionContext, ActionResult, Status
from .builtins import TIER_TABLE, all_actions, build_context, build_registry
from .paths import PathScope, PathResult, ScopeError
from .registry import ActionRegistry, DispatchOutcome
from .shell import ShellGuard, scrub_env

__all__ = [
    "Action",
    "ActionContext",
    "ActionResult",
    "ActionRegistry",
    "DispatchOutcome",
    "PathScope",
    "PathResult",
    "ScopeError",
    "ShellGuard",
    "Status",
    "TIER_TABLE",
    "all_actions",
    "build_context",
    "build_registry",
    "scrub_env",
]
