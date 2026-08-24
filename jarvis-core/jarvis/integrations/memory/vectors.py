"""Semantic recall for the note store, with no new dependency and no new box.

## Why this is not a vector database

`MemoryStore._score` is token overlap with a stop list. It finds "the good
coffee is in the left cupboard" from *"where is the good coffee"* and misses it
entirely from *"where do we keep the caffeine"* — the words do not touch. That
is the gap this closes.

The obvious answer is a vector store, and at this size the obvious answer is
wrong. The store holds `max_entries: 500` notes. Cosine over 500 x 768 floats
is roughly 380k multiply-adds: single-digit milliseconds in pure Python, beside
an 8B generation that takes seconds. A dedicated service would add a container,
a port, a volume, a failure mode and a second copy of the user's private notes,
to make an irrelevant cost smaller. Qdrant specifically was measured and
rejected on a second count: its stock container POSTs to `telemetry.qdrant.io`
hourly, and "nothing goes to the cloud at runtime" is the first sentence of this
project's README.

So: vectors come from the model server that is **already running**, over the
`embed()` call on the LLM client, and live in a plain JSON sidecar.
`jarvis-core`'s dependencies stay seven pure-Python wheels — no numpy, no
onnxruntime — and the image still builds on a Pi, which `DEVIATIONS.md` §9
records as a deliberate constraint.

## The sidecar is derived, and disposable

    <config>/.storage/memory.json           the notes. Source of truth.
    <config>/.storage/memory-vectors.json   this. Delete it and it rebuilds.

Entries are keyed by note id and carry a hash of the text they were computed
from, so a hand-edit to `memory.json` — which is a supported thing to do, the
whole store is meant to be readable and editable — invalidates exactly the one
vector it should. Changing the embedding model invalidates all of them at once,
because the model name is recorded at the top and a mismatch discards the file.

**Nothing here is ever the only copy of anything.** A note without a vector is
found by keyword, which is what happens today; a note with one is found by both.

## Degrading

Every failure mode ends in "fall back to keyword search", never in an error the
user sees:

  * no embedding model pulled -> `embed()` 404s -> semantic recall is off;
  * the model server is down -> the turn was not going to happen anyway;
  * a dimension change mid-life -> the sidecar is discarded and rebuilt.

## Task prefixes

`nomic-embed-text` is trained with them and is measurably worse without:
documents are embedded as `search_document: <text>` and queries as
`search_query: <text>`. Omitting them does not fail loudly — it just retrieves
badly, which is the exact failure this module exists to fix, so they are applied
here rather than left to callers. A model that does not use prefixes is not
harmed by them beyond a couple of tokens.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import struct
from array import array
from typing import Any, Iterable, Sequence

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "memory-vectors"
STORAGE_VERSION = 1

#: The default embedding model. 137M params, 768 dimensions, Apache-2.0, and
#: `ollama pull nomic-embed-text` is the whole installation. `all-minilm` (46 MB,
#: 384 dims) is the Pi-tier alternative and needs no code change.
DEFAULT_EMBED_MODEL = "nomic-embed-text"

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

#: Below this, two notes are not about the same thing. Cosine on normalised
#: embeddings runs about 0.3-0.5 for unrelated text from the same domain, so a
#: floor well above that is what keeps "everything ranked" from being mistaken
#: for "everything relevant".
SIMILARITY_FLOOR = 0.62


def text_hash(text: str) -> str:
    """Short digest of the text a vector was computed from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def pack(vector: Sequence[float]) -> str:
    """Floats -> base64. About 40% the size of a JSON array of decimals, and
    the sidecar sits beside notes the user is invited to read — a wall of
    seventeen-digit floats would make `memory.json`'s neighbour unreadable."""
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


def unpack(blob: str) -> array:
    raw = base64.b64decode(blob.encode("ascii"))
    out = array("f")
    out.frombytes(raw)
    return out


def normalise(vector: Sequence[float]) -> array:
    """Unit length, so similarity is a dot product and nothing else.

    Stored normalised rather than normalised per query: the division happens
    once per note ever, instead of once per note per turn.
    """
    out = array("f", (float(x) for x in vector))
    length = math.sqrt(sum(x * x for x in out))
    if length <= 0:
        return out
    for index in range(len(out)):
        out[index] = out[index] / length
    return out


def similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine of two already-normalised vectors, i.e. their dot product."""
    if len(a) != len(b) or not a:
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


class VectorIndex:
    """The sidecar: id -> (hash, unit vector), plus the arithmetic over it.

    Deliberately not a class that knows about notes. It is handed text and ids
    by `MemoryStore` and hands back scores; what a note *is* stays in one place.
    """

    def __init__(
        self,
        client: Any = None,
        model: str = DEFAULT_EMBED_MODEL,
        store: Any = None,
    ) -> None:
        self.client = client
        self.model = model or DEFAULT_EMBED_MODEL
        self._store = store
        self._vectors: dict[str, array] = {}
        self._hashes: dict[str, str] = {}
        self._dim = 0
        #: Set once an `embed()` call has failed in a way that will keep
        #: failing — no model pulled, endpoint absent. Stops one missing model
        #: from adding an HTTP round trip to every note written for the rest of
        #: the process's life.
        self._unavailable = False

    @property
    def enabled(self) -> bool:
        return self.client is not None and not self._unavailable

    def __len__(self) -> int:
        return len(self._vectors)

    # --- persistence ------------------------------------------------------
    async def async_load(self) -> None:
        if self._store is None:
            return
        try:
            data = await self._store.load()
        except Exception:  # a broken sidecar is a rebuild, never a failure
            _LOGGER.warning("Could not read the vector sidecar; rebuilding", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        if str(data.get("model") or "") != self.model:
            # A different embedding model produces vectors in a different
            # space. Comparing across them is not "slightly worse", it is
            # meaningless, so the whole file goes.
            if data.get("model"):
                _LOGGER.info(
                    "Embedding model changed %s -> %s; rebuilding the index",
                    data.get("model"),
                    self.model,
                )
            return
        vectors = data.get("vectors")
        if not isinstance(vectors, dict):
            return
        for entry_id, record in vectors.items():
            if not isinstance(record, dict):
                continue
            blob, digest = record.get("v"), record.get("h")
            if not isinstance(blob, str) or not isinstance(digest, str):
                continue
            try:
                vector = unpack(blob)
            except Exception:
                continue
            self._vectors[str(entry_id)] = vector
            self._hashes[str(entry_id)] = digest
            self._dim = self._dim or len(vector)

    async def async_save(self) -> None:
        if self._store is None:
            return
        try:
            await self._store.save(
                {
                    "model": self.model,
                    "dim": self._dim,
                    "vectors": {
                        entry_id: {"h": self._hashes.get(entry_id, ""), "v": pack(vector)}
                        for entry_id, vector in self._vectors.items()
                    },
                }
            )
        except Exception:  # losing the cache costs a rebuild, not a turn
            _LOGGER.warning("Could not write the vector sidecar", exc_info=True)

    # --- maintenance ------------------------------------------------------
    def is_current(self, entry_id: str, text: str) -> bool:
        return self._hashes.get(entry_id) == text_hash(text)

    def forget(self, entry_id: str) -> None:
        self._vectors.pop(entry_id, None)
        self._hashes.pop(entry_id, None)

    def prune(self, live_ids: Iterable[str]) -> int:
        """Drop vectors for notes that no longer exist.

        Without this the sidecar is the one place a forgotten note leaves a
        trace, which is precisely the promise the memory integration makes
        about being deletable.
        """
        keep = set(live_ids)
        stale = [entry_id for entry_id in self._vectors if entry_id not in keep]
        for entry_id in stale:
            self.forget(entry_id)
        return len(stale)

    async def async_clear(self) -> None:
        """Drop every vector and write the empty sidecar out.

        Saved rather than merely emptied in memory: "delete everything" that
        leaves a file full of embeddings on disk has deleted nothing a user
        would recognise as their data.
        """
        self._vectors.clear()
        self._hashes.clear()
        await self.async_save()

    async def async_index(self, items: Sequence[tuple[str, str]]) -> int:
        """Embed and store `(id, text)` pairs whose vectors are missing or stale."""
        if not self.enabled:
            return 0
        wanted = [(i, t) for i, t in items if t.strip() and not self.is_current(i, t)]
        if not wanted:
            return 0
        vectors = await self._embed([DOCUMENT_PREFIX + t for _, t in wanted])
        if not vectors:
            return 0
        written = 0
        for (entry_id, text), vector in zip(wanted, vectors):
            if not vector:
                continue
            unit = normalise(vector)
            self._dim = self._dim or len(unit)
            if len(unit) != self._dim:
                # Two dimensionalities in one index cannot be compared. The
                # newcomer is the odd one out only by arrival order, so the
                # honest move is to drop the file and start again.
                _LOGGER.info("Embedding dimension changed; rebuilding the index")
                self._vectors.clear()
                self._hashes.clear()
                self._dim = len(unit)
            self._vectors[entry_id] = unit
            self._hashes[entry_id] = text_hash(text)
            written += 1
        await self.async_save()
        return written

    # --- retrieval --------------------------------------------------------
    async def async_search(self, query: str) -> dict[str, float]:
        """`{entry_id: similarity}` for everything at or above the floor."""
        if not self.enabled or not self._vectors or not query.strip():
            return {}
        vectors = await self._embed([QUERY_PREFIX + query])
        if not vectors or not vectors[0]:
            return {}
        probe = normalise(vectors[0])
        if len(probe) != self._dim:
            return {}
        scored = {
            entry_id: similarity(probe, vector)
            for entry_id, vector in self._vectors.items()
        }
        return {i: s for i, s in scored.items() if s >= SIMILARITY_FLOOR}

    # --- plumbing ---------------------------------------------------------
    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        embed = getattr(self.client, "embed", None)
        if not callable(embed):
            self._unavailable = True
            return []
        try:
            return await embed(list(texts), model=self.model)
        except Exception as exc:
            # One log line, then quiet. A model that is not pulled will not
            # become pulled by being asked five hundred more times, and a note
            # store that logs a stack trace per write is unusable.
            if not self._unavailable:
                _LOGGER.warning(
                    "Semantic recall is off: could not embed with %r (%s). "
                    "`ollama pull %s` switches it on; keyword search is "
                    "unaffected either way.",
                    self.model,
                    exc,
                    self.model,
                )
            self._unavailable = True
            return []


def fuse(keyword: dict[str, float], semantic: dict[str, float], k: int = 60) -> list[str]:
    """Reciprocal rank fusion of two rankings, best first.

    RRF rather than a weighted sum of the scores, because the two do not share
    a scale: token overlap is a fraction of matched terms and cosine is an
    angle, and a weight that balanced them on one query would not on the next.
    RRF only reads each list's *order*, which is the part both agree on the
    meaning of. `k=60` is the constant from the original paper and is not
    sensitive at this size.

    A note in both lists outranks a note in either, which is the property that
    matters: agreement between a lexical and a semantic match is the strongest
    signal available here.
    """
    ranks: dict[str, float] = {}
    for ranking in (keyword, semantic):
        ordered = sorted(ranking, key=lambda i: -ranking[i])
        for position, entry_id in enumerate(ordered):
            ranks[entry_id] = ranks.get(entry_id, 0.0) + 1.0 / (k + position + 1)
    return sorted(ranks, key=lambda i: -ranks[i])
