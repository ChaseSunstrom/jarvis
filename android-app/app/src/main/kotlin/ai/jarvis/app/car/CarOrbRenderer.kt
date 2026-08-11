package ai.jarvis.app.car

import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.ReactorOrb
import ai.jarvis.app.ui.SiriPalette
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color

/**
 * The arc reactor, as a still, for the car display.
 *
 * ## Why a bitmap and not the view
 *
 * *"can you add complete functionality for android auto, with a view of Jarvis
 * on the display, similar to the web app view?"*
 *
 * The car app library does not host arbitrary views. An app on a head unit
 * hands the host a *template* — a described list, pane or message — and the
 * host draws it, in its own styling, at whatever size and density that car
 * has. There is no `SurfaceView` to put [ai.jarvis.app.ui.SiriOrbView] into
 * outside of the navigation templates, which Jarvis is not eligible for and
 * should not claim to be.
 *
 * What a template WILL take is an image. So the same [ReactorOrb] that draws
 * the orb on the phone draws it here into a `Bitmap`, and the car shows the
 * real thing rather than a flat icon of it — same geometry, same palette, same
 * state colours.
 *
 * ## Why it does not animate
 *
 * Because it must not. A car host throttles how often an app may refresh its
 * template, and the whole point of the limit is that a moving thing on a
 * screen beside the road is a thing being looked at. The orb changes when the
 * STATE changes — listening, thinking, speaking — which is information the
 * driver asked for, and holds still the rest of the time.
 *
 * That is also why [render] takes a phase rather than reading a clock: a
 * still frame of a rotating assembly needs a definite one, and pinning it
 * makes the same state produce the same image, which is what lets
 * [JarvisCarScreen] avoid re-sending an identical template.
 */
object CarOrbRenderer {

    /**
     * Draw [mode] at [size] pixels square.
     *
     * @param level 0..1 voice amplitude, which swells the assembly exactly as
     *   it does on the phone.
     */
    fun render(mode: JarvisOrbView.Mode, size: Int, level: Float = 0f): Bitmap {
        val side = size.coerceIn(MIN_PX, MAX_PX)
        val bitmap = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        // Transparent, not the app's background: the host paints its own
        // surface behind this and a black square would be a black square on a
        // light theme.
        canvas.drawColor(Color.TRANSPARENT)

        val tone = mode.tone
        val span = side / 2f
        val frame = ReactorOrb.Frame().apply {
            cx = span
            cy = span
            radius = span / (ReactorOrb.OUTER_FACTOR * MAX_SCALE) *
                (1f + AMPLITUDE_SWELL * level.coerceIn(0f, 1f))
            alpha = 1f
            this.level = level.coerceIn(0f, 1f)
            phase = STILL_PHASE
            spinDeg = STILL_SPIN_DEG
            blobs = SiriPalette.blobs(tone)
            core = SiriPalette.core(tone)
            rim = SiriPalette.rim(tone)
            maxRadius = span
            // Deliberately off even while thinking. Turbulence is the one part
            // of this picture that only reads as motion, and a still frame of
            // it is just an asymmetric orb.
            turbulence = false
        }
        ReactorOrb(density = side / NOMINAL_DP).draw(canvas, frame)
        return bitmap
    }

    /**
     * A definite point in the assembly's rotation.
     *
     * Chosen rather than sampled: the same state must produce the same image,
     * or [JarvisCarScreen] cannot tell a real change from a redraw and would
     * spend its refresh budget on identical pictures.
     */
    private const val STILL_PHASE = 0.22f
    private const val STILL_SPIN_DEG = 14f

    /** Matches SiriOrbView, so the car and the phone swell identically. */
    private const val AMPLITUDE_SWELL = 0.16f
    private const val MAX_SCALE = 1f + AMPLITUDE_SWELL

    /**
     * What one density-independent pixel is worth here.
     *
     * [ReactorOrb] takes a display density because on a phone it is sizing
     * strokes against a real screen. A car display's density is the host's
     * business and is not knowable from here, so the orb is drawn as though it
     * were this many dp across — which makes stroke weights scale with the
     * bitmap instead of with somebody's phone.
     */
    private const val NOMINAL_DP = 160f

    private const val MIN_PX = 64
    private const val MAX_PX = 640
}
