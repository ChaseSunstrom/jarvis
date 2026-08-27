package ai.jarvis.app.testing

import ai.jarvis.app.MainActivity
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.os.Bundle
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView

/**
 * DEBUG SOURCE SET ONLY — see the header of [TestHooks]. Declared in
 * `app/src/debug/AndroidManifest.xml`, so it exists in no release manifest.
 *
 * An empty Jarvis-shaped surface for `BootAnimationTest`: the same
 * [JarvisOrbView] the home screen owns, at the same size and position, plus a
 * stand-in for the block of home controls whose opacity the power-on sequence
 * fades up at the handoff.
 *
 * ## Why not just use MainActivity
 *
 * The boot sequence is a *cold start* effect: `MainActivity` plays it once per
 * process, gated on `JarvisApp.consumeColdStart`, and hands its own
 * `JarvisBootAnimation` the orb through `setBootDrive`. Testing it there means
 * racing that instance — whichever activity happens to be first in the
 * instrumented run wins the cold-start flag, and a second overlay driving the
 * same orb would fight the first for `setBootDrive`. The result would be a test
 * whose outcome depends on class execution order.
 *
 * So the test builds its own overlay over this host instead. What it exercises
 * is unchanged: the real [ai.jarvis.app.ui.JarvisBootAnimation], driving a real
 * [JarvisOrbView], reporting real `onHomeAlpha` values off the real
 * `BootTimeline`, and removing itself from its parent when it completes. The
 * only thing that differs from the shipping path is which `ViewGroup` it is
 * added to.
 */
class TestHostActivity : Activity() {

    /** The orb the boot sequence drives, exactly as MainActivity's is driven. */
    lateinit var orb: JarvisOrbView
        private set

    /** Stands in for MainActivity's `homeControls`: alpha 0 until the handoff. */
    lateinit var homeControls: TextView
        private set

    /** The FrameLayout a boot overlay is added to. */
    lateinit var root: FrameLayout
        private set

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }

        orb = JarvisOrbView(this).apply {
            chromeEnabled = true
            setStateLabel(MainActivity.IDLE_CAPTION)
            // Matches MainActivity's cold-start branch: the orb waits at a point
            // for the sequence to ignite it rather than playing its entrance.
            beginBoot()
        }
        root.addView(
            orb,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        homeControls = TextView(this).apply {
            text = HOME_CONTROLS_TEXT
            setTextColor(JarvisUi.DIM)
            alpha = 0f
        }
        root.addView(
            homeControls,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                android.view.Gravity.BOTTOM
            )
        )

        setContentView(root)
    }

    companion object {
        /** Anchor the test can find on screen once the handoff has happened. */
        const val HOME_CONTROLS_TEXT = "HOME CONTROLS"
    }
}
