"""The ways a person can reach Jarvis, and the one thing they all return.

Four, and the differences between them are the point:

* `ApiVoice`   — audio on the binary channel of `assist_pipeline/run`. What a
                 satellite and the phone do.
* `BrowserVoice` — a real headless Chromium with a WAV wired to its microphone,
                 driving the actual HUD: its VAD decides when speech started,
                 its websocket carries the audio, its DOM shows the answer.
* `Text`       — `/api/conversation/process`. What an automation or a script does.
* `BrowserText` — typing into the console's chat panel.

Each returns a `Turn`: what was said, what Jarvis *heard*, what it answered in
text, what it answered **out loud** (transcribed back through the same Whisper),
and the per-stage timings. A scenario asserts on a `Turn` and does not care
which transport produced it — which is what lets one fixture have a voice and a
text variant with no second copy of the expectations.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import LiveError
from .audio import room_tone, silence
from .voice import Ears, Mouth, Utterance

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

#: How long one turn may take before the rig calls it a failure.
#:
#: 120 s was too short, measured rather than guessed: on this host a spoken,
#: tool-using turn is 15–20 s, and a request that makes the model reason at
#: length before handing the work to the background reached 120 s and was cut
#: off mid-turn. The latency itself is reported (and is an open issue —
#: `ISSUES.md`); this timeout only decides when to stop waiting.
TURN_TIMEOUT = float(os.environ.get("LIVE_TURN_TIMEOUT", "240"))

#: Silence in front of every utterance. The HUD opens its microphone when the
#: page mounts, and Chromium starts playing the fake capture file immediately —
#: without a lead-in the first syllable is gone before anything is listening,
#: and the failure looks like bad recognition rather than a race.
LEAD_IN_SECONDS = 1.2
#: And after, so the VAD sees the end of speech rather than the end of the file.
TAIL_SECONDS = 1.0


@dataclass
class Turn:
    """One thing said, and everything that came back."""

    said: str
    transcript: str = ""
    reply_text: str = ""
    reply_heard: str = ""
    tts_url: str = ""
    wake_word: str = ""
    conversation_id: str = ""
    error: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    #: Seconds from the start of the turn to each stage's first frame.
    latency: dict[str, float] = field(default_factory=dict)
    transport: str = ""

    @property
    def spoke(self) -> bool:
        return bool(self.tts_url or self.reply_heard)

    def as_dict(self) -> dict[str, Any]:
        return {
            "said": self.said,
            "transcript": self.transcript,
            "reply_text": self.reply_text,
            "reply_heard": self.reply_heard,
            "wake_word": self.wake_word,
            "latency": {k: round(v, 3) for k, v in self.latency.items()},
            "transport": self.transport,
            "error": self.error,
        }


def _stage_latencies(events: list[dict[str, Any]], started: float) -> dict[str, float]:
    """When each stage first reported, relative to the start of the turn.

    Taken from arrival time here rather than the server's own timestamps: what
    a person experiences is when the answer reached them, and a server clock
    that says the reply was ready 300 ms ago is not a faster reply.
    """
    out: dict[str, float] = {}
    interesting = {
        "stt-end": "stt",
        "intent-start": "intent_start",
        "intent-progress": "ttft",
        "intent-end": "intent",
        "tts-start": "tts_request",
        "tts-end": "tts",
        "run-end": "total",
    }
    for event in events:
        name = interesting.get(str(event.get("type") or ""))
        if name and name not in out:
            out[name] = float(event.get("at") or time.monotonic()) - started
    return out


class Link:
    """Whichever websocket client is current.

    A scenario can restart jarvis-core mid-way — that is the only honest way to
    test "it remembered" — and the socket does not survive that. The transports
    hold this rather than a client, so the restart swaps one attribute instead
    of rebuilding everything that referred to the old one.
    """

    def __init__(self, client: Any) -> None:
        self.client = client


class ApiVoice:
    """Audio in on the binary channel, audio out through the TTS proxy."""

    name = "voice-api"

    def __init__(self, link: "Link", harness, mouth: Mouth, ears: Ears) -> None:
        self.link = link
        self.harness = harness
        self.mouth = mouth
        self.ears = ears

    @property
    def client(self):
        return self.link.client

    async def say(
        self,
        text: str,
        *,
        pcm: bytes | None = None,
        rate: int | None = None,
        start_stage: str = "stt",
        conversation_id: str | None = None,
        timeout: float = TURN_TIMEOUT,
        wake_phrase: str = "",
    ) -> Turn:
        utterance: Utterance | None = None
        if pcm is None:
            utterance = self.mouth.say(text)
            pcm, rate = utterance.pcm, utterance.rate
        rate = int(rate or 16000)
        audio = silence(LEAD_IN_SECONDS / 4, rate) + pcm + silence(TAIL_SECONDS / 2, rate)

        wake_audio = None
        if wake_phrase:
            # The wake phrase goes in FIRST and the command only follows once
            # the detector has fired, because both stages read one audio
            # stream: anything sent before detection is spent on the wake
            # stage. A satellite behaves exactly this way.
            spoken_wake = self.mouth.say(wake_phrase)
            wake_audio = room_tone(0.4, spoken_wake.rate, level_db=-60) + spoken_wake.pcm
            start_stage = "wake"

        started = time.monotonic()
        run = await self.client.run_pipeline(
            audio=audio,
            wake_audio=wake_audio,
            sample_rate=rate,
            start_stage=start_stage,
            conversation_id=conversation_id,
            timeout=timeout,
            run_timeout=timeout,
            keep_streaming=wake_audio is not None,
        )
        # `at` comes off the client, which stamps each frame as it ARRIVES.
        # Stamping here instead gave every stage the same number, because this
        # code runs once, after the whole run is over.
        events = [
            {"type": event.get("type"), "data": event.get("data"),
             "at": float(event.get("at") or time.monotonic())}
            for event in run.events
        ]
        turn = Turn(
            said=text,
            transcript=run.transcript,
            reply_text=run.response_text,
            tts_url=run.tts_url,
            wake_word=run.wake_word,
            conversation_id=run.conversation_id,
            error=run.error,
            events=events,
            latency=_stage_latencies(events, started),
            transport=self.name,
        )
        turn.latency.setdefault("total", time.monotonic() - started)
        if turn.tts_url:
            turn.reply_heard = await self._hear_reply(turn.tts_url)
        return turn

    async def _hear_reply(self, url: str) -> str:
        """Fetch the spoken answer and transcribe it — the loop's other half."""
        data = await self.client.get_bytes(url)
        if not data:
            return ""
        return await self.ears.hear_wav(data)


