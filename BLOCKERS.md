# BLOCKERS.md — what needs you, not more work from me

Only two kinds of thing belong here: something that needs hardware or a
service this account cannot reach, and something that needs a decision only the
operator can make. Everything else is a milestone, not a blocker.

---

## 1. ~~Docker access for `jarvisdev`~~ — **resolved 2026-08-25**

Kept, rather than deleted, because three claims elsewhere were written around it.

`jarvisdev` is now in the `docker` group and the socket is reachable:

```
$ id -nG | grep docker        → docker
$ docker run --rm hello-world → Hello from Docker!
$ docker compose ls           → jarvis running(3), jarvis-core restarting(1), running(6)
```

What that unblocks, and where it is now tracked:

* **The coding agent's live containment check** — M19 runs it for real.
* **SearXNG**, so `evals/research_eval.py --backend live` (the Scripted claim in
  `docs/verification.md`) can be run here: `docker compose --profile search up -d
  searxng`, then `SEARXNG_URL=http://127.0.0.1:8888`.
* **The whole compose-native testing addition** — M28 and M29.

Two things it immediately surfaced, both real and both fixed under M28: `photon`
restarts in a loop, and `jarvis-web` reports unhealthy.

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
