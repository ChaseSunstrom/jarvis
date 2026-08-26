package ai.jarvis.app.ui

import android.animation.ArgbEvaluator
import android.content.Context
import android.graphics.Canvas
import android.util.AttributeSet
import android.view.Choreographer
import android.view.View
import kotlin.math.min

/**
 * The floating orb: the arc reactor, on a window that sits over whatever app is
 * in front.
 *
 * This is the surface the user sees when they say "Hey Jarvis" while using
 * something else — it lives in a `TYPE_APPLICATION_OVERLAY` window put up by
 * [ai.jarvis.app.assist.AssistOverlay]. It used to be a *different object* from
 * the reactor the app's own screens show: three drifting blobs here, rings and
 * ticks there. That difference was the first thing reported about it — *"the
 * notification thing is different than the actual orb that spawns with the wake
 * word"* — and it is gone. Both surfaces draw [ReactorOrb], which is one
 * implementation rather than two that have to be retuned in step.
 *
 * What is still this class's own:
 *
 *  * **The clock is a [Choreographer], not a `ValueAnimator`.** [JarvisOrbView]
 *    documents why it uses an animator — the instrumented suite sets the system
 *    animator duration scale to 0 so Espresso is not waiting on something
 *    infinite, and an infinite animator at scale 0 ends on its first frame. That
 *    trade is right for a view Espresso drives and wrong for this one: nothing
 *    automated ever opens the overlay, and a user with battery saver on (which
 *    also forces that scale to 0) would otherwise get a frozen picture. Frames
 *    stop at [onDetachedFromWindow], and the overlay is only attached while a
 *    conversation is live.
 *  * **The colours are blended, not swapped.** LISTENING → THINKING is a colour
 *    moving across the orb rather than a different orb.
 *  * **No chrome around the view.** No brackets, no wordmark, no scrim: this
 *    window is sized to a card on somebody else's home screen, and anything
 *    anchored to the view's own edges would be the box this surface spent three
 *    reports losing.
 */
class SiriOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    // --- state --------------------------------------------------------------

    private var tone = SiriPalette.Tone.LISTENING

    /**
     * Live blob colours; blended toward the tone's over [TONE_BLEND_MS]. From
     * [ReactorOrb.Palette], so this orb rests in the accent as the home
     * screen's and the console's do.
     */
    private val colors = ReactorOrb.Palette.blobs(tone).copyOf()
    private var coreColor = ReactorOrb.Palette.core(tone)
    private var rimColor = ReactorOrb.Palette.rim(tone)

    /** No decorative motion (see [JarvisUi.reducedMotion]); read once per attach. */
    private var stillness = false

    /** Where the blend started, and from which colours. */
    private var blendFrom = colors.copyOf()
    private var blendCoreFrom = coreColor
    private var blendRimFrom = rimColor
    private var blendStartMs = 0L

    /** Free-running orbit phase, radians. Never reset, so a tone change never jumps. */
    private var phase = 0f

    /** Free-running chrome rotation, degrees. Same. */
    private var spinDeg = 0f

    /** The reactor's own clock, in seconds — every period is read off it against the tokens. */
    private var timeSeconds = 0f

    /** Raw and smoothed microphone level, 0..1. */
    private var amplitude = 0f
    private var smoothed = 0f

    /** 0 while arriving, 1 once fully present. */
    private var entrance = 0f
    private var entranceStartMs = 0L

    private var lastFrameMs = 0L
    private var frameScheduled = false

    private val evaluator = ArgbEvaluator()

    private val reactor = ReactorOrb(resources.displayMetrics.density)
    private val frameSpec = ReactorOrb.Frame()

    private val frame = Choreographer.FrameCallback { nanos ->
        frameScheduled = false
        advance(nanos / 1_000_000L)
        if (isAttachedToWindow) schedule()
    }

    // --- public API ----------------------------------------------------------

    /** Live microphone level, 0..1. Swells the core and the halo. */
    fun setAmplitude(level: Float) {
        // The same gain the in-app reactor applies, and for the same reason: a
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

    /**
     * Map from the shared state machine the rest of the app speaks.
     *
     * One line, because the mapping itself lives on the mode — see
     * [JarvisOrbView.Mode.tone]. It was a `when` here, which meant the two state
     * machines agreed only for as long as somebody kept them agreeing.
     */
    fun setMode(mode: JarvisOrbView.Mode) = setTone(mode.tone)

    // --- the clock -----------------------------------------------------------

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        lastFrameMs = 0L
        stillness = JarvisUi.reducedMotion(context)
        // Under reduced motion the orb is simply there: no scale-in.
        entrance = if (stillness) 1f else 0f
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
        if (!stillness) {
            // Louder means faster, so the orb visibly reacts to a voice rather than
            // only to a state change.
            val hz = SiriPalette.orbitHz(tone) * (1f + 0.6f * smoothed)
            phase = (phase + dt * hz * ReactorOrb.TWO_PI) % ReactorOrb.TWO_PI
            spinDeg = (spinDeg + dt * spinDegPerSecond()) % 360f
            // The reactor's own clock: the renderer reads every period off it
            // against the tokens, so the overlay turns at the home screen's rate.
            timeSeconds += dt
            entrance = ((nowMs - entranceStartMs).toFloat() / ENTRANCE_MS).coerceIn(0f, 1f)
        }
        applyBlend(nowMs)
        invalidate()
    }

    /** The rates [JarvisOrbView] turns its chrome at, so the two agree. */
    private fun spinDegPerSecond(): Float =
        if (tone == SiriPalette.Tone.IDLE) 20f else 40f

    private fun applyBlend(nowMs: Long) {
        val target = ReactorOrb.Palette.blobs(tone)
        val t = if (blendStartMs == 0L) {
            1f
        } else {
            ((nowMs - blendStartMs).toFloat() / TONE_BLEND_MS).coerceIn(0f, 1f)
        }
        for (i in colors.indices) {
            colors[i] = evaluator.evaluate(t, blendFrom[i], target[i]) as Int
        }
        coreColor = evaluator.evaluate(t, blendCoreFrom, ReactorOrb.Palette.core(tone)) as Int
        rimColor = evaluator.evaluate(t, blendRimFrom, ReactorOrb.Palette.rim(tone)) as Int
    }

    // --- drawing --------------------------------------------------------------

    override fun onDraw(canvas: Canvas) {
        val span = min(width, height) / 2f
        if (span <= 0f) return

        // Arrive by growing from 40% and fading up, which is the entrance the
        // in-app orb plays; the two surfaces are one object and should arrive
        // like one.
        val arrive = EASE_OUT(entrance)

        val f = frameSpec
        f.cx = width / 2f
        f.cy = height / 2f
        // The ball is whatever leaves the OUTERMOST primitive inside the view at
        // the largest scale onDraw can ask for. Inverting the relationship is
        // what keeps the chrome from being clipped into a box by the parent.
        f.radius = span / (ReactorOrb.OUTER_FACTOR * MAX_SCALE) *
            (0.4f + 0.6f * arrive) * (1f + AMPLITUDE_SWELL * smoothed)
        f.alpha = arrive
        f.level = smoothed
        f.time = timeSeconds
        f.phase = phase
        f.spinDeg = spinDeg
        f.blobs = colors
        f.core = coreColor
        f.rim = rimColor
        f.idle = tone == SiriPalette.Tone.IDLE
        f.rimAlpha = if (tone == SiriPalette.Tone.LISTENING || tone == SiriPalette.Tone.SPEAKING) {
            ReactorOrb.RIM_ALPHA_LIT
        } else {
            ReactorOrb.RIM_ALPHA_REST
        }
        f.maxRadius = span
        f.turbulence = tone == SiriPalette.Tone.THINKING
        reactor.draw(canvas, f)
    }

    companion object {
        /** Matches [JarvisOrbView.AMPLITUDE_GAIN] and jarvis-web's `micLevel * 4`. */
        private const val AMPLITUDE_GAIN = 4f

        private const val ENTRANCE_MS = 420f
        private const val TONE_BLEND_MS = 320f

        /** How much a full-level voice grows the whole assembly. */
        private const val AMPLITUDE_SWELL = 0.16f

        /**
         * The largest scale [onDraw] can ask for. The entrance only ever
         * shrinks, so this is the amplitude swell alone — and the ball is sized
         * by dividing by it, which is what guarantees the outermost ring stays
         * inside the view at any volume.
         */
        private const val MAX_SCALE = 1f + AMPLITUDE_SWELL

        private val EASE_OUT: (Float) -> Float = { t -> 1f - (1f - t) * (1f - t) * (1f - t) }
    }
}
