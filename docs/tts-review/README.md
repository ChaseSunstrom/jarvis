# TTS review — Piper against Kokoro, on this host

Five replies Jarvis actually gives, synthesised by both engines and then
transcribed back through the real Whisper. Regenerate with:

    python3 scripts/verify/tts_ab.py --out docs/tts-review

## What the numbers say

| | median synth | real-time factor | round-trip WER | cost |
| --- | --- | --- | --- | --- |
| Piper `en_GB-alan-medium` | 1.72 s | 0.52x | 0.040 | 33 MB of model, already in the stack |
| Kokoro `bm_george` | 1.42 s | 0.39x | 0.000 | 3.2 GB image, 1 GB resident |

Both synthesise comfortably faster than real time and the gap between them is
inside the run-to-run variance — two runs of this same script put Piper at
0.40x and 0.49x, and Kokoro at 0.47x and 0.42x. Word error is 0.00 for both
on most sentences; the one that moved was *"Front door or garage door?"*,
which Whisper mis-heard from Piper once out of two runs.

**So the numbers do not decide it, and this file exists because of that.**
The voice does, and that is not something a test can hold an opinion about.

| # | What it says | Piper | Kokoro |
| --- | --- | --- | --- |
| 1 | The ceiling lights are on, Sir. | [1-piper.wav](1-piper.wav) (1.18 s, 0.53x, WER 0.00) | [1-kokoro.wav](1-kokoro.wav) (0.98 s, 0.40x, WER 0.00) |
| 2 | I'm afraid there's no boiler pressure sensor in the house, so I can't tell you the reading. | [2-piper.wav](2-piper.wav) (2.35 s, 0.45x, WER 0.00) | [2-kokoro.wav](2-kokoro.wav) (2.18 s, 0.40x, WER 0.00) |
| 3 | It's sixteen degrees outside, and the kitchen window is still open. | [3-piper.wav](3-piper.wav) (1.85 s, 0.38x, WER 0.00) | [3-kokoro.wav](3-kokoro.wav) (1.64 s, 0.36x, WER 0.00) |
| 4 | I've queued that research and I'll tell you when it's done. | [4-piper.wav](4-piper.wav) (1.72 s, 0.53x, WER 0.00) | [4-kokoro.wav](4-kokoro.wav) (1.42 s, 0.41x, WER 0.00) |
| 5 | Front door or garage door? | [5-piper.wav](5-piper.wav) (1.07 s, 0.70x, WER 0.20) | [5-kokoro.wav](5-kokoro.wav) (0.91 s, 0.38x, WER 0.00) |

## The default, and why

**Piper stays.** Not because it wins — it does not, measurably — but because
it is 33 MB against 3.2 GB and it is already running. A tie is not a reason to
spend three gigabytes of somebody's disk.

## To switch

```bash
cd jarvis-core && docker compose --profile kokoro up -d jarvis-tts
```

then in `config/configuration.yaml`, under `voice:`:

```yaml
  tts:
    engine: openai
    url: http://127.0.0.1:8880/v1
    voice: bm_george        # bm_daniel, bm_lewis, bf_emma… /v1/audio/voices lists them
```

Nothing else changes: `jarvis/voice/openai_tts.py` returns exactly what the
Wyoming client returns, and `pipeline.py` does not know which one it has.
