"""System state, volume, desktop notifications, lock and sleep.

``psutil`` is used when it is importable and the stdlib is used when it is not,
because a self-hosted agent should install and run on a bare Python. Every
per-OS helper degrades to ``unsupported`` with an actionable sentence rather
than raising.
"""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult

try:  # optional
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore[assignment]

__all__ = ["GetSystemState", "SetVolume", "Notify", "LockScreen", "Sleep"]


def _run(argv: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Small helper for the per-OS shell-outs in this module.

    Unlike :class:`~jarvis_desktop.actions.shell.RunCommand` these are fixed
    argv built from validated parameters — never a string from the model — so
    they are not routed through the denylist.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]}: timed out"
    except OSError as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class GetSystemState(Action):
    id = "get_system_state"
    tier = ActionTier.AUTO
    description = "Report OS, hostname, uptime, CPU, memory, disk and battery for this machine."
    params_schema = {"path": "string (optional): which filesystem to report disk usage for"}
    capability = "system"
    timeout_s = 10.0

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        data: dict[str, Any] = {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "device_name": ctx.config.device_name,
            "psutil": psutil is not None,
        }
        try:
            data["user"] = getpass.getuser()
        except Exception:  # noqa: BLE001 - no passwd entry in some containers
            data["user"] = None

        data["cpu"] = self._cpu()
        data["memory"] = self._memory()
        data["disk"] = self._disk(ctx, params)
        battery = self._battery()
        if battery is not None:
            data["battery"] = battery
        uptime = self._uptime()
        if uptime is not None:
            data["uptime_s"] = uptime
        return ActionResult.success(data)

    # --- per-metric, psutil first, stdlib second ---------------------------

    @staticmethod
    def _cpu() -> dict[str, Any]:
        out: dict[str, Any] = {"count": os.cpu_count()}
        if psutil is not None:
            try:
                out["percent"] = psutil.cpu_percent(interval=0.2)
            except Exception:  # noqa: BLE001
                pass
        if hasattr(os, "getloadavg"):
            try:
                one, five, fifteen = os.getloadavg()
                out["load_avg"] = [round(one, 2), round(five, 2), round(fifteen, 2)]
                if "percent" not in out and out["count"]:
                    out["percent"] = round(min(100.0, one / out["count"] * 100), 1)
            except OSError:
                pass
        return out

    @staticmethod
    def _memory() -> dict[str, Any]:
        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                return {
                    "total_bytes": vm.total,
                    "available_bytes": vm.available,
                    "percent_used": vm.percent,
                }
            except Exception:  # noqa: BLE001
                pass
        # Linux: /proc/meminfo. Everything else: sysconf, when it has the keys.
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                fields = {}
                for line in fh:
                    key, _, rest = line.partition(":")
                    value = rest.strip().split(" ")[0]
                    if value.isdigit():
                        fields[key] = int(value) * 1024
            total = fields.get("MemTotal")
            available = fields.get("MemAvailable", fields.get("MemFree"))
            if total:
                return {
                    "total_bytes": total,
                    "available_bytes": available,
                    "percent_used": (
                        round((total - available) / total * 100, 1) if available else None
                    ),
                }
        except OSError:
            pass
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            return {"total_bytes": total, "available_bytes": None, "percent_used": None}
        except (ValueError, OSError, AttributeError):
            return {"total_bytes": None, "available_bytes": None, "percent_used": None}

    @staticmethod
    def _disk(ctx: ActionContext, params: dict[str, Any]) -> dict[str, Any]:
        raw = Action.str_param(params, "path")
        target = ctx.scope.default_root
        if raw:
            resolved = ctx.scope.resolve(raw, allow_root=True)
            # Disk usage of a path outside the roots leaks nothing dangerous,
            # but keeping every file-shaped parameter inside the scope means
            # there is one rule to remember rather than two.
            if resolved.allowed and resolved.path is not None:
                target = resolved.path
        try:
            usage = shutil.disk_usage(str(target))
        except OSError as exc:
            return {"path": str(target), "error": str(exc)}
        return {
            "path": str(target),
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "percent_used": round((usage.total - usage.free) / usage.total * 100, 1)
            if usage.total
            else None,
        }

    @staticmethod
    def _battery() -> dict[str, Any] | None:
        if psutil is not None and hasattr(psutil, "sensors_battery"):
            try:
                battery = psutil.sensors_battery()
            except Exception:  # noqa: BLE001
                battery = None
            if battery is not None:
                return {
                    "percent": round(battery.percent, 1),
                    "charging": bool(battery.power_plugged),
                    "seconds_left": (
                        battery.secsleft if battery.secsleft and battery.secsleft > 0 else None
                    ),
                }
        # Linux without psutil: sysfs.
        base = "/sys/class/power_supply"
        try:
            names = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
        except OSError:
            return None
        for name in names:
            try:
                with open(f"{base}/{name}/capacity", encoding="utf-8") as fh:
                    percent = int(fh.read().strip())
                with open(f"{base}/{name}/status", encoding="utf-8") as fh:
                    status = fh.read().strip()
            except (OSError, ValueError):
                continue
            return {"percent": percent, "charging": status.lower() == "charging", "status": status}
        return None

    @staticmethod
    def _uptime() -> float | None:
        if psutil is not None:
            try:
                return round(time.time() - psutil.boot_time(), 1)
            except Exception:  # noqa: BLE001
                pass
        try:
            with open("/proc/uptime", encoding="utf-8") as fh:
                return round(float(fh.read().split()[0]), 1)
        except (OSError, ValueError, IndexError):
            return None


