# Answering only your voice

Jarvis can unlock doors, send messages and run shell commands. Until this
existed, the only thing between a voice and all of that was possession of the
room: anyone within earshot of a satellite, or holding the phone, was the owner
as far as the pipeline was concerned.

This is the other question — *whose voice was that* — asked on the audio, before
the intent stage sees the words.

```
mic ──► wake ──► stt ─┬─► speaker ──► intent ──► tts
                      │      │
                      │      └─ refused: the turn stops here and the
                      │         transcript never reaches the agent
                      └─ the same audio, verified in a worker thread while
                         the recogniser is still working
```

## What it is worth

Be clear about this before turning it on.

It raises the cost of talking to Jarvis from **"be in the room"** to **"sound
like the owner to a spectral matcher"**. That stops a house guest, a television,
a smart speaker in the next room and a stranger at the window — which is most of
what actually happens.

It does **not** stop a recording of your voice, and it is not a second factor
for anything that matters. A determined attacker with a clip of you talking gets
through. The tier system and its human approval gate are still what stand in
front of the dangerous verbs, and nothing here changes that: an accepted voice
still cannot send an SMS without a human tapping APPROVE on the phone.

It is also a **classical** verifier — MFCC statistics and a pitch distribution
with a per-dimension z-test — not a neural speaker embedder. An ECAPA-TDNN
trained on thousands of speakers is markedly better at this. What the classical
route buys is that it runs in the process that is already running, in a fraction
of the time the recogniser takes, with no model file, no GPU and no new
dependency, on the same Pi that is already doing STT. `Embedder` in
`jarvis/voice/speaker.py` is the seam if you have somewhere to run a real one.

## Turning it on, in the right order

**Do not skip the middle step.** The threshold that suits your voice, your
microphone and your room is not knowable in advance, and the failure mode of
guessing is that Jarvis stops answering *you* — which you will read as "the wake
word broke", not "the threshold is 0.4 too low".

### 1. Enrol

On the phone: **Settings → Whose voice → TEACH JARVIS MY VOICE**. The screen
lists the phrases your server asked for and marks off the ones you have given —
**tap to start, tap again when you have finished the line**, one line per tap.

Two details that used to be wrong here. It is *not* press-and-hold: holding does
nothing, and the label says `TAP TO SPEAK`. And the count is not five — it is
whatever your server's `min_samples`/`max_samples` say, which is why the screen
shows "3 of 20 samples" rather than a fixed list. The phrases themselves come
from the server too (`prompts` in the status payload), so the phone and the
console offer the same ones.

The screen tells you about each sample as you give it: a line that was too short
or had no measurable pitch says so straight away, which is the whole reason the
API takes one sample per request. **SAY THAT ONE AGAIN** re-offers the phrase you
just read — worth knowing that it cannot *remove* the sample already stored,
because there is no per-sample delete in the API; `DELETE /api/voice/speaker`
(FORGET MY VOICE) is all-or-nothing.

Say them **the way you would actually say them** — the question as a question,
the order as an order. This is the single biggest thing between a gate that
works and one that locks you out, and it is not a style note:

> The profile's denominator is how much *you* vary between utterances. Enrol
> five calm, identical-sounding phrases and it learns that you never vary; then
> the first time you ask a question, or snap an order, or have a cold, the pitch
> block alone reads several standard deviations out and the turn is refused.

Measured on the synthetic cast in `jarvis-core/tests/synth_voice.py`: with five
same-pitch enrolment samples the owner's own held-out utterances *overlap* the
nearest impostor's. With five that vary in length, level and pitch — the same
speaker, the same five utterances' worth of effort — the owner's worst score is
7.6 and the nearest impostor's best is 9.3, with nothing in between.

That is why the phrases are served from `/api/voice/speaker` rather than typed
into a screen: both surfaces read one list, and each line is chosen to move
something.

### 2. Observe

In `configuration.yaml`:

```yaml
voice:
  speaker:
    mode: observe
```

`observe` runs the whole check, emits the same `speaker-end` event with the same
verdict, and **lets every turn through**. Leave it there for a few days of
ordinary use.

Then read the numbers. `TEST MY VOICE` on the phone scores a fresh utterance and
tells you whether enforcement would have refused it. Get somebody else to try
it too — the gap between their score and yours is the thing you are setting a
threshold in the middle of.

### 3. Enforce

```yaml
voice:
  speaker:
    mode: enforce
    threshold: 8.8      # optional; enrolment's own suggestion is used otherwise
```

## Every setting, and what it costs

```yaml
voice:
  speaker:
    mode: off                 # off | observe | enforce
    threshold: 8.8            # mean squared z. Lower is stricter.
    on_reject: speak          # speak | silent
    refusal: "I'm sorry, I don't recognise that voice."
    allow_unverifiable: true
```

**`mode`** — `off` is the shipped default and costs nothing: the audio is not
even buffered. An unknown value falls back to `off` with an error in the log,
never to `enforce`; a typo must not be able to lock you out of your own house,
and must not silently disable a gate you meant to turn on either.

**`threshold`** — in units of standard deviations from your enrolled centre,
averaged over all 46 dimensions, so it keeps its meaning when the room changes.
Omit it and the profile uses what enrolment worked out from its own leave-one-out
spread: the **worst** enrolment sample times 1.25. The worst rather than the
average on purpose — the average tells you how you usually sound, and a gate is
not troubled by the usual case.

