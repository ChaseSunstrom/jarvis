#!/usr/bin/env bash
#
# The instrumented (on-device) end-to-end run, as one file.
#
# WHY THIS IS A FILE AND NOT AN INLINE `script:` BLOCK
# ---------------------------------------------------
# `reactivecircus/android-emulator-runner` does not hand its `script:` input to
# a shell as one program. `src/script-parser.ts` splits the input on
# `/\r\n|\n|\r/`, trims each piece and drops blank and `#`-comment lines;
# `src/main.ts` then runs the pieces one at a time:
#
#     for (const script of scripts) {
#       await exec.exec('sh', ['-c', script], { env: ... });
#     }
#
# Every line gets its OWN shell. A variable assigned on one line is gone by the
# next, a `set` applies to nothing after it, a backslash continuation is
# severed, and a multi-line `if ... fi` becomes fragments that are each a syntax
# error alone. None of that is visible to `sh -n`, `bash -n` or actionlint,
# because each fragment is invalid only in isolation — and the first fragment to
# fail makes `exec.exec` throw, which fails the step with nothing run.
#
# That is what run 31303989376 hit. Its first line was `set -uo pipefail`, and
# /bin/sh on ubuntu-latest is dash:
#
#     [command]/usr/bin/sh -c set -uo pipefail
#     /usr/bin/sh: 1: set: Illegal option -o pipefail
#     ##[error]The process '/usr/bin/sh' failed with exit code 2
#
# Note the echoed command: one line, not the block. Making that line POSIX would
# only have moved the death a few lines down, to `if adb reverse ...; then`.
#
# The only shape immune to the splitter is a single line that runs a file. This
# is that file. Because it is invoked as `bash <this file>` it is bash, and the
# bashism rules that apply to an inline block do not apply here.
# `testing/e2e/test_ci_workflow_contract.py` enforces both halves: that the
# `script:` input stays one line, and that the file it names exists and parses.
#
# Environment (all set by the job before the emulator step):
#   GITHUB_WORKSPACE      the checkout root
#   JARVIS_HARNESS_PORT   the port the harness's jarvis-core is listening on
#   JARVIS_HARNESS_TOKEN  its bearer token (deterministic, not a secret)
#   API_LEVEL             the emulator API level, for the annotations only
#   GITHUB_STEP_SUMMARY   the run summary file
#   ANDROID_SERIAL        set by the action, so adb targets this emulator

# `-u` and `-o pipefail`, but deliberately NOT `-e`.
#
# Everything after the Gradle call exists to collect evidence about a run that
# has probably just failed, and the emulator is destroyed the moment this script
# returns. Under errexit the first `adb pull` that finds nothing would abort the
# script and take the logcat, the crash buffer and the step summary with it —
# losing exactly the artefacts a red run is read from. So errexit stays off and
# every command below is checked by hand: each one either carries `|| true`,
# captures its status with `|| status=$?`, or sits inside an `if`.
#
# pipefail is safe here for the same reason. It changes the status of
# `ls ... | wc -l` when there are no screenshots, and that status is discarded
# into a variable rather than ending the script.
set -uo pipefail

ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/../.." && pwd)}"
API_LEVEL="${API_LEVEL:-unknown}"

if [ -z "${JARVIS_HARNESS_PORT:-}" ] || [ -z "${JARVIS_HARNESS_TOKEN:-}" ]; then
  echo "::error::JARVIS_HARNESS_PORT/JARVIS_HARNESS_TOKEN are unset — the harness step did not run or did not export them"
  exit 1
fi

cd "${ROOT}/android-app" || exit 1
mkdir -p artifacts

adb wait-for-device
adb devices

# Two ways for the app to reach the harness, and both are permitted by a
# network-security config, so take the one that can be verified before the suite
# runs and fall back to the other.
#
#  1. adb reverse -> 127.0.0.1, which the SHIPPING config already allows
#     cleartext to, so the app is exercised in its real transport posture.
#     Establishing it either works or fails now.
#  2. 10.0.2.2, QEMU's alias for the host's loopback, allowed by the DEBUG
#     variant config (src/debug/res/xml/network_security_config.xml).
HARNESS_URL="http://127.0.0.1:${JARVIS_HARNESS_PORT}"
if adb reverse "tcp:${JARVIS_HARNESS_PORT}" "tcp:${JARVIS_HARNESS_PORT}"; then
  adb reverse --list || true
else
  echo "::warning::adb reverse tcp:${JARVIS_HARNESS_PORT} failed; falling back to the emulator host alias"
  HARNESS_URL="http://10.0.2.2:${JARVIS_HARNESS_PORT}"
fi
echo "harness for the app: ${HARNESS_URL}"

adb logcat -c || true

status=0
gradle :app:connectedDebugAndroidTest --no-daemon --stacktrace \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessUrl="${HARNESS_URL}" \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessToken="${JARVIS_HARNESS_TOKEN}" \
  -Pandroid.testInstrumentationRunnerArguments.jarvisRequireHarness=true \
  || status=$?
