"""Synthetic keyboard and mouse, via pyautogui when it is installed.

This is the most sensitive capability the agent has after ``run_command``:
whatever is focused receives the keystrokes, and the agent cannot see what that
is. A "type the summary into my notes" that lands in a banking tab types into a
banking tab. So:

* ``type_text``, ``click`` and ``move_mouse`` are Tier 3 — a fresh human
  approval every single time, showing the verbatim text and coordinates.
* The whole module is **off by default** (``input_automation.enabled``) on top
  of that, so a machine that never needs it cannot be talked into it.
* ``pyautogui`` is never a hard dependency. Without it every action here reports
  ``unsupported`` with an install hint.

``screenshot`` is Tier 2, matching the shared tier table (the phone rates it
Tier 2 as well): it captures rather than acts. Its output is untrusted data —
screen text is exactly the kind of content an injected page controls — and it is
written into the workspace so the model gets a path rather than a megabyte of
base64.
"""

from __future__ import annotations

import time
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult
from .paths import ScopeError, safe_join

__all__ = ["TypeText", "Click", "MoveMouse", "Screenshot"]

_INSTALL_HINT = (
    "input automation needs pyautogui: pip install 'jarvis-desktop[input]' "
    "(or pip install pyautogui). On Linux it also needs an X11/XWayland session."
)

MAX_TYPE_CHARS = 4000


def _pyautogui() -> Any | None:
    try:
        import pyautogui  # type: ignore

        return pyautogui
    except Exception:  # noqa: BLE001 - it raises on a headless display too
        return None


class _InputAction(Action):
    capability = "ui_automation"
    timeout_s = 60.0

    def available(self, ctx: ActionContext) -> bool:
        return bool(ctx.config.input_automation.enabled) and _pyautogui() is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if not ctx.config.input_automation.enabled:
            return (
                "input automation is disabled on this machine; set "
                '"input_automation": {"enabled": true} in the jarvis-desktop config'
            )
        return _INSTALL_HINT

    def _gui(self, ctx: ActionContext) -> Any | None:
        if not ctx.config.input_automation.enabled:
            return None
        gui = _pyautogui()
        if gui is not None:
            # A runaway loop can be stopped by slamming the pointer into a
            # corner. Costs nothing and has saved people before.
            try:
                gui.FAILSAFE = True
                gui.PAUSE = 0.02
            except Exception:  # noqa: BLE001
                pass
        return gui


class TypeText(Action):
    id = "type_text"
    tier = ActionTier.CONFIRM
    description = "Type text on this machine's keyboard, into whatever is focused."
    params_schema = {
        "text": "string: the exact text to type",
        "interval_s": "number (optional): delay between keystrokes, default 0.01",
        "press_enter": "bool (optional): press Enter afterwards",
    }
    capability = "ui_automation"
    timeout_s = 120.0

    available = _InputAction.available
    unavailable_reason = _InputAction.unavailable_reason
    _gui = _InputAction._gui

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        text = params.get("text")
        if not isinstance(text, str) or not text:
            return ActionResult.failed("text is required")
        if len(text) > MAX_TYPE_CHARS:
            return ActionResult.failed(
                f"text is {len(text)} characters; the limit is {MAX_TYPE_CHARS}"
            )
        interval = max(0.0, min(self.float_param(params, "interval_s", 0.01), 1.0))
        try:
            gui.write(text, interval=interval)
            if self.bool_param(params, "press_enter"):
                gui.press("enter")
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"typing failed: {exc}")
        return ActionResult.success(
            characters=len(text), pressed_enter=self.bool_param(params, "press_enter")
        )


