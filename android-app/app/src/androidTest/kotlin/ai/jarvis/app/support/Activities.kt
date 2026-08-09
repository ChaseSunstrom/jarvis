package ai.jarvis.app.support

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import org.junit.Assert.fail

/**
 * Launching and observing activities, without `ActivityScenario`.
 *
 * `ActivityScenario` is the modern, pleasant API and it is the wrong tool for
 * this app. Its documentation states it does not support activities declared
 * with `launchMode="singleTask"` or `"singleInstance"`, and three of the four
 * screens this suite drives are exactly that: `MainActivity` and
 * `JarvisAssistActivity` are `singleTask`, and the assist surface is also
 * `noHistory` with its own `taskAffinity`. Those are not incidental — they are
 * what make the assist popup behave like an assistant instead of an app — so
 * the test has to fit the app rather than the other way round.
 *
 * `Instrumentation.ActivityMonitor` has no such restriction: it watches the
 * process for an activity of a given class, whatever task it lands in and
 * whoever started it. That last part matters for
 * `MainActivity → SettingsActivity`, where the thing under test is the button,
 * not the launch.
 */
object Activities {

    private val instrumentation: Instrumentation
        get() = InstrumentationRegistry.getInstrumentation()

    /** Launch [type] directly and return the instance once it exists. */
    fun <T : Activity> launch(
        type: Class<T>,
        timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS,
        configure: (Intent) -> Unit = {},
    ): T = expect(type, timeoutMs) {
        val context = instrumentation.targetContext
        val intent = Intent(context, type).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        configure(intent)
        context.startActivity(intent)
    }

    /** Launch whatever [intent] resolves to, and wait for [type] to appear. */
    fun <T : Activity> launchIntent(
        type: Class<T>,
        intent: Intent,
        timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS,
    ): T = expect(type, timeoutMs) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        instrumentation.targetContext.startActivity(intent)
    }

    /**
     * Run [trigger] and return the [type] instance it caused to start.
     *
     * The monitor is registered BEFORE the trigger runs, which is the whole
     * point: an activity that starts and finishes inside a few milliseconds —
     * a consent prompt answered by a countdown, an assist surface that closes
     * on inactivity — would be gone before any after-the-fact poll could see it.
     */
    fun <T : Activity> expect(
        type: Class<T>,
        timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS,
        trigger: () -> Unit,
    ): T {
        val monitor = instrumentation.addMonitor(type.name, null, false)
        try {
            trigger()
            val activity = monitor.waitForActivityWithTimeout(timeoutMs)
            if (activity == null) {
                fail(
                    "${type.simpleName} did not start within ${timeoutMs}ms.\n" +
                        "Foreground window dump:\n${Device.windowDump()}"
                )
            }
            @Suppress("UNCHECKED_CAST")
            return activity as T
        } finally {
            instrumentation.removeMonitor(monitor)
        }
    }

    /**
     * Assert [type] does NOT start while [window] passes, and return control.
     *
     * The proof shape for "the user was not asked a second time". Necessarily a
     * real wait: there is no event for the absence of an activity.
     */
    fun assertDoesNotStart(
        type: Class<out Activity>,
        window: Long,
        what: String,
        trigger: () -> Unit,
    ) {
        val monitor = instrumentation.addMonitor(type.name, null, false)
        try {
            trigger()
            val activity = monitor.waitForActivityWithTimeout(window)
            if (activity != null) {
                fail("$what — but ${type.simpleName} started anyway.")
            }
        } finally {
            instrumentation.removeMonitor(monitor)
        }
    }

    /** Activities currently in [stage]. Safe from any thread. */
    fun inStage(stage: Stage): List<Activity> {
        var result: List<Activity> = emptyList()
        instrumentation.runOnMainSync {
            result = ActivityLifecycleMonitorRegistry.getInstance()
                .getActivitiesInStage(stage)
                .toList()
        }
        return result
    }

    fun isResumed(activity: Activity): Boolean = inStage(Stage.RESUMED).contains(activity)

    fun awaitResumed(activity: Activity, timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        Waits.until("${activity.javaClass.simpleName} to be RESUMED", timeoutMs) {
            isResumed(activity)
        }
    }

    fun awaitFinished(activity: Activity, timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        Waits.until("${activity.javaClass.simpleName} to finish", timeoutMs) {
            activity.isFinishing || activity.isDestroyed || inStage(Stage.DESTROYED).contains(activity)
        }
    }

    /** Read a value off an activity on the main thread. */
    fun <T> onMain(block: () -> T): T {
        var result: T? = null
        var thrown: Throwable? = null
        instrumentation.runOnMainSync {
            try {
                result = block()
            } catch (t: Throwable) {
                thrown = t
            }
        }
        thrown?.let { throw it }
        @Suppress("UNCHECKED_CAST")
        return result as T
    }

    /**
     * Finish everything this process has on screen.
     *
     * Between tests rather than after each one: a leaked activity is a real
     * defect, but leaving one up would make the NEXT test fail instead, which
     * hides which test actually leaked it.
     */
    fun finishAll(timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        val open = (inStage(Stage.RESUMED) + inStage(Stage.PAUSED) + inStage(Stage.STOPPED))
            .distinct()
        if (open.isEmpty()) return
        instrumentation.runOnMainSync {
            open.forEach { if (!it.isFinishing) it.finish() }
        }
        Waits.until("every activity in this process to finish", timeoutMs) {
            (inStage(Stage.RESUMED) + inStage(Stage.PAUSED)).isEmpty()
        }
    }
}
