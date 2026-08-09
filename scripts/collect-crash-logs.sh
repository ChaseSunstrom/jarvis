#!/usr/bin/env bash
#
# Collect everything needed to diagnose an app crash on a GrapheneOS device.
# "It keeps crashing" is unfixable without the stack trace — this grabs it.
#
# Usage:
#   scripts/collect-crash-logs.sh                    # auto-detect the Jarvis app
#   scripts/collect-crash-logs.sh ai.jarvis.app      # a specific package
#   OUT=/tmp/report scripts/collect-crash-logs.sh
#
# Produces a directory of text files; attach the whole thing to a bug report.
# Nothing here needs root.
set -uo pipefail

OUT="${OUT:-./jarvis-crash-report-$(date +%Y%m%d-%H%M%S)}"
# The app is android-app/, applicationId ai.jarvis.app. Debug and release
# share it deliberately; .debug is kept as a candidate for local variants.
CANDIDATES=(
    "ai.jarvis.app"
    "ai.jarvis.app.debug"
)

err() { echo "ERROR: $*" >&2; }
say() { echo "==> $*"; }

command -v adb >/dev/null 2>&1 || { err "adb not found on PATH (install platform-tools)"; exit 1; }

STATE="$(adb get-state 2>/dev/null || true)"
if [ "$STATE" != "device" ]; then
    err "No device in 'device' state (got '${STATE:-none}').
Enable Developer options -> USB debugging, plug in, and accept the RSA prompt.
On GrapheneOS: Settings -> System -> Developer options -> USB debugging."
    exit 1
fi

# --- which package(s) are we looking at? ----------------------------------
PKGS=()
if [ $# -gt 0 ]; then
    PKGS=("$@")
else
    for cand in "${CANDIDATES[@]}"; do
        if adb shell pm path "$cand" >/dev/null 2>&1; then PKGS+=("$cand"); fi
    done
fi
if [ ${#PKGS[@]} -eq 0 ]; then
    err "The Jarvis app is not installed. Pass a package name explicitly, or see android-app/README.md."
    exit 1
fi

mkdir -p "$OUT"
say "Writing report to $OUT"
say "Packages: ${PKGS[*]}"

# --- device / OS context ---------------------------------------------------
{
    echo "# Device"
    for prop in ro.build.version.release ro.build.version.sdk ro.build.version.security_patch \
                ro.product.model ro.product.device ro.build.fingerprint \
                ro.build.version.incremental ro.build.type; do
        printf '%s = %s\n' "$prop" "$(adb shell getprop "$prop" 2>/dev/null | tr -d '\r')"
    done
    echo
    echo "# GrapheneOS-specific"
    printf 'is_grapheneos_fingerprint = %s\n' \
        "$(adb shell getprop ro.build.fingerprint 2>/dev/null | tr -d '\r' | grep -qi graphene && echo yes || echo 'no/unknown')"
    printf 'exec_spawn (GrapheneOS no-zygote) = %s\n' \
        "$(adb shell getprop persist.security.exec_spawn 2>/dev/null | tr -d '\r')"
    printf 'memory_tagging = %s\n' "$(adb shell getprop arm64.memtag.default 2>/dev/null | tr -d '\r')"
} > "$OUT/00-device.txt" 2>&1

# --- per package -----------------------------------------------------------
for PKG in "${PKGS[@]}"; do
    SAFE="${PKG//./_}"
    say "Collecting for $PKG"

    # Install + version info
    adb shell dumpsys package "$PKG" > "$OUT/10-package-$SAFE.txt" 2>&1

    # Granted/denied permissions — on GrapheneOS the user can DENY the
    # INTERNET permission entirely, which looks like a hang/crash to an app
    # that assumes networking always works. This section shows that.
    {
        echo "# Runtime permissions for $PKG"
        adb shell dumpsys package "$PKG" 2>/dev/null \
            | sed -n '/runtime permissions:/,/^$/p'
        echo
        echo "# Declared/install permissions"
        adb shell dumpsys package "$PKG" 2>/dev/null \
            | sed -n '/requested permissions:/,/install permissions:/p'
        echo
        echo "# NOTE: GrapheneOS adds a per-app 'Network' toggle (INTERNET permission)."
        echo "#       If INTERNET is denied, Jarvis cannot reach the server at all."
        echo "#       Check: Settings > Apps > $PKG > Permissions > Network."
    } > "$OUT/11-permissions-$SAFE.txt" 2>&1

    # THE most useful artefact: why the process died, kept by the OS (API 30+).
    adb shell dumpsys activity exit-info "$PKG" > "$OUT/20-exit-info-$SAFE.txt" 2>&1

    # Any crash the system still remembers
    adb shell dumpsys dropbox --print 2>/dev/null \
        | grep -A 200 -i "$PKG" > "$OUT/21-dropbox-$SAFE.txt" 2>&1 || true
done

# --- logcat ----------------------------------------------------------------
say "Dumping logcat buffers"
adb logcat -b crash -d           > "$OUT/30-logcat-crash.txt"  2>&1
adb logcat -b main -d -t 5000    > "$OUT/31-logcat-main.txt"    2>&1
adb logcat -b system -d -t 2000  > "$OUT/32-logcat-system.txt"  2>&1

# Filter the interesting lines out of the noise
{
    echo "# FATAL / crash lines"
    grep -nE "FATAL EXCEPTION|AndroidRuntime|beginning of crash|E .*ActivityManager.*(died|crash)|SIGSEGV|SIGABRT|libc" \
        "$OUT/30-logcat-crash.txt" "$OUT/31-logcat-main.txt" "$OUT/32-logcat-system.txt" 2>/dev/null | head -200
    echo
    echo "# Lines mentioning our packages"
    for PKG in "${PKGS[@]}"; do
        grep -n "$PKG" "$OUT/31-logcat-main.txt" 2>/dev/null | tail -100
    done
} > "$OUT/40-SUMMARY.txt" 2>&1

# --- live capture ----------------------------------------------------------
cat > "$OUT/RUN-LIVE-CAPTURE.sh" <<'LIVE'
#!/usr/bin/env bash
# Reproduce the crash while this runs, then Ctrl-C.
PKG="${1:-ai.jarvis.app}"
adb logcat -c
echo "Log cleared. Launch the app and reproduce the crash now. Ctrl-C when done."
adb logcat -b crash -b main "*:W" | tee live-crash.txt
LIVE
chmod +x "$OUT/RUN-LIVE-CAPTURE.sh"

say "Done."
echo
echo "Start here:  $OUT/40-SUMMARY.txt"
echo "Then:        $OUT/20-exit-info-*.txt   (why the process died)"
echo "             $OUT/11-permissions-*.txt (GrapheneOS Network toggle!)"
echo
echo "If 40-SUMMARY.txt is empty, the crash may not have happened since boot."
echo "Run this to capture it live:"
echo "    $OUT/RUN-LIVE-CAPTURE.sh <package>"
