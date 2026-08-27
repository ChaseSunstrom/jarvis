#!/usr/bin/env bash
# M98 — The phone keeps up: the speaker gate's mode reaches the phone, a held
# Tier-3 action is answered on its consent screen, a typed turn runs the same
# pipeline, and the tier contract records the phone's ask-once.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M98" "the phone keeps up"

check "the phone refreshes the speaker gate after every register, from /api/voice/speaker" python3 -c '
from pathlib import Path
host = Path("android-app/app/src/main/kotlin/ai/jarvis/app/channel/DeviceChannelHost.kt").read_text()
assert "private fun refreshSpeakerGate(" in host and "afterRegistered = { refreshSpeakerGate(" in host
assert "fresh.mode == \"enforce\" && fresh.enrolled" in host
channel = Path("android-app/app/src/main/kotlin/ai/jarvis/app/channel/JarvisChannel.kt").read_text()
assert "afterRegistered: (() -> Unit)?" in channel and "afterRegistered?.invoke()" in channel
client = Path("android-app/app/src/main/kotlin/ai/jarvis/app/config/VoiceIdentityClient.kt").read_text()
assert "get(\"/api/voice/speaker\")" in client
print("register -> refreshSpeakerGate -> /api/voice/speaker -> speakerGateEnforcing")
'
check "a held Tier-3 action reaches the consent screen and is answered over jarvis/approve" python3 -c '
from pathlib import Path
bridge = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/ApprovalBridge.kt").read_text()
assert "fun raiseServerRequest(" in bridge and "serverSenders" in bridge
convo = Path("android-app/app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt").read_text()
assert "ApprovalBridge.raiseServerRequest(" in convo
pipe = Path("android-app/app/src/main/kotlin/ai/jarvis/app/assist/AssistPipelineClient.kt").read_text()
assert "fun sendCommand(type: String" in pipe
assert "jarvis/approve" in convo or "jarvis/approve" in bridge
print("approval_request -> consent screen -> jarvis/approve")
'
check "a typed field on the voice screen sends the text down the same pipeline" python3 -c '
from pathlib import Path
act = Path("android-app/app/src/main/kotlin/ai/jarvis/app/JarvisAssistActivity.kt").read_text()
assert "typedView = EditText(this)" in act and "EditorInfo.IME_ACTION_SEND" in act and "sendTyped(" in act
convo = Path("android-app/app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt").read_text()
assert "fun sendTyped(text: String)" in convo and "AssistPipelineClient.StartStage.INTENT" in convo
print("EditText -> sendTyped -> StartStage.INTENT")
'
check "the tier contract records the phone asking once on tier 2, and both sides read it" python3 -c '
import json
from pathlib import Path
c = json.loads(Path("tests/contracts/tool_tiers.json").read_text())
text = json.dumps(c)
assert "phone_asks_once_on_tier_2" in text and "The phone asks ONCE for a tier-2 action" in text
assert "phone_asks_once_on_tier_2" in Path("android-app/tools/tool_tiers_test.py").read_text()
print("tool_tiers.json: the phone variant, read by the mirror")
'
check "phone mirror: the on-device turn" python3 android-app/tools/on_device_turn_test.py
check "phone mirror: a prompt reaches the person it was raised for (20 checks, the twentieth M98)" python3 android-app/tools/prompt_reaches_the_user_test.py
check "phone mirror: tool tiers" python3 android-app/tools/tool_tiers_test.py
use_venv
check_sh "the tier contract's server half agrees, a nested bundle reaches the phone intact, the shipped skill loads" \
    'cd jarvis-core && python3 -m pytest tests/test_tool_tiers_contract.py tests/test_device_control.py tests/test_skills.py -q --timeout=120 --timeout-method=signal -k "contract or nested_bundle or phone_tasks_skill or tiers" 2>&1 | tail -1'
check "PHONE TASKS' way in: import_tasks and list_tasks on the phone, the phone-tasks skill on the house" python3 -c '
from pathlib import Path
kt = Path("android-app/app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/TaskActions.kt").read_text()
assert "object ImportPhoneTasks : JarvisAction" in kt and "override val tier = ActionTier.CONFIRM" in kt
assert "import(bundle, fromServer = true)" in kt
skill = Path("jarvis-core/config/skills/phone-tasks/SKILL.md").read_text()
assert skill.startswith("---\nname: phone-tasks\n") and "import_tasks" in skill
print("import_tasks (tier 3), list_tasks, phone-tasks skill")
'
check "phone mirror: phone tasks" python3 android-app/tools/phone_tasks_test.py
check "docs/verification.md carries the twentieth check and the milestone" python3 -c '
from pathlib import Path
doc = Path("docs/verification.md").read_text()
assert "prompt_reaches_the_user_test.py` (20 checks)" in doc or "(20 checks)" in doc
assert "M98" in doc
print("verification.md: 20 checks, M98")
'

