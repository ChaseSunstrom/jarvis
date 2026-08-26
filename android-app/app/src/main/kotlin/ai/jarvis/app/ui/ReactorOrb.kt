package ai.jarvis.app.ui

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import ai.jarvis.app.ui.theme.JarvisTokens
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * The arc reactor, as an instrument. One implementation, drawn by every
 * surface that shows it.
 *
 * Reactor II (`docs/design/c2-reactor.html`) draws the reactor flat: a
 * graduated bezel, a ring of blades with a glint walking round, a
 * counter-rotating dashed coil, a level arc that carries the live value, and a
 * dark lens with two iris arcs and one hot dot. The web console draws exactly
 * that in `jarvis-web/src/lib/ui/Reactor.svelte`; this is the same object on
 * a Canvas, so the phone and the browser show one instrument and not two
 * cousins of it. The previous renderer here was a lit glass ball — a
 * considerable piece of work, and the wrong object for the chosen direction.
 *
 * ## The contract
 *
 * Every radius is a fraction of [Frame.radius] — the bezel's radius, `R` —
 * and every count, gap and fraction is `tests/contracts/reactor_geometry.json`,
 * typed here as the constants the file names (`android-app/tools/
 * reactor_orb_test.py` holds this file to it, and `jarvis-web/src/lib/ui/
 * reactor.test.ts` holds the web to the same file). The clock is
 * `motion.reactor.*` from `design/tokens.json`, generated into
 * [JarvisTokens.Motion.Reactor]; the palette is `color.orb.*`, handed in by
 * the caller from [SiriPalette] so the five states are one table.
 *
 * ## What it draws, outside in
 *
 *  1. **[drawBezel]** — [TICKS] ticks round the rim, a long one every
 *     [LONG_TICK_EVERY]. Still: it is the gauge everything else turns against.
 *  2. **[drawBlades]** — [BLADES] arcs at [R_BLADE], turning once per
 *     `Reactor.BLADES`, with a glint walking round once per `Reactor.GLINT`:
 *     each blade brightens for a moment in turn, which is what reads as a
 *     machine running rather than a drawing of one.
 *  3. **[drawCoil]** — a fine dashed ring at [R_COIL], counter-rotating on
 *     `Reactor.COIL`. Two rings turning opposite ways read as an assembly;
 *     the same way, as a disc.
 *  4. **[drawLevel]** — the arc at [R_LEVEL] in the live colour: the
 *     microphone's amplitude while listening, the player's while speaking, a
 *     slow breath of [IDLE_BREATH_LEVEL] while idle. This is the one place the
 *     reactor carries information rather than character.
 *  5. **[drawLens]** — the dark lens at [R_CORE] with a radial fall-off to the
 *     live colour at its rim, two iris arcs on their own periods, the fine
 *     dashed think ring that turns only while the model is thinking, and the
 *     hot dot with its halo. The whole lens breathes on `Reactor.BREATHE`.
 *
 * Nothing here glows the screen. The glow budget is the dot's halo and the
 * level arc's soft edge; the previous halo outside the ball is gone, which is
 * also why [OUTER_FACTOR] is 1: nothing is drawn past the bezel.
 *
 * ## The boot
 *
 * The four [Frame.ringScale] / [Frame.ringAlpha] slots the power-on sequence
 * drives are the instrument's layers, inner to outer: the lens
 * ([RING_INNER_RIM]), the level arc ([RING_MID_DASH]), the coil
 * ([RING_FINE_DASH]) and the blades with their bezel ([RING_GAUGE]). The names
 * are the ones `BootTimeline` and its mirror have always used, so the boot
 * needed no retuning to assemble a different reactor.
 */
class ReactorOrb(private val density: Float) {

    /**
     * One frame's worth of inputs. Mutable and reused: this is written 60 times
     * a second on the main thread and a fresh object per frame is pure garbage.
     */
    class Frame {
        var cx = 0f
        var cy = 0f

        /** The bezel's radius, `R`. Everything else is a fraction of it. */
        var radius = 0f

        /** Master opacity. */
        var alpha = 1f

