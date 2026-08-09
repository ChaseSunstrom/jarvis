package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.ui.JarvisOrbView
import androidx.test.espresso.assertion.ViewAssertions.matches
import androidx.test.espresso.matcher.ViewMatchers.isDisplayed
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
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
        for (label in listOf("TAP TO SPEAK", "MANAGE", "AUTOMATIONS", "SETTINGS")) {
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
}
