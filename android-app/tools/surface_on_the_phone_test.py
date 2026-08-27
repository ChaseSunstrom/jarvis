#!/usr/bin/env python3
"""The house's surface on the phone (M103), pinned from the outside.

The console draws every kind of panel as an instrument; the phone says each
one in a line. What must not drift: the kinds the phone knows are the
server's KINDS (integrations/surface), the frames it sends are the server's
names (websocket.py's handlers), the voice screen actually adds the two views,
and the device channel subscribes before it lists (the rule every watch on the
phone follows, so nothing that arrives between the two is lost).

Run:  python3 android-app/tools/surface_on_the_phone_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app"
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))


def main() -> int:
    watch = (APP / "surface/SurfaceWatch.kt").read_text()
    server = (ROOT / "jarvis-core/jarvis/integrations/surface/__init__.py").read_text()
    server_kinds = set(re.search(r'KINDS = \(([^)]*)\)', server).group(1).replace('"', "").replace(" ", "").split(","))
    phone_kinds = set(re.search(r'val KINDS: Set<String> = setOf\(([^)]*)\)', watch).group(1).replace('"', "").replace(" ", "").split(","))
    check("the phone's kinds are the server's KINDS", phone_kinds == server_kinds, f"phone {sorted(phone_kinds)} server {sorted(server_kinds)}")
    line_fn = watch.split("fun line(", 1)[1]
    said = set(re.findall(r'^\s*"([a-z]+)" ->', line_fn, re.M))
    check("every kind has a line of its own", server_kinds <= said, str(sorted(server_kinds - said)))

    ws = (ROOT / "jarvis-core/jarvis/api/websocket.py").read_text()
    for const, frame in (("TYPE_LIST", "jarvis/surface/list"), ("TYPE_REMOVE", "jarvis/surface/remove")):
        check(f"{const} is a frame the server answers", f'const val {const} = "{frame}"' in watch and f'"{frame}"' in ws, frame)
    check("the event is the server's", 'const val EVENT = "jarvis_surface_changed"' in watch and "jarvis_surface_changed" in server)
    check("remove names the panel as the server does (`panel`)", 'JSONObject().put("panel", panelId)' in watch and 'msg.get("panel")' in ws.split("_cmd_surface_remove", 1)[1][:200])

    channel = (APP / "channel/JarvisChannel.kt").read_text()
    body = channel.split("private fun watchSurface", 1)[1][:800]
    check("the channel subscribes before it lists", body.index("SurfaceWatch.EVENT") < body.index("SurfaceWatch.TYPE_LIST"))
    check("an event that is neither a task nor a moment reaches the surface", "SurfaceWatch.onEvent(msg)" in channel)

    activity = (APP / "JarvisAssistActivity.kt").read_text()
    check("the voice screen has the surface view", "SurfaceView(this)" in activity and "root.addView(surfaceView" in activity)
    check("the voice screen has the task dock", "TaskDockView(this)" in activity and "root.addView(taskDockView" in activity)
    check("the screen listens while it is started and stops when it stops", "SurfaceWatch.listen" in activity and "TaskWatch.listen" in activity and "unlistenSurface" in activity)
    check("a dismissed panel goes through the channel's remove", "SurfaceWatch.TYPE_REMOVE" in activity and "SurfaceWatch.removeArgs(" in activity)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok    " if ok else "  FAIL  ") + name + (f"\n        | {detail}" if not ok and detail else ""))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
