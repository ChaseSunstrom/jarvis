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
        /** Nothing running. Deep cyan — jarvis-web's `--jv-accent-deep`. */
        IDLE(0xFF2BB0D8.toInt()),
        LISTENING(0xFF3FD8FF.toInt()), // cyan
        THINKING(0xFFFF9E2C.toInt()),  // amber
        SPEAKING(0xFFFFCF5C.toInt()),  // gold
        /** Something failed. Red — jarvis-web's `--jv-danger`. */
        ERROR(0xFFFF6B5C.toInt())
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

    /**
     * Paint the full-view vignette behind the orb.
     *
     * On a screen-sized surface this is what makes the reactor read against
     * whatever is behind it. Inside a small card it is wrong: the card has its
     * own ground, and a near-opaque rectangle the size of the view would turn
     * a popup into a blackout. Off for any host that supplies its own
     * background.
     */
    var scrimEnabled = true

    private var stateLabel = "LISTENING"

    /** Ring rotation, degrees, free-running so a mode change never jumps it. */
    private var spinDeg = 0f

    /** Timestamp of the last frame, for the wall-clock integration above. */
    private var lastFrameMs = 0L

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

    private var colorAnimator: ValueAnimator? = null

    // --- the frame clock ----------------------------------------------------

    /**
     * Ring rotation, breathing, amplitude smoothing and the sole `invalidate`,
     * driven by the display's own vsync rather than by a `ValueAnimator`.
     *
     * This used to be two infinite `ValueAnimator`s. On a real phone with the
     * developer-options **Animator duration scale** set to off — which several
     * battery savers also force — an infinite `ValueAnimator` ends on its first
     * frame, and the orb froze completely: no breathing, no amplitude, no
     * colour blend, on the exact devices nobody tests on. `Choreographer` is
     * not scaled, so the reactor keeps turning regardless.
     *
     * Integrating against the wall clock instead of reading an animator's
     * fraction is also what lets the rotation and breathing rates depend on
     * [mode] without the phase jumping when the mode changes.
     */
    private var clockRunning = false

    private val frameCallback = object : android.view.Choreographer.FrameCallback {
        override fun doFrame(frameTimeNanos: Long) {
            if (!clockRunning) return
            val nowMs = frameTimeNanos / 1_000_000L
            // First frame, or a clock that jumped: advance nothing, just seed.
            val dtMs = if (lastFrameMs == 0L) 0L else (nowMs - lastFrameMs).coerceIn(0L, 100L)
            lastFrameMs = nowMs
            val dt = dtMs / 1000f

            spinDeg = (spinDeg + dt * spinDegPerSecond()) % 360f
            breathPhase = (breathPhase + dt * TWO_PI / breathPeriodSeconds()) % TWO_PI
            smoothedAmplitude += (amplitude - smoothedAmplitude) * 0.22f

            invalidate()
            android.view.Choreographer.getInstance().postFrameCallback(this)
        }
    }

    private fun startClock() {
        if (clockRunning) return
        clockRunning = true
        lastFrameMs = 0L
        android.view.Choreographer.getInstance().postFrameCallback(frameCallback)
    }

    private fun stopClock() {
        clockRunning = false
        android.view.Choreographer.getInstance().removeFrameCallback(frameCallback)
    }

    /**
     * Ring rotation rate. The web shader runs its rings at 0.35 rad/s while
     * nothing is happening and 0.70 rad/s while a turn is live; the difference
     * is most of what makes that orb read as busy rather than decorative.
     */
    private fun spinDegPerSecond(): Float = if (mode == Mode.IDLE) 20f else 40f

    /**
     * Breathing period, seconds. These are the web HUD's CSS fallback
     * durations (Orb.svelte): 3.5s idle, 1.4s listening, 1s thinking, 1.2s
     * speaking — the clearest statement of the intended per-state pulse.
     */
    private fun breathPeriodSeconds(): Float = when (mode) {
        Mode.IDLE -> 3.5f
        Mode.LISTENING -> 1.4f
        Mode.THINKING -> 1.0f
        Mode.SPEAKING -> 1.2f
        Mode.ERROR -> 1.6f
    }

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
        startClock()
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
        startClock()
    }

    /**
     * The boot is over. Settle into the idle breathing state — fully arrived,
     * chrome on, no entrance replay. Safe to call twice.
     */
    fun endBoot() {
        bootDrive = null
        entranceProgress = 1f
        edgeSweepDone = true
        startClock()
        invalidate()
    }

    /**
     * Live mic level, 0..1. Modulates core radius, glow and ring brightness.
     *
     * [AMPLITUDE_GAIN] is applied here rather than by the caller so every
     * surface gets it and the VAD keeps the raw value it needs. Without it the
     * orb is fed a smoothed RMS that spends conversational speech between 0.02
     * and 0.10, which through a `1 + 0.14 * level` term is a size change under
     * one and a half percent — animated, wired end to end, and invisible.
     * jarvis-web applies exactly this gain before its own orb
     * (`Math.min(micLevel * 4, 1)`).
     */
    fun setAmplitude(level: Float) {
        amplitude = min(1f, max(0f, level * AMPLITUDE_GAIN))
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

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        // The clock is torn down on detach; a view that comes back (a popup
        // reused by singleTask, a re-added overlay) must start turning again.
        if (wasRunning) startClock()
    }

    /** True once any of the entry points started the clock. */
    private var wasRunning = false

    override fun onDetachedFromWindow() {
        entranceAnimator.cancel()
        edgeSweepAnimator.cancel()
        colorAnimator?.cancel()
        wasRunning = clockRunning
        stopClock()
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
        val base = baseRadius()
        val breath = 1f + 0.04f * sin(breathPhase.toDouble()).toFloat()
        // During the boot the core scale IS the ignition: it starts at a point
        // and decelerates out to full size. Outside it, the entrance animator.
        val arrival = boot?.coreScale ?: (0.7f + 0.3f * entranceProgress)
        // The GLOBAL amplitude term stays small: the web shader grows the core,
        // not the rings, so a loud voice must not push the boundary ring into
        // the margin baseRadius() reserved for it.
        val scale = arrival * breath * (1f + 0.06f * smoothedAmplitude)
        val r = base * scale
        val a = boot?.coreAlpha ?: entranceProgress          // master fade
        val chromeA = boot?.chromeAlpha ?: a
        // The web shader's global brightness term: everything lifts with the
        // voice, which is most of what makes the orb feel driven by the mic.
        val lift = 0.88f + 0.5f * smoothedAmplitude

        if (scrimEnabled) drawScrim(canvas, cx, cy, a)
        if (chromeEnabled) drawBrackets(canvas, chromeA)
        // The boot draws its own scan line; the edge sweep would fight it.
        if (boot == null) drawEdgeLight(canvas)

        // radii as fractions of r (mirror the web shader proportions). During
        // the boot each ring is also pushed out from 55% to its resting radius,
        // one at a time, overshooting slightly as it lands.
        val rInnerRim = r * 1.45f * ringScale(boot, RING_INNER_RIM)
        val rMidDash = r * 2.15f * ringScale(boot, RING_MID_DASH)
        val rFineDash = r * 2.55f * ringScale(boot, RING_FINE_DASH)
        val rGauge = r * GAUGE_FACTOR * ringScale(boot, RING_GAUGE)
        val rOuter = r * OUTER_FACTOR * ringScale(boot, RING_GAUGE)

        val aInnerRim = a * ringAlpha(boot, RING_INNER_RIM) * lift
        val aMidDash = a * ringAlpha(boot, RING_MID_DASH) * lift
        val aFineDash = a * ringAlpha(boot, RING_FINE_DASH) * lift
        val aGauge = a * ringAlpha(boot, RING_GAUGE) * lift

        drawAnnulusSweep(canvas, cx, cy, r * 1.5f, rGauge, -spinDeg, aGauge)
        drawTicks(canvas, cx, cy, rGauge, 72, dp(6f), dp(1f), aGauge * 0.8f)
        drawTicks(canvas, cx, cy, rGauge, 12, dp(11f), dp(1.6f), aGauge)
        drawDashedRing(canvas, cx, cy, rMidDash, 28, spinDeg, dp(2.5f), aMidDash)
        drawDashedRing(canvas, cx, cy, rFineDash, 64, -spinDeg * 1.43f, dp(1.4f), aFineDash * 0.75f)
        drawRing(canvas, cx, cy, rOuter, dp(1f), aGauge * 0.4f)
        drawRing(canvas, cx, cy, rInnerRim, dp(1.6f), aInnerRim)
        if (mode == Mode.THINKING && boot == null) drawTurbulence(canvas, cx, cy, r, a)
        drawCore(canvas, cx, cy, r, a * lift)

        if (chromeEnabled) drawText(canvas, cx, cy, chromeA)
    }

    /**
     * The THINKING-only ring, wobbling in radius at ~2 rad/s.
     *
     * The web shader's turbulence band (Orb.svelte), and the single cheapest
     * cue that Jarvis is working rather than merely a different colour. Sits
     * between the inner rim and the mid dashes so it cannot collide with the
     * boundary ring the geometry is budgeted against.
     */
    private fun drawTurbulence(canvas: Canvas, cx: Float, cy: Float, r: Float, a: Float) {
        val wobble = 1f + 0.02f * sin(breathPhase.toDouble() * 2.0).toFloat()
        drawRing(canvas, cx, cy, r * 1.88f * wobble, dp(1.2f), a * 0.4f)
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

    private fun drawCore(canvas: Canvas, cx: Float, cy: Float, rBase: Float, a: Float) {
        // The boot ignites the core from a literal point, so the first frames
        // ask for a zero radius — and RadialGradient throws on that. Every
        // primitive below takes the same precaution: nothing invisible is worth
        // a shader, and nothing degenerate is worth a crash.
        if (rBase < MIN_DRAW_PX || a <= 0f) return
        // The CORE is where the mic level lives, exactly as in the web shader
        // (core radius 0.125 + level*0.05, i.e. up to +40%). Growing this
        // rather than the whole reactor is what makes speech visible without
        // pushing the outer rings off the edge of the view.
        val r = rBase * (1f + 0.35f * smoothedAmplitude)
        val glowR = r * 2.4f
        glowPaint.shader = RadialGradient(
            cx, cy, glowR,
            intArrayOf(withAlpha(currentColor, 120), withAlpha(currentColor, 30), Color.TRANSPARENT),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        glowPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        canvas.drawCircle(cx, cy, glowR, glowPaint)

        corePaint.shader = RadialGradient(
            cx, cy, r,
            intArrayOf(Color.WHITE, lighten(currentColor, 0.4f), currentColor),
            floatArrayOf(0f, 0.35f, 1f),
            Shader.TileMode.CLAMP
        )
        corePaint.alpha = (255 * a).toInt().coerceIn(0, 255)
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
        canvas: Canvas, cx: Float, cy: Float, rIn: Float, rOut: Float,
        rotationDeg: Float, a: Float
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
        sweepMatrix.setRotate(rotationDeg, cx, cy)
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
     * Core radius. Everything else this class draws is a fraction of it.
     *
     * Derived from the space available rather than picked, because it was
     * picked before and picked wrong: `min(width, height) * 0.20f`, with the
     * comment "fits the smaller screen dimension", while the outermost ring is
     * drawn at [OUTER_FACTOR] × that and scaled up again by breathing and mic
     * amplitude. The outer radius came out at 0.85 × min(w, h) against a
     * largest-possible 0.5, so the gauge ring and the boundary ring ran off the
     * left and right edges and the reactor read as two arcs rather than a ring.
     *
     * Inverting the relationship is what keeps it fixed: whatever the ring
     * multipliers become, the core is whatever leaves the OUTERMOST primitive —
     * at its largest breath-plus-amplitude scale, plus its own stroke — inside
     * the view.
     */
    private fun baseRadius(): Float {
        val half = min(width, height) / 2f - dp(2f)   // the outer ring's stroke
        return max(0f, half) / (OUTER_FACTOR * MAX_SCALE)
    }

    /**
     * The outer boundary radius with the breathing and the mic level taken out.
     * The chrome is positioned against this rather than the live radius so the
     * wordmark and the caption stay put while the orb breathes — and so the
     * boot animation can land its own wordmark on exactly this baseline.
     */
    private fun restingOuterRadius(): Float = baseRadius() * OUTER_FACTOR

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

        /** The 72/12-tick gauge ring, as a multiple of the core radius. */
        const val GAUGE_FACTOR = 3.0f

        /**
         * Outermost radius drawn, as a multiple of the core radius. Read by
         * [baseRadius], which sizes the core so THIS still fits — so a retuned
         * ring cannot silently decouple from the safety margin. Anything drawn
         * beyond it must raise this constant.
         */
        const val OUTER_FACTOR = 3.6f

        /**
         * The largest scale `onDraw` can ask for: the breathing peak times the
         * global mic-amplitude term. Kept above both of them (and above the
         * boot's ring overshoot, which tops out at 1.069 × 1.04) so the margin
         * holds in every state.
         */
        private const val MAX_SCALE = 1.04f * 1.14f

        /**
         * Outer boundary radius as a fraction of the smaller screen edge.
         *
         * Only read by `JarvisBootAnimation.fallbackBaselineY`, for the case
         * where there is no orb to ask. Mirrors [baseRadius] × [OUTER_FACTOR]
         * modulo the stroke inset, so the fallback lands on the real baseline.
         */
        const val REST_OUTER_FACTOR = 0.5f / (1.04f * 1.14f)

        /**
         * Mic level → orb. jarvis-web applies the same factor before its orb
         * (`Math.min(micLevel * 4, 1)`) because the raw smoothed RMS of speech
         * spends its life in the bottom tenth of the 0..1 range.
         */
        private const val AMPLITUDE_GAIN = 4f

        private const val EDGE_STROKE_DP = 3f

        /** `(2 * PI).toFloat()`, written out because `const val` wants a literal. */
        private const val TWO_PI = 6.2831855f

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
