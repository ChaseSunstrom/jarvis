package ai.jarvis.app.support

import android.app.UiAutomation
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.fail
import java.util.concurrent.TimeoutException

/**
 * Asserting that a toast appeared.
 *
 * ## Why not just look for the text on screen
 *
 * Because it does not reliably work. Espresso cannot see a toast at all — a
 * toast lives in its own window, outside the activity's view hierarchy, and
 * Espresso's root matcher is scoped to the activity. UiAutomator's `By.text`
 * scrapes the accessibility *window* list, and a `TYPE_TOAST` window is not
 * consistently present in it across platform versions. Either approach produces
 * a test that passes on one API level and mysteriously fails on the next.
 *
 * ## What this does instead
 *
 * The platform announces a toast as an accessibility event —
 * `TYPE_NOTIFICATION_STATE_CHANGED` with `className` of `android.widget.Toast`
 * and the toast's text in `event.text`. That is how TalkBack reads toasts aloud,
 * it is the same on every version this app supports, and
 * `UiAutomation.executeAndWaitForEvent` is built to catch exactly this: it
 * starts listening, runs the action, and returns the first matching event.
 *
 * ## Why this test exists at all
 *
 * `SettingsActivity`'s PHONE TASKS and AUDIT LOG buttons point at
 * `ai.jarvis.app.automation.ui.*` activities that
 * are declared in AndroidManifest.xml. They ARE implemented now — the test
 * branches on that and asserts the screen opens — but the manifest entry is
 * what makes their absence dangerous in a build that strips the module: as
 * `JarvisScreens` explains, that combination does NOT throw
 * `ActivityNotFoundException`. The intent resolves against the manifest entry
 * and the app dies later with "Unable to instantiate activity". The toast is
 * the evidence that `JarvisScreens.isPresent` caught it first, and asserting
 * the toast rather than merely "did not crash" is what tells the fixed
 * behaviour from a version where the crash happens a moment later.
 *
 * ## The one rule for the action you pass
 *
 * **It must be a single interaction that waits for nothing.** Click, type, press
 * — not `tap()` (which scrolls), not a `Waits.until`, not another
 * `executeAndWaitForEvent`.
 *
 * `UiAutomation` has exactly one event queue and one `mWaitingForEventDelivery`
 * flag. `executeAndWaitForEvent` claims both for the duration of the command it
 * runs, and on the way out clears the queue and lowers the flag — including when
 * it is the INNER of two nested calls. UiAutomator's scrolling is built on that
 * same method, so a scroll inside the action unsubscribes the toast wait
 * wrapping it. The click still lands, the app still toasts, and this class
 * reports "No toast was posted at all" — which reads as an app defect and is
 * not one.
 *
 * Run 31309094331 is the worked example: `NavigationTest` failed on Settings'
 * AUTOMATIONS button while passing on the home screen's, the only difference
 * being that the Settings one had to be scrolled to. Logcat from the same second
 * shows the app doing exactly the right thing —
 *
 *     W/JarvisScreens: …AutomationsActivity is not present in this build
 *     W/NotificationService: Toast already killed. pkg=ai.jarvis.app
 *
 * Find the control first, then pass only the click.
 * `android-app/tools/instrumentation_contract_test.py` fails the fast lane if an
 * action here ever grows a wait again.
 */
object Toasts {

    private const val TAG = "JarvisTestToasts"
    private const val TOAST_CLASS = "android.widget.Toast"

