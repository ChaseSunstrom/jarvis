package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Toasts
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.ui.CrashLogActivity
import ai.jarvis.app.ui.JarvisScreens
import ai.jarvis.app.ui.SystemCheckActivity
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiObject2
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Every button on the home screen and on Settings does something, and nothing
 * takes the app down.
 *
 * ## The two buttons that matter most
 *
 * `AndroidManifest.xml` declares `ai.jarvis.app.automation.ui.AutomationsActivity`
 * and `…AuditLogActivity` so the automation module never has to edit it, and
 * neither class exists in this build. That combination is a trap, and
 * `JarvisScreens` documents it precisely: because the components ARE declared,
 * `startActivity` does not throw `ActivityNotFoundException` — the intent
 * resolves against the manifest entry, and the app dies a moment later on the
 * main thread with "Unable to instantiate activity". A `try/catch` around
 * `startActivity` looks like it handles this and does not.
 *
 * `JarvisScreens.isPresent` is the actual fix: resolve the class first, toast if
 * it is missing. So the test asserts the TOAST, not merely the absence of a
 * crash — "nothing crashed" would also pass on a build where the crash simply
 * happens one frame later, and the toast is the only positive evidence that the
 * guard ran.
 *
 * ## How "nothing crashes" is checked
 *
 * Twice over. The instrumented suite runs inside the app's own process, so a
 * crash on the main thread takes the test run with it. And `JarvisTestRule`
 * asserts after every test that `JarvisCrashHandler` recorded nothing, which
 * catches a crash on a background thread that would otherwise pass silently.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class NavigationTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    // --- MainActivity -------------------------------------------------------

    @Test
    fun homeManageButtonExplainsItselfWhenNothingIsConfigured() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        tap("MANAGE")

        // No server configured, so MANAGE must say so rather than opening a
        // WebView onto nowhere.
        Waits.until("the home screen to explain that MANAGE needs a server first") {
            Device.ui.findObject(
                By.text(Views.containingIgnoringCase("Set the server URL and token in Settings"))
            ) != null
        }
        assertTrue(
            "MainActivity must still be the foreground activity",
            Activities.isResumed(MainActivity::class.java),
        )

        Screenshots.take("NavigationTest-home-manage-unconfigured")
    }

    @Test
    fun homeSettingsButtonOpensSettings() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        val settings = Activities.expect(SettingsActivity::class.java) { tap("SETTINGS") }
        Activities.awaitResumed(settings)

        Waits.until("the settings screen to render its server URL field") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("Server URL"))) != null
        }

        Screenshots.take("NavigationTest-settings")
    }

    @Test
    fun homeTalkButtonSendsAnUnconfiguredUserToSettings() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        // With no server configured, tapping the orb control must not start a
        // conversation with nobody — it must take the user somewhere useful.
        val settings = Activities.expect(SettingsActivity::class.java) { tap("TAP TO SPEAK") }
        Activities.awaitResumed(settings)

        Screenshots.take("NavigationTest-talk-unconfigured")
    }

    @Test
    fun homeAutomationsButtonToastsRatherThanCrashing() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        assertUnimplementedScreenIsHandled(
            label = "AUTOMATIONS",
            className = JarvisScreens.AUTOMATIONS,
            screenshot = "NavigationTest-home-automations",
        )

        assertTrue(
            "MainActivity must survive a tap on a screen this build does not have",
            Activities.isResumed(MainActivity::class.java),
        )
    }

    // --- SettingsActivity ---------------------------------------------------

    @Test
    fun settingsAutomationsAndAuditLogButtonsToastRatherThanCrashing() {
        openSettings()

        assertUnimplementedScreenIsHandled(
            label = "AUTOMATIONS",
            className = JarvisScreens.AUTOMATIONS,
            screenshot = "NavigationTest-settings-automations",
        )
        assertUnimplementedScreenIsHandled(
            label = "AUDIT LOG",
            className = JarvisScreens.AUDIT_LOG,
            screenshot = "NavigationTest-settings-audit-log",
        )

        assertTrue(
            "SettingsActivity must survive taps on screens this build does not have",
            Activities.isResumed(SettingsActivity::class.java),
        )
    }

    @Test
    fun settingsOpensTheDiagnosticScreensItOwns() {
        openSettings()

        val systemCheck = Activities.expect(SystemCheckActivity::class.java) {
            tap("SYSTEM CHECK")
        }
        Activities.awaitResumed(systemCheck)
        Screenshots.take("NavigationTest-system-check")
        Device.ui.pressBack()
        Activities.awaitResumed(SettingsActivity::class.java)

        val crashLog = Activities.expect(CrashLogActivity::class.java) {
            tap("CRASH LOGS")
        }
        Activities.awaitResumed(crashLog)
        Screenshots.take("NavigationTest-crash-logs")
        Device.ui.pressBack()
        Activities.awaitResumed(SettingsActivity::class.java)
    }

    @Test
    fun settingsSystemAccessButtonsNeverCrash() {
        openSettings()

        // Each of these hands off to a system settings screen. On an emulator
        // image that has the screen, it opens; on one that does not,
        // SettingsActivity catches ActivityNotFoundException and toasts. Both
        // are correct, and neither may crash — which is what the crash-log
        // assertion in JarvisTestRule checks after this returns.
        for (label in SYSTEM_ACCESS_BUTTONS) {
            tapAndComeBack(label)
        }

        Screenshots.take("NavigationTest-settings-system-access")
    }

    @Test
    fun settingsTokenPasteButtonReportsWhatItDid() {
        openSettings()

        // Whether the emulator's clipboard happens to be empty is not this
        // suite's business, and both answers are correct. What must always
        // happen is that the button says which one it was: a token that
        // half-arrives with no feedback sends the user off debugging a
        // connection instead of a paste.
        Toasts.expectAnyOf("Clipboard is empty", "Pasted") { tap("PASTE") }

        Screenshots.take("NavigationTest-settings-paste")
    }

    // --- helpers ------------------------------------------------------------

    private fun openSettings(): SettingsActivity {
        val settings = Activities.launch(SettingsActivity::class.java)
        Activities.awaitResumed(settings)
        // Anchored on a label near the TOP of the screen. "SAVE" sits at the
        // bottom of a long ScrollView and would need scrolling to find, which
        // would leave the screen scrolled before the test had done anything.
        Waits.until("the settings screen to finish building") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("Server URL"))) != null
        }
        return settings
    }

    /**
     * Tap a button by its label, scrolling it into view first if need be.
     *
     * UiAutomator rather than Espresso because several of these live inside a
     * `ScrollView` in a hierarchy with no resource ids, and because the same
     * helper then works across an activity boundary.
     */
    private fun tap(label: String) {
        val button = findButton(label)
        assertNotNull(
            "No button labelled \"$label\" on screen.\n${Device.windowDump()}",
            button,
        )
        button!!.click()
    }

    /** The settings screen is a long ScrollView; most of its buttons start off it. */
    private fun findButton(label: String): UiObject2? =
        Views.findScrolling(By.text(Views.textIgnoringCase(label)))

    /**
     * Assert that a button pointing at a screen this build does not ship toasts
     * instead of crashing.
     *
     * Branching on whether the class exists rather than hard-coding "it does
     * not": the automation module is expected to land eventually, and a test
     * that then fails with "expected a toast" would be reporting its own
     * staleness as a product defect. Either way the assertion is real — the
     * screen opens, or the user is told it is not there.
     */
    private fun assertUnimplementedScreenIsHandled(
        label: String,
        className: String,
        screenshot: String,
    ) {
        if (classExists(className)) {
            // The automation module landed. Assert the screen actually opens.
            tap(label)
            Waits.until("$className to reach the foreground") {
                Activities.isResumed(className)
            }
            Screenshots.take("$screenshot-implemented")
            Device.ui.pressBack()
            return
        }
        Toasts.expect("not available in this build") { tap(label) }
        Screenshots.take(screenshot)
    }

    private fun classExists(className: String): Boolean = try {
        Class.forName(className)
        true
    } catch (t: Throwable) {
        false
    }

    /**
     * Tap something that may leave the app, then get back to Settings however
     * far it went.
     *
     * Bounded and explicit rather than a blind `pressBack()`: a settings deep
     * link can land in a nested screen of the Settings app, and a test that
     * assumed one Back would silently start driving whatever screen it happened
     * to be looking at.
     */
    private fun tapAndComeBack(label: String) {
        val device = Device.ui
        tap(label)
        device.waitForIdle(IDLE_MS)
        Screenshots.take("NavigationTest-settings-${label.lowercase().replace(' ', '-')}")

        repeat(MAX_BACK_PRESSES) {
            if (Activities.isResumed(SettingsActivity::class.java)) return
            device.pressBack()
            device.waitForIdle(IDLE_MS)
        }
        if (!Activities.isResumed(SettingsActivity::class.java)) {
            // Whatever it opened would not let us out. Relaunching keeps the
            // remaining buttons testable and still proves this one did not crash
            // — the crash-log assertion in JarvisTestRule is what covers that.
            openSettings()
        }
    }

    private companion object {
        val SYSTEM_ACCESS_BUTTONS = listOf(
            "ASSISTANT",
            "ACCESSIBILITY",
            "NOTIFICATIONS",
            "OVERLAY",
            "BATTERY",
            "APP INFO",
        )

        const val MAX_BACK_PRESSES = 4
        const val IDLE_MS = 3_000L
    }
}
