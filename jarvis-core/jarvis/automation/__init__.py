"""The Jarvis automation engine.

Three layers, deliberately Home Assistant shaped so existing YAML ports over:

* :mod:`.triggers`    — trigger platforms (``async_attach`` -> unsubscribe)
* :mod:`.conditions`  — ``async_check(jarvis, config, variables) -> bool``
* :mod:`.actions`     — the script executor (``async_execute_script``)
* :mod:`.engine`      — :class:`Automation` objects + :class:`AutomationManager`

Nothing here imports an integration; the integrations under
``jarvis/integrations/{automation,script,scene,input_helpers}`` are thin YAML
adapters on top of these modules.
"""

from __future__ import annotations

from .actions import ScriptError, StopScript, async_execute_script
from .conditions import async_check, async_check_all
from .engine import Automation, AutomationManager, ModeController
from .triggers import async_attach_trigger, async_attach_triggers
from .util import parse_duration, render_bool, render_complex

__all__ = [
    "Automation",
    "AutomationManager",
    "ModeController",
    "ScriptError",
    "StopScript",
    "async_attach_trigger",
    "async_attach_triggers",
    "async_check",
    "async_check_all",
    "async_execute_script",
    "parse_duration",
    "render_bool",
    "render_complex",
]
