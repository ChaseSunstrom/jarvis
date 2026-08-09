"""The Jarvis end-to-end test harness.

    from testing.harness import Harness, JarvisClient, FakeDevice, speech_pcm

    with Harness() as jarvis:                     # real jarvis-core, fake GPU
        async with JarvisClient(jarvis.base_url, jarvis.token) as client:
            await client.connect()
            run = await client.run_pipeline(audio=speech_pcm())

`harness.py` boots the real server against `fake_ollama.py` and
`fake_wyoming.py`; `client.py` drives it. See ``docs/testing.md``.
"""

from __future__ import annotations

from .client import (
    DEFAULT_DEVICE_ACTIONS,
    EventStream,
    FakeDevice,
    JarvisApiError,
    JarvisClient,
    PipelineRun,
    parse_wav,
    pcm_chunks,
    rms,
    silence_pcm,
    speech_pcm,
    tone_pcm,
)
from .fake_ollama import FakeOllama, Script
from .fake_wyoming import FakeWyomingServer, FakeWyomingStack
from .harness import Harness, HarnessError, build_config, free_port

__all__ = [
    "DEFAULT_DEVICE_ACTIONS",
    "EventStream",
    "FakeDevice",
    "FakeOllama",
    "FakeWyomingServer",
    "FakeWyomingStack",
    "Harness",
    "HarnessError",
    "JarvisApiError",
    "JarvisClient",
    "PipelineRun",
    "Script",
    "build_config",
    "free_port",
    "parse_wav",
    "pcm_chunks",
    "rms",
    "silence_pcm",
    "speech_pcm",
    "tone_pcm",
]
