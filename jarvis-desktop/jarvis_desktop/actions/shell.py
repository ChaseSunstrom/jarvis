"""``run_command`` — Tier 3, always.

This is the most dangerous thing the agent can do, so it is fenced on five
independent sides:

1. **Tier 3.** Every invocation shows the verbatim command line in a consent
   prompt. This is the actual security boundary; everything below is
   defence in depth.
2. **No shell by default.** Without ``shell.use_shell`` in the config the string
   is split with :mod:`shlex` and exec'd directly, so ``;``, ``&&``, backticks,
   ``$(...)`` and redirection are literal argv text rather than syntax. Turning
   it on is a config edit the server cannot make.
3. **A denylist of shapes that are never a good idea** — ``rm -rf /``, ``mkfs``,
   ``dd of=/dev/sda``, ``shutdown``, the classic fork bomb, ``curl | sh``. This
   is a *tripwire, not a sandbox*: a denylist over a Turing-complete interpreter
   can always be evaded, and pretending otherwise would be the dangerous part.
   It exists to catch the LLM confidently doing something catastrophic, not to
   contain an attacker who already has approval to run commands.
4. **A scrubbed environment.** The child gets a small allowlist of variables and
   nothing that looks like a credential — in particular not ``JARVIS_TOKEN``.
5. **A timeout and an output cap.** The process is killed at the deadline (whole
   group, on POSIX) and stdout/stderr are truncated, so a command cannot wedge
   the agent or flood the socket.

Command output is UNTRUSTED DATA and is flagged as such on the way back.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult

__all__ = ["DenyRule", "ShellGuard", "scrub_env", "RunCommand", "DEFAULT_DENYLIST"]


@dataclass(frozen=True)
class DenyRule:
    name: str
    pattern: str
    why: str

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)


#: Shapes refused outright, before the prompt is even shown.
DEFAULT_DENYLIST: tuple[DenyRule, ...] = (
    DenyRule(
        "fork_bomb",
        r":\s*\(\s*\)\s*\{.{0,40}\|.{0,20}&.{0,20}\}\s*;?\s*:",
        "fork bomb",
    ),
    DenyRule(
        "rm_root",
        r"\brm\b(?=(?:\s+-{1,2}[\w-]+)*\s+-{0,2}[\w-]*r)"
        r"(?=(?:\s+-{1,2}[\w-]+)*\s+-{0,2}[\w-]*f)"
        r".*?\s(?:/|/\*|~|~/|\$HOME|\.\.?)(?:\s|$)",
        "recursive force-delete of the filesystem root, home or cwd",
    ),
    DenyRule(
        "rm_system_dir",
        r"\brm\b(?=[^\n]*\s-{1,2}[\w-]*[rf])[^\n]*\s"
        r"/(?:bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var|"
        r"System|Library|Applications)(?:/|\s|$)",
        "recursive delete of a system directory",
    ),
    DenyRule("mkfs", r"\bmkfs(\.\w+)?\b", "formats a filesystem"),
    DenyRule("dd_to_device", r"\bdd\b[^\n]*\bof\s*=\s*/dev/", "writes raw blocks to a device"),
    DenyRule(
        "redirect_to_block_device",
        r">\s*/dev/(?:sd[a-z]|nvme\d|disk\d|hd[a-z]|mmcblk\d)",
        "writes to a raw block device",
    ),
    DenyRule(
        "partition_tools",
        r"\b(?:fdisk|sfdisk|parted|gparted|diskutil\s+(?:eraseDisk|reformat|partitionDisk)|wipefs)\b",
        "repartitions a disk",
    ),
    DenyRule(
        # Anchored to command position (start of line, after a separator, or
        # after sudo) so that "echo shutdown is scheduled" is just a sentence.
        "power",
        r"(?:^|[;&|]\s*|\bsudo\s+)(?:shutdown|reboot|halt|poweroff)\b|"
        r"(?:^|[;&|]\s*|\bsudo\s+)init\s+[06]\b|"
        r"\bsystemctl\s+(?:poweroff|reboot|halt|suspend|hibernate)\b",
        "powers the machine down or reboots it (use the sleep/lock actions)",
    ),
    DenyRule(
        "chmod_world_root",
        r"\bchmod\b[^\n]*?\s-{1,2}[\w-]*R[\w-]*\s+(?:0?777|a\+rwx)\s+/(?:\s|$)",
        "makes the whole filesystem world-writable",
    ),
    DenyRule(
        "chown_root",
        r"\bchown\b[^\n]*?\s-{1,2}[\w-]*R[\w-]*\s+[^\s]+\s+/(?:\s|$)",
        "recursively rewrites ownership of the filesystem root",
    ),
    DenyRule(
        "curl_pipe_shell",
        r"\b(?:curl|wget|fetch)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b",
        "downloads and executes a remote script in one step",
    ),
    DenyRule(
        "history_wipe",
        r"\bhistory\s+-c\b|>\s*~?/?\.?(?:bash|zsh)_history\b",
        "erases the shell history (anti-forensics)",
    ),
    DenyRule(
        "windows_format",
        r"\bformat\s+[a-z]:|\bdel\b[^\n]*\s/[sq][^\n]*\s[a-z]:\\|\bdiskpart\b|"
        r"\bcipher\s+/w\b|\bvssadmin\s+delete\s+shadows\b",
        "destroys a Windows volume or its shadow copies",
    ),
    DenyRule(
        "self_destruct",
        r"\brm\b[^\n]*\bjarvis[-_]desktop\b|\bkillall\b\s+jarvis",
        "deletes or kills the Jarvis agent itself",
    ),
)


class ShellGuard:
    """Pure logic — no subprocess, no I/O. The unit under test."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        self.rules: list[tuple[DenyRule, re.Pattern[str]]] = [
            (rule, rule.compiled()) for rule in DEFAULT_DENYLIST
        ]
        for index, pattern in enumerate(extra):
            rule = DenyRule(f"user_{index}", pattern, "refused by local config")
            try:
                self.rules.append((rule, rule.compiled()))
            except re.error:
                # A bad user regex must not silently disable the whole guard.
                continue

    @staticmethod
    def normalize(command: str) -> str:
        """Fold the noise an evader hides behind: whitespace runs, non-breaking
        spaces, and line continuations. Deliberately does NOT try to undo
        quoting or variable expansion — see the module docstring on why this is
        a tripwire rather than a sandbox."""
        text = command.replace(" ", " ").replace("\\\n", " ")
        text = text.replace("\r", " ").replace("\n", " ; ").replace("\t", " ")
        return re.sub(r"\s+", " ", text).strip()

    def check(self, command: str | Sequence[str]) -> DenyRule | None:
        """The rule this command trips, or None.

        Accepts either a command line or an argv list; an argv list is joined
        with :func:`shlex.join` so ``["rm", "-rf", "/"]`` is caught exactly like
        ``"rm -rf /"``.
        """
        if isinstance(command, str):
            text = command
        else:
            try:
                text = shlex.join(str(part) for part in command)
            except Exception:  # noqa: BLE001
                text = " ".join(str(part) for part in command)
        normalized = self.normalize(text)
        if not normalized:
            return None
        # Also test a de-quoted form so `rm -rf "/"` and `rm -rf '/'` are caught.
        dequoted = self.normalize(normalized.replace('"', "").replace("'", ""))
        for rule, pattern in self.rules:
            if pattern.search(normalized) or pattern.search(dequoted):
                return rule
        return None

    def explain(self, rule: DenyRule) -> str:
        return (
            f"refused: this command matches the local denylist rule "
            f"'{rule.name}' ({rule.why}). Jarvis will not run it even with approval."
        )


