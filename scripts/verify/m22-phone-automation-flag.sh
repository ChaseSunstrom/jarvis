#!/usr/bin/env bash
# M22 — phone automation: the interfaces are designed and scaffolded, the
# feature is behind a compile-time flag that is OFF, the runtime master switch
# defaults OFF, nothing is tested on a device, and every deferred check is in
# docs/ANDROID_DEVICE_TESTS.md.
source "$(dirname "$0")/lib.sh"
verify_begin "M22" "phone automation scaffolded and flagged OFF"
use_venv
KT=android-app/app/src/main/kotlin/ai/jarvis/app

# Asked of the file rather than of one line of it: the declaration is spread
# over five lines (a `findProperty(...) ?: "false"` with the reasoning above
# it), and a grep for both strings on one line said the flag was missing while
# the flag was right there. `phone_automation_flag_test.py` makes the same two
# assertions properly and is run below as well.
check "compile-time flag PHONE_AUTOMATION exists" \
    grep -q '"PHONE_AUTOMATION"' android-app/app/build.gradle.kts
check "…and defaults to false" python3 -c '
import re
from pathlib import Path
text = Path("android-app/app/build.gradle.kts").read_text()
tail = text[text.index("\"PHONE_AUTOMATION\""):][:300]
assert re.search(r"\?:\s*\"false\"", tail), tail[:200]
'
for f in automation/accessibility/JarvisAccessibilityService.kt automation/notify/JarvisNotificationListener.kt automation/AutomationBridge.kt; do
    check "gated by the flag: $f" grep -q 'BuildConfig.PHONE_AUTOMATION' "$KT/$f"
done
# The default lives on the `getBoolean(KEY_ENABLED, …)` line, which is nowhere
# near the string "automation_enabled" — that is the KEY's declaration, in the
# companion object at the bottom of the file.
check "runtime master switch defaults OFF" python3 -c '
import re
from pathlib import Path
text = Path("android-app/app/src/main/kotlin/ai/jarvis/app/automation/policy/PolicyStore.kt").read_text()
assert re.search(r"getBoolean\(KEY_ENABLED,\s*false\)", text), "the master switch defaults ON"
assert re.search(r"KEY_ENABLED = \"automation_enabled\"", text), "the key was renamed"
'
check "the interface is scaffolded (interface PhoneAutomation)" grep -rqE 'interface PhoneAutomation' "$KT/automation"
require_file android-app/docs/phone-automation.md
require_file android-app/app/src/test/kotlin/ai/jarvis/app/automation/PhoneAutomationFlagTest.kt
check "python mirror of the flag test passes" python3 android-app/tools/phone_automation_flag_test.py
check "device backlog lists the checks deferred for enabling it" grep -q PHONE_AUTOMATION docs/ANDROID_DEVICE_TESTS.md
check "DEVIATIONS.md records the decision" grep -qi 'phone automation' DEVIATIONS.md
# No live scenarios of its own — this milestone does not add a capability
# anybody talks to. What it must not do is break the ones that exist, so a
# named smoke subset runs: house-light-on, chat-context-retention, lock-needs-a-human.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
