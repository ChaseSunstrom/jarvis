package ai.jarvis.app.ui

import kotlin.math.pow

/**
 * PURE LOGIC — no Android imports, no views, no clock. Every number the Jarvis
 * power-on sequence needs, as a function of one variable: elapsed milliseconds.
 *
 * [JarvisBootAnimation] owns a single `ValueAnimator` and asks this object what
 * to draw at time `t`. Nothing else keeps state, nothing schedules itself, and
 * there is no chain of `postDelayed` calls to fall out of sync — which is the
 * whole reason the timeline lives here instead of inside the view. It also
 * means the timeline is testable off-device (`tools/boot_timeline_test.py`
 * mirrors this file and asserts the two agree).
 *
 * The sequence, ~1.4 s end to end:
 *
 * ```
 *    0 ms  black; one hairline scan line sweeps top -> bottom
 *  120 ms  the reactor core ignites from a point, with a bloom flare
 *  300 ms  rings materialise outward, one at a time, each overshooting
 *  600 ms  "J A R V I S" resolves in, letter by letter, spacing settling
 *  850 ms  three system-check lines type on in monospace
 * 1200 ms  everything but the orb fades; the home UI fades up around it
 * ```
 *
 * The six [Stage]s tile `[0, TOTAL_MS]` exactly — no gap, no overlap — so
 * "which stage is this" always has one answer. Individual *elements* do
 * overlap (the scan line is still fading while the core ignites); that overlap
 * is expressed by the per-element functions, not by the stage partition.
 */
object BootTimeline {

    /** Full sequence length at animation scale 1. */
    const val TOTAL_MS = 1400L

    // --- stage boundaries --------------------------------------------------

    const val SCAN_START_MS = 0L
    const val IGNITE_START_MS = 120L
    const val RINGS_START_MS = 300L
    const val WORDMARK_START_MS = 600L
    const val CHECKS_START_MS = 850L
    const val HANDOFF_START_MS = 1200L

    /** The six stages, in order, tiling the whole timeline. */
    enum class Stage(val startMs: Long, val endMs: Long) {
        /** Black, with a single hairline scan sweeping down the screen. */
        SCAN(SCAN_START_MS, IGNITE_START_MS),

        /** The core ignites from a point and blooms. */
        IGNITE(IGNITE_START_MS, RINGS_START_MS),

        /** Four rings materialise outward, one at a time. */
        RINGS(RINGS_START_MS, WORDMARK_START_MS),

        /** The wordmark resolves in, letter by letter. */
        WORDMARK(WORDMARK_START_MS, CHECKS_START_MS),

        /** Three system-check lines type on. */
        CHECKS(CHECKS_START_MS, HANDOFF_START_MS),

        /** Chrome dissolves, the orb settles, the home UI fades up. */
        HANDOFF(HANDOFF_START_MS, TOTAL_MS),
    }

    // --- element timing ----------------------------------------------------

    /** The scan line finishes its sweep exactly as the core ignites. */
    const val SCAN_FADE_MS = 100L

    /** Core scale-in, from a point to full size. */
    const val CORE_RISE_MS = 180L

    /** The core's own fade-up: quicker than its scale, so it reads as ignition. */
    const val CORE_FADE_MS = 90L

    /** Bloom flare: up and back down, peaking halfway. */
    const val FLARE_MS = 200L

    /** Inner rim, dashed mid ring, fine dashes, gauge ticks. */
    const val RING_COUNT = 4

    /** Delay between one ring starting and the next. */
    const val RING_STAGGER_MS = 60L

    /** How long one ring takes to arrive (including its overshoot). */
    const val RING_MS = 120L

    /** How far past its resting size a ring swings. Android's OvershootInterpolator tension. */
    const val RING_TENSION = 2.2f

    /** J A R V I S. */
    const val LETTER_COUNT = 6

    const val LETTER_STAGGER_MS = 26L
    const val LETTER_MS = 120L

    /** Letter spacing (em) at the start of the resolve, and at rest. */
    const val LETTER_SPACING_START = 0.90f
    const val LETTER_SPACING_END = 0.55f

