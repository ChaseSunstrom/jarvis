# BLOCKERS.md — what needs you, not more work from me

Only two kinds of thing belong here: something that needs hardware or a
service this account cannot reach, and something that needs a decision only the
operator can make. Everything else is a milestone, not a blocker.

---

## 1. Docker access for `jarvisdev`

**Needed by:** M19 (the coding sandbox's live containment check), the research
scenarios' real SearXNG, and the compose-level checks in `docs/verification.md`.

`jarvisdev` is not in the `docker` group, so `docker ps` is a permission error
and nothing in this run can start, stop or inspect a container.

What is affected, precisely:

* **The coding agent's containment claim.** `container_argv()` is a pure
  function and every fence it builds is asserted offline (`tests/test_code_sandbox.py`),
  so the *command line* is proved. That a real container honours it is not, and
  cannot be from here.
* **SearXNG.** The research scenarios run against `testing/live/fixture_search.py`,
  which serves SearXNG's own `/search?format=json` shape over a fixture website
  this repository owns. Jarvis's real search client, fetcher and reader all run
  unchanged; SearXNG itself — its engines, ranking and rate limits — is not
  exercised.
* **The whisper container's flags.** The doubled-transcript issue in
  `ISSUES.md` may be a `condition_on_previous_text` setting on the
  faster-whisper container, which cannot be changed or tested without
  restarting it.

**To unblock:** `sudo usermod -aG docker jarvisdev` (then a new login), or run
the affected checks as a user who is already in the group.

## 2. A faster model for the voice path, or a GPU

**Needed by:** the full-mode latency threshold (median round trip ≤ 2 s).

Measured on this host: a spoken, tool-using turn takes 15–20 s end to end, of
which ~11 s is streaming and recognising the audio and ~6 s is the model. The
model is `qwen3.8-27b` on a remote llama-swap; there is no GPU on this box and
faster-whisper runs on four shared vCPUs.

This is not a code defect and no change in this repository will fix it. It
needs one of:

* a small model (3–8 B class) served at the same endpoint for the voice path,
  with the large one kept for research and coding; or
* a GPU on the model host, or on this one.

Which of those to do is an operator's decision about cost and quality, so the
threshold is reported as missed in `docs/LIVE_TEST_REPORT.md` rather than
quietly lowered.

## 3. Everything that needs a phone, a wall panel or a microphone

**Needed by:** M08's device backlog, M22's phone automation, and the rows in
`docs/verification.md` marked **Unproven**.

No device of any kind is reachable from this run, by instruction. The Kotlin is
verified by its JVM-side mirrors and (once a JDK is installed) by
`./gradlew assembleDebug`, lint and Robolectric; the on-device gates are listed
in `docs/ANDROID_DEVICE_TESTS.md` with the exact steps for whoever has the
hardware.

The same applies to a real microphone in a real room: the live rig synthesises
the user's speech and delivers it through the real audio paths, which proves
the pipeline but not the acoustics of your kitchen.