class SetVolume(Action):
    id = "set_volume"
    tier = ActionTier.AUTO  # trivially reversible; the user can hear it happen
    description = "Set this machine's output volume (0-100), or mute/unmute it."
    params_schema = {
        "level": "int 0-100: the new output volume",
        "mute": "bool (optional): mute or unmute instead of setting a level",
    }
    capability = "system"
    # Above the longest per-OS deadline below (the Windows mixer nudge waits up
    # to 20s). An action whose own subprocess outlives the dispatcher's cap gets
    # reported as a timeout while the work carries on in an orphaned thread.
    timeout_s = 25.0

    def available(self, ctx: ActionContext) -> bool:
        system = ctx.system
        if system == "Darwin" or system == "Windows":
            return True
        return ctx.which("pactl", "wpctl", "amixer") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return (
            "no supported mixer found; install pulseaudio-utils (pactl), "
            "wireplumber (wpctl) or alsa-utils (amixer)"
        )

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        mute = params.get("mute")
        if mute is not None and "level" not in params:
            return self._set_mute(ctx, self.bool_param(params, "mute"))
        if "level" not in params:
            return ActionResult.failed("level (0-100) or mute is required")
        level = max(0, min(100, self.int_param(params, "level", -1)))
        if self.int_param(params, "level", -1) < 0:
            return ActionResult.failed("level must be an integer between 0 and 100")

        system = ctx.system
        if system == "Darwin":
            code, _, err = _run(["osascript", "-e", f"set volume output volume {level}"])
        elif system == "Windows":
            code, _, err = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    # No third-party module: nudge the volume with media keys.
                    "$w = New-Object -ComObject WScript.Shell;"
                    "1..50 | ForEach-Object { $w.SendKeys([char]174) };"
                    f"1..{max(0, level) // 2} | ForEach-Object {{ $w.SendKeys([char]175) }}",
                ],
                timeout=20.0,
            )
        elif ctx.which("pactl"):
            code, _, err = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
        elif ctx.which("wpctl"):
            code, _, err = _run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level / 100:.2f}"])
        elif ctx.which("amixer"):
            code, _, err = _run(["amixer", "-q", "sset", "Master", f"{level}%"])
        else:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "no mixer")

        if code != 0:
            return ActionResult.failed(f"could not set the volume: {err.strip() or code}")
        return ActionResult.success(level=level)

    def _set_mute(self, ctx: ActionContext, mute: bool) -> ActionResult:
        system = ctx.system
        if system == "Darwin":
            flag = "true" if mute else "false"
            code, _, err = _run(["osascript", "-e", f"set volume output muted {flag}"])
        elif ctx.which("pactl"):
            code, _, err = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
        elif ctx.which("wpctl"):
            code, _, err = _run(
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"]
            )
        elif ctx.which("amixer"):
            code, _, err = _run(["amixer", "-q", "sset", "Master", "mute" if mute else "unmute"])
        else:
            return ActionResult.unsupported("muting is not supported on this machine")
        if code != 0:
            return ActionResult.failed(f"could not change mute: {err.strip() or code}")
        return ActionResult.success(muted=mute)


class Notify(Action):
    id = "notify"
    tier = ActionTier.AUTO
    description = "Show a desktop notification on this machine."
    params_schema = {
        "title": "string: the notification title",
        "message": "string: the body text",
        "urgency": "string (optional): low | normal | critical",
    }
    capability = "notify"
    #: Above the 15s PowerShell toast deadline, for the same reason as SetVolume.
    timeout_s = 20.0

    def available(self, ctx: ActionContext) -> bool:
        # Always available: the last resort is a log line, which is honest and
        # never fails. The agent should not lose a notification because the
        # desktop toolkit is missing.
        return bool(ctx.config.notifications_enabled)

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "notifications are disabled in the jarvis-desktop config"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        title = (self.str_param(params, "title") or "Jarvis").strip()[:200]
        message = (self.str_param(params, "message") or "").strip()[:2000]
        if not message:
            return ActionResult.failed("message is required")
        urgency = (self.str_param(params, "urgency") or "normal").strip().lower()
        if urgency not in ("low", "normal", "critical"):
            urgency = "normal"

        system = ctx.system
        if system == "Linux" and ctx.which("notify-send"):
            code, _, err = _run(["notify-send", "-u", urgency, "--", title, message])
            if code == 0:
                return ActionResult.success(delivered="notify-send", title=title)
        elif system == "Darwin" and ctx.which("osascript"):
            script = (
                f"display notification {_applescript_str(message)} "
                f"with title {_applescript_str(title)}"
            )
            code, _, err = _run(["osascript", "-e", script])
            if code == 0:
                return ActionResult.success(delivered="osascript", title=title)
        elif system == "Windows":
            delivered = _windows_toast(title, message)
            if delivered:
                return ActionResult.success(delivered=delivered, title=title)

        # Degrade gracefully: a log line is still a delivered notification as
        # far as the user's journal is concerned, and it never fails.
        import logging

        logging.getLogger("jarvis_desktop.notify").info("NOTIFY %s: %s", title, message)
        return ActionResult.success(
            delivered="log",
            title=title,
            note="no desktop notifier found; the notification was written to the agent log",
        )


