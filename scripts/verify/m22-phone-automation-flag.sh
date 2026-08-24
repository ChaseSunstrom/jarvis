#!/usr/bin/env bash
# M22 — phone automation: the interfaces are designed and scaffolded, the
# feature is behind a compile-time flag that is OFF, the runtime master switch
# defaults OFF, nothing is tested on a device, and every deferred check is in
# docs/ANDROID_DEVICE_TESTS.md.
source "$(dirname "$0")/lib.sh"
verify_begin "M22" "phone automation scaffolded and flagged OFF"
use_venv
KT=android-app/app/src/main/kotlin/ai/jarvis/app

check "compile-time flag PHONE_AUTOMATION exists" grep -qE 'buildConfigField\("boolean",\s*"PHONE_AUTOMATION"' android-app/app/build.gradle.kts
check "…and defaults to false" grep -qE 'PHONE_AUTOMATION"[^\n]*"false"' android-app/app/build.gradle.kts
for f in automation/accessibility/JarvisAccessibilityService.kt automation/notify/JarvisNotificationListener.kt automation/AutomationBridge.kt; do
    check "gated by the flag: $f" grep -q 'BuildConfig.PHONE_AUTOMATION' "$KT/$f"
done
check "runtime master switch defaults OFF" grep -qE 'automation_enabled[^\n]*false' "$KT/automation/policy/PolicyStore.kt"
check "the interface is scaffolded (interface PhoneAutomation)" grep -rqE 'interface PhoneAutomation' "$KT/automation"
require_file android-app/docs/phone-automation.md
require_file android-app/app/src/test/kotlin/ai/jarvis/app/automation/PhoneAutomationFlagTest.kt
check "python mirror of the flag test passes" python3 android-app/tools/phone_automation_flag_test.py
check "device backlog lists the checks deferred for enabling it" grep -q PHONE_AUTOMATION docs/ANDROID_DEVICE_TESTS.md
check "DEVIATIONS.md records the decision" grep -qi 'phone automation' DEVIATIONS.md
verify_end