    /** Blur radius, in dp, a letter starts with before it sharpens. */
    const val LETTER_BLUR_DP = 7.0f

    const val CHECK_LINE_COUNT = 3
    const val CHECK_STAGGER_MS = 90L

    /** Time for one check line to type on, whatever its length. */
    const val CHECK_TYPE_MS = 170L

    /** Chrome fade at the handoff. */
    const val HANDOFF_FADE_MS = 140L

    /** The home UI starts fading up slightly after the chrome starts leaving. */
    const val HOME_FADE_DELAY_MS = 60L
    const val HOME_FADE_MS = 140L

    // --- interpolators (mirrors of the platform ones) -----------------------

    /** `android.view.animation.DecelerateInterpolator`: `1 - (1-t)^(2f)`. */
    fun decelerate(t: Float, factor: Float = 1f): Float {
        val p = clamp01(t)
        return 1f - (1f - p).toDouble().pow((2f * factor).toDouble()).toFloat()
    }

    /** `android.view.animation.AccelerateInterpolator`: `t^(2f)`. */
    fun accelerate(t: Float, factor: Float = 1f): Float {
        val p = clamp01(t)
        return p.toDouble().pow((2f * factor).toDouble()).toFloat()
    }

    /**
     * `android.view.animation.OvershootInterpolator`. Exceeds 1 near the end
     * and settles back — callers must expect a value above 1.
     */
    fun overshoot(t: Float, tension: Float = RING_TENSION): Float {
        val p = clamp01(t) - 1f
        return p * p * ((tension + 1f) * p + tension) + 1f
    }

    fun clamp01(v: Float): Float = if (v < 0f) 0f else if (v > 1f) 1f else v

    /** Progress through a window, clamped. A zero-length window is instantly done. */
    fun window(tMs: Long, startMs: Long, durationMs: Long): Float {
        if (durationMs <= 0L) return if (tMs >= startMs) 1f else 0f
        return clamp01((tMs - startMs).toFloat() / durationMs.toFloat())
    }

    // --- stages -------------------------------------------------------------

    /** Which stage `tMs` falls in. Before 0 is [Stage.SCAN]; at or past the end, [Stage.HANDOFF]. */
    fun stageAt(tMs: Long): Stage {
        for (stage in Stage.values()) {
            if (tMs < stage.endMs) return stage
        }
        return Stage.HANDOFF
    }

    /** How far through [stage] `tMs` is, clamped to 0..1. */
    fun stageProgress(tMs: Long, stage: Stage): Float =
        window(tMs, stage.startMs, stage.endMs - stage.startMs)

    // --- elements -----------------------------------------------------------

    /** Scan line position, 0 = top edge, 1 = bottom edge. */
    fun scanY(tMs: Long): Float = decelerate(window(tMs, SCAN_START_MS, IGNITE_START_MS), 0.7f)

    /** Scan line opacity: solid through the sweep, gone shortly after ignition. */
    fun scanAlpha(tMs: Long): Float =
        if (tMs <= IGNITE_START_MS) 1f
        else 1f - window(tMs, IGNITE_START_MS, SCAN_FADE_MS)

    /** Core radius as a fraction of its resting radius. Starts at a point. */
    fun coreScale(tMs: Long): Float {
        if (tMs < IGNITE_START_MS) return 0f
        return decelerate(window(tMs, IGNITE_START_MS, CORE_RISE_MS), 1.8f)
    }

    /** Core opacity. Rises faster than the scale so it reads as a spark, not a balloon. */
    fun coreAlpha(tMs: Long): Float {
        if (tMs < IGNITE_START_MS) return 0f
        return decelerate(window(tMs, IGNITE_START_MS, CORE_FADE_MS), 1.4f)
    }

    /** The one-shot bloom around the ignition, peaking mid-flare. */
    fun flareAlpha(tMs: Long): Float {
        if (tMs < IGNITE_START_MS) return 0f
        val p = window(tMs, IGNITE_START_MS, FLARE_MS)
        if (p >= 1f) return 0f
        return 4f * p * (1f - p)
    }

