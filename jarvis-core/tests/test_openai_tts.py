"""The opt-in speech client: the shape the pipeline expects, and one WAV trap.

`OpenAiTtsClient` exists so the operator can hear Kokoro instead of Piper
without a code change. It has to hand the pipeline exactly what
`WyomingTtsClient` hands it, and it has to survive the header a streaming
service writes — which is the bug these tests are mostly about.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from jarvis.voice.openai_tts import OpenAiTtsClient, OpenAiTtsError


def wav_bytes(seconds: float = 0.5, rate: int = 24000, lie_about_length: bool = False) -> bytes:
    frames = b"".join(
        struct.pack("<h", int(3000 * (i % 50 - 25) / 25)) for i in range(int(rate * seconds))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    data = bytearray(buffer.getvalue())
    if lie_about_length:
        # What a streamed WAV actually looks like: the header is written before
        # the audio exists, so the frame count is a placeholder. Kokoro's says
        # eighty-nine thousand seconds.
        data[40:44] = struct.pack("<I", 0xFFFFFFF0)
    return bytes(data)


class FakeResponse:
    def __init__(self, content=b"", status_code=200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.sent: list[dict] = []

    async def post(self, url, json=None, timeout=None):
        self.sent.append({"url": url, "body": json})
        return self.answers.pop(0) if self.answers else FakeResponse(wav_bytes())


@pytest.mark.asyncio
async def test_it_returns_what_the_wyoming_client_returns():
    """`pipeline.py` must not know which engine it has."""
    http = FakeHttp(FakeResponse(wav_bytes(0.25, 24000)))
    pcm, rate, width, channels = await OpenAiTtsClient(client=http).synthesize("hello")
    assert rate == 24000 and width == 2 and channels == 1
    assert len(pcm) == 24000 * 0.25 * 2


@pytest.mark.asyncio
async def test_a_streamed_wav_that_lies_about_its_length_still_plays():
    """The real bug: reading by the header's frame count returned 89478 seconds.

    Measured against a real Kokoro container — its WAV header carries a
    placeholder, so the length has to come from the bytes.
    """
    http = FakeHttp(FakeResponse(wav_bytes(0.5, 24000, lie_about_length=True)))
    pcm, rate, _width, _channels = await OpenAiTtsClient(client=http).synthesize("hello")
    assert abs(len(pcm) / (rate * 2) - 0.5) < 0.05, "the audio was truncated or invented"


@pytest.mark.asyncio
async def test_the_request_is_the_openai_shape():
    http = FakeHttp()
    await OpenAiTtsClient(url="http://tts/v1", voice="bm_george", client=http).synthesize("hi")
    sent = http.sent[0]
    assert sent["url"] == "http://tts/v1/audio/speech"
    assert sent["body"]["voice"] == "bm_george"
    assert sent["body"]["response_format"] == "wav"
    assert sent["body"]["input"] == "hi"


@pytest.mark.asyncio
async def test_a_per_call_voice_wins_over_the_configured_one():
    http = FakeHttp()
    await OpenAiTtsClient(voice="bm_george", client=http).synthesize("hi", voice="bf_emma")
    assert http.sent[0]["body"]["voice"] == "bf_emma"


@pytest.mark.asyncio
async def test_a_service_that_is_down_says_which_service():
    class Broken:
        async def post(self, *_a, **_k):
            raise ConnectionError("refused")

    with pytest.raises(OpenAiTtsError) as err:
        await OpenAiTtsClient(url="http://tts/v1", client=Broken()).synthesize("hi")
    assert "audio/speech" in str(err.value)


@pytest.mark.asyncio
async def test_something_that_is_not_audio_is_named_as_such():
    http = FakeHttp(FakeResponse(b'{"detail":"no such voice"}'))
    with pytest.raises(OpenAiTtsError) as err:
        await OpenAiTtsClient(client=http).synthesize("hi")
    assert "not a WAV" in str(err.value)


@pytest.mark.asyncio
async def test_an_empty_body_is_an_error_rather_than_silence():
    """Silence played to a person is indistinguishable from a broken speaker."""
    http = FakeHttp(FakeResponse(b""))
    with pytest.raises(OpenAiTtsError):
        await OpenAiTtsClient(client=http).synthesize("hi")


@pytest.mark.asyncio
async def test_is_available_is_a_question_and_never_a_raise():
    class Broken:
        async def post(self, *_a, **_k):
            raise ConnectionError("refused")

    assert await OpenAiTtsClient(client=Broken()).is_available() is False
    assert await OpenAiTtsClient(client=FakeHttp()).is_available() is True