#: Variables the child is allowed to inherit. Everything else is dropped.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TZ",
    "PWD",
    # Windows needs these or nothing resolves at all.
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)

_SECRETISH = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|CREDENTIAL|PRIVATE_KEY|"
    r"ACCESS_KEY|SESSION|COOKIE|AUTH)",
    re.IGNORECASE,
)


def scrub_env(
    env: Mapping[str, str] | None = None, passthrough: Iterable[str] = ()
) -> dict[str, str]:
    """Build the child environment: a small allowlist, minus anything secret-ish.

    ``passthrough`` lets the user add variables in the config file. Even those
    are dropped if the name looks like a credential — the point is that the
    agent's own ``JARVIS_TOKEN`` can never reach a command the model wrote, and
    a config typo should not undo that.
    """
    source = os.environ if env is None else env
    allowed = set(_ENV_ALLOWLIST) | {name.strip() for name in passthrough if name.strip()}
    out: dict[str, str] = {}
    for name in allowed:
        value = source.get(name)
        if value is None:
            continue
        if _SECRETISH.search(name) or name.upper().startswith("JARVIS_"):
            continue
        out[name] = value
    out.setdefault("PATH", os.defpath)
    # Marks the process as ours in `ps` output and lets a script notice.
    out["JARVIS_AGENT"] = "1"
    return out


