"""Serving on-device model files to the phone, from the user's own server.

The Android app can run wake-word detection and speech-to-text locally, which
is faster on the tail of an utterance, works with the server unreachable, and —
the real reason — stops a continuous microphone feed leaving the phone. To do
that it needs model weights, and the weights have to come from somewhere.

**Not from the APK.** They are tens of megabytes, they change on their own
schedule, and most people will never turn the feature on. Shipping them in the
package makes every install pay for a minority feature.

**Not from the internet, either.** That is the part worth being careful about.
A phone that fetches from GitHub or Hugging Face tells a third party — and
every network between — that this device is setting up a private voice
assistant, right down to which wake word. It also breaks the one rule the app
holds everywhere else: it talks to the configured Jarvis and to nothing else,
with a pinned host and a bearer token. An exception carved out for "just the
model download" is an exception in the code path that runs on an unconfigured,
freshly-installed device.

So the server is the mirror. jarvis-core fetches a model once, on a machine
that already reaches the internet for `ollama pull`, verifies it against a
digest pinned in this file, and serves it to the phone over the same
authenticated origin the phone already uses. The phone gains no new network
trust, and on a WireGuard-only network it works unchanged.

The digest is the whole security story for the fetch: the upstream URL is
plain HTTPS to a third party, so what makes the bytes trustworthy is that they
hash to a value written down here, in the repository, reviewed like code.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Where fetched weights live, under the config directory next to `.storage`.
CACHE_DIRNAME = "models"

#: Refuse anything larger than this, however big the catalogue claims it is.
#: A mirror that streams an unbounded body into a home server's disk is a
#: denial-of-service with extra steps.
MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One downloadable file, and what it must hash to."""

    name: str
    url: str
    sha256: str
    #: What it is for, shown in the console and in the app.
    purpose: str
    #: Rough size, for a progress bar before the download starts.
    bytes: int = 0


#: The catalogue. Deliberately a hardcoded tuple rather than anything the
#: network or the model can extend: this is a list of URLs the server will
#: fetch and write to disk, so it is exactly the kind of list that must not be
#: writable from a request.
#:
#: openWakeWord's three-stage pipeline. The first two are shared by every wake
#: word; only the last is specific to "hey jarvis", which is why they are
#: separate files rather than one bundle.
CATALOGUE: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="melspectrogram.onnx",
        url=(
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
            "melspectrogram.onnx"
        ),
        sha256="ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
        purpose="Audio to mel frames. Shared by every wake word.",
        bytes=1_087_958,
    ),
    ModelSpec(
        name="embedding_model.onnx",
        url=(
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
            "embedding_model.onnx"
        ),
        sha256="70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
        purpose="Mel frames to embeddings. Shared by every wake word.",
        bytes=1_326_578,
    ),
    ModelSpec(
        name="hey_jarvis_v0.1.onnx",
        url=(
            "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
            "hey_jarvis_v0.1.onnx"
        ),
        sha256="94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
        purpose="The wake word itself.",
        bytes=1_271_370,
    ),
)

CATALOGUE_BY_NAME: dict[str, ModelSpec] = {spec.name: spec for spec in CATALOGUE}


def cache_dir(jarvis: "Jarvis") -> Path:
    return Path(jarvis.config_dir) / CACHE_DIRNAME


def local_path(jarvis: "Jarvis", name: str) -> Path | None:
    """Where `name` lives, or None if it is not a catalogue entry.

    Membership of the catalogue IS the path validation. Building a path from a
    request-supplied name and then trying to prove it stayed inside a directory
    is the losing half of that argument; a fixed set of known names never
    enters it.
    """
    spec = CATALOGUE_BY_NAME.get(name)
    if spec is None:
        return None
    return cache_dir(jarvis) / spec.name


def digest_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def is_cached(jarvis: "Jarvis", name: str) -> bool:
    path = local_path(jarvis, name)
    return bool(path and path.is_file() and path.stat().st_size > 0)


def catalogue_payload(jarvis: "Jarvis") -> list[dict[str, object]]:
    """Every model, whether it is here yet, and how big it is."""
    return [
        {
            "name": spec.name,
            "purpose": spec.purpose,
            "bytes": spec.bytes,
            "sha256": spec.sha256,
            "cached": is_cached(jarvis, spec.name),
        }
        for spec in CATALOGUE
    ]


class ModelError(Exception):
    """A fetch failed, with a sentence for the user."""


async def async_ensure(jarvis: "Jarvis", name: str) -> Path:
    """Return a verified local copy of `name`, fetching it once if needed.

    Raises [ModelError] rather than returning a half-written file: a truncated
    ONNX model does not fail loudly on the phone, it fails as a wake word that
    never triggers, which is indistinguishable from the feature being off.
    """
    spec = CATALOGUE_BY_NAME.get(name)
    if spec is None:
        raise ModelError(f"{name!r} is not a model this server knows about")

    path = cache_dir(jarvis) / spec.name
    if path.is_file() and path.stat().st_size > 0:
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        await asyncio.to_thread(_download, spec, tmp)
        actual = await asyncio.to_thread(digest_of, tmp)
        if actual != spec.sha256:
            raise ModelError(
                f"{spec.name} did not match its pinned digest "
                f"(got {actual[:16]}…, expected {spec.sha256[:16]}…). "
                "Refusing to serve it."
            )
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    _LOGGER.info("models: fetched %s (%d bytes)", spec.name, path.stat().st_size)
    return path


def _download(spec: ModelSpec, target: Path) -> None:
    """Blocking fetch. Runs on a worker thread; never on the event loop."""
    import urllib.request

    request = urllib.request.Request(spec.url, headers={"User-Agent": "jarvis-core"})
    written = 0
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        if getattr(response, "status", 200) != 200:
            raise ModelError(f"{spec.url} answered {response.status}")
        with target.open("wb") as handle:
            while True:
                block = response.read(1024 * 256)
                if not block:
                    break
                written += len(block)
                if written > MAX_BYTES:
                    raise ModelError(f"{spec.name} is larger than {MAX_BYTES} bytes")
                handle.write(block)
    if written == 0:
        raise ModelError(f"{spec.url} returned nothing")
