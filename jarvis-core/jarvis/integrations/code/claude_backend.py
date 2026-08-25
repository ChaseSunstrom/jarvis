"""Claude Code as an execution backend, headless, in the same sandbox.

The local coding agent is the default and stays it. This is the alternative for
work that is genuinely heavy — a refactor across twenty files, a bug nobody can
localise — and it is the **first deliberate exception to "no cloud"** in this
project: it sends the repository's code to Anthropic. That is a decision, so it
is off, it needs a key the operator supplies, and `BLOCKERS.md` carries the row.

## What does NOT change when you switch backends

Everything that makes a coding job safe:

* **The same sandbox.** The run happens inside the repository's environment
  container, through `Workspace.run_sandboxed` — the one with no host mounts
  and whatever network policy the environment declares. There is no path by
  which a delegated run writes outside it, because there is no path by which it
  runs outside it.
* **The same approval gate.** A repository on `ask` holds every edit for a
  human, whichever backend produced it: the gate is in `approvals.py`, in front
  of the workspace, and this backend goes through the workspace.
* **The same verification.** The repository's own checks decide whether the job
  is green. Claude Code's opinion of its work is not the criterion.

## The protocol

`claude --print --output-format json`, which answers one JSON object on stdout:

    {"type": "result", "subtype": "success", "result": "...", "is_error": false,
     "num_turns": 7, "total_cost_usd": 0.42, "session_id": "..."}

That is the whole contract, and `testing/fixtures/fake_claude_code.py` speaks
it. CI runs against the stand-in — there is no key on a runner and there should
not be — so what is proved offline is the plumbing, the containment and the
gate; what needs a key is the model's actual output, and that is honest rather
than hidden.
"""

from __future__ import annotations

import json
import logging
import shlex
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: The binary, inside the sandbox. Overridable for the stand-in.
DEFAULT_COMMAND = "claude"

#: How long one delegated run may take. Longer than a local job: the whole
#: point of delegating is that the work is bigger.
DEFAULT_TIMEOUT = 1800.0


class ClaudeBackendError(RuntimeError):
    """The delegated run could not happen, and this says which part."""


@dataclass
class ClaudeResult:
    """What one headless run produced."""

    ok: bool = False
    text: str = ""
    turns: int = 0
    cost_usd: float = 0.0
    session_id: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": "claude-code",
            "ok": self.ok,
            "turns": self.turns,
            "cost_usd": round(self.cost_usd, 4),
            "session_id": self.session_id or None,
            "error": self.error or None,
        }


def parse_result(raw: str) -> ClaudeResult:
    """Claude Code's `--output-format json`, or a clear failure.

    Tolerant about what surrounds the object — a sandbox wrapper can prepend a
    line — and strict about what it means: `is_error` is the verdict, and a
    payload that does not carry one is not a result.
    """
    text = (raw or "").strip()
    if not text:
        return ClaudeResult(ok=False, error="the backend printed nothing at all")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return ClaudeResult(ok=False, error=f"no JSON in the output: {text[:200]}")
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as err:
        return ClaudeResult(ok=False, error=f"unreadable output: {err}")
    if not isinstance(payload, dict) or "is_error" not in payload:
        return ClaudeResult(
            ok=False,
            error="the output is not a Claude Code result (no is_error)",
            raw=payload if isinstance(payload, dict) else {},
        )
    return ClaudeResult(
        ok=not bool(payload.get("is_error")),
        text=str(payload.get("result") or ""),
        turns=int(payload.get("num_turns") or 0),
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        session_id=str(payload.get("session_id") or ""),
        error="" if not payload.get("is_error") else str(payload.get("result") or "failed"),
        raw=payload,
    )


@dataclass
class ClaudeCodeBackend:
    """Runs Claude Code inside a workspace's sandbox, and nowhere else."""

    enabled: bool = False
    api_key: str = ""
    command: str = DEFAULT_COMMAND
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT
    #: Extra flags an operator wants on every run. Never `--dangerously-*`:
    #: see `refuse_dangerous`.
    extra_args: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key)

    def why_not(self) -> str:
        if not self.enabled:
            return "the Claude Code backend is off (`code: claude_code: enabled: false`)"
        if not self.api_key:
            return (
                "the Claude Code backend has no API key. It is the one path in "
                "this project that sends code off the network, so it does not "
                "start without one being supplied deliberately."
            )
        return ""

    def argv(self, instruction: str) -> list[str]:
        """The command line, as a string for the sandbox session to run."""
        parts = [self.command, "--print", "--output-format", "json"]
        if self.model:
            parts += ["--model", self.model]
        parts += list(self.extra_args)
        parts.append(instruction)
        return parts

    def command_line(self, instruction: str) -> str:
        return " ".join(shlex.quote(part) for part in self.argv(instruction))

    async def run(self, workspace: Any, instruction: str) -> ClaudeResult:
        """One delegated run, inside the repository's own environment.

        `run_sandboxed` raises when there is no environment rather than falling
        back to the host — which is the property this whole module depends on,
        and the reason nothing here has a "run it here instead" branch.
        """
        why = self.why_not()
        if why:
            raise ClaudeBackendError(why)
        if not getattr(workspace, "sandboxed", False):
            raise ClaudeBackendError(
                "refusing to delegate a coding job to a repository with no "
                "sandbox: the containment claim is the whole reason this is "
                "allowed to exist"
            )
        _LOGGER.info("Delegating a coding job to Claude Code in the sandbox")
        code, out = await workspace.run_sandboxed(
            self.command_line(instruction), timeout=self.timeout
        )
        result = parse_result(out)
        if code != 0 and result.ok:
            # A zero-exit contract that disagrees with its own payload: believe
            # the exit code, because that is the one the sandbox reports.
            result.ok = False
            result.error = result.error or f"the backend exited {code}"
        return result


def refuse_dangerous(args: list[str] | tuple[str, ...]) -> str:
    """"" if these flags are acceptable, else why they are not.

    `--dangerously-skip-permissions` turns off the tool's own gate. The
    repository's gate would still hold — approvals are in front of the
    workspace — but a backend configured to bypass its own permissions inside a
    sandbox is a mistake nobody meant to make, and it is cheap to refuse.
    """
    for arg in args or ():
        if "dangerously" in str(arg):
            return f"refusing {arg!r}: it turns off the backend's own permission gate"
    return ""


def build(config: Any) -> ClaudeCodeBackend:
    options = config if isinstance(config, dict) else {}
    extra = tuple(str(a) for a in (options.get("extra_args") or ()))
    refused = refuse_dangerous(extra)
    if refused:
        _LOGGER.error("code: claude_code: %s", refused)
        extra = ()
    return ClaudeCodeBackend(
        enabled=bool(options.get("enabled", False)),
        api_key=str(options.get("api_key") or ""),
        command=str(options.get("command") or DEFAULT_COMMAND),
        model=str(options.get("model") or ""),
        timeout=float(options.get("timeout") or DEFAULT_TIMEOUT),
        extra_args=extra,
    )
