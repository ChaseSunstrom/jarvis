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

On the phone: **Settings → Whose voice → TEACH JARVIS MY VOICE**. On the
console: **SETTINGS › Voice → ENROL**. The screen lists the phrases your server
asked for and marks off the ones you have given — on the phone **tap to start,
tap again when you have finished the line**, one line per tap; on the console
RECORD, then STOP.

**A sample is enrolled under a name** (M71). The box above the phrases — "who
is this?" — is who is reading them; leave it empty and the sample goes to the
server's default person, `owner`, which is who every enrolment before names
existed went to. A new name is a new person, up to the server's `max_people`
(eight; a store with dozens of voiceprints is a store nobody is curating), and
a name is at most forty printable characters, matched case-insensitively —
"Ted" and "ted" are one person. Every write carries it as `?label=`. The phrase
list follows THAT person's count, so two people enrolling on one phone are not
asked each other's phrases.

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
because there is no per-sample delete in the API. Deleting is per PERSON:
`DELETE /api/voice/speaker?label=Ted` forgets Ted and keeps everyone else —
FORGET on a person's row, on the phone and on the console — and a bare
`DELETE /api/voice/speaker` forgets everyone, which is what the phone's bottom
button (FORGET MY VOICE, or **FORGET EVERYONE** once there is more than one)
and the console's FORGET do.

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

Then read the numbers. `TEST MY VOICE` on the phone, or TEST on the console,
scores a fresh utterance against everyone enrolled and says **who** it was —
"Recognised as Ted · 2.31 against 8.83" — and whether enforcement would have
refused it; a refusal says who it was nearest, so a false reject of the owner
reads as "nearest: owner" rather than as a stranger. Get somebody else to try
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
Omit it and each profile uses what enrolment worked out from its own
leave-one-out spread: the **worst** enrolment sample times 1.25. The worst
rather than the average on purpose — the average tells you how you usually
sound, and a gate is not troubled by the usual case. Name one and it applies to
every enrolled person, and it is held on the gate rather than written into the
profiles: enrolment recomputes each profile's own suggestion after every
sample, and a configured number that lived in the profile was overwritten by
the next phrase until the next restart put it back. The status payload says
which is live (`configured_threshold`), and the screens say "set in
configuration.yaml; enrolment suggests …".

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

## Who is speaking

With more than one person enrolled the question stops being "is this the
owner" and becomes "who is this". A turn's audio is compared with everyone,
and the best verdict wins — an accepted one over a refusal, and among those the
lowest score — so two people who sound alike are each credited to whoever they
fit better, never to whoever enrolled first. The verdict carries two names and
they are never the same field: `label` is who it was, set only when the gate
accepted the voice; `nearest` is the closest enrolled person, set on a refusal
too, so a consumer reading `label: owner` as "the owner spoke" is never handed
the nearest miss under that key.

**The agent is told.** A recognised turn reaches the conversation agent with
`speaker=<label>`, and the system prompt ends with one line: *The person
speaking was recognised by voice as Ted.* Only ever a name the gate accepted:
a turn it could not judge — typed text, `mode: off`, a half-second "stop" —
gets no line rather than "unknown", because "unverified" and "stranger" are
different claims and a prompt that called the owner with a cold "unrecognised"
would have the model treating them as an intruder. It is context, not
authority: the tier system still asks a human before anything irreversible,
and the line unlocks nothing. It goes after the clock so the cached prompt
prefix survives a change of speaker.

**The house sees it.** `speaker-end` reaches the client that ran the turn, as
before. The pipeline also fires `jarvis_speaker_verdict` on the bus for every
other surface — the console's activity strip and the phone's draw a `speaker`
row from it: the name when accepted; "unverified" for audio too short or too
quiet to judge, which is never painted as a stranger; "not recognised ·
refused · nearest owner", failed only when the gate actually refused the turn.
The fields are `tests/contracts/speaker_verdict.json`, which the server's
tests, the console's and the phone's mirror all read. No event ever carries
audio, a vector or the transcript.

**Adaptation learns the right person.** With `adapt: true`, a confident turn
teaches only the profile that accepted it; the other people's profiles are not
moved by a voice that was not theirs.

## What is stored, and where

The **voiceprints** are `<config>/.storage/voice_profile.json`, chmod 600
like every other store: for each person, a handful of 46-float vectors, a
threshold and a name (`people: [...]`, store version 2; a version-1 file — one
profile at the top level, from before names — loads as one person called
`owner`, so an upgrade keeps whoever was enrolled). It is biometric data and
it never leaves the box — no endpoint returns the vectors, only counts, scores
and timestamps, per person. `DELETE /api/voice/speaker?label=Ted` removes one
person's; a bare DELETE, or **FORGET EVERYONE** on the phone, overwrites it.

**Enrolment is a durable write about a person**, and it is reachable only over
REST with a credential a person holds — the phone's bearer token, or the
console password. There is no tool for the model and no socket command, so no
turn, and in particular no turn that has read untrusted content, can enrol
anybody; `test_no_tool_and_no_websocket_command_can_enrol` pins it and
`security.md` says why that is the tier.

The **audio** is not stored at all. A turn's PCM is held in memory only while a
gate is active, capped at 20 seconds, dropped the moment the verdict lands, and
cancelled with the run if the turn dies. Enrolment samples exist for the length
of one HTTP request. There is no debug flag that writes a recording to disk.

## The API

Every route takes an optional `label` in the query string — the person's
name. Without one, `enrol` adds to `owner`, `verify` compares with everyone
and says who, and `DELETE` forgets everyone.

| | |
|---|---|
| `GET /api/voice/speaker[?label=]` | mode, the phrases, `people` (one summary each), `configured_threshold`, and at the top level one person's counts, scores and suggested threshold — the named one, else the first — so a client from before names keeps working; `enrolled` is whether anybody is, `person_enrolled` whether that person is |
| `POST /api/voice/speaker/enrol[?label=]` | one sample — WAV, or raw 16 kHz mono PCM — for that person; 400 for a name that cannot be one, 409 when the house holds `max_people` already ("forget somebody first" — never a quiet eviction) |
| `POST /api/voice/speaker/verify[?label=]` | score a sample without enrolling it: `verdict.label` says who, `verdict.nearest` who it was nearest, `would_block` what enforcement would do; with `label`, against that person only (404 if they are not enrolled) |
| `DELETE /api/voice/speaker[?label=]` | forget one person (404 if unknown), or everyone |

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
your own voice can close it. `tests/test_speaker_gate.py` settles the household
on the same cast — the owner and the soprano enrolled under two names, the
baritone as the stranger: each credited to their own person, the stranger to
nobody and nearest the owner, the agent told, the bus fired, the store
round-tripped, one person forgotten while the other stays. Everything is
seeded — a flaky biometric test teaches you to re-run it.
