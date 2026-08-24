#!/usr/bin/env bash
# M08 — Android proven on this host with no device: the toolchain lives under
# $HOME, ./gradlew assembleDebug / testDebugUnitTest / lintDebug (blocking) /
# Roborazzi screenshots all pass, the Compose theme is generated from the token
# source, and every device-only check is written down in
# docs/ANDROID_DEVICE_TESTS.md.
source "$(dirname "$0")/lib.sh"
verify_begin "M08" "android: headless build, tests, blocking lint, JVM screenshots, device-test backlog"
use_venv
use_local_bin
export JAVA_HOME="${JAVA_HOME:-$HOME/.local/jdk}"
[ -d "$JAVA_HOME/bin" ] && export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"

check_sh "JDK 17+ (java -version)" 'java -version 2>&1 | grep -qE "version \"(17|2[0-9])"'
check "Android SDK platform 35" test -d "$ANDROID_HOME/platforms/android-35"
check_sh "Android build-tools installed" 'ls -d "$ANDROID_HOME"/build-tools/*/ >/dev/null'
require_exec android-app/gradlew
require_file android-app/gradle/wrapper/gradle-wrapper.jar
check "compose enabled" grep -qE 'compose\s*=\s*true' android-app/app/build.gradle.kts
check "lint is blocking" grep -qE 'abortOnError\s*=\s*true' android-app/app/build.gradle.kts
check_not "CI no longer tolerates lint failures" grep -nE 'lintDebug.*\|\| true|continue-on-error: true' .github/workflows/android-apk.yml
check "Robolectric in the catalog" grep -qi robolectric android-app/gradle/libs.versions.toml
check "Roborazzi (JVM screenshots) in the catalog" grep -qi roborazzi android-app/gradle/libs.versions.toml
check_sh ">= 5 golden screenshots recorded on the JVM" '[ "$(find android-app/app/src/test -name "*.png" | wc -l)" -ge 5 ]'
check_sh "./gradlew assembleDebug" \
    'cd android-app && timeout 1800 ./gradlew -q assembleDebug 2>&1 | tail -15 && ls app/build/outputs/apk/debug/*.apk'
check_sh "./gradlew testDebugUnitTest (JUnit + Robolectric)" \
    'cd android-app && timeout 1800 ./gradlew -q testDebugUnitTest 2>&1 | tail -15'
check_sh "./gradlew lintDebug (blocking)" 'cd android-app && timeout 1200 ./gradlew -q lintDebug 2>&1 | tail -15'
check_sh "./gradlew verifyRoborazziDebug (screenshots match goldens)" \
    'cd android-app && timeout 1200 ./gradlew -q verifyRoborazziDebug 2>&1 | tail -10'
check "token lint: android-app/app/src/main/kotlin has no hard-coded value left (baseline empty)" python3 scripts/verify/token_lint.py --require-clean android-app/app/src/main/kotlin
check "Python mirrors still pass" make -s test-android
require_file docs/ANDROID_DEVICE_TESTS.md
check "device-test backlog has the columns" grep -qE '^\| *ID *\| *Area *\| *Check *\| *Why device-only *\|' docs/ANDROID_DEVICE_TESTS.md
check_sh "device-test backlog has >= 20 entries" '[ "$(grep -cE "^\| *ADT-[0-9]{3} " docs/ANDROID_DEVICE_TESTS.md)" -ge 20 ]'
verify_end
