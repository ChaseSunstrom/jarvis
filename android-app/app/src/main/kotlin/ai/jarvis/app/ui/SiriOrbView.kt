package ai.jarvis.app.ui

import android.animation.ArgbEvaluator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.Choreographer
import android.view.View
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/**
 * The floating orb: three coloured blobs drifting through each other inside a
 * glowing ball, the way Siri looks rather than the way an instrument looks.
 *
 * This is the surface the user sees when they say "Hey Jarvis" while using
 * something else — it lives in a `TYPE_APPLICATION_OVERLAY` window put up by
 * [ai.jarvis.app.assist.AssistOverlay], over whatever app is in front. The
 * arc-reactor [JarvisOrbView] stays what the app's own screens show: rings,
 * ticks, a radar sweep, one colour per state. That reads as a HUD, which is
 * right inside Jarvis and wrong floating over somebody's messages.
 *
 * **How the colour comes about.** Each blob is a [RadialGradient] from its
 * colour to the same colour at zero alpha, composited with
 * [PorterDuff.Mode.SCREEN] inside one saved layer, so where two overlap the
 * result brightens toward their sum instead of one covering the other. That is
 * the whole trick: three slowly-orbiting circles and an additive blend produce
 * a continuously shifting field that never repeats on any timescale a person
 * watches for. Colours and rates come from [SiriPalette].
 *
 * **The clock is a [Choreographer], not a `ValueAnimator`.** [JarvisOrbView]
 * documents why it uses an animator — the instrumented suite sets the system
 * animator duration scale to 0 so Espresso is not waiting on something
 * infinite, and an infinite animator at scale 0 ends on its first frame. That
 * trade is right for a view Espresso drives and wrong for this one: nothing
 * automated ever opens the overlay, and a user with battery saver on (which
 * also forces that scale to 0) would otherwise get a frozen picture instead of
 * the animation. Frames stop at [onDetachedFromWindow], and the overlay is only
 * attached while a conversation is live.
 */
class SiriOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    // --- state --------------------------------------------------------------

    private var tone = SiriPalette.Tone.LISTENING

    /** Live blob colours; blended toward the tone's over [TONE_BLEND_MS]. */
    private val colors = SiriPalette.blobs(tone).copyOf()
    private var coreColor = SiriPalette.core(tone)
    private var rimColor = SiriPalette.rim(tone)

    /** Where the blend started, and from which colours. */
    private var blendFrom = colors.copyOf()
    private var blendCoreFrom = coreColor
    private var blendRimFrom = rimColor
    private var blendStartMs = 0L

    /** Free-running orbit phase, radians. Never reset, so a tone change never jumps. */
    private var phase = 0f

    /** Raw and smoothed microphone level, 0..1. */
    private var amplitude = 0f
    private var smoothed = 0f

    /** 0 while arriving, 1 once fully present. */
    private var entrance = 0f
    private var entranceStartMs = 0L

    private var lastFrameMs = 0L
    private var frameScheduled = false

    private val evaluator = ArgbEvaluator()

    // --- paint ---------------------------------------------------------------

    private val blobPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        xfermode = PorterDuffXfermode(PorterDuff.Mode.SCREEN)
    }
    private val corePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        xfermode = PorterDuffXfermode(PorterDuff.Mode.SCREEN)
    }
    private val haloPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    /** No xfermode: this is the ground, not one of the things blended onto it. */
    private val substratePaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
    }

    private val frame = Choreographer.FrameCallback { nanos ->
        frameScheduled = false
        advance(nanos / 1_000_000L)
        if (isAttachedToWindow) schedule()
    }

    // --- public API ----------------------------------------------------------

    /** Live microphone level, 0..1. Swells the blobs and the halo. */
    fun setAmplitude(level: Float) {
        // The same gain the arc reactor applies, and for the same reason: a
        // smoothed RMS spends ordinary speech between 0.02 and 0.10, and
        // without it every visible term moves by about one percent.
        amplitude = (level * AMPLITUDE_GAIN).coerceIn(0f, 1f)
    }

    /**
     * Switch state. The new palette is blended in rather than swapped, so
     * LISTENING → THINKING is a colour moving across the orb rather than a
     * different orb.
     */
    fun setTone(next: SiriPalette.Tone) {
        if (next == tone) return
        tone = next
        blendFrom = colors.copyOf()
        blendCoreFrom = coreColor
        blendRimFrom = rimColor
        blendStartMs = now()
        invalidate()
    }

    /** Map from the shared state machine the rest of the app speaks. */
    fun setMode(mode: JarvisOrbView.Mode) = setTone(
        when (mode) {
            JarvisOrbView.Mode.IDLE -> SiriPalette.Tone.IDLE
            JarvisOrbView.Mode.LISTENING -> SiriPalette.Tone.LISTENING
            JarvisOrbView.Mode.THINKING -> SiriPalette.Tone.THINKING
            JarvisOrbView.Mode.SPEAKING -> SiriPalette.Tone.SPEAKING
            JarvisOrbView.Mode.ERROR -> SiriPalette.Tone.ERROR
        }
    )

    // --- the clock -----------------------------------------------------------

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        lastFrameMs = 0L
        entrance = 0f
        entranceStartMs = now()
        schedule()
    }

    override fun onDetachedFromWindow() {
        Choreographer.getInstance().removeFrameCallback(frame)
        frameScheduled = false
        super.onDetachedFromWindow()
    }

    private fun schedule() {
        if (frameScheduled) return
        frameScheduled = true
        Choreographer.getInstance().postFrameCallback(frame)
    }

    private fun now(): Long = android.os.SystemClock.uptimeMillis()

    private fun advance(nowMs: Long) {
        // A first frame, or a clock that jumped, advances nothing.
        val dtMs = if (lastFrameMs == 0L) 0L else (nowMs - lastFrameMs).coerceIn(0L, 100L)
        lastFrameMs = nowMs
        val dt = dtMs / 1000f

        smoothed += (amplitude - smoothed) * 0.22f
        // Louder means faster, so the orb visibly reacts to a voice rather than
        // only to a state change.
        val hz = SiriPalette.orbitHz(tone) * (1f + 0.6f * smoothed)
        phase = ((phase + dt * hz * TWO_PI) % TWO_PI).toFloat()

        entrance = ((nowMs - entranceStartMs).toFloat() / ENTRANCE_MS).coerceIn(0f, 1f)
        applyBlend(nowMs)
        invalidate()
    }

    private fun applyBlend(nowMs: Long) {
        val target = SiriPalette.blobs(tone)
        val t = if (blendStartMs == 0L) {
            1f
        } else {
            ((nowMs - blendStartMs).toFloat() / TONE_BLEND_MS).coerceIn(0f, 1f)
        }
        for (i in colors.indices) {
            colors[i] = evaluator.evaluate(t, blendFrom[i], target[i]) as Int
        }
        coreColor = evaluator.evaluate(t, blendCoreFrom, SiriPalette.core(tone)) as Int
        rimColor = evaluator.evaluate(t, blendRimFrom, SiriPalette.rim(tone)) as Int
    }

    // --- drawing --------------------------------------------------------------

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val span = min(width, height) / 2f
        if (span <= 0f) return

        // Arrive by growing from 40% and fading up, which is the entrance the
        // in-app orb plays; the two surfaces should feel like one object.
        val arrive = EASE_OUT(entrance)
        val radius = span * BALL_FRACTION * (0.4f + 0.6f * arrive) * (1f + 0.16f * smoothed)
        val alpha = arrive

        drawHalo(canvas, cx, cy, radius, alpha, span)

        // One layer for the additive pass. Screen-blending straight onto the
        // window would brighten whatever app is behind the overlay, not the
        // blobs — the layer is what confines the blend to the orb.
        val layer = canvas.saveLayer(null, null)
        drawSubstrate(canvas, cx, cy, radius, alpha)
        for (i in 0 until SiriPalette.BLOB_COUNT) {
            drawBlob(canvas, cx, cy, radius, alpha, i)
        }
        drawCore(canvas, cx, cy, radius, alpha)
        canvas.restoreToCount(layer)

        drawRim(canvas, cx, cy, radius, alpha)
    }

    /**
     * The dark ball the colours live inside.
     *
     * Without it the orb is three translucent gradients and a rim, and over a
     * white app — a browser, a chat, a photo — that is a pale smudge rather
     * than an object. Reported as "the orb is too transparent", and the cause
     * is structural rather than a matter of tuning any one alpha: additive
     * blending has nothing to add to, so every part of it stayed as bright as
     * whatever was behind it.
     *
     * Drawn INSIDE the layer and BEFORE the blobs, with no xfermode, so it is
     * the ground they screen against — which is what makes the colours read as
     * lit rather than as washed. Its own edge fades to nothing well before the
     * ball's radius, so the orb still has no hard outline.
     */
    private fun drawSubstrate(canvas: Canvas, cx: Float, cy: Float, radius: Float, alpha: Float) {
        substratePaint.shader = RadialGradient(
            cx, cy, radius,
            intArrayOf(
                withAlpha(SUBSTRATE_COLOR, SUBSTRATE_ALPHA * alpha),
                withAlpha(SUBSTRATE_COLOR, SUBSTRATE_ALPHA * 0.92f * alpha),
                withAlpha(SUBSTRATE_COLOR, 0f),
            ),
            SUBSTRATE_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, radius, substratePaint)
    }

    private fun drawBlob(canvas: Canvas, cx: Float, cy: Float, radius: Float, alpha: Float, i: Int) {
        // Rates chosen as a small irrational-ish spread rather than multiples:
        // 1 : 0.73 : 1.31 never returns to the same arrangement, so the orb does
        // not visibly loop the way 1 : 2 : 3 would.
        val rate = ORBIT_RATES[i]
        val angle = phase * rate + ORBIT_OFFSETS[i]
        // The orbit is an ellipse, wider than tall, so the motion reads as
        // rolling rather than as three dots going round a circle.
        val orbit = radius * ORBIT_FRACTION * (0.75f + 0.25f * smoothed)
        val bx = cx + orbit * cos(angle)
        val by = cy + orbit * sin(angle) * 0.72f
        val blobRadius = radius * BLOB_FRACTION * (1f + 0.10f * smoothed)

        val color = colors[i]
        blobPaint.alpha = 255
        blobPaint.shader = RadialGradient(
            bx, by, blobRadius,
            intArrayOf(
                withAlpha(color, BLOB_ALPHA * alpha),
                withAlpha(color, BLOB_ALPHA * 0.55f * alpha),
                withAlpha(color, 0f),
            ),
            BLOB_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(bx, by, blobRadius, blobPaint)
    }

    private fun drawCore(canvas: Canvas, cx: Float, cy: Float, radius: Float, alpha: Float) {
        val coreRadius = radius * (CORE_FRACTION + 0.10f * smoothed)
        corePaint.shader = RadialGradient(
            cx, cy, coreRadius,
            intArrayOf(
                withAlpha(coreColor, CORE_ALPHA * alpha),
                withAlpha(coreColor, 0f),
            ),
            CORE_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, coreRadius, corePaint)
    }

    /** The bloom outside the ball, which is what makes it read as light. */
    private fun drawHalo(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        alpha: Float,
        span: Float,
    ) {
        // Clamped to the view, and this is the whole of "there is still a box
        // around the orb". A View's canvas is clipped to its bounds by its
        // parent, and this halo is the only thing here that can exceed them:
        // at 1.55x the ball plus a quarter more with the microphone level, a
        // loud voice pushed it to ~1.29x the half-width and the clip cut it
        // into a bright SQUARE. Which meant the box appeared exactly while
        // somebody was talking — the seconds they were looking at it — and was
        // not there in any screenshot taken of a quiet orb.
        val haloRadius = min(radius * (HALO_FRACTION + 0.25f * smoothed), span)
        haloPaint.shader = RadialGradient(
            cx, cy, haloRadius,
            intArrayOf(
                withAlpha(rimColor, HALO_ALPHA * alpha),
                withAlpha(rimColor, HALO_ALPHA * 0.35f * alpha),
                withAlpha(rimColor, 0f),
            ),
            HALO_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, haloRadius, haloPaint)
    }

    private fun drawRim(canvas: Canvas, cx: Float, cy: Float, radius: Float, alpha: Float) {
        rimPaint.shader = null
        rimPaint.strokeWidth = resources.displayMetrics.density * RIM_WIDTH_DP
        rimPaint.color = withAlpha(rimColor, RIM_ALPHA * alpha * (0.7f + 0.3f * smoothed))
        canvas.drawCircle(cx, cy, radius, rimPaint)
    }

    private fun withAlpha(color: Int, fraction: Float): Int =
        Color.argb(
            (255f * fraction.coerceIn(0f, 1f)).toInt(),
            Color.red(color),
            Color.green(color),
            Color.blue(color),
        )

    companion object {
        private const val TWO_PI = (2.0 * Math.PI).toFloat()

        /** Matches [JarvisOrbView.AMPLITUDE_GAIN] and jarvis-web's `micLevel * 4`. */
        private const val AMPLITUDE_GAIN = 4f

        private const val ENTRANCE_MS = 420f
        private const val TONE_BLEND_MS = 320f

        /** Ball radius as a fraction of half the view's shorter side. */
        private const val BALL_FRACTION = 0.62f
        private const val BLOB_FRACTION = 0.80f
        private const val ORBIT_FRACTION = 0.34f
        private const val CORE_FRACTION = 0.34f
        private const val HALO_FRACTION = 1.55f

        private const val BLOB_ALPHA = 0.92f
        private const val CORE_ALPHA = 0.95f

        /**
         * The bloom, and the rim.
         *
         * Both raised with the substrate rather than instead of it. The halo is
         * the only part that is *meant* to be faint — it is light in the air —
         * but at 0.30 over a bright app it was invisible, which left the orb
         * with a hard edge and no glow.
         */
        private const val HALO_ALPHA = 0.42f
        private const val RIM_ALPHA = 0.72f
        private const val RIM_WIDTH_DP = 1.4f

        /**
         * The ball behind the colours. Deep navy rather than black: black over a
         * dark wallpaper is a hole, and this has to read as an object on both.
         */
        private val SUBSTRATE_COLOR = 0xFF060B16.toInt()

        /**
         * Nearly opaque at the middle. This is the number that answers "too
         * transparent": at 0 the orb was whatever was behind it, tinted.
         */
        private const val SUBSTRATE_ALPHA = 0.90f

        private val BLOB_STOPS = floatArrayOf(0f, 0.45f, 1f)
        private val CORE_STOPS = floatArrayOf(0f, 1f)
        private val HALO_STOPS = floatArrayOf(0f, 0.55f, 1f)

        /**
         * Flat to 78% of the ball, then out to nothing.
         *
         * The fade has to happen inside the ball's own radius or the substrate
         * gets a visible circular edge, which is the "box" complaint in a
         * rounder form.
         */
        private val SUBSTRATE_STOPS = floatArrayOf(0f, 0.78f, 1f)

        private val ORBIT_RATES = floatArrayOf(1f, 0.73f, 1.31f)
        private val ORBIT_OFFSETS = floatArrayOf(0f, TWO_PI / 3f, 2f * TWO_PI / 3f)

        private val EASE_OUT: (Float) -> Float = { t -> 1f - (1f - t) * (1f - t) * (1f - t) }
    }
}
