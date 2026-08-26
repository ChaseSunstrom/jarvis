#!/usr/bin/env bash
# M71 — Enrolment, complete.
#
# The operator's ask, verbatim: "make sure enrolment is completely implemented
# and complete". The answer is an audit — every step of voice enrolment end to
# end, in `docs/verification.md` under "Enrolment, complete (M71)", each row
# saying what proves it — and this gate, which FAILS on any row that still
# says Missing, checks that every test the audit names exists, and then runs
# them: the server's household (names, the verdict saying who, the agent told,
# the bus, the store, the configured threshold, the API, the posture that no
# tool or command can enrol), the console (people rows, a name on every
# sample, TEST, the four states, the strip's speaker row), the phone (the
# mirrors, the JVM, lint, the golden), the harness self-test, and the docs.
#
# No `set -e`: lib.sh's contract is that a failing check does not stop the
# run, so the summary names every missing piece rather than the first.
. "$(dirname "$0")/lib.sh"
verify_begin "M71" "enrolment, complete"
use_venv

require_file tests/contracts/speaker_verdict.json
require_file jarvis-core/tests/test_speaker_gate.py
require_file jarvis-web/e2e/enrol.spec.ts
require_file android-app/tools/enrolment_flow_test.py
require_file scripts/verify/m71-enrolment.sh

# --- the audit -------------------------------------------------------------------
check "the audit table exists, covers every step, and has no Missing row" python3 -c '
from pathlib import Path
doc = Path("docs/verification.md").read_text()
head = "### Enrolment, complete (M71)"
assert head in doc, "docs/verification.md has no M71 section"
body = doc[doc.index(head):]
body = body[:body.index("\n### ", 1)]
rows = [l for l in body.splitlines() if l.startswith("| ") and not l.startswith("| Step") and not l.startswith("|---")]
assert len(rows) >= 18, f"only {len(rows)} rows; the audit is meant to be every step end to end"
missing = [r.split("|")[1].strip() for r in rows if "| Missing" in r or "| **Missing**" in r]
assert not missing, "steps still Missing: " + "; ".join(missing)
steps = " ".join(rows).lower()
for word in ("phrases", "name", "recording", "upload", "embedding", "storing", "verifying", "who is speaking", "stranger", "bus", "threshold", "re-enrolment", "removing", "more than one", "console screen", "phone screen", "security posture", "documented", "real human speech", "live rig"):
    assert word in steps, f"the audit has no row about: {word}"
print(f"{len(rows)} rows, none Missing")
'
check "every test the audit names exists somewhere it can run" python3 -c '
import re
from pathlib import Path
doc = Path("docs/verification.md").read_text()
body = doc[doc.index("### Enrolment, complete (M71)"):]
body = body[:body.index("\n### ", 1)]
names = set(re.findall(r"`(?:[a-z_]+\.py::)?(test_[a-z0-9_]+)`", body))
assert len(names) >= 40, f"only {len(names)} test names in the audit"
haystack = "".join(p.read_text() for p in list(Path("jarvis-core/tests").glob("test_*.py")) + list(Path("android-app/tools").glob("*_test.py")))
absent = sorted(n for n in names if f"def {n}(" not in haystack)
assert not absent, "named but not written: " + ", ".join(absent)
for spec, needle in (("jarvis-web/e2e/enrol.spec.ts", "REMOVE forgets one person"), ("jarvis-web/e2e/voice-live.spec.ts", "a stranger refused, are rows too"), ("jarvis-web/src/lib/activity.test.ts", "who the voice gate heard"), ("android-app/app/src/test/kotlin/ai/jarvis/app/assist/ActivityRowsTest.kt", "aVoiceHeardIsNamedAndTooShortToJudgeIsNotAStranger")):
    assert needle in Path(spec).read_text(), f"{spec} lacks: {needle}"
print(f"{len(names)} Python tests, four console/JVM specs")
'

