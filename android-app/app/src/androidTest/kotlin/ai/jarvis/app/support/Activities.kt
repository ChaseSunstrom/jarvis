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
 * with `launchMode="singleTask"` or `"singleInstance"`, and the screens this
 * suite drives are exactly that: `MainActivity` and `JarvisAssistActivity` are
 * both `singleTask`, and the assist surface is additionally `noHistory` with its
 * own `taskAffinity`. Those declarations are not incidental — they are what make
 * the assist popup behave like an assistant rather than an app — so the test has
 * to fit the app rather than the other way round.
 *
 * `Instrumentation.ActivityMonitor` has no such restriction: it watches the
 * process for an activity of a given class, whatever task it lands in and
 * whoever started it. That last part matters for `MainActivity →
 * SettingsActivity`, where the thing under test is the button, not the launch.
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

    /** Fire [intent] and wait for [type] to appear. */
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
     * The monitor is registered BEFORE the trigger runs, which is not optional:
     * a consent prompt is raised from the WebSocket reader thread the instant
     * the dispatcher reaches its ASK branch, and an activity that starts and
     * finishes quickly would be gone before any after-the-fact poll could see
     * it.
     */
    @Suppress("UNCHECKED_CAST")
    fun <T : Activity> expect(
        type: Class<T>,
        timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS,
        trigger: () -> Unit,
    ): T {
        val monitor = instrumentation.addMonitor(type.name, null, false)
        try {
            trigger()
            val activity = monitor.waitForActivityWithTimeout(timeoutMs)
                ?: run {
                    fail(
                        "${type.simpleName} did not start within ${timeoutMs}ms.\n" +
                            "Foreground window dump:\n${Device.windowDump()}"
                    )
                    error("unreachable")
                }
            return activity as T
        } finally {
            instrumentation.removeMonitor(monitor)
        }
    }

    /**
     * Assert [type] does NOT start while [window] passes.
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

    /** Activities currently in [stage]. Safe to call from any thread. */
    fun inStage(stage: Stage): List<Activity> {
        val collected = ArrayList<Activity>()
        instrumentation.runOnMainSync {
            collected.addAll(
                ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(stage)
            )
        }
        return collected
    }

    fun isResumed(activity: Activity): Boolean = inStage(Stage.RESUMED).contains(activity)

    fun awaitResumed(activity: Activity, timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        Waits.until("${activity.javaClass.simpleName} to be RESUMED", timeoutMs) {
            isResumed(activity)
        }
    }

    /**
     * Is an activity of this class the one on screen?
     *
     * Asked of the in-process lifecycle registry rather than of `dumpsys
     * activity`, and that is not a style choice. The instrumented suite runs
     * inside the app's own process, so the registry knows exactly which of OUR
     * activities is resumed — no output parsing, no ambiguity, and no false
     * positive from the system Settings app, several of whose screens are also
     * called something-`SettingsActivity`. When the user has left the app
     * entirely, none of ours is resumed, which is precisely the question the
     * navigation test asks after tapping a settings deep link.
     */
    fun isResumed(type: Class<out Activity>): Boolean =
        inStage(Stage.RESUMED).any { type.isInstance(it) }

    /** As [isResumed], for a class this suite must not reference by type. */
    fun isResumed(className: String): Boolean =
        inStage(Stage.RESUMED).any { it.javaClass.name == className }

    fun awaitResumed(type: Class<out Activity>, timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        Waits.until("${type.simpleName} to be the resumed activity", timeoutMs) {
            isResumed(type)
        }
    }

    /** True while any activity of this app is on screen. */
    fun anyResumed(): Boolean = inStage(Stage.RESUMED).isNotEmpty()

    fun awaitFinished(activity: Activity, timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        Waits.until("${activity.javaClass.simpleName} to finish", timeoutMs) {
            activity.isFinishing ||
                activity.isDestroyed ||
                inStage(Stage.DESTROYED).contains(activity)
        }
    }

    /**
     * Run [block] on the main thread and hand back what it returned.
     *
     * Reading a `View` from the instrumentation thread is a data race in
     * principle and a `CalledFromWrongThreadException` in practice, so every
     * assertion in this suite that touches the view tree goes through here.
     */
    @Suppress("UNCHECKED_CAST")
    fun <T> onMain(block: () -> T): T {
        val holder = arrayOfNulls<Any>(1)
        var thrown: Throwable? = null
        instrumentation.runOnMainSync {
            try {
                holder[0] = block()
            } catch (t: Throwable) {
                thrown = t
            }
        }
        thrown?.let { throw it }
        return holder[0] as T
    }

    /**
     * Finish everything this process has on screen.
     *
     * Called between tests rather than inside them: a leaked activity is a real
     * defect, and leaving one up would make the NEXT test fail instead, hiding
     * which test actually leaked it.
     */
    fun finishAll(timeoutMs: Long = Waits.DEFAULT_TIMEOUT_MS) {
        val open = OPEN_STAGES.flatMap { inStage(it) }.distinct()
        if (open.isEmpty()) return
        instrumentation.runOnMainSync {
            open.forEach { if (!it.isFinishing) it.finish() }
        }
        Waits.until("every activity in this process to finish", timeoutMs) {
            OPEN_STAGES.none { stage -> inStage(stage).isNotEmpty() }
        }
    }

    /**
     * Every stage an activity can sit in while still being on the back stack.
     * PRE_ON_CREATE and DESTROYED are deliberately absent: one is too early to
     * finish, the other is already gone.
     */
    private val OPEN_STAGES = listOf(
        Stage.CREATED,
        Stage.STARTED,
        Stage.RESUMED,
        Stage.PAUSED,
        Stage.STOPPED,
    )
}