def _applescript_str(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ps_single_quote(text: str) -> str:
    """Body of a PowerShell **single-quoted** string literal.

    Inside `'...'` PowerShell treats every character literally except `'`
    itself, which is escaped by doubling it. So doubling quotes is the whole
    escape — provided nothing else is interpolated into the script.

    This matters more than it looks: the callers build a ``-Command`` string
    from parameters the *server* chose, and ``notify`` is Tier 1. Without this,
    a title of ``x'); <anything>; ('`` would close the literal and run arbitrary
    PowerShell with no consent prompt anywhere in the path.

    Control characters are dropped as well: they cannot escape the literal, but
    they can hide the rest of a payload from anyone reading the audit log.
    """
    cleaned = "".join(" " if ch in "\r\n\t" else ch for ch in text if ord(ch) >= 0x20 or ch in "\r\n\t")
    return cleaned.replace("'", "''")


def _windows_toast(title: str, message: str) -> str | None:
    try:
        from win10toast import ToastNotifier  # type: ignore

        ToastNotifier().show_toast(title, message, duration=8, threaded=True)
        return "win10toast"
    except Exception:  # noqa: BLE001
        pass
    code, _, _ = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
            " ContentType = WindowsRuntime] > $null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(2);"
            f"$t.GetElementsByTagName('text')[0].AppendChild("
            f"$t.CreateTextNode('{_ps_single_quote(title)}')) > $null;"
            f"$t.GetElementsByTagName('text')[1].AppendChild("
            f"$t.CreateTextNode('{_ps_single_quote(message)}')) > $null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Jarvis')"
            ".Show([Windows.UI.Notifications.ToastNotification]::new($t))",
        ],
        timeout=15.0,
    )
    return "powershell" if code == 0 else None


class LockScreen(Action):
    id = "lock_screen"
    # Tier 3: it can lock a user out of a session with unsaved work, and the
    # only way back in is a password the agent does not have.
    tier = ActionTier.CONFIRM
    description = "Lock this machine's screen."
    params_schema: dict[str, str] = {}
    capability = "system"
    timeout_s = 12.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system in ("Darwin", "Windows"):
            return True
        return ctx.which(
            "loginctl", "xdg-screensaver", "swaylock", "i3lock", "gnome-screensaver-command"
        ) is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "no screen locker found; install one of loginctl, swaylock, i3lock or xdg-screensaver"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        system = ctx.system
        if system == "Darwin":
            candidates = [["pmset", "displaysleepnow"]]
        elif system == "Windows":
            candidates = [["rundll32.exe", "user32.dll,LockWorkStation"]]
        else:
            candidates = [
                ["loginctl", "lock-session"],
                ["swaylock", "-f"],
                ["i3lock"],
                ["gnome-screensaver-command", "--lock"],
                ["xdg-screensaver", "lock"],
            ]
        for argv in candidates:
            if not shutil.which(argv[0]):
                continue
            code, _, err = _run(argv)
            if code == 0:
                return ActionResult.success(locked_with=argv[0])
        return ActionResult.failed("every screen locker this machine has refused to run")


class Sleep(Action):
    id = "sleep"
    tier = ActionTier.CONFIRM
    description = "Suspend this machine (sleep). The agent goes offline until it wakes."
    params_schema: dict[str, str] = {}
    capability = "system"
    timeout_s = 15.0

    def available(self, ctx: ActionContext) -> bool:
        if ctx.system in ("Darwin", "Windows"):
            return True
        return ctx.which("systemctl", "loginctl", "pm-suspend") is not None

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return "no suspend command found (systemctl / loginctl / pm-suspend)"

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        system = ctx.system
        if system == "Darwin":
            candidates = [["pmset", "sleepnow"]]
        elif system == "Windows":
            candidates = [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]]
        else:
            candidates = [
                ["systemctl", "suspend"],
                ["loginctl", "suspend"],
                ["pm-suspend"],
            ]
        for argv in candidates:
            if not shutil.which(argv[0]):
                continue
            code, _, err = _run(argv, timeout=10.0)
            if code == 0:
                return ActionResult.success(suspended_with=argv[0])
        return ActionResult.failed("every suspend command this machine has refused to run")
