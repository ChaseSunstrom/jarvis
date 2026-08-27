#!/usr/bin/env bash
# M96 — Stop means stop: a run is stopped at the server, and its end says so.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M96" "stop means stop"

check "the websocket has assist_pipeline/stop, the pipeline marks an interrupted run, the console and the mock send and answer it" python3 -c '
from pathlib import Path
ws = Path("jarvis-core/jarvis/api/websocket.py").read_text()
assert "\"assist_pipeline/stop\": WebSocketHandler._cmd_pipeline_stop" in ws
pipe = Path("jarvis-core/jarvis/voice/pipeline.py").read_text()
assert "self.interrupted = True" in pipe and "{\"interrupted\": True} if self.interrupted else {}" in pipe
client = Path("jarvis-web/src/lib/pipeline.ts").read_text()
assert "stopRun(): boolean" in client and "assist_pipeline/stop" in client
page = Path("jarvis-web/src/routes/+page.svelte").read_text()
assert "client?.stopRun();" in page
mock = Path("tests/web/mock-ha.mjs").read_text()
assert "case \x27assist_pipeline/stop\x27" in mock
assert "Stop means stop (M96)" in Path("jarvis-core/docs/clients.md").read_text()
print("stop: command, flag, console, mock, docs")
'
check_sh "the API suite: a run stopped mid-answer ends interrupted; a run not in progress is not_found" \
    'cd jarvis-core && python3 -m pytest tests/test_api.py -q --timeout=120 --timeout-method=signal -k "stopped_at_the_server or pipeline_run" 2>&1 | tail -1'

use_venv
check "on the house, a run stopped one second in ends with run-end interrupted" python3 -c '
import asyncio, json, os, time
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
async def main():
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        await ws.send(json.dumps({"id": 1, "type": "assist_pipeline/run", "start_stage": "intent", "end_stage": "intent",
                                  "input": {"text": "Tell me, in as many words as you like, everything you know about the house."},
                                  "conversation_id": "test:m96-stop"}))
        deadline = time.time() + 30
        started = False
        while time.time() < deadline:
            m = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if m.get("type") == "event" and m["event"]["type"] in ("intent-start", "intent-progress"):
                started = True; break
        assert started, "the run never started answering"
        await asyncio.sleep(1.0)
        await ws.send(json.dumps({"id": 2, "type": "assist_pipeline/stop", "run_id": 1}))
        stopped = None; end = None
        deadline = time.time() + 20
        while time.time() < deadline and (stopped is None or end is None):
            m = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if m.get("id") == 2 and m.get("type") == "result": stopped = m
            if m.get("type") == "event" and m["event"]["type"] == "run-end": end = m
        assert stopped and stopped.get("success"), stopped
        assert end and end["event"]["data"].get("interrupted") is True, end
        print("stopped; run-end", end["event"]["data"])
asyncio.run(main())
'

verify_end
