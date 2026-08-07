#!/usr/bin/env bash
#
# Apply the Jarvis overlay to a home-assistant/android fork checkout.
# Safe to re-run: the copy is a clean replace of app/src/jarvis and
# overlay/patches/apply.py is fully idempotent.
#
# Usage:
#   ./apply-to-fork.sh                       # clones into ./ha-android-fork
#   HA_ANDROID_DIR=~/src/ha-android ./apply-to-fork.sh   # use existing checkout
#   HA_ANDROID_REPO=git@github.com:you/android ./apply-to-fork.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORK_DIR="${HA_ANDROID_DIR:-$SCRIPT_DIR/ha-android-fork}"
REPO_URL="${HA_ANDROID_REPO:-https://github.com/home-assistant/android}"

echo "==> Jarvis overlay -> $FORK_DIR"

# 1. Get the fork checkout.
if [ -d "$FORK_DIR/.git" ]; then
    echo "==> Using existing checkout: $FORK_DIR"
elif [ -e "$FORK_DIR" ]; then
    echo "ERROR: $FORK_DIR exists but is not a git checkout." >&2
    exit 1
else
    echo "==> Cloning $REPO_URL (depth 1)..."
    git clone --depth 1 "$REPO_URL" "$FORK_DIR"
fi

if [ ! -f "$FORK_DIR/app/build.gradle.kts" ]; then
    echo "ERROR: $FORK_DIR/app/build.gradle.kts not found - not a home-assistant/android checkout?" >&2
    exit 1
fi

# 2. Copy the jarvis flavor source set (clean replace so deletions propagate).
echo "==> Copying overlay/app/src/jarvis -> $FORK_DIR/app/src/jarvis"
rm -rf "$FORK_DIR/app/src/jarvis"
cp -R "$SCRIPT_DIR/overlay/app/src/jarvis" "$FORK_DIR/app/src/"

# 3. Run the idempotent patcher (flavor, source sets, deps, AssistActivity
#    helper, mock google-services.json).
echo "==> Running overlay/patches/apply.py"
python3 "$SCRIPT_DIR/overlay/patches/apply.py" "$FORK_DIR"

cat <<EOF

==> Overlay applied.

Next steps:
  1. Create a signing keystore (once) - see android/keystore.md:
       keytool -genkeypair -v -keystore jarvis-release.keystore \\
         -alias jarvis -keyalg RSA -keysize 4096 -validity 10000
  2. Build the release APK (inside $FORK_DIR):
       ./gradlew :app:assembleJarvisRelease
  3. Sign + verify:
       apksigner sign --ks jarvis-release.keystore --ks-key-alias jarvis \\
         app/build/outputs/apk/jarvis/release/app-jarvis-release-unsigned.apk
       apksigner verify --print-certs <signed.apk>
  4. Install, then re-apply the GrapheneOS assistant role (needed after
     EVERY install/update):
       <jarvis repo>/scripts/adb-jarvis-role.sh
  5. Measure activation cold start (target <= 300 ms):
       adb shell am start -W -a android.intent.action.ASSIST

Publish the signed APK to GitHub Releases and add the repo URL to Obtainium
on the phone (see android/keystore.md).
EOF
