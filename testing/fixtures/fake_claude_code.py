#!/usr/bin/env python3
"""A stand-in that speaks Claude Code's headless protocol.

There is no API key on a CI runner and there should not be one: the delegated
backend is the single path in this project that sends code off the network. So
what CI proves is the plumbing, the containment and the gate — and it proves
them against something that answers exactly what the real thing answers.

    fake-claude --print --output-format json "fix the failing tests"
    {"type":"result","subtype":"success","result":"…","is_error":false,…}

Behaviour is steered by the instruction, so a test can ask for a failure
without a second binary:

    …containing "FAIL"      answers is_error: true
    …containing "SLOW"      sleeps past a short timeout
    …containing "GARBAGE"   prints something that is not a result
    anything else           edits `fake_claude_was_here.txt` and succeeds
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--print" not in args:
        print("this stand-in only speaks --print", file=sys.stderr)
        return 2
    instruction = args[-1] if args else ""

    if "SLOW" in instruction:
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "30")))
    if "GARBAGE" in instruction:
        print("I am not JSON and I never was")
        return 0
    if "FAIL" in instruction:
        print(json.dumps({
            "type": "result", "subtype": "error_during_execution",
            "result": "could not work out what to change", "is_error": True,
            "num_turns": 3, "total_cost_usd": 0.11, "session_id": "fake-session",
        }))
        return 0

    # The successful path leaves a real edit behind, so a test can assert the
    # work happened in the sandbox rather than that a string came back.
    marker = Path(os.environ.get("FAKE_CLAUDE_MARKER", "fake_claude_was_here.txt"))
    try:
        marker.write_text(f"edited by the stand-in: {instruction[:120]}\n", encoding="utf-8")
    except OSError as err:
        print(json.dumps({
            "type": "result", "subtype": "error_during_execution",
            "result": f"could not write in the sandbox: {err}", "is_error": True,
            "num_turns": 1, "total_cost_usd": 0.0, "session_id": "fake-session",
        }))
        return 0

    print(json.dumps({
        "type": "result", "subtype": "success",
        "result": f"Done: {instruction[:200]}", "is_error": False,
        "num_turns": 7, "total_cost_usd": 0.42, "session_id": "fake-session",
        "usage": {"input_tokens": 900, "output_tokens": 120},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