    /**
     * Run [action] and assert a toast whose text contains [expectedSubstring]
     * appears within [timeoutMs].
     */
    fun expect(
        expectedSubstring: String,
        timeoutMs: Long = DEFAULT_TIMEOUT_MS,
        action: () -> Unit,
    ) {
        val seen = mutableListOf<String>()
        val uiAutomation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val filter = UiAutomation.AccessibilityEventFilter { event ->
            val text = if (isToast(event)) textOf(event).also { seen.add(it) } else null
            text != null && text.contains(expectedSubstring, ignoreCase = true)
        }
        try {
            uiAutomation.executeAndWaitForEvent(
                { action() },
                filter,
                timeoutMs,
            )
        } catch (e: TimeoutException) {
            fail(
                "Expected a toast containing \"$expectedSubstring\" within ${timeoutMs}ms. " +
                    if (seen.isEmpty()) {
                        "No toast was posted at all — which for the unimplemented-screen " +
                            "buttons means JarvisScreens.isPresent() did not run, and the " +
                            "next thing to happen would have been a crash."
                    } else {
                        "Toasts seen instead: ${seen.joinToString("; ") { "\"$it\"" }}"
                    }
            )
        } catch (t: Throwable) {
            fail(
                "Waiting for a toast containing \"$expectedSubstring\" failed with " +
                    "${t.javaClass.simpleName}: ${t.message}"
            )
        }
    }

    /**
     * Run [action] and assert a toast matching ANY of [alternatives] appears.
     *
     * For a control whose correct answer depends on the device rather than on
     * the code: SettingsActivity's PASTE says "Clipboard is empty" or "Pasted N
     * characters" depending on what the emulator happens to have on its
     * clipboard, and both are right. The claim worth testing is that it always
     * says SOMETHING — a token that half-arrives with no feedback is the worst
     * outcome, because the user then debugs a connection instead of a paste.
     */
    fun expectAnyOf(
        vararg alternatives: String,
        timeoutMs: Long = DEFAULT_TIMEOUT_MS,
        action: () -> Unit,
    ) {
        val seen = mutableListOf<String>()
        val uiAutomation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val filter = UiAutomation.AccessibilityEventFilter { event ->
            val text = if (isToast(event)) textOf(event).also { seen.add(it) } else null
            text != null && alternatives.any { text.contains(it, ignoreCase = true) }
        }
        try {
            uiAutomation.executeAndWaitForEvent({ action() }, filter, timeoutMs)
        } catch (t: Throwable) {
            fail(
                "Expected a toast containing one of " +
                    alternatives.joinToString(", ") { "\"$it\"" } +
                    " within ${timeoutMs}ms. " +
                    if (seen.isEmpty()) "No toast was posted at all."
                    else "Toasts seen instead: ${seen.joinToString("; ") { "\"$it\"" }}"
            )
        }
    }

    /**
     * Run [action] and report whether a toast containing [expectedSubstring]
     * appeared, without failing either way.
     *
     * For the buttons that hand off to a system settings screen: on an emulator
     * image with no such screen the app toasts "This device has no screen for
     * that setting", and on one that has it the settings activity opens. Both
     * are correct, and the test's actual assertion is that neither crashed.
     */
    fun observe(
        expectedSubstring: String,
        timeoutMs: Long = SHORT_TIMEOUT_MS,
        action: () -> Unit,
    ): Boolean {
        val uiAutomation = InstrumentationRegistry.getInstrumentation().uiAutomation
        val filter = UiAutomation.AccessibilityEventFilter { event ->
            isToast(event) && textOf(event).contains(expectedSubstring, ignoreCase = true)
        }
        return try {
            uiAutomation.executeAndWaitForEvent({ action() }, filter, timeoutMs)
            true
        } catch (t: Throwable) {
            Log.i(TAG, "no toast containing \"$expectedSubstring\" (${t.javaClass.simpleName})")
            false
        }
    }

    private fun isToast(event: AccessibilityEvent): Boolean =
        event.eventType == AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED &&
            event.className?.toString() == TOAST_CLASS

    private fun textOf(event: AccessibilityEvent): String =
        event.text.joinToString(" ") { it?.toString().orEmpty() }

    const val DEFAULT_TIMEOUT_MS = 10_000L
    const val SHORT_TIMEOUT_MS = 4_000L
}
