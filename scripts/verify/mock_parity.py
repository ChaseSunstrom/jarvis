#!/usr/bin/env python3
"""The mock cannot drift from the server unnoticed (M101).

Every list command the server answers is sent to the running house AND to the
console's mock backend, and every key the server puts on a payload — at the
top level and on the first row of every list inside it — that the mock's
answer lacks is reported. A key the mock has and the server does not is
listed too, quieter: the console may read it and the server never sends it.

Why: the approvals banner never seeded from a real server and the n8n line
said "connected" to a house with no n8n, and neither test caught it because
the mock encoded the console's assumption. A key the mock never carried is a
key no console test can see.

What it does NOT prove: values. Shape only — that the console's tests are
looking at the same fields the house sends.

Usage: python3 scripts/verify/mock_parity.py [--live URL] [--mock-port N]
Exit 1 when the mock lacks a key the server sent; 0 otherwise. Lists the
server answered with no rows are reported as "no row to compare" and do not
count against the mock — the comparison is only as deep as the house's data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".venv/lib/python3.11/site-packages"))

try:
    import websockets
except ImportError:  # pragma: no cover - the venv has it
    print("websockets is not installed; run under the repo venv", file=sys.stderr)
    sys.exit(2)

#: Every list command the server answers (websocket.py's _HANDLERS) plus the
#: states. The M101 gate fails when the server gains one this does not send.
COMMANDS: list[str] = [
    "config/area_registry/list", "config/automation/list", "config/companion/list",
    "config/device_registry/list", "config/entity_registry/list", "config/settings/list",
    "config/token/list", "config/tool/list", "jarvis/code/list", "jarvis/conversation/list",
    "jarvis/dashboards/list", "jarvis/extensions/list", "jarvis/mcp/list", "jarvis/memory/list",
    "jarvis/notes/list", "jarvis/notifications/list", "jarvis/schedule/list", "jarvis/skills/list",
    "jarvis/surface/list", "jarvis/tasks/list", "jarvis/tools/list", "jarvis/traces/list",
    "get_states",
]

#: Keys whose presence is a matter of the house, not the shape — reported but
#: never counted against the mock.
OPTIONAL: dict[str, set[str]] = {
    "jarvis/tools/list": {"catalogue"},
}


def token() -> str:
    for line in (ROOT / "jarvis-core/.env").read_text().splitlines():
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("JARVIS_TOKEN", "")


async def ask(url: str, access_token: str, command: str, timeout: float = 20.0):
    async with websockets.connect(url, max_size=None) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if first.get("type") == "auth_required":
            await ws.send(json.dumps({"type": "auth", "access_token": access_token}))
            ok = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if ok.get("type") != "auth_ok":
                raise RuntimeError(f"auth refused: {ok}")
        await ws.send(json.dumps({"id": 1, "type": command}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if frame.get("id") == 1 and frame.get("type") == "result":
                if not frame.get("success"):
                    return {"__error__": frame.get("error")}
                return frame.get("result")
        raise TimeoutError(command)


def shape(payload) -> tuple[set[str], dict[str, set[str]]]:
    """Top-level keys, and the keys of the first row of every list inside."""
    top: set[str] = set()
    rows: dict[str, set[str]] = {}
    if isinstance(payload, dict):
        top = set(payload.keys())
        for key, value in payload.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows[key] = set(value[0].keys())
    elif isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            rows["(list)"] = set(payload[0].keys())
    return top, rows


def start_mock(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["node", str(ROOT / "tests/web/mock-ha.mjs"), str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    import socket

    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("the mock did not start")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", default=os.environ.get("JARVIS_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--mock-port", type=int, default=8397)
    args = parser.parse_args()
    live_ws = args.live.replace("http", "ws", 1) + "/api/websocket"
    mock = start_mock(args.mock_port)
    mock_ws = f"ws://127.0.0.1:{args.mock_port}/api/websocket"
    missing_total = 0
    try:
        for command in COMMANDS:
            try:
                served = await ask(live_ws, token(), command)
            except Exception as err:  # noqa: BLE001 - reported per command
                print(f"  ?     {command}: the house did not answer ({err})")
                continue
            try:
                mocked = await ask(mock_ws, "test-token", command)
            except Exception as err:  # noqa: BLE001
                print(f"  FAIL  {command}: the mock did not answer ({err})")
                missing_total += 1
                continue
            if isinstance(served, dict) and "__error__" in served:
                print(f"  ?     {command}: the house refused: {served['__error__']}")
                continue
            if isinstance(mocked, dict) and "__error__" in mocked:
                print(f"  FAIL  {command}: the mock refused: {mocked['__error__']}")
                missing_total += 1
                continue
            s_top, s_rows = shape(served)
            m_top, m_rows = shape(mocked)
            optional = OPTIONAL.get(command, set())
            lacks = sorted((s_top - m_top) - optional)
            extra = sorted(m_top - s_top)
            row_lacks: dict[str, list[str]] = {}
            no_rows: list[str] = []
            for key, keys in s_rows.items():
                if key in m_rows:
                    diff = sorted(keys - m_rows[key])
                    if diff:
                        row_lacks[key] = diff
                else:
                    no_rows.append(key)
            served_empty = [k for k, v in (served.items() if isinstance(served, dict) else []) if isinstance(v, list) and not v]
            flag = "FAIL" if lacks or row_lacks else "ok  "
            if lacks or row_lacks:
                missing_total += len(lacks) + sum(len(v) for v in row_lacks.values())
            detail = []
            if lacks:
                detail.append(f"mock lacks top-level {lacks}")
            for key, diff in row_lacks.items():
                detail.append(f"mock rows of {key!r} lack {diff}")
            if extra:
                detail.append(f"mock sends extra {extra}")
            if served_empty:
                detail.append(f"no row to compare in {served_empty}")
            print(f"  {flag}  {command}" + (": " + "; ".join(detail) if detail else ""))
    finally:
        mock.kill()
    print(f"\nmock parity: {missing_total} key(s) the server sends and the mock lacks")
    return 1 if missing_total else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
