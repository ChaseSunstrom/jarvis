package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.FakeJarvisServer
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import ai.jarvis.app.ui.JarvisOrbView
import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The assist surface answers `ACTION_ASSIST`, shows the orb, and gets out of the
 * way.
 *
 * This is the screen the whole app is for: the thing that appears when you
 * squeeze the phone, hold the power button, or say the wake word. It is
 * transparent, draws over the lock screen, lives in its own task and is
 * `noHistory`, and every one of those is a property that only exists on a real
 * device — there is nothing here a JVM test could have checked.
 *
 * ## Why it points at a fake server
 *
 * `JarvisAssistActivity.begin()` starts a real `JarvisConversation` as soon as
 * the first frame is drawn. Pointed at a dead port, the pipeline client reports
 * a connection failure, the activity shows the error and closes itself 2.5
 * seconds later — so the surface under test would vanish mid-assertion, and the
 * test would be measuring a timeout rather than a UI. Pointing it at a fake
 * server that completes the handshake and then says nothing keeps the popup in
 * its listening state, which is the state a user sees.
 *
 * ## What is deliberately NOT asserted
 *
 * That the assist gesture itself works, or that the assistant role is held.
 * Neither is grantable from a test: the role is a Secure Setting written by the
 * user or by adb (see the README), and the gesture is a system input path. What
 * is asserted is the half this app owns — that the intent the system would send
 * reaches an activity that draws the right thing.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class AssistActivityTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var server: FakeJarvisServer

    @Before
    fun pointTheAppAtASilentServer() {
        server = FakeJarvisServer().start()
        TestHooks.configure(context, server.baseUrl, server.expectedToken)
        // No synthetic speech: this test is about the surface, not the round
        // trip. ConversationE2ETest covers the audio path.
    }

    @After
    fun stopServer() {
        server.close()
    }

    @Test
    fun actionAssistOpensTheOrbSurfaceAndBackClosesIt() {
        val assist = Activities.launchIntent(
            JarvisAssistActivity::class.java,
            Intent(Intent.ACTION_ASSIST).setPackage(context.packageName),
        )
        Activities.awaitResumed(assist)

        Activities.onMain {
            assertTrue(
                "The assist surface must not close itself on the way up — that is what " +
                    "an unconfigured or unreachable server looks like",
                !assist.isFinishing,
            )
            val orb = Views.firstOfType(assist, JarvisOrbView::class.java)
            assertNotNull(
                "The assist surface exists to show the orb; without one there is " +
                    "nothing on screen at all, since the window is transparent",
                orb,
            )
            assertTrue("The orb must be attached", orb!!.isAttachedToWindow)
        }

        // The window is transparent, so this screenshot is the only way to see
        // that the surface renders over whatever was behind it.
        Screenshots.take("AssistActivityTest-assist-surface")

        Device.ui.pressBack()
        Activities.awaitFinished(assist)
    }

    @Test
    fun voiceCommandOpensTheSameSurface() {
        // The wake-word path arrives as ACTION_VOICE_COMMAND rather than
        // ACTION_ASSIST. Both are declared on the same activity, and both have
        // to work — a wake word that opens nothing is the most confusing
        // possible failure, because there is no button to blame.
        val assist = Activities.launchIntent(
            JarvisAssistActivity::class.java,
            Intent(Intent.ACTION_VOICE_COMMAND).setPackage(context.packageName),
        )
        Activities.awaitResumed(assist)

        Screenshots.take("AssistActivityTest-voice-command")

        Device.ui.pressBack()
        Activities.awaitFinished(assist)
    }

    @Test
    fun tappingTheSurfaceDismissesIt() {
        val assist = Activities.launchIntent(
            JarvisAssistActivity::class.java,
            Intent(Intent.ACTION_ASSIST).setPackage(context.packageName),
        )
        Activities.awaitResumed(assist)

        // A tap anywhere closes it: an assistant popup that has to be dismissed
        // with a specific gesture is one the user learns to be afraid of.
        val device = Device.ui
        device.click(device.displayWidth / 2, device.displayHeight / 2)

        Activities.awaitFinished(assist)
        Waits.until("the assist surface to leave the screen") {
            !Activities.isResumed(JarvisAssistActivity::class.java)
        }

        Screenshots.take("AssistActivityTest-dismissed")
    }

    @Test
    fun anUnconfiguredDeviceIsSentToSettingsInsteadOfListening() {
        // Undo the @Before: this is the first-launch case, where the assist
        // gesture fires before the user has ever opened the app.
        TestHooks.resetState(context)

        val settings = Activities.expect(SettingsActivity::class.java) {
            context.startActivity(
                Intent(Intent.ACTION_ASSIST)
                    .setPackage(context.packageName)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
        Activities.awaitResumed(settings)

        Waits.until("the assist surface to close once it has handed over to Settings") {
            !Activities.isResumed(JarvisAssistActivity::class.java)
        }

        Screenshots.take("AssistActivityTest-unconfigured-to-settings")
    }
}
