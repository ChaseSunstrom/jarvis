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

# --- make the logcat this job collects actually contain the app -------------
#
# The logcat is pulled with `adb logcat -d` AFTER the suite, which means it is
# whatever survived in logd's ring buffer for the whole thirteen-minute run.
# Two things were eating it, and between them they cost a real diagnosis:
#
#  * **chatty.** logd dedups and prunes noisy uids and replaces the lines with
#    "uid=10167(ai.jarvis.app) expire N lines". Run 31610213287 has 192 of those
#    and exactly 8 surviving lines from the app's own tags — so when
#    ConsentGateTest failed with "ApprovalActivity did not start", the entire
#    record of what the dispatcher did had been thrown away. Setting the filter
#    property empty turns the pruning off.
#  * **buffer size.** The default 256 KiB per buffer is a couple of minutes of a
#    busy emulator. 64 MiB covers the run.
#
# All best-effort: an emulator image that refuses either is no worse off than
# before, and neither is worth failing the suite over.
adb shell setprop persist.logd.filter "" || true
adb shell setprop logd.filter "" || true
adb logcat -G 64M || true
# The properties are read by logd at init, so restart it — otherwise the filter
# change applies to the next boot, which is the next job.
adb shell stop logd || true
adb shell start logd || true
adb wait-for-device || true
echo "logcat buffer: $(adb logcat -g 2>/dev/null | head -n 2 | tr '\n' ' ')"

adb logcat -c || true

status=0
# Through `tee`, because gradle's own output is the ONLY place the failing test
# names and their exceptions appear. Nothing else in this job repeats them: the
# step summary said "see the artifacts", the artifacts are a zipped HTML report,
# and the job log is 2800 lines of Gradle stack frames with the nine lines that
# matter somewhere in the middle. Diagnosing the 10 August red run meant
# fetching that log four times to binary-search it, which is not a thing anyone
# should have to do twice.
#
# PIPESTATUS[0], not `$?`: with `tee` on the right the pipeline's own status is
# tee's, and `|| status=$?` would then record 0 for a suite that failed.
gradle :app:connectedDebugAndroidTest --no-daemon --stacktrace \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessUrl="${HARNESS_URL}" \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessToken="${JARVIS_HARNESS_TOKEN}" \
  -Pandroid.testInstrumentationRunnerArguments.jarvisRequireHarness=true \
  2>&1 | tee artifacts/gradle-connected.log
status="${PIPESTATUS[0]}"
echo "gradle :app:connectedDebugAndroidTest exited ${status}"

# The failing test names and the first line of each exception, pulled out of
# that log. Gradle prints a `FAILED` line per failing test followed by the
# throwable, and prints nothing at all for the ones that passed, so this is the
# whole of what went wrong and none of what did not.
#
# `sed` strips the ANSI colour gradle writes even when nothing is a terminal —
# the raw line is `...FAILED \x1b[0m`, and a grep for `FAILED$` silently matches
# none of them.
FAILURES=artifacts/failing-tests.txt
sed 's/\x1b\[[0-9;]*m//g' artifacts/gradle-connected.log 2>/dev/null \
  | grep -E -A 1 '^ai\.jarvis\.app\..* > .* FAILED' \
  | grep -v '^--$' > "${FAILURES}" 2>/dev/null || true
if [ -s "${FAILURES}" ]; then
  echo "----- failing tests -----"
  cat "${FAILURES}"
fi

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
  # 2>/dev/null on the run-as, deliberately: its stderr is binary-adjacent and
  # goes down the same pipe as the tar stream. The listing below is what says
  # whether it had anything to send.
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

# Zero is not a result, it is the absence of one — and every branch above hides
# its own errors, so "pulled 0" used to be the entire explanation. Run
# 31610213287 lost the screenshot for a genuine failure this way and the window
# dump in the assertion came back empty at the same time, which left nothing at
# all to diagnose from. So when the count is zero, say where the app was told to
# write and what is actually there.
if [ "${shots}" = "0" ]; then
  echo "----- no screenshots came off the device; where were they written? -----"
  # The app logs its chosen directory once per run. If this line is absent, the
  # suite never even tried to capture one.
  adb logcat -d -s JarvisScreenshots:* JarvisTestHooks:* 2>/dev/null | tail -n 40 || true
  echo "----- app-private files (run-as) -----"
  adb exec-out run-as ai.jarvis.app ls -la files 2>&1 | head -n 30 || true
  adb exec-out run-as ai.jarvis.app ls -la files/screenshots 2>&1 | head -n 30 || true
  echo "----- external files dir -----"
  adb shell ls -la "/sdcard/Android/data/ai.jarvis.app/files" 2>&1 | head -n 20 || true
  echo "-----------------------------------------------------------------------"
fi

# --- logs -------------------------------------------------------------------
adb logcat -d -v time > artifacts/logcat.txt 2>&1 || true
adb logcat -b crash -d -v time > artifacts/logcat-crash.txt 2>&1 || true
adb shell dumpsys package ai.jarvis.app > artifacts/dumpsys-package.txt 2>&1 || true