class RunCommand(Action):
    id = "run_command"
    tier = ActionTier.CONFIRM
    description = "Run a shell command on this machine and return its output."
    params_schema = {
        "command": "string: the command line (split with shlex unless the user "
        "opted into shell mode in the config)",
        "argv": "array of strings (optional): exec form, used instead of command",
        "cwd": "string (optional): working directory, must be inside an allowed root",
        "timeout_s": "number (optional): kill the command after this long",
    }
    capability = "shell"

    def __init__(self) -> None:
        self._guards: dict[tuple[str, ...], ShellGuard] = {}

    @property
    def timeout_s(self) -> float:  # type: ignore[override]
        # The dispatcher's cap sits above the subprocess's own timeout so the
        # child is killed by its own deadline first and we get its partial
        # output, rather than the dispatcher abandoning a thread.
        return 300.0

    def available(self, ctx: ActionContext) -> bool:
        return bool(ctx.config.shell.enabled)

    def unavailable_reason(self, ctx: ActionContext) -> str | None:
        return (
            "shell commands are disabled on this machine; set "
            '"shell": {"enabled": true} in the jarvis-desktop config to allow them'
        )

    def _guard(self, ctx: ActionContext) -> ShellGuard:
        key = tuple(ctx.config.shell.extra_denylist)
        guard = self._guards.get(key)
        if guard is None:
            guard = ShellGuard(key)
            self._guards[key] = guard
        return guard

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        cfg = ctx.config.shell
        if not cfg.enabled:
            return ActionResult.unsupported(self.unavailable_reason(ctx) or "shell disabled")

        argv_param = params.get("argv")
        command = self.str_param(params, "command")
        if isinstance(argv_param, (list, tuple)) and argv_param:
            argv: list[str] = [str(part) for part in argv_param]
            display = shlex.join(argv)
            use_shell = False
        elif command and command.strip():
            display = command.strip()
            use_shell = bool(cfg.use_shell)
            if use_shell:
                argv = [display]
            else:
                try:
                    argv = shlex.split(display)
                except ValueError as exc:
                    return ActionResult.failed(f"could not parse the command: {exc}")
                if not argv:
                    return ActionResult.failed("empty command")
        else:
            return ActionResult.failed("command or argv is required")

        tripped = self._guard(ctx).check(display)
        if tripped is not None:
            return ActionResult.denied(self._guard(ctx).explain(tripped))

        cwd = self._resolve_cwd(ctx, params)
        if isinstance(cwd, ActionResult):
            return cwd

        timeout = self.float_param(params, "timeout_s", cfg.timeout_s)
        timeout = max(1.0, min(timeout, 240.0))
        env = scrub_env(passthrough=cfg.env_passthrough)

        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "errors": "replace",
        }
        if os.name == "posix":
            # Own process group, so a command that forks is killed whole.
            popen_kwargs["start_new_session"] = True

        try:
            if use_shell:
                proc = subprocess.Popen(display, shell=True, **popen_kwargs)  # noqa: S602
            else:
                proc = subprocess.Popen(argv, **popen_kwargs)
        except FileNotFoundError:
            return ActionResult.failed(f"command not found: {argv[0] if argv else display}")
        except PermissionError:
            return ActionResult.failed(f"not permitted to execute {argv[0] if argv else display}")
        except OSError as exc:
            return ActionResult.failed(f"could not start the command: {exc}")

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""

        cap = max(1024, cfg.max_output_bytes)
        out_text, out_trunc = _cap(stdout or "", cap)
        err_text, err_trunc = _cap(stderr or "", cap)

        payload = {
            "command": display,
            "argv": argv if not use_shell else None,
            "exit_code": proc.returncode,
            "stdout": out_text,
            "stderr": err_text,
            "truncated": out_trunc or err_trunc,
            "timed_out": timed_out,
            "cwd": str(cwd),
            "shell": use_shell,
        }
        if timed_out:
            result = ActionResult.untrusted(payload)
            result.ok = False
            result.error = f"command timed out after {timeout:g}s and was killed"
            from .base import Status

            result.status = Status.ERROR
            return result
        # Output is text this machine did not author. Flagged so nothing
        # downstream mistakes it for an instruction.
        return ActionResult.untrusted(payload)

    def _resolve_cwd(self, ctx: ActionContext, params: dict[str, Any]) -> Any:
        raw = self.str_param(params, "cwd") or ctx.config.shell.cwd
        if not raw:
            return ctx.scope.default_root
        resolved = ctx.scope.resolve(raw, allow_root=True, must_be_dir=True)
        if not resolved.allowed or resolved.path is None:
            return ActionResult.failed(f"cwd rejected: {resolved.reason}")
        return resolved.path


def _cap(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"...(+{len(text) - limit} chars)", True


def _kill_tree(proc: subprocess.Popen[Any]) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def python_executable() -> str:
    """Used by tests that need a command guaranteed to exist."""
    return sys.executable or "python3"