# --- the contract, read by all three ----------------------------------------------
check "speaker_verdict.json is a contract all three surfaces read, and activity_rows.json carries the kind" python3 -c '
import json
from pathlib import Path
v = json.loads(Path("tests/contracts/speaker_verdict.json").read_text())
assert v["event"] == "jarvis_speaker_verdict" and v["row"]["kind"] == "speaker"
for key in ("label", "nearest", "accepted", "reason", "enforced", "run_id"):
    assert key in v["required"], key
assert "vector" in v["never"] and "audio" in v["never"]
rows = json.loads(Path("tests/contracts/activity_rows.json").read_text())
assert "speaker" in rows["kinds"] and rows["events"]["jarvis_speaker_verdict"] == "speaker"
for path in ("jarvis-core/jarvis/voice/pipeline.py", "jarvis-web/src/lib/activity.svelte.ts", "android-app/app/src/main/kotlin/ai/jarvis/app/assist/ActivityRows.kt", "tests/web/mock-ha.mjs"):
    assert "jarvis_speaker_verdict" in Path(path).read_text(), f"{path} does not name the event"
for path in ("jarvis-web/src/lib/activity.svelte.ts", "android-app/app/src/main/kotlin/ai/jarvis/app/assist/ActivityRows.kt"):
    src = Path(path).read_text()
    for reason in v["unverifiable_reasons"]:
        assert reason in src, f"{path} does not know {reason} is unverifiable"
print("one event, one kind, three readers, the unverifiable reasons in both strips")
'

# --- the server ----------------------------------------------------------------------
check "ruff" python3 -m ruff check jarvis-core/jarvis/voice jarvis-core/jarvis/api/speaker.py jarvis-core/jarvis/llm/agent.py jarvis-core/jarvis/integrations/voice jarvis-core/tests/test_speaker_gate.py jarvis-core/tests/test_llm.py android-app/tools/enrolment_flow_test.py android-app/tools/activity_mirror_test.py
check "no tool, no socket command and no service can enrol; the routes are token-gated" python3 -c '
import re, sys
from pathlib import Path
sys.path.insert(0, "jarvis-core")
from jarvis.api.websocket import WebSocketHandler
bad = [c for c in WebSocketHandler._HANDLERS if "speaker" in c or "enrol" in c]
assert not bad, bad
rest = Path("jarvis-core/jarvis/api/rest.py").read_text()
for route in ("/voice/speaker\"", "/voice/speaker/enrol\"", "/voice/speaker/verify\""):
    assert f"@api_router" in rest and route in rest, route
assert "@open_router" not in rest[rest.index("whose voice this Jarvis answers"):rest.index("/config/settings/list")]
tools = Path("jarvis-core/jarvis/llm/tools.py").read_text().lower()
assert "enrol" not in tools and "voiceprint" not in tools, "the toolbox mentions enrolment"
print("REST only, on the token-gated router")
'
check_sh "server: the speaker gate, the household, the bus, the store, the API, the posture" \
    'cd jarvis-core && python3 -m pytest tests/test_speaker_gate.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check_sh "server: the agent is told who is speaking, and the prompt prefix stays stable" \
    'cd jarvis-core && python3 -m pytest tests/test_llm.py -q --timeout=120 --timeout-method=signal -k "speaking or prompt_prefix or system_prompt" 2>&1 | tail -2'
check_sh "server: the pipeline suite still passes with a speaker on the converse" \
    'cd jarvis-core && python3 -m pytest tests/test_voice.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check_sh "server: the verifier still separates the cast" \
    'cd jarvis-core && python3 -m pytest tests/test_speaker.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check_sh "the harness self-test (the e2e rig with a fake STT) still passes" \
    'bash testing/scripts/run-e2e.sh -q 2>&1 | tail -3'

