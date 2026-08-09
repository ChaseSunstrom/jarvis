"""Clipboard read/write.

Both are Tier 2. Reading looks harmless until you remember what lives on a
clipboard — a password manager's last copy, a 2FA code, a bank account number —
so it asks before the first read and its payload is flagged untrusted on the way
back. Writing is Tier 2 because it silently replaces something the user was
about to paste.

Backends, in order: ``pyperclip`` if installed, then the platform CLI
(``wl-copy``/``xclip``/``xsel``, ``pbcopy``/``pbpaste``, ``clip``/PowerShell),
then ``unsupported`` with an install hint.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult

__all__ = ["ReadClipboard", "WriteClipboard", "clipboard_backend"]

MAX_CLIPBOARD_CHARS = 100_000

_INSTALL_HINT = (
    "no clipboard backend found. Install pyperclip (pip install pyperclip), or "
    "wl-clipboard / xclip / xsel on Linux."
)


def _pyperclip() -> Any | None:
    try:
        import pyperclip  # type: ignore

        return pyperclip
    except Exception:  # noqa: BLE001
        return None


def clipboard_backend(ctx: ActionContext) -> str | None:
    """Which backend this machine would use, or None."""
    if _pyperclip() is not None:
        return "pyperclip"
    system = ctx.system
    if system == "Darwin" and ctx.which("pbcopy"):
        return "pbcopy"
    if system == "Windows":
        return "powershell"
    tool = ctx.which("wl-copy", "xclip", "xsel")
    return tool.rsplit("/", 1)[-1] if tool else None


class _ClipboardAction(Action):
    capability = "clipboard"
    timeout_s = 15.0

    def available(self, ctx: ActionContext) -> bool:
        return bool(ctx.config.clipboard_enabled) and clipboard_backend(ctx) is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if not ctx.config.clipboard_enabled:
            return "clipboard access is disabled in the jarvis-desktop config"
        return _INSTALL_HINT


class ReadClipboard(_ClipboardAction):
    id = "read_clipboard"
    tier = ActionTier.NOTIFY
    description = "Read the current contents of this machine's clipboard."
    params_schema: dict[str, str] = {}

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        backend = clipboard_backend(ctx)
        if backend is None:
            return ActionResult.unsupported(_INSTALL_HINT)

        if backend == "pyperclip":
            module = _pyperclip()
            assert module is not None
            try:
                text = module.paste()
            except Exception as exc:  # noqa: BLE001 - pyperclip raises its own
                return ActionResult.failed(f"clipboard read failed: {exc}")
        else:
            argv = {
                "pbcopy": ["pbpaste"],
                "wl-copy": ["wl-paste", "--no-newline"],
                "xclip": ["xclip", "-selection", "clipboard", "-o"],
                "xsel": ["xsel", "--clipboard", "--output"],
                "powershell": ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            }.get(backend)
            if argv is None:
                return ActionResult.unsupported(_INSTALL_HINT)
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=8, check=False,
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return ActionResult.failed(f"clipboard read failed: {exc}")
            if proc.returncode != 0:
                return ActionResult.failed(
                    f"clipboard read failed: {(proc.stderr or '').strip() or proc.returncode}"
                )
            text = proc.stdout

        truncated = len(text) > MAX_CLIPBOARD_CHARS
        # Whatever is on the clipboard was put there by some other program. It
        # is the textbook injection vector, so it is DATA.
        return ActionResult.untrusted(
            {
                "content": text[:MAX_CLIPBOARD_CHARS],
                "length": len(text),
                "truncated": truncated,
                "via": backend,
            }
        )


class WriteClipboard(_ClipboardAction):
    id = "write_clipboard"
    tier = ActionTier.NOTIFY
    description = "Replace this machine's clipboard contents with some text."
    params_schema = {"content": "string: the text to put on the clipboard"}

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        content = params.get("content")
        if not isinstance(content, str):
            return ActionResult.failed("content is required and must be a string")
        if len(content) > MAX_CLIPBOARD_CHARS:
            return ActionResult.failed(
                f"content is {len(content)} characters; the limit is {MAX_CLIPBOARD_CHARS}"
            )
        backend = clipboard_backend(ctx)
        if backend is None:
            return ActionResult.unsupported(_INSTALL_HINT)

        if backend == "pyperclip":
            module = _pyperclip()
            assert module is not None
            try:
                module.copy(content)
            except Exception as exc:  # noqa: BLE001
                return ActionResult.failed(f"clipboard write failed: {exc}")
            return ActionResult.success(length=len(content), via=backend)

        argv = {
            "pbcopy": ["pbcopy"],
            "wl-copy": ["wl-copy"],
            "xclip": ["xclip", "-selection", "clipboard"],
            "xsel": ["xsel", "--clipboard", "--input"],
            "powershell": ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
        }.get(backend)
        if argv is None:
            return ActionResult.unsupported(_INSTALL_HINT)
        try:
            proc = subprocess.run(
                argv, input=content, capture_output=True, text=True, timeout=8, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ActionResult.failed(f"clipboard write failed: {exc}")
        if proc.returncode != 0:
            return ActionResult.failed(
                f"clipboard write failed: {(proc.stderr or '').strip() or proc.returncode}"
            )
        return ActionResult.success(length=len(content), via=backend)
