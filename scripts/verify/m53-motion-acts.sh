#!/usr/bin/env bash
# M53 — Motion when it does things.
#
# One vocabulary (docs/design/MOTION.md): what moves when Jarvis listens,
# thinks, calls a tool, steps a task, reads memory, reads a sensor, looks at a
# camera, waits on you, speaks, errs. Every duration and easing is a token;
# nothing moves under reduced motion; every choreography is measured and the
# signature recordings are regenerated here so they cannot go stale.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M53" "motion when it does things"

require_file docs/design/MOTION.md
require_file design/tokens.json
require_file jarvis-web/e2e/motion.spec.ts

check "the motion vocabulary names every act" python3 -c '
from pathlib import Path
doc = Path("docs/design/MOTION.md").read_text()
for act in ("listening", "thinking", "calling a tool", "stepping a task", "reading memory", "reading a sensor", "looking at a camera", "waiting on you", "speaking", "an error", "a moment landing"):
    assert act in doc, f"MOTION.md does not say what moves when {act}"
print("eleven acts, each with what moves and its tokens")
'
check "the tokens the vocabulary names exist" python3 -c '
import json
t = json.load(open("design/tokens.json"))["motion"]
for path in ("reactor.breathe", "reactor.level", "reactor.think", "reactor.iris-a", "reactor.speak", "dur.enter", "dur.blink", "dur.pulse", "ease.out", "budget.frame"):
    node = t
    for key in path.split("."):
        assert key in node, f"motion.{path} is not a token"
        node = node[key]
print("every named token is in design/tokens.json")
'
check "generated files current" python3 design/build.py --check
check "each choreography has a measured test" python3 -c '
from pathlib import Path
spec = Path("jarvis-web/e2e/motion.spec.ts").read_text()
for name in ("tool call", "task step", "memory read", "sensor change", "camera look", "held bar", "error", "moment"):
    assert name in spec, f"motion.spec.ts does not measure the {name} choreography"
print("eight choreographies measured")
'
check "no value typed by hand" python3 scripts/verify/token_lint.py --require-clean jarvis-web/src

ensure_web_build
check_sh "each choreography: no long frame, and still under reduced motion" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test motion.spec.ts 2>&1 | tail -3'
check "the measurements are on disk for the report" test -f .verify/motion.json
check_sh "the signature recordings, regenerated" \
    'cd jarvis-web && E2E_PORT=$E2E_PORT npx playwright test motion-review.spec.ts 2>&1 | tail -2 && node scripts/collect-motion-review.mjs | tail -3'
check "a recording of Jarvis at work exists" test -s docs/motion-review/5-at-work.webm

verify_end
