package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A microphone that opens, reads happily and returns zeroes forever.
 *
 * That is what Android does to a while-in-use foreground service started from
 * the background, what a GrapheneOS per-app *Sensors* toggle does, and what a
 * hardware mute does. None of them raise an error, so the always-on listener
 * would otherwise show "Jarvis is listening" over a microphone nothing can
 * reach.
 *
 * The load-bearing decision is that the test is exactly zero rather than a
 * small threshold: this watch runs for hours in whatever room the phone is in,
 * and a quiet room's RMS really does sit near the 0.0005 that
 * `JarvisConversation` calls dead. A muted recorder is not quiet — it is
 * arithmetically zero.
 */
class MicSilenceWatchTest {

    /** Feed a level every 100 ms for [ms], returning how often it fired. */
    private fun feed(watch: MicSilenceWatch, from: Long, ms: Long, level: Float): Int {
        var fired = 0
        var t = from
        while (t < from + ms) {
            if (watch.onLevel(t, level)) fired++
            t += 100
        }
        return fired
    }

    @Test
    fun aMutedRecorderIsReportedOnce() {
        val watch = MicSilenceWatch()
        val fired = feed(watch, 0, 5 * MicSilenceWatch.MUTED_AFTER_MS, 0f)
        assertEquals("should fire exactly once, not on every frame", 1, fired)
        assertTrue(watch.muted)
    }

    @Test
    fun aSilentHouseIsNotAMutedMicrophone() {
        val watch = MicSilenceWatch()
        // A real recorder's noise floor. Small; never zero.
        assertEquals(0, feed(watch, 0, 10 * MicSilenceWatch.MUTED_AFTER_MS, 0.00002f))
        assertFalse(watch.muted)
    }

    @Test
    fun theLevelJarvisConversationCallsDeadIsStillNotZero() {
        val watch = MicSilenceWatch()
        assertEquals(0, feed(watch, 0, 10 * MicSilenceWatch.MUTED_AFTER_MS, 0.0005f))
        assertFalse(watch.muted)
    }

    @Test
    fun oneRealFrameResetsTheWholeRun() {
        val watch = MicSilenceWatch()
        feed(watch, 0, MicSilenceWatch.MUTED_AFTER_MS - 1_000, 0f)
        assertFalse(watch.onLevel(MicSilenceWatch.MUTED_AFTER_MS - 1_000, 0.2f))
        // Almost-a-full-window of silence again: still not enough on its own.
        assertEquals(
            0,
            feed(watch, MicSilenceWatch.MUTED_AFTER_MS, MicSilenceWatch.MUTED_AFTER_MS - 1_000, 0f),
        )
        assertFalse(watch.muted)
    }

    @Test
    fun silenceEndingOneFrameShortNeverFires() {
        val watch = MicSilenceWatch()
        assertEquals(0, feed(watch, 0, MicSilenceWatch.MUTED_AFTER_MS, 0f))
        assertFalse(watch.muted)
    }

    @Test
    fun aClockThatWentBackwardsReSeedsRatherThanFiring() {
        val watch = MicSilenceWatch()
        assertFalse(watch.onLevel(500_000, 0f))
        // A manual time change, or an emulator snapshot restore. "Silent since
        // 500 s ago" must not become "silent for the whole epoch".
        assertFalse(watch.onLevel(1_000, 0f))
        assertFalse(watch.onLevel(2_000, 0f))
        assertFalse(watch.muted)
    }

    @Test
    fun aNegativeLevelIsACallerBugNotSilence() {
        val watch = MicSilenceWatch()
        assertEquals(0, feed(watch, 0, 3 * MicSilenceWatch.MUTED_AFTER_MS, -1f))
        assertFalse(watch.muted)
    }

    @Test
    fun resettingLetsItFireAgainForTheNextRun() {
        val watch = MicSilenceWatch()
        assertEquals(1, feed(watch, 0, 2 * MicSilenceWatch.MUTED_AFTER_MS, 0f))
        watch.reset()
        assertFalse(watch.muted)
        assertEquals(
            1,
            feed(watch, 10_000_000, 2 * MicSilenceWatch.MUTED_AFTER_MS, 0f),
        )
    }
}