# --- the Kotlin itself, when the toolchain is here (one gradle at a time on this box) ---------
if test -d "$HOME/.local/jdk" && test -d "$HOME/Android/Sdk" && test -x "$HOME/.local/gradle/bin/gradle"; then
    export JAVA_HOME="$HOME/.local/jdk" ANDROID_HOME="$HOME/Android/Sdk"
    export PATH="$JAVA_HOME/bin:$HOME/.local/gradle/bin:$PATH"
    check_sh "the Kotlin builds and its unit tests pass" \
        'cd android-app && gradle :app:assembleDebug :app:testDebugUnitTest --no-daemon -q 2>&1 | tail -3 && echo "assembleDebug, testDebugUnitTest"'
else
    check_not "the Android toolchain is here (a JDK, the SDK, gradle)" false
fi

# --- the house ------------------------------------------------------------------------------
check "on the house, /api/voice/speaker answers the phone with a mode and whether a voice is enrolled" python3 -c '
import json, os, urllib.request
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080") + "/api/voice/speaker"
req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token()})
body = json.load(urllib.request.urlopen(req, timeout=10))
assert body.get("mode") in ("off", "observe", "enforce"), body
assert isinstance(body.get("enrolled"), bool), body
print("mode", body["mode"], "enrolled", body["enrolled"])
'
check "on the house, a typed turn from the phone (start_stage intent) is answered" python3 -c '
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
                                  "input": {"text": "What is the time, in one short sentence?"},
                                  "conversation_id": "test:m98-typed"}))
        deadline = time.time() + 60; reply = None
        while time.time() < deadline:
            m = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if m.get("type") == "event" and m["event"]["type"] == "intent-end":
                reply = m["event"]["data"]["intent_output"]["response"]["speech"]["plain"]["speech"]; break
        assert reply and reply.strip(), "no intent-end with a reply"
        print("typed:", reply.strip()[:90])
asyncio.run(main())
'
check "on the house, a phone that registers import_tasks is sent a task bundle for a spoken-style request, and the skill is what taught it" python3 -c '
import asyncio, json, os, time, uuid
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
MANIFEST = [
    {"id": "import_tasks", "tier": 3, "capability": "automation", "available": True,
     "description": "Install one or more tasks on this phone (automations it runs by itself). A task with an action that needs confirming arrives switched off.",
     "params": {"bundle": "object: {version: 1, tasks: [...]} in the phone task format (see the phone-tasks skill)", "task": "object: a single task"}},
    {"id": "list_tasks", "tier": 1, "capability": "automation", "available": True, "description": "List the tasks on this phone", "params": {}},
    {"id": "toggle_torch", "tier": 1, "capability": "device_settings", "available": True, "description": "Turn the camera flash (torch) on or off.", "params": {"on": "bool"}},
]
async def auth(ws):
    await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
    assert json.loads(await ws.recv())["type"] == "auth_ok"
async def main():
    device_id = "test-phone-" + uuid.uuid4().hex[:6]
    async with websockets.connect(url, max_size=None) as dev, websockets.connect(url, max_size=None) as user:
        await auth(dev); await auth(user)
        await dev.send(json.dumps({"id": 1, "type": "jarvis/device/register", "device": {
            "id": device_id, "name": "Test phone", "platform": "android",
            "capabilities": ["device", "automation"], "app_version": "1.0.0", "actions": MANIFEST}}))
        reg = json.loads(await asyncio.wait_for(dev.recv(), 10)); assert reg.get("success"), reg
        await user.send(json.dumps({"id": 1, "type": "assist_pipeline/run", "start_stage": "intent", "end_stage": "intent",
            "input": {"text": "Set up a task on my Test phone: whenever it is plugged in, turn its torch on."},
            "conversation_id": "test:m98-phone-task-" + device_id}))
        async def phone():
            deadline = time.time() + 150
            while time.time() < deadline:
                m = json.loads(await asyncio.wait_for(dev.recv(), 150))
                if m.get("type") == "device_command":
                    return m
            raise AssertionError("no device_command reached the phone")
        cmd = await phone()
        assert cmd["action"] == "import_tasks", cmd
        assert cmd["tier"] == 3, cmd
        bundle = cmd["params"].get("bundle") or {"tasks": [cmd["params"].get("task")]}
        tasks = bundle.get("tasks") or []
        assert tasks and isinstance(tasks[0], dict), cmd["params"]
        task = tasks[0]
        triggers = [t if isinstance(t, str) else t.get("type") for t in task.get("triggers") or []]
        assert "power_connected" in triggers, task
        actions = [s.get("action") for s in task.get("steps") or [] if isinstance(s, dict)]
        assert "toggle_torch" in actions, task
        # The fake phone plays the person tapping yes on the consent screen.
        await dev.send(json.dumps({"type": "device_result", "command_id": cmd["command_id"], "status": "ok",
                                   "result": {"imported": 1, "held_for_consent": 0, "tasks": [{"id": task.get("id"), "name": task.get("name"), "enabled": True}]}}))
        reply = None; deadline = time.time() + 90
        while time.time() < deadline:
            m = json.loads(await asyncio.wait_for(user.recv(), 90))
            if m.get("type") == "event" and m["event"]["type"] == "intent-end":
                reply = m["event"]["data"]["intent_output"]["response"]["speech"]["plain"]["speech"]; break
        assert reply, "no reply after the import"
        print("task", task.get("id"), "triggers", triggers, "->", reply.strip()[:100])
asyncio.run(main())
'
verify_end
