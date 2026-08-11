package ai.jarvis.app.ui

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.SweepGradient
import kotlin.math.cos
import kotlin.math.sin

/**
 * The arc reactor. One implementation, drawn by every surface that shows it.
 *
 * There used to be two orbs, and the difference between them was the thing
 * people noticed first: [SiriOrbView] — the window that floats over other apps
 * — was three coloured blobs drifting inside a glowing ball, and [JarvisOrbView]
 * — the app's own screens — was rings, ticks and a radar sweep around a flat
 * disc. Two surfaces, two objects, and the report was exactly that: *"the
 * notification thing is different than the actual orb that spawns with the wake
 * word, why is this?"*
 *
 * They are one object now. This class is it, and both views own an instance
 * rather than a copy of the drawing code, so "they look the same" is structural
 * instead of a promise that two `onDraw`s will be retuned in step.
 *
 * ## What it draws, centre out
 *
 * Inside one additive layer, so overlapping colours brighten toward their sum
 * rather than covering one another:
 *
 *  1. **[drawSubstrate]** — a nearly opaque dark ball. Additive blending needs
 *     something to add TO; without it the orb is whatever is behind it, tinted,
 *     which is the "too transparent" report in its entirety.
 *  2. **[drawBlob] ×3** — the drifting colour field, from [SiriPalette]. Rates
 *     1 : 0.73 : 1.31 never return to the same arrangement, so it does not
 *     visibly loop.
 *  3. **[drawSpokes]** — the reactor's coils. Ten arc segments in an annulus,
 *     and the single element that most says "arc reactor" rather than "orb".
 *     They are inside the layer deliberately: the drifting blob colours *light*
 *     them, instead of a flat overprint that would read as a decal.
 *  4. **[drawCore]** — the hot centre.
 *  5. **[drawGlass]** — the specular highlight and the inner-edge shadow. This
 *     is what makes a flat circle read as a lit ball under a glass cover, and
 *     it is the whole of the "make it more 3D" ask: a self-luminous sphere has
 *     no terminator, so the depth has to come from the cover over it.
 *
 * Then, outside the layer, the instrument chrome: a fresnel rim, a rotating
 * dashed ring, counter-rotating fine dashes, a 72/12 gauge and a radar sweep.
 * Outside, because chrome screen-blended against the blob field washes out to
 * white wherever a blob passes under it.
 *
 * ## Geometry
 *
 * Every radius here is a multiple of the ball's radius, and [OUTER_FACTOR] is
 * the largest of them. Callers size the ball as
 * `half_the_view / (OUTER_FACTOR * maxScale)`, so a retuned ring cannot
 * silently push the outermost primitive past the view's own clip — which is
 * how the orb acquired a box the last time these numbers moved.
 *
 * Mirrored in `android-app/tools/reactor_orb_test.py`, together with the web
 * console's shader, which draws the same object with the same proportions.
 */
class ReactorOrb(private val density: Float) {

    /**
     * One frame's worth of inputs. Mutable and reused: this is written 60 times
     * a second on the main thread and a fresh object per frame is pure garbage.
     */
    class Frame {
        var cx = 0f
        var cy = 0f

        /** Radius of the glowing ball. Everything else is a multiple of it. */
        var radius = 0f

        /** Master opacity. */
        var alpha = 1f

        /** Smoothed microphone level, 0..1, already gained. */
        var level = 0f

        /** Free-running orbit phase in radians. Drives the blobs. */
        var phase = 0f

        /** Chrome rotation in degrees. Free-running, so a state change never jumps it. */
        var spinDeg = 0f

        var blobs: IntArray = SiriPalette.blobs(SiriPalette.Tone.LISTENING)
        var core: Int = SiriPalette.core(SiriPalette.Tone.LISTENING)
        var rim: Int = SiriPalette.rim(SiriPalette.Tone.LISTENING)

        /** The bloom outside the ball. Off for a host that supplies its own. */
        var halo = true

        /** The rings, ticks and sweep. */
        var chrome = true

        /** The THINKING-only wobbling band. */
        var turbulence = false

