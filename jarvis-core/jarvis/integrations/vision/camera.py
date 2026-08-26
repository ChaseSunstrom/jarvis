"""Camera sources, as entities, and the frames they produce.

Four source kinds, all reduced to the same thing — one JPEG in memory:

``still``   one HTTP GET of a snapshot URL. What most cameras and every
            doorbell offer, and the cheapest thing to ask of them.
``mjpeg``   open the stream, read until one complete JPEG has gone past, hang
            up. Never holds the stream open: a pull is a moment, not a tap.
``rtsp``    shell out to ffmpeg for a single frame, if ffmpeg is installed. If
            it is not, that is a clear message, not a traceback.
``mqtt``    the most recent frame a camera published to a topic.

Every path has a timeout. There is no code here that can wait forever, because
the failure mode of a camera integration without one is a service call that
never returns and a house that stops answering.

Frames live in memory with a short TTL and a size cap, and are **never written
to disk** unless someone explicitly passes a filename to ``camera.snapshot``.
The TTL is not a performance tweak; it is the difference between "Jarvis can
look at the front door" and "Jarvis keeps a copy of the front door".
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from ...const import STATE_IDLE
from ...entity import Entity
from .consent import CONSENT_NEVER, DEFAULT_CONSENT, normalise_consent

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

PLATFORM_STILL = "still"
PLATFORM_MJPEG = "mjpeg"
PLATFORM_RTSP = "rtsp"
PLATFORM_MQTT = "mqtt"
#: A stream go2rtc restreams (`jarvis-core/go2rtc/go2rtc.yaml`), read through
#: its snapshot endpoint. Underneath it is `still`: one GET, one JPEG. The
#: platform exists so a camera is named by its stream rather than by a URL an
#: operator has to assemble — and so the downscale go2rtc does on the way in
#: (`w=`) is the default rather than something to know about.
PLATFORM_GO2RTC = "go2rtc"
PLATFORMS = (PLATFORM_STILL, PLATFORM_MJPEG, PLATFORM_RTSP, PLATFORM_MQTT, PLATFORM_GO2RTC)

#: Where go2rtc listens when it is the one this stack ships: its API is bound
#: to loopback on purpose (it skips authentication for localhost requests, and
#: says so in bold), so the only address that can reach it is this one.
DEFAULT_GO2RTC_URL = "http://127.0.0.1:1984"
#: The width go2rtc scales a snapshot to. The same number `max_edge` defaults
#: to, so a look costs the same whether or not Pillow is installed here.
DEFAULT_GO2RTC_WIDTH = 1280
GO2RTC_FRAME_PATH = "/api/frame.jpeg"
_STREAM_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

CAMERA_DOMAIN = "camera"
STATE_STREAMING = "streaming"

DEFAULT_TIMEOUT = 10.0
DEFAULT_FRAME_TTL = 30.0
#: 8 MiB is a generous 4K JPEG. Past that something is wrong — a video stream
#: mistaken for a snapshot URL, or a camera that answers with a firmware blob.
DEFAULT_MAX_FRAME_BYTES = 8 * 1024 * 1024

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"

#: ffmpeg for one frame: no stdin (so it cannot block waiting for a keypress),
#: TCP transport (UDP loses packets and produces half a frame), one frame out
#: as JPEG on stdout.
FFMPEG_ARGS = (
    "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
    "-i", "{url}", "-frames:v", "1", "-f", "image2", "-vcodec", "mjpeg",
    "pipe:1",
)

FFMPEG_MISSING = (
    "ffmpeg is not installed, so RTSP cameras cannot produce a frame. Install "
    "it on the host (`apt install ffmpeg`) or add it to the jarvis-core image "
    "— the shipped image does not include it. Most cameras also offer an HTTP "
    "snapshot URL, which is faster: use `platform: still` instead."
)


class CameraError(Exception):
    """A frame could not be produced. Carries a message a human can act on."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def redact_url(url: str) -> str:
    """A URL safe to log, put in an error, or show in an attribute.

    Camera URLs routinely carry credentials, either as ``user:pass@`` or as an
    auth token in the query string. Both are stripped here, because this
    string ends up in log lines and audit entries that outlive the request.
    """
    text = str(url or "")
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "(unparseable url)"
    if not parts.scheme:
        return text.split("?", 1)[0]
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    if parts.username:
        host = f"***@{host}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _scalar(value: Any) -> str:
    """A YAML scalar as a usable string; ``""``/``''`` mean absent.

    ``!secret`` and ``!env_var`` can both hand back a bare pair of quotes when
    the value is missing, and a truthy two-character password is a worse
    failure than no password at all.
    """
    text = str(value if value is not None else "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return "" if text in ('""', "''") else text


def iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


#: How many steps :func:`jpeg_dimensions` will take before giving up. A real
#: JPEG reaches its frame header in a handful — the parser hops segment to
#: segment by declared length. Malformed bytes are what make it crawl a byte
#: at a time, and eight megabytes of that is a third of a second of pure
#: Python with the event loop stopped, chosen by whatever answered the camera
#: URL. Past this budget the answer is simply "no dimensions", which is what
#: every caller already handles.
MAX_MARKER_STEPS = 4096


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's SOF marker, without decoding it.

    Pure Python and about twenty lines, which is worth it: it means the size
    of a frame is reportable, and an oversized one is recognisable, on an
    installation that has no image library at all.
    """
    if not data[:2] == JPEG_SOI:
        return None
    index, end, steps = 2, len(data), 0
    while index + 9 < end:
        steps += 1
        if steps > MAX_MARKER_STEPS:
            return None
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > end:
            return None
        length = int.from_bytes(data[index + 2:index + 4], "big")
        # SOF0..SOF15, excluding the four that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if index + 9 > end:
                return None
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return (width, height)
        if length < 2:
            return None
        index += 2 + length
    return None


class JpegScanner:
    """Finds one complete JPEG in a buffer that keeps growing.

    This is how a frame is pulled out of an MJPEG stream. Deliberately not a
    multipart parser: cameras disagree about boundaries, content-length
    headers and line endings, and every one of them agrees about SOI and EOI.

    It remembers where it got to, and that is the whole point. Re-searching
    the buffer from the beginning for every chunk that arrives is quadratic:
    eight megabytes of not-quite-MJPEG delivered in eight-kilobyte pieces cost
    four and a half seconds of pure event-loop CPU before the frame cap
    stopped it, and the thing choosing the chunk size is the camera. Carrying
    a cursor makes the same stream linear. The one-byte overlap covers a
    marker split across two chunks.
    """

    __slots__ = ("start", "cursor")

    def __init__(self) -> None:
        self.start = -1   # where SOI was seen; -1 until it has been
        self.cursor = 0   # everything before this has been searched

    def feed(self, buffer: bytes | bytearray) -> bytes | None:
        if self.start < 0:
            found = buffer.find(JPEG_SOI, self.cursor)
            if found < 0:
                self.cursor = max(0, len(buffer) - 1)
                return None
            self.start = found
            self.cursor = found + 2
        end = buffer.find(JPEG_EOI, self.cursor)
        if end < 0:
            self.cursor = max(self.start + 2, len(buffer) - 1)
            return None
        return bytes(buffer[self.start:end + 2])


def extract_jpeg(buffer: bytes) -> bytes | None:
    """The first complete JPEG in a byte buffer, or None if it is incomplete."""
    return JpegScanner().feed(buffer)


def _max_base64_chars(limit: int) -> int:
    """How long a base64 string may be to decode to at most ``limit`` bytes."""
    return (limit + 2) // 3 * 4 + 1024  # slack for a data: prefix and padding


def decode_payload(payload: Any, limit: int = DEFAULT_MAX_FRAME_BYTES) -> bytes:
    """An MQTT payload as image bytes, refused before it is decoded if huge.

    The MQTT client decodes payloads as UTF-8 text, so a raw JPEG cannot
    survive the trip — publishers send base64 (optionally as a ``data:`` URI),
    which is what this accepts.

    The length is checked against the frame cap on the *encoded* string. MQTT
    payloads are untrusted by the security model, and decoding first meant one
    published message could make Jarvis allocate 64 MiB and spend a second and
    a half of the event loop on base64 before the cap it violates was even
    looked at. Anything a publisher sends is now refused at its own size.
    """
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
        if limit and len(data) > limit:
            raise CameraError(
                f"the MQTT payload is {len(data)} bytes, over the "
                f"{limit}-byte frame cap"
            )
        return data
    text = str(payload or "").strip()
    if not text:
        raise CameraError("the MQTT payload was empty")
    if limit and len(text) > _max_base64_chars(limit):
        raise CameraError(
            f"the MQTT payload is {len(text)} characters, more than a "
            f"{limit}-byte frame can encode to. Nothing was decoded. Publish "
            "a snapshot rather than a video segment, or raise max_frame_bytes."
        )
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    text = re.sub(r"\s+", "", text)
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CameraError(
            "the MQTT payload is not base64. Jarvis reads MQTT camera frames "
            "as base64 (or a data: URI) because MQTT payloads arrive as text."
        ) from exc


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def go2rtc_snapshot_url(base: str, stream: str, width: int = DEFAULT_GO2RTC_WIDTH) -> str:
    """go2rtc's snapshot endpoint for one stream.

    `frame.jpeg` exists only when a stream carries an MJPEG codec, which is
    why the shipped `go2rtc.yaml` gives every H.264 camera a second
    `ffmpeg:<name>#video=mjpeg` source: go2rtc transcodes on demand and stops
    when nobody is asking. `w=` is the downscale; `0` leaves it out.
    """
    root = str(base or DEFAULT_GO2RTC_URL).strip().rstrip("/")
    if root.lower().endswith(GO2RTC_FRAME_PATH):
        root = root[: -len(GO2RTC_FRAME_PATH)]
    query: dict[str, str] = {"src": stream}
    if width > 0:
        query["w"] = str(int(width))
    return f"{root}{GO2RTC_FRAME_PATH}?{urlencode(query)}"


@dataclass(frozen=True)
class CameraConfig:
    """One entry under ``vision: cameras:``."""

    name: str
    platform: str = PLATFORM_STILL
    url: str = ""
    username: str = ""
    password: str = ""
    auth: str = "basic"           # basic | digest
    area: str = ""
    consent: str = DEFAULT_CONSENT
    topic: str = ""               # mqtt only
    stream: str = ""              # go2rtc only
    timeout: float = DEFAULT_TIMEOUT
    verify_ssl: bool = True
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES

    @classmethod
    def from_config(cls, options: Any, go2rtc_url: str = DEFAULT_GO2RTC_URL) -> "CameraConfig":
        """One camera. `go2rtc_url` is the `vision: go2rtc_url:` default for
        `platform: go2rtc` cameras that name no `url` of their own."""
        if not isinstance(options, dict):
            raise ValueError("each camera must be a mapping")
        name = _scalar(options.get("name"))
        if not name:
            raise ValueError("a camera needs a name")

        platform = _scalar(options.get("platform")).lower() or PLATFORM_STILL
        if platform not in PLATFORMS:
            raise ValueError(
                f"unknown camera platform {platform!r} for {name!r}; "
                f"expected one of {', '.join(PLATFORMS)}"
            )

        url = _scalar(options.get("url") or options.get("still_url") or options.get("stream_url"))
        topic = _scalar(options.get("topic") or options.get("state_topic"))
        stream = _scalar(options.get("stream") or options.get("src"))
        if platform == PLATFORM_MQTT:
            if not topic:
                raise ValueError(f"camera {name!r} is mqtt but has no topic")
        elif platform == PLATFORM_GO2RTC:
            # The stream name is the whole address. It goes into a query
            # string, so anything outside a plain identifier is a typo rather
            # than something to encode and send.
            if not stream:
                raise ValueError(f"camera {name!r} is go2rtc but names no stream")
            if not _STREAM_NAME.match(stream):
                raise ValueError(
                    f"camera {name!r}: go2rtc stream {stream!r} is not a plain name "
                    "(letters, digits, '_', '-', '.')"
                )
            url = go2rtc_snapshot_url(
                url or go2rtc_url, stream,
                _int(options.get("width"), DEFAULT_GO2RTC_WIDTH),
            )
        elif not url:
            raise ValueError(f"camera {name!r} ({platform}) has no url")

        auth = _scalar(options.get("auth")).lower() or "basic"
        if auth not in ("basic", "digest"):
            raise ValueError(f"camera {name!r}: auth must be basic or digest")

        return cls(
            name=name,
            platform=platform,
            url=url,
            username=_scalar(options.get("username")),
            password=_scalar(options.get("password")),
            auth=auth,
            area=_scalar(options.get("area")),
            consent=normalise_consent(options.get("consent")),
            topic=topic,
            stream=stream,
            timeout=max(1.0, _float(options.get("timeout"), DEFAULT_TIMEOUT)),
            verify_ssl=bool(options.get("verify_ssl", True)),
            max_frame_bytes=max(
                1024, _int(options.get("max_frame_bytes"), DEFAULT_MAX_FRAME_BYTES)
            ),
        )

    @property
    def safe_url(self) -> str:
        return redact_url(self.url) if self.url else self.topic

    def httpx_auth(self) -> Any:
        if not self.username:
            return None
        if self.auth == "digest":
            return httpx.DigestAuth(self.username, self.password)
        return httpx.BasicAuth(self.username, self.password)


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Frame:
    """One image, in memory, with the moment it was taken."""

    data: bytes
    camera: str = ""
    content_type: str = "image/jpeg"
    fetched_at: float = field(default_factory=time.time)

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def dimensions(self) -> tuple[int, int] | None:
        return jpeg_dimensions(self.data)

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.fetched_at

    def as_dict(self) -> dict[str, Any]:
        size = self.dimensions
        return {
            "camera": self.camera,
            "content_type": self.content_type,
            "bytes": self.size,
            "taken_at": iso(self.fetched_at),
            "width": size[0] if size else None,
            "height": size[1] if size else None,
        }


class FrameStore:
    """The most recent frame per camera, held briefly and then dropped.

    Bounded twice: by age (``ttl``) and by total bytes. Reading an expired
    frame returns nothing *and* drops it, so the cache cannot quietly become
    an archive of everything the cameras saw today.
    """

    def __init__(self, ttl: float = DEFAULT_FRAME_TTL, max_bytes: int = DEFAULT_MAX_FRAME_BYTES * 4) -> None:
        self.ttl = max(0.0, float(ttl))
        self.max_bytes = max(0, int(max_bytes))
        self._frames: dict[str, Frame] = {}

    def put(self, key: str, frame: Frame) -> Frame:
        self._frames[key] = frame
        self._evict()
        return frame

    def get(self, key: str, max_age: float | None = None) -> Frame | None:
        """A held frame, if one is young enough to still exist.

        ``max_age`` can only ask for something *fresher* than the TTL, never
        older: the TTL is the promise about how long a frame is kept, and a
        caller must not be able to talk the store into keeping one longer by
        asking nicely. Expiry is enforced on read as well as on write, so a
        store nobody has written to since is not quietly holding an old frame.
        """
        self.purge_expired()
        frame = self._frames.get(key)
        if frame is None:
            return None
        limit = self.ttl if max_age is None else min(self.ttl, max_age)
        if frame.age() > limit:
            return None
        return frame

    def drop(self, key: str) -> None:
        self._frames.pop(key, None)

    def clear(self) -> None:
        self._frames.clear()

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self._frames.values())

    def purge_expired(self) -> None:
        now = time.time()
        for key, frame in list(self._frames.items()):
            if frame.age(now) > self.ttl:
                del self._frames[key]

    def _evict(self) -> None:
        self.purge_expired()
        # Oldest out first until the cap is respected.
        while self.max_bytes and self.total_bytes > self.max_bytes and self._frames:
            oldest = min(self._frames.items(), key=lambda item: item[1].fetched_at)[0]
            del self._frames[oldest]


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
class CameraSource:
    """A configured camera, and the one thing it does: hand back a frame."""

    def __init__(
        self,
        config: CameraConfig,
        client: httpx.AsyncClient,
        jarvis: "Jarvis | None" = None,
    ) -> None:
        self.config = config
        self.client = client
        self.jarvis = jarvis
        self.last_frame_at: float = 0.0
        self.last_snapshot_at: float = 0.0
        self.last_error: str = ""
        self._mqtt_frame: Frame | None = None
        self._unsubscribe: Any = None

    # --- lifecycle --------------------------------------------------------
    async def async_setup(self) -> None:
        if self.config.platform != PLATFORM_MQTT or self.jarvis is None:
            return
        if self.config.consent == CONSENT_NEVER:
            # `never` says Jarvis does not look through this camera. Staying
            # subscribed would keep the most recent thing it saw in memory
            # anyway, which is the same picture arriving by a different road.
            _LOGGER.info(
                "vision: %s is `consent: never`, so %r is not subscribed",
                self.config.name, self.config.topic,
            )
            return
        from ..mqtt import async_subscribe  # local: mqtt is optional

        async def _on_message(message: Any) -> None:
            try:
                data = decode_payload(
                    getattr(message, "payload", ""), self.config.max_frame_bytes
                )
            except CameraError as exc:
                _LOGGER.warning("vision: %s on %s", exc, self.config.topic)
                return
            if len(data) > self.config.max_frame_bytes:
                _LOGGER.warning(
                    "vision: dropped an oversized frame (%d bytes) on %s",
                    len(data), self.config.topic,
                )
                return
            self._mqtt_frame = Frame(data, camera=self.config.name)

        try:
            self._unsubscribe = await async_subscribe(
                self.jarvis, self.config.topic, _on_message
            )
        except Exception:
            _LOGGER.exception(
                "vision: could not subscribe %s to %s",
                self.config.name, self.config.topic,
            )

    def forget(self) -> None:
        """Drop the frame this source is holding, if it is holding one.

        Only ``mqtt`` ever is: the others produce a frame on demand and hand
        it straight to the store, where the TTL applies. A pushed frame has
        nowhere else to live, so it sits here until the next publish replaces
        it — which is exactly why something has to be able to clear it.
        """
        self._mqtt_frame = None

    async def async_shutdown(self) -> None:
        unsub, self._unsubscribe = self._unsubscribe, None
        self._mqtt_frame = None
        if callable(unsub):
            try:
                result = unsub()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover - defensive
                _LOGGER.debug("vision: unsubscribe failed", exc_info=True)

    # --- fetching ---------------------------------------------------------
    async def fetch(self) -> Frame:
        """One frame, or :class:`CameraError` with something actionable in it."""
        platform = self.config.platform
        try:
            if platform in (PLATFORM_STILL, PLATFORM_GO2RTC):
                # go2rtc's snapshot endpoint IS a still: one GET, one JPEG.
                frame = await self._fetch_still()
            elif platform == PLATFORM_MJPEG:
                frame = await self._fetch_mjpeg()
            elif platform == PLATFORM_RTSP:
                frame = await self._fetch_rtsp()
            elif platform == PLATFORM_MQTT:
                frame = self._fetch_mqtt()
            else:  # pragma: no cover - from_config rejects these
                raise CameraError(f"unsupported camera platform {platform!r}")
        except CameraError as exc:
            self.last_error = str(exc)
            raise
        self.last_error = ""
        self.last_frame_at = frame.fetched_at
        return frame

    def _check_size(self, data: bytes) -> bytes:
        limit = self.config.max_frame_bytes
        if len(data) > limit:
            raise CameraError(
                f"{self.config.name} returned {len(data)} bytes, over the "
                f"{limit}-byte frame cap. Point this camera at a snapshot URL "
                "rather than a video stream, or raise max_frame_bytes."
            )
        if not data:
            raise CameraError(f"{self.config.name} returned an empty response")
        return data

    def _too_big(self, seen: int) -> CameraError:
        return CameraError(
            f"{self.config.name} sent more than the "
            f"{self.config.max_frame_bytes}-byte frame cap ({seen} bytes read "
            "before it was cut off). Point this camera at a snapshot URL "
            "rather than a video stream, or raise max_frame_bytes."
        )

    async def _within_deadline(self, coro: Any) -> Frame:
        """A total deadline on producing one frame.

        httpx's timeouts are per read operation, not per request. A camera
        that dribbles one byte every nine seconds never trips a ten-second
        read timeout and takes years to reach the frame cap, so the fetch — and
        the concurrency slot it is holding — waits effectively forever. The
        rtsp path has always had a real deadline through ``wait_for``; these
        are the two that did not, which is what made "there is no code here
        that can wait forever" not quite true.

        ``timeout`` is documented as "seconds to wait for a frame", and this
        is the reading of it that matches those words.
        """
        cfg = self.config
        try:
            return await asyncio.wait_for(coro, cfg.timeout)
        except asyncio.TimeoutError as exc:
            raise CameraError(
                f"{cfg.name} at {cfg.safe_url} did not deliver a complete "
                f"frame within {cfg.timeout:g}s."
            ) from exc

    async def _fetch_still(self) -> Frame:
        return await self._within_deadline(self._read_still())

    async def _fetch_mjpeg(self) -> Frame:
        return await self._within_deadline(self._read_mjpeg())

    async def _read_still(self) -> Frame:
        """One HTTP GET, read as a stream so the cap is a cap.

        ``client.get`` would buffer the whole body first and only then compare
        it to ``max_frame_bytes``, which makes the cap a report rather than a
        limit: whatever answers the camera URL decides how much memory Jarvis
        allocates, and a slow drip never trips the read timeout because bytes
        keep arriving. Reading it in chunks means the connection is dropped at
        the cap, with nothing beyond it ever held.
        """
        cfg = self.config
        limit = cfg.max_frame_bytes
        buffer = bytearray()
        content_type = ""
        try:
            async with self.client.stream(
                "GET",
                cfg.url,
                auth=cfg.httpx_auth(),
                timeout=httpx.Timeout(cfg.timeout, connect=min(5.0, cfg.timeout)),
                headers={"Accept": "image/jpeg, image/*;q=0.8"},
            ) as response:
                if response.status_code in (401, 403):
                    raise CameraError(
                        f"{cfg.name} rejected the credentials (HTTP "
                        f"{response.status_code}). Check username/password, and "
                        f"whether the camera wants `auth: digest`."
                    )
                if response.status_code >= 400:
                    raise CameraError(
                        f"{cfg.name} returned HTTP {response.status_code} for "
                        f"{cfg.safe_url}."
                    )

                content_type = (
                    str(response.headers.get("content-type", ""))
                    .split(";")[0].strip().lower()
                )
                # Checked before a byte of the body is read: a login page is
                # not worth downloading to find out it is a login page.
                if content_type and not content_type.startswith("image/"):
                    raise CameraError(
                        f"{cfg.name} answered with {content_type!r}, not an "
                        "image. That URL is usually a login or status page "
                        "rather than a snapshot endpoint."
                    )
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise self._too_big(len(buffer))
        except httpx.TimeoutException as exc:
            raise CameraError(
                f"{cfg.name} at {cfg.safe_url} timed out after {cfg.timeout:g}s "
                f"({type(exc).__name__})."
            ) from exc
        except httpx.HTTPError as exc:
            raise CameraError(
                f"{cfg.name} is unreachable at {cfg.safe_url} "
                f"({type(exc).__name__}: {exc})."
            ) from exc

        data = self._check_size(bytes(buffer))
        return Frame(data, camera=cfg.name, content_type=content_type or "image/jpeg")

    async def _read_mjpeg(self) -> Frame:
        """Read the stream only until one whole JPEG has gone past."""
        cfg = self.config
        buffer = bytearray()
        scanner = JpegScanner()
        try:
            async with self.client.stream(
                "GET",
                cfg.url,
                auth=cfg.httpx_auth(),
                timeout=httpx.Timeout(cfg.timeout, connect=min(5.0, cfg.timeout)),
            ) as response:
                if response.status_code >= 400:
                    raise CameraError(
                        f"{cfg.name} returned HTTP {response.status_code} for "
                        f"{cfg.safe_url}."
                    )
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    frame = scanner.feed(buffer)
                    if frame is not None:
                        return Frame(
                            self._check_size(frame),
                            camera=cfg.name,
                            content_type="image/jpeg",
                        )
                    if len(buffer) > cfg.max_frame_bytes:
                        raise CameraError(
                            f"{cfg.name} sent {len(buffer)} bytes without a "
                            "complete JPEG. That stream is probably not MJPEG."
                        )
        except httpx.TimeoutException as exc:
            raise CameraError(
                f"{cfg.name} at {cfg.safe_url} timed out after {cfg.timeout:g}s "
                f"({type(exc).__name__})."
            ) from exc
        except httpx.HTTPError as exc:
            raise CameraError(
                f"{cfg.name} is unreachable at {cfg.safe_url} "
                f"({type(exc).__name__}: {exc})."
            ) from exc
        raise CameraError(
            f"{cfg.name} closed the stream before a complete frame arrived."
        )

    async def _fetch_rtsp(self) -> Frame:
        cfg = self.config
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise CameraError(FFMPEG_MISSING)

        args = [arg.format(url=cfg.url) for arg in FFMPEG_ARGS]
        try:
            process = await asyncio.create_subprocess_exec(
                ffmpeg, *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            raise CameraError(f"could not run ffmpeg for {cfg.name}: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=cfg.timeout
            )
        except asyncio.TimeoutError as exc:
            await _terminate(process)
            raise CameraError(
                f"ffmpeg did not produce a frame from {cfg.name} "
                f"({cfg.safe_url}) within {cfg.timeout:g}s."
            ) from exc

        if process.returncode != 0 or not stdout:
            detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
            reason = detail[-1] if detail else f"exit code {process.returncode}"
            raise CameraError(
                f"ffmpeg could not read {cfg.name} ({cfg.safe_url}): {reason}"
            )
        return Frame(self._check_size(bytes(stdout)), camera=cfg.name)

    def _fetch_mqtt(self) -> Frame:
        frame = self._mqtt_frame
        if frame is None:
            raise CameraError(
                f"no frame has arrived on {self.config.topic!r} yet. An MQTT "
                "camera can only be looked at once it has published something."
            )
        return frame


async def _terminate(process: Any) -> None:
    """Kill a stuck ffmpeg and reap it, without ever blocking on it."""
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:  # pragma: no cover - already gone
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except (asyncio.TimeoutError, Exception):  # pragma: no cover - defensive
        _LOGGER.warning("vision: an ffmpeg process would not die")


# ---------------------------------------------------------------------------
# writing a frame out (only ever when asked by name)
# ---------------------------------------------------------------------------
def resolve_snapshot_path(jarvis: "Jarvis", filename: str) -> Path:
    """Where ``camera.snapshot: filename:`` is allowed to write.

    Inside the config directory, and nowhere else. A camera integration that
    will write a JPEG to any path it is handed is a file-write primitive
    wearing a hat, and the thing asking may be a model that just read a web
    page.
    """
    root = Path(jarvis.config_dir).resolve()
    candidate = Path(str(filename)).expanduser()
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if target != root and root not in target.parents:
        raise CameraError(
            f"refusing to write outside the config directory ({root}). "
            "Give a path inside it, such as 'snapshots/front_door.jpg'."
        )
    if target.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
        raise CameraError("a snapshot filename must end in .jpg, .png or .webp")
    return target


def write_snapshot_sync(path: Path, data: bytes) -> str:
    """Create the directory and write the frame. Blocking — call it in a thread.

    Megabytes to a file the operator named, quite possibly on an SD card. Not
    long, but long enough to be the wrong thing to do on the event loop, and
    the codebase already puts its file I/O on :func:`asyncio.to_thread`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


# ---------------------------------------------------------------------------
# entity
# ---------------------------------------------------------------------------
class CameraEntity(Entity):
    """A camera in the state machine: idle, or streaming while it is read."""

    _attr_should_poll = False
    _attr_icon = "mdi:cctv"

    def __init__(self, source: CameraSource) -> None:
        self.source = source
        self._attr_name = source.config.name
        self._attr_unique_id = f"vision_{source.config.name}"
        self._attr_state = STATE_IDLE

    @property
    def config(self) -> CameraConfig:
        return self.source.config

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "platform": self.config.platform,
            "area": self.config.area or None,
            "consent": self.config.consent,
            "source": self.config.safe_url,
            "last_snapshot_at": iso(self.source.last_snapshot_at),
            "last_frame_at": iso(self.source.last_frame_at),
            "last_error": self.source.last_error or None,
        }

    def mark_streaming(self) -> None:
        self._attr_state = STATE_STREAMING
        self.async_write_state()

    def mark_idle(self, ok: bool = True) -> None:
        self._attr_state = STATE_IDLE
        self._attr_available = bool(ok)
        self.async_write_state()


__all__ = [
    "CAMERA_DOMAIN",
    "DEFAULT_FRAME_TTL",
    "DEFAULT_GO2RTC_URL",
    "DEFAULT_GO2RTC_WIDTH",
    "DEFAULT_MAX_FRAME_BYTES",
    "FFMPEG_MISSING",
    "MAX_MARKER_STEPS",
    "PLATFORMS",
    "PLATFORM_GO2RTC",
    "PLATFORM_MJPEG",
    "PLATFORM_MQTT",
    "PLATFORM_RTSP",
    "PLATFORM_STILL",
    "STATE_STREAMING",
    "CameraConfig",
    "CameraEntity",
    "CameraError",
    "CameraSource",
    "Frame",
    "FrameStore",
    "JpegScanner",
    "decode_payload",
    "extract_jpeg",
    "go2rtc_snapshot_url",
    "iso",
    "jpeg_dimensions",
    "redact_url",
    "resolve_snapshot_path",
    "write_snapshot_sync",
]