# --- the docs ---------------------------------------------------------------------------
check "the API is documented with its label on every route, and the docs carry the rest" python3 -c '
from pathlib import Path
clients = Path("jarvis-core/docs/clients.md").read_text()
for route in ("`GET /api/voice/speaker[?label=]`", "`POST /api/voice/speaker/enrol[?label=", "`POST /api/voice/speaker/verify[?label=]`", "`DELETE /api/voice/speaker[?label=]`", "jarvis_speaker_verdict", "speaker-not-recognised"):
    assert route in clients, f"clients.md lacks: {route}"
identity = Path("docs/voice-identity.md").read_text()
for needle in ("## Who is speaking", "?label=", "max_people", "FORGET EVERYONE", "configured_threshold", "people: [...]", "recognised by voice as"):
    assert needle in identity, f"voice-identity.md lacks: {needle}"
checks = {
    "docs/security.md": "## Enrolling a voice is a durable write about a person, and no turn can do it",
    "DEVIATIONS.md": "## 22. Enrolment has no tool and no socket command (M71)",
    "CHANGELOG.md": "**M71 — enrolment, complete.**",
    "MILESTONES.md": "**M71 — Enrolment, complete**",
    "docs/verification.md": "### Enrolment, complete (M71)",
    "jarvis-core/docs/voice.md": "each under a name",
    "docs/UI_MIGRATION.md": "one row per enrolled person (`person-`)",
}
for path, needle in checks.items():
    assert needle in Path(path).read_text(), f"{path} lacks: {needle}"
adt = Path("docs/ANDROID_DEVICE_TESTS.md").read_text()
for row in ("ADT-052", "ADT-053", "ADT-054"):
    assert f"| {row} | Voice identity |" in adt, f"no device row {row}"
print(f"clients.md (4 routes + 2 events), voice-identity.md, {len(checks)} more documents, 3 device rows")
'

# --- the console -------------------------------------------------------------------------
check "the relay forwards a checked label on GET and DELETE, and never the admin token on a write" python3 -c '
from pathlib import Path
route = Path("jarvis-web/src/routes/api/voice/speaker/+server.ts").read_text()
assert "searchParams.get(\x27label\x27)" in route and "encodeURIComponent(label)" in route, "label is not forwarded"
assert "MAX_LABEL_CHARS = 40" in route and "control characters" in route, "label is forwarded unchecked"
for write in ("enrol", "verify"):
    body = Path(f"jarvis-web/src/routes/api/voice/speaker/{write}/+server.ts").read_text()
    assert "backend.token" not in body and "sessionValid(" in body
print("label checked and encoded; the writes carry the caller")
'
check "the panel has people rows with REMOVE, a name box, TEST, and its four states; the mock answers in the server shape" python3 -c '
from pathlib import Path
panel = Path("jarvis-web/src/lib/sections/SettingsVoice.svelte").read_text()
for needle in ("data-jv-row", "person-remove-", "speaker-loading", "speaker-error-state", "speaker-offline", "speaker-unsupported", "nobody — the gate is inert", "speaker-threshold-configured", "Forget everyone"):
    assert needle in panel, f"SettingsVoice.svelte lacks {needle}"
enrol = Path("jarvis-web/src/lib/components/EnrolVoice.svelte").read_text()
for needle in ("enrol-name", "enrol-test", "enrol-test-result", "writeQuery(label)", "verdictLine(", "labelProblem("):
    assert needle in enrol, f"EnrolVoice.svelte lacks {needle}"
mock = Path("tests/web/mock-ha.mjs").read_text()
for needle in ("function speakerStatus", "people: world.people", "/api/voice/speaker/verify", "jarvis/test/speaker_household", "jarvis/test/speaker_verdict", "configured_threshold"):
    assert needle in mock, f"the mock lacks {needle}"
print("rows, name, test, four states; the mock has people, verify and the hooks")
'
check "token lint: no new hard-coded value" python3 scripts/verify/token_lint.py
check "every screen is declared and uses ScreenState" python3 scripts/verify/web_states_check.py
check "no dead controls" node scripts/verify/web_dead_controls.mjs
ensure_web_deps
check_sh "the console's unit tests: the strip, enrolment, the relay and the routes" \
    'cd jarvis-web && npx vitest run src/lib/activity.test.ts src/lib/enrolment.test.ts src/lib/server/routes.test.ts src/lib/server/speakerRelay.test.ts 2>&1 | tail -3'
