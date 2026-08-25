# PROCESS.md — working rules for the autonomous phase

These are the rules I (Claude, working in this repository) follow while taking
Jarvis from the audited state in `docs/AUDIT.md` to the target state in
`MILESTONES.md`. They exist so that a session that starts cold — after a
context reset, on another day, in a subagent — behaves the same way as the one
that wrote them. `CLAUDE.md` still applies underneath; this file is the layer
for the milestone run.

## 1. The unit of work is a milestone

- `MILESTONES.md` is the plan and the ledger. Work happens on the first
  unchecked milestone whose dependencies are checked, in the order written,
  unless §3 (parallel work) applies.
- Before touching code: run the milestone's verify script
  (`bash scripts/verify/mNN-*.sh`). **It must fail.** A check that passes
  before the work exists is not checking the work; fix the check first, in its
  own commit, and say why in the commit body.
- Do the work. Re-run the script until it is green. Then run `make verify-all`
  (or, when the full run is too slow for the loop, `ONLY="m00 mNN <every
  milestone whose script touches the same surface>"` — and the full run before
  the commit).
- A milestone is **done** when, and only when: its verify script passes; `make
  verify-all` shows no milestone that was green going red; `docs/verification.md`
  and `DEVIATIONS.md` say what changed (`/claims`); `CHANGELOG.md` has the
  entry; for Android work, `docs/ANDROID_DEVICE_TESTS.md` lists every check
  deferred to a device; the checkbox in `MILESTONES.md` is ticked in the same
  commit as the work.
- One milestone, one commit (plus the commits for fixed checks and for
  blockers). The subject line names the milestone and the behaviour, in this
  repository's style — sentence case, no `feat:` prefix, the *change* not the
  diff (`M04: a running job shows every tool call as it happens`). The body
  says what was wrong, what was done, how it was checked, what was not done,
  and ends with the suite counts.

## 2. Verification is the engine, and it is not negotiable

- The verify scripts are the definition of the target state. They run every
  check and report every failure; they have no "skip". A feature that cannot
  be verified on this host is a failing check that names what is missing.
- Never make a check pass by weakening it: no `|| true`, no `-` prefix in a
  Makefile recipe, no `continue-on-error`, no `test.skip`, no shrinking a
  grep's scope, no editing a count to match. If a check is genuinely wrong,
  change it in its own commit whose body explains the mistake.
- Never leave a `MUTANT` or `DELIBERATELY BROKEN` marker in the tree; never
  re-run a flaky test until it is green and call it done — a flaky test is a
  bug with a name.
- Numbers in docs come from commands, not from memory. `docs/verification.md`
  is re-measured, never hand-edited.
- Every new feature gets its automated proof in the same milestone: unit
  tests where the logic is, a Playwright spec where the behaviour is visible,
  a contract table in `tests/contracts/` where two languages must agree.

### 2b. And it has to work in the stack that actually runs

From M28 on, the compose stack is the runtime under test. `docker compose up -d --wait` is the
first step of the live suite; the scenarios talk to the real services; a container that is
unhealthy at the start or has ERROR-level log lines at the end fails the run. Destructive
scenarios snapshot the named volumes they touch and restore them afterwards, so the suite is
re-runnable against a live stack rather than against a fresh fake.

Two containers were broken for two days while every suite was green (`photon` restarting,
`jarvis-web` unhealthy). That is what testing against a copy of the runtime buys you.

### 2c. Nothing new ships because it sounds good

Every service in the local AI toolbelt follows one contract: a baseline snapshot of the numbers
this suite already reports (research pass rate, routing accuracy, WER, per-stage latency), then
the change, then the same numbers. If they did not improve, the service comes out. The choices
and the rejections — with the sources — go in `docs/TOOLING_DECISIONS.md`, and nothing takes GPU
residency without a written VRAM justification.

### 2d. And it has to be safe to point at the internet

From M38 on, Jarvis grows reach: messaging channels, a calendar, a mailbox, downloadable
skills, an optional cloud provider. Every one of those is an inbound path, and the assistants
this capability set is modelled on shipped 140k internet-exposed instances and a marketplace
supply-chain attack. So five rules bind every milestone in that block, and each is asserted
rather than asserted-to:

1. **Nothing is exposed to the public internet** — tailnet or loopback, no static tokens in
   URLs. An unknown sender is ignored and the fact is logged; it is never served.
2. **External content is data, never instruction.** Everything fetched, received or installed
   is quarantined and stripped of chat-template control literals before a model sees it, and
   it can never silently trigger a state-changing tool — those hit the approval gate whatever
   the content asks for.
3. **Least privilege.** Narrowest tool allowlist and credential scope that works, per
   subagent, per integration, per skill. No ambient god-tool.
4. **Secrets are injected at call time** and never persisted into memory, notes, logs or
   traces.
5. **Nothing installs or runs unseen**: allowlisted source, pinned ref, recorded hash,
   declared permissions shown and approved, execution sandboxed.

The red-team scenario file in the live suite is where this is decided — injection through a
fetched page and through an inbound message, a cross-conversation leak probe, a
non-allowlisted sender, a malicious skill install. **The suite fails if any probe succeeds.**

### 2a. And it has to work when somebody talks to it

From M24 on, a capability is not done until its **live scenarios** pass:
`testing/live/` synthesises a user's speech with Piper, delivers it through the
real audio-input API and through a real browser's microphone, and transcribes
Jarvis's spoken answers back with the same Whisper the system itself uses.

