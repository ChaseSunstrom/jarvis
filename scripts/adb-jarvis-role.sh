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
# Jarvis ships inside the degoogled `minimal` flavor, so the installed
# applicationId is io.homeassistant.companion.android.minimal (a debug build
# adds a further .debug suffix). The Kotlin CLASSES always live in
# io.homeassistant.companion.android.jarvis.* regardless of applicationId.
#
# The package is auto-detected from what's actually installed; override with
# `JARVIS_PKG=<id> scripts/adb-jarvis-role.sh` or pass it as the first arg.
# (The APK build workflow also prints exact commands for its resolved id.)
#
set -euo pipefail

CLASS_PKG="io.homeassistant.companion.android.jarvis"
err() { echo "ERROR: $*" >&2; exit 1; }

command -v adb >/dev/null 2>&1 || err "adb not found on PATH. Install Android platform-tools."

STATE0="$(adb get-state 2>/dev/null || true)"
[ "$STATE0" = "device" ] || err "No device in 'device' state (adb get-state -> '${STATE0:-none}').
Check: cable connected, USB debugging enabled, this host authorized ('adb devices')."

# Resolve the installed package: explicit override, else the first candidate
# that adb reports installed.
PKG="${JARVIS_PKG:-${1:-}}"
if [ -z "$PKG" ]; then
    for cand in \
        io.homeassistant.companion.android.minimal \
        io.homeassistant.companion.android.minimal.debug \
        io.homeassistant.companion.android.jarvis \
        io.homeassistant.companion.android.jarvis.debug \
        io.homeassistant.companion.android; do
        if adb shell pm path "$cand" >/dev/null 2>&1; then PKG="$cand"; break; fi
    done
fi
[ -n "$PKG" ] || err "Could not find an installed Home Assistant/Jarvis package. Install the APK first, or set JARVIS_PKG."
echo "==> Using package: $PKG"

ASSIST_ACTIVITY="$PKG/$CLASS_PKG.JarvisAssistActivity"
VIS_SERVICE="$PKG/$CLASS_PKG.JarvisVoiceInteractionService"

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
