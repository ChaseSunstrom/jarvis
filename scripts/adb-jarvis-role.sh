#!/usr/bin/env bash
#
# Re-apply the Jarvis assistant role on a GrapheneOS device over adb.
#
# WHY THIS EXISTS: GrapheneOS clears the `assistant` and
# `voice_interaction_service` Secure Settings on every app reinstall or
# update. After each Obtainium update of the Jarvis app, run this script (USB
# debugging enabled) to make the assist gesture / power long-press launch
# Jarvis again. Signature-protected app data survives updates; the role
# assignment does not.
#
# The app is `android-app/` — a standalone build, not a Home Assistant fork.
# Debug and release deliberately share one applicationId (ai.jarvis.app) so
# the commands below are the same either way.
#
# Override the package with `JARVIS_PKG=<id> scripts/adb-jarvis-role.sh`, or
# pass it as the first argument.
#
set -euo pipefail

DEFAULT_PKG="ai.jarvis.app"
# Component names are relative to the package, and the two do NOT share a
# sub-package: the activity sits at the root, the service under .assist.
ASSIST_CLASS=".JarvisAssistActivity"
VIS_CLASS=".assist.JarvisVoiceInteractionService"

err() { echo "ERROR: $*" >&2; exit 1; }

command -v adb >/dev/null 2>&1 || err "adb not found on PATH. Install Android platform-tools."

STATE0="$(adb get-state 2>/dev/null || true)"
[ "$STATE0" = "device" ] || err "No device in 'device' state (adb get-state -> '${STATE0:-none}').
Check: cable connected, USB debugging enabled, this host authorized ('adb devices')."

# Resolve the installed package: explicit override, else the default.
PKG="${JARVIS_PKG:-${1:-$DEFAULT_PKG}}"
adb shell pm path "$PKG" >/dev/null 2>&1 || err "Package '$PKG' is not installed.
Build and install the APK first (see android-app/README.md), or set JARVIS_PKG."
echo "==> Using package: $PKG"

ASSIST_ACTIVITY="$PKG/$PKG$ASSIST_CLASS"
VIS_SERVICE="$PKG/$PKG$VIS_CLASS"

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
