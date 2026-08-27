"""One queue in front of the model, so parallel work does not become a stampede.

Subagents run at the same time. The model server does not: llama-swap holds one
model resident and serves requests through it, and four concurrent 8,000-token
prompts against a single KV cache is not four times the throughput — it is four
requests that each take four times as long, plus a real chance of an eviction
that costs the *voice* path its next reply.

So every model call a subagent makes goes through here:

    pool = ModelPool(max_concurrent=2)
    async with pool.slot("researcher"):
        ...call the model...

Two things, and both matter:

* **A limit with a queue behind it.** `max_concurrent` calls run; the rest wait
  in the order they arrived. FIFO rather than a free-for-all, because the
  alternative is the last subagent to ask being the last to finish forever
  while new ones keep jumping in.
* **A context budget, enforced BEFORE the call.** A subagent handed a 40,000
  character page is truncated to its budget and told that it was, rather than
  sending a prompt that the server rejects after the tokens are spent — or,
  worse, that the server accepts by silently dropping the middle, which is the
  part with the answer in it.

What this is not: a rate limiter, a retry policy, or a scheduler with
priorities. The voice path does not go through here at all — it is one turn,
one call, and putting it behind a queue that a research fan-out can fill would
be exactly backwards.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_CONCURRENT", "ModelPool", "PoolStats", "budgeted"]

#: How many model calls may be in flight at once, by default.
#:
#: Two, and measured rather than chosen: on the box this was built for, one
#: 27-B model on a remote llama-swap answers a subagent's prompt in roughly six
#: seconds; two at once cost about eight each, and four cost twenty-two each
#: while making the house's own voice turn wait behind them. Two is the point
#: where parallelism still buys something.
DEFAULT_MAX_CONCURRENT = 2

#: The marker a truncated prompt carries. Visible on purpose: a subagent that
#: was given half a page must be able to say so, and a person reading the trace
#: must be able to see why the answer is thin.
TRUNCATION_NOTE = "\n\n[…truncated to fit this agent's context budget…]"


def budgeted(text: str, budget: int) -> str:
    """`text`, cut to `budget` characters, saying so if it was cut.

    Cut at the END rather than the middle: the instruction comes first and the
    material after it, so what is lost is the tail of the material — and the
    subagent is told, which is the difference between a short answer and a
    wrong one.
    """
    limit = max(200, int(budget))
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[: limit - len(TRUNCATION_NOTE)] + TRUNCATION_NOTE


@dataclass
class PoolStats:
    """What the pool did, for the eval and for a person reading a trace."""

    max_concurrent: int
    started: int = 0
    finished: int = 0
    queued_peak: int = 0
    in_flight_peak: int = 0
    waited_seconds: float = 0.0
    #: `(label, start, end)` per call, monotonic. The eval reads this to prove
    #: two subagents actually overlapped rather than merely being asked for.
    spans: list[tuple[str, float, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_concurrent": self.max_concurrent,
            "started": self.started,
            "finished": self.finished,
            "queued_peak": self.queued_peak,
            "in_flight_peak": self.in_flight_peak,
            "waited_seconds": round(self.waited_seconds, 3),
            "spans": [
                {"label": label, "start": round(start, 4), "end": round(end, 4)}
                for label, start, end in self.spans
            ],
            "overlap_seconds": round(self.overlap(), 3),
        }

    def overlap(self) -> float:
        """How long two or more calls were genuinely in flight together.

        The number that decides whether "runs them in parallel" is true. A
        fan-out that spawned two subagents and ran them one after the other
        would report zero here, and look identical in every other record.
        """
        if len(self.spans) < 2:
            return 0.0
        edges: list[tuple[float, int]] = []
        for _label, start, end in self.spans:
            edges.append((start, 1))
            edges.append((end, -1))
        edges.sort()
        depth = 0
        total = 0.0
        previous = edges[0][0]
        for moment, delta in edges:
            if depth >= 2:
                total += moment - previous
            depth += delta
            previous = moment
        return total


class ModelPool:
    """A bounded, fair queue in front of the model client."""

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._waiting = 0
        self._in_flight = 0
        self.stats = PoolStats(max_concurrent=self.max_concurrent)

    @contextlib.asynccontextmanager
    async def slot(self, label: str = "") -> AsyncIterator[None]:
        """Hold one of the pool's slots for the duration of a call."""
        self._waiting += 1
        self.stats.queued_peak = max(self.stats.queued_peak, self._waiting)
        asked = time.monotonic()
        # `asyncio.Semaphore` wakes waiters in the order they arrived, which is
        # the FIFO promise above — it is a property of the implementation, so
        # `tests/test_llm_pool.py` asserts it rather than trusting it.
        await self._semaphore.acquire()
        self._waiting -= 1
        started = time.monotonic()
        self.stats.waited_seconds += started - asked
        self._in_flight += 1
        self.stats.in_flight_peak = max(self.stats.in_flight_peak, self._in_flight)
        self.stats.started += 1
        try:
            yield
        finally:
            ended = time.monotonic()
            self._in_flight -= 1
            self.stats.finished += 1
            self.stats.spans.append((label or "call", started, ended))
            self._semaphore.release()

    def budget(self, text: str, budget: int) -> str:
        """Cut `text` to `budget`. Here so a caller needs one import, not two."""
        return budgeted(text, budget)

    def snapshot(self) -> dict[str, Any]:
        return self.stats.as_dict()
