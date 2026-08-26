# Overnight brief — the 2 AM instructions

You are running unattended overnight on the in-progress JARVIS project (fully
local voice assistant: web, desktop, Android). Extensive planning and work
already exist — you are NOT starting fresh, NOT inventing your own plan, NOT
restyling to your own taste. Tonight: make genuine, verified progress on the
remaining milestones by driving the REAL JARVIS end-to-end, and leave a clean
morning trail.

**Orient first.** Read `CLAUDE.md`, `MILESTONES.md`, `PROCESS.md`,
`BLOCKERS.md`, `docs/AUDIT.md`, `docs/UI_MIGRATION.md`, and everything under
`docs/design/`, `docs/ui-review/`, `docs/motion-review/`. Design source of
truth: `design/tokens.json`; chosen direction: C2 (screenshots in
`docs/design/`). If something critical is missing or contradictory, log it in
`docs/OVERNIGHT_LOG.md`, make the safest assumption, flag it, and continue
with lower-risk work.

**Work method — plan, then plan the plan, then execute.** Write tonight's
execution plan to `docs/OVERNIGHT_LOG.md`: remaining unchecked milestones,
ordered dependencies-first and highest-value/lowest-risk-first, what you'll
parallelize, what you'll defer to my review and why. Per milestone, plan
before building (scope, machine-checkable verification, subtasks, which get
subagents), then implement. Follow `PROCESS.md`: commit per milestone with
clear messages, check a box only when its verify script actually passes now,
keep `CHANGELOG.md` current.

**Subagent management.** Use scoped subagents for parallel independent work
— research, per-page UI migration, test authoring, capability implementation
— each with a narrow tool allowlist and its own context budget. Respect the
per-task concurrency limit against the vLLM endpoint; queue beyond it, don't
over-fan-out. Roll results up to yourself and reconcile. Log which subagents
ran on what.

**Exercise the REAL JARVIS to verify — this is the point.** Drive the actual
running stack, not mocks, for everything reversible: real voice/text through
the live containers (`docker compose up -d --wait` first), real research runs
against the fixture site, real coding tasks in the sandbox (make the fixture
repo's tests pass, containment holds), real subagent delegation with the live
agent tree, real memory writes/reads and forget (snapshot the affected volumes
first so it's rerunnable, restore after), real notes, real task
queue/schedule/cancel watched through the task UI via Playwright. Prove JARVIS
genuinely does each thing correctly and completely by making it do it and
checking the real result.

**Do NOT trigger irreversible external actions unattended.** No sending real
emails, no real outbound channel messages to anyone, no writes to my real
calendar, no financial actions, no destructive git (no force-push /
history-rewrite / branch-delete), no `docker compose down -v` on a volume with
real data without snapshot-and-restore. Those code paths get verified against
LOCAL FIXTURE servers instead (fixture inbox, CalDAV test container, mock
channel) — assert the fixture received it, then clean up. The real-credential
live smoke test of those paths waits for me in the morning, behind the
approval gate. Everything else, exercise for real.

**Verify everything (unattended = higher bar).** `make verify-all` passes;
token-lint zero hardcoded values; live-interaction suite green against real
containers at threshold (intent ≥ 95 %, WER ≤ 10 %, routing ≥ 90 %, round trip
≤ 2 s median, zero critical issues). Never weaken a test or threshold to force
a pass — if it can't pass legitimately, log it and move on. Capture
per-page/per-breakpoint screenshots to `docs/ui-review/` and
signature-surface captures to `docs/motion-review/`.

**Stay in scope; keep security up.** Only milestone work (+ the audit phase);
new ideas go to `docs/FUTURE.md` for my approval, not implemented tonight.
Prompt-injection quarantine, approval gates on irreversible actions, sandbox
containment, and secrets handling stay enforced — don't disable a guardrail
to make progress. No phone/device access; Android verified by build/unit/lint
only.

**Park what needs me.** Nav/tab consolidation structure → propose in
`docs/UI_MIGRATION.md`, migrate only unambiguous pages, hold judgment calls.
Home screen/reactor and motion taste → build, capture, mark awaiting-review,
iterate on my notes later. "Should we even do this" → `docs/FUTURE.md`.

**If blocked and out of safe work: stop cleanly, don't thrash.** A parked
task is fine; a broken repo is not.

**Morning handoff — write before you stop.** End `docs/OVERNIGHT_LOG.md`
with: what you completed (with verify evidence), what you deferred and why,
every assumption made, everything awaiting my review, the cold state of the
build, and clear next steps. Assume I read only this file first.

---

## The goal that drives the loop

Maximal *verified* progress on the unchecked `MILESTONES.md` items within the
rails above — boxes checked only when their verify scripts pass now — ending
in a complete `docs/OVERNIGHT_LOG.md`. If the 2 AM prompt arrives while the
current work (M50 integration and after) is still in flight, that work is
finished first; this brief is queued behind it, not in place of it.
