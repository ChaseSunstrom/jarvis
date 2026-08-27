package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.testing.TestHooks
import ai.jarvis.app.ui.ConsoleTab
import ai.jarvis.app.ui.JarvisOrbView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The app starts, and the home screen is the home screen.
 *
 * The cheapest test in this suite and the one with the best record. Every unit
 * test in `src/test` passed on the day the APK crashed on launch with a
 * `ClassNotFoundException`, because a JVM test never loads the app's classes the
 * way ART does, never inflates a view, and never runs `Application.onCreate`.
 * "It compiles" and "it starts" are different claims, and only one of them can
 * be checked without a device.
 *
 * The assertions are therefore about existence rather than behaviour:
 *
 *  * `MainActivity` reaches RESUMED — not created-then-immediately-finished.
 *  * The orb is instantiated, attached and actually laid out. It is the view the
 *    whole product is built around, and a custom `View` subclass constructed
 *    reflectively is exactly the shape of class that a missing constructor or a
 *    stripped class takes down.
 *  * Every control an untouched install should offer is on screen.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class AppLaunchTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    @Test
    fun mainActivityLaunchesAndShowsTheOrb() {
        val activity = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(activity)

        Activities.onMain {
            assertTrue(
                "MainActivity must not be finishing straight after launch",
                !activity.isFinishing,
            )

            val orb = Views.firstOfType(activity, JarvisOrbView::class.java)
            assertNotNull(
                "MainActivity's view tree must contain a JarvisOrbView. A null here " +
                    "means the home layout never built — check logcat for a " +
                    "ClassNotFoundException or a failure inside buildUi().",
                orb,
            )
            assertTrue("The orb must be attached to the window", orb!!.isAttachedToWindow)
        }

        // Through Espresso as well as through the object graph: this asserts the
        // orb is genuinely visible to the user, not merely present in the
        // hierarchy at zero size or behind something.
        Views.ofType(JarvisOrbView::class.java).check(matches(isDisplayed()))

        Screenshots.take("AppLaunchTest-home")
    }

    @Test
    fun homeScreenOffersTheTalkControlAndTheNavigation() {
        val activity = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(activity)

        // Matched through the accessibility tree rather than through Espresso so
        // the failure carries the whole window dump: on a screen with no
        // resource ids, "no view matched" is otherwise unactionable.
        val device = Device.ui
        // The two controls every install shows: the mute — opening the app
        // opens the microphone, so this is the voice control — and MANAGE, the
        // one way into the console. (LISTEN and PHONE sit beside them; neither
        // is what this test is about.) The console's sections are NOT here;
        // they are the tab strip inside the console frame, and a second copy of
        // them on this screen is exactly what had to be kept in step by hand.
        // See ConsoleFrame.
        val labels = listOf("SET UP JARVIS", "MANAGE")
        for (label in labels) {
            Waits.until("the home screen to show \"$label\"") {
                device.findObject(By.text(Views.textIgnoringCase(label))) != null
            }
        }

        Screenshots.take("AppLaunchTest-home-controls")
    }

    @Test
    fun anUnconfiguredInstallSaysWhereToStart() {
        // JarvisTestRule cleared the config, so this is a first-run device: it
        // must point at Settings rather than sit there looking broken.
        val activity = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(activity)

        val device = Device.ui
        Waits.until("the home screen to explain that no server is configured") {
            device.findObject(
                By.text(Views.containingIgnoringCase("point me at your Jarvis server"))
            ) != null
        }

        Screenshots.take("AppLaunchTest-unconfigured")
    }

    /**
     * The one-shot tour every other test in this suite turns off.
     *
     * `JarvisTestRule` resets to a first-install state and then calls
     * `TestHooks.markFirstRunSeen`, because otherwise this behaviour lands on
     * top of every home-screen assertion in the suite — the checklist is an
     * Activity, Espresso matches the topmost window, and nine tests across
     * three classes failed reading "the home screen does not exist" while the
     * home screen was built, attached and one window down.
     *
     * That suppression is only safe while the behaviour itself is pinned
     * somewhere, so this is that somewhere. Arming the flag AFTER the rule has
     * run is what makes this a genuine first launch.
     *
     * An emulator always qualifies: "display over other apps" and the
     * battery-optimisation exemption are granted on a Settings screen a test
     * cannot drive, and `adb shell pm grant` reaches neither — so
     * `missingEssentials()` is non-empty here by construction rather than by
     * luck.
     */
    @Test
    fun aFirstLaunchWithSomethingEssentialMissingOpensTheChecklist() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertTrue(
            "This test is meaningless unless something essential is missing, and on " +
                "an emulator something always is. If this fails, the requirement " +
                "table changed and the first-run flow no longer has a trigger.",
            GrapheneCompat.missingEssentials(context).isNotEmpty(),
        )
        TestHooks.armFirstRunChecklist(context)

        val activity = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(activity)

        Waits.until("the first-run checklist to open over the home screen") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("SYSTEM CHECK"))) != null
        }

        Screenshots.take("AppLaunchTest-first-run-checklist")
    }
}