class Text:
    """No audio at all: the same request as words."""

    name = "text-api"

    def __init__(self, link: "Link") -> None:
        self.link = link

    @property
    def client(self):
        return self.link.client

    async def say(self, text: str, *, conversation_id: str | None = None,
                  timeout: float = TURN_TIMEOUT, **_ignored: Any) -> Turn:
        started = time.monotonic()
        answer = await self.client.conversation(text, conversation_id=conversation_id)
        response = (answer or {}).get("response") or {}
        speech = ((response.get("speech") or {}).get("plain") or {}).get("speech") or ""
        return Turn(
            said=text,
            transcript=text,
            reply_text=str(speech),
            conversation_id=str((answer or {}).get("conversation_id") or ""),
            latency={"total": time.monotonic() - started},
            transport=self.name,
        )


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Console:
    """The built console, served against a real jarvis-core.

    The e2e suite serves it against `tests/web/mock-ha.mjs`; the live rig points
    the same node build at the harness instead, because a mock backend cannot
    run a pipeline, hold a conversation or finish a task — which is all this
    suite is about.
    """

    def __init__(self, base_url: str, token: str, port: int | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None
        self._log = REPO_ROOT / ".verify" / "live" / "console.log"

    def start(self, timeout: float = 90.0) -> "Console":
        build = REPO_ROOT / "jarvis-web" / "build" / "index.js"
        if not build.is_file():
            raise LiveError(
                "the console is not built — run: cd jarvis-web && npm run build"
            )
        node = shutil.which("node")
        if not node:
            raise LiveError("node is not on PATH")
        self._log.parent.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "PORT": str(self.port),
            "HOST": "127.0.0.1",
            "JARVIS_BACKEND": "core",
            "JARVIS_URL": self.base_url,
            "JARVIS_TOKEN": self.token,
            "JARVIS_PIPELINE": "Jarvis",
            "JARVIS_TTS_VOICE": "en_GB-alan-medium",
        }
        self._process = subprocess.Popen(
            [node, "build"],
            cwd=str(REPO_ROOT / "jarvis-web"),
            env=env,
            stdout=self._log.open("wb"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise LiveError(
                    f"the console exited immediately (rc={self._process.returncode}); "
                    f"see {self._log}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return self
            except OSError:
                time.sleep(0.2)
        raise LiveError(f"the console did not listen on {self.port} within {timeout:g}s")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - a wedged node
                self._process.kill()
            self._process = None

    def __enter__(self) -> "Console":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


class Browser:
    """A real browser, a real microphone, the real HUD.

    Driven from Node rather than python-playwright: `jarvis-web/node_modules`
    already has Playwright and its browser, and installing a second copy for
    Python would put 150 MB and a second version on the box to do the same job.
    The Node side is `browser_turn.cjs`; everything it needs arrives as one JSON
    document on argv and comes back as one on stdout.
    """

    name = "voice-browser"

    def __init__(self, console: "Console", mouth: Mouth, ears: Ears,
                 headless: bool = True) -> None:
        self.console = console
        self.mouth = mouth
        self.ears = ears
        self.headless = headless
        self._script = HERE / "browser_turn.cjs"

    async def say(
        self,
        text: str,
        *,
        pcm: bytes | None = None,
        rate: int | None = None,
        mode: str = "voice",
        timeout: float = 180.0,
        **_ignored: Any,
    ) -> Turn:
        wav_path = None
        if mode == "voice":
            utterance = self.mouth.say(text) if pcm is None else Utterance(text, pcm, int(rate or 16000))
            padded = Utterance(
                text=utterance.text,
                pcm=silence(LEAD_IN_SECONDS, utterance.rate)
                + utterance.pcm
                + silence(TAIL_SECONDS, utterance.rate),
                rate=utterance.rate,
            )
            wav_path = padded.write_wav(
                REPO_ROOT / ".verify" / "live" / "browser" / f"turn-{abs(hash(text)) % 10**8}.wav"
            )

        job = {
            "url": self.console.url,
            "mode": mode,
            "text": text,
            "wav": str(wav_path) if wav_path else None,
            "headless": self.headless,
            "timeoutMs": int(timeout * 1000),
        }
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            "node",
            str(self._script),
            json.dumps(job),
            cwd=str(REPO_ROOT / "jarvis-web"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=timeout + 30)
        except asyncio.TimeoutError as exc:
            process.kill()
            raise LiveError(f"the browser turn did not finish within {timeout:g}s") from exc

        try:
            result = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise LiveError(
                f"the browser turn produced no result: {err.decode('utf-8', 'replace')[-2000:]}"
            ) from exc
        if result.get("error") and not result.get("transcript"):
            raise LiveError(f"the browser turn failed: {result['error']}")

        turn = Turn(
            said=text,
            transcript=str(result.get("transcript") or ""),
            reply_text=str(result.get("response") or ""),
            tts_url=str(result.get("ttsUrl") or ""),
            latency={
                key: float(value) / 1000.0
                for key, value in (result.get("latency") or {}).items()
            },
            transport=f"{self.name}-{mode}",
        )
        turn.latency.setdefault("total", time.monotonic() - started)
        if turn.tts_url:
            turn.reply_heard = await self._hear(turn.tts_url)
        return turn

    async def _hear(self, url: str) -> str:
        import httpx

        full = url if url.startswith("http") else f"{self.console.url}{url}"
        async with httpx.AsyncClient(timeout=60.0) as http:
            answer = await http.get(full)
            if answer.status_code != 200 or not answer.content:
                return ""
        return await self.ears.hear_wav(answer.content)