        /**
         * Largest radius the halo may reach. A View's canvas is clipped to its
         * bounds by its parent, and the halo is the only thing here that can
         * exceed them — uncapped, a loud voice pushed it past the edge and the
         * clip turned the glow into a bright SQUARE, visible only while somebody
         * was talking.
         */
        var maxRadius = Float.MAX_VALUE

        /** Per-ring arrival, outward. 1 outside the boot sequence. */
        val ringScale = FloatArray(RING_COUNT) { 1f }
        val ringAlpha = FloatArray(RING_COUNT) { 1f }

        /** Hand the rings back to their resting values. */
        fun settleRings() {
            for (i in 0 until RING_COUNT) {
                ringScale[i] = 1f
                ringAlpha[i] = 1f
            }
        }
    }

    // --- paint ---------------------------------------------------------------

    /** Everything inside the layer that ADDS light. */
    private val additive = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        xfermode = PorterDuffXfermode(PorterDuff.Mode.SCREEN)
    }

    /** Everything inside the layer that does not: the ground, and the glass. */
    private val plain = Paint(Paint.ANTI_ALIAS_FLAG)

    private val strokeAdditive = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
        xfermode = PorterDuffXfermode(PorterDuff.Mode.SCREEN)
    }

    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val sweepPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

    private val arcRect = RectF()
    private val annulus = Path()
    private val sweepMatrix = Matrix()

    private fun dp(v: Float) = v * density

    // --- the whole object ------------------------------------------------------

    fun draw(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX || f.alpha <= 0f) return

        if (f.halo) drawHalo(canvas, f)

        // Bounded to the ball rather than to the view. `saveLayer(null, null)`
        // allocates an offscreen the size of the whole canvas, and this view is
        // full-screen on the home surface — 60 times a second, for a ball that
        // occupies a fifth of it.
        val pad = r * LAYER_PAD
        val layer = canvas.saveLayer(f.cx - pad, f.cy - pad, f.cx + pad, f.cy + pad, null)
        drawSubstrate(canvas, f)
        for (i in 0 until SiriPalette.BLOB_COUNT) drawBlob(canvas, f, i)
        drawSpokes(canvas, f)
        drawCore(canvas, f)
        drawGlass(canvas, f)
        canvas.restoreToCount(layer)

        if (f.chrome) drawChrome(canvas, f)
        drawRim(canvas, f)
    }

    // --- inside the layer -------------------------------------------------------

    /**
     * The dark ball the colours live inside.
     *
     * Its own edge fades to nothing inside the ball's radius, so there is no
     * hard circular outline — which is the "box" complaint in a rounder form.
     */
    private fun drawSubstrate(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX) return
        plain.shader = RadialGradient(
            f.cx, f.cy, r,
            intArrayOf(
                withAlpha(SUBSTRATE_COLOR, SUBSTRATE_ALPHA * f.alpha),
                withAlpha(SUBSTRATE_COLOR, SUBSTRATE_ALPHA * 0.92f * f.alpha),
                withAlpha(SUBSTRATE_COLOR, 0f),
            ),
            SUBSTRATE_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, r, plain)
        plain.shader = null
    }

    private fun drawBlob(canvas: Canvas, f: Frame, i: Int) {
        val r = f.radius
        val angle = f.phase * ORBIT_RATES[i] + ORBIT_OFFSETS[i]
        // An ellipse, wider than tall, so the motion reads as rolling rather
        // than as three dots going round a circle.
        val orbit = r * ORBIT_FRACTION * (0.75f + 0.25f * f.level)
        val bx = f.cx + orbit * cos(angle)
        val by = f.cy + orbit * sin(angle) * 0.72f
        val blobRadius = r * BLOB_FRACTION * (1f + 0.10f * f.level)
        if (blobRadius < MIN_DRAW_PX) return

        val color = f.blobs[i % f.blobs.size]
        additive.shader = RadialGradient(
            bx, by, blobRadius,
            intArrayOf(
                withAlpha(color, BLOB_ALPHA * f.alpha),
                withAlpha(color, BLOB_ALPHA * 0.55f * f.alpha),
                withAlpha(color, 0f),
            ),
            BLOB_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(bx, by, blobRadius, additive)
        additive.shader = null
    }

    /**
     * The coils: ten arc segments in an annulus, with a thin radial divider in
     * each gap.
     *
     * Screen-blended against the blob field rather than painted over it, so a
     * blob drifting under a coil lights that coil in its own colour. Painted
     * over, the same shape reads as a decal stuck on the front of a ball.
     *
     * They counter-rotate slowly against the chrome outside, which is what
     * stops the whole assembly from looking like one rigid disc.
     */
    private fun drawSpokes(canvas: Canvas, f: Frame) {
        val r = f.radius
        val ringR = r * SPOKE_RADIUS
        val width = r * SPOKE_WIDTH
        if (ringR < MIN_DRAW_PX || width < MIN_DRAW_PX) return

        arcRect.set(f.cx - ringR, f.cy - ringR, f.cx + ringR, f.cy + ringR)
        strokeAdditive.strokeWidth = width
        strokeAdditive.color =
            withAlpha(f.core, SPOKE_ALPHA * f.alpha * (0.70f + 0.30f * f.level))

        val span = 360f / SPOKE_COUNT
        val arc = span - SPOKE_GAP_DEG
        val spin = -f.spinDeg * SPOKE_SPIN_RATIO
        for (i in 0 until SPOKE_COUNT) {
            canvas.drawArc(arcRect, spin + i * span, arc, false, strokeAdditive)
        }

        // The dividers, down the middle of each gap. Deliberately fainter than
        // the plates: lead with the dividers and the reactor reads as a
        // starburst, which is a different object and a much cheaper-looking one.
        val rIn = r * SPOKE_INNER
        val rOut = r * SPOKE_OUTER
        strokeAdditive.strokeWidth = dp(SPOKE_DIVIDER_DP)
        strokeAdditive.color =
            withAlpha(f.rim, SPOKE_DIVIDER_ALPHA * f.alpha * (0.70f + 0.30f * f.level))
        for (i in 0 until SPOKE_COUNT) {
            val a = ((spin + i * span - SPOKE_GAP_DEG / 2f) * RAD_PER_DEG)
            val ca = cos(a)
            val sa = sin(a)
            canvas.drawLine(
                f.cx + ca * rIn, f.cy + sa * rIn,
                f.cx + ca * rOut, f.cy + sa * rOut,
                strokeAdditive,
            )
        }

        // The two rings the plates are seated between. These are what make the
        // annulus read as an assembly with an inside and an outside rather than
        // as ten highlights floating in the glow.
        strokeAdditive.strokeWidth = dp(SPOKE_SEAT_DP)
        strokeAdditive.color = withAlpha(f.core, SPOKE_SEAT_ALPHA * f.alpha)
        canvas.drawCircle(f.cx, f.cy, rIn, strokeAdditive)
        canvas.drawCircle(f.cx, f.cy, rOut, strokeAdditive)
        strokeAdditive.shader = null
    }

    private fun drawCore(canvas: Canvas, f: Frame) {
        val r = f.radius
        // The mic level lives HERE rather than on the whole assembly: growing
        // the core makes speech visible without pushing the outer rings past
        // the margin the caller sized the ball against.
        val coreRadius = r * (CORE_FRACTION + CORE_LEVEL_GAIN * f.level)
        if (coreRadius < MIN_DRAW_PX) return
        // Short of white on purpose. Screen-blended over a colour field that is
        // already near its own ceiling, a white core clips the middle of the orb
        // flat and erases both the drifting colour and the inner half of the
        // coils — which is most of what there is to look at.
        additive.shader = RadialGradient(
            f.cx, f.cy, coreRadius,
            intArrayOf(
                withAlpha(lighten(f.core, CORE_WHITENESS), CORE_ALPHA * f.alpha),
                withAlpha(f.core, CORE_ALPHA * 0.80f * f.alpha),
                withAlpha(f.core, 0f),
            ),
            CORE_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, coreRadius, additive)
        additive.shader = null
    }

    /**
     * The cover over the reactor: a specular highlight and a darkened inner
     * edge.
     *
     * A self-luminous sphere has no terminator — it is its own light source, so
     * shading it from outside is simply wrong and reads as dirt. The depth has
     * to come from the glass in front of it, which is what these two do: the
     * highlight fixes a light source somewhere up and to the left, and the
     * inner shadow gives the cover a thickness for that light to fall off
     * across. Together they are the difference between a circle and a ball.
     */
    private fun drawGlass(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX) return

        // The rolled inner edge, painted over everything the reactor emits.
        plain.shader = RadialGradient(
            f.cx, f.cy, r,
            intArrayOf(
                withAlpha(EDGE_SHADOW_COLOR, 0f),
                withAlpha(EDGE_SHADOW_COLOR, 0f),
                withAlpha(EDGE_SHADOW_COLOR, EDGE_SHADOW_ALPHA * f.alpha),
            ),
            EDGE_SHADOW_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, r, plain)
        plain.shader = null

        val sx = f.cx - r * SPECULAR_X
        val sy = f.cy - r * SPECULAR_Y
        val sr = r * SPECULAR_R
        if (sr < MIN_DRAW_PX) return
        additive.shader = RadialGradient(
            sx, sy, sr,
            intArrayOf(
                withAlpha(Color.WHITE, SPECULAR_ALPHA * f.alpha),
                withAlpha(Color.WHITE, 0f),
            ),
            SPECULAR_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(sx, sy, sr, additive)
        additive.shader = null
    }

    // --- outside the layer -------------------------------------------------------

    /** The bloom, which is what makes the ball read as light rather than paint. */
    private fun drawHalo(canvas: Canvas, f: Frame) {
        val haloRadius =
            minOf(f.radius * (HALO_FRACTION + HALO_LEVEL_GAIN * f.level), f.maxRadius)
        if (haloRadius < MIN_DRAW_PX) return
        plain.shader = RadialGradient(
            f.cx, f.cy, haloRadius,
            intArrayOf(
                withAlpha(f.rim, HALO_ALPHA * f.alpha),
                withAlpha(f.rim, HALO_ALPHA * 0.35f * f.alpha),
                withAlpha(f.rim, 0f),
            ),
            HALO_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, haloRadius, plain)
        plain.shader = null
    }

    /**
     * The ball's own edge, lit.
     *
     * A single flat stroke is a drawn circle. Running it from dim where the
     * specular is to bright directly opposite is the fresnel every rounded
     * transparent object has, and it costs one gradient.
     */
    private fun drawRim(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX) return
        val a = RIM_ALPHA * f.alpha * (0.7f + 0.3f * f.level)
        rimPaint.strokeWidth = dp(RIM_WIDTH_DP)
        rimPaint.shader = LinearGradient(
            f.cx - r, f.cy - r, f.cx + r, f.cy + r,
            intArrayOf(
                withAlpha(f.rim, a * 0.30f),
                withAlpha(f.rim, a),
                withAlpha(f.core, a),
            ),
            RIM_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, r, rimPaint)
        rimPaint.shader = null
    }

    private fun drawChrome(canvas: Canvas, f: Frame) {
        val r = f.radius
        // Everything lifts with the voice, which is most of what makes the orb
        // feel driven by a microphone rather than by a timer.
        val lift = 0.88f + 0.5f * f.level
        val aInner = f.alpha * f.ringAlpha[RING_INNER_RIM] * lift
        val aMid = f.alpha * f.ringAlpha[RING_MID_DASH] * lift
        val aFine = f.alpha * f.ringAlpha[RING_FINE_DASH] * lift
        val aGauge = f.alpha * f.ringAlpha[RING_GAUGE] * lift

        val rInner = r * INNER_RIM_FACTOR * f.ringScale[RING_INNER_RIM]
        val rMid = r * MID_DASH_FACTOR * f.ringScale[RING_MID_DASH]
        val rFine = r * FINE_DASH_FACTOR * f.ringScale[RING_FINE_DASH]
        val rGauge = r * GAUGE_FACTOR * f.ringScale[RING_GAUGE]
        val rOuter = r * OUTER_FACTOR * f.ringScale[RING_GAUGE]

        drawAnnulusSweep(canvas, f, r * SWEEP_INNER_FACTOR, rGauge, -f.spinDeg, aGauge)
        drawTicks(canvas, f, rGauge, 72, r * MINOR_TICK, dp(1f), aGauge * 0.8f)
        drawTicks(canvas, f, rGauge, 12, r * MAJOR_TICK, dp(1.6f), aGauge)
        drawDashedRing(canvas, f, rMid, 28, f.spinDeg, dp(2.5f), aMid)
        drawDashedRing(canvas, f, rFine, 64, -f.spinDeg * 1.43f, dp(1.4f), aFine * 0.75f)
        drawRing(canvas, f, rOuter, dp(1f), aGauge * 0.4f)
        drawRing(canvas, f, rInner, dp(1.6f), aInner)
        if (f.turbulence) {
            // The single cheapest cue that Jarvis is working rather than merely
            // a different colour. Sits between the inner rim and the mid dashes
            // so it cannot collide with the boundary the geometry is budgeted
            // against.
            val wobble = 1f + 0.02f * sin(f.phase * 2f)
            drawRing(canvas, f, r * TURBULENCE_FACTOR * wobble, dp(1.2f), f.alpha * 0.4f)
        }
    }

    private fun drawRing(canvas: Canvas, f: Frame, r: Float, stroke: Float, a: Float) {
        if (r < MIN_DRAW_PX || a <= 0f) return
        ringPaint.shader = null
        ringPaint.color = f.rim
        ringPaint.strokeWidth = stroke
        ringPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        canvas.drawCircle(f.cx, f.cy, r, ringPaint)
    }

    private fun drawDashedRing(
        canvas: Canvas, f: Frame, r: Float,
        dashes: Int, rotationDeg: Float, stroke: Float, a: Float,
    ) {
        if (r < MIN_DRAW_PX || a <= 0f || dashes <= 0) return
        val circumference = (2.0 * Math.PI * r).toFloat()
        val seg = circumference / (dashes * 2f)
        // A DashPathEffect whose intervals sum to zero is undefined in Skia; at
        // a sub-pixel radius the ring is invisible anyway.
        if (seg <= 0f) return
        ringPaint.shader = null
        ringPaint.color = f.rim
        ringPaint.strokeWidth = stroke
        ringPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        ringPaint.pathEffect = DashPathEffect(floatArrayOf(seg, seg), 0f)
        canvas.save()
        canvas.rotate(rotationDeg, f.cx, f.cy)
        canvas.drawCircle(f.cx, f.cy, r, ringPaint)
        canvas.restore()
        ringPaint.pathEffect = null
    }

    private fun drawTicks(
        canvas: Canvas, f: Frame, r: Float,
        count: Int, length: Float, stroke: Float, a: Float,
    ) {
        if (r < MIN_DRAW_PX || a <= 0f || count <= 0) return
        tickPaint.color = f.rim
        tickPaint.strokeWidth = stroke
        tickPaint.alpha = (255 * a).toInt().coerceIn(0, 255)
        val rIn = r - length / 2f
        val rOut = r + length / 2f
        for (i in 0 until count) {
            val ang = (i.toFloat() / count) * 2.0 * Math.PI
            val ca = cos(ang).toFloat()
            val sa = sin(ang).toFloat()
            canvas.drawLine(
                f.cx + ca * rIn, f.cy + sa * rIn,
                f.cx + ca * rOut, f.cy + sa * rOut,
                tickPaint,
            )
        }
    }

    private fun drawAnnulusSweep(
        canvas: Canvas, f: Frame, rIn: Float, rOut: Float,
        rotationDeg: Float, a: Float,
    ) {
        if (rOut < MIN_DRAW_PX || rOut <= rIn || a <= 0f) return
        annulus.reset()
        annulus.fillType = Path.FillType.EVEN_ODD
        annulus.addCircle(f.cx, f.cy, rOut, Path.Direction.CW)
        annulus.addCircle(f.cx, f.cy, rIn, Path.Direction.CW)

        val sweep = SweepGradient(
            f.cx, f.cy,
            intArrayOf(Color.TRANSPARENT, Color.TRANSPARENT, withAlpha(f.rim, 0.59f), Color.TRANSPARENT),
            SWEEP_STOPS,
        )
        sweepMatrix.setRotate(rotationDeg, f.cx, f.cy)
        sweep.setLocalMatrix(sweepMatrix)
        sweepPaint.shader = sweep
        sweepPaint.alpha = (110 * a * (0.6f + 0.4f * f.level)).toInt().coerceIn(0, 255)

        canvas.save()
        canvas.clipPath(annulus)
        canvas.drawRect(f.cx - rOut, f.cy - rOut, f.cx + rOut, f.cy + rOut, sweepPaint)
        canvas.restore()
        sweepPaint.shader = null
    }

    private fun lighten(color: Int, fraction: Float): Int = Color.rgb(
        Color.red(color) + ((255 - Color.red(color)) * fraction).toInt(),
        Color.green(color) + ((255 - Color.green(color)) * fraction).toInt(),
        Color.blue(color) + ((255 - Color.blue(color)) * fraction).toInt(),
    )

    private fun withAlpha(color: Int, fraction: Float): Int =
        Color.argb(
            (255f * fraction.coerceIn(0f, 1f)).toInt(),
            Color.red(color),
            Color.green(color),
            Color.blue(color),
        )

    companion object {
        /** Rings, outward. The boot sequence brings them in in this order. */
        const val RING_INNER_RIM = 0
        const val RING_MID_DASH = 1
        const val RING_FINE_DASH = 2
        const val RING_GAUGE = 3
        const val RING_COUNT = 4

        // --- geometry, all as multiples of the ball's radius ------------------

        /** Blob radius. Larger than the orbit, so the three always overlap. */
        const val BLOB_FRACTION = 0.80f

        /** How far the blob centres wander from the middle. */
        const val ORBIT_FRACTION = 0.30f

        const val CORE_FRACTION = 0.30f

        /**
         * How much the microphone level grows the core.
         *
         * A third again at full volume, and capped there by the coils: a core
         * that reaches [SPOKE_INNER] swallows them, which would leave the one
         * element that says "arc reactor" visible at rest and gone the moment
         * anybody spoke.
         */
        const val CORE_LEVEL_GAIN = 0.10f

        /** The coil annulus: [SPOKE_INNER]..[SPOKE_OUTER]. */
        const val SPOKE_INNER = 0.42f
        const val SPOKE_OUTER = 0.92f
        const val SPOKE_COUNT = 10
        const val SPOKE_GAP_DEG = 9f
        const val SPOKE_ALPHA = 0.42f
        const val SPOKE_DIVIDER_ALPHA = 0.28f
        const val SPOKE_DIVIDER_DP = 1.1f

        /** The two rings the plates sit between. */
        const val SPOKE_SEAT_ALPHA = 0.55f
        const val SPOKE_SEAT_DP = 1.4f

        /**
         * The coils turn against the chrome, slowly. Same clock, opposite sign:
         * two things moving together read as one rigid disc, and the reactor is
         * supposed to look like an assembly.
         */
        const val SPOKE_SPIN_RATIO = 0.35f

        /** Radius of the coil ring's centreline, and its stroke width. */
        const val SPOKE_RADIUS = (SPOKE_INNER + SPOKE_OUTER) / 2f
        const val SPOKE_WIDTH = SPOKE_OUTER - SPOKE_INNER

        const val INNER_RIM_FACTOR = 1.05f
        const val TURBULENCE_FACTOR = 1.14f
        const val MID_DASH_FACTOR = 1.22f
        const val FINE_DASH_FACTOR = 1.36f
        const val GAUGE_FACTOR = 1.50f

        /** Where the radar sweep's wedge begins, just outside the ball. */
        const val SWEEP_INNER_FACTOR = 1.10f

        const val MINOR_TICK = 0.08f
        const val MAJOR_TICK = 0.15f

        /**
         * The outermost primitive, as a multiple of the ball's radius.
         *
         * Callers size the ball so THIS still fits inside the view at the
         * largest scale they can ask for. Anything drawn beyond it must raise
         * this constant — which is the whole point of stating it here rather
         * than leaving the largest radius to be whichever `drawX` happens to
         * have the biggest number in it.
         *
         * The gauge's major ticks reach `GAUGE_FACTOR + MAJOR_TICK / 2`, so
         * this has to stay above that too.
         */
        const val OUTER_FACTOR = 1.70f

        /**
         * How far past the ball the saved layer extends. The blobs are the
         * furthest thing inside it: a centre at [ORBIT_FRACTION] plus a radius
         * of [BLOB_FRACTION], and the layer must not clip them.
         */
        const val LAYER_PAD = ORBIT_FRACTION + BLOB_FRACTION + 0.05f

        /** The bloom, and how much a voice swells it. */
        const val HALO_FRACTION = 1.30f
        const val HALO_LEVEL_GAIN = 0.25f

        // --- colour ------------------------------------------------------------

        const val BLOB_ALPHA = 0.92f
        const val CORE_ALPHA = 0.62f

        /** How far the core's centre is pushed toward white. */
        const val CORE_WHITENESS = 0.35f
        const val HALO_ALPHA = 0.42f
        const val RIM_ALPHA = 0.72f
        const val RIM_WIDTH_DP = 1.4f

        /**
         * The ball behind the colours. Deep navy rather than black: black over a
         * dark wallpaper is a hole, and this has to read as an object on both.
         */
        val SUBSTRATE_COLOR = 0xFF060B16.toInt()

        /**
         * Nearly opaque at the middle. This is the number that answers "too
         * transparent": at 0 the orb was whatever was behind it, tinted.
         */
        const val SUBSTRATE_ALPHA = 0.90f

        /** The rolled inner edge of the cover. */
        val EDGE_SHADOW_COLOR = 0xFF01040A.toInt()
        const val EDGE_SHADOW_ALPHA = 0.55f

        /** Where the highlight sits, and how big it is. Up and to the left. */
        const val SPECULAR_X = 0.34f
        const val SPECULAR_Y = 0.38f
        const val SPECULAR_R = 0.46f
        const val SPECULAR_ALPHA = 0.26f

        private val BLOB_STOPS = floatArrayOf(0f, 0.45f, 1f)
        private val CORE_STOPS = floatArrayOf(0f, 0.38f, 1f)
        private val HALO_STOPS = floatArrayOf(0f, 0.55f, 1f)
        private val SPECULAR_STOPS = floatArrayOf(0f, 1f)
        private val RIM_STOPS = floatArrayOf(0f, 0.5f, 1f)
        private val SWEEP_STOPS = floatArrayOf(0f, 0.62f, 0.92f, 1f)

        /** Transparent until 82% of the ball, then down into the edge shadow. */
        private val EDGE_SHADOW_STOPS = floatArrayOf(0f, 0.82f, 1f)

        /**
         * Flat to 78% of the ball, then out to nothing.
         *
         * The fade has to happen inside the ball's own radius or the substrate
         * gets a visible circular edge.
         */
        private val SUBSTRATE_STOPS = floatArrayOf(0f, 0.78f, 1f)

        /**
         * Orbit rates, as a small irrational-ish spread rather than multiples:
         * 1 : 0.73 : 1.31 never returns to the same arrangement, so the field
         * does not visibly loop the way 1 : 2 : 3 would.
         */
        private val ORBIT_RATES = floatArrayOf(1f, 0.73f, 1.31f)

        /** Thirds of a turn, written out: the web shader carries the same two. */
        private val ORBIT_OFFSETS = floatArrayOf(0f, 2.0943951f, 4.1887902f)

        /** `(2 * PI).toFloat()`, written out because `const val` wants a literal. */
        const val TWO_PI = 6.2831855f
        const val RAD_PER_DEG = 0.017453292f
        const val DEG_PER_RAD = 57.29578f

        /**
         * Below this radius (in px) a shape is not worth drawing, and a shader
         * built for it is worth a crash: `RadialGradient` rejects a radius of
         * zero outright. The boot sequence starts the ball at exactly zero, so
         * this is a live path, not a theoretical one.
         */
        const val MIN_DRAW_PX = 0.5f
    }
}