echo "gradle :app:connectedDebugAndroidTest exited ${status}"
if [ "${status}" != "0" ]; then
  # An annotation, so the failure is on the run's summary page and not only
  # 4000 lines down the job log.
  echo "::error::the instrumented suite failed (gradle exit ${status}); see artifacts android-e2e-reports-api${API_LEVEL} and android-e2e-logs-api${API_LEVEL}"
fi

# --- screenshots ------------------------------------------------------------
# ai.jarvis.app.testing.TestHooks.screenshotDir() = the app's external files
# dir, which needs no storage permission.
SHOTS=/sdcard/Android/data/ai.jarvis.app/files/screenshots
adb shell ls -l "$SHOTS" || true
adb pull "$SHOTS" artifacts/ || true
if [ -z "$(ls -A artifacts/screenshots 2>/dev/null || true)" ]; then
  echo "screenshots unreadable as the shell user; retrying with adb root"
  # NOT discarded. Run 31310120431 tried this, then tried the raw path below,
  # and both said "No such file or directory" — which is what a rooted adbd and
  # an adbd that silently stayed `shell` look like from here. The one thing that
  # tells them apart is what `adb root` said and who `adb shell` then is, so say
  # both out loud rather than debug it again a run later.
  adb root || true
  adb wait-for-device
  echo "adb shell runs as: $(adb shell id || true)"
  adb pull "$SHOTS" artifacts/ || true
  # /sdcard is a FUSE view, and from Android 11 that view HIDES Android/data
  # from every uid but the owning app's — root included, because the
  # restriction is enforced by the daemon serving the mount rather than by
  # permissions. Run 31309094331 is the worked example: the app wrote fifteen
  # PNGs and both pulls above still said
  #
  #   adb: error: failed to stat remote object
  #        '/sdcard/Android/data/ai.jarvis.app/files/screenshots':
  #        No such file or directory
  #
  # /data/media/0 is the same bytes on the real filesystem, underneath FUSE, and
  # a rooted adbd reads it directly. This is why the emulator target is
  # `google_apis` and never `google_apis_playstore` — a Play image cannot be
  # rooted, and this is the pull that needs it.
  RAW=/data/media/0/Android/data/ai.jarvis.app/files
  if [ -z "$(ls -A artifacts/screenshots 2>/dev/null || true)" ]; then
    adb shell ls -l "$RAW" || true
    adb pull "$RAW/screenshots" artifacts/ || true
  fi
  # Root can read the raw path but `adb pull` of a directory it cannot stat is a
  # dead end, so copy it somewhere the shell user owns and pull that.
  if [ -z "$(ls -A artifacts/screenshots 2>/dev/null || true)" ]; then
    adb shell "rm -rf /data/local/tmp/screenshots && cp -r '$RAW/screenshots' /data/local/tmp/ && chmod -R 777 /data/local/tmp/screenshots" || true
    adb pull /data/local/tmp/screenshots artifacts/ || true
  fi
  adb unroot >/dev/null 2>&1 || true
  adb wait-for-device || true
fi
# Internal-storage fallback: screenshotDir() lands there on a device with no
# external volume.
if [ -z "$(ls -A artifacts/screenshots 2>/dev/null || true)" ]; then
  adb exec-out run-as ai.jarvis.app tar -cf - files/screenshots 2>/dev/null \
    | tar -xf - -C artifacts 2>/dev/null || true
  if [ -d artifacts/files/screenshots ]; then
    # An earlier partial pull may have left an empty directory in the way; mv
    # into it would nest rather than replace.
    rmdir artifacts/screenshots 2>/dev/null || true
    mv artifacts/files/screenshots artifacts/screenshots || true
  fi
fi
shots="$(ls -1 artifacts/screenshots/*.png 2>/dev/null | wc -l | tr -d ' ')"
echo "pulled ${shots} screenshot(s)"

# --- logs -------------------------------------------------------------------
adb logcat -d -v time > artifacts/logcat.txt 2>&1 || true
adb logcat -b crash -d -v time > artifacts/logcat-crash.txt 2>&1 || true
adb shell dumpsys package ai.jarvis.app > artifacts/dumpsys-package.txt 2>&1 || true

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### e2e · android emulator (API ${API_LEVEL})"
    echo ""
    if [ "${status}" = "0" ]; then
      echo "The instrumented suite PASSED on a real emulator."
    else
      echo "The instrumented suite FAILED (gradle exit ${status})."
    fi
    echo ""
    echo "- Real APK, real activities, real sockets. \`ConversationE2ETest\` drove a full voice turn against the real \`jarvis-core\` harness at \`${HARNESS_URL}\`."
    echo "- Screenshots pulled: **${shots}** — artifact **android-e2e-screenshots-api${API_LEVEL}**."
    echo "- HTML/XML reports: artifact **android-e2e-reports-api${API_LEVEL}**."
    echo "- logcat (incl. the crash buffer): artifact **android-e2e-logs-api${API_LEVEL}**."
  } >> "$GITHUB_STEP_SUMMARY"
fi
# Tells the fallback summary step that a real summary exists. A file, not a step
# output, because this runs inside the action's own process where $GITHUB_OUTPUT
# belongs to the action and not to us.
: > artifacts/summary-written

exit "${status}"
