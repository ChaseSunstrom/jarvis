#!/usr/bin/env bash
# M64 — the phone looks like the console.
#
# An audit put the phone's native screens beside the console and listed
# fourteen ways they differed: an underline per tab rebuilt on every switch, a
# box per row, APPROVE in green, a purple resting orb, a wordmark painted over
# it, nine hand-typed trackings, forty-five dp literals the lint could not see,
# centred caps titles, no screen states, gradient bars, gold memories, a
# stagger token nobody read. Every check here reads the Kotlin for one of
# those and fails if it comes back. Build, unit, lint and goldens only — never
# a device; what only a handset shows is in docs/ANDROID_DEVICE_TESTS.md.
set -u
source "$(dirname "$0")/lib.sh"
verify_begin "M64" "the phone looks like the console"
use_venv
use_local_bin 2>/dev/null || true
export JAVA_HOME="${JAVA_HOME:-$HOME/.local/jdk}"
[ -d "$JAVA_HOME/bin" ] && export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

KT=android-app/app/src/main/kotlin/ai/jarvis/app
UI=$KT/ui/JarvisUi.kt
FRAME=$KT/ui/ConsoleFrame.kt

# --- 1. the tab strip: one measured underline that slides ----------------------
check_not "no underline built per tab (withUnderline is gone)" grep -q "withUnderline" $FRAME
check "exactly one view carries the underline tag" bash -c "[ \"\$(grep -c 'tag = UNDERLINE_TAG' $FRAME)\" = 1 ]"
check "the underline slides on motion.dur.base with the out curve" bash -c "grep -q 'Motion.Dur.BASE' $FRAME && grep -q 'JarvisUi.EASE_OUT' $FRAME"
check "the strip scrolls the current tab into view and fades the overflowing edge" bash -c "grep -q 'smoothScrollTo' $FRAME && grep -q 'isHorizontalFadingEdgeEnabled = true' $FRAME && grep -q 'override fun getSolidColor' $FRAME"
check "tabs use the smallest chrome step with TIGHT tracking" bash -c "grep -A12 'fun tab(' $UI | grep -q 'letterSpacing = TRACK_TIGHT'"
check "the bar keeps the mark and the readout" bash -c "grep -q 'class BrandMark' $FRAME && grep -q 'fun setStatus(label: String, tone: Tone)' $FRAME"
check "a tab switch selects, it does not rebuild" bash -c "! sed -n '/private fun markCurrentTab()/,/^    }/p' $KT/ManagementActivity.kt | grep -q removeAllViews"
check "the console's front doors are still the phone's (ConsoleTab and PHONE untouched)" python3 android-app/tools/console_parity_test.py

# --- 2. rows: one panel, hairlines between --------------------------------------
check "JarvisUi draws a list as one panel with hairline dividers" bash -c "grep -q 'fun rows(' $UI && grep -A8 'fun rows(' $UI | grep -q 'SHOW_DIVIDER_MIDDLE'"
check_not "checkRow no longer boxes itself" bash -c "sed -n '/fun checkRow(/,/^    }/p' $UI | grep -q 'background = panel('"
check "the activity strip separates rows with a hairline" grep -q "SHOW_DIVIDER_MIDDLE" $KT/ui/ActivityStrip.kt
check "the checklist and the crash log draw their rows in one panel" bash -c "grep -q 'JarvisUi.rows(' $KT/ui/SystemCheckActivity.kt && grep -q 'JarvisUi.rows(' $KT/ui/CrashLogActivity.kt"

# --- 3. APPROVE is the accent primary ------------------------------------------
check "the consent yes is filled with the accent, not the OK green" bash -c "sed -n '/fun consentButton(/,/^        }/p' $UI | grep -q 'setColor(if (yes) ACCENT'"
check_not "no consent fill in a semantic tone" bash -c "sed -n '/fun consentButton(/,/^        }/p' $UI | grep -q 'setColor(if (yes) tone'"
check "the held bar's action is the screen's primary" bash -c "sed -n '/fun banner(/,/^    }/p' $UI | grep -q 'primary(context, actionLabel'"

# --- 4. activity rows: a dot, the body face, mono data --------------------------
check "an activity row is a state dot, a tag, the body face and tabular mono" bash -c "grep -q 'StateDot' $KT/ui/ActivityStrip.kt && grep -q 'BODY_FACE' $KT/ui/ActivityStrip.kt && grep -q 'fontFeatureSettings' $KT/ui/ActivityStrip.kt"
check_not "no 64 dp bold mono kind tag" grep -q "TAG_WIDTH_DP" $KT/ui/ActivityStrip.kt
check "the dot glows and pulses only while live, on the motion tokens" bash -c "grep -q 'Motion.Dur.PULSE' $KT/ui/StateDot.kt && grep -q 'JarvisTokens.Color.GLOW' $KT/ui/StateDot.kt"

