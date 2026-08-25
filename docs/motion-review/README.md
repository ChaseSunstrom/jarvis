# Motion review — for your eyes, not the test suite's

Four recordings of the moments that carry the character of the interface.
`jarvis-web/e2e/motion.spec.ts` can prove the motion is smooth, consistent and
accessible; it cannot prove any of it feels good, and no test will.

| | What it shows |
|---|---|
| [1-boot.webm](1-boot.webm) | The boot sequence, from a cold session to the interface settled. Skippable with any key or click, and it never blocks typing |
| [2-orb-states.webm](2-orb-states.webm) | idle → listening → thinking → speaking. In the running app the level is real audio: the microphone's amplitude while listening, the player's while speaking |
| [3-task-running.webm](3-task-running.webm) | A task arriving and working: the bar, the steps opening, tool calls appearing while it runs |
| [4-navigation.webm](4-navigation.webm) | Moving between pages — entrances, staggered lists, and what a route change looks like |

## Re-record them

```bash
cd jarvis-web && npx playwright test motion-review.spec.ts
python3 - <<'PY'   # copies them here; see the milestone's verify script
PY
```

## What is already proved, and what is not

**Proved** (`scripts/verify/m44-motion.sh`):

* the frame budget holds while the boot sequence and the orb are both running;
* the page does not shift under a finger (cumulative layout shift < 0.1);
* the boot sequence never blocks interaction — typing works during it;
* `prefers-reduced-motion: reduce` stops **everything**, including the orb's
  rAF loop, and the interface still works;
* every duration and curve is a token, enforced by `token_lint.py`.

**Not proved, and this is what your notes are for:**

* whether the boot sequence is the right length, or is showing off;
* whether the orb's four states read as four states at a glance;
* whether the stagger on a long list is a wave or a delay;
* whether any of it is, in the end, cool.

Notes go straight into the next iteration — this milestone is not finished
until you have watched these and said so. It is tracked as `BLOCKERS.md` §5
rather than as more work waiting on me, because it is not.
