#!/usr/bin/env bash
# M90 — The claims register, re-measured.
#
# docs/verification.md's suite-size tables carried counts from 12 Aug beside
# sections dated a fortnight later, three different numbers for one suite, a
# row for a component deleted under M49 and four rows pessimistic about what
# the rig proves every night (the quality audit, 27 Aug 2026). The tables
# are regenerated from the commands printed beside them; this gate reruns
# those commands and fails the moment the table and the tree disagree.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M90" "the claims register, re-measured"

check "the suite-size table says what the commands say today" python3 -c '
import re, subprocess
from pathlib import Path
doc = Path("docs/verification.md").read_text()
table = doc.split("### Suite sizes, measured", 1)[1].split("Within `jarvis-core`, by file", 1)[0]
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
def cell(label):
    for line in table.splitlines():
        if line.startswith("| " + label):
            return line.split("|")[2].strip()
    raise AssertionError("no row for " + label)
want = {
    "`jarvis-core`": run("grep -rhoE \"^\\s*(async )?def test_\" jarvis-core/tests | wc -l"),
    "`jarvis-desktop`": run("grep -rhoE \"^\\s*(async )?def test_\" jarvis-desktop/tests | wc -l"),
    "`jarvis-browser`": run("grep -rhoE \"^\\s*(async )?def test_\" jarvis-browser/tests | wc -l"),
    "`jarvis-web` (vitest": run("grep -rhoE \"^\\s*(it|test)\\(\" jarvis-web/src --include=\"*.test.ts\" | wc -l"),
    "`jarvis-web` (Playwright": run("grep -rhoE \"^\\s*test\\(\" jarvis-web/e2e/*.spec.ts | wc -l"),
    "`android-app` (JVM": run("grep -rho \"@Test\" android-app/app/src/test | wc -l"),
    "`scripts/verify`": run("ls scripts/verify/m[0-9][0-9]-*.sh | wc -l"),
}
drift = []
for label, now in want.items():
    said = re.sub(r"[^0-9]", "", cell(label).split(";")[0])
    if said != now:
        drift.append(f"{label}: table says {said}, the tree says {now}")
assert not drift, "; ".join(drift)
print("seven rows agree with the tree:", ", ".join(f"{k.strip(chr(96)).split(chr(32))[0]}={v}" for k, v in want.items()))
'
check "the per-file table agrees with the tree" python3 -c '
import re, subprocess
from pathlib import Path
doc = Path("docs/verification.md").read_text()
block = doc.split("Within `jarvis-core`, by file", 1)[1]
rows = re.findall(r"^\| `(test_[a-z_]+\.py)` \| (\d+) \|", block, re.M)
assert rows, "no per-file rows"
drift = []
for fname, said in rows:
    now = subprocess.run(f"grep -cE \"^\\s*(async )?def test_\" jarvis-core/tests/{fname}", shell=True, capture_output=True, text=True).stdout.strip()
    if said != now:
        drift.append(f"{fname}: {said} vs {now}")
assert not drift, "; ".join(drift)
print(len(rows), "files agree")
'
check "no row names a component that no longer exists, and nothing says Ollama runs here" python3 -c '
from pathlib import Path
doc = Path("docs/verification.md").read_text()
assert "WebGL arc-reactor orb" not in doc, "the orb was deleted under M49"
assert "test_the_persona_prompts_tools_all_exist" not in doc
assert "whisper, piper, openWakeWord, ollama" not in doc
assert "The suite-size tables were measured on **2026-08-27**" in doc
print("no ghosts")
'
check "the four rows the rig proves say so: Wyoming, the orchestrator, MQTT, the Kotlin" python3 -c '
from pathlib import Path
doc = Path("docs/verification.md").read_text()
assert "| Wyoming against **real** whisper/piper/openWakeWord, with audio | **Live** (the rig)" in doc
assert "| The orchestrator against a **real** running service | **Scripted** |" in doc
assert "| MQTT against a real broker with **real devices** | **Live** (the broker) / **Manual** (a device) |" in doc
assert "| **The Kotlin compiles** | **Automated (here and in CI)** |" in doc
print("four rows at the level the evidence supports")
'
check "ISSUES.md carries the two probes that assert a state without looking" bash -c 'grep -q "assert a door.s state without looking" ISSUES.md'
check "the speaker row names its own skip" bash -c 'grep -q "test_a_refused_turn_never_teaches" docs/verification.md'

verify_end
