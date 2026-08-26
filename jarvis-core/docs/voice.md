# Voice

Wake word, speech to text, the model, and speech back out. Four containers on
one machine, no cloud leg anywhere in the path.

```
mic (satellite / phone / browser)
  │  16 kHz mono PCM, Int16LE, over the websocket
  ▼
wake ──── wyoming-openwakeword :10400   "hey jarvis"
  │
  ▼
stt  ──── wyoming-whisper      :10300   audio → text
  │
  ▼
intent ── llm (ollama)         :11434   text → answer + tool calls
  │                                     tools dispatch to domain services
  ▼
tts  ──── wyoming-piper        :10200   text → 22.05 kHz WAV
  │
  ▼
/api/tts_proxy/<token>.wav              the client fetches and plays it
```

## Wiring

`docker-compose.yml` runs all four with `network_mode: host`, so
`configuration.yaml` reaches them on loopback:

```yaml
voice:
  language: en
  stt:  {host: 127.0.0.1, port: 10300}
  tts:  {host: 127.0.0.1, port: 10200, voice: en_GB-alan-medium}
  wake: {host: 127.0.0.1, port: 10400, model: hey_jarvis}
```

`tts: voice:` has to match `PIPER_VOICE` in `docker-compose.yml` — that is the
voice `wyoming-piper` is started with and therefore the one it has loaded.
Naming a different one is not an error, but every utterance then depends on
Piper fetching it, which is slow the first time and impossible offline.
`tests/test_packaging.py` compares the two and fails if they drift.

The Wyoming client is written from scratch over asyncio TCP — newline-delimited
JSON headers with an optional binary payload — so there is no `wyoming` package
to install and no version skew with the containers. Connections are made per
call rather than held open, which costs a few milliseconds and buys immunity to
a container restart underneath you.

Check what each service reports about itself:

```bash
curl -s localhost:8080/api/config -H "Authorization: Bearer $TOKEN"
docker compose logs wyoming-piper | tail
```

## Whose voice

Jarvis can be told to answer only you. It is off by default, and turning it on
has a right order — enrol, observe, then enforce — because the threshold is not
knowable in advance and guessing it locks you out rather than a stranger.

```yaml
voice:
  speaker:
    mode: observe        # off (default) | observe | enforce
    # threshold: 8.8     # optional; applies to every enrolled person, and
                         # wins over each profile's own measurement
```

More than one person can be enrolled, each under a name (`?label=` on the
API; the "who is this?" box on the phone and the console), and a turn is
compared with everyone: the verdict says who, the agent is told, and the
activity strips draw a stranger. The whole thing, including what it is and is
not worth, is in [`../../docs/voice-identity.md`](../../docs/voice-identity.md).

## Pipelines

A pipeline is a named end-to-end configuration a client asks for by name. The
first one defined is the default.

```yaml
voice:
  pipelines:
    - name: Jarvis
      voice: en_GB-alan-medium
      wake_word: hey_jarvis
      language: en
    - name: Guest
      voice: en_US-lessac-medium
      wake_word: ok_nabu
```

Per-pipeline keys: `name`, `id`, `language`, `voice` (alias for `tts_voice`),
`wake_word`, `stt_engine`, `tts_engine`, `conversation_engine`. Anything else
is kept in `extra` and passed through.

`en_GB-alan-medium` is the voice the persona is written for, and the one the
shipped compose file loads. A second pipeline naming another voice — `Guest`
above — makes Piper download it on first use into `./wyoming/piper`: slow once,
and a hard failure on a box with no internet. If a different voice is the one
you actually want, change `PIPER_VOICE` rather than adding a pipeline. The full
list is in the Piper samples page; the naming is `<lang>-<speaker>-<quality>`.

## Running a pipeline

Same contract as Home Assistant's `assist_pipeline`, because the clients were
written against it:

```
client  {"id": 2, "type": "assist_pipeline/run",
         "start_stage": "wake", "end_stage": "tts",
         "input": {"sample_rate": 16000}}
server  {"id": 2, "type": "result", "success": true, "result": null}
server  {"id": 2, "type": "event", "event": {"type": "run-start", "data": {
           "runner_data": {"stt_binary_handler_id": 1, "timeout": 300}}}}
client  <binary>  0x01 + Int16LE PCM        audio for that run
client  <binary>  0x01                      lone id byte = end of audio
server  {"id": 2, "type": "event", "event": {"type": "run-end"}}
```

The first byte of every binary frame is the handler id from `runner_data`,
which is what lets several runs share one socket. A lone id byte with no PCM
after it means "that is all the audio".

Events, in order: `run-start`, `wake_word-start`, `wake_word-end`, `stt-start`,
`stt-vad-start`, `stt-vad-end`, `stt-end`, `intent-start`, `intent-progress`
(one per streamed token delta), `intent-end`, `tts-chunk` (a sentence synthesised while the model writes the next — M60; zero or more, each with its own `tts_output.url`), `tts-start`, `tts-end` (the whole reply; when chunks were sent, also `chunks` and `remainder_url`, the part they did not cover),
`run-end`. Failures emit `error` with a `code` and `message`, then `run-end`.

