#!/usr/bin/env python3
"""Start the REAL desktop agent with the two screen-facing backends stubbed.

    JARVIS_E2E_CONTROL=/tmp/ctl python3 agent_runner.py run --server ws://... -v

This is ``python -m jarvis_desktop`` with exactly two substitutions, made
before ``main()`` is called:

* :func:`jarvis_desktop.consent.build_gateway` — the Tier-2/Tier-3
  confirmation prompt, normally a tkinter dialog or a terminal question.
* :func:`jarvis_desktop.companion.build_asker` — the dialog a
  ``companion.ask`` question is rendered in.

Both of those exist to put something in front of a person and wait for them to
click. CI has no person and no display, so each is replaced by a backend that
reads its answer from a JSON file and appends what it was asked to a JSONL
file. That is the whole seam.

Nothing else is replaced or patched. The channel, the handshake, the action
registry, the policy engine, the tier arithmetic, the path scope, the SSRF
guard, the audit log, presence and the companion handler are all the shipping
code, running in a real process against a real socket.

Two properties are deliberately preserved by the stubs:

* **They fail closed.** An unreadable, missing or unparseable control file is a
  DENIAL, exactly as a crashed toolkit is in the real gateway. A test that
  forgets to say "approve" cannot accidentally get an approval.
* **They cannot remember anything.** The stub returns the verdict it was told
  to and never invents ``approved_always``, so nothing in the suite can quietly
  turn a per-command approval into a standing rule behind the policy engine's
  back.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any

# The launcher may be run by absolute path from anywhere, so make sure the
# package it is launching is importable regardless of the working directory.
AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from jarvis_desktop.companion import AskOutcome, Asker, CompanionMessage  # noqa: E402
from jarvis_desktop.consent import (  # noqa: E402
    ApprovalRequest,
    ApprovalVerdict,
    ConsentGateway,
    render_prompt,
)

_COUNTER = itertools.count(1)


def _append(path: Path, payload: dict[str, Any]) -> None:
    """One JSON object, one line, one write. Readable while it is being written."""
    line = json.dumps(payload, default=str, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _read(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class FileConsentGateway(ConsentGateway):
    """The confirmation prompt, answered from ``consent.json``.

    ``unattended`` is False on purpose: this stub stands in for a human who is
    present and clicking, so a denial here must read as "the user said no" and
    not as "there was nobody to ask".

    For the same reason it honours ``on_interaction``: answering a prompt is
    being at the machine, and the real gateways report it as presence. A stub
    that stayed silent there would leave the e2e agent looking idle in exactly
    the situation where it is not.
    """

    name = "e2e-file-consent"

    def __init__(self, control: Path, on_interaction: Any = None) -> None:
        self.control = control
        self.on_interaction = on_interaction

    @property
    def unattended(self) -> bool:
        return False

    async def request(self, request: ApprovalRequest) -> ApprovalVerdict:
        answer = _read(self.control / "consent.json")
        verdict = ApprovalVerdict.from_answer(answer.get("verdict", "denied"))
        _append(
            self.control / "prompts.jsonl",
            {
                "seq": next(_COUNTER),
                "action_id": request.action_id,
                "description": request.description,
                # Verbatim, because "the prompt showed the truth" is the thing
                # worth asserting on.
                "params": dict(request.params),
                "tier": int(request.tier.wire),
                "tier_name": request.tier.name,
                "reason": request.reason,
                "command_id": request.command_id,
                "rememberable": bool(request.rememberable),
                "timeout_s": request.timeout_s,
                "verdict": verdict.value,
                "rendered": render_prompt(request),
            },
        )
        # The control file standing in for a person who answered.
        self.note_interaction()
        return verdict


class FileAsker(Asker):
    """The ``companion.ask`` dialog, answered from ``answer.json``."""

    name = "e2e-file-asker"

    def __init__(self, control: Path) -> None:
        self.control = control

    def usable(self) -> bool:
        return True

    @property
    def unattended(self) -> bool:
        return False

    async def ask(self, message: CompanionMessage) -> AskOutcome:
        answer = _read(self.control / "answer.json")
        status = str(answer.get("status") or "dismissed")
        text = answer.get("answer")
        _append(
            self.control / "asks.jsonl",
            {
                "seq": next(_COUNTER),
                "message_id": message.message_id,
                "kind": message.kind,
                "mode": message.mode,
                "text": message.text,
                "options": list(message.options),
                "importance": message.importance,
                "timeout_s": message.timeout_s,
                "conversation_id": message.conversation_id,
                "replied": status,
                "answer": text,
            },
        )
        if status == "answered":
            return AskOutcome.answered("" if text is None else str(text))
        if status == "timeout":
            return AskOutcome.timed_out()
        if status == "undeliverable":
            return AskOutcome.undeliverable()
        return AskOutcome.dismissed()


def main(argv: list[str] | None = None) -> int:
    control_dir = os.environ.get("JARVIS_E2E_CONTROL")
    if not control_dir:
        print("JARVIS_E2E_CONTROL must point at the control directory", file=sys.stderr)
        return 2
    control = Path(control_dir)
    control.mkdir(parents=True, exist_ok=True)

    import jarvis_desktop.__main__ as entry

    # `cmd_run` calls these by name out of its own module namespace, so this is
    # the whole substitution. Signatures match the originals.
    entry.build_gateway = lambda headless_deny=False, on_interaction=None: (
        FileConsentGateway(control, on_interaction)
    )
    entry.build_asker = lambda headless=False: FileAsker(control)

    return int(entry.main(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
