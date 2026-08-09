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
 * `MainActivity`'s AUTOMATIONS button and `SettingsActivity`'s AUTOMATIONS and
 * AUDIT LOG buttons all point at `ai.jarvis.app.automation.ui.*` activities that
 * are declared in AndroidManifest.xml but are not implemented in this build.
 * As `JarvisScreens` explains, that combination does NOT throw
 * `ActivityNotFoundException` — the intent resolves against the manifest entry
 * and the app dies later with "Unable to instantiate activity". The toast is the
 * evidence that `JarvisScreens.isPresent` caught it first. Asserting the toast
 * rather than merely "did not crash" is what makes the test able to tell the
 * fixed behaviour from a version where the crash simply happens a moment later.
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
            if (!isToast(event)) return@AccessibilityEventFilter false
            val text = textOf(event)
            seen.add(text)
            text.contains(expectedSubstring, ignoreCase = true)
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
