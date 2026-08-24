"""The live interaction rig: talk to Jarvis the way a person does.

Everything else in `testing/` proves a *mechanism*: the pipeline emits these
events, the reducer understands that payload, the planner writes steps. None of
it proves the thing anybody actually cares about — that you can say a sentence
out loud and the right thing happens.

So this rig closes the loop with real components on both ends:

    Piper (en_US-amy-low)  ->  audio  ->  [the audio-input API | a real browser
                                           microphone in headless Chromium]
                                      ->  jarvis-core, real Whisper, real model
                                      ->  Piper (en_GB-alan-medium)
                                      ->  audio  ->  real Whisper  ->  text

The user's voice is deliberately NOT Jarvis's: different speaker, accent and
sex, so no transcript can be quietly attributed to the wrong side of the
conversation.

Modules:

* `voice.py`     — synthesise the user, hear Jarvis
* `audio.py`     — noise at a named SNR, silence, clipping
* `transport.py` — the two delivery paths, and the text one
* `judge.py`     — a local model scoring "does this reply mean the right thing"
* `scenario.py`  — the YAML fixture format and its loader
* `runner.py`    — run scenarios, collect results
* `report.py`    — WER, accuracy, latency, the scorecard
* `fixture_site.py` / `fixture_search.py` — a small web with known content

Nothing here reaches the internet at run time, and nothing here touches a
phone. `voices/` is fetched once by `fetch_voice.py` and gitignored, exactly as
the Wyoming models are.
"""

from __future__ import annotations

__all__ = ["LiveError"]


class LiveError(RuntimeError):
    """The rig itself could not run — as distinct from a scenario failing."""