    /** When ring [index] starts arriving. */
    fun ringStartMs(index: Int): Long = RINGS_START_MS + index * RING_STAGGER_MS

    /**
     * Ring [index] materialising, 0 = absent, 1 = at rest. Briefly exceeds 1:
     * that is the overshoot, and the view is expected to scale the radius by it.
     */
    fun ringReveal(tMs: Long, index: Int): Float {
        val p = window(tMs, ringStartMs(index), RING_MS)
        if (p <= 0f) return 0f
        if (p >= 1f) return 1f
        return overshoot(p)
    }

    /** Ring opacity — plain fade, no overshoot, so the ring never flickers past full. */
    fun ringAlpha(tMs: Long, index: Int): Float =
        decelerate(window(tMs, ringStartMs(index), RING_MS), 1.2f)

    fun letterStartMs(index: Int): Long = WORDMARK_START_MS + index * LETTER_STAGGER_MS

    /** Opacity of letter [index] of the wordmark. */
    fun letterAlpha(tMs: Long, index: Int): Float =
        decelerate(window(tMs, letterStartMs(index), LETTER_MS), 1.3f)

    /** Blur radius in dp for letter [index]: wide and soft, sharpening to zero. */
    fun letterBlur(tMs: Long, index: Int): Float =
        LETTER_BLUR_DP * (1f - decelerate(window(tMs, letterStartMs(index), LETTER_MS), 1.3f))

    /**
     * Wordmark letter spacing in em. Settles from [LETTER_SPACING_START] to
     * [LETTER_SPACING_END] across the whole wordmark stage, so the word closes
     * up as the last letters land.
     */
    fun letterSpacing(tMs: Long): Float {
        val span = letterStartMs(LETTER_COUNT - 1) + LETTER_MS - WORDMARK_START_MS
        val p = decelerate(window(tMs, WORDMARK_START_MS, span), 1.6f)
        return LETTER_SPACING_START + (LETTER_SPACING_END - LETTER_SPACING_START) * p
    }

    fun checkStartMs(index: Int): Long = CHECKS_START_MS + index * CHECK_STAGGER_MS

    /** How much of check line [index] has been typed, 0..1. */
    fun checkProgress(tMs: Long, index: Int): Float =
        window(tMs, checkStartMs(index), CHECK_TYPE_MS)

    /** Characters of a [length]-character check line visible at `tMs`. */
    fun typedChars(tMs: Long, index: Int, length: Int): Int {
        if (length <= 0) return 0
        val n = (checkProgress(tMs, index) * length).toInt()
        return if (n > length) length else n
    }

