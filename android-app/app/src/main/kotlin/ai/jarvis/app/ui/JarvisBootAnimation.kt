package ai.jarvis.app.ui

import ai.jarvis.app.config.JarvisConfig
import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.provider.Settings
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.LinearInterpolator
import kotlin.math.max
import kotlin.math.min
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The Jarvis power-on: a black screen, a hairline scan, an arc reactor igniting
 * from a point, a system reporting itself online — and then it leaves, because
 * the home HUD has been behind it the whole time.
 *
 * Two rules shape this.
 *
 * **One clock.** A single [ValueAnimator] runs 0 -> 1 and every frame asks
 * [BootTimeline] what to draw at that millisecond. No `postDelayed` chains, so
 * there is nothing to fall out of sync, nothing to leak past
 * [onDetachedFromWindow], and [skip] is simply "set the clock to the end".
 *
 * **One orb.** This view does not draw the reactor. It drives the real
 * [JarvisOrbView] the home screen already owns, through
 * [JarvisOrbView.setBootDrive]. The orb does not jump at the handoff because it
 * never changed object — only who was telling it what size to be. This overlay
 * is transparent and draws only the things that exist during the boot and then
 * go: the scan line, the ignition bloom, the wordmark, the check lines.
 *
 * It is skippable by tapping anywhere, and it does not play at all if the user
 * turned animations off — see [BootTimeline.shouldSkip].
 */
