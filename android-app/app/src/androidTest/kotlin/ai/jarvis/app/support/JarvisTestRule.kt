package ai.jarvis.app.support

import ai.jarvis.app.crash.JarvisCrashHandler
import ai.jarvis.app.testing.TestHooks
import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.fail
import org.junit.rules.TestWatcher
import org.junit.runner.Description
import java.io.File

/**
 * The setup every instrumented test in this suite needs, and the diagnostics
 * every failure in CI needs.
 *
 * ## Before each test
 *
 * Screen on, keyguard gone, runtime permissions granted, and the app back to a
 * first-install state — no server, no token, no per-action policy, no audit
 * history, no companion ledger, no synthetic microphone, no channel. Tests share
 * one process and one data directory, so without that reset the order they
 * happen to run in becomes part of their meaning.
 *
 * Note the direction of the reset: every part of it makes the device MORE
 * cautious. An unconfigured phone talks to nobody and a cleared policy store
 * asks about everything, so nothing here can make a later test pass by relaxing
 * a gate.
 *
 * ## After a failure
 *
 * A screenshot and the window hierarchy, both into the screenshot directory that
 * CI collects. A failed instrumented test in CI gives you a stack trace and
 * nothing else; a picture of the screen at the moment it went wrong, plus the
 * accessibility tree that Espresso and UiAutomator were matching against, is the
 * difference between "diagnose it from the artefacts" and "reproduce it
 * locally and hope".
 */
class JarvisTestRule(
    /** Set false for a test that arranges its own permissions. */
    private val grantPermissions: Boolean = true,
    /** Set false to keep state written by a previous test in the same class. */
    private val resetState: Boolean = true,
) : TestWatcher() {

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    override fun starting(description: Description) {
        Log.i(TAG, "=== ${description.className}#${description.methodName} ===")
        Device.wakeAndUnlock()
        if (grantPermissions) Device.grantStandardTestPermissions()
        if (resetState) TestHooks.resetState(context)
        // So that the crash check in `succeeded` can only see crashes THIS test
        // caused. `TestHooks.resetState` clears the audit log; the crash log is
        // a different file and is deliberately not part of a state reset.
        runCatching { JarvisCrashHandler.clear(context) }
    }

    /**
     * A crash on ANY thread fails the test, not just one that killed the run.
     *
     * The instrumented suite runs in the app's own process, so a crash on the
     * main thread takes the whole run down and is impossible to miss. A crash on
     * a background thread — the WebSocket reader, the mic worker, a coroutine
     * dispatcher — does not, and would otherwise show up as some unrelated
     * assertion timing out several tests later.
     *
     * `JarvisCrashHandler` records every uncaught throwable to
     * `filesDir/jarvis/crashes.jsonl` before delegating, so the log is the one
     * place that knows about all of them. This is the "and nothing crashed" half
     * of NavigationTest, applied to every test in the suite for free.
     */
    override fun succeeded(description: Description) {
        val crashes = runCatching { JarvisCrashHandler.recent(context) }.getOrDefault(emptyList())
        if (crashes.isEmpty()) return
        Screenshots.take("${simpleName(description)}-CRASHED")
        fail(
            "${description.methodName} passed its assertions but the app recorded " +
                "${crashes.size} uncaught exception(s):\n" +
                crashes.joinToString("\n") { "  [${it.thread}] ${it.headline()}" }
        )
    }

    override fun failed(e: Throwable, description: Description) {
        val name = "${simpleName(description)}-FAILURE"
        Log.w(TAG, "test failed: ${description.methodName}", e)
        Screenshots.take(name)
        dumpWindows(name)
    }

    override fun finished(description: Description) {
        // Whatever happened, do not leave a socket, a fake microphone or an
        // activity running into the next test.
        runCatching { TestHooks.stopChannel(context) }
        runCatching { TestHooks.clearSyntheticSpeech() }
        runCatching { Activities.finishAll() }
        runCatching { Device.ui.pressHome() }
    }

    /** `ClassName-methodName`, safe for a file name. */
    fun simpleName(description: Description): String {
        val cls = description.className.substringAfterLast('.')
        return "$cls-${description.methodName ?: "unknown"}"
    }

    private fun dumpWindows(name: String) {
        runCatching {
            val target = File(TestHooks.screenshotDir(context), "$name.windows.txt")
            target.writeText(Device.windowDump())
            Log.i(TAG, "window hierarchy -> ${target.absolutePath}")
        }
    }

    private companion object {
        const val TAG = "JarvisTestRule"
    }
}
