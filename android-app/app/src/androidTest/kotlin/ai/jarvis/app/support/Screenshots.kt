package ai.jarvis.app.support

import ai.jarvis.app.testing.TestHooks
import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import java.io.File

/**
 * PNG capture into the app's external files dir, so CI can `adb pull` the whole
 * directory as a build artefact and a human can look at what the app actually
 * rendered.
 *
 * ## Deliberately non-fatal
 *
 * A screenshot never fails a test. The point of these files is to explain a
 * failure, and a suite where the diagnostic tool can itself be the cause of a
 * red build is a suite people learn to ignore. When a capture fails, a
 * `<name>.FAILED.txt` is written in its place with the reason, so a missing
 * screenshot is visible in the artefact listing rather than silently absent.
 *
 * ## FLAG_SECURE
 *
 * `ApprovalActivity` and `CompanionAskActivity` both set `FLAG_SECURE`, which is
 * the correct behaviour — a Tier-3 prompt's parameters must not reach the screen
 * recorder. `UiAutomation.takeScreenshot`, which is what `UiDevice` uses,
 * captures secure layers because it runs with system privilege; if a platform
 * build ever declines to, those two screenshots come out black. That is a
 * cosmetic loss, and the assertions in those tests are made against the
 * accessibility tree, never against pixels.
 */
object Screenshots {

    private const val TAG = "JarvisScreenshots"

    /**
     * Capture the whole screen as `<name>.png`.
     *
     * @return the file, or null when the capture failed.
     */
    fun take(name: String): File? = capture(name, waitForIdle = true)

    /**
     * Capture without waiting for the screen to go idle.
     *
     * For the one case where idleness is the opposite of what is wanted: a
     * screenshot *during* an animation. `waitForIdle` would block until the
     * animation finished and hand back a picture of the end state, which is the
     * frame the other screenshot already shows.
     */
    fun takeImmediately(name: String): File? = capture(name, waitForIdle = false)

    private fun capture(name: String, waitForIdle: Boolean): File? {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val target = TestHooks.screenshotFile(context, name)
        return try {
            val device = UiDevice.getInstance(instrumentation)
            // Let layout and any pending draws settle first, or the artefact
            // shows a half-built screen and reads as a bug that is not there.
            // Short and bounded: UiAutomator's own idle wait, not a sleep.
            if (waitForIdle) device.waitForIdle(IDLE_TIMEOUT_MS)
            if (!device.takeScreenshot(target)) {
                markFailed(target, "UiDevice.takeScreenshot returned false")
                Log.w(TAG, "screenshot $name: takeScreenshot returned false")
                null
            } else {
                Log.i(TAG, "screenshot $name -> ${target.absolutePath} (${target.length()} bytes)")
                target
            }
        } catch (t: Throwable) {
            markFailed(target, "${t.javaClass.simpleName}: ${t.message}")
            Log.w(TAG, "screenshot $name failed", t)
            null
        }
    }

    /**
     * As [take], but with the settle delay callers sometimes need for a
     * transition that has no idle signal — a window animation the framework
     * does not report, a `ValueAnimator` mid-flight.
     *
     * This is the ONLY place in the suite where a sleep is acceptable, and only
     * because the thing being waited for is "the pixels look right", which
     * nothing exposes as a condition. It never gates an assertion.
     */
    fun takeAfterSettling(name: String, settleMs: Long = SETTLE_MS): File? {
        Thread.sleep(settleMs)
        return take(name)
    }

    /** Everything captured so far, for a test that wants to assert it produced artefacts. */
    fun captured(): List<File> {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        return TestHooks.screenshotDir(context)
            .listFiles { f -> f.name.endsWith(".png") }
            .orEmpty()
            .sortedBy { it.name }
    }

    private fun markFailed(target: File, reason: String) {
        runCatching {
            File(target.parentFile, target.nameWithoutExtension + ".FAILED.txt")
                .writeText("screenshot capture failed: $reason\n")
        }
    }

    private const val IDLE_TIMEOUT_MS = 3_000L
    private const val SETTLE_MS = 250L
}
