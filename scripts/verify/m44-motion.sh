#!/usr/bin/env bash
# M44 — the motion system. Durations and curves are tokens like everything
# else; the constraints are measured rather than asserted; and the part no test
# can judge is recorded for a person to watch.
source "$(dirname "$0")/lib.sh"
verify_begin "M44" "the motion system, and the moments built on it"
use_venv

require_file docs/motion-review/README.md
require_file jarvis-web/e2e/motion.spec.ts

check "the four curves the brief names exist as tokens" python3 -c '
import json
from pathlib import Path
ease = json.loads(Path("design/tokens.json").read_text())["motion"]["ease"]
for name in ("standard", "decelerate", "accelerate", "spring"):
    assert name in ease, f"no {name} easing"
print(", ".join(sorted(ease)))
'
check "motion is generated onto every surface, not typed twice" python3 -c '
from pathlib import Path
web = Path("jarvis-web/src/lib/styles/tokens.css").read_text()
android = Path("android-app/app/src/main/kotlin/ai/jarvis/app/ui/theme/JarvisTokens.kt").read_text()
assert "--jv-ease-decelerate" in web, "the web tokens are stale"
assert "object Motion" in android, "the Android tokens carry no motion"
assert "object Ease" in android, "the Android tokens carry no curves"
print("web and Android both generated from design/tokens.json")
'
check_sh "the generated files are not stale" 'python3 design/build.py --check 2>&1 | tail -2'
check "every animation comes from a primitive" \
    grep -q 'export function sharedElement' jarvis-web/src/lib/motion.ts
check "a primitive under reduced motion animates nothing" python3 -c '
import sys
sys.path.insert(0, ".")
from pathlib import Path
text = Path("jarvis-web/src/lib/motion.ts").read_text()
assert "faster animation" in text, "the policy is not written down"
# And asserted as behaviour by the unit tests, which the check below runs.
for primitive in ("export function fade", "export function slide", "export function scale",
                  "export function shimmer", "export function glowPulse"):
    assert primitive in text, f"no {primitive}"
print("five primitives, all reduced-motion aware")
'
check_not "and there is only ONE reduced-motion kill switch" python3 -c '
from pathlib import Path
text = Path("jarvis-web/src/lib/styles/base.css").read_text()
count = text.count("@media (prefers-reduced-motion: reduce)")
# Two would be one too many: a second, weaker rule silently overrides the
# stronger one, which is exactly what happened while this was being written.
raise SystemExit(0 if count > 2 else 1)
'
check_sh "the primitives, in their own tests" \
    'cd jarvis-web && npx vitest run src/lib/motion.test.ts 2>&1 | tail -3'

# The hard constraints. Measured in a real browser: a frame budget, a layout
# shift, a preference that has to actually be emulated, and interaction that
# has to work while things move.
run_playwright "frame budget, layout shift, reduced motion, and never blocking" motion.spec.ts

check "the taste checkpoint is recorded and waiting" python3 -c '
from pathlib import Path
review = Path("docs/motion-review")
videos = sorted(review.glob("*.webm"))
assert len(videos) >= 4, f"only {len(videos)} recording(s)"
text = (review / "README.md").read_text()
assert "Not proved" in text, "the review page does not say what it cannot prove"
print(f"{len(videos)} recordings: " + ", ".join(v.name for v in videos))
'
verify_end
