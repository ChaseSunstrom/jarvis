package ai.jarvis.app.ui

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.RadialGradient
import android.graphics.Rect
import android.graphics.Shader
import android.graphics.drawable.Drawable
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * Something to read the orb and its words against, without putting them in a box.
 *
 * ## Why this exists
 *
 * *"it is hard to read text/view the entire orb as text behind the orb is
 * still rendering, can we make a blur around the text and orb so it can be
 * understood easier?"*
 *
 * The assist overlay is a `TYPE_APPLICATION_OVERLAY` window with a transparent
 * background, so whatever app is underneath draws straight through the orb and
 * through every line of the transcript. Legibility was a shadow on the text,
 * which helps a caption and does nothing for the orb's edge.
 *
 * The obvious fix is the one this must not do. Two earlier versions of this
 * surface were a dark rounded card with a cyan stroke, and both were removed
 * for the same reason: the frame became the first thing you saw, and the
 * reports said so — a box with an orb inside it, sitting on somebody's home
 * screen. So this is a gradient with no edge of its own. It is densest behind
 * the orb, thins out through the text, and reaches zero before the window
 * does, which means there is nothing on screen with a border.
 *
 * ## What it is for on API 31+, where there IS a real blur
 *
 * Both, and they do different jobs. `FLAG_BLUR_BEHIND` blurs the wallpaper and
 * the app behind, which softens detail but does not darken it — pale content
 * behind pale text stays unreadable, because blurring white gives white. This
 * supplies the contrast. It also hides the blur's own edge: the platform blurs
 * the window's whole rectangle, so without something fading out over the top
 * of it the result is a visibly rectangular patch of soft — a box again, by
 * another route.
 *
 * Below API 31, and whenever the platform refuses the blur (battery saver, the
 * developer option turned off, a GPU that cannot), this is the entire effect,
 * which is why it is tuned to be sufficient on its own.
 */
class ReadabilityScrim(
    /** Alpha at the centre, 0..1. */
    private val strength: Float = 0.76f,
    /**
     * Where the densest point sits, as a fraction of height.
     *
     * Not the middle: the orb is at the top of the column and is the thing most
     * in need of a ground, while the text below it already carries a shadow.
     */
    private val focusY: Float = 0.34f,
) : Drawable() {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var shader: Shader? = null

    override fun onBoundsChange(bounds: Rect) {
        super.onBoundsChange(bounds)
        rebuild(bounds)
    }

    private fun rebuild(bounds: Rect) {
        if (bounds.width() <= 0 || bounds.height() <= 0) {
            // A RadialGradient with a radius of zero throws, and a drawable
            // with no bounds yet is ordinary rather than exceptional — it
            // happens on every first measure pass.
            shader = null
            return
        }
        val centreX = bounds.exactCenterX()
        val centreY = bounds.top + bounds.height() * focusY
        // Past the corners on purpose. A radius that stopped at the edge would
        // put the transparent end of the gradient exactly on the window's
        // boundary, and the eye finds that line — the thing being avoided is a
        // visible rectangle, so the fade has to finish before the edge does.
        val radius = maxOf(bounds.width(), bounds.height()) * 0.86f
        val core = (strength.coerceIn(0f, 1f) * 255).toInt()

        shader = RadialGradient(
            centreX,
            centreY,
            radius,
            intArrayOf(
                // The ground, at four opacities. `JarvisTokens.Color.BG` and
                // not three numbers: this is the same black the console and
                // the desktop draw, and it moved once already.
                withAlpha(core),
                withAlpha((core * 0.94f).toInt()),
                withAlpha((core * 0.03f).toInt()),
                withAlpha(0),
            ),
            // Nearly flat out to 0.72, then off a cliff.
            //
            // The first tuning was a gentle four-stop ramp and `overlay_scrim_test`
            // showed it was decorative: by the last line of a reply it was 0.28
            // opaque, which is 1.96:1 for white text over white content behind —
            // i.e. invisible in exactly the case this exists for. Density has to
            // be held across the whole text column, so the falloff cannot start
            // until after it, and then has to be quick enough to reach zero
            // before the window's corner (0.963 of the radius) or the card comes
            // back as a rectangle of shade.
            floatArrayOf(0f, 0.72f, 0.90f, 1f),
            Shader.TileMode.CLAMP,
        )
        paint.shader = shader
    }

    override fun draw(canvas: Canvas) {
        if (shader == null) rebuild(bounds)
        if (shader == null) return
        canvas.drawRect(bounds, paint)
    }

    override fun setAlpha(alpha: Int) {
        paint.alpha = alpha
        invalidateSelf()
    }

    override fun setColorFilter(colorFilter: ColorFilter?) {
        paint.colorFilter = colorFilter
        invalidateSelf()
    }

    @Deprecated("Required by Drawable; TRANSLUCENT is correct for a gradient with alpha.")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT

    /** The background token at `alpha`, 0..255. */
    private fun withAlpha(alpha: Int): Int =
        (JarvisTokens.Color.BG and 0x00FFFFFF) or (alpha.coerceIn(0, 255) shl 24)
}
