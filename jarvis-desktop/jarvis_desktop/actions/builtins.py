"""The LOCAL action table — the authority on what this machine can do and how
dangerous each of those things is.

The server's ``tier`` field can raise these numbers for a single command;
nothing can lower them. Adding an action is a two-line change here plus a row in
the README, so the table is deliberately in one place where it can be reviewed
as a whole.

The tiers here match the phone's table action for action where the two overlap
(``read_file`` AUTO, ``write_file`` NOTIFY, ``delete_file`` CONFIRM,
``read_clipboard``/``write_clipboard`` NOTIFY, ``http_request`` NOTIFY raising
to CONFIRM for non-GET, shell CONFIRM). That is the point: a user who learns
what Jarvis will and will not do without asking should not have to learn it
twice.
"""

from __future__ import annotations

from ..audit import AuditLog
from ..config import Config
from ..consent import ConsentGateway
from ..policy import ActionTier, PolicyProvider
from .apps import FocusWindow, LaunchApp, ListWindows, OpenUrl
from .base import Action, ActionContext
from .clipboard import ReadClipboard, WriteClipboard
from .files import DeleteFile, ListDir, ReadFile, WriteFile
from .inputauto import Click, MoveMouse, Screenshot, TypeText
from .net import HttpRequest
from .paths import PathScope
from .registry import ActionRegistry
from .shell import RunCommand
from .system import GetSystemState, LockScreen, Notify, SetVolume, Sleep

__all__ = ["all_actions", "build_context", "build_registry", "TIER_TABLE"]


def all_actions() -> list[Action]:
    """Every built-in action, in the order the manifest will list them."""
    return [
        # System
        GetSystemState(),
        SetVolume(),
        Notify(),
        LockScreen(),
        Sleep(),
        # Apps and windows
        LaunchApp(),
        OpenUrl(),
        ListWindows(),
        FocusWindow(),
        # Files
        ReadFile(),
        WriteFile(),
        ListDir(),
        DeleteFile(),
        # Clipboard
        ReadClipboard(),
        WriteClipboard(),
        # Network
        HttpRequest(),
        # Shell
        RunCommand(),
        # Input automation
        TypeText(),
        Click(),
        MoveMouse(),
        Screenshot(),
    ]


#: The table again, flat, as a review artefact and a test fixture. If this and
#: :func:`all_actions` ever disagree, ``tests/test_actions.py`` fails.
TIER_TABLE: dict[str, ActionTier] = {
    "get_system_state": ActionTier.AUTO,
    "set_volume": ActionTier.AUTO,
    "notify": ActionTier.AUTO,
    "lock_screen": ActionTier.CONFIRM,
    "sleep": ActionTier.CONFIRM,
    "launch_app": ActionTier.AUTO,
    "open_url": ActionTier.AUTO,
    "list_windows": ActionTier.AUTO,
    "focus_window": ActionTier.NOTIFY,
    "read_file": ActionTier.AUTO,
    "write_file": ActionTier.NOTIFY,
    "list_dir": ActionTier.AUTO,
    "delete_file": ActionTier.CONFIRM,
    "read_clipboard": ActionTier.NOTIFY,
    "write_clipboard": ActionTier.NOTIFY,
    "http_request": ActionTier.NOTIFY,
    "run_command": ActionTier.CONFIRM,
    "type_text": ActionTier.CONFIRM,
    "click": ActionTier.CONFIRM,
    "move_mouse": ActionTier.CONFIRM,
    "screenshot": ActionTier.NOTIFY,
}


def build_context(config: Config) -> ActionContext:
    scope = PathScope(config.file_roots or (config.workspace,))
    scope.ensure_roots()
    allowed_hosts = tuple(h for h in (config.server_host,) if h)
    return ActionContext(config=config, scope=scope, allowed_hosts=allowed_hosts)


def build_registry(
    config: Config,
    policy: PolicyProvider,
    audit: AuditLog,
    consent: ConsentGateway | None = None,
    ctx: ActionContext | None = None,
) -> ActionRegistry:
    """Wire the whole action layer together."""
    registry = ActionRegistry(
        ctx or build_context(config), policy=policy, audit=audit, consent=consent
    )
    registry.register_all(all_actions())
    return registry
