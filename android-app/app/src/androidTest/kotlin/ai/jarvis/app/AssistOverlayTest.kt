package ai.jarvis.app

import ai.jarvis.app.assist.AssistOverlay
import ai.jarvis.app.ui.ReadabilityScrim
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.ui.SiriOrbView
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * The floating orb, put on a real screen by a real WindowManager.
 *
 * This test exists because of a specific and repeated failure: the overlay was
 * reported not working three times, and each time the reasoning about *why* was
 * done by reading code rather than by running it. Reading code found a
 * plausible cause each time and was wrong twice. `TYPE_APPLICATION_OVERLAY` is
 * exactly the kind of thing that cannot be verified by inspection — whether a
 * window is accepted depends on an appop, a window type, a set of flags, and
 * the platform's mood about all three.
 *
 * So this asks Android. It grants the overlay appop through the shell the same
 * way the suite grants runtime permissions, attaches the real
 * [AssistOverlay] through the real `WindowManager`, and asserts the view is
 * genuinely attached and laid out with a non-zero size — which is the thing
 * that was not happening.
 *
 * It runs on the emulator job in `.github/workflows/e2e.yml`, so a regression
 * here fails in CI rather than on somebody's phone.
 */
class AssistOverlayTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context: Context
        get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Before
    fun grantOverlay() {
        // The one permission that decides whether any of this is possible, and
        // the one a user has to grant on a Settings screen. `appops` is how the
        // shell grants it; `pm grant` does not work for SYSTEM_ALERT_WINDOW.
        Device.shell("appops set ${context.packageName} SYSTEM_ALERT_WINDOW allow")
        Device.wakeAndUnlock()
    }

    @Test
    fun theOverlayIsActuallyAcceptedByTheWindowManager() {
        assertTrue(
            "SYSTEM_ALERT_WINDOW was not granted by the shell, so this test " +
                "cannot say anything about the overlay. Grant it by hand:\n" +
                "  adb shell appops set ${context.packageName} SYSTEM_ALERT_WINDOW allow",
            Settings.canDrawOverlays(context),
        )

        var overlay: AssistOverlay? = null
        try {
            onMain {
                overlay = AssistOverlay(context) { }
                assertTrue(
                    "WindowManager refused the overlay window. This is the failure " +
                        "the user has reported three times; whatever changed about " +
                        "the window type or flags, changed it back.",
                    overlay!!.attach(),
                )
            }
            InstrumentationRegistry.getInstrumentation().waitForIdleSync()

            onMain {
                assertTrue("attach() returned true but isShowing is false", overlay!!.isShowing)
                val root = rootOf(overlay!!)
                assertNotNull("the overlay has no view tree", root)
                assertTrue("the overlay view is not attached to a window", root!!.isAttachedToWindow)
                // The bug that produced "only the notification" would leave a
                // window that exists and measures to nothing.
                assertTrue(
                    "the overlay laid out to ${root.width}x${root.height}; a zero " +
                        "dimension is a window nobody can see",
                    root.width > 0 && root.height > 0,
                )
                assertEquals("the overlay is not visible", View.VISIBLE, root.visibility)

                val orb = firstOfType(root, SiriOrbView::class.java)
                assertNotNull("the overlay is on screen without the orb in it", orb)
                assertTrue("the orb has no size", orb!!.width > 0 && orb.height > 0)
            }
            // The artifact worth having. "The overlay is not popping up" is a
            // report about something visible, and a picture of the emulator
            // with the orb floating on it is the only answer to it that does
            // not require taking someone's word.
            Screenshots.take("assist-overlay-on-screen")
        } finally {
            onMain { overlay?.detach() }
        }
    }

    @Test
    fun theOrbIsNotInsideABox() {
        // "It is surrounded by boxes, instead of just being the arc reactor
        // circle." The card is gone; this is what stops it coming back.
        //
        // The assertion is "not a PANEL", not "no background". It was the
        // second until the overlay had to become readable over a stranger's
        // app — *"it is hard to read text/view the entire orb as text behind
        // the orb is still rendering"* — which needs something behind the
        // words. A ReadabilityScrim is allowed by name because it is a radial
        // gradient that reaches zero before the window's edge: there is no
        // line on screen anywhere. The two shapes a card is made of are not
        // allowed: a GradientDrawable is what JarvisUi.panel() returns, a
        // rounded rectangle with a cyan stroke, and a ColorDrawable is a flat
        // slab. Either one back here is the box coming back.
        var overlay: AssistOverlay? = null
        try {
            onMain {
                overlay = AssistOverlay(context) { }
                assertTrue(overlay!!.attach())
            }
            InstrumentationRegistry.getInstrumentation().waitForIdleSync()
            onMain {
                val root = rootOf(overlay!!)!!
                val background = root.background
                assertTrue(
                    "the overlay draws a panel behind the orb again: " +
                        "${background?.javaClass?.name}",
                    background == null || background is ReadabilityScrim,
                )
            }
        } finally {
            onMain { overlay?.detach() }
        }
    }

    /**
     * The two complaints that survived the panel being deleted, asked of pixels.
     *
     * "It is too transparent, and there's still a box around the orb." Neither
     * is answerable by looking for a `background` — the first is about what the
     * orb paints and the second turned out to be too: the halo grows with the
     * microphone level, a View's canvas is clipped to its bounds, and a loud
     * voice pushed the bloom past the edge so the clip cut it into a bright
     * square. It appeared only WHILE somebody was speaking, which is why no
     * still of a quiet orb ever showed it.
     *
     * So: draw the real view at full amplitude into a bitmap and look. The
     * corners must be empty and the middle must be solid.
     */
    @Test
    fun theOrbIsSolidInTheMiddleAndEmptyInTheCorners() {
        var overlay: AssistOverlay? = null
        try {
            onMain {
                overlay = AssistOverlay(context) { }
                assertTrue(overlay!!.attach())
            }
            InstrumentationRegistry.getInstrumentation().waitForIdleSync()
            val orb = onMainResult { firstOfType(rootOf(overlay!!)!!, SiriOrbView::class.java) }
            assertNotNull("no orb in the overlay", orb)

            // Let the entrance finish — it fades up over 420 ms, and a bitmap
            // taken before it does says "transparent" about an orb that is
            // simply still arriving.
            SystemClock.sleep(900)
            // Full microphone level: the loudest the orb ever draws, which is
            // both the worst case for the halo overflowing and the moment the
            // user is looking at it.
            onMain { orb!!.setAmplitude(1f) }
            SystemClock.sleep(400)

            val shot = onMainResult {
                val bitmap = Bitmap.createBitmap(orb!!.width, orb.height, Bitmap.Config.ARGB_8888)
                bitmap.eraseColor(Color.TRANSPARENT)
                orb.draw(Canvas(bitmap))
                bitmap
            }
            assertTrue("the orb has no size to draw", shot.width > 8 && shot.height > 8)

            val middle = Color.alpha(shot.getPixel(shot.width / 2, shot.height / 2))
            assertTrue(
                "the centre of the orb is $middle/255 opaque — it is a smudge of " +
                    "whatever is behind it rather than an object on top of it",
                middle >= 200,
            )

            // Every corner, one pixel in. Anything drawn here is the square clip
            // of something that wanted to be round.
            val inset = 1
            for ((x, y) in listOf(
                inset to inset,
                shot.width - 1 - inset to inset,
                inset to shot.height - 1 - inset,
                shot.width - 1 - inset to shot.height - 1 - inset,
            )) {
                val corner = Color.alpha(shot.getPixel(x, y))
                assertTrue(
                    "the orb paints ${corner}/255 into its corner at ($x, $y). A circle " +
                        "cannot reach a corner, so this is the box: something is being " +
                        "clipped to the view's bounds instead of fitting inside them.",
                    corner == 0,
                )
            }
            shot.recycle()
        } finally {
            onMain { overlay?.detach() }
        }
    }

    @Test
    fun detachingRemovesTheWindow() {
        var overlay: AssistOverlay? = null
        onMain {
            overlay = AssistOverlay(context) { }
            assertTrue(overlay!!.attach())
        }
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
        val root = onMainResult { rootOf(overlay!!) }
        onMain { overlay!!.detach() }
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
        onMain {
            assertFalse("isShowing is still true after detach", overlay!!.isShowing)
            assertFalse(
                "the view is still attached to a window; the overlay leaked",
                root!!.isAttachedToWindow,
            )
        }
        // Detaching twice must not throw: the conversation can end from several
        // places at once, and a crash in a foreground service holding the
        // microphone is the worst possible outcome of a double-free.
        onMain { overlay!!.detach() }
    }

    @Test
    fun withoutThePermissionItRefusesInsteadOfCrashing() {
        Device.shell("appops set ${context.packageName} SYSTEM_ALERT_WINDOW deny")
        try {
            assertFalse(Settings.canDrawOverlays(context))
            onMain {
                val overlay = AssistOverlay(context) { }
                assertFalse(
                    "attach() claimed success without the permission, so the " +
                        "caller would never fall back to the notification",
                    overlay.attach(),
                )
                assertFalse(overlay.isShowing)
            }
        } finally {
            Device.shell("appops set ${context.packageName} SYSTEM_ALERT_WINDOW allow")
        }
    }

    // --- helpers -------------------------------------------------------------

    /** The overlay's root view, via the WindowManager it was added to. */
    private fun rootOf(overlay: AssistOverlay): ViewGroup? = overlay.rootForTest

    private fun <T : View> firstOfType(root: View, type: Class<T>): T? {
        if (type.isInstance(root)) return type.cast(root)
        if (root !is ViewGroup) return null
        for (i in 0 until root.childCount) {
            firstOfType(root.getChildAt(i), type)?.let { return it }
        }
        return null
    }

    private fun onMain(block: () -> Unit) =
        InstrumentationRegistry.getInstrumentation().runOnMainSync(block)

    private fun <T> onMainResult(block: () -> T): T {
        var result: T? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync { result = block() }
        @Suppress("UNCHECKED_CAST")
        return result as T
    }

}
