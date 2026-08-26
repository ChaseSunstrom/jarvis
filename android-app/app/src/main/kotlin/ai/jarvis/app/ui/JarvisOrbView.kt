package ai.jarvis.app.ui

import ai.jarvis.app.BuildConfig
import ai.jarvis.app.R
import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.Choreographer
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import kotlin.math.max
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The Jarvis HUD: the arc reactor, plus everything that frames it on a surface
 * the app owns outright.
 *
 * The reactor itself is [ReactorOrb] — the same object, drawn the same way, as
 * the floating overlay window shows. This class is the *host*: the full-view
 * vignette, the corner brackets, the JARVIS wordmark and state caption, the
 * one-shot edge-light sweep, and the hooks the power-on sequence drives it
 * through ([BootDrive]).
 *
 * Colours follow the HUD: cyan idle/listening, amber thinking, gold speaking,
 * red on failure — and they are [SiriPalette]'s, not a second table that agrees
 * with it by hand. Switch with [setMode]; transitions blend. [setAmplitude]
 * feeds the live mic level.
 */
class JarvisOrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    /**
     * What the reactor is doing, and what it wears while doing it.
     *
     * The colour is *derived* from [SiriPalette] rather than restated. It was
     * restated — five ARGB literals here and the same five over there — and two
     * tables that have to agree by hand are two tables that eventually do not.
     */
    enum class Mode(val tone: SiriPalette.Tone) {
        /** Nothing running. Deep cyan — jarvis-web's `--jv-accent-deep`. */
        IDLE(SiriPalette.Tone.IDLE),
        LISTENING(SiriPalette.Tone.LISTENING),
        THINKING(SiriPalette.Tone.THINKING),
        SPEAKING(SiriPalette.Tone.SPEAKING),

        /** Something failed. Red — jarvis-web's `--jv-danger`. */
        ERROR(SiriPalette.Tone.ERROR);

        /**
         * The one colour this state is, for everything that is not the orb:
         * captions, brackets, the edge light. The reactor itself uses all three
         * of the tone's blob colours.
         */
        val color: Int get() = SiriPalette.rim(tone)
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

    /** The single colour the chrome wears; the blend's current value. */
    private var currentColor = mode.color

    /** Live reactor colours, blended toward the mode's over [COLOR_BLEND_MS]. */
    private val blobColors = SiriPalette.blobs(mode.tone).copyOf()
    private var coreColor = SiriPalette.core(mode.tone)

    /** Where the blend started. */
    private var blendFrom = blobColors.copyOf()
    private var blendCoreFrom = coreColor
    private var blendRimFrom = currentColor

    private var entranceProgress = 0f
    private var edgeSweepProgress = 0f
    private var edgeSweepDone = false
    private var breathPhase = 0f
    private var orbitPhase = 0f
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

    init {
        // Focusable so TalkBack can land on it and read the state, and given a
        // description before the first frame so a screen whose orb never
        // changes mode still says what it is.
        isFocusable = true
        contentDescription = context.getString(R.string.a11y_orb)
    }

    /** Ring rotation, degrees, free-running so a mode change never jumps it. */
    private var spinDeg = 0f

    /** Timestamp of the last frame, for the wall-clock integration above. */
    private var lastFrameMs = 0L

    /** Non-null only while the power-on sequence is driving this view. */
    private var bootDrive: BootDrive? = null

    // --- paints / geometry -------------------------------------------------

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val reactor = ReactorOrb(density)
    private val frameSpec = ReactorOrb.Frame()

    private val scrimPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    // The wordmark in the label face and the caption in mono — the caption
    // is a state readout, which is data; the wordmark is a word.
    private val wordmarkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.LABEL_FACE
        textAlign = Paint.Align.CENTER
    }
    private val captionPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.MONO_FACE
        textAlign = Paint.Align.CENTER
    }

    /** The field lines behind the instrument: three faint circles, hairline. */
    private val fieldPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(FIELD_STROKE_DP)
    }
    private val argbEvaluator = ArgbEvaluator()

    /**
     * The reactor's own clock, in seconds since this view started turning.
     * Integrated from the wall clock in [advance] like everything else here,
     * and handed to the renderer, which reads every period off it against the
     * tokens — so the phone's blades take the same two minutes the web's do.
     */
    private var timeSeconds = 0f

    /** When the last tool call lit the blades, on [timeSeconds]; -1 before any (M53). */
    private var lastWorkAt = -1f
    private val sweepSeconds = JarvisTokens.Motion.Dur.SWEEP / 1000f
    private val speakSeconds = JarvisTokens.Motion.Reactor.SPEAK / 1000f

    /** A camera is being looked at: the iris arcs gather and hold. */
    var looking = false
        set(value) {
            if (field != value) {
                field = value
                invalidate()
            }
        }

    /** A tool call started: light the blades once and let them settle. */
    fun work() {
        lastWorkAt = timeSeconds
        invalidate()
    }

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

    /** True between [startClock] and [stopClock], animator scale notwithstanding. */
    private var clockRunning = false

    /** True while a [frameCallback] is posted, so nothing can double-post it. */
    private var frameCallbackScheduled = false

    /**
     * The 60 fps tick: ring rotation, blob drift, breathing, amplitude
     * smoothing and the sole `invalidate`. One clock now, where there used to be
     * two — a spin animator whose `animatedFraction` WAS the rotation, and a
     * breath animator whose `animatedValue` WAS the phase.
     *
     * Neither could be given a per-state rate without restarting it, which
     * jumps the phase. So the animator is only a ticker: every quantity is
     * integrated against the wall clock in [advance], and changing rate mid-turn
     * is continuous by construction.
     *
     * It is the *preferred* ticker, not the only one. With the system **animator
     * duration scale** at 0 — developer options, or a battery saver forcing it,
     * both routine on GrapheneOS — an infinite `ValueAnimator` ends on its own
     * first frame and this whole view stops redrawing: no breathing, no
     * rotation, no amplitude, no colour blend. A frozen orb is exactly what "the
     * animation isn't looped" reported. [frameCallback] is the answer to that,
     * and the end listener here is what notices: a clock declared INFINITE that
     * reaches `onAnimationEnd` did not finish, it died.
     */
    private val frameAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = FRAME_CLOCK_MS
        interpolator = LinearInterpolator()
        repeatCount = ValueAnimator.INFINITE
        addUpdateListener { advance() }
        addListener(object : android.animation.AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: android.animation.Animator) {
                // [stopClock] clears clockRunning BEFORE it cancels, so a
                // deliberate stop does not come back through here and restart
                // the view on a different clock.
                if (clockRunning) startFrameCallback()
            }
        })
    }

    /**
     * The clock of last resort: a vsync callback, which nothing in developer
     * options scales and no battery saver switches off.
     *
     * [SiriOrbView] uses one outright and says why it can. This view cannot,
     * because it is the one Espresso drives: the instrumented suite sets the
     * animator scale to 0 for its whole run (`animationsDisabled = true`)
     * precisely so `onView` is not waiting on an animation that never ends, and
     * `AppLaunchTest` matches this class through `onView`. So the callback
     * engages only where the animator has already proved useless, and
     * [frameClockFallbackEnabled] lets the debug-only test hooks hold it off
     * altogether — which is what keeps CI on exactly the frame clock it went
     * green with.
     *
     * Stops with the view: nothing is re-posted once it is detached.
     */
    private val frameCallback = Choreographer.FrameCallback {
        frameCallbackScheduled = false
        if (clockRunning) {
            advance()
            if (isAttachedToWindow) postFrameCallback()
        }
    }

    private fun advance() {
        val nowMs = android.os.SystemClock.uptimeMillis()
        // First frame, or a clock that jumped: seed, advance nothing.
        val dtMs = if (lastFrameMs == 0L) 0L else (nowMs - lastFrameMs).coerceIn(0L, 100L)
        lastFrameMs = nowMs
        val dt = dtMs / 1000f

        spinDeg = (spinDeg + dt * spinDegPerSecond()) % 360f
        timeSeconds += dt
        breathPhase = (breathPhase + dt * ReactorOrb.TWO_PI / breathPeriodSeconds()) %
            ReactorOrb.TWO_PI
        // The blob field drifts at the same per-state rate the overlay uses, and
        // faster with a voice, so both surfaces move alike.
        val hz = SiriPalette.orbitHz(mode.tone) * (1f + 0.6f * smoothedAmplitude)
        orbitPhase = (orbitPhase + dt * hz * ReactorOrb.TWO_PI) % ReactorOrb.TWO_PI
        smoothedAmplitude += (amplitude - smoothedAmplitude) * 0.22f
        invalidate()
    }

    private fun startClock() {
        clockRunning = true
        // Do not even start an animator the platform has already said it will
        // not run. At scale 0 `start()` ends it inside the same call, and the
        // end listener would restart the view on the vsync clock a frame later
        // — correct, but with one dead frame and a needless animator.
        if (fallbackClockAllowed() && !ValueAnimator.areAnimatorsEnabled()) {
            frameAnimator.cancel()
            startFrameCallback()
            return
        }
        if (frameAnimator.isStarted || frameCallbackScheduled) return
        lastFrameMs = 0L
        frameAnimator.start()
    }

    private fun stopClock() {
        // Before the cancel, not after: cancelling an animator delivers
        // onAnimationEnd, and the listener there treats a running clock's end
        // as the scale-0 death it is meant to recover from.
        clockRunning = false
        frameAnimator.cancel()
        Choreographer.getInstance().removeFrameCallback(frameCallback)
        frameCallbackScheduled = false
    }

    /** Hand the clock to vsync. Safe to call when it is already there. */
    private fun startFrameCallback() {
        if (!fallbackClockAllowed()) return
        if (frameCallbackScheduled) return
        // A clock that has just changed hands has no previous frame to measure
        // against, exactly as a clock that has just started does.
        lastFrameMs = 0L
        postFrameCallback()
    }

    private fun postFrameCallback() {
        if (frameCallbackScheduled) return
        frameCallbackScheduled = true
        Choreographer.getInstance().postFrameCallback(frameCallback)
    }

    /**
     * Whether [frameCallback] may run at all.
     *
     * Read through `BuildConfig.DEBUG` so R8 folds the whole question away in a
     * release build: shipping code has no reachable path to the flag, and the
     * fallback there is unconditional.
     */
    private fun fallbackClockAllowed(): Boolean =
        !BuildConfig.DEBUG || frameClockFallbackEnabled

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
        describeSelf()
        invalidate()
    }

    /**
     * Say what this orb is, and what it is doing.
     *
     * A custom `View` that draws everything itself has no text for TalkBack to
     * find, so this one was announced as nothing at all — on every surface, for
     * the life of the app. It is the largest thing on four screens and it is the
     * *only* thing on the wake overlay, so a blind user got a screen with no
     * content on it and a conversation that never said it had started.
     *
     * `announceForAccessibility` as well as the description, because the state
     * caption is the whole information and it changes several times a turn:
     * listening → processing → responding. A description alone is read once, on
     * focus, and nothing here takes focus.
     */
    private fun describeSelf() {
        val caption = stateLabel.trim().lowercase()
        val spoken = context.getString(
            R.string.a11y_orb_state,
            if (caption.isEmpty()) mode.name.lowercase() else caption,
        )
        if (contentDescription?.toString() == spoken) return
        contentDescription = spoken
        // Only once the view is attached: an announcement from a detached view
        // is dropped by the platform, and the description above is what a
        // TalkBack focus will read when it does arrive.
        if (isAttachedToWindow) announceForAccessibility(spoken)
    }

    /** Switch orb colour scheme (listening/thinking/speaking). */
    fun setMode(newMode: Mode) {
        if (newMode == mode) return
        mode = newMode
        describeSelf()
        blendFrom = blobColors.copyOf()
        blendCoreFrom = coreColor
        blendRimFrom = currentColor
        colorAnimator?.cancel()
        // With the system animator duration scale at 0 — developer options, a
        // battery saver, or the instrumented suite — an animator's update
        // listener may never fire, and a state change that leaves the PREVIOUS
        // colour on screen is worse than one that skips its transition. Ask
        // first, and snap when there is nothing to animate with.
        if (!ValueAnimator.areAnimatorsEnabled()) {
            applyBlend(1f)
            invalidate()
            return
        }
        colorAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = COLOR_BLEND_MS
            addUpdateListener { applyBlend(it.animatedValue as Float); invalidate() }
            start()
        }
    }

    private fun applyBlend(t: Float) {
        val target = SiriPalette.blobs(mode.tone)
        for (i in blobColors.indices) {
            blobColors[i] = argbEvaluator.evaluate(t, blendFrom[i], target[i]) as Int
        }
        coreColor = argbEvaluator.evaluate(t, blendCoreFrom, SiriPalette.core(mode.tone)) as Int
        currentColor = argbEvaluator.evaluate(t, blendRimFrom, mode.color) as Int
    }

    // --- lifecycle ---------------------------------------------------------

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        invalidate()
    }

    /** Whether the clock was running when this view was last detached. */
    private var wasRunning = false

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        // The clock is torn down on detach; a view that comes back (a popup
        // reused by singleTask, a re-added overlay) must start turning again.
        if (wasRunning) startClock()
    }

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
        val breath = 1f + 0.04f * sin(breathPhase)
        // During the boot the core scale IS the ignition: it starts at a point
        // and decelerates out to full size. Outside it, the entrance animator.
        val arrival = boot?.coreScale ?: (0.7f + 0.3f * entranceProgress)
        // The GLOBAL amplitude term stays small: the reactor grows its CORE with
        // the voice, not its rings, so a loud voice must not push the boundary
        // ring into the margin baseRadius() reserved for it.
        val scale = arrival * breath * (1f + 0.06f * smoothedAmplitude)
        val a = boot?.coreAlpha ?: entranceProgress          // master fade
        val chromeA = boot?.chromeAlpha ?: a

        if (scrimEnabled) drawScrim(canvas, cx, cy, a)
        // It is chrome, so it goes wherever the wordmark goes — including
        // through the handoff fade. What it draws now is Reactor II's field:
        // three faint circles behind the instrument, not a rounded rectangle
        // traced around the view, which was the box every report of one meant.
        if (chromeEnabled) drawEdgeLight(canvas, chromeA)

        val f = frameSpec
        f.cx = cx
        f.cy = cy
        f.radius = base * scale
        f.alpha = a
        f.level = smoothedAmplitude
        f.time = timeSeconds
        f.phase = orbitPhase
        f.spinDeg = spinDeg
        f.blobs = blobColors
        f.core = coreColor
        f.rim = currentColor
        f.idle = mode == Mode.IDLE
        f.rimAlpha = if (mode == Mode.LISTENING || mode == Mode.SPEAKING) {
            ReactorOrb.RIM_ALPHA_LIT
        } else {
            ReactorOrb.RIM_ALPHA_REST
        }
        f.maxRadius = min(width, height) / 2f
        f.turbulence = mode == Mode.THINKING && boot == null
        f.workSweep = if (lastWorkAt < 0f) 0f else (1f - (timeSeconds - lastWorkAt) / sweepSeconds).coerceIn(0f, 1f)
        f.cadence = if (mode == Mode.SPEAKING) {
            1f - ReactorOrb.CADENCE_DEPTH * (0.5f - 0.5f * cos(ReactorOrb.TWO_PI * timeSeconds / speakSeconds))
        } else {
            1f
        }
        f.looking = looking
        if (boot == null) {
            f.settleRings()
        } else {
            for (i in 0 until RING_COUNT) {
                // Each ring is pushed out from 55% to its resting radius, one at
                // a time, overshooting slightly as it lands.
                f.ringScale[i] = 0.55f + 0.45f * boot.ringReveal[i]
                f.ringAlpha[i] = boot.ringAlpha[i]
            }
        }
        reactor.draw(canvas, f)

        if (chromeEnabled) drawText(canvas, cx, cy, chromeA)
    }

    private fun drawScrim(canvas: Canvas, cx: Float, cy: Float, a: Float) {
        scrimPaint.shader = RadialGradient(
            cx, cy, max(width, height) * 0.7f,
            intArrayOf(
                withAlpha(currentColor, (26 * a).toInt()),
                JarvisTokens.Color.SCRIM,
                JarvisTokens.Color.SCRIM_APPROVAL
            ),
            floatArrayOf(0f, 0.45f, 1f),
            Shader.TileMode.CLAMP
        )
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), scrimPaint)
    }

    /**
     * The field: Reactor II's three faint circles behind the instrument — one
     * just outside the bezel, a dashed one further out, a plain one further
     * still. They are what the reactor sits IN, and they are hairlines, so
     * they cannot read as a box however the view is cut.
     *
     * Scaled by the chrome opacity, because the boot hands this view over
     * mid-fade: anything drawn at full strength the instant `bootDrive` goes
     * null snaps on around the screen while everything beside it is still
     * fading up. The entrance sweep fades them in the same way.
     */
    private fun drawEdgeLight(canvas: Canvas, chromeA: Float) {
        val entrance = if (edgeSweepDone) 1f else edgeSweepProgress
        val a = entrance * chromeA
        if (a <= 0f) return
        val cx = width / 2f
        val cy = height / 2f
        val r = restingOuterRadius()
        if (r <= 0f) return
        fieldPaint.pathEffect = null
        fieldPaint.color = withAlpha(JarvisTokens.Color.LINE_HAIR, (255 * a).toInt())
        canvas.drawCircle(cx, cy, r * FIELD_NEAR, fieldPaint)
        canvas.drawCircle(cx, cy, r * FIELD_FAR, fieldPaint)
        fieldPaint.pathEffect = DashPathEffect(floatArrayOf(dp(FIELD_DASH_DP), dp(FIELD_GAP_DP)), 0f)
        canvas.drawCircle(cx, cy, r * FIELD_MID, fieldPaint)
        fieldPaint.pathEffect = null
    }

    private fun drawText(canvas: Canvas, cx: Float, cy: Float, a: Float) {
        if (a <= 0f) return
        // The wordmark: bright, not glowing. It is a word, and the reactor
        // beneath it is the thing that is lit.
        wordmarkPaint.color = withAlpha(JarvisTokens.Color.TEXT_BRIGHT, (235 * a).toInt())
        wordmarkPaint.textSize = dp(WORDMARK_DP)
        wordmarkPaint.letterSpacing = WORDMARK_SPACING
        canvas.drawText("JARVIS", cx, wordmarkBaselineY(), wordmarkPaint)

        // The caption: the state, in the state's colour — the one line under
        // the instrument that says which of five things Jarvis is doing.
        captionPaint.color = withAlpha(currentColor, (200 * a).toInt())
        captionPaint.textSize = dp(CAPTION_DP)
        captionPaint.letterSpacing = JarvisUi.TRACK_WIDE
        val botY = min(height - dp(CAPTION_MARGIN_DP), cy + restingOuterRadius() + dp(CAPTION_MARGIN_DP))
        canvas.drawText(stateLabel, cx, botY, captionPaint)
    }

    /**
     * The glowing ball's radius. Everything the reactor draws is a multiple of
     * it, and [ReactorOrb.OUTER_FACTOR] is the largest of those multiples.
     *
     * Derived from the space available rather than picked, because it was
     * picked before and picked wrong: a fraction of `min(width, height)` with
     * the comment "fits the smaller screen dimension", while the outermost ring
     * is drawn at OUTER_FACTOR × that and scaled up again by breathing and mic
     * amplitude. The outer radius came out at 0.85 × min(w, h) against a
     * largest-possible 0.5, so the gauge ring and the boundary ring ran off the
     * left and right edges and the reactor read as two arcs rather than a ring.
     *
     * Inverting the relationship is what keeps it fixed: whatever the ring
     * multipliers become, the ball is whatever leaves the OUTERMOST primitive —
     * at its largest breath-plus-amplitude scale, plus its own stroke — inside
     * the view.
     */
    private fun baseRadius(): Float {
        val half = min(width, height) / 2f - dp(2f)   // the outer ring's stroke
        return max(0f, half) / (ReactorOrb.OUTER_FACTOR * MAX_SCALE)
    }

    /**
     * The outer boundary radius with the breathing and the mic level taken out.
     * The chrome is positioned against this rather than the live radius so the
     * wordmark and the caption stay put while the orb breathes — and so the
     * boot animation can land its own wordmark on exactly this baseline.
     */
    private fun restingOuterRadius(): Float = baseRadius() * ReactorOrb.OUTER_FACTOR

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

    companion object {
        /**
         * TEST SEAM — **debug builds only**, and the only mutable state this
         * class keeps outside an instance.
         *
         * True in every shipping build, and unreachable from one: the sole
         * writer is `ai.jarvis.app.testing.TestHooks`, which exists in the debug
         * source set alone (`assertNoTestHooksInRelease` in app/build.gradle.kts
         * fails the build if that ever stops being true), and
         * [fallbackClockAllowed] does not consult it unless `BuildConfig.DEBUG`.
         *
         * It is off for the instrumented suite because that suite is the one
         * place the fallback's trigger is met deliberately: `animationsDisabled
         * = true` sets the animator scale to 0 for the whole run so Espresso is
         * not waiting on an animation that never ends, and `AppLaunchTest`
         * matches this view through `onView`. Whether a vsync callback re-posted
         * every frame still lets `onView` reach idle is a question about a
         * device, and this repository has none to ask; the suite is not the
         * thing to find out on. Held to the animator there, this view behaves in
         * CI exactly as it did before the fallback existed.
         *
         * It can only make the view redraw LESS, and it reaches nothing but its
         * own frame clock.
         */
        @JvmStatic
        var frameClockFallbackEnabled = true

        /** Orb scale-in + fade; keep under the 300 ms activation budget. */
        const val ENTRANCE_MS = 260L

        /** One full edge-light sweep. */
        const val EDGE_SWEEP_MS = 350L

        /** State-to-state colour crossfade. */
        const val COLOR_BLEND_MS = 260L

        /**
         * Rings, outward. The boot sequence brings them in in this order, and
         * these are [ReactorOrb]'s own indices — aliased rather than restated,
         * because [BootDrive] sizes its arrays from RING_COUNT and the renderer
         * indexes them.
         */
        const val RING_INNER_RIM = ReactorOrb.RING_INNER_RIM
        const val RING_MID_DASH = ReactorOrb.RING_MID_DASH
        const val RING_FINE_DASH = ReactorOrb.RING_FINE_DASH
        const val RING_GAUGE = ReactorOrb.RING_GAUGE
        const val RING_COUNT = ReactorOrb.RING_COUNT

        /** Wordmark metrics, shared with [JarvisBootAnimation]. */
        const val WORDMARK_DP = 26f
        const val WORDMARK_SPACING = 0.55f

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
         * where there is no orb to ask. Mirrors [baseRadius] × OUTER_FACTOR
         * modulo the stroke inset, so the fallback lands on the real baseline.
         */
        const val REST_OUTER_FACTOR = 0.5f / (1.04f * 1.14f)

        /**
         * Mic level → orb. jarvis-web applies the same factor before its orb
         * (`Math.min(micLevel * 4, 1)`) because the raw smoothed RMS of speech
         * spends its life in the bottom tenth of the 0..1 range.
         */
        private const val AMPLITUDE_GAIN = 4f

        /** The field lines' stroke and dash, in dp, and their radii as multiples of the bezel's. */
        private const val FIELD_STROKE_DP = 1f
        private const val FIELD_DASH_DP = 1f
        private const val FIELD_GAP_DP = 10f
        private const val FIELD_NEAR = 1.18f
        private const val FIELD_MID = 1.62f
        private const val FIELD_FAR = 2.2f

        /** The caption under the instrument: its size and its distance from the bezel, in dp. */
        private const val CAPTION_DP = 12f
        private const val CAPTION_MARGIN_DP = 56f

        /**
         * Period of the ticker. Nothing reads its value — every quantity is
         * integrated from the wall clock — so this is only how often it wraps.
         */
        private const val FRAME_CLOCK_MS = 4000L
    }
}
