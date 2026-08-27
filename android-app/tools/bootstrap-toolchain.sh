#!/usr/bin/env bash
# Everything needed to build the Android app, under $HOME, with no root.
#
#   bash android-app/tools/bootstrap-toolchain.sh
#
# Installs, and skips anything already there:
#
#   ~/.local/jdk        Temurin JDK 17 (the AGP 8.7 baseline)
#   ~/Android/Sdk       cmdline-tools, platform-tools, platforms;android-35,
#                       build-tools;35.0.0
#
# Why under $HOME and not with a package manager: this host has no sudo, no
# pip, and no Android anything — which is the situation the milestone is
# written for. The same script works on a CI runner, which is why the versions
# are pinned rather than "latest".
#
# It is idempotent and it is slow the first time: about 1.2 GB of downloads.
set -euo pipefail

JDK_VERSION="${JDK_VERSION:-17}"
SDK_PLATFORM="${SDK_PLATFORM:-35}"
BUILD_TOOLS="${BUILD_TOOLS:-35.0.0}"
JAVA_HOME_DIR="${JAVA_HOME_DIR:-$HOME/.local/jdk}"
ANDROID_HOME_DIR="${ANDROID_HOME:-$HOME/Android/Sdk}"
#: Pinned. `commandlinetools-linux-latest.zip` is not a thing that exists, and a
#: floating one would make a green build unreproducible.
CMDLINE_TOOLS_ZIP="${CMDLINE_TOOLS_ZIP:-commandlinetools-linux-11076708_latest.zip}"

say() { printf '\n=== %s\n' "$*"; }

# --- JDK --------------------------------------------------------------------
if [ -x "$JAVA_HOME_DIR/bin/javac" ]; then
    say "JDK already at $JAVA_HOME_DIR ($("$JAVA_HOME_DIR/bin/java" -version 2>&1 | head -1))"
else
    say "installing Temurin JDK $JDK_VERSION into $JAVA_HOME_DIR"
    mkdir -p "$(dirname "$JAVA_HOME_DIR")"
    tmp="$(mktemp -d)"
    url="https://api.adoptium.net/v3/binary/latest/${JDK_VERSION}/ga/linux/x64/jdk/hotspot/normal/eclipse"
    curl -fsSL "$url" -o "$tmp/jdk.tar.gz"
    tar xzf "$tmp/jdk.tar.gz" -C "$tmp"
    extracted="$(find "$tmp" -maxdepth 1 -type d -name 'jdk-*' | head -1)"
    [ -n "$extracted" ] || { echo "the JDK archive did not contain a jdk-* directory" >&2; exit 1; }
    rm -rf "$JAVA_HOME_DIR"
    mv "$extracted" "$JAVA_HOME_DIR"
    rm -rf "$tmp"
fi
export JAVA_HOME="$JAVA_HOME_DIR"
export PATH="$JAVA_HOME/bin:$PATH"

# --- SDK command-line tools -------------------------------------------------
SDKMANAGER="$ANDROID_HOME_DIR/cmdline-tools/latest/bin/sdkmanager"
if [ -x "$SDKMANAGER" ]; then
    say "cmdline-tools already at $ANDROID_HOME_DIR"
else
    say "installing Android cmdline-tools into $ANDROID_HOME_DIR"
    mkdir -p "$ANDROID_HOME_DIR/cmdline-tools"
    tmp="$(mktemp -d)"
    curl -fsSL "https://dl.google.com/android/repository/$CMDLINE_TOOLS_ZIP" -o "$tmp/tools.zip"
    # The zip contains `cmdline-tools/`; sdkmanager insists on being at
    # `cmdline-tools/latest/`, and gets that wrong on its own.
    (cd "$tmp" && unzip -q tools.zip)
    rm -rf "$ANDROID_HOME_DIR/cmdline-tools/latest"
    mv "$tmp/cmdline-tools" "$ANDROID_HOME_DIR/cmdline-tools/latest"
    rm -rf "$tmp"
fi

export ANDROID_HOME="$ANDROID_HOME_DIR"
export ANDROID_SDK_ROOT="$ANDROID_HOME_DIR"

# --- packages ---------------------------------------------------------------
say "accepting licences and installing platform $SDK_PLATFORM, build-tools $BUILD_TOOLS"
yes | "$SDKMANAGER" --licenses >/dev/null 2>&1 || true
"$SDKMANAGER" --install \
    "platform-tools" \
    "platforms;android-$SDK_PLATFORM" \
    "build-tools;$BUILD_TOOLS" >/dev/null

# --- Robolectric runtimes ---------------------------------------------------
#
# Robolectric runs the framework from an `android-all` jar per SDK, and fetches
# it from Maven Central inside the forked test JVM the first time a test runs.
# On this host that JVM cannot resolve repo1.maven.org at all — while curl from
# the same shell gets a 200 — so the download happens here, where a failure is
# a line in this script rather than an `AssertionError` in an unrelated test.
#
# Pinned to the versions Robolectric 4.14.1 asks for, which is why the file
# names are literal: `libs.versions.toml` and this list move together.
ROBO_DIR="${ROBOLECTRIC_DEPS:-$HOME/.cache/robolectric}"
mkdir -p "$ROBO_DIR"
for artifact in 14-robolectric-10818077-i7 15-robolectric-12650502-i7; do
    jar="android-all-instrumented-$artifact.jar"
    if [ -s "$ROBO_DIR/$jar" ]; then
        say "Robolectric runtime already at $ROBO_DIR/$jar"
        continue
    fi
    say "fetching the Robolectric runtime $artifact (~150 MB)"
    curl -fsSL \
        "https://repo1.maven.org/maven2/org/robolectric/android-all-instrumented/$artifact/$jar" \
        -o "$ROBO_DIR/$jar"
done

say "done"
printf 'JAVA_HOME=%s\nANDROID_HOME=%s\n' "$JAVA_HOME_DIR" "$ANDROID_HOME_DIR"
"$JAVA_HOME_DIR/bin/java" -version 2>&1 | head -1
ls "$ANDROID_HOME_DIR/platforms" "$ANDROID_HOME_DIR/build-tools"