class JarvisBootAnimation @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    /**
     * The orb this sequence drives — the *same instance* the home screen uses.
     * Null degrades to chrome only, which is what a preview wants.
     */
    var orb: JarvisOrbView? = null

    /** The check lines currently being typed. Derived from [actionCount]. */
    private var lines: List<String> = BootTimeline.checkLines(null)

    /**
     * Registered action count for the third check line. Null or 0 omits the
     * line rather than announcing "0 actions ready" at someone who has not
     * connected yet.
     */
    var actionCount: Int? = null
        set(value) {
            field = value
            lines = BootTimeline.checkLines(value)
        }

    /** Called once, on the main thread, when the sequence finishes or is skipped. */
    var onComplete: (() -> Unit)? = null

    /**
     * Called every frame with the home UI's opacity, 0..1, so the host can fade
     * its own controls up around the settling orb.
     */
    var onHomeAlpha: ((Float) -> Unit)? = null

    /** Position on the timeline, in unscaled milliseconds. */
    private var timeMs: Long = 0L

    private var finished = false
    private var started = false

    /** Reused every frame; see [JarvisOrbView.BootDrive]. */
    private val drive = JarvisOrbView.BootDrive()

    // --- paints -------------------------------------------------------------

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val scanPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val scanGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val flarePaint = Paint(Paint.ANTI_ALIAS_FLAG)
    // The same faces the settled screen uses, so the letters that resolve in
    // are the letters that stay: the wordmark in the label face, the checks in
    // mono (they are data — a line typed by a machine).
    private val letterPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.LABEL_FACE
        textAlign = Paint.Align.CENTER
    }
    private val checkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.MONO_FACE
        textAlign = Paint.Align.RIGHT
    }
    private val caretPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    /** Glyph advances for the wordmark, measured once per size. */
    private val glyphWidths = FloatArray(WORDMARK.length)
    private var glyphWidthsFor = 0f

    /**
     * Blur filters are immutable, and a fresh one per letter per frame is six
     * allocations a frame for nothing. Quantised to half a dp and cached.
     */
    private val blurCache = HashMap<Int, BlurMaskFilter>()

    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        interpolator = LinearInterpolator()
        addUpdateListener { a ->
            // A cancel() from skip() delivers its callbacks synchronously while
            // this view is being torn down; nothing may move the clock back off
            // the end state once the sequence has completed.
            if (finished) return@addUpdateListener
            timeMs = ((a.animatedValue as Float) * BootTimeline.TOTAL_MS).toLong()
            pushFrame()
        }
        addListener(object : AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: Animator) = finish()
        })
    }

    init {
        // Transparent: the orb is BEHIND this view and has to be visible
        // through it from the first frame. The black of the power-on is the
        // orb's own scrim over the window background, not a lid on top of it.
        setBackgroundColor(Color.TRANSPARENT)
        // Taps mean "skip". They must not fall through to the home screen and
        // start a conversation nobody asked for.
        isClickable = true
        isFocusable = false
    }

    // --- public API ---------------------------------------------------------

    /**
     * False when this sequence would not animate at all — the user turned
     * animations off, or asked for reduced motion.
     *
     * The host needs to know this *before* it decides how long to wait for the
     * platform splash to hand over. Waiting on a splash exit in order to play
     * an animation that has been switched off is how "I disabled animations"
     * turns into "the app opens on a black screen for half a second".
     */
    fun willPlay(): Boolean =
        !BootTimeline.shouldSkip(animatorScale(), reducedMotion())

    /**
     * Start the sequence. A second call is ignored, so a configuration change
     * cannot restart the boot halfway through.
     *
     * Honours `Settings.Global.ANIMATOR_DURATION_SCALE`: at 0 this lands on the
     * end state immediately and completes without ever animating.
     */
    fun start() {
        if (started) return
        started = true

        val duration = BootTimeline.scaledDurationMs(animatorScale(), reducedMotion())
        if (duration <= 0L) {
            skip()
            return
        }
        orb?.beginBoot()
        orb?.setBootDrive(drive)
        pushFrame()                // frame 0 is black, drawn before the clock runs
        animator.duration = duration
        animator.start()
    }

    /**
     * Jump to the end state and complete. Idempotent.
     *
     * The end state comes out of the same [BootTimeline] functions as every
     * other frame, so a skip lands on exactly the frame the full sequence would
     * have ended on — there is no second code path to get wrong.
     *
     * The clock is moved BEFORE the animator is cancelled, and that order is
     * load-bearing. `Animator.cancel()` sends `onAnimationCancel` **followed by
     * `onAnimationEnd`** to its listeners — it is documented to, and AOSP's
     * `endAnimation()` does — so the cancel below re-enters [finish] itself.
     * Cancel first and that [finish] would settle the orb, fade the home UI up
     * and detach this view while `timeMs` was still mid-sequence, leaving every
     * statement after the cancel operating on a detached view whose `orb` and
     * callbacks the detach had already nulled. The trailing [finish] is for the
     * other case: an animator that never started notifies nobody.
     */
    fun skip() {
        if (finished) return
        timeMs = BootTimeline.TOTAL_MS
        orb?.setBootDrive(drive)
        pushFrame()
        animator.cancel()
        finish()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            performClick()
            return true
        }
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        skip()
        return true
    }

    // --- lifecycle ----------------------------------------------------------

    override fun onDetachedFromWindow() {
        // Everything this view owns dies here: the clock, its listeners, the
        // cached filters, the reference to the orb, and the callbacks that
        // would otherwise keep a finished Activity alive.
        animator.cancel()
        animator.removeAllUpdateListeners()
        animator.removeAllListeners()
        blurCache.clear()
        orb?.setBootDrive(null)
        orb = null
        onComplete = null
        onHomeAlpha = null
        super.onDetachedFromWindow()
    }

    // --- the frame ----------------------------------------------------------

    /** Fill in this frame's values for the orb and the host, then redraw. */
    private fun pushFrame() {
        val t = timeMs
        drive.coreScale = BootTimeline.coreScale(t)
        drive.coreAlpha = BootTimeline.coreAlpha(t)
        // The orb's own chrome fades up exactly as this overlay's fades out,
        // onto the same baseline, so the wordmark hands over without moving.
        //
        // orbChromeAlpha, NOT homeAlpha. Both wordmarks are the same glyphs in
        // the same colour on the same pixels, so this is a crossfade of one
        // object, and homeAlpha starts HOME_FADE_DELAY_MS late — which put a
        // hole in the middle of it and dropped the wordmark to a quarter of its
        // opacity at 1260ms before it came back. The host still gets homeAlpha
        // for its controls, which are crossfading with nothing.
        drive.chromeAlpha = BootTimeline.orbChromeAlpha(t)
        for (i in 0 until JarvisOrbView.RING_COUNT) {
            drive.ringReveal[i] = BootTimeline.ringReveal(t, i)
            drive.ringAlpha[i] = BootTimeline.ringAlpha(t, i)
        }
        onHomeAlpha?.invoke(BootTimeline.homeAlpha(t))
        invalidate()
    }

    private fun finish() {
        if (finished) return
        finished = true
        val done = onComplete
        // Settle the orb before telling the host we are done, so the first home
        // frame already contains a fully arrived, breathing orb.
        orb?.endBoot()
        onComplete = null
        onHomeAlpha?.invoke(1f)
        (parent as? ViewGroup)?.removeView(this)
        done?.invoke()
    }

    // --- drawing ------------------------------------------------------------

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (width == 0 || height == 0) return
        val t = timeMs
        val chrome = BootTimeline.chromeAlpha(t)
        if (chrome <= 0f) return

        drawScanLine(canvas, t, chrome)
        drawFlare(canvas, t, chrome)
        drawWordmark(canvas, t, chrome)
        drawChecks(canvas, t, chrome)
    }

    /** A single hairline sweeping top to bottom, with a soft trail behind it. */
    private fun drawScanLine(canvas: Canvas, t: Long, chrome: Float) {
        val a = BootTimeline.scanAlpha(t) * chrome
        if (a <= 0f) return
        val y = BootTimeline.scanY(t) * height
        val trail = dp(70f)

        scanGlowPaint.shader = LinearGradient(
            0f, y - trail, 0f, y,
            Color.TRANSPARENT, withAlpha(JarvisUi.ACCENT, (46f * a).toInt()),
            Shader.TileMode.CLAMP
        )
        canvas.drawRect(0f, y - trail, width.toFloat(), y, scanGlowPaint)
        scanGlowPaint.shader = null

        scanPaint.color = withAlpha(SCAN_WHITE, (230f * a).toInt())
        canvas.drawRect(0f, y - dp(0.75f), width.toFloat(), y + dp(0.75f), scanPaint)
    }

    /** The bloom that goes off when the core ignites. */
    private fun drawFlare(canvas: Canvas, t: Long, chrome: Float) {
        val f = BootTimeline.flareAlpha(t)
        val a = f * chrome
        if (a <= 0.004f) return
        val cx = width / 2f
        val cy = height / 2f
        val r = min(width, height) * (0.18f + 0.55f * f)
        if (r <= 0f) return

        flarePaint.shader = RadialGradient(
            cx, cy, r,
            intArrayOf(
                withAlpha(Color.WHITE, (150f * a).toInt()),
                withAlpha(JarvisUi.ACCENT, (70f * a).toInt()),
                Color.TRANSPARENT,
            ),
            floatArrayOf(0f, 0.35f, 1f),
            Shader.TileMode.CLAMP
        )
        canvas.drawCircle(cx, cy, r, flarePaint)
        flarePaint.shader = null
    }

    /**
     * "J A R V I S" resolving letter by letter, wide spacing closing as it
     * lands.
     *
     * The layout deliberately reproduces what `Paint.setLetterSpacing` plus
     * `Align.CENTER` would produce — the trailing gap is included in the width
     * that gets centred — so at the end of the timeline these glyphs sit on
     * exactly the pixels [JarvisOrbView]'s own wordmark occupies.
     */
    private fun drawWordmark(canvas: Canvas, t: Long, chrome: Float) {
        val size = dp(JarvisOrbView.WORDMARK_DP)
        letterPaint.textSize = size
        measureGlyphs(size)

        val gap = BootTimeline.letterSpacing(t) * size
        var total = gap * WORDMARK.length
        for (w in glyphWidths) total += w

        val cx = width / 2f
        val baseY = orb?.wordmarkBaselineY() ?: fallbackBaselineY()

        var x = cx - total / 2f
        for (i in WORDMARK.indices) {
            val a = BootTimeline.letterAlpha(t, i) * chrome
            val w = glyphWidths[i]
            if (a > 0.004f) {
                letterPaint.maskFilter = blurFilter(BootTimeline.letterBlur(t, i))
                // Bright, not the accent: the wordmark is a word, and what is
                // lit on this screen is the instrument under it.
                letterPaint.color = withAlpha(JarvisTokens.Color.TEXT_BRIGHT, (240f * a).toInt())
                canvas.drawText(WORDMARK, i, i + 1, x + w / 2f, baseY, letterPaint)
            }
            x += w + gap
        }
        letterPaint.maskFilter = null
    }

    /** Right-aligned monospace system checks, typing on one character at a time. */
    private fun drawChecks(canvas: Canvas, t: Long, chrome: Float) {
        if (lines.isEmpty()) return
        checkPaint.textSize = dp(12.5f)
        checkPaint.letterSpacing = JarvisUi.TRACK_CHROME

        val right = width - dp(26f)
        val lineHeight = dp(20f)
        val firstY = height - dp(40f) - lineHeight * (lines.size - 1)

        for (i in lines.indices) {
            val line = lines[i]
            val shown = BootTimeline.typedChars(t, i, line.length)
            if (shown <= 0) continue
            val progress = BootTimeline.checkProgress(t, i)
            val a = chrome * (0.55f + 0.45f * progress)
            checkPaint.color = withAlpha(JarvisUi.DIM, (210f * a).toInt())
            val y = firstY + lineHeight * i
            canvas.drawText(line, 0, shown, right, y, checkPaint)

            // A caret while the line is still typing. It is what makes this
            // read as a terminal rather than a fade-in.
            if (shown < line.length) {
                val w = checkPaint.measureText(line, 0, shown)
                caretPaint.color = withAlpha(JarvisUi.ACCENT, (200f * chrome).toInt())
                canvas.drawRect(
                    right - w - dp(6f), y - dp(9f),
                    right - w - dp(1.5f), y + dp(1.5f),
                    caretPaint
                )
            }
        }
    }

    // --- helpers ------------------------------------------------------------

    private fun measureGlyphs(size: Float) {
        if (glyphWidthsFor == size) return
        for (i in WORDMARK.indices) {
            glyphWidths[i] = letterPaint.measureText(WORDMARK, i, i + 1)
        }
        glyphWidthsFor = size
    }

    /** Only used when there is no orb to ask; mirrors the orb's own maths. */
    private fun fallbackBaselineY(): Float = max(
        dp(72f),
        height / 2f - min(width, height) * JarvisOrbView.REST_OUTER_FACTOR - dp(48f)
    )

    /** Quantised to half a dp so the cache stays small and lookups actually hit. */
    private fun blurFilter(radiusDp: Float): BlurMaskFilter? {
        if (radiusDp <= 0.05f) return null
        val key = (radiusDp * 2f).toInt()
        if (key <= 0) return null
        return blurCache.getOrPut(key) {
            BlurMaskFilter(dp(key / 2f), BlurMaskFilter.Blur.NORMAL)
        }
    }

    private fun withAlpha(color: Int, alpha: Int): Int =
        Color.argb(alpha.coerceIn(0, 255), Color.red(color), Color.green(color), Color.blue(color))

    /**
     * `Settings.Global.ANIMATOR_DURATION_SCALE`, defaulting to 1 when it cannot
     * be read. GrapheneOS is strict about what a third-party app may read out
     * of Settings, and a launch animation must never be the thing that throws.
     */
    private fun animatorScale(): Float = try {
        Settings.Global.getFloat(
            context.contentResolver,
            Settings.Global.ANIMATOR_DURATION_SCALE,
            1f
        )
    } catch (t: Throwable) {
        1f
    }

    /**
     * Reduced motion. Android exposes no "prefers reduced motion" flag, so the
     * honest proxy is the transition-animation scale: a user who set it to zero
     * has said, in the only words the platform gives them, that they do not
     * want this.
     */
    private fun reducedMotion(): Boolean = try {
        Settings.Global.getFloat(
            context.contentResolver,
            Settings.Global.TRANSITION_ANIMATION_SCALE,
            1f
        ) <= 0f
    } catch (t: Throwable) {
        false
    }

    companion object {
        private const val WORDMARK = "JARVIS"

        /** The scan line itself: near-white, faintly cyan. */
        private const val SCAN_WHITE = JarvisTokens.Color.TEXT_BRIGHT

        /**
         * The action count the last successful registration recorded, for the
         * third check line. Deliberately tolerant: a missing value, a wrong
         * type or a locked-storage read all mean "no line", never a crash on
         * launch.
         *
         * Read through [JarvisConfig] rather than by opening its preferences
         * file directly, so the key has exactly one owner and the writer (the
         * command channel, on `registered`) and this reader cannot drift.
         */
        @JvmStatic
        fun lastActionCount(context: Context): Int? = try {
            JarvisConfig(context.applicationContext).lastActionCount.takeIf { it > 0 }
        } catch (t: Throwable) {
            null
        }
    }
}
