#!/usr/bin/env bash
# M61 — Android: the equal of the web, and of Tasker.
#
# Two halves. The phone's screens are the console's (the voice screen with
# the graph and the activity strip, motion on Canvas from the same state
# machine), and the phone can do what Tasker can — measured against
# docs/ANDROID_TASKER_PARITY.md, whose "done" rows must name actions that
# exist in the registry. Build, unit, lint and goldens only: never a device.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M61" "the phone: the equal of the web, and of Tasker"

KT=android-app/app/src/main/kotlin/ai/jarvis/app
require_file docs/ANDROID_TASKER_PARITY.md
require_file tests/contracts/activity_rows.json
require_file tests/contracts/reactor_geometry.json

# --- Tasker parity, measured against the registry -----------------------------
check "every 'done' row in the parity table names an action that exists" python3 -c '
import re
from pathlib import Path
doc = Path("docs/ANDROID_TASKER_PARITY.md").read_text()
kt = Path("android-app/app/src/main/kotlin/ai/jarvis/app/automation")
ids = set(re.findall(r"override val id\s*=\s*\"([a-z][a-z0-9_.]+)\"", "".join(p.read_text() for p in kt.rglob("*.kt"))))
app = Path("android-app/app/src/main/kotlin")
# Conditions (app_foreground, screen_on/off), the companion say mode and the ui_* ids the accessibility agent
# registers are string ids anywhere in the app, not override val ids.
ids |= set(re.findall(r"\"(ui_[a-z_]+|take_screenshot|app_foreground|screen_on|screen_off|say)\"", "".join(p.read_text() for p in app.rglob("*.kt"))))
done = gap = 0; missing = []
for line in doc.splitlines():
    if not line.startswith("|") or "| done |" not in line and "| gap |" not in line: continue
    cells = [c.strip() for c in line.strip("|").split("|")]
    status = cells[-1]
    if status == "gap": gap += 1; continue
    if status != "done": continue
    done += 1
    named = re.findall(r"`([a-z][a-z0-9_.]+)`", cells[1])
    if not named: continue  # a component, not an action (triggers, the task engine)
    for name in named:
        if name not in ids: missing.append(name)
assert not missing, f"done rows name actions the registry does not have: {sorted(set(missing))}"
assert gap == 0, f"{gap} row(s) still marked gap in docs/ANDROID_TASKER_PARITY.md"
print(f"{done} Tasker rows done, none open")
'
check "every gap that became an action has a unit test" python3 -c '
import re
from pathlib import Path
tests = "".join(p.read_text() for p in Path("android-app/app/src/test").rglob("*.kt"))
doc = Path("docs/ANDROID_TASKER_PARITY.md").read_text()
closed = []
for line in doc.splitlines():
    if not line.startswith("|") or "| done |" not in line or "M61" not in line: continue
    cells = [c.strip() for c in line.strip("|").split("|")]
    closed += re.findall(r"`([a-z][a-z0-9_]+)`", cells[1])[:1]
assert closed, "no row says M61 closed it"
for name in closed:
    assert name in tests, f"no unit test mentions {name}"
print(f"{len(closed)} rows closed by M61, each with a unit test")
'

# --- the phone looks like the web ---------------------------------------------
check "the phone draws the activity strip from the shared vocabulary" test -f android-app/tools/activity_mirror_test.py
check "the activity mirror agrees with the contract" python3 android-app/tools/activity_mirror_test.py
check "the voice screen has the graph and the activity strip" bash -c "grep -rqE 'ActivityStrip|activityStrip' $KT/ui && grep -rqE 'KnowledgeGraph|knowledgeGraph' $KT/ui"
check "the reactor sweeps, beats and irises on the phone (the M53 vocabulary)" bash -c "grep -qE 'sweep' $KT/ui/ReactorOrb.kt && grep -qE 'speak|cadence' $KT/ui/ReactorOrb.kt && grep -qE 'iris|looking' $KT/ui/ReactorOrb.kt"
check "the geometry mirror still pins Kotlin to the contract" python3 android-app/tools/reactor_orb_test.py
check "token lint: the phone is clean" python3 scripts/verify/token_lint.py --require-clean android-app/app/src/main/
check_sh "the Android mirrors" 'make -s test-android 2>&1 | tail -3'
check_sh ">= 12 golden screenshots, the voice screen with its strip among them" '
n=$(ls android-app/app/src/test/screenshots/*.png | wc -l); test "$n" -ge 12 && ls android-app/app/src/test/screenshots/ | grep -qE "voice-(activity|graph)" && echo "$n goldens"'

# --- built, tested, linted, no device -----------------------------------------
check_sh "./gradlew assembleDebug" 'cd android-app && timeout 1500 ./gradlew -q assembleDebug 2>&1 | tail -5'
check_sh "./gradlew testDebugUnitTest" 'cd android-app && timeout 1500 ./gradlew -q testDebugUnitTest 2>&1 | tail -8'
check_sh "./gradlew lintDebug (blocking)" 'cd android-app && timeout 1200 ./gradlew -q lintDebug 2>&1 | tail -8'
check_sh "./gradlew verifyRoborazziDebug" 'cd android-app && timeout 1200 ./gradlew -q verifyRoborazziDebug 2>&1 | tail -5'
check "the device backlog names what only a handset can confirm about M61" python3 -c '
from pathlib import Path
doc = Path("docs/ANDROID_DEVICE_TESTS.md").read_text()
assert "M61" in doc, "no ADT row for M61"
print("ADT rows for M61 present")
'

verify_end
