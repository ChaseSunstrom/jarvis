package ai.jarvis.app.channel

/**
 * PURE LOGIC — no Android imports, no clock, no randomness of its own.
 *
 * Reconnect delay for the command channel: exponential, capped, jittered.
 *
 * Jitter is not decoration. A phone that reconnects on a fixed schedule after a
 * server restart joins every other client in a thundering herd, and — more to
 * the point here — a fixed reconnect cadence is a beacon: anyone watching the
 * WireGuard link sees "this device is a Jarvis phone" from the timing alone.
 *
 * The randomness is injected as a `0.0 <= r < 1.0` argument so the schedule is
 * a pure function and the Python mirror can assert on exact numbers.
 */
class Backoff(
    /** Floor. Never reconnect faster than this. */
    val baseMs: Long = DEFAULT_BASE_MS,
    /** Ceiling. A server that has been down for an hour is polled every 5 min. */
    val maxMs: Long = DEFAULT_MAX_MS,
    val factor: Double = 2.0
) {

    init {
        require(baseMs > 0) { "baseMs must be positive" }
        require(maxMs >= baseMs) { "maxMs must be >= baseMs" }
        require(factor > 1.0) { "factor must be > 1" }
    }

    /** Consecutive failed attempts. Reset by [reset] on a successful register. */
    var attempt: Int = 0
        private set

    /**
     * The window an attempt draws from, before jitter: `base * factor^attempt`,
     * clamped to [maxMs]. Exposed so a log line can say "retrying within 32 s"
     * without re-deriving it.
     */
    fun ceilingFor(attempt: Int): Long {
        if (attempt <= 0) return baseMs
        var value = baseMs.toDouble()
        repeat(attempt) {
            value *= factor
            if (value >= maxMs) return maxMs
        }
        return value.toLong().coerceIn(baseMs, maxMs)
    }

    /**
     * Delay for a given attempt: uniform in `[baseMs, ceilingFor(attempt)]`.
     *
     * "Full jitter with a floor" rather than textbook full jitter (`[0, cap]`),
     * because a zero-length delay against a server that is refusing the
     * handshake is just a hot loop with extra steps.
     */
    fun delayFor(attempt: Int, random: Double): Long {
        val r = random.coerceIn(0.0, 0.999_999)
        val ceiling = ceilingFor(attempt)
        if (ceiling <= baseMs) return baseMs
        return baseMs + ((ceiling - baseMs) * r).toLong()
    }

    /** Advance one step and return the delay to sleep. */
    fun next(random: Double): Long {
        val delay = delayFor(attempt, random)
        if (attempt < MAX_ATTEMPT) attempt++
        return delay
    }

    /** Back to the floor. Call this only after a *successful registration*. */
    fun reset() {
        attempt = 0
    }

    /**
     * Jump straight to a long delay without walking there.
     *
     * Used for failures that will not fix themselves in a second: a rejected
     * token, a server URL the transport policy refuses. Retrying those quickly
     * accomplishes nothing except burning battery and, for a bad token,
     * hammering the server's auth path.
     */
    fun penalise(minimumAttempt: Int = PENALTY_ATTEMPT) {
        if (attempt < minimumAttempt) attempt = minimumAttempt.coerceAtMost(MAX_ATTEMPT)
    }

    companion object {
        const val DEFAULT_BASE_MS = 1_000L      // 1 s
        const val DEFAULT_MAX_MS = 300_000L     // 5 min

        /** Enough steps to reach the ceiling from 1 s at factor 2; more is pointless. */
        const val MAX_ATTEMPT = 12

        /** ~64 s floor, i.e. what a rejected token gets. */
        const val PENALTY_ATTEMPT = 6
    }
}
