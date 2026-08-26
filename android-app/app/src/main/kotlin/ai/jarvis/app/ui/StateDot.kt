package ai.jarvis.app.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.view.View
import ai.jarvis.app.ui.theme.JarvisTokens
import kotlin.math.min

/**
 * The dot that says how a thing is going — the console's `.dot` in
 * `Activity.svelte`, the `i` in `CallLine.svelte`, the dot in
 * `StatusReadout.svelte`. One drawing, so an activity row, a tool call and the
 * bar's readout agree about what "live" looks like: the accent with its small
 * glow, pulsing; failed is the danger mark; done is the OK mark; at rest it is
 * the tick colour, which is below AA on purpose because a dot is not text.
 *
 * The pulse is where the accent is spent, and it is the one animation a list
 * row is allowed. It runs on the motion tokens and not at all under reduced
 * motion — `ToolActivityView` used to pulse its own dot on a hand-typed
 * 500 ms and never asked. It stops with the view: an animator on a detached
 * view keeps the whole overlay tree alive.
 */
class StateDot(context: Context) : View(context) {

    enum class Tone { NEUTRAL, REST, LIVE, OK, WARN, FAILED }

    private var tone = Tone.REST

    /** How dim the pulse goes; 1 when not pulsing. */
    private var pulseAlpha = 1f

    private var pulser: ValueAnimator? = null

    /** How long one breath takes: `motion.dur.pulse` for a row, `motion.dur.blink` for a readout. */
    var periodMs: Int = JarvisTokens.Motion.Dur.PULSE

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = JarvisTokens.Color.GLOW }

    init {
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
    }

    fun set(next: Tone) {
        if (next == tone && (next != Tone.LIVE || pulser != null)) return
        tone = next
        if (tone == Tone.LIVE) startPulse() else stopPulse()
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val r = min(width, height) / 2f
        if (r <= 0f) return
        fill.color = when (tone) {
            Tone.NEUTRAL -> JarvisTokens.Color.TEXT_FAINT
            Tone.REST -> JarvisTokens.Color.TICK
            Tone.LIVE -> JarvisTokens.Color.ACCENT
            Tone.OK -> JarvisTokens.Color.OK
            Tone.WARN -> JarvisTokens.Color.WARN
            Tone.FAILED -> JarvisTokens.Color.DANGER
        }
        if (tone == Tone.LIVE) {
            // `--jv-glow-sm`: the halo is the budgeted glow, and it is what
            // makes a live dot read as lit rather than merely coloured.
            glow.alpha = ((JarvisTokens.Color.GLOW ushr 24) * pulseAlpha).toInt().coerceIn(0, 255)
            canvas.drawCircle(cx, cy, r + JarvisUi.dp(context, JarvisUi.Space.TIGHT), glow)
            fill.alpha = (255 * pulseAlpha).toInt().coerceIn(0, 255)
        } else {
            fill.alpha = 255
        }
        canvas.drawCircle(cx, cy, r, fill)
    }

    private fun startPulse() {
        if (pulser != null) return
        if (JarvisUi.reducedMotion(context) || !ValueAnimator.areAnimatorsEnabled()) {
            pulseAlpha = 1f
            return
        }
        pulser = ValueAnimator.ofFloat(PULSE_FLOOR, 1f).apply {
            duration = periodMs.toLong()
            repeatCount = ValueAnimator.INFINITE
            repeatMode = ValueAnimator.REVERSE
            interpolator = JarvisUi.EASE_IN_OUT
            addUpdateListener {
                pulseAlpha = it.animatedValue as Float
                invalidate()
            }
            start()
        }
    }

    private fun stopPulse() {
        pulser?.cancel()
        pulser = null
        pulseAlpha = 1f
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (tone == Tone.LIVE) startPulse()
    }

    override fun onDetachedFromWindow() {
        stopPulse()
        super.onDetachedFromWindow()
    }

    private companion object {
        /** `Activity.svelte`'s pulse: from 0.55 up to full and back. */
        const val PULSE_FLOOR = 0.55f
    }
}