    /** Opacity of everything that is not the orb. Falls to 0 across the handoff. */
    fun chromeAlpha(tMs: Long): Float =
        1f - decelerate(window(tMs, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2f)

    /** Opacity of the home UI fading up around the settling orb. */
    fun homeAlpha(tMs: Long): Float =
        decelerate(window(tMs, HANDOFF_START_MS + HOME_FADE_DELAY_MS, HOME_FADE_MS), 1.2f)

    /**
     * Opacity of the ORB's own chrome — its brackets, wordmark and caption —
     * across the handoff. The exact complement of [chromeAlpha], because it
     * replaces what [chromeAlpha] is removing.
     *
     * It used to be [homeAlpha], and that was a visible defect at the end of
     * the sequence. The boot overlay and the orb draw the same JARVIS wordmark,
     * in the same colour, on the same baseline, so those two fades are a
     * crossfade of ONE object; [homeAlpha] deliberately starts
     * [HOME_FADE_DELAY_MS] late so the home CONTROLS arrive after the chrome
     * has begun leaving, which left a hole in the middle of that crossfade.
     * Combined opacity bottomed out near 0.29 around t = 1263 ms: the wordmark
     * dipped almost out and came back, half a breath before the orb settled.
     *
     * The controls keep [homeAlpha] — they are not crossfading with anything.
     */
    fun orbChromeAlpha(tMs: Long): Float =
        decelerate(window(tMs, HANDOFF_START_MS, HANDOFF_FADE_MS), 1.2f)

    // --- motion settings ----------------------------------------------------

    /**
     * True when the sequence must not play at all: the user has turned
     * animations off (`Settings.Global.ANIMATOR_DURATION_SCALE` is 0) or asked
     * for reduced motion. Trapping someone in an animation they disabled is not
     * a flourish, it is a bug.
     */
    fun shouldSkip(animatorScale: Float, reducedMotion: Boolean): Boolean =
        reducedMotion || animatorScale <= 0f || animatorScale.isNaN()

    /**
     * Sequence length honouring the system animation scale. 0 means "jump
     * straight to the end state". A user who slowed animations down gets a
     * slower boot, capped so a 10x developer setting cannot hang the launcher.
     */
    fun scaledDurationMs(animatorScale: Float, reducedMotion: Boolean = false): Long {
        if (shouldSkip(animatorScale, reducedMotion)) return 0L
        val scaled = (TOTAL_MS * animatorScale).toLong()
        return if (scaled > MAX_DURATION_MS) MAX_DURATION_MS else scaled
    }

    /** Upper bound on the sequence however far the user cranks the scale. */
    const val MAX_DURATION_MS = 4200L

    // --- end state ----------------------------------------------------------

    /**
     * Everything the view needs to draw the finished frame. `skip()` jumps
     * here; so does the natural end of the animation, which is what makes the
     * two paths indistinguishable on screen.
     */
    data class EndState(
        val scanAlpha: Float,
        val coreScale: Float,
        val coreAlpha: Float,
        val flareAlpha: Float,
        val ringReveal: List<Float>,
        val letterAlpha: List<Float>,
        val letterSpacing: Float,
        val checkProgress: List<Float>,
        val chromeAlpha: Float,
        val orbChromeAlpha: Float,
        val homeAlpha: Float,
    )

    /** The frame at [TOTAL_MS], computed from the same functions as every other frame. */
    fun endState(): EndState = stateAt(TOTAL_MS)

    /** The frame at `tMs`, as one value. Used by tests and by `skip()`. */
    fun stateAt(tMs: Long): EndState = EndState(
        scanAlpha = scanAlpha(tMs),
        coreScale = coreScale(tMs),
        coreAlpha = coreAlpha(tMs),
        flareAlpha = flareAlpha(tMs),
        ringReveal = (0 until RING_COUNT).map { ringReveal(tMs, it) },
        letterAlpha = (0 until LETTER_COUNT).map { letterAlpha(tMs, it) },
        letterSpacing = letterSpacing(tMs),
        checkProgress = (0 until CHECK_LINE_COUNT).map { checkProgress(tMs, it) },
        chromeAlpha = chromeAlpha(tMs),
        orbChromeAlpha = orbChromeAlpha(tMs),
        homeAlpha = homeAlpha(tMs),
    )

    // --- check lines --------------------------------------------------------

    /** The first two lines are always true by the time they are shown. */
    const val CHECK_CORE = "core online"
    const val CHECK_VOICE = "voice pipeline ready"

    /**
     * The lines to type, given the number of actions this device last
     * registered with the server. Null or 0 means we have never completed a
     * registration, and the third line is simply omitted rather than typing a
     * confident "0 actions ready" at someone who has not connected yet.
     *
     * It counts *actions*, not devices, because that is the only number of this
     * shape the phone actually learns: `jarvis/device/register` answers with
     * the size of the manifest the server accepted. The count is persisted by
     * [ai.jarvis.app.config.JarvisConfig.lastActionCount], written by the
     * command channel when a registration succeeds — a boot line with nothing
     * writing its input is a line that never appears.
     */
    fun checkLines(actionCount: Int?): List<String> {
        val lines = mutableListOf(CHECK_CORE, CHECK_VOICE)
        if (actionCount != null && actionCount > 0) {
            lines += if (actionCount == 1) "1 action ready" else "$actionCount actions ready"
        }
        return lines
    }
}
