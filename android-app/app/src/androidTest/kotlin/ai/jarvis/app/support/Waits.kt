package ai.jarvis.app.support

import android.os.SystemClock
import org.junit.Assert.fail

/**
 * Polling waits, and the reason almost nothing in this suite sleeps for a fixed
 * duration.
 *
 * A sleep encodes a guess about how long something takes on the machine it was
 * written on. On a CI emulator — cold JIT, shared CPU, no hardware
 * acceleration — that guess is wrong in both directions: too short and the test
 * flakes, too long and a suite of thirty of them takes a quarter of an hour. A
 * wait-for-condition is correct at any speed and returns the instant the
 * condition holds.
 *
 * There are exactly two fixed delays in the whole suite, and both are honest
 * about what they are:
 *
 *  * `Screenshots.takeAfterSettling` waits for "the pixels look right", which
 *    nothing exposes as a condition. It never gates an assertion.
 *  * `BootAnimationTest` sleeps to reach a moment *inside* a 1400ms animation.
 *    "Still running 300ms in" is a claim about elapsed time, so elapsed time is
 *    the right tool — and a `ValueAnimator`'s clock is wall-clock, so the
 *    assertion cannot go the other way however slow the machine is.
 *
 * The failure messages here are deliberately long. When one of these fires in
 * CI, the message and an artefact screenshot are all anybody has.
 */
object Waits {

    /** Generous by design: a cold emulator can take seconds to do anything. */
    const val DEFAULT_TIMEOUT_MS = 15_000L

    /** Network round trips through a real socket and a real server. */
    const val NETWORK_TIMEOUT_MS = 45_000L

    /** A full voice turn: connect, authenticate, stream audio, STT, LLM, TTS. */
    const val CONVERSATION_TIMEOUT_MS = 90_000L

    private const val POLL_MS = 50L

    /**
     * Block until [condition] is true, or fail with [what] as the description.
     *
     * Must not be called from the main thread — instrumentation test methods
     * run on the instrumentation thread, which is exactly where this belongs.
     */
    fun until(
        what: String,
        timeoutMs: Long = DEFAULT_TIMEOUT_MS,
        pollMs: Long = POLL_MS,
        condition: () -> Boolean,
    ) {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        var lastError: Throwable? = null
        while (SystemClock.elapsedRealtime() < deadline) {
            val satisfied = try {
                lastError = null
                condition()
            } catch (t: Throwable) {
                // A condition that throws while the UI is mid-transition is
                // normal; a condition that throws for the whole timeout is the
                // real failure, so the last throwable is reported with it.
                lastError = t
                false
            }
            if (satisfied) return
            Thread.sleep(pollMs)
        }
        val suffix = lastError?.let { "; last error: ${it.javaClass.simpleName}: ${it.message}" } ?: ""
        fail("Timed out after ${timeoutMs}ms waiting for: $what$suffix")
    }

    /**
     * Block until [supplier] returns non-null, and return it.
     *
     * The shape most assertions actually want: "wait for the frame the server
     * sent back, then assert about it", rather than waiting for a boolean and
     * then re-fetching a value that may have changed in between.
     */
    fun <T : Any> untilPresent(
        what: String,
        timeoutMs: Long = DEFAULT_TIMEOUT_MS,
        pollMs: Long = POLL_MS,
        supplier: () -> T?,
    ): T {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        var lastError: Throwable? = null
        while (SystemClock.elapsedRealtime() < deadline) {
            val value = try {
                lastError = null
                supplier()
            } catch (t: Throwable) {
                lastError = t
                null
            }
            if (value != null) return value
            Thread.sleep(pollMs)
        }
        val suffix = lastError?.let { "; last error: ${it.javaClass.simpleName}: ${it.message}" } ?: ""
        fail("Timed out after ${timeoutMs}ms waiting for: $what$suffix")
        error("unreachable")
    }

    /**
     * Assert [condition] stays false for [forMs].
     *
     * The proof shape for "nothing happened": a second consent prompt did not
     * appear, a denied action did not run anyway. Necessarily a real wait —
     * there is no event to poll for the absence of — so callers keep [forMs]
     * short and use it only where absence is the assertion.
     */
    fun neverBecomesTrue(
        what: String,
        forMs: Long,
        pollMs: Long = POLL_MS,
        condition: () -> Boolean,
    ) {
        val deadline = SystemClock.elapsedRealtime() + forMs
        while (SystemClock.elapsedRealtime() < deadline) {
            if (condition()) fail("$what happened, and it must not have")
            Thread.sleep(pollMs)
        }
    }
}
