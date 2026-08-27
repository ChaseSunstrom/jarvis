#!/usr/bin/env bash
# M94 — In here: the device a request came from reaches the model and the tools.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M94" "in here"

check "the device rides from the pipeline through converse to the prompt, and is remembered for the tools" python3 -c '
from pathlib import Path
pipe = Path("jarvis-core/jarvis/voice/pipeline.py").read_text()
assert "def device_facts(self)" in pipe and "extra[\"device\"] = self.device_facts()" in pipe
agent = Path("jarvis-core/jarvis/llm/agent.py").read_text()
assert "device: dict[str, Any] | None = None," in agent and "def device_line(self" in agent and "remember_device(self.jarvis, context, device)" in agent
devices = Path("jarvis-core/jarvis/api/devices.py").read_text()
assert "class RequestDevices" in devices and "def device_of(" in devices
control = Path("jarvis-core/jarvis/integrations/device_control/__init__.py").read_text()
assert "_asking_device(context)" in control
print("pipeline -> converse -> prompt line; remembered; tell_user prefers it")
'
check_pytest "the agent: one line naming the device, after the speaker, none for a turn from no device" 'cd jarvis-core && python3 -m pytest tests/test_llm.py -q --timeout=120 --timeout-method=signal -k device_asked'
check_pytest "the pipeline hands the device to a converse that takes it and leaves one that cannot alone" 'cd jarvis-core && python3 -m pytest tests/test_voice.py -q --timeout=120 --timeout-method=signal -k asking_device'
check_pytest "tell_user goes to the device that asked unless one is named" 'cd jarvis-core && python3 -m pytest tests/test_device_control.py -q --timeout=120 --timeout-method=signal -k tell_user'

use_venv
check "on the house, a registered device asks which device it is on, and is told its own name" python3 -c '
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
        await ws.send(json.dumps({"id": 1, "type": "jarvis/device/register", "device": {"id": "verify-m94-tablet", "name": "Verification tablet", "platform": "test", "capabilities": ["ask"]}}))
        reg = json.loads(await ws.recv())
        assert reg.get("success"), reg
        await ws.send(json.dumps({"id": 2, "type": "assist_pipeline/run", "start_stage": "intent", "end_stage": "intent",
                                  "input": {"text": "What is the name of the device I am speaking to you from right now? Answer with its name only."},
                                  "conversation_id": "test:m94-in-here"}))
        reply = ""
        deadline = time.time() + 60
        while time.time() < deadline:
            m = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if m.get("type") != "event": continue
            e = m["event"]
            if e["type"] == "intent-end":
                reply = e["data"]["intent_output"]["response"]["speech"]["plain"]["speech"]
            if e["type"] == "run-end": break
        assert "verification tablet" in reply.lower(), reply
        print("told:", reply[:100])
asyncio.run(main())
'

check_sh "on the house: the rig as a tablet in the kitchen, and 'in here' means the kitchen" \
    'LIVE_ONLY=in-here-by-voice timeout 900 bash scripts/verify/live_interaction.sh --full 2>&1 | grep -v onnxruntime | tail -4'
verify_end
