"""The Jarvis end-to-end test harness.

    from testing.harness import Harness, JarvisClient, FakeDevice, speech_pcm

    with Harness() as jarvis:                     # real jarvis-core, fake GPU
        async with JarvisClient(jarvis.base_url, jarvis.token) as client:
            await client.connect()
            run = await client.run_pipeline(audio=speech_pcm())

`harness.py` boots the real server against `fake_ollama.py` and
`fake_wyoming.py`; `client.py` drives it. See ``docs/testing.md``.

Names resolve lazily (PEP 562) for two reasons: the two fakes are stdlib-only
and stay importable on a machine with no httpx or websockets, and running one
of them with ``python -m`` does not first import its own package.
"""

from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "DEFAULT_DEVICE_ACTIONS": "client",
    "EventStream": "client",
    "FakeDevice": "client",
    "JarvisApiError": "client",
    "JarvisClient": "client",
    "PipelineRun": "client",
    "parse_wav": "client",
    "pcm_chunks": "client",
    "rms": "client",
    "silence_pcm": "client",
    "speech_pcm": "client",
    "tone_pcm": "client",
    "FakeOllama": "fake_ollama",
    "Script": "fake_ollama",
    "FakeWyomingServer": "fake_wyoming",
    "FakeWyomingStack": "fake_wyoming",
    "sine_pcm": "fake_wyoming",
    "Harness": "harness",
    "HarnessError": "harness",
    "build_config": "harness",
    "free_port": "harness",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f".{module_name}", __name__), name)


def __dir__() -> list[str]:
    return __all__
