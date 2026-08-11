package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Harness
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Toasts
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import androidx.test.platform.app.InstrumentationRegistry
import ai.jarvis.app.ui.CrashLogActivity
import ai.jarvis.app.ui.ConsoleTab
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
    fun manageTakesAnUnconfiguredUserSomewhereTheyCanActuallyFixIt() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        // With no server configured, MANAGE must not open a WebView onto
        // nothing — and must not lecture either. It used to print "set the
        // server URL and token under PHONE", which became a loop the moment
        // PHONE moved into the console frame's tab strip: the strip is behind
        // MANAGE, so that sentence told somebody who had just tapped MANAGE to
        // go and find something only MANAGE could reach.
        val settings = Activities.expect(SettingsActivity::class.java) { tap("MANAGE") }
        Activities.awaitResumed(settings)

        Waits.until("the settings screen to render its server URL field") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("Server URL"))) != null
        }

        Screenshots.take("NavigationTest-home-console-unconfigured")
    }

    @Test
    fun homePhoneButtonOpensThePhonesOwnSettings() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        // PHONE, not SETTINGS. SETTINGS is one of the console's tabs and is the
        // HOUSE's settings; this one is the mobile half — permissions, the wake
        // word, which server this device talks to.
        //
        // Configured first, or MANAGE would short-circuit to these settings on
        // its own (see manageTakesAnUnconfiguredUserSomewhereTheyCanActuallyFixIt)
        // and this would pass without the tab strip existing at all.
        TestHooks.configure(
            InstrumentationRegistry.getInstrumentation().targetContext,
            serverUrl = Harness.baseUrl,
            token = Harness.token,
        )
        tap("MANAGE")
        val settings = Activities.expect(SettingsActivity::class.java) {
            tap(ConsoleTab.PHONE_LABEL)
        }
        Activities.awaitResumed(settings)

        Waits.until("the settings screen to render its server URL field") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("Server URL"))) != null
        }

        Screenshots.take("NavigationTest-phone-settings")
    }

    @Test
    fun everyConsoleTabIsReachableFromManage() {
        // Configured, because MANAGE sends an unconfigured user straight to the
        // phone's own settings instead of opening the frame.
        TestHooks.configure(
            InstrumentationRegistry.getInstrumentation().targetContext,
            serverUrl = Harness.baseUrl,
            token = Harness.token,
        )
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        // The property the user asked for in words: the phone is not a
        // different screen from the web view. Every section the console has is
        // offered by the console's own label, so a page added to one and not
        // the other is a failure here rather than a surprise on somebody's
        // phone.
        //
        // Behind MANAGE rather than on the home screen, which is the change:
        // the strip that offers them is the one the console frame draws, not a
        // second copy the home screen kept in step by hand.
        tap("MANAGE")
        for (tab in ConsoleTab.entries) {
            Waits.until("the console frame to offer its ${tab.label} tab") {
                Device.ui.findObject(By.text(Views.textIgnoringCase(tab.label))) != null
            }
        }
        // And the mobile half, in the same strip, which is the other half of
        // deduplicating the two.
        Waits.until("the console frame to offer ${ConsoleTab.PHONE_LABEL}") {
            Device.ui.findObject(
                By.text(Views.textIgnoringCase(ConsoleTab.PHONE_LABEL))
            ) != null
        }

        Screenshots.take("NavigationTest-console-tabs")
    }

    @Test
    fun homeMuteButtonSendsAnUnconfiguredUserToSettings() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        // With no server configured the pill says so, and tapping it must take
        // the user somewhere useful rather than toggling a mute on a
        // microphone that has nowhere to send anything.
        val settings = Activities.expect(SettingsActivity::class.java) { tap("SET UP JARVIS") }
        Activities.awaitResumed(settings)

        Screenshots.take("NavigationTest-mute-unconfigured")
    }

    // --- SettingsActivity ---------------------------------------------------

    @Test
    fun settingsPhoneTasksAndAuditLogButtonsToastRatherThanCrashing() {
        openSettings()

        assertUnimplementedScreenIsHandled(
            label = "PHONE TASKS",
            className = JarvisScreens.AUTOMATIONS,
            screenshot = "NavigationTest-settings-phone-tasks",
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
        //
        // Scrolled into view BEFORE the toast window opens — see `locate`.
        val paste = locate("PASTE")
        Toasts.expectAnyOf("Clipboard is empty", "Pasted") { paste.click() }

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
        locate(label).click()
    }

    /**
     * Find a button by its label, scrolling it into view, WITHOUT clicking it.
     *
     * Split out from [tap] because of a trap that costs an emulator run to
     * diagnose. `UiAutomation` has exactly one event queue, and
     * `executeAndWaitForEvent` — which both `Toasts` and UiAutomator's own
     * scrolling are built on — takes it over for the duration of the command it
     * is given, then on the way out sets `mWaitingForEventDelivery = false` and
     * clears the queue. So a scroll *inside* the action passed to
     * [Toasts.expect] silently unsubscribes the toast wait that is wrapping it:
     * the click lands, the app toasts, and the outer filter is no longer
     * listening. It then fails with "No toast was posted at all", which points
     * at the app rather than at the harness.
     *
     * That is what run 31309094331 hit. The app was entirely correct — logcat
     * from the same second shows the guard running and the toast being shown:
     *
     *     W/JarvisScreens: ai.jarvis.app.automation.ui.AutomationsActivity is
     *                      not present in this build
     *     W/NotificationService: Toast already killed. pkg=ai.jarvis.app
     *
     * The home-screen version of the same assertion passed, because that button
     * is on screen and needs no scroll — which is exactly how a nested-wait bug
     * disguises itself as a difference between two screens.
     *
     * So: scroll here, click inside the toast window, and keep the action passed
     * to `Toasts` down to a single interaction that waits for nothing.
     */
    private fun locate(label: String): UiObject2 {
        val button = findButton(label)
        assertNotNull(
            "No button labelled \"$label\" on screen.\n${Device.windowDump()}",
            button,
        )
        return button!!
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
        // Scrolled into view BEFORE the toast window opens — see `locate`. The
        // assertion is unchanged: a toast, not merely the absence of a crash.
        val button = locate(label)
        Toasts.expect("not available in this build") { button.click() }
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
        /**
         * The buttons on Settings that leave the app, and must come back.
         *
         * This used to be eight raw Settings shortcuts sitting in a grid on the
         * Settings screen, with no indication of which were granted, two of them
         * opening a screen a button higher up already opened, and two more with
         * near-identical names and unrelated meanings. They now live on the
         * checklist, which shows each one's state and what breaks without it —
         * so what is left to walk from here is APP INFO plus the two grants
         * Settings still offers directly, because those decide whether a wake
         * word can put anything on screen at all. The checklist itself is walked
         * by `settingsOpensTheDiagnosticScreensItOwns`.
         */
        val SYSTEM_ACCESS_BUTTONS = listOf(
            "APP INFO",
            "ALLOW BACKGROUND",
            "DISPLAY OVER APPS",
        )

        const val MAX_BACK_PRESSES = 4
        const val IDLE_MS = 3_000L
    }
}