# --- 5. labels through one recipe; the lint sees what it missed -----------------
check "the label recipe exists (labelText) and the readout recipe (readout)" bash -c "grep -q 'fun labelText(' $UI && grep -q 'fun readout(' $UI"
check_not "no letterSpacing literal anywhere in the phone's hand-written Kotlin" bash -c "grep -rnE 'letterSpacing\s*=\s*0\.[0-9]+f?' $KT --include='*.kt' --exclude-dir=theme"
check_not "no dp literal behind a this@Activity receiver" bash -c "grep -rnE 'dp\(this@[A-Za-z]+, [0-9]+\)' $KT --include='*.kt'"
check "token_lint has the tracking rule and sees this@Foo receivers" bash -c "grep -q 'KT_TRACK' scripts/verify/token_lint.py && grep -q 'KT_SPACE = re.compile(r\"\\\\bdp\\\\(\\\\s\\*\\[\\\\w@.\\]+' scripts/verify/token_lint.py"
check "token_lint's new rules catch a planted literal" python3 -c '
import importlib.util, sys
spec = importlib.util.spec_from_file_location("token_lint", "scripts/verify/token_lint.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
hits = m.scan_kotlin("val a = JarvisUi.dp(this@MainActivity, 200)\nletterSpacing = 0.2f\nletterSpacing = 0f\nJarvisUi.dp(view.context, 7)\n")
kinds = [h.split(": ")[1] for h in hits]
assert kinds == ["raw dp", "raw tracking", "raw dp"], hits
print("dp(this@Foo, N), letterSpacing = 0.2f and dp(view.context, N) are hits; letterSpacing = 0f is not")
'
check "token lint: the phone is clean, baseline not widened" bash -c "python3 scripts/verify/token_lint.py --require-clean android-app/app/src/main/kotlin && python3 -c \"import json; b=json.load(open('design/token-lint.baseline.json')); assert b['files']=={}, b['files']\""

# --- 6. colours: no raw white, danger as words is danger-text -------------------
check_not "no Color.WHITE on a phone screen" grep -rn "Color.WHITE" $KT/assist/ToolActivityView.kt $KT/companion/CompanionAskActivity.kt
check "failure text is the danger TEXT colour" bash -c "grep -q 'DENY_TEXT' $KT/assist/ToolActivityView.kt && grep -q 'DENY_TEXT' $KT/tasks/TaskProgressView.kt && grep -q 'const val DENY_TEXT = JarvisTokens.Color.DANGER_TEXT' $UI"
check_not "no sentence set in the danger mark colour" grep -rn "setTextColor(JarvisUi.DENY)" $KT

# --- 7. the resting orb is the accent's; the brand is the bar's -----------------
check "the reactor rests in accent-deep with an accent dot, as Reactor.svelte does" python3 android-app/tools/reactor_orb_test.py
check "the reactor's moves are still the M53 vocabulary" python3 android-app/tools/reactor_motion_test.py
check_sh "generated files current; SiriPalette still pinned to color.orb.*" 'python3 design/build.py --check 2>&1 | tail -1'
check_not "no JARVIS wordmark painted over the orb" bash -c "grep -q 'wordmarkPaint' $KT/ui/JarvisOrbView.kt || grep -q 'drawText(\"JARVIS\"' $KT/ui/JarvisOrbView.kt"
check "the caption is accent-deep, the chrome face, the label step, wide tracking" bash -c "sed -n '/private fun drawText(/,/^    }/p' $KT/ui/JarvisOrbView.kt | grep -q 'ACCENT_DEEP' && sed -n '/private fun drawText(/,/^    }/p' $KT/ui/JarvisOrbView.kt | grep -q 'JarvisUi.sp(context, JarvisUi.Type.LABEL)'"
check "the voice screen wears the bar's brand" grep -q "ConsoleFrame.brand(this)" $KT/MainActivity.kt

# --- 8. screen titles: the console's ScreenTitle --------------------------------
check "the title is left-aligned display face with a lede under it" bash -c "grep -A8 'fun title(' $UI | grep -q 'gravity = Gravity.START' && grep -q 'fun screenTitle(' $UI"
check "Settings, System check and Crash logs open with a ScreenTitle" bash -c "grep -q 'JarvisUi.screenTitle(ctx, \"Phone\"' $KT/SettingsActivity.kt && grep -q 'screenTitle(' $KT/ui/SystemCheckActivity.kt && grep -q 'screenTitle(' $KT/ui/CrashLogActivity.kt"
check "every literal the instrumented suite looks for is still on its screen" python3 android-app/tools/instrumentation_contract_test.py

# --- 9. the four screen states ---------------------------------------------------
require_file $KT/ui/ScreenStates.kt
check "ScreenStates has loading, empty, error and offline" bash -c "for f in loading empty error offline; do grep -q \"fun \$f(\" $KT/ui/ScreenStates.kt || exit 1; done"
check "the console screen draws loading, error and offline from ScreenStates" bash -c "grep -q 'ScreenStates.loading(' $KT/ManagementActivity.kt && grep -q 'ScreenStates.error(' $KT/ManagementActivity.kt && grep -q 'ScreenStates.offline(' $KT/ManagementActivity.kt"
check "the crash log's empty state is the console's" grep -q "ScreenStates.empty(" $KT/ui/CrashLogActivity.kt
check "the states screen still says what a screen reader needs" python3 android-app/tools/accessibility_labels_test.py

# --- 10. the settings section strip ---------------------------------------------
require_file $KT/ui/SectionStrip.kt
check "the strip is a hairline box with the current segment on surface-2" bash -c "grep -q 'SURFACE_2' $KT/ui/SectionStrip.kt && grep -q 'LINE_HAIR' $KT/ui/SectionStrip.kt"
check "settings scrolls one column to the section the strip names" bash -c "grep -q 'SectionStrip(ctx, SECTIONS)' $KT/SettingsActivity.kt && grep -q 'SectionStrip.sectionAt(' $KT/SettingsActivity.kt && grep -c 'ScrollView(ctx)' $KT/SettingsActivity.kt | grep -q '^1$'"

# --- 11. control geometry: the console's --------------------------------------
check "button and primary pad space-4 by space-2 (16 by 10)" bash -c "sed -n '/fun button(/,/^        }/p' $UI | grep -q 'dp(context, Space.SECTION)' && sed -n '/fun primary(/,/^        }/p' $UI | grep -q 'dp(context, Space.ROW)'"
check "a field pads space-2 by space-3" bash -c "sed -n '/fun field(/,/^    }/p' $UI | grep -q 'dp(context, Space.ROW), dp(context, Space.STEP)'"
check "a chooser is a value: body face, untracked, at the left, on the field ground" bash -c "sed -n '/fun chooser(/,/^    }/p' $UI | grep -q 'BODY_FACE' && sed -n '/fun chooser(/,/^    }/p' $UI | grep -q 'Gravity.START' && ! sed -n '/fun chooser(/,/^    }/p' $UI | grep -q 'button(context'"

# --- 12. progress fills are flat --------------------------------------------------
check_not "no gradient fill in the task bar" grep -nE "LinearGradient|Orientation.LEFT_RIGHT" $KT/tasks/TaskProgressView.kt
check_not "no gradient fill in the tool bar" grep -nE "LinearGradient|Orientation.LEFT_RIGHT" $KT/assist/ToolActivityView.kt
check "the sweep runs on motion.dur.sweep" grep -q "Motion.Dur.SWEEP" $KT/tasks/TaskProgressView.kt

# --- 13. graph labels and memories --------------------------------------------------
check "graph labels are the body face with a ground knockout; memories are not gold" bash -c "grep -q 'knockoutPaint' $KT/ui/KnowledgeGraphView.kt && grep -q 'BODY_FACE' $KT/ui/KnowledgeGraphView.kt && ! grep -q 'GOLD' $KT/ui/KnowledgeGraphView.kt"
check "the graph mirror still holds the Kotlin to the contract" python3 android-app/tools/knowledge_graph_mirror_test.py

# --- 14. motion: the stagger is read; reduced motion stops the decorative --------
check "lists enter on motion.stagger.step, capped, over motion.dur.enter" bash -c "grep -q 'Motion.Stagger.STEP' $UI && grep -q 'Motion.Stagger.CAP' $UI && grep -q 'Motion.Dur.ENTER' $UI && grep -q 'JarvisUi.enter(' $KT/ui/ActivityStrip.kt"
check "reduced motion is one question, asked by the orb, the dot and the sweep" bash -c "grep -q 'fun reducedMotion(' $UI && for f in ui/JarvisOrbView.kt ui/SiriOrbView.kt ui/StateDot.kt tasks/TaskProgressView.kt; do grep -q 'reducedMotion(' $KT/\$f || exit 1; done"

# --- the mirrors, the build, the goldens -------------------------------------------
check_sh "the Android mirrors" 'make -s test-android 2>&1 | tail -3'
check_sh ">= 14 golden screenshots, the states and the section strip among them" '
n=$(ls android-app/app/src/test/screenshots/*.png | wc -l); test "$n" -ge 14 && ls android-app/app/src/test/screenshots/screen-states.png android-app/app/src/test/screenshots/section-strip.png >/dev/null && echo "$n goldens"'
check_sh "./gradlew assembleDebug" 'cd android-app && timeout 1500 ./gradlew -q assembleDebug 2>&1 | tail -5'
check_sh "./gradlew testDebugUnitTest" 'cd android-app && timeout 1500 ./gradlew -q testDebugUnitTest 2>&1 | tail -8'
check_sh "./gradlew lintDebug (blocking)" 'cd android-app && timeout 1200 ./gradlew -q lintDebug 2>&1 | tail -8'
check_sh "./gradlew verifyRoborazziDebug (the goldens look like the console)" 'cd android-app && timeout 1200 ./gradlew -q verifyRoborazziDebug 2>&1 | tail -5'
check "the device backlog names what only a handset can confirm about the look" python3 -c '
from pathlib import Path
doc = Path("docs/ANDROID_DEVICE_TESTS.md").read_text()
assert "M64" in doc, "no ADT row for M64"
print("ADT rows for M64 present")
'
check "the claims register has the M64 section" grep -q "### The phone looks like the console (M64)" docs/verification.md

# The rule every gate keeps (M25): it runs a slice of the live suite.
check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
