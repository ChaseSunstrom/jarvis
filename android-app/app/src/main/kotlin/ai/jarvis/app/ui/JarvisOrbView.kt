package ai.jarvis.app.ui

import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.SweepGradient
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Pure android.graphics arc-reactor HUD, visually matching the Jarvis web HUD.
 * No dependencies beyond the platform SDK.
 *
 * Layers (centre out), all scaled by entrance + mic amplitude:
 *  - dark vignette scrim so the orb reads on any background;
 *  - optional chrome (corner brackets + JARVIS wordmark + state caption);
 *  - arc-reactor core: hot white centre falling to the mode colour, with glow;
 *  - bright inner rim ring;
 *  - rotating dashed mid ring + counter-rotating fine dashes;
 *  - 72-tick gauge ring with 12 major ticks;
 *  - a radar sweep wedge in the annulus;
 *  - faint outer boundary ring.
 *
 * Entrance also runs a one-shot edge-light sweep around the screen border.
 * Colours follow the HUD: cyan idle/listening, amber thinking, gold speaking.
 * Switch with [setMode]; transitions blend. [setAmplitude] feeds live mic level.
 */
class JarvisOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    enum class Mode(val color: Int) {
        LISTENING(0xFF3FD8FF.toInt()), // cyan
        THINKING(0xFFFF9E2C.toInt()),  // amber
        SPEAKING(0xFFFFCF5C.toInt())   // gold
    }

    /**
     * The frame the boot sequence wants, written by [JarvisBootAnimation] once
     * per frame while the power-on plays.
     *
     * Mutable and reused on purpose: this is touched 60 times a second on the
     * main thread and a fresh object per frame is pure garbage. Only the boot
     * animation writes it, and only between [beginBoot] and [endBoot].
     *
     * Values follow [BootTimeline]: [ringReveal] may briefly exceed 1 (that is
     * the overshoot), [ringAlpha] never does.
     */
    class BootDrive {
        /** Core radius as a fraction of its resting radius. Starts at a point. */
        var coreScale = 0f

        /** Master opacity for the reactor itself. */
        var coreAlpha = 0f

        /** Opacity for the brackets, wordmark and caption. */
        var chromeAlpha = 0f

        /** Per-ring arrival, outward: inner rim, mid dashes, fine dashes, gauge. */
        val ringReveal = FloatArray(JarvisOrbView.RING_COUNT)
        val ringAlpha = FloatArray(JarvisOrbView.RING_COUNT)
    }

    // --- state -------------------------------------------------------------

    private var mode = Mode.LISTENING
    private var currentColor = mode.color

    private var entranceProgress = 0f
    private var edgeSweepProgress = 0f
    private var edgeSweepDone = false
    private var breathPhase = 0f
    private var amplitude = 0f
    private var smoothedAmplitude = 0f

    /** Draw brackets + wordmark + caption (for the activation popup). */
    var chromeEnabled = true
    private var stateLabel = "LISTENING"

    /** Non-null only while the power-on sequence is driving this view. */
    private var bootDrive: BootDrive? = null

    // --- paints / geometry -------------------------------------------------

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val corePaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val sweepPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val scrimPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bracketPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2f)
    }
    private val wordmarkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }
    private val captionPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.NORMAL)
        textAlign = Paint.Align.CENTER
    }

    private val edgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val edgePath = Path()
    private val edgeRect = RectF()
    private val annulus = Path()
    private val sweepMatrix = Matrix()
    private var edgeShader: SweepGradient? = null
    private val argbEvaluator = ArgbEvaluator()

    // --- animators ---------------------------------------------------------

    private val entranceAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = ENTRANCE_MS
        interpolator = DecelerateInterpolator(1.8f)
        addUpdateListener { entranceProgress = it.animatedValue as Float; invalidate() }
    }

    private val edgeSweepAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = EDGE_SWEEP_MS
        interpolator = LinearInterpolator()
        addUpdateListener { edgeSweepProgress = it.animatedValue as Float }
    }

    // Single continuous clock for ring rotation + radar sweep (no invalidate).
    private val spinAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = SPIN_MS
        interpolator = LinearInterpolator()
        repeatCount = ValueAnimator.INFINITE
    }

    // 60fps clock: breathing + amplitude smoothing + the sole invalidate.
    private val breathAnimator = ValueAnimator.ofFloat(0f, (2.0 * Math.PI).toFloat()).apply {
        duration = BREATH_MS
        interpolator = LinearInterpolator()
        repeatCount = ValueAnimator.INFINITE
        addUpdateListener {
            breathPhase = it.animatedValue as Float
            smoothedAmplitude += (amplitude - smoothedAmplitude) * 0.22f
            invalidate()
        }
    }

    private var colorAnimator: ValueAnimator? = null

    // --- public API --------------------------------------------------------

    /** Kick off the entrance: edge sweep + orb scale-in. Safe to call again. */
    fun startEntrance() {
        edgeSweepDone = false
        entranceAnimator.cancel()
        edgeSweepAnimator.cancel()
        edgeSweepAnimator.removeAllListeners()
        edgeSweepAnimator.addListener(object : android.animation.AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: android.animation.Animator) {
                edgeSweepDone = true
                invalidate()
            }
        })
        entranceAnimator.start()
        edgeSweepAnimator.start()
        if (!spinAnimator.isStarted) spinAnimator.start()
        if (!breathAnimator.isStarted) breathAnimator.start()
    }

    /**
     * Hand this view over to the power-on sequence. The orb keeps its clocks
     * running (breathing, ring spin) but takes its size, opacity and ring
     * arrival from [drive] instead of from its own entrance animator.
     *
     * This is what makes the boot seamless: the splash and the home screen are
     * the same orb object, so there is nothing to swap out at the handoff.
     * Pass null to hand control back.
     */
    fun setBootDrive(drive: BootDrive?) {
        bootDrive = drive
        invalidate()
    }

    /** Start the clocks for a boot: no entrance animator, no edge sweep. */
    fun beginBoot() {
        entranceAnimator.cancel()
        edgeSweepAnimator.cancel()
        entranceProgress = 0f
        edgeSweepDone = true
        if (!spinAnimator.isStarted) spinAnimator.start()
        if (!breathAnimator.isStarted) breathAnimator.start()
    }

    /**
     * The boot is over. Settle into the idle breathing state — fully arrived,
     * chrome on, no entrance replay. Safe to call twice.
     */
    fun endBoot() {
        bootDrive = null
        entranceProgress = 1f
        edgeSweepDone = true
        if (!spinAnimator.isStarted) spinAnimator.start()
        if (!breathAnimator.isStarted) breathAnimator.start()
        invalidate()
    }

    /** Live mic level, 0..1. Modulates core radius, glow and ring brightness. */
    fun setAmplitude(level: Float) {
        amplitude = min(1f, max(0f, level))
    }

    /** Caption under the orb (e.g. LISTENING / PROCESSING / RESPONDING). */
    fun setStateLabel(label: String) {
        stateLabel = label
        invalidate()
    }

    /** Switch orb colour scheme (listening/thinking/speaking). */
    fun setMode(newMode: Mode) {
        if (newMode == mode) return
        mode = newMode
        colorAnimator?.cancel()
        colorAnimator = ValueAnimator.ofObject(argbEvaluator, currentColor, newMode.color).apply {
            duration = 220L
            addUpdateListener { currentColor = it.animatedValue as Int; invalidate() }
            start()
        }
    }

    // --- lifecycle ---------------------------------------------------------

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val inset = dp(EDGE_STROKE_DP) / 2f
        edgeRect.set(inset, inset, w - inset, h - inset)
        val corner = dp(24f)
        edgePath.reset()
        edgePath.addRoundRect(edgeRect, corner, corner, Path.Direction.CW)
        edgePaint.strokeWidth = dp(EDGE_STROKE_DP)
        edgeShader = SweepGradient(
            w / 2f, h / 2f,
            intArrayOf(Color.TRANSPARENT, Color.TRANSPARENT, currentColor, Color.TRANSPARENT),
            floatArrayOf(0f, 0.55f, 0.8f, 1f)
        )
    }

    override fun onDetachedFromWindow() {
        entranceAnimator.cancel()
        edgeSweepAnimator.cancel()
        spinAnimator.cancel()
        breathAnimator.cancel()
        colorAnimator?.cancel()
        bootDrive = null
        super.onDetachedFromWindow()
    }

    // --- drawing -----------------------------------------------------------

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (width == 0 || height == 0) return

        val boot = bootDrive
        val cx = width / 2f
        val cy = height / 2f
        // Base reactor radius: fits the smaller screen dimension.
        val base = min(width, height) * 0.20f
        val breath = 1f + 0.04f * sin(breathPhase.toDouble()).toFloat()
        // During the boot the core scale IS the ignition: it starts at a point
        // and decelerates out to full size. Outside it, the entrance animator.
        val arrival = boot?.coreScale ?: (0.7f + 0.3f * entranceProgress)
        val scale = arrival * breath * (1f + 0.14f * smoothedAmplitude)
        val r = base * scale
        val a = boot?.coreAlpha ?: entranceProgress          // master fade
        val chromeA = boot?.chromeAlpha ?: a
        val spin = spinAnimator.animatedFraction             // 0..1 continuous

        drawScrim(canvas, cx, cy, a)
        if (chromeEnabled) drawBrackets(canvas, chromeA)
        // The boot draws its own scan line; the edge sweep would fight it.
        if (boot == null) drawEdgeLight(canvas)

        // radii as fractions of r (mirror the web shader proportions). During
        // the boot each ring is also pushed out from 55% to its resting radius,
        // one at a time, overshooting slightly as it lands.
        val rInnerRim = r * 1.45f * ringScale(boot, RING_INNER_RIM)
        val rMidDash = r * 2.15f * ringScale(boot, RING_MID_DASH)
        val rFineDash = r * 2.55f * ringScale(boot, RING_FINE_DASH)
        val rGauge = r * 3.0f * ringScale(boot, RING_GAUGE)
        val rOuter = r * 3.6f * ringScale(boot, RING_GAUGE)

        val aInnerRim = a * ringAlpha(boot, RING_INNER_RIM)
        val aMidDash = a * ringAlpha(boot, RING_MID_DASH)
        val aFineDash = a * ringAlpha(boot, RING_FINE_DASH)
        val aGauge = a * ringAlpha(boot, RING_GAUGE)

        drawAnnulusSweep(canvas, cx, cy, r * 1.5f, rGauge, spin, aGauge)
        drawTicks(canvas, cx, cy, rGauge, 72, dp(6f), dp(1f), aGauge * 0.8f)
        drawTicks(canvas, cx, cy, rGauge, 12, dp(11f), dp(1.6f), aGauge)
        drawDashedRing(canvas, cx, cy, rMidDash, 28, spin * 360f, dp(2.5f), aMidDash)
        drawDashedRing(canvas, cx, cy, rFineDash, 64, -spin * 720f, dp(1.4f), aFineDash * 0.75f)
        drawRing(canvas, cx, cy, rOuter, dp(1f), aGauge * 0.4f)
        drawRing(canvas, cx, cy, rInnerRim, dp(1.6f), aInnerRim)
        drawCore(canvas, cx, cy, r, a)

        if (chromeEnabled) drawText(canvas, cx, cy, chromeA)
    }

    /** Ring radius multiplier: 1 when idle, pushing outward during the boot. */
    private fun ringScale(boot: BootDrive?, index: Int): Float =
        if (boot == null) 1f else 0.55f + 0.45f * boot.ringReveal[index]

    /** Ring opacity multiplier: 1 when idle, per-ring arrival during the boot. */
    private fun ringAlpha(boot: BootDrive?, index: Int): Float =
        if (boot == null) 1f else boot.ringAlpha[index]

    private fun drawScrim(canvas: Canvas, cx: Float, cy: Float, a: Float) {
        scrimPaint.shader = RadialGradient(
            cx, cy, max(width, height) * 0.7f,
            intArrayOf(
                withAlpha(currentColor, (26 * a).toInt()),
                0xE60A0E14.toInt(),
                0xF204070C.toInt()
            ),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), scrimPaint)
    }

    private fun drawBrackets(canvas: Canvas, a: Float) {
        bracketPaint.color = withAlpha(currentColor, (90 * a).toInt())
        val m = dp(18f)
        val len = dp(28f)
        val w = width.toFloat(); val h = height.toFloat()
        // top-left
        canvas.drawLine(m, m, m + len, m, bracketPaint)
        canvas.drawLine(m, m, m, m + len, bracketPaint)
        // top-right
        canvas.drawLine(w - m, m, w - m - len, m, bracketPaint)
        canvas.drawLine(w - m, m, w - m, m + len, bracketPaint)
        // bottom-left
        canvas.drawLine(m, h - m, m + len, h - m, bracketPaint)
        canvas.drawLine(m, h - m, m, h - m - len, bracketPaint)
        // bottom-right
        canvas.drawLine(w - m, h - m, w - m - len, h - m, bracketPaint)
        canvas.drawLine(w - m, h - m, w - m, h - m - len, bracketPaint)
    }

    private fun drawEdgeLight(canvas: Canvas) {
        val shader = edgeShader ?: return
        if (!edgeSweepDone) {
            sweepMatrix.setRotate(edgeSweepProgress * 360f - 90f, width / 2f, height / 2f)
            shader.setLocalMatrix(sweepMatrix)
            edgePaint.shader = shader
            edgePaint.alpha = (255 * (1f - 0.3f * edgeSweepProgress)).toInt()
        } else {
            edgePaint.shader = null
            edgePaint.color = currentColor
            edgePaint.alpha = (30 + 80 * smoothedAmplitude).toInt().coerceIn(0, 255)
        }
        canvas.drawPath(edgePath, edgePaint)
    }

    private fun drawCore(canvas: Canvas, cx: Float, cy: Float, r: Float, a: Float) {
        // The boot ignites the core from a literal point, so the first frames
        // ask for a zero radius — and RadialGradient throws on that. Every
        // primitive below takes the same precaution: nothing invisible is worth
        // a shader, and nothing degenerate is worth a crash.
        if (r < MIN_DRAW_PX || a <= 0f) return
        val glowR = r * 2.4f
        glowPaint.shader = RadialGradient(
            cx, cy, glowR,
            intArrayOf(withAlpha(currentColor, 120), withAlpha(currentColor, 30), Color.TRANSPARENT),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        glowPaint.alpha = (255 * a).toInt()
        canvas.drawCircle(cx, cy, glowR, glowPaint)

        corePaint.shader = RadialGradient(
            cx, cy, r,
            intArrayOf(Color.WHITE, lighten(currentColor, 0.4f), currentColor),
            floatArrayOf(0f, 0.35f, 1f),
            Shader.TileMode.CLAMP
        )
        corePaint.alpha = (255 * a).toInt()
        canvas.drawCircle(cx, cy, r, corePaint)
    }

    private fun drawRing(canvas: Canvas, cx: Float, cy: Float, r: Float, stroke: Float, a: Float) {
        if (r < MIN_DRAW_PX || a <= 0f) return
        ringPaint.shader = null
        ringPaint.color = currentColor
        ringPaint.strokeWidth = stroke
        ringPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        canvas.drawCircle(cx, cy, r, ringPaint)
    }

    private fun drawDashedRing(
        canvas: Canvas, cx: Float, cy: Float, r: Float,
        dashes: Int, rotationDeg: Float, stroke: Float, a: Float
    ) {
        if (r < MIN_DRAW_PX || a <= 0f || dashes <= 0) return
        val circumference = (2.0 * Math.PI * r).toFloat()
        val seg = circumference / (dashes * 2f)
        // A DashPathEffect whose intervals sum to zero is undefined behaviour
        // in Skia; at a sub-pixel radius the ring is invisible anyway.
        if (seg <= 0f) return
        ringPaint.shader = null
        ringPaint.color = currentColor
        ringPaint.strokeWidth = stroke
        ringPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        ringPaint.pathEffect = DashPathEffect(floatArrayOf(seg, seg), 0f)
        canvas.save()
        canvas.rotate(rotationDeg, cx, cy)
        canvas.drawCircle(cx, cy, r, ringPaint)
        canvas.restore()
        ringPaint.pathEffect = null
    }

    private fun drawTicks(
        canvas: Canvas, cx: Float, cy: Float, r: Float,
        count: Int, length: Float, stroke: Float, a: Float
    ) {
        if (r < MIN_DRAW_PX || a <= 0f || count <= 0) return
        tickPaint.color = currentColor
        tickPaint.strokeWidth = stroke
        tickPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        val rIn = r - length / 2f
        val rOut = r + length / 2f
        for (i in 0 until count) {
            val ang = (i.toFloat() / count) * 2.0 * Math.PI
            val ca = cos(ang).toFloat()
            val sa = sin(ang).toFloat()
            canvas.drawLine(cx + ca * rIn, cy + sa * rIn, cx + ca * rOut, cy + sa * rOut, tickPaint)
        }
    }

    private fun drawAnnulusSweep(
        canvas: Canvas, cx: Float, cy: Float, rIn: Float, rOut: Float, spin: Float, a: Float
    ) {
        if (rOut < MIN_DRAW_PX || rOut <= rIn || a <= 0f) return
        annulus.reset()
        annulus.fillType = Path.FillType.EVEN_ODD
        annulus.addCircle(cx, cy, rOut, Path.Direction.CW)
        annulus.addCircle(cx, cy, rIn, Path.Direction.CW)

        val sweep = SweepGradient(
            cx, cy,
            intArrayOf(Color.TRANSPARENT, Color.TRANSPARENT, withAlpha(currentColor, 150), Color.TRANSPARENT),
            floatArrayOf(0f, 0.62f, 0.92f, 1f)
        )
        sweepMatrix.setRotate(spin * 360f, cx, cy)
        sweep.setLocalMatrix(sweepMatrix)
        sweepPaint.shader = sweep
        sweepPaint.alpha = (110 * a * (0.6f + 0.4f * smoothedAmplitude)).toInt().coerceIn(0, 255)

        canvas.save()
        canvas.clipPath(annulus)
        canvas.drawRect(cx - rOut, cy - rOut, cx + rOut, cy + rOut, sweepPaint)
        canvas.restore()
        sweepPaint.shader = null
    }

    private fun drawText(canvas: Canvas, cx: Float, cy: Float, a: Float) {
        if (a <= 0f) return
        wordmarkPaint.color = withAlpha(currentColor, (235 * a).toInt())
        wordmarkPaint.textSize = dp(WORDMARK_DP)
        wordmarkPaint.letterSpacing = WORDMARK_SPACING
        canvas.drawText("JARVIS", cx, wordmarkBaselineY(), wordmarkPaint)

        captionPaint.color = withAlpha(currentColor, (200 * a).toInt())
        captionPaint.textSize = dp(13f)
        captionPaint.letterSpacing = 0.4f
        val botY = min(height - dp(56f), cy + restingOuterRadius() + dp(56f))
        canvas.drawText(stateLabel, cx, botY, captionPaint)
    }

    /**
     * The outer boundary radius with the breathing and the mic level taken out.
     * The chrome is positioned against this rather than the live radius so the
     * wordmark and the caption stay put while the orb breathes — and so the
     * boot animation can land its own wordmark on exactly this baseline.
     */
    private fun restingOuterRadius(): Float = min(width, height) * REST_OUTER_FACTOR

    /**
     * Baseline of the JARVIS wordmark. [JarvisBootAnimation] calls this so the
     * letters it resolves in finish exactly where the idle wordmark lives —
     * there is no jump at the handoff because there is nowhere to jump to.
     */
    fun wordmarkBaselineY(): Float =
        max(dp(72f), height / 2f - restingOuterRadius() - dp(48f))

    // --- colour helpers ----------------------------------------------------

    private fun withAlpha(color: Int, alpha: Int): Int =
        Color.argb(alpha.coerceIn(0, 255), Color.red(color), Color.green(color), Color.blue(color))

    private fun lighten(color: Int, fraction: Float): Int {
        val r = Color.red(color) + ((255 - Color.red(color)) * fraction).toInt()
        val g = Color.green(color) + ((255 - Color.green(color)) * fraction).toInt()
        val b = Color.blue(color) + ((255 - Color.blue(color)) * fraction).toInt()
        return Color.rgb(min(r, 255), min(g, 255), min(b, 255))
    }

    companion object {
        /** Orb scale-in + fade; keep under the 300 ms activation budget. */
        const val ENTRANCE_MS = 260L

        /** One full edge-light sweep. */
        const val EDGE_SWEEP_MS = 350L

        /** Rings, outward. The boot sequence brings them in in this order. */
        const val RING_INNER_RIM = 0
        const val RING_MID_DASH = 1
        const val RING_FINE_DASH = 2
        const val RING_GAUGE = 3
        const val RING_COUNT = 4

        /** Wordmark metrics, shared with [JarvisBootAnimation]. */
        const val WORDMARK_DP = 26f
        const val WORDMARK_SPACING = 0.55f

        /** Outer boundary radius as a fraction of the smaller screen edge. */
        const val REST_OUTER_FACTOR = 0.20f * 3.6f

        private const val BREATH_MS = 1600L
        private const val SPIN_MS = 8000L
        private const val EDGE_STROKE_DP = 3f

        /**
         * Below this radius (in px) a shape is not worth drawing, and a shader
         * built for it is worth a crash: `RadialGradient` rejects a radius of
         * zero outright, and a `DashPathEffect` whose intervals sum to zero is
         * undefined in Skia. The boot sequence starts the core at exactly zero,
         * so this is a live path, not a theoretical one.
         */
        private const val MIN_DRAW_PX = 0.5f
    }
}