class Click(Action):
    id = "click"
    tier = ActionTier.CONFIRM
    description = "Click the mouse at a screen position on this machine."
    params_schema = {
        "x": "int: screen x, or omit to click where the pointer already is",
        "y": "int: screen y",
        "button": "string (optional): left | right | middle (default left)",
        "clicks": "int (optional): 1 or 2 (default 1)",
    }
    capability = "ui_automation"
    timeout_s = 60.0

    available = _InputAction.available
    unavailable_reason = _InputAction.unavailable_reason
    _gui = _InputAction._gui

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        button = (self.str_param(params, "button") or "left").lower()
        if button not in ("left", "right", "middle"):
            return ActionResult.failed("button must be left, right or middle")
        clicks = max(1, min(self.int_param(params, "clicks", 1), 2))
        x = params.get("x")
        y = params.get("y")
        try:
            width, height = gui.size()
            if x is None or y is None:
                gui.click(button=button, clicks=clicks)
                position = None
            else:
                px, py = int(x), int(y)
                if not (0 <= px < width and 0 <= py < height):
                    return ActionResult.failed(
                        f"({px}, {py}) is off screen; this display is {width}x{height}"
                    )
                gui.click(x=px, y=py, button=button, clicks=clicks)
                position = [px, py]
        except (TypeError, ValueError):
            return ActionResult.failed("x and y must be integers")
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"click failed: {exc}")
        return ActionResult.success(position=position, button=button, clicks=clicks)


class MoveMouse(Action):
    id = "move_mouse"
    # Tier 3 with the rest: moving the pointer is the setup move for a click,
    # and a pointer that jumps under someone's hand is how a click lands
    # somewhere other than where they were looking.
    tier = ActionTier.CONFIRM
    description = "Move the mouse pointer to a screen position on this machine."
    params_schema = {
        "x": "int: screen x",
        "y": "int: screen y",
        "duration_s": "number (optional): glide time, default 0",
    }
    capability = "ui_automation"
    timeout_s = 60.0

    available = _InputAction.available
    unavailable_reason = _InputAction.unavailable_reason
    _gui = _InputAction._gui

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)
        try:
            x, y = int(params["x"]), int(params["y"])
        except (KeyError, TypeError, ValueError):
            return ActionResult.failed("x and y are required integers")
        duration = max(0.0, min(self.float_param(params, "duration_s", 0.0), 5.0))
        try:
            width, height = gui.size()
            if not (0 <= x < width and 0 <= y < height):
                return ActionResult.failed(
                    f"({x}, {y}) is off screen; this display is {width}x{height}"
                )
            gui.moveTo(x, y, duration=duration)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"pointer move failed: {exc}")
        return ActionResult.success(position=[x, y])


class Screenshot(Action):
    id = "screenshot"
    # Tier 2 per the shared tier table: it captures, it does not act. The image
    # still lands only in the workspace and its contents are untrusted.
    tier = ActionTier.NOTIFY
    description = "Capture the screen of this machine and save it in the workspace."
    params_schema = {
        "filename": "string (optional): name for the PNG, default a timestamp",
        "region": "array [x, y, width, height] (optional): capture part of the screen",
    }
    capability = "ui_automation"
    timeout_s = 60.0

    available = _InputAction.available
    unavailable_reason = _InputAction.unavailable_reason
    _gui = _InputAction._gui

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        gui = self._gui(ctx)
        if gui is None:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or _INSTALL_HINT)

        name = self.str_param(params, "filename") or f"screenshot-{int(time.time())}.png"
        if not name.lower().endswith(".png"):
            name += ".png"
        target_dir = ctx.config.input_automation.screenshot_dir or "screenshots"
        try:
            base = ctx.scope.resolve(target_dir, allow_root=True)
            if not base.allowed or base.path is None:
                return ActionResult.failed(f"screenshot directory rejected: {base.reason}")
            base.path.mkdir(parents=True, exist_ok=True)
            destination = safe_join(base.path, name)
        except ScopeError as exc:
            return ActionResult.failed(f"filename rejected: {exc}")
        except OSError as exc:
            return ActionResult.failed(f"could not prepare the screenshot directory: {exc}")

        region = params.get("region")
        kwargs: dict[str, Any] = {}
        if isinstance(region, (list, tuple)) and len(region) == 4:
            try:
                kwargs["region"] = tuple(int(v) for v in region)
            except (TypeError, ValueError):
                return ActionResult.failed("region must be four integers [x, y, width, height]")

        try:
            image = gui.screenshot(**kwargs)
            image.save(str(destination))
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"screenshot failed: {exc}")

        relative = ctx.scope.resolve(str(destination))
        # Whatever is on screen was drawn by other programs, so the capture is
        # untrusted data — including any text an OCR step later pulls out of it.
        return ActionResult.untrusted(
            {
                "path": relative.relative if relative.allowed else str(destination),
                "absolute_path": str(destination),
                "bytes": destination.stat().st_size if destination.exists() else 0,
            }
        )
