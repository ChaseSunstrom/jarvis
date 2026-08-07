#!/usr/bin/env bash
#
# Re-apply the Jarvis assistant role on a GrapheneOS device over adb.
#
# WHY THIS EXISTS: GrapheneOS clears the `assistant` and
# `voice_interaction_service` Secure Settings on every app reinstall or
# update. After each Obtainium update of the Jarvis companion app, run this
# script (USB debugging enabled) to make the assist gesture / long-press
# launch Jarvis again. Signature-protected app data survives updates; the
# role assignment does not.
#
# Component names match the jarvis flavor:
#   applicationId = io.homeassistant.companion.android.jarvis
#   classes live in io.homeassistant.companion.android.jarvis.* (manifest
#   namespace io.homeassistant.companion.android + .jarvis package dir).
#
set -euo pipefail

PKG="io.homeassistant.companion.android.jarvis"
ASSIST_ACTIVITY="$PKG/io.homeassistant.companion.android.jarvis.JarvisAssistActivity"
VIS_SERVICE="$PKG/io.homeassistant.companion.android.jarvis.JarvisVoiceInteractionService"

err() { echo "ERROR: $*" >&2; exit 1; }

command -v adb >/dev/null 2>&1 || err "adb not found on PATH. Install Android platform-tools."

STATE="$(adb get-state 2>/dev/null || true)"
if [ "$STATE" != "device" ]; then
    err "No device in 'device' state (adb get-state -> '${STATE:-none}').
Check: cable connected, USB debugging enabled, this host authorized on the phone ('adb devices')."
fi

if ! adb shell pm path "$PKG" >/dev/null 2>&1; then
    err "Package $PKG is not installed on the device. Install the jarvis-flavor APK first."
fi

echo "==> Setting assistant Secure Settings..."
adb shell settings put secure assistant "$ASSIST_ACTIVITY"
adb shell settings put secure voice_interaction_service "$VIS_SERVICE"
adb shell settings put secure assist_gesture_enabled 1

echo "==> Verifying..."
GOT_ASSISTANT="$(adb shell settings get secure assistant | tr -d '\r')"
GOT_VIS="$(adb shell settings get secure voice_interaction_service | tr -d '\r')"
GOT_GESTURE="$(adb shell settings get secure assist_gesture_enabled | tr -d '\r')"

echo "    assistant                 = $GOT_ASSISTANT"
echo "    voice_interaction_service = $GOT_VIS"
echo "    assist_gesture_enabled    = $GOT_GESTURE"

FAIL=0
[ "$GOT_ASSISTANT" = "$ASSIST_ACTIVITY" ] || { echo "MISMATCH: assistant (expected $ASSIST_ACTIVITY)" >&2; FAIL=1; }
[ "$GOT_VIS" = "$VIS_SERVICE" ] || { echo "MISMATCH: voice_interaction_service (expected $VIS_SERVICE)" >&2; FAIL=1; }
[ "$GOT_GESTURE" = "1" ] || { echo "MISMATCH: assist_gesture_enabled (expected 1)" >&2; FAIL=1; }

if [ "$FAIL" -ne 0 ]; then
    err "Role not fully applied. On GrapheneOS also check Settings > Apps > Default apps > Digital assistant app."
fi

echo "==> Done. Test with:"
echo "    adb shell am start -W -a android.intent.action.ASSIST"
