# Custom wake word: training, deployment, and where it runs

"Hey Jarvis" ships pretrained in both openWakeWord and microWakeWord, so P5/P6
work out of the box. This doc is the path for a **custom** phrase (or a
better-tuned "Hey Jarvis") and for understanding where detection runs on each
surface.

## Licensing, upfront

- **openWakeWord code** ([dscripka/openWakeWord](https://github.com/dscripka/openWakeWord)):
  Apache-2.0 — free to use anywhere.
- **Official pretrained models** (including `hey_jarvis_v0.1`):
  **CC BY-NC-SA 4.0**, English-only. Personal, non-commercial use — exactly
  our case — is fine. Don't redistribute them inside a commercial product.
- Models **you train yourself** with the notebook are yours; the synthetic
  speech generated with Piper is unencumbered for this use.

## Training a model (openWakeWord official path)

1. **Use the official training notebook/Colab** from the openWakeWord repo
   (`notebooks/training_models.ipynb` / the "train new models" Colab). It
   automates the whole recipe below; a full run is a few hours on a free
   Colab GPU.
2. **Synthetic positives:** thousands of clips of the target phrase are
   generated with **Piper** TTS (now maintained as
   [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl); GPL-3.0
   code, which is fine — we only consume its audio output) across many
   voices, speeds, and pitches. Rule of thumb: 3–6 syllable phrases detect
   best ("hey jarvis" is in the sweet spot); avoid phrases that collide with
   common speech.
3. **Negative data:** large "not the wake word" corpora (the notebook pulls
   precomputed openWakeWord features from public datasets — speech, music,
   noise) plus *adversarial* negatives: phonetically close phrases
   ("hey travis", "jar of fish") that Piper also synthesizes.
4. **Augmentation:** room impulse responses + background noise mixing
   (audiomentations/torch-audiomentations in the notebook) so the model
   survives kitchens and cars rather than just clean TTS audio.
5. **Iterate on the false-accept/false-reject curve.** Test against real
   recordings of *your* voice at distance. Expect 2–3 training rounds tuning
   `target_phrase` variants and negative weighting.
6. **Optional per-speaker verifier:** openWakeWord supports
   `custom_verifier_models` — a small logistic-regression layer trained on
   ~20 recordings of the actual user saying the phrase (plus negatives),
   attached per wake word model via `custom_verifier_threshold`. Cheap and
   effective for cutting TV/guest false accepts; only speakers you trained
   will trigger reliably.

## Deployment targets

### Server-side: wyoming-openwakeword (primary)

The HA add-on / container `wyoming-openwakeword` listens on **tcp/10400**
and comes with `ok_nabu`, `hey_jarvis`, `hey_mycroft`, etc. preinstalled.

- Custom models: drop the trained `.tflite` into the add-on's
  `--custom-model-dir` (add-on config: "Custom model directory",
  typically `/share/openwakeword`), restart, select the model on the
  Wyoming satellite / Assist pipeline.
- This is where wake word runs for **satellite hardware** (Voice PE etc. can
  offload wake word to the server, though Voice PE normally runs
  microWakeWord on-device).

### On-device Android: microWakeWord (companion app 2026.3+)

The phone app's always-on path uses **microWakeWord**, not openWakeWord —
tiny streaming models designed for microcontrollers, also used by ESPHome.

- `hey_jarvis` exists as a stock microWakeWord model, so no training is
  needed for the default phrase.
- A custom phrase needs the **microWakeWord training pipeline**
  ([kahrendt/microWakeWord](https://github.com/kahrendt/microWakeWord),
  Apache-2.0) — same idea (Piper synthetic positives, negatives,
  augmentation) but a different feature frontend and model architecture;
  **openWakeWord `.tflite` files are not drop-in compatible**. Budget more
  iteration: microWakeWord models are smaller and less forgiving.
- Battery/UX caveats of the Android implementation are covered in
  `docs/android.md` §4 (no DSP path for third-party apps, foreground
  service, experimental quirks).

### Browser (jarvis-web)

P4 status: the web HUD uses **push-to-talk plus VAD-based hands-free**
(browser VAD endpointing after PTT arm) — no wake word in the browser yet.
The plug-in point is documented in `jarvis-web`: an
**openWakeWord-to-WASM/ONNX** runtime (onnxruntime-web running the melspec +
embedding + wake models on an AudioWorklet feed) can be slotted in front of
the existing VAD without changing the pipeline contract. Until that lands,
hands-free in the browser means "arm once per session", not "always
listening".

## Quick reference

| Surface | Engine | Model file | Where it runs |
|---|---|---|---|
| HA satellites / server | openWakeWord | `.tflite` (OWW) | wyoming-openwakeword, port 10400 |
| Android companion (jarvis flavor) | microWakeWord | `.tflite`/`.json` (mWW) | foreground service on the phone |
| Web HUD | none yet (PTT + VAD) | — | OWW-WASM plug-in point reserved |
