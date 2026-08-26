# Future — ideas parked for the operator's approval

Per the overnight brief, nothing here is implemented; each is a proposal with
the evidence that raised it. Ordered by what it would be worth, not by how
long it would take. Move an item into `MILESTONES.md` to make it work.

## Higher value

1. **Run every scenario through the console as well as the API, in `--full`
   mode.** Tonight was the first time any scenario drove the real console,
   and the one that did (`resilience-core-restart` as `text-ui`) found a
   defect the API variants cannot see: the chat thread does not survive a
   core restart (`ISSUES.md`). The transport now works; making `voice-ui` /
   `text-ui` part of `--full` for every scenario is a `variants` default and
   an evening of triage.
2. **A faster path for background tasks on this hardware.** A "look into
   every sensor" task is a step per model round trip; on a remote 27–30B
   model with no GPU that is 10–30 s a step and a five-minute job. The
   proactive-moment scenario's 240 s budget is honest and this box cannot
   meet it (`BLOCKERS.md` §2). Options: a smaller model for the planner's
   bookkeeping steps, batching read-only steps into one tool round, or a
   GPU. Measure before choosing.
3. **Forgetting from notes as well as memory.** `forget` now blanks the
   transcript turns that carried a fact; a note written from the same
   sentence (`note_create`) keeps it. Whether "forget that" should reach a
   note is the operator's call — a note is a document, and deleting documents
   on a spoken instruction is exactly what the approval gate exists for.

## Lower value

4. **The graph's labels could be measured, not estimated.** Collision
   avoidance uses a per-character width; a `getBBox()` pass after first
   paint would let two long labels sit closer without flipping.
5. **The phone's voice screen through the rig.** ADT-031…035 record what only
   a handset can confirm; an emulator in CI could carry the instrument's
   goldens at more than one density.
6. **Retire the `--no-browser` flag's second meaning.** It both skips the
   console build and, now, skips the browser variants; two flags would read
   better once the browser variants are routine.