- Every milestone that builds a capability runs its own slice of the suite
  (`LIVE_CAPABILITY=<name>`); every other one runs a named smoke subset. Both
  are in the milestone's verify script, and neither is optional.
- Scenarios are written **against the target state, now** — a capability that
  does not exist yet has its scenarios in the tree marked
  `gated-on: <milestone>`. They are not skipped: `--implemented-only` does not
  select them and full mode runs them and fails. There is still no third
  outcome.
- Assertions are about the *house*, not the wording: the service that was
  called, the state that changed, the task that appeared, the file that was
  not written. The local-LLM judge grades only what a deterministic check
  cannot express, and every verdict it gives is logged with its reason.
- A defect found by talking to Jarvis gets an `ISSUES.md` entry **and** a
  regression scenario, in the same change that fixes it. An entry with no
  regression scenario has to say why one cannot exist.
- Thresholds (full mode): intent ≥ 95 %, WER ≤ 10 %, routing ≥ 90 %, median
  round trip ≤ 2 s, zero critical issues. A threshold that cannot be met on
  this hardware is reported as missed in `docs/LIVE_TEST_REPORT.md` and
  written up in `BLOCKERS.md` — never re-scored to fit.

## 3. Parallel work

- Milestones marked `parallel-ok` in `MILESTONES.md`, with no shared files or
  directories, may run at the same time — one subagent per milestone, each in
  its own git worktree, each owning its branch until its verify script is
  green. The main session integrates: rebase, run `make verify-all`, commit.
- Two agents never edit the same surface (`jarvis-web/`, `jarvis-core/jarvis/`,
  `android-app/`, `jarvis-desktop/`) at once. Cross-surface milestones
  (design tokens, contracts) are serial and go first.
- A subagent reports facts and file paths, not conclusions; the main session
  verifies by running the script before ticking anything.

## 4. Blocked means: write it down and move on

- If a milestone cannot be finished — a toolchain that needs root, a service
  that is not reachable, a design question only the owner can answer — append
  an entry to `BLOCKERS.md`: the milestone, what was tried (commands and
  errors verbatim), what would unblock it, and the date. Then take the next
  milestone whose dependencies are met. **Do not stop.**
- A blocked milestone keeps its failing verify script. The table in
  `make verify-all` is allowed to be red for exactly the milestones listed in
  `BLOCKERS.md`, and for nothing else.
- At the start of every session, re-read `BLOCKERS.md` and re-run the verify
  script of each blocked milestone: the world changes (a package installed,
  a service came up) and a blocker that has cleared is the next job.

## 5. Scope

- The scope is `MILESTONES.md`. Discovering necessary work that no milestone
  covers means **adding a milestone** — with its verify script and size — in
  the right place in the order, in its own commit, before doing the work. The
  diff for a milestone never quietly grows to include something else.
- Reuse first. `docs/AUDIT.md` names what exists and whether to keep, extend
  or replace it; a milestone that rewrites something the audit said to keep
  needs a sentence in the commit body saying what the audit missed.
- Deleting behaviour is a scope change too. Behaviour that the target state
  does not mention stays unless a milestone says otherwise.

## 6. Hard constraints, restated as actions

- **100 % local.** The only model endpoint is the configured OpenAI-compatible
  `LLM_URL` (llama-swap / llama.cpp); STT is Wyoming whisper, TTS is Wyoming
  piper. No SDK, client library, or URL for a cloud AI or SaaS service enters
  any requirements file, `package.json`, Gradle catalog, or source. Tests use
  the fakes in `testing/` and `tests/web/mock-ha.mjs`.
- **One design-token source.** `design/tokens.json` is the only place a
  colour, size, radius, shadow or duration is typed by a human. Every other
  token file is generated and diffed by a verify script. A hard-coded value
  in a surface is a failing check, not a style preference.
- **Headless host.** Verification uses Playwright headless Chromium, Node,
  pytest, Gradle, Xvfb where a window system is unavoidable. Nothing needs a
  display, a microphone or a speaker; nothing talks to the live containers on
  :8080/:8199 — the verify runs use `E2E_PORT` and the mock backend.
- **No device access, ever, in this run.** No `adb`, no emulator, no
  `connectedAndroidTest`, no attempt to reach a phone, head unit or desktop
  session. Android is proven by `./gradlew assembleDebug`, `testDebugUnitTest`,
  `lintDebug` (blocking) and Robolectric/JVM screenshot tests; everything else
  goes into `docs/ANDROID_DEVICE_TESTS.md` as a precise deferred check. Phone
  automation ships behind a compile-time flag that is OFF.
- **This host has no root and no Docker socket access** for `jarvisdev`.
  Toolchains go under `$HOME` (JDK, Android SDK, Gradle, Playwright browsers);
  anything that needs `apt` or the Docker daemon is a blocker, not a workaround.

## 7. Session hygiene

Start: `git status` (clean, on the working branch), `make verify-all ONLY=m00`,
read `BLOCKERS.md`, read `MILESTONES.md` to find the next unchecked milestone.

End (or before a context reset): commit or stash nothing half-done — a
milestone in progress is left as a WIP commit on its own branch with
`WIP:` in the subject and a `## Where I stopped` section in the body; the
`CHANGELOG.md` `Unreleased` section is current; `BLOCKERS.md` is current.

Report to the owner in plain terms: what got ticked, what is red and why,
what is next. No "should work"; if it was not run, say it was not run.