# WHAT THE APP SAID DURING THE TEST THAT FAILED, in the job log.
#
# A failing instrumented test reports the assertion and nothing else: the log
# above says "timed out waiting for 2 jarvis_message_result frames" and leaves
# every reason it could have happened equally open. The app narrates itself
# under `Jarvis*` tags — which handler admitted the frame, whether a sender was
# wired, whether the send came back false — and those lines decide it.
#
# They were already captured, and only into `android-e2e-logs-api*`. Downloading
# a CI artifact needs credentials and a browser, which is what whoever is
# reading a red build from a terminal, a phone or an automated session does not
# have. The artifact is still the complete record; this is the part that answers
# the question, printed where the failure is.
#
# WINDOWED ON THE FAILING TEST, not tailed. The first version of this printed
# the last 800 Jarvis-tagged lines, which sounds equivalent and is not: tests
# run in class-name order, so a failure in CompanionAskTest — C, near the front
# — is the first thing a tail throws away. Run 32091666250 proved it, printing
# a clean dump of SettingsPersistenceTest while the two failures it was added
# to explain scrolled off the top.
#
# `JarvisTestRule.starting()` logs `=== class#method ===` for every test, so the
# window is exact: from the failing test's own marker to the next test's.
if [ "${status}" != "0" ] && [ -s artifacts/logcat.txt ]; then
  echo "----- what the app logged during each failing test -----"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "${FAILURES}" artifacts/logcat.txt "${API_LEVEL}" <<'PY' || true
import re, sys

failures_path, logcat_path, api_level = sys.argv[1], sys.argv[2], sys.argv[3]
PER_TEST_CAP = 400

try:
    failing = open(failures_path, encoding="utf-8", errors="replace").read()
except OSError:
    failing = ""
try:
    lines = open(logcat_path, encoding="utf-8", errors="replace").read().splitlines()
except OSError:
    lines = []

# `ai.jarvis.app.CompanionAskTest > aMethodName[emulator-5554 - 11] FAILED`
tests = re.findall(r"^(ai\.jarvis\.app\.[\w.$]+)\s+>\s+([\w$]+)", failing, re.M)
seen, ordered = set(), []
for cls, method in tests:
    if (cls, method) not in seen:
        seen.add((cls, method))
        ordered.append((cls, method))

if not ordered:
    print("(no failing test names were parsed from %s; nothing to window on)" % failures_path)
    sys.exit(0)

# Every per-test marker, in order, so a window ends where the next test begins.
marker = re.compile(r"=== (ai\.jarvis\.app\.[\w.$]+)#([\w$]+) ===")
marks = [(i, m.group(1), m.group(2))
         for i, line in enumerate(lines)
         for m in [marker.search(line)] if m]

for cls, method in ordered:
    print("")
    print("=== %s#%s ===" % (cls, method))
    at = next((k for k, (_, c, mm) in enumerate(marks) if c == cls and mm == method), None)
    if at is None:
        print("  (no `=== %s#%s ===` marker in the logcat — the test never started,"
              % (cls, method))
        print("   or the buffer wrapped before it. Full log: artifact android-e2e-logs-api%s)"
              % api_level)
        continue
    begin = marks[at][0]
    stop = marks[at + 1][0] if at + 1 < len(marks) else len(lines)
    window = lines[begin:stop]
    # The app's own narration plus the rule's, which carries the failure and its
    # stack. Everything else in the window is the platform talking to itself.
    kept = [ln for ln in window if re.search(r"[VDIWEF]/Jarvis", ln)]
    if not kept:
        print("  (the test ran but logged nothing under a Jarvis tag)")
        continue
    if len(kept) > PER_TEST_CAP:
        print("  (%d lines; showing the last %d — the rest are in artifact "
              "android-e2e-logs-api%s)" % (len(kept), PER_TEST_CAP, api_level))
        kept = kept[-PER_TEST_CAP:]
    for ln in kept:
        print("  " + ln)
PY
  else
    echo "(python3 is not on PATH; falling back to the tail of the app log)"
    grep -E '[VDIWEF]/Jarvis' artifacts/logcat.txt | tail -n 200 || true
  fi
  echo "-------------------------------------------------------------------------"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### e2e · android emulator (API ${API_LEVEL})"
    echo ""
    if [ "${status}" = "0" ]; then
      echo "The instrumented suite PASSED on a real emulator."
    else
      echo "The instrumented suite FAILED (gradle exit ${status})."
      echo ""
      # The names, here, on the summary page. "See the artifacts" is what this
      # said before, and it is not an answer when the artifacts are a zip of an
      # HTML report and the job log is 2800 lines long.
      if [ -s "${FAILURES}" ]; then
        echo "<details open><summary>Failing tests</summary>"
        echo ""
        echo '```'
        cat "${FAILURES}"
        echo '```'
        echo ""
        echo "</details>"
      else
        echo "Gradle failed without naming a test, so the suite did not get as far"
        echo "as running one — a build, install or emulator fault. The \`* What went"
        echo "wrong:\` block in the job log says which."
      fi
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
