#!/usr/bin/env bash
#
# Apply the Jarvis overlay to a home-assistant/android fork checkout.
# Safe to re-run: the copy is a clean replace of the jarvis package dir and
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

# 2. Copy the Jarvis sources + resources into the MAIN source set. Jarvis is
#    baked into main and built as the existing degoogled `minimal` flavor, so
#    there is no brittle flavor-inheritance to maintain. Our files are
#    uniquely named (kotlin/.../jarvis/**, res/values/jarvis_styles.xml,
#    res/xml/jarvis_voice_interaction_service.xml) so they never clobber
#    upstream files. Clean-replace only our own jarvis package dir.
echo "==> Copying Jarvis sources -> $FORK_DIR/app/src/main"
rm -rf "$FORK_DIR/app/src/main/kotlin/io/homeassistant/companion/android/jarvis"
mkdir -p "$FORK_DIR/app/src/main/kotlin/io/homeassistant/companion/android" \
         "$FORK_DIR/app/src/main/res/values" \
         "$FORK_DIR/app/src/main/res/xml" \
         "$FORK_DIR/app/src/main/res/drawable" \
         "$FORK_DIR/app/src/main/res/mipmap" \
         "$FORK_DIR/app/src/main/res/mipmap-anydpi-v26"
cp -R "$SCRIPT_DIR/overlay/app/src/main/kotlin/io/homeassistant/companion/android/jarvis" \
      "$FORK_DIR/app/src/main/kotlin/io/homeassistant/companion/android/"
cp "$SCRIPT_DIR/overlay/app/src/main/res/values/jarvis_styles.xml" \
   "$FORK_DIR/app/src/main/res/values/jarvis_styles.xml"
cp "$SCRIPT_DIR/overlay/app/src/main/res/xml/jarvis_voice_interaction_service.xml" \
   "$FORK_DIR/app/src/main/res/xml/jarvis_voice_interaction_service.xml"
# Jarvis launcher icon (adaptive + pre-26 fallback).
cp "$SCRIPT_DIR/overlay/app/src/main/res/drawable/ic_jarvis_foreground.xml" \
   "$SCRIPT_DIR/overlay/app/src/main/res/drawable/ic_jarvis_background.xml" \
   "$FORK_DIR/app/src/main/res/drawable/"
cp "$SCRIPT_DIR/overlay/app/src/main/res/mipmap/ic_jarvis.xml" \
   "$FORK_DIR/app/src/main/res/mipmap/ic_jarvis.xml"
cp "$SCRIPT_DIR/overlay/app/src/main/res/mipmap-anydpi-v26/ic_jarvis.xml" \
   "$FORK_DIR/app/src/main/res/mipmap-anydpi-v26/ic_jarvis.xml"

# 3. Run the idempotent patcher (manifest merge + mock google-services.json).
echo "==> Running overlay/patches/apply.py"
python3 "$SCRIPT_DIR/overlay/patches/apply.py" "$FORK_DIR"

cat <<EOF

==> Overlay applied.

Next steps:
  1. Create a signing keystore (once) - see android/keystore.md:
       keytool -genkeypair -v -keystore jarvis-release.keystore \\
         -alias jarvis -keyalg RSA -keysize 4096 -validity 10000
  2. Build the APK (inside $FORK_DIR) - Jarvis ships in the degoogled
     'minimal' flavor:
       ./gradlew :app:assembleMinimalDebug     # installable, no signing setup
       ./gradlew :app:assembleMinimalRelease    # then sign per keystore.md
  3. Sign + verify a release build:
       apksigner sign --ks jarvis-release.keystore --ks-key-alias jarvis \\
         app/build/outputs/apk/minimal/release/app-minimal-release-unsigned.apk
       apksigner verify --print-certs <signed.apk>
  4. Install, then re-apply the GrapheneOS assistant role (needed after
     EVERY install/update):
       <jarvis repo>/scripts/adb-jarvis-role.sh
  5. Measure activation cold start (target <= 300 ms):
       adb shell am start -W -a android.intent.action.ASSIST

Publish the signed APK to GitHub Releases and add the repo URL to Obtainium
on the phone (see android/keystore.md).
EOF