ensure_web_build
check_sh "svelte-check finds nothing" 'cd jarvis-web && npx svelte-check --threshold error 2>&1 | tail -1'
run_playwright "enrolment on the console, the strip's speaker row, and the settings page" enrol.spec.ts voice-live.spec.ts settings.spec.ts
run_playwright "the voice-identity tests written before names, the inventory and the states for the Voice rows" e2e.spec.ts menus.spec.ts states.spec.ts -g '"voice|Voice|enrol|voiceprint|inventory names"'
check_sh "three pictures of it, at three widths" \
    'cd jarvis-web && UI_REVIEW=1 E2E_PORT=$E2E_PORT npx playwright test ui-review.spec.ts -g "Voice settings" 2>&1 | tail -2 && cd .. && test -f docs/ui-review/settings-voice/desktop.png && test -f docs/ui-review/settings-voice/tablet.png && test -f docs/ui-review/settings-voice/mobile.png && echo "docs/ui-review/settings-voice: desktop, tablet, mobile"'

# --- the phone ------------------------------------------------------------------------------
check "the Kotlin enrols by name, lists the household, names who it heard, and has the speaker kind" python3 -c '
from pathlib import Path
client = Path("android-app/app/src/main/kotlin/ai/jarvis/app/config/VoiceIdentityClient.kt").read_text()
assert "fun enrol(pcm: ByteArray, label: String? = null)" in client and "fun forget(label: String? = null)" in client
screen = Path("android-app/app/src/main/kotlin/ai/jarvis/app/VoiceIdentityActivity.kt").read_text()
for needle in ("nameField", "renderPeople", "FORGET_ALL", "RECOGNISED AS", "Asking Jarvis who is enrolled"):
    assert needle in screen, needle
rows = Path("android-app/app/src/main/kotlin/ai/jarvis/app/assist/ActivityRows.kt").read_text()
assert "SPEAKER" in rows and "\"jarvis_speaker_verdict\" to Kind.SPEAKER" in rows
print("name, household, who, speaker kind")
'
check "phone mirror: enrolment" python3 android-app/tools/enrolment_flow_test.py
check "phone mirror: the strip's vocabulary" python3 android-app/tools/activity_mirror_test.py
check "the Android toolchain is here (a JDK, the SDK, gradle)" bash -c 'test -d "$HOME/.local/jdk" && test -d "$HOME/Android/Sdk" && test -x "$HOME/.local/gradle/bin/gradle"'
export JAVA_HOME="$HOME/.local/jdk" ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$JAVA_HOME/bin:$HOME/.local/gradle/bin:$PATH"
check_sh "the Kotlin builds" 'cd android-app && gradle :app:assembleDebug --no-daemon -q 2>&1 | tail -3'
check_sh "the JVM tests pass, the strip's speaker row among them" \
    'cd android-app && gradle :app:testDebugUnitTest --no-daemon -q 2>&1 | tail -3 && python3 -c "
import glob, xml.etree.ElementTree as ET
t = f = 0
for x in glob.glob(\"app/build/test-results/testDebugUnitTest/*.xml\"):
    r = ET.parse(x).getroot(); t += int(r.get(\"tests\", 0)); f += int(r.get(\"failures\", 0)) + int(r.get(\"errors\", 0))
assert t > 0 and f == 0, (t, f)
print(f\"{t} JVM tests, {f} failures\")"'
check_sh "lint is clean" 'cd android-app && gradle :app:lintDebug --no-daemon -q 2>&1 | tail -3'
check_sh "the voice-activity golden, with its speaker row, still matches" \
    'cd android-app && gradle :app:verifyRoborazziDebug --no-daemon -q --tests "ai.jarvis.app.screenshot.ScreenshotTest" 2>&1 | tail -3 && echo "goldens verified"'

verify_end
