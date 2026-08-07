"""File-queue bridge to the network-less sandbox.

The sandbox runs with ``network_mode: none`` so HTTP is impossible by
design. The only crossover is the shared /workspace volume:

    /workspace/.exec/queue/<id>.json    orchestrator → sandbox (atomic rename)
    /workspace/.exec/results/<id>.json  sandbox → orchestrator

Only the orchestrator writes to queue/, and it does so exclusively for
requests the ExecGate reports as approved. The sandbox executes whatever is
in its queue — the trust boundary is the gate, and the sandbox's own
isolation bounds the blast radius if that ever failed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class SandboxQueue:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace) / ".exec"
        self.queue = self.root / "queue"
        self.results = self.root / "results"
        self.queue.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)

    def enqueue(self, request_id: str, command: str, timeout: int = 60) -> None:
        job = {"id": request_id, "command": command, "timeout": timeout}
        tmp = self.queue / f".{request_id}.tmp"
        tmp.write_text(json.dumps(job))
        os.rename(tmp, self.queue / f"{request_id}.json")  # atomic hand-off

    def wait_result(self, request_id: str, timeout: float = 120.0) -> dict:
        path = self.results / f"{request_id}.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                try:
                    result = json.loads(path.read_text())
                except json.JSONDecodeError:
                    time.sleep(0.05)  # writer mid-flight
                    continue
                path.unlink(missing_ok=True)
                return result
            time.sleep(0.2)
        return {
            "id": request_id,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"sandbox did not answer within {timeout:.0f}s "
            "(is jarvis-sandbox running?)",
            "timed_out": True,
        }
