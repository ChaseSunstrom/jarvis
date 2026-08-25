"""A cross-encoder, asked which of these results actually answers the question.

Retrieval gets you twenty candidates that share words with the query. A
cross-encoder reads the query and one candidate *together* and scores that pair
— which is slow enough that you would never run it over a whole store, and
cheap enough to run over twenty. Reranking after retrieval is the largest
quality gain per line of code in this system, and it is one HTTP call.

    reranker = Reranker(url="http://127.0.0.1:7998")
    order = await reranker.order("where do we keep the caffeine", texts)

What it must never do is fail a search. Every error path returns "no opinion"
and the caller keeps the order it already had — a reranker that is down makes
retrieval no better, never worse, and says so once rather than per query.

Two wire shapes exist for the same idea and this speaks both: Text Embeddings
Inference takes ``{"query", "texts"}``, Infinity and Jina take
``{"query", "documents"}``. The first shape is tried once, and a 4xx switches
to the other for the life of the process — a rerank service is not something
you swap mid-conversation, and probing on every call would double the traffic.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import httpx

_LOGGER = logging.getLogger(__name__)

#: How long a rerank may take before the answer is "no opinion". A cross-encoder
#: over twenty short notes is tens of milliseconds on a CPU; a second is already
#: a broken service, and the turn behind this is waiting.
DEFAULT_TIMEOUT = 3.0

#: Never send the whole store to a cross-encoder. It is O(candidates), and the
#: point of retrieval is to have narrowed it first.
MAX_CANDIDATES = 40


class Reranker:
    """One rerank endpoint, or nothing at all."""

    def __init__(
        self,
        url: str = "",
        model: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        client: Any = None,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.model = str(model or "")
        self.timeout = float(timeout)
        self._client = client
        #: "texts" or "documents", learned on first use.
        self._key = "texts"
        #: Set after a failure that will keep failing, so the log line and the
        #: latency are both paid once.
        self._off = False

    @property
    def configured(self) -> bool:
        return bool(self.url) and not self._off

    def _endpoint(self) -> str:
        return self.url if self.url.endswith("/rerank") else f"{self.url}/rerank"

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(self._endpoint(), json=body, timeout=self.timeout)
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            return await http.post(self._endpoint(), json=body)

    async def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        """A score per text, in the order given, or [] for "no opinion"."""
        candidates = [str(t) for t in texts][:MAX_CANDIDATES]
        if not self.configured or not query.strip() or len(candidates) < 2:
            return []
        body: dict[str, Any] = {"query": query, self._key: candidates}
        if self.model:
            body["model"] = self.model
        try:
            answer = await self._post(body)
            if answer.status_code in (400, 404, 422) and self._key == "texts":
                # The other dialect. Learned once, not probed per call.
                self._key = "documents"
                body = {"query": query, "documents": candidates}
                if self.model:
                    body["model"] = self.model
                answer = await self._post(body)
            answer.raise_for_status()
            payload = answer.json()
        except Exception as exc:  # noqa: BLE001 - a reranker is never fatal
            if not self._off:
                _LOGGER.info(
                    "Reranking is off: %s at %s (%s). Retrieval still works — "
                    "results are in the order retrieval found them.",
                    type(exc).__name__, self._endpoint(), exc,
                )
            self._off = True
            return []

        rows = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        out = [0.0] * len(candidates)
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = int(row.get("index", -1))
            score = row.get("score", row.get("relevance_score"))
            if 0 <= index < len(out) and score is not None:
                out[index] = float(score)
        return out

    async def order(self, query: str, texts: Sequence[str]) -> list[int]:
        """The indices of `texts`, best first — or [] for "keep what you had"."""
        scored = await self.scores(query, texts)
        if not scored:
            return []
        return sorted(range(len(scored)), key=lambda i: -scored[i])
