"""Pure-logic channel helpers: rate limiting, reconnect backoff, admission control.

No clock of their own, no sockets, no randomness — time and randomness are
parameters, so every test is deterministic and every one of these is a plain
function of its inputs.

Ported from ``android-app/.../channel/{TokenBucket,Backoff,CommandGate}.kt``.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

__all__ = ["TokenBucket", "Backoff", "CommandGate", "Admission"]


class TokenBucket:
    """Rate limit for inbound commands and outbound events.

    A server that has been prompt-injected — or a server someone else now
    controls — can send commands as fast as the socket allows. Policy still
    gates every one of them, but a flood of Tier-3 requests is a denial of
    service against the *user*: a wall of consent prompts is a wall nobody
    reads, and a wall nobody reads is a wall somebody clicks through.

    So the agent bounds the arrival rate before policy is ever consulted.

    Monotonic time only. Pass ``time.monotonic()``; wall-clock jumps (NTP, the
    user changing the date) would otherwise hand out a free refill. Time going
    backwards is treated as zero elapsed rather than as a negative refill.
    """

    #: Ten commands back to back, then one per second sustained. Sized against
    #: what a real turn looks like: "turn the lights off and set an alarm" is
    #: two or three commands; a task the server drives step by step might be
    #: eight. Sixty a minute is far more than a conversation produces and far
    #: less than a loop produces.
    DEFAULT_CAPACITY = 10.0
    DEFAULT_REFILL_PER_SECOND = 1.0

    def __init__(
        self,
        capacity: float = DEFAULT_CAPACITY,
        refill_per_second: float = DEFAULT_REFILL_PER_SECOND,
        start: float = 0.0,
        initial_tokens: float | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        start_tokens = self.capacity if initial_tokens is None else float(initial_tokens)
        self._tokens = min(max(start_tokens, 0.0), self.capacity)
        self._last = float(start)
        self._lock = threading.Lock()

    def peek(self, now: float) -> float:
        """Tokens available at ``now``, without consuming any."""
        with self._lock:
            self._refill(now)
            return self._tokens

    def try_acquire(self, now: float, cost: float = 1.0) -> bool:
        """Spend ``cost`` tokens. False means: do not dispatch it, and answer
        the server with an error — never silently swallow it, or the server
        hangs forever waiting for a ``device_result``."""
        with self._lock:
            self._refill(now)
            if self._tokens < cost:
                return False
            self._tokens -= cost
            return True

    def wait_s(self, now: float, cost: float = 1.0) -> float:
        """Seconds until ``cost`` tokens exist. 0 when they already do."""
        with self._lock:
            self._refill(now)
            if self._tokens >= cost:
                return 0.0
            deficit = cost - self._tokens
            return math.ceil(deficit / self.refill_per_second * 1000.0) / 1000.0

    def reset(self, now: float) -> None:
        """Refill to full. Used when a fresh socket comes up, never by the server."""
        with self._lock:
            self._tokens = self.capacity
            self._last = float(now)

    def _refill(self, now: float) -> None:
        elapsed = float(now) - self._last
        self._last = float(now)
        if elapsed <= 0:
            # Backwards clock => no elapsed time, and re-anchored above so the
            # next call measures from here.
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)

    @staticmethod
    def for_commands(start: float = 0.0) -> "TokenBucket":
        return TokenBucket(TokenBucket.DEFAULT_CAPACITY, TokenBucket.DEFAULT_REFILL_PER_SECOND, start)

    @staticmethod
    def for_events(start: float = 0.0) -> "TokenBucket":
        """Outbound ``device_event`` limiter. Triggers can chatter — a flapping
        network link produces up/down pairs for as long as the NIC is unhappy —
        and there is no reason to relay that at full rate."""
        return TokenBucket(20.0, 2.0, start)


class Backoff:
    """Reconnect delay: exponential, capped, jittered.

    Jitter is not decoration. An agent that reconnects on a fixed schedule after
    a server restart joins every other client in a thundering herd, and a fixed
    cadence is a beacon: anyone watching the WireGuard link sees "this machine
    is a Jarvis client" from the timing alone.

    Randomness is injected as a ``0.0 <= r < 1.0`` argument so the schedule is a
    pure function and the tests assert exact numbers.
    """

    DEFAULT_BASE_S = 1.0
    DEFAULT_MAX_S = 300.0
    MAX_ATTEMPT = 16
    PENALTY_ATTEMPT = 6

    def __init__(
        self,
        base_s: float = DEFAULT_BASE_S,
        max_s: float = DEFAULT_MAX_S,
        factor: float = 2.0,
    ) -> None:
        if base_s <= 0:
            raise ValueError("base_s must be positive")
        if max_s < base_s:
            raise ValueError("max_s must be >= base_s")
        if factor <= 1.0:
            raise ValueError("factor must be > 1")
        self.base_s = float(base_s)
        self.max_s = float(max_s)
        self.factor = float(factor)
        self.attempt = 0

    def ceiling_for(self, attempt: int) -> float:
        """The window an attempt draws from, before jitter."""
        if attempt <= 0:
            return self.base_s
        value = self.base_s
        for _ in range(attempt):
            value *= self.factor
            if value >= self.max_s:
                return self.max_s
        return min(max(value, self.base_s), self.max_s)

    def delay_for(self, attempt: int, random: float) -> float:
        """Uniform in ``[base_s, ceiling_for(attempt)]``.

        "Full jitter with a floor" rather than textbook full jitter
        (``[0, cap]``), because a zero-length delay against a server that is
        refusing the handshake is a hot loop with extra steps.
        """
        r = min(max(float(random), 0.0), 0.999999)
        ceiling = self.ceiling_for(attempt)
        if ceiling <= self.base_s:
            return self.base_s
        return self.base_s + (ceiling - self.base_s) * r

    def next(self, random: float) -> float:
        """Advance one step and return the delay to sleep."""
        delay = self.delay_for(self.attempt, random)
        if self.attempt < self.MAX_ATTEMPT:
            self.attempt += 1
        return delay

    def reset(self) -> None:
        """Back to the floor. Call this only after a *successful registration*."""
        self.attempt = 0

    def penalise(self, minimum_attempt: int | None = None) -> None:
        """Jump straight to a long delay without walking there.

        For failures that will not fix themselves in a second: a rejected token,
        a server URL the host pin refuses. Retrying those quickly accomplishes
        nothing except hammering the server's auth path.
        """
        floor = self.PENALTY_ATTEMPT if minimum_attempt is None else minimum_attempt
        if self.attempt < floor:
            self.attempt = min(floor, self.MAX_ATTEMPT)


@dataclass(frozen=True)
class Admission:
    """What :class:`CommandGate` decided. Every branch has a defined reply."""

    kind: str
    #: The stored reply, for ``already_answered``.
    reply: dict | None = None
    detail: str = ""

    ACCEPTED = "accepted"
    ALREADY_ANSWERED = "already_answered"
    STILL_RUNNING = "still_running"
    ACTION_BUSY = "action_busy"
    AT_CAPACITY = "at_capacity"
    MALFORMED = "malformed"

    @property
    def accepted(self) -> bool:
        return self.kind == Admission.ACCEPTED


class CommandGate:
    """Admission control for inbound ``device_command`` frames.

    Four things it enforces:

    1. **Exactly-once execution per ``command_id``.** A reconnect can redeliver;
       a retrying server can duplicate. Neither may run ``run_command`` twice. A
       repeat of a finished command replays the stored reply.
    2. **One in-flight command per action id.** Two ``type_text``s racing into
       the same field is not a feature. The second is refused, not queued —
       queueing would let a flood build a backlog that outlives the flood.
    3. **A global concurrency cap.** Actions hold real resources and each can be
       sitting on a consent prompt for a minute.
    4. **Bounded memory.** The dedupe history is a fixed-size LRU. Unlimited
       distinct ``command_id``s get a bounded map, not an OOM.
    """

    DEFAULT_MAX_CONCURRENT = 4
    DEFAULT_HISTORY = 128

    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        history_size: int = DEFAULT_HISTORY,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.history_size = max(1, int(history_size))
        self._running_by_command: dict[str, str] = {}
        self._running_actions: set[str] = set()
        self._answered: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def in_flight(self) -> int:
        with self._lock:
            return len(self._running_by_command)

    def admit(self, command_id: object, action_id: object) -> Admission:
        cid = str(command_id or "").strip()
        action = str(action_id or "").strip()
        if not cid:
            return Admission(Admission.MALFORMED, detail="device_command without a command_id")
        if not action:
            return Admission(Admission.MALFORMED, detail="device_command without an action")

        with self._lock:
            if cid in self._answered:
                self._answered.move_to_end(cid)
                return Admission(Admission.ALREADY_ANSWERED, reply=self._answered[cid])
            if cid in self._running_by_command:
                return Admission(Admission.STILL_RUNNING)
            if action in self._running_actions:
                return Admission(Admission.ACTION_BUSY, detail=action)
            if len(self._running_by_command) >= self.max_concurrent:
                return Admission(
                    Admission.AT_CAPACITY, detail=str(len(self._running_by_command))
                )
            self._running_by_command[cid] = action
            self._running_actions.add(action)
            return Admission(Admission.ACCEPTED)

    def complete(self, command_id: str, reply: dict) -> None:
        """Record the reply that was sent and free the slots."""
        with self._lock:
            self._release_locked(command_id)
            cid = str(command_id).strip()
            if not cid:
                return
            self._answered[cid] = reply
            self._answered.move_to_end(cid)
            while len(self._answered) > self.history_size:
                self._answered.popitem(last=False)

    def abandon(self, command_id: str) -> None:
        """Free the slots WITHOUT remembering an answer — for a command whose
        task was cancelled by a shutdown, where the server got no reply and a
        redelivery after reconnect should be allowed to run."""
        with self._lock:
            self._release_locked(command_id)

    def clear_in_flight(self) -> None:
        """Drop in-flight bookkeeping on socket loss. History survives on purpose."""
        with self._lock:
            self._running_by_command.clear()
            self._running_actions.clear()

    def clear_all(self) -> None:
        with self._lock:
            self._running_by_command.clear()
            self._running_actions.clear()
            self._answered.clear()

    def _release_locked(self, command_id: str) -> None:
        action = self._running_by_command.pop(str(command_id).strip(), None)
        if action is not None:
            self._running_actions.discard(action)


def monotonic_clock() -> Callable[[], float]:
    import time

    return time.monotonic
