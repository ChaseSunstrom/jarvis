package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHostActivity
import ai.jarvis.app.ui.BootTimeline
import ai.jarvis.app.ui.JarvisBootAnimation
import android.animation.ValueAnimator
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * The power-on sequence plays, finishes, hands the screen over, and can be
 * skipped.
 *
 * ## Why this test has to fight the test framework
 *
 * `testOptions { animationsDisabled = true }` sets the three global animation
 * scales to 0 for the whole instrumented run, which is right for every other
 * test here. `JarvisBootAnimation` reads
 * `Settings.Global.ANIMATOR_DURATION_SCALE` and — correctly, deliberately, see
 * `BootTimeline.shouldSkip` — collapses straight to its end state when it is 0.
 * A test of the animation running under those settings would be asserting that a
 * disabled animation is disabled.
 *
 * So this class restores the scales for its own duration and puts them back
 * exactly as it found them. Restoring the *setting* is not enough on its own:
 * `ValueAnimator` caches the scale in the app process and is updated
 * asynchronously by the system, so every test here waits on
 * `ValueAnimator.areAnimatorsEnabled()` — which reads that cached value — before
 * asserting anything about timing. Without that wait, a fast CI machine can run
 * the assertions in the window where `Settings.Global` already says 1.0 and the
 * animator still believes 0, and the sequence would collapse for reasons that
 * have nothing to do with the code under test.
 *
 * ## Why not MainActivity
 *
 * `MainActivity` plays the boot once per PROCESS, behind
 * `JarvisApp.consumeColdStart`, so whether it plays at all depends on which test
 * class the runner executed first. A test whose meaning depends on execution
 * order is not a test. This drives the sequence directly over [TestHostActivity]
 * instead — the real `JarvisBootAnimation`, driving the real `JarvisOrbView`
 * through `setBootDrive`, off the real `BootTimeline`, over the real layout
 * shape (orb behind, home controls faded out in front). Only the `ViewGroup` it
 * is added to differs.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class BootAnimationTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private lateinit var originalScales: Device.AnimationScales

    @Before
    fun restoreAnimations() {
        originalScales = Device.animationScales()
        Device.setAllAnimationScales("1.0")
        awaitAnimatorsEnabled(true)
    }

    @After
    fun putAnimationsBack() {
        Device.setAnimationScales(originalScales)
    }

    @Test
    fun theSequencePlaysThroughAndHandsOverToTheHomeUi() {
        val host = Activities.launch(TestHostActivity::class.java)
        Activities.awaitResumed(host)

        val completed = AtomicBoolean(false)
        val frameCount = AtomicInteger(0)
        val lastHomeAlpha = AtomicReference(-1f)

        val boot = Activities.onMain {
            attach(host) { animation ->
                animation.onHomeAlpha = { alpha ->
                    frameCount.incrementAndGet()
                    lastHomeAlpha.set(alpha)
                    host.homeControls.alpha = alpha
                }
                animation.onComplete = { completed.set(true) }
            }
        }

        // With the scales restored, the sequence must intend to animate. If this
        // fails, everything below would be asserting the skip path by accident.
        assertTrue(
            "With animator_duration_scale restored to 1.0, willPlay() must be true. " +
                "Scales now: ${Device.animationScales()}, " +
                "ValueAnimator.areAnimatorsEnabled()=${ValueAnimator.areAnimatorsEnabled()}",
            Activities.onMain { boot.willPlay() },
        )

        Activities.onMain { boot.start() }

        // Mid-flight. The whole timeline is BootTimeline.TOTAL_MS (1400ms) and
        // the handoff does not even begin until HANDOFF_START_MS (1200ms), so a
        // check a few hundred milliseconds in is inside the sequence by a wide
        // margin — a ValueAnimator's clock is wall-clock, so it cannot have
        // finished early however slow the machine is. The assertion comes first
        // and the screenshot second: capturing takes time, and it is the
        // assertion that has to be sound.
        Thread.sleep(MIDFLIGHT_MS)
        assertFalse(
            "The sequence must still be running ${MIDFLIGHT_MS}ms in; " +
                "BootTimeline.TOTAL_MS is ${BootTimeline.TOTAL_MS}ms",
            completed.get(),
        )
        assertEquals(
            "The home UI stays hidden until the handoff at " +
                "${BootTimeline.HANDOFF_START_MS}ms — showing it early would defeat " +
                "the whole point of the sequence",
            0f,
            lastHomeAlpha.get(),
            0.001f,
        )
        Screenshots.takeImmediately("BootAnimationTest-midflight")

        Waits.until("the power-on sequence to complete", COMPLETION_TIMEOUT_MS) {
            completed.get()
        }

        assertTrue(
            "onHomeAlpha must be driven once a frame; got ${frameCount.get()} callbacks " +
                "for a ${BootTimeline.TOTAL_MS}ms sequence, which is a collapsed run, " +
                "not an animated one",
            frameCount.get() > MIN_ANIMATED_FRAMES,
        )
        assertEquals(
            "The handoff must leave the home UI fully opaque, which is what " +
                "BootTimeline.homeAlpha(TOTAL_MS) reports",
            1f,
            lastHomeAlpha.get(),
            0.001f,
        )
        assertEquals(
            "…and the timeline must agree, so the view and the timeline cannot drift",
            1f,
            BootTimeline.homeAlpha(BootTimeline.TOTAL_MS),
            0.001f,
        )

        Activities.onMain {
            assertNull(
                "The overlay must remove itself from its parent when it finishes. " +
                    "One left attached is invisible and clickable, and would swallow " +
                    "every tap on the home screen forever.",
                boot.parent,
            )
            assertEquals(
                "The home controls must be fully faded up after the handoff",
                1f,
                host.homeControls.alpha,
                0.001f,
            )
        }

        Screenshots.take("BootAnimationTest-after-handoff")
    }

    @Test
    fun skipJumpsStraightToTheEndState() {
        val host = Activities.launch(TestHostActivity::class.java)
        Activities.awaitResumed(host)

        val completed = AtomicBoolean(false)
        val lastHomeAlpha = AtomicReference(-1f)

        val boot = Activities.onMain {
            attach(host) { animation ->
                animation.onHomeAlpha = { alpha ->
                    lastHomeAlpha.set(alpha)
                    host.homeControls.alpha = alpha
                }
                animation.onComplete = { completed.set(true) }
            }
        }

        Activities.onMain { boot.start() }

        // Skip straight away — the case a user creates by tapping the screen
        // during the power-on, which MainActivity.toggleTalk routes here rather
        // than starting a conversation nobody asked for.
        Activities.onMain { boot.skip() }

        Waits.until("skip() to complete the sequence", SKIP_TIMEOUT_MS) { completed.get() }

        Activities.onMain {
            assertEquals(
                "skip() must land on the same frame the full sequence ends on. " +
                    "The end state comes out of the same BootTimeline functions as " +
                    "every other frame precisely so there is no second code path.",
                1f,
                lastHomeAlpha.get(),
                0.001f,
            )
            assertEquals(
                "The home controls must be fully faded up after a skip too",
                1f,
                host.homeControls.alpha,
                0.001f,
            )
            assertNull("A skipped overlay must also detach itself", boot.parent)

            // Idempotent: a second tap, or a destroy racing a tap, must not
            // re-fire onComplete or move the clock back off the end.
            completed.set(false)
            boot.skip()
            assertFalse("skip() must be idempotent", completed.get())
        }

        Screenshots.take("BootAnimationTest-skipped")
    }

    @Test
    fun theSequenceStandsDownWhenTheUserTurnedAnimationsOff() {
        // The other half of the contract, and the reason the rest of this class
        // has to restore the scales at all: a user who set the animator scale to
        // 0 has said, in the only words the platform gives them, that they do
        // not want this. The sequence must then complete instantly rather than
        // holding the home UI invisible while it plays to nobody.
        Device.setAllAnimationScales("0")
        awaitAnimatorsEnabled(false)
        try {
            val host = Activities.launch(TestHostActivity::class.java)
            Activities.awaitResumed(host)

            val completed = AtomicBoolean(false)
            val boot = Activities.onMain {
                attach(host) { animation ->
                    animation.onHomeAlpha = { alpha -> host.homeControls.alpha = alpha }
                    animation.onComplete = { completed.set(true) }
                }
            }

            assertFalse(
                "willPlay() must be false at animator scale 0",
                Activities.onMain { boot.willPlay() },
            )
            assertEquals(
                "…and the timeline must agree",
                0L,
                BootTimeline.scaledDurationMs(0f, reducedMotion = false),
            )

            Activities.onMain { boot.start() }
            Waits.until("start() to complete immediately at scale 0", SKIP_TIMEOUT_MS) {
                completed.get()
            }
            Activities.onMain {
                assertEquals(
                    "The home UI must be handed over immediately, not left invisible " +
                        "for the duration of an animation that is switched off",
                    1f,
                    host.homeControls.alpha,
                    0.001f,
                )
            }

            Screenshots.take("BootAnimationTest-animations-off")
        } finally {
            Device.setAllAnimationScales("1.0")
        }
    }

    // --- helpers ------------------------------------------------------------

    /**
     * Build a boot overlay over [host], wire it up with [configure], and add it
     * on top of the orb — the same arrangement `MainActivity.startBootAnimation`
     * makes. MAIN THREAD ONLY.
     */
    private fun attach(
        host: TestHostActivity,
        configure: (JarvisBootAnimation) -> Unit,
    ): JarvisBootAnimation {
        val animation = JarvisBootAnimation(host)
        animation.orb = host.orb
        animation.actionCount = null
        configure(animation)
        host.root.addView(
            animation,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        return animation
    }

    /**
     * Wait until the app process's `ValueAnimator` agrees with the setting we
     * just wrote.
     *
     * `areAnimatorsEnabled()` reads the cached duration scale that every
     * `ValueAnimator` in this process actually uses, which the system pushes in
     * asynchronously after a `settings put`. Polling `Settings.Global` instead
     * would prove only that the write landed, not that the animator knows.
     */
    private fun awaitAnimatorsEnabled(expected: Boolean) {
        Waits.until(
            "ValueAnimator.areAnimatorsEnabled() to become $expected after writing the " +
                "animation scales (currently ${Device.animationScales()})",
            SCALE_PROPAGATION_TIMEOUT_MS,
        ) {
            ValueAnimator.areAnimatorsEnabled() == expected
        }
    }

    private companion object {
        /**
         * Comfortably inside the sequence: the handoff starts at
         * BootTimeline.HANDOFF_START_MS (1200ms) and the whole thing runs for
         * TOTAL_MS (1400ms), so 300ms is during the ignition/rings with a wide
         * margin on both sides.
         */
        const val MIDFLIGHT_MS = 300L

        /**
         * A 1400ms sequence at 60fps produces ~80 onHomeAlpha callbacks; a
         * collapsed one produces two. Ten is far enough from both to be a real
         * discriminator without being a frame-rate assertion.
         */
        const val MIN_ANIMATED_FRAMES = 10

        /**
         * TOTAL_MS is 1400ms; the rest is slack for a cold emulator, where the
         * first ValueAnimator in a process can take a surprising while to
         * produce its first frame.
         */
        const val COMPLETION_TIMEOUT_MS = 20_000L

        const val SKIP_TIMEOUT_MS = 5_000L

        /** How long the system may take to push a changed scale into this process. */
        const val SCALE_PROPAGATION_TIMEOUT_MS = 15_000L
    }
}