**`on_reject`** — `speak` is the default and the choice is not obvious. An
assistant that silently ignores you is indistinguishable from one that did not
hear you, and a false reject is the failure this feature will actually produce;
the person being locked out has to be told why. `silent` is a supported choice
for anyone who would rather a stranger learn nothing — it still logs and still
emits the event, so "silent" never means "invisible".

**`allow_unverifiable`** — the most consequential default in the file.
"Stop", "yes", "louder", "no, the other one" are all under half a second, and
an assistant that refuses every short word is not usable. Audio too short, too
quiet or too breathy to judge is therefore let through by default. The exposure
that buys back is bounded: an attacker who can only pass unverifiable audio can
only say things too short to carry a sentence, and everything dangerous is still
behind human approval. Set it `false` if that trade is wrong for you.

## Things that will surprise you

**Whispering is refused, even yours.** An utterance with no measurable pitch is
scored on timbre alone — otherwise "I could not measure your pitch" would read
as "your pitch is wrong", and being recorded quietly would make you a stranger.
But it is then refused anyway, with its own reason, because a block an impostor
can switch off is a block an impostor *will* switch off. The breathy speaker in
the test cast yields no F0 in five of six utterances and scored 6.2 against a
threshold of 9.0 on timbre alone. It was getting in.

**A crash lets the turn through.** A verifier that throws has not said "this is
a stranger", and treating a bug as a refusal would lock you out of your own
house on a traceback. It is logged loudly and the turn proceeds.

**Transcribing on the phone suspends itself while this is enforcing**, and you
do not have to remember to do anything. A turn the phone transcribes locally
sends words rather than sound, so there is nothing to check — with both switched
on, every turn used to walk past the gate. Neither setting looks dangerous on
its own, which is why this is handled in code rather than in a warning:

* the phone stops using the local path while the gate enforces, streams instead,
  and the settings screen's status line reads SUSPENDED with the reason;
* the server refuses a transcript that admits it came from a microphone it never
  heard, so the guarantee holds even if the phone is old or misconfigured.

It cannot simply move to the phone. Android's on-device recogniser *owns the
microphone*: the app gets partial text and a level, never samples, so there is no
audio there to check. See [`../DEVIATIONS.md`](../DEVIATIONS.md) §10.

**The console's text chat is not gated.** Typing is authenticated by the bearer
token, which is a stronger credential than a voice. This gate is about who is
talking in a room where the microphone is open to whoever is standing there.

## What is stored, and where

The **voiceprint** is `<config>/.storage/voice_profile.json`, chmod 600 like
every other store: a handful of 46-float vectors and a threshold. It is
biometric data about one person and it never leaves the box — no endpoint
returns the vectors, only counts, scores and timestamps. `DELETE
/api/voice/speaker`, or **FORGET MY VOICE** on the phone, overwrites it.

The **audio** is not stored at all. A turn's PCM is held in memory only while a
gate is active, capped at 20 seconds, dropped the moment the verdict lands, and
cancelled with the run if the turn dies. Enrolment samples exist for the length
of one HTTP request. There is no debug flag that writes a recording to disk.

## The API

| | |
|---|---|
| `GET /api/voice/speaker` | mode, sample count, your own scores, the suggested threshold, the phrases |
| `POST /api/voice/speaker/enrol` | one sample — WAV, or raw 16 kHz mono PCM |
| `POST /api/voice/speaker/verify` | score a sample without enrolling it, and say whether it would be refused |
| `DELETE /api/voice/speaker` | forget the voiceprint |

Enrolment takes one sample per request because the useful feedback is per
sample: "that one was too quiet, say it again" between phrases, rather than one
failure for the whole set at the end.

## How it actually works

`jarvis/voice/dsp.py` — a hand-written radix-2 FFT, a mel filterbank stored as
runs so a band only touches the bins it covers, a DCT, and autocorrelation via
Wiener-Khinchin. Stdlib only, for the reason `audio.py` gives: jarvis-core
installs from wheels with no compiler on a Pi.

`jarvis/voice/speaker.py` — per voiced frame, a normalised power spectrum, 26
log-mel bands, 20 cepstral coefficients with c0 dropped (it is how loud you were
and how far from the microphone, which is the one thing about a frame that says
nothing about who produced it). Mean and standard deviation of those across the
utterance, plus a soft-assigned log-F0 histogram: 46 dimensions.

The score is the mean squared z-score against the enrolled per-dimension spread,
with that spread shrunk toward a prior estimated from the enrolment set itself.
Not cosine similarity: cosine between two utterances of ordinary speech sits
near 0.95 whoever is talking, so an absolute cosine threshold is a magic number
that means nothing until it is tuned per microphone and silently means nothing
afterwards.

Deviations are **not** clipped, though the obvious robustification says they
should be. It was tried: clipping each dimension's squared z at 25 pulls the
owner's worst case from 7.6 to 3.9 and the nearest impostor's best from 9.3 to
3.5 — it compresses the impostor harder than the owner and destroys the
separation it was meant to protect. An impostor differs on many dimensions at
once, which is exactly the signal a clip throws away.

## What the tests can and cannot settle

`tests/test_speaker.py` runs against `tests/synth_voice.py`, a source-filter
synthesiser: a glottal pulse train at F0 with jitter, through formant
resonators, plus breath noise. That is the verifier's own claim about what
distinguishes people, written as a signal generator.

It settles that the code separates signals differing in exactly the cues it says
it uses, and that it holds one speaker together across different words, lengths
and levels. **It cannot settle accuracy on real human speech**, and no test
claims to; that row is Unproven in [`verification.md`](verification.md) and only
your own voice can close it. Everything is seeded — a flaky biometric test
teaches you to re-run it.
