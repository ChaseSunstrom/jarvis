#!/usr/bin/env bash
# Compile the Android app the way the pipeline does, on this machine.
#
# ## Why this exists
#
# Nothing in the dev container compiled Kotlin. Every Android change was
# verified by the mirror specs in this directory — which read the source as
# text and can say a great deal about it, but cannot say whether it COMPILES —
# and then pushed, and the answer came back from `Build Jarvis APK` about
# fifteen minutes later. A one-character type annotation cost a full CI round
# trip that way:
#
#     e: JarvisConversation.kt:151:30 Type checking has run into a recursive
#     problem. Easiest workaround: specify the types of your declarations
#     explicitly.
#
# ...which is what a `val` whose initializer mentions itself does, and which no
# amount of reading the diff was going to catch.
#
# Java and Gradle are already in the container. The only missing piece is the
# Android SDK, and it is one download away.
#
# ## What it does NOT do
#
# It does not run the instrumented suite: that needs an emulator, and the
# emulator is the one part of the pipeline this cannot stand in for. It does
# compile `androidTest`, which catches the failure mode that actually recurs —
# a test referring to a label or a helper the app no longer has.
#
# Usage:  android-app/tools/local-android-build.sh [gradle tasks...]
#
# With no arguments it runs what `Build Jarvis APK` runs, minus the signing:
# compile, unit tests, assemble, lint.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_APP="$(dirname "${HERE}")"

# Kept out of the repo and out of $HOME: it is ~2GB of toolchain, and a
# scratchpad is where a container puts things it can afford to lose.
SDK_ROOT="${JARVIS_LOCAL_SDK:-${TMPDIR:-/tmp}/jarvis-android-sdk}"
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"

# Read from the build file rather than pinned here, so this cannot claim to
# reproduce a build it is installing the wrong platform for. Failing loudly on
# a build file that no longer states it, because the alternative is installing
# some default platform and calling the resulting compile a verification.
BUILD_FILE="${ANDROID_APP}/app/build.gradle.kts"
[[ -f "${BUILD_FILE}" ]] || BUILD_FILE="${ANDROID_APP}/app/build.gradle"
compile_sdk="$(grep -oP 'compileSdk\s*=\s*\K[0-9]+' "${BUILD_FILE}" || true)"
if [[ -z "${compile_sdk}" ]]; then
  echo "cannot read compileSdk from ${BUILD_FILE}" >&2
  exit 1
fi
# AGP asks for a build-tools revision by name and fails the task graph without
# it — which surfaces as "Could not determine the dependencies of task
# :app:compileDebugKotlin > Failed to find Build Tools revision 34.0.0", i.e.
# as a dependency-resolution problem rather than as a missing SDK package. The
# fallback is AGP's own default for the plugin version in use.
build_tools="$(grep -oP 'buildToolsVersion\s*=\s*"\K[0-9.]+' "${BUILD_FILE}" || true)"
: "${build_tools:=34.0.0}"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }

if [[ ! -x "${SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager" ]]; then
  log "installing the Android command-line tools into ${SDK_ROOT}"
  mkdir -p "${SDK_ROOT}/cmdline-tools"
  tmp_zip="$(mktemp -t android-cmdline-XXXXXX.zip)"
  trap 'rm -f "${tmp_zip}"' EXIT
  curl -sSLo "${tmp_zip}" "${CMDLINE_TOOLS_URL}"
  unzip -q -o "${tmp_zip}" -d "${SDK_ROOT}/cmdline-tools"
  # The archive unpacks as `cmdline-tools/`; sdkmanager insists on being under
  # a version directory and `latest` is the one it accepts.
  #
  # An `if` rather than `[[ ... ]] && mv`: under `set -e` that idiom exits the
  # script when the test is false, so a layout Google had already changed would
  # look like a silent success here and a missing sdkmanager later.
  if [[ -d "${SDK_ROOT}/cmdline-tools/cmdline-tools" ]]; then
    mv "${SDK_ROOT}/cmdline-tools/cmdline-tools" "${SDK_ROOT}/cmdline-tools/latest"
  fi
fi

export ANDROID_HOME="${SDK_ROOT}"
export ANDROID_SDK_ROOT="${SDK_ROOT}"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-${SDK_ROOT}/gradle-home}"
SDKMANAGER="${SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager"

if [[ ! -d "${SDK_ROOT}/platforms/android-${compile_sdk}" ]]; then
  log "accepting licences and installing platform ${compile_sdk} + build-tools"
  yes 2>/dev/null | "${SDKMANAGER}" --licenses >/dev/null 2>&1 || true
  "${SDKMANAGER}" \
    "platforms;android-${compile_sdk}" \
    "build-tools;${build_tools}" \
    "platform-tools" >/dev/null
fi

tasks=("$@")
if [[ ${#tasks[@]} -eq 0 ]]; then
  tasks=(
    :app:compileDebugKotlin
    :app:compileDebugAndroidTestKotlin
    :app:testDebugUnitTest
    :app:assembleDebug
    :app:lintDebug
  )
fi

log "gradle ${tasks[*]}"
cd "${ANDROID_APP}"
exec gradle "${tasks[@]}" --no-daemon
