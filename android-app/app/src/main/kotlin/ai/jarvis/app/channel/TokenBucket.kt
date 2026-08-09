package ai.jarvis.app.channel

/**
 * PURE LOGIC — no Android imports, no clock of its own. Mirrored by
 * `android-app/tools/channel_protocol_test.py`.
 *
 * Inbound rate limit for `device_command` frames. A server that has been
 * prompt-injected — or a server someone else now controls — can send commands
 * as fast as the socket allows. Policy still gates every one of them, but a
 * flood of Tier-3 requests is a denial-of-service against the *user*: a wall of
 * consent prompts is a wall nobody reads, and a wall nobody reads is a wall
 * somebody taps through.
 *
 * So the phone bounds the arrival rate before policy is ever consulted.
 *
 * The clock is a parameter rather than a field. That keeps the class free of
 * `SystemClock`/`System.currentTimeMillis`, makes every test deterministic, and
 * makes the Python mirror a line-for-line copy.
 *
 * Monotonic time only. Pass `SystemClock.elapsedRealtime()`; wall-clock jumps
 * (NTP, timezone, the user changing the date) would otherwise hand out a free
 * refill. Time going backwards is treated as zero elapsed rather than as a
 * negative refill.
 */
class TokenBucket(
    /** Burst size: how many commands may arrive back to back. */
    val capacity: Double,
    /** Steady-state rate once the burst is spent. */
    val refillPerSecond: Double,
    startMs: Long = 0L,
    initialTokens: Double = capacity
) {

    init {
        require(capacity > 0) { "capacity must be positive" }
        require(refillPerSecond > 0) { "refillPerSecond must be positive" }
    }

    private var tokens: Double = initialTokens.coerceIn(0.0, capacity)
    private var lastMs: Long = startMs

    /** Tokens available at [nowMs], without consuming any. */
    @Synchronized
    fun peek(nowMs: Long): Double {
        refill(nowMs)
        return tokens
    }

    /**
     * Try to spend [cost] tokens. Returns true when the command may proceed.
     * False means: do not dispatch it, log a warning, and answer the server
     * with an error — never silently swallow it, or the server hangs forever
     * waiting for a `device_result`.
     */
    @Synchronized
    fun tryAcquire(nowMs: Long, cost: Double = 1.0): Boolean {
        refill(nowMs)
        if (tokens < cost) return false
        tokens -= cost
        return true
    }

    /** Milliseconds until [cost] tokens exist. 0 when they already do. */
    @Synchronized
    fun waitMs(nowMs: Long, cost: Double = 1.0): Long {
        refill(nowMs)
        if (tokens >= cost) return 0L
        val deficit = cost - tokens
        return Math.ceil(deficit / refillPerSecond * 1000.0).toLong()
    }

    /** Refill to full. Used when a fresh socket comes up, never by the server. */
    @Synchronized
    fun reset(nowMs: Long) {
        tokens = capacity
        lastMs = nowMs
    }

    private fun refill(nowMs: Long) {
        // Backwards clock => no elapsed time, and re-anchor so the next call
        // measures from here instead of accumulating a huge positive delta.
        val elapsed = nowMs - lastMs
        lastMs = nowMs
        if (elapsed <= 0) return
        tokens = (tokens + elapsed / 1000.0 * refillPerSecond).coerceAtMost(capacity)
    }

    companion object {
        /**
         * Defaults for the command channel: ten commands back to back, then one
         * per second sustained.
         *
         * Sized against what a real turn looks like. "Turn the lights off and
         * set an alarm" is two or three commands; a task the server drives
         * step-by-step might be eight. Sixty commands a minute is far more than
         * a human conversation produces and far less than a loop produces.
         */
        const val DEFAULT_CAPACITY = 10.0
        const val DEFAULT_REFILL_PER_SECOND = 1.0

        fun forCommands(startMs: Long): TokenBucket =
            TokenBucket(DEFAULT_CAPACITY, DEFAULT_REFILL_PER_SECOND, startMs)

        /**
         * Outbound `device_event` limiter. Triggers can chatter — a flapping
         * Bluetooth link produces connect/disconnect pairs for as long as the
         * radio is unhappy — and there is no reason to relay that at full rate.
         */
        fun forEvents(startMs: Long): TokenBucket = TokenBucket(20.0, 2.0, startMs)
    }
}