`start_stage` and `end_stage` let a client use part of the pipeline: a phone
that did wake word on-device starts at `stt`; a text chat box starts at
`intent`; something that only wants audio out runs `intent` → `tts`.

Text in, text out, no audio at all:

```bash
curl -s localhost:8080/api/conversation/process \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"text": "turn the kitchen lights down a bit"}'
```

## Audio format

16 kHz, mono, 16-bit signed little-endian PCM, all the way in. Piper answers at
22.05 kHz and the pipeline keeps whatever the server reports rather than
resampling, since the client is about to play it anyway.

TTS output is cached in memory under a random token and served once from
`/api/tts_proxy/<token>.wav`. That route needs no bearer token — the token in
the URL is the credential, which is what lets a satellite play the answer
without holding a long-lived secret.

## Voice activity detection

The runner watches loudness and stops recording after a silence gap, so nobody
has to press a button to say they are done. Defaults, in
`jarvis/voice/pipeline.py`:

| | Default | Effect if you change it |
|---|---|---|
| `vad_threshold` | RMS 200.0 | Lower catches quieter speech and more fridge hum. Raise it in a noisy kitchen. |
| `vad_silence_ms` | 900 ms | The end-of-speech gap. Below ~700 ms it truncates people who pause mid-sentence; above ~1200 ms every reply feels sluggish. |

These are constructor arguments on `PipelineRun`, not YAML keys. Clients may
override `sample_rate` and `timeout` on the run message.

## Latency

The number that matters is wake word to first audible word. Budget on a
reasonable box — a modern desktop CPU, 8B model, no GPU:

| Stage | Target | Notes |
|---|---|---|
| Wake detection | < 100 ms | openWakeWord is small; this is essentially free. |
| End-of-speech (VAD) | 900 ms | Fixed cost by construction. The largest single item. |
| STT | 200–600 ms | Streaming sherpa is at the low end. Batch faster-whisper `base` is at the high end and grows with utterance length. |
| Model first token | 300 ms–3 s | **The variable.** A warm model is a few hundred ms; a cold one pays the whole load. |
| Tool round trip | 50–200 ms each | Multiplied by how many rounds the model takes. |
| TTS first chunk | 150–400 ms | Piper `medium`. `low` is roughly half, and sounds it. |
| **Total** | **1.5–3 s** | Above 4 s people start repeating themselves. |

### Where it actually goes wrong

**A cold model.** Ollama unloads after `OLLAMA_KEEP_ALIVE` (5 minutes by
default), and the next request pays several seconds of load. Set
`OLLAMA_KEEP_ALIVE=30m`, or `keep_alive: 30m` under `llm:`, and the difference
is the single biggest win available.

**Tool rounds.** `max_tool_rounds: 5` means a worst-case turn is five
model calls, not one. Lower it to 2–3 if replies feel slow; the cost is that
genuinely multi-step requests start failing halfway.

**Context size.** `num_ctx: 8192` with a long conversation and a big house
summary means a lot of prompt to process before the first token. Trim
`conversation: max_turns` before you trim the context.

**Doing STT on the same GPU as the model.** They queue behind each other. If
you have one GPU, run STT on the CPU — sherpa streaming is fast enough there.

**A quiet or distant microphone.** Everything downstream is fine and the
transcript is still wrong. Fix the microphone before tuning anything here.

### Measuring it

The `intent-start` and `tts-start` event timestamps bracket the model, which is
where the variance lives. `scripts/pipeline-smoke.py` in the parent repo does a
full round trip end to end.

## Wake word

`hey_jarvis` ships with the openWakeWord image, along with `ok_nabu`,
`hey_mycroft` and others. `--preload-model` in `docker-compose.yml` loads it at
startup so the first detection is not slower than the rest.

Custom phrases go in `./wyoming/openwakeword`, which is mounted at `/custom`
and passed as `--custom-model-dir`. Training a `.tflite` is covered in
`../../docs/wake-word-training.md` in the parent repo.

Note that on-device Android wake uses **microWakeWord**, not openWakeWord.
Different feature frontend, different architecture — openWakeWord `.tflite`
files are not drop-in compatible. `hey_jarvis` exists as a stock model for
both, which is why the default phrase needs no training on either side.

## Speaking without being asked

```yaml
service: voice.say
data:
  text: The washing machine has finished, Sir.
  entity_id: media_player.kitchen     # optional; also plays it there
```

Returns the cached audio URL. Firing it into an empty house is rude and, more
practically, useless — the persona's routing rules exist so that a status
update while the user is out becomes a notification instead. Wire
`llm: user_context:` up and let the model choose.
