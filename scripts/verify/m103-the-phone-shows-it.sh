#!/usr/bin/env bash
# M103 — The phone shows what the house puts up: the surface's panels and the
# task dock on the voice screen, live, in the phone's own vocabulary.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M103" "the phone shows what the house puts up"

check "the voice screen has a surface view and a task dock, fed by the device channel" python3 -c '
from pathlib import Path
act = Path("android-app/app/src/main/kotlin/ai/jarvis/app/JarvisAssistActivity.kt").read_text()
assert "SurfaceView(" in act and "TaskDockView(" in act
watch = Path("android-app/app/src/main/kotlin/ai/jarvis/app/surface/SurfaceWatch.kt").read_text()
assert "jarvis/surface/list" in watch and "jarvis_surface_changed" in watch and "jarvis/surface/remove" in watch
print("SurfaceView, TaskDockView, SurfaceWatch")
'
check "phone mirror: the surface on the phone" python3 android-app/tools/surface_on_the_phone_test.py
check "phone mirror: the strip's vocabulary is untouched" python3 android-app/tools/activity_mirror_test.py
if test -d "$HOME/.local/jdk" && test -d "$HOME/Android/Sdk" && test -x "$HOME/.local/gradle/bin/gradle"; then
    export JAVA_HOME="$HOME/.local/jdk" ANDROID_HOME="$HOME/Android/Sdk"
    export PATH="$JAVA_HOME/bin:$HOME/.local/gradle/bin:$PATH"
    check_sh "the Kotlin builds and its unit tests pass" \
        'cd android-app && gradle :app:assembleDebug :app:testDebugUnitTest --no-daemon -q 2>&1 | tail -3 && echo "assembleDebug, testDebugUnitTest"'
    check_sh "the voice screen's goldens, panels included" \
        'cd android-app && gradle :app:verifyRoborazziDebug --no-daemon -q --tests "ai.jarvis.app.screenshot.ScreenshotTest" 2>&1 | tail -3 && echo "goldens verified"'
else
    check_not "the Android toolchain is here (a JDK, the SDK, gradle)" false
fi
use_venv
check "on the house: a phone-shaped socket receives the surface event the screen draws from" python3 -c '
import asyncio, json, os, time
import websockets
def token():
    for line in open("jarvis-core/.env"):
        if line.startswith("JARVIS_TOKEN="):
            return line.split("=", 1)[1].strip().strip(chr(34))
    return ""
url = os.environ.get("JARVIS_URL", "http://127.0.0.1:8080").replace("http", "ws", 1) + "/api/websocket"
async def result(ws, n):
    # The reply with THIS id: a subscribed socket also receives events, and a
    # house with scenarios running on it changes its surface between two frames.
    deadline = time.time() + 30
    while time.time() < deadline:
        m = json.loads(await asyncio.wait_for(ws.recv(), 30))
        if m.get("id") == n and m.get("type") == "result":
            return m
    raise AssertionError("no result for %d" % n)
async def main():
    async with websockets.connect(url, max_size=None) as phone, websockets.connect(url, max_size=None) as other:
        for ws in (phone, other):
            await ws.recv(); await ws.send(json.dumps({"type": "auth", "access_token": token()}))
            assert json.loads(await ws.recv())["type"] == "auth_ok"
        await phone.send(json.dumps({"id": 1, "type": "jarvis/device/register", "device": {"id": "m103-phone", "name": "M103 phone", "platform": "android", "capabilities": ["device"], "app_version": "1.0.0", "actions": []}}))
        assert (await result(phone, 1)).get("success")
        await phone.send(json.dumps({"id": 2, "type": "subscribe_events", "event_type": "jarvis_surface_changed"}))
        assert (await result(phone, 2)).get("success")
        await phone.send(json.dumps({"id": 3, "type": "jarvis/surface/list"}))
        listing = await result(phone, 3); assert listing.get("success"), listing
        await other.send(json.dumps({"id": 1, "type": "jarvis/surface/place", "panel": {"kind": "entity", "entity": "light.bed_light", "title": "M103 probe"}}))
        shown = await result(other, 1); assert shown.get("success"), shown
        panel_id = shown["result"]["panel"]["id"]
        deadline = time.time() + 15; got = None
        while time.time() < deadline:
            m = json.loads(await asyncio.wait_for(phone.recv(), 15))
            if m.get("type") == "event" and m["event"].get("event_type") == "jarvis_surface_changed":
                data = m["event"].get("data") or {}
                if any(p.get("id") == panel_id for p in data.get("panels", [])):
                    got = data; break
        assert got, "the phone socket never saw the panel go up"
        await other.send(json.dumps({"id": 2, "type": "jarvis/surface/remove", "panel": panel_id}))
        assert (await result(other, 2)).get("success")
        print("the phone socket saw the panel go up:", panel_id)
asyncio.run(main())
'
verify_end
