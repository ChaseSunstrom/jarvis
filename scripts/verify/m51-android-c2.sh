#!/usr/bin/env bash
# M51 — the phone, on the same look.
#
# The instrument on Canvas from the same geometry contract the web reads, the
# four states from the same palette, C2's buttons, panels and type on every
# native screen, and the goldens re-recorded so the JVM screenshot tests
# describe the new look rather than the old one. No device, no emulator.
source "$(dirname "$0")/lib.sh"
verify_begin "M51" "the phone, on the same look"
use_venv
export JAVA_HOME="${JAVA_HOME:-$HOME/.local/jdk}"
[ -d "$JAVA_HOME/bin" ] && export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

KT=android-app/app/src/main/kotlin/ai/jarvis/app
require_file tests/contracts/reactor_geometry.json
require_file $KT/ui/ReactorOrb.kt

check "the phone's reactor is the instrument, from the contract" python3 -c '
import json
from pathlib import Path
src = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt").read_text()
for need in ("drawBezel", "drawBlades", "drawCoil", "drawLevel", "drawLens"):
    assert need in src, f"ReactorOrb.kt has no {need}"
for gone in ("BLOB_FRACTION", "SPECULAR", "fresnel", "sphere normal"):
    assert gone not in src, f"ReactorOrb.kt still draws the sphere: {gone}"
print("bezel · blades · coil · level · lens")
'
check "the geometry mirror pins Kotlin to the contract" python3 android-app/tools/reactor_orb_test.py
check "the four states wear color.orb.* on the phone" python3 -c '
from pathlib import Path
src = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/ReactorOrb.kt").read_text()
assert "SiriPalette" in src or "JarvisTokens.Color.ORB" in src, "the reactor does not read the pinned palette"
print("palette from the tokens")
'
check_sh "generated files current; SiriPalette matches color.orb.*" 'python3 design/build.py --check 2>&1 | tail -2'

check "JarvisUi has no pills, ghosts or corner brackets" python3 -c '
from pathlib import Path
src = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/JarvisUi.kt").read_text()
for bad in ("fun pill(", "fun ghost(", "class CornerBrackets"):
    assert bad not in src, f"JarvisUi.kt still has {bad}"
for need in ("fun button(", "fun primary("):
    assert need in src, f"JarvisUi.kt has no {need}"
print("button · primary; hairline panels")
'
check_not "no screen draws corner brackets" grep -rq "CornerBrackets" $KT
check "the console frame's strip has the underline" grep -q "underline" $KT/ui/ConsoleFrame.kt
check "token lint: the phone is clean" python3 scripts/verify/token_lint.py --require-clean android-app/app/src/main/kotlin
check "the phone offers the same front doors as the browser" python3 android-app/tools/console_parity_test.py
check_sh "the Android mirrors" 'make -s test-android 2>&1 | tail -3'

check_sh ">= 8 golden screenshots, the four orb states among them" '
n=$(find android-app/app/src/test -name "*.png" | wc -l); [ "$n" -ge 8 ] || { echo "$n goldens"; exit 1; }
for s in idle listening thinking speaking; do ls android-app/app/src/test/screenshots/orb-$s.png >/dev/null || exit 1; done; echo "$n goldens"'
check_sh "./gradlew assembleDebug" \
    'cd android-app && timeout 1800 ./gradlew -q assembleDebug 2>&1 | tail -15 && ls app/build/outputs/apk/debug/*.apk'
check_sh "./gradlew testDebugUnitTest (JUnit + Robolectric)" \
    'cd android-app && timeout 1800 ./gradlew -q testDebugUnitTest 2>&1 | tail -15'
check_sh "./gradlew lintDebug (blocking)" 'cd android-app && timeout 1200 ./gradlew -q lintDebug 2>&1 | tail -15'
check_sh "./gradlew verifyRoborazziDebug (screenshots match goldens)" \
    'cd android-app && timeout 1200 ./gradlew -q verifyRoborazziDebug 2>&1 | tail -10'

check "the device backlog names what only a device can confirm about the look" python3 -c '
from pathlib import Path
text = Path("docs/ANDROID_DEVICE_TESTS.md").read_text()
assert "M51" in text, "no M51 rows in the device backlog"
print("M51 rows present")
'
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