        /** Smoothed level, 0..1, already gained: the arc's fill. */
        var level = 0f

        /**
         * The reactor's clock, in seconds, free-running. Every rotation and
         * the breath are read off it against the token periods, so a state
         * change never jumps a ring and two views started at different
         * moments still turn at one speed.
         */
        var time = 0f

        /** Kept for callers that still integrate it; the instrument reads [time]. */
        var phase = 0f

        /** Kept for callers that still integrate it; the instrument reads [time]. */
        var spinDeg = 0f

        /** The state's palette: `[live, deep, …]` — blob-0 and blob-1 of `color.orb.*`. */
        var blobs: IntArray = SiriPalette.blobs(SiriPalette.Tone.LISTENING)

        /** The hot dot: `core` of `color.orb.*`. */
        var core: Int = SiriPalette.core(SiriPalette.Tone.LISTENING)

        /** The rim's colour. Equals `blobs[0]` for every tone; kept for callers. */
        var rim: Int = SiriPalette.rim(SiriPalette.Tone.LISTENING)

        /** Nothing is happening: the level arc breathes instead of following a voice. */
        var idle = false

        /** How solid the lens rim is. Lifted while listening or speaking. */
        var rimAlpha = RIM_ALPHA_REST

        /** The THINKING-only inner ring. */
        var turbulence = false

        /**
         * Largest radius anything may reach. A View's canvas is clipped to its
         * bounds by its parent; nothing here exceeds the bezel, but the cap
         * stays so a caller's budget cannot be silently exceeded by a retune.
         */
        var maxRadius = Float.MAX_VALUE

        /** Per-layer arrival, inner to outer. 1 outside the boot sequence. */
        val ringScale = FloatArray(RING_COUNT) { 1f }
        val ringAlpha = FloatArray(RING_COUNT) { 1f }

        /** Hand the layers back to their resting values. */
        fun settleRings() {
            for (i in 0 until RING_COUNT) {
                ringScale[i] = 1f
                ringAlpha[i] = 1f
            }
        }
    }

