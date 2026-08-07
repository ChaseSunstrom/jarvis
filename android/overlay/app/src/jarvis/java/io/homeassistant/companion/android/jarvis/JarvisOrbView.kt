package io.homeassistant.companion.android.jarvis

import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.SweepGradient
import android.util.AttributeSet
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Pure android.graphics orb + edge-light view, visually matching the Jarvis
 * web HUD orb. No dependencies beyond the platform SDK.
 *
 * Elements:
 *  - Edge light: a stroked rounded-rect path hugging the screen edges with a
 *    sweeping gradient shader; one full sweep runs [EDGE_SWEEP_MS] ms on
 *    entrance, then the edge settles to a faint static glow.
 *  - Orb: radial-gradient circle rising from the bottom edge on entrance,
 *    then "breathing" (slow scale oscillation). [setAmplitude] modulates
 *    radius and glow with live mic level (0..1).
 *
 * Colors follow the HUD: cyan while idle/listening, amber while thinking,
 * gold while speaking. Switch with [setMode]; transitions are blended.
 */
class JarvisOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    enum class Mode(val color: Int) {
        LISTENING(0xFF35E4FF.toInt()), // cyan
        THINKING(0xFFFFB347.toInt()),  // amber
        SPEAKING(0xFFFFD24A.toInt())   // gold
    }

    // --- state -------------------------------------------------------------

    private var mode = Mode.LISTENING
    private var currentColor = mode.color

    /** 0..1 entrance progress: orb rise + fade-in. */
    private var entranceProgress = 0f

    /** 0..1 progress of the one-shot edge sweep. */
    private var edgeSweepProgress = 0f
    private var edgeSweepDone = false

    /** Breathing phase in radians, advances continuously. */
    private var breathPhase = 0f

    /** Live mic amplitude 0..1 (already smoothed by caller or here). */
    private var amplitude = 0f
    private var smoothedAmplitude = 0f

    // --- paints / geometry ---------------------------------------------------

    private val orbPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
    }
    private val edgePath = Path()
    private val edgeRect = RectF()
    private val sweepMatrix = Matrix()
    private var edgeShader: SweepGradient? = null
    private val argbEvaluator = ArgbEvaluator()

    // --- animators -----------------------------------------------------------

    private val entranceAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = ENTRANCE_MS
        interpolator = DecelerateInterpolator(1.8f)
        addUpdateListener {
            entranceProgress = it.animatedValue as Float
            invalidate()
        }
    }

    private val edgeSweepAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = EDGE_SWEEP_MS
        interpolator = LinearInterpolator()
        addUpdateListener {
            edgeSweepProgress = it.animatedValue as Float
            invalidate()
        }
    }

    private val breathAnimator = ValueAnimator.ofFloat(0f, (2.0 * Math.PI).toFloat()).apply {
        duration = BREATH_MS
        interpolator = LinearInterpolator()
        repeatCount = ValueAnimator.INFINITE
        addUpdateListener {
            breathPhase = it.animatedValue as Float
            // Exponential smoothing of the raw mic amplitude, piggybacked on
            // the breathing animator so there is a single invalidation clock.
            smoothedAmplitude += (amplitude - smoothedAmplitude) * 0.25f
            invalidate()
        }
    }

    private var colorAnimator: ValueAnimator? = null

    // --- public API ----------------------------------------------------------

    /** Kick off the entrance: edge sweep + orb rise. Safe to call again. */
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
        if (!breathAnimator.isStarted) breathAnimator.start()
    }

    /** Live mic level, 0..1. Modulates orb radius and glow. */
    fun setAmplitude(level: Float) {
        amplitude = min(1f, max(0f, level))
    }

    /** Switch orb color scheme (listening/thinking/speaking). */
    fun setMode(newMode: Mode) {
        if (newMode == mode) return
        mode = newMode
        colorAnimator?.cancel()
        colorAnimator = ValueAnimator.ofObject(argbEvaluator, currentColor, newMode.color).apply {
            duration = 200L
            addUpdateListener {
                currentColor = it.animatedValue as Int
                invalidate()
            }
            start()
        }
    }

    // --- lifecycle -----------------------------------------------------------

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val inset = EDGE_STROKE_DP * resources.displayMetrics.density / 2f
        edgeRect.set(inset, inset, w - inset, h - inset)
        val corner = 24f * resources.displayMetrics.density
        edgePath.reset()
        edgePath.addRoundRect(edgeRect, corner, corner, Path.Direction.CW)
        edgePaint.strokeWidth = EDGE_STROKE_DP * resources.displayMetrics.density
        edgeShader = SweepGradient(
            w / 2f,
            h / 2f,
            intArrayOf(Color.TRANSPARENT, Color.TRANSPARENT, currentColor, Color.TRANSPARENT),
            floatArrayOf(0f, 0.55f, 0.8f, 1f)
        )
    }

    override fun onDetachedFromWindow() {
        entranceAnimator.cancel()
        edgeSweepAnimator.cancel()
        breathAnimator.cancel()
        colorAnimator?.cancel()
        super.onDetachedFromWindow()
    }

    // --- drawing ---------------------------------------------------------------

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (width == 0 || height == 0) return
        drawEdgeLight(canvas)
        drawOrb(canvas)
    }

    private fun drawEdgeLight(canvas: Canvas) {
        val shader = edgeShader ?: return
        if (!edgeSweepDone) {
            // Sweeping highlight: rotate the gradient a full turn during entrance.
            sweepMatrix.setRotate(edgeSweepProgress * 360f - 90f, width / 2f, height / 2f)
            shader.setLocalMatrix(sweepMatrix)
            edgePaint.shader = shader
            edgePaint.alpha = (255 * (1f - 0.3f * edgeSweepProgress)).toInt()
        } else {
            // Settled: faint uniform glow that follows mic amplitude.
            edgePaint.shader = null
            edgePaint.color = currentColor
            edgePaint.alpha = (40 + 90 * smoothedAmplitude).toInt().coerceAtMost(255)
        }
        canvas.drawPath(edgePath, edgePaint)
    }

    private fun drawOrb(canvas: Canvas) {
        val density = resources.displayMetrics.density
        val baseRadius = ORB_RADIUS_DP * density
        // Breathing: +-6% radius; amplitude: up to +35% radius.
        val breath = 1f + 0.06f * sin(breathPhase.toDouble()).toFloat()
        val radius = baseRadius * breath * (1f + 0.35f * smoothedAmplitude) * entranceScale()

        val cx = width / 2f
        // Rise: from just below the bottom edge to its resting position.
        val restY = height - RESTING_BOTTOM_MARGIN_DP * density
        val startY = height + baseRadius
        val cy = startY + (restY - startY) * entranceProgress

        // Outer glow (bigger, translucent radial gradient).
        val glowRadius = radius * 2.4f
        glowPaint.shader = RadialGradient(
            cx, cy, glowRadius,
            intArrayOf(withAlpha(currentColor, 110), withAlpha(currentColor, 30), Color.TRANSPARENT),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        glowPaint.alpha = (255 * entranceProgress).toInt()
        canvas.drawCircle(cx, cy, glowRadius, glowPaint)

        // Core orb: bright center falling off to the mode color.
        orbPaint.shader = RadialGradient(
            cx, cy, radius,
            intArrayOf(Color.WHITE, lighten(currentColor, 0.35f), currentColor),
            floatArrayOf(0f, 0.35f, 1f),
            Shader.TileMode.CLAMP
        )
        orbPaint.alpha = (255 * entranceProgress).toInt()
        canvas.drawCircle(cx, cy, radius, orbPaint)
    }

    private fun entranceScale(): Float = 0.6f + 0.4f * entranceProgress

    private fun withAlpha(color: Int, alpha: Int): Int =
        Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))

    private fun lighten(color: Int, fraction: Float): Int {
        val r = Color.red(color) + ((255 - Color.red(color)) * fraction).toInt()
        val g = Color.green(color) + ((255 - Color.green(color)) * fraction).toInt()
        val b = Color.blue(color) + ((255 - Color.blue(color)) * fraction).toInt()
        return Color.rgb(min(r, 255), min(g, 255), min(b, 255))
    }

    companion object {
        /** Orb rise + fade-in duration; keep under the 300 ms activation budget. */
        const val ENTRANCE_MS = 250L

        /** One full edge-light sweep. */
        const val EDGE_SWEEP_MS = 350L

        private const val BREATH_MS = 1600L
        private const val ORB_RADIUS_DP = 36f
        private const val RESTING_BOTTOM_MARGIN_DP = 120f
        private const val EDGE_STROKE_DP = 4f
    }
}