    // --- paint ---------------------------------------------------------------

    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
    }
    private val round = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val arc = RectF()

    private fun dp(v: Float) = v * density

    /** A period from the tokens, in seconds. */
    private fun seconds(ms: Int): Float = ms / 1000f

    private val bladesPeriod = seconds(JarvisTokens.Motion.Reactor.BLADES)
    private val coilPeriod = seconds(JarvisTokens.Motion.Reactor.COIL)
    private val irisAPeriod = seconds(JarvisTokens.Motion.Reactor.IRIS_A)
    private val irisBPeriod = seconds(JarvisTokens.Motion.Reactor.IRIS_B)
    private val breathePeriod = seconds(JarvisTokens.Motion.Reactor.BREATHE)
    private val glintPeriod = seconds(JarvisTokens.Motion.Reactor.GLINT)
    private val levelPeriod = seconds(JarvisTokens.Motion.Reactor.LEVEL)
    private val thinkPeriod = seconds(JarvisTokens.Motion.Reactor.THINK)

    // --- the whole instrument --------------------------------------------------

    fun draw(canvas: Canvas, f: Frame) {
        val r = min(f.radius, f.maxRadius)
        if (r < MIN_DRAW_PX || f.alpha <= 0f) return
        drawBezel(canvas, f, r)
        drawBlades(canvas, f, r)
        drawCoil(canvas, f, r)
        drawLevel(canvas, f, r)
        drawLens(canvas, f, r)
    }

    /** Where a layer is between "not yet" and "arrived", for the boot. */
    private fun layerAlpha(f: Frame, layer: Int): Float =
        (f.alpha * f.ringAlpha[layer]).coerceIn(0f, 1f)

    /** 0..1 in, 0..1 out: a cosine breath, so the turnarounds are soft. */
    private fun breath(t: Float, period: Float): Float =
        0.5f - 0.5f * cos(TWO_PI * t / period)

    // --- the bezel ---------------------------------------------------------------

    private fun drawBezel(canvas: Canvas, f: Frame, r: Float) {
        val a = layerAlpha(f, RING_GAUGE)
        val rr = r * f.ringScale[RING_GAUGE]
        if (a <= 0f || rr < MIN_DRAW_PX) return
        for (i in 0 until TICKS) {
            val long = i % LONG_TICK_EVERY == 0
            val angle = i * TWO_PI / TICKS - HALF_PI
            val inner = rr - rr * (if (long) LONG_TICK_LEN else SHORT_TICK_LEN)
            line.strokeWidth = dp(if (long) LONG_TICK_WIDTH_DP else TICK_WIDTH_DP)
            line.color = withAlpha(
                if (long) JarvisTokens.Color.TEXT_DIM else JarvisTokens.Color.TICK,
                a,
            )
            canvas.drawLine(
                f.cx + inner * cos(angle), f.cy + inner * sin(angle),
                f.cx + rr * cos(angle), f.cy + rr * sin(angle),
                line,
            )
        }
    }

    // --- the blades ------------------------------------------------------------------

    /**
     * The glint: how lit blade [i] is at clock [t], 0..1.
     *
     * One blade after another, a full turn per `Reactor.GLINT`: each blade
     * comes up to the live colour over the first [GLINT_PEAK] of its slot and
     * fades back to the line colour by [GLINT_TAIL] — the web's keyframes,
     * restated as a function of the same clock.
     */
    private fun glint(t: Float, i: Int): Float {
        val p = ((t / glintPeriod - i.toFloat() / BLADES) % 1f + 1f) % 1f
        return when {
            p < GLINT_PEAK -> p / GLINT_PEAK
            p < GLINT_TAIL -> 1f - (p - GLINT_PEAK) / (GLINT_TAIL - GLINT_PEAK)
            else -> 0f
        }
    }

    private fun drawBlades(canvas: Canvas, f: Frame, r: Float) {
        val a = layerAlpha(f, RING_GAUGE)
        val rb = r * R_BLADE * f.ringScale[RING_GAUGE]
        if (a <= 0f || rb < MIN_DRAW_PX) return
        val live = f.blobs[0]
        val step = 360f / BLADES
        val sweep = step - BLADE_GAP_DEG
        // The web sizes the stroke against the drawing's own size: `size / 52`
        // with a floor. Here the drawing is 2R across.
        line.strokeWidth = max(dp(BLADE_WIDTH_MIN), 2f * r / BLADE_WIDTH_RATIO)
        arc.set(f.cx - rb, f.cy - rb, f.cx + rb, f.cy + rb)
        val turned = 360f * (f.time / bladesPeriod)
        canvas.save()
        canvas.rotate(turned, f.cx, f.cy)
        for (i in 0 until BLADES) {
            // Every third blade is quieter, as on the web, so the ring has a
            // rhythm rather than being one grey band with gaps in it.
            val rest = if (i % 3 == 2) JarvisTokens.Color.LINE_SOFT else JarvisTokens.Color.LINE
            line.color = withAlpha(mix(rest, live, glint(f.time, i)), a)
            canvas.drawArc(arc, i * step - 90f, sweep, false, line)
        }
        canvas.restore()
    }

    // --- the coil ---------------------------------------------------------------------

    private fun drawCoil(canvas: Canvas, f: Frame, r: Float) {
        val a = layerAlpha(f, RING_FINE_DASH)
        val rc = r * R_COIL * f.ringScale[RING_FINE_DASH]
        if (a <= 0f || rc < MIN_DRAW_PX) return
        canvas.save()
        canvas.rotate(-360f * (f.time / coilPeriod), f.cx, f.cy)
        drawDashedRing(
            canvas, f.cx, f.cy, rc,
            dp(COIL_DASH_ON_DP), dp(COIL_DASH_OFF_DP), dp(COIL_WIDTH_DP),
            withAlpha(JarvisTokens.Color.TICK, a),
        )
        canvas.restore()
    }

    // --- the level -------------------------------------------------------------------

    private fun drawLevel(canvas: Canvas, f: Frame, r: Float) {
        val a = layerAlpha(f, RING_MID_DASH)
        val rl = r * R_LEVEL * f.ringScale[RING_MID_DASH]
        if (a <= 0f || rl < MIN_DRAW_PX) return
        val live = f.blobs[0]
        arc.set(f.cx - rl, f.cy - rl, f.cx + rl, f.cy + rl)

        line.strokeWidth = dp(LEVEL_WIDTH)
        line.color = withAlpha(JarvisTokens.Color.LINE_SOFT, a)
        canvas.drawCircle(f.cx, f.cy, rl, line)

        // Idle breathes; anything else is the voice, and the voice is not
        // smoothed here — the caller already did that.
        val level = if (f.idle) {
            f.level + IDLE_BREATH_LEVEL * breath(f.time, levelPeriod)
        } else {
            f.level
        }.coerceIn(0f, 1f)
        val sweep = 360f * level
        if (sweep <= 0f) return

        // The soft edge first, wider and faint, then the arc itself: the
        // web's `drop-shadow(0 0 4px glow)` without a blur filter, which a
        // software canvas would pay for sixty times a second.
        round.strokeWidth = dp(LEVEL_WIDTH) * LEVEL_GLOW_WIDTH_RATIO
        round.color = withAlpha(live, a * LEVEL_GLOW_ALPHA)
        canvas.drawArc(arc, -90f, sweep, false, round)
        round.strokeWidth = dp(LEVEL_WIDTH)
        round.color = withAlpha(live, a)
        canvas.drawArc(arc, -90f, sweep, false, round)
    }

    // --- the lens --------------------------------------------------------------------

    private fun drawLens(canvas: Canvas, f: Frame, r: Float) {
        val a = layerAlpha(f, RING_INNER_RIM)
        val scale = 1f + (BREATHE_SCALE - 1f) * breath(f.time, breathePeriod)
        val rc = r * R_CORE * f.ringScale[RING_INNER_RIM] * scale
        if (a <= 0f || rc < MIN_DRAW_PX) return
        val live = f.blobs[0]
        val deep = if (f.blobs.size > 1) f.blobs[1] else live

        // The dark lens: the ground, then one step up, then the live colour at
        // the very rim. Nearly opaque — see SUBSTRATE_ALPHA — so the overlay
        // reads as an object over another app rather than a tint on it.
        fill.shader = RadialGradient(
            f.cx, f.cy, rc,
            intArrayOf(
                withAlpha(JarvisTokens.Color.BG, a * SUBSTRATE_ALPHA),
                withAlpha(JarvisTokens.Color.BG_RAISED, a * SUBSTRATE_ALPHA),
                withAlpha(deep, a * LENS_DEEP_STOP_ALPHA),
                withAlpha(live, a * LENS_LIVE_STOP_ALPHA),
            ),
            LENS_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, rc, fill)
        fill.shader = null

        line.strokeWidth = dp(RIM_WIDTH_DP)
        line.color = withAlpha(live, a * f.rimAlpha)
        canvas.drawCircle(f.cx, f.cy, rc, line)

        // Two iris arcs on two periods, turning opposite ways, so the lens
        // never phase-locks into a still picture.
        round.strokeWidth = dp(IRIS_WIDTH_DP)
        val ra = rc * IRIS_A_R
        arc.set(f.cx - ra, f.cy - ra, f.cx + ra, f.cy + ra)
        round.color = withAlpha(deep, a * IRIS_A_ALPHA)
        canvas.drawArc(arc, -90f + 360f * (f.time / irisAPeriod), 180f * IRIS_A_SWEEP, false, round)
        val rb = rc * IRIS_B_R
        arc.set(f.cx - rb, f.cy - rb, f.cx + rb, f.cy + rb)
        round.color = withAlpha(JarvisTokens.Color.TEXT_DIM, a * IRIS_B_ALPHA)
        canvas.drawArc(arc, 90f - 360f * (f.time / irisBPeriod), 180f * IRIS_B_SWEEP, false, round)

        // The think ring: the fastest thing on the instrument, and only while
        // the model is thinking — which is how thinking reads as a different
        // state from across a room, not only as a different colour.
        if (f.turbulence) {
            val rt = r * R_THINK * f.ringScale[RING_INNER_RIM] * scale
            canvas.save()
            canvas.rotate(360f * (f.time / thinkPeriod), f.cx, f.cy)
            drawDashedRing(
                canvas, f.cx, f.cy, rt,
                dp(THINK_DASH_ON_DP), dp(THINK_DASH_OFF_DP), dp(THINK_WIDTH_DP),
                withAlpha(live, a * THINK_ALPHA),
            )
            canvas.restore()
        }

        drawDot(canvas, f, r, a, live)
    }

    /** The hot dot and its halo: the one lit point the eye settles on. */
    private fun drawDot(canvas: Canvas, f: Frame, r: Float, a: Float, live: Int) {
        val glow = max(dp(DOT_GLOW_MIN), 2f * r / DOT_GLOW_RATIO)
        val dot = max(dp(DOT_MIN), 2f * r / DOT_RATIO)
        if (glow < MIN_DRAW_PX || dot < MIN_DRAW_PX) return
        // The halo keeps time with the voice: brighter as the level rises,
        // which is the web's pulse on the same element while speaking.
        val halo = a * (DOT_GLOW_ALPHA_REST + DOT_GLOW_ALPHA_GAIN * f.level).coerceIn(0f, 1f)
        fill.shader = RadialGradient(
            f.cx, f.cy, glow,
            intArrayOf(withAlpha(live, halo), withAlpha(live, halo * 0.5f), withAlpha(live, 0f)),
            DOT_GLOW_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, glow, fill)
        fill.shader = null
        fill.color = withAlpha(f.core, a)
        canvas.drawCircle(f.cx, f.cy, dot, fill)
    }

    // --- helpers ----------------------------------------------------------------

    /**
     * A ring of dashes. `on`/`off` are the dash and the gap in px; a dash
     * pattern whose intervals sum to zero is undefined in Skia, and the boot
     * asks for a radius of exactly zero on its first frame, so both are
     * guarded rather than trusted.
     */
    private fun drawDashedRing(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        on: Float,
        off: Float,
        width: Float,
        color: Int,
    ) {
        if (radius < MIN_DRAW_PX) return
        val seg = on + off
        if (seg <= 0f) return
        line.strokeWidth = width
        line.color = color
        line.pathEffect = DashPathEffect(floatArrayOf(on, off), 0f)
        canvas.drawCircle(cx, cy, radius, line)
        line.pathEffect = null
    }

    /** [color] at [fraction] of its opacity, on top of whatever alpha it had. */
    private fun withAlpha(color: Int, fraction: Float): Int {
        val alpha = (Color.alpha(color) * fraction.coerceIn(0f, 1f)).toInt().coerceIn(0, 255)
        return Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))
    }

    /** Linear blend of two colours' channels. */
    private fun mix(from: Int, to: Int, t: Float): Int {
        val k = t.coerceIn(0f, 1f)
        fun ch(a: Int, b: Int) = (a + (b - a) * k).toInt().coerceIn(0, 255)
        return Color.argb(
            ch(Color.alpha(from), Color.alpha(to)),
            ch(Color.red(from), Color.red(to)),
            ch(Color.green(from), Color.green(to)),
            ch(Color.blue(from), Color.blue(to)),
        )
    }

    companion object {
        /**
         * The layers, inner to outer, as the boot sequence brings them in. The
         * names are the ones the previous reactor's rings had and the boot
         * timeline still uses; what they index now is the instrument's layers.
         */
        const val RING_INNER_RIM = 0
        const val RING_MID_DASH = 1
        const val RING_FINE_DASH = 2
        const val RING_GAUGE = 3
        const val RING_COUNT = 4

        // --- geometry: tests/contracts/reactor_geometry.json ------------------
        //
        // Every name below is a key of the contract in upper case, and
        // `android-app/tools/reactor_orb_test.py` refuses this file drifting
        // from it. Fractions are of R, the bezel's radius.

        const val TICKS = 120
        const val LONG_TICK_EVERY = 10
        const val LONG_TICK_LEN = 0.07f
        const val SHORT_TICK_LEN = 0.032f
        const val BLADES = 36
        const val BLADE_GAP_DEG = 3f
        const val R_BLADE = 0.85f
        const val BLADE_WIDTH_RATIO = 52
        const val BLADE_WIDTH_MIN = 3f
        const val R_COIL = 0.74f
        const val R_LEVEL = 0.65f
        const val LEVEL_WIDTH = 3f
        const val R_CORE = 0.56f
        const val IRIS_A_R = 0.9f
        const val IRIS_A_SWEEP = 1.25f
        const val IRIS_B_R = 0.82f
        const val IRIS_B_SWEEP = 1.1f
        const val R_THINK = 0.47f
        const val DOT_RATIO = 70
        const val DOT_MIN = 2.5f
        const val DOT_GLOW_RATIO = 34
        const val DOT_GLOW_MIN = 4f
        const val BREATHE_SCALE = 1.025f
        const val IDLE_BREATH_LEVEL = 0.14f

        /**
         * The outermost primitive, as a multiple of R.
         *
         * Callers size R so THIS still fits inside the view at the largest
         * scale they can ask for. The instrument draws nothing past its bezel,
         * so it is exactly 1 — and anything drawn beyond the bezel must raise
         * it, which is the whole point of stating it here.
         */
        const val OUTER_FACTOR = 1.0f

        /**
         * How opaque the lens's ground is. Nearly: on the overlay window this
         * is what makes the reactor an object over another app rather than a
         * tint on it, which is the "too transparent" report in one number.
         */
        const val SUBSTRATE_ALPHA = 0.90f

        // --- strokes and dashes, in dp --------------------------------------------

        const val TICK_WIDTH_DP = 1f
        const val LONG_TICK_WIDTH_DP = 1.2f
        const val COIL_WIDTH_DP = 1f
        const val COIL_DASH_ON_DP = 2f
        const val COIL_DASH_OFF_DP = 6f
        const val IRIS_WIDTH_DP = 1f
        const val RIM_WIDTH_DP = 1f
        const val THINK_WIDTH_DP = 1f
        const val THINK_DASH_ON_DP = 1f
        const val THINK_DASH_OFF_DP = 5f

        // --- opacities ----------------------------------------------------------------

        const val RIM_ALPHA_REST = 0.55f

        /** Listening and speaking lift the rim; the web does the same. */
        const val RIM_ALPHA_LIT = 0.85f
        const val IRIS_A_ALPHA = 0.7f
        const val IRIS_B_ALPHA = 0.6f
        const val THINK_ALPHA = 0.55f
        const val LENS_DEEP_STOP_ALPHA = 0.55f
        const val LENS_LIVE_STOP_ALPHA = 0.75f
        const val DOT_GLOW_ALPHA_REST = 0.45f
        const val DOT_GLOW_ALPHA_GAIN = 0.4f
        const val LEVEL_GLOW_ALPHA = 0.28f
        const val LEVEL_GLOW_WIDTH_RATIO = 3f

        /** The glint's shape over one blade's slot: up in the first 3%, gone by 11%. */
        const val GLINT_PEAK = 0.03f
        const val GLINT_TAIL = 0.11f

        private val LENS_STOPS = floatArrayOf(0f, 0.82f, 0.96f, 1f)
        private val DOT_GLOW_STOPS = floatArrayOf(0f, 0.5f, 1f)

        /** `(2 * PI).toFloat()`, written out because `const val` wants a literal. */
        const val TWO_PI = 6.2831855f
        const val HALF_PI = 1.5707964f
        const val RAD_PER_DEG = 0.017453292f
        const val DEG_PER_RAD = 57.29578f

        /**
         * Below this radius (in px) a shape is not worth drawing, and a shader
         * built for it is worth a crash: `RadialGradient` rejects a radius of
         * zero outright. The boot sequence starts the lens at exactly zero, so
         * this is a live path, not a theoretical one.
         */
        const val MIN_DRAW_PX = 0.5f
    }
}
