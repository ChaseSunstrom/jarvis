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
 *  3. **[drawSphereShading]** — the ball's near side and its terminator, both
 *     hung off [LIGHT_X]. See below.
 *  4. **[drawSpokes]** — the reactor's coils, and the single element that most
 *     says "arc reactor" rather than "orb": a dark [drawHousing] recess with the
 *     shadow it casts on the ball, a metal hub ring, then ten filled keystone
 *     plates seated in that recess, each lit by its own angle to the light, with
 *     the outer lip's shadow across them. The plates are inside the layer
 *     deliberately: the drifting blob colours *light* them, instead of a flat
 *     overprint that would read as a decal. The housing inside it is the one
 *     thing there that is not additive — see [drawHousing] for why it cannot be.
 *  5. **[drawCore]** — the hot centre.
 *  6. **[drawGlass]** — limb darkening, the fresnel arc and the specular
 *     highlight, in that order, over everything the reactor emits.
 *
 * Then, outside the layer, the instrument chrome: a fresnel rim, a rotating
 * dashed ring, counter-rotating fine dashes, a 72/12 gauge and a radar sweep.
 * Outside, because chrome screen-blended against the blob field washes out to
 * white wherever a blob passes under it.
 *
 * ## Why it reads as a ball and not as a disc
 *
 * The report this half of the file answers is *"I dont want just an orb clock,
 * I want it to look 3d and actually nice, similar to the rest of the AIs"*. The
 * orb had structure — a recess, plates, seats — and no DEPTH: every shape was a
 * gradient about the same centre, so brightness fell off with radius alone and
 * the whole thing read as concentric rings printed on a circle.
 *
 * A Canvas has no per-pixel shader, so there is no `dot(n, l)` to be had here.
 * The sphere is stacked gradients instead, and the trick that does most of the
 * work is that their centres are NOT the ball's centre:
 *
 *  * [drawSphereShading] lifts a broad highlight centred [SPHERE_LIGHT_OFFSET]
 *    of the way toward the light, and drops a terminator that grows with
 *    distance FROM that same point rather than from the middle. Offsetting one
 *    radial gradient is the whole difference between a disc and a ball;
 *  * [drawGlass] darkens the limb, then puts a thin bright fresnel arc on the
 *    side AWAY from the light — an edge brighter than the middle is what says
 *    "surface at a grazing angle" rather than "gradient";
 *  * the specular is tight, offset to [SPECULAR_OFFSET] (which is where the
 *    half-vector actually meets the sphere, not a guess) and drifts a fraction
 *    of a percent, because a highlight nailed to one pixel reads as a sticker;
 *  * [drawHousing] casts the assembly's shadow onto the ball, away from the
 *    light, and [drawSpokes] lights each plate by one cosine of its own angle,
 *    so the plate facing the light is the brightest of the ten.
 *
 * All of it hangs off ONE light direction, [LIGHT_X]/[LIGHT_Y]/[LIGHT_Z], which
 * is the same vector the web shader normalises into its `L`. Two surfaces lit
 * from two directions is the most visible drift there is — the highlight is the
 * first thing anybody looks at — so `reactor_orb_test.py` pins the direction,
 * the specular offset and the plate-lighting rule across both.
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

    /**
     * The housing's machined parts, inside the layer and deliberately NOT
     * additive: a hub ring that adds light is another glow, and the reactor
     * already has enough of those. This one is struck by the same light source
     * the glass highlight fixes, so it reads as turned metal.
     */
    private val metal = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val tickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val sweepPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }

    /** The two seat radii each plate's keystone is struck between. */
    private val seatInRect = RectF()
    private val seatOutRect = RectF()

    private val annulus = Path()
    private val plate = Path()
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
        drawSphereShading(canvas, f)
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
     * Screen x of a point [fraction] of the ball's radius toward the light.
     *
     * Every offset centre in this file goes through these two, so the light can
     * only be in one place.
     */
    private fun litX(f: Frame, fraction: Float) = f.cx + LIGHT_DIR_X * f.radius * fraction

    /**
     * Screen y of the same point.
     *
     * [LIGHT_DIR_Y] is stated with y UP, the way the shader's `vec3 L` is, and
     * Skia's y points DOWN — hence the sign. Getting this backwards puts the
     * phone's highlight below the middle and the browser's above it, which is
     * about the most obvious disagreement two renderers can have.
     */
    private fun litY(f: Frame, fraction: Float) = f.cy - LIGHT_DIR_Y * f.radius * fraction

    /** The same two, for the fill. */
    private fun fillX(f: Frame, fraction: Float) = f.cx + FILL_DIR_X * f.radius * fraction

    private fun fillY(f: Frame, fraction: Float) = f.cy - FILL_DIR_Y * f.radius * fraction

    /**
     * One specular lobe, as a gradient the size of a Blinn-Phong lobe.
     *
     * A Canvas cannot raise anything to a power, so it is handed a gradient
     * shaped like the answer instead: full at the centre, half at
     * [SPECULAR_HALF] of the radius — which is where a lobe of that exponent
     * is at half intensity — and out by the radius, which is where it has
     * fallen to two percent. See [SPECULAR_POWER]; the caller works out the
     * radius, this draws it.
     *
     * Painted into the BALL rather than into the lobe's own circle. A highlight
     * sits off centre, so the wide one reaches past the silhouette on the lit
     * side — and light added outside the ball's own edge is a smear on the
     * bloom that no amount of shading inside the ball will explain.
     */
    private fun drawLobe(
        canvas: Canvas, f: Frame,
        x: Float, y: Float, radius: Float, color: Int, alpha: Float,
    ) {
        if (radius < MIN_DRAW_PX || f.radius < MIN_DRAW_PX || alpha <= 0f) return
        additive.shader = RadialGradient(
            x, y, radius,
            intArrayOf(
                withAlpha(color, alpha * f.alpha),
                withAlpha(color, alpha * 0.5f * f.alpha),
                withAlpha(color, 0f),
            ),
            SPECULAR_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, f.radius, additive)
        additive.shader = null
    }

    /**
     * The sphere: a lit near side, and a terminator away from it.
     *
     * This is the function that makes the orb a ball. Everything else in the
     * layer is a gradient centred on the middle of the circle, and a stack of
     * those is a disc with rings on it however many of them there are —
     * brightness that falls off with RADIUS is a flat target lit head-on, which
     * is exactly what *"I dont want just an orb clock, I want it to look 3d"*
     * was looking at.
     *
     * A real renderer would take `dot(n, l)`. A Canvas has no per-pixel shader,
     * so that cosine is faked with three gradients struck about points offset
     * along the two light directions rather than about the middle:
     *
     *  * an additive lift toward the key, brightest where the light strikes and
     *    gone by the time it reaches the far side;
     *  * a darkening that grows with distance from that same point, which is
     *    the terminator. Its radius is deliberately larger than the ball, so
     *    the shading is still climbing at the far limb instead of having
     *    bottomed out into a black rind;
     *  * the fill, back over the terminator from the opposite corner, because
     *    one light in a black room gives a crescent moon.
     *
     * The true diffuse pole is further out than [SPHERE_LIGHT_OFFSET] — it is at
     * `length(L.xy)`, about seven tenths of the way to the edge — but a gradient
     * centred out there crowds all of its falloff into the last third of the
     * ball and reads as a crescent moon of a different kind. Pulled in, it
     * reads as a sphere.
     *
     * Both gradients are painted into a circle of the ball's own radius, so
     * nothing they do can escape the ball and stain the bloom around it.
     */
    private fun drawSphereShading(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX) return

        // The light breathes a hair. Nothing about this is legible frame to
        // frame; it is there so the orb is never completely still, and its rate
        // is not a multiple of anything else so there is no seam to catch.
        val wander = 1f + SPHERE_WANDER * sin(f.phase * SPHERE_WANDER_RATE)
        val lx = litX(f, SPHERE_LIGHT_OFFSET * wander)
        val ly = litY(f, SPHERE_LIGHT_OFFSET * wander)

        val litRadius = r * SPHERE_LIT_R
        if (litRadius >= MIN_DRAW_PX) {
            additive.shader = RadialGradient(
                lx, ly, litRadius,
                intArrayOf(
                    withAlpha(lighten(f.rim, SPHERE_LIT_WHITENESS), SPHERE_LIT_ALPHA * f.alpha),
                    withAlpha(f.rim, SPHERE_LIT_ALPHA * 0.45f * f.alpha),
                    withAlpha(f.rim, 0f),
                ),
                SPHERE_LIT_STOPS,
                Shader.TileMode.CLAMP,
            )
            canvas.drawCircle(f.cx, f.cy, r, additive)
            additive.shader = null
        }

        val fallRadius = r * TERMINATOR_R
        if (fallRadius < MIN_DRAW_PX) return
        plain.shader = RadialGradient(
            lx, ly, fallRadius,
            intArrayOf(
                withAlpha(TERMINATOR_COLOR, 0f),
                withAlpha(TERMINATOR_COLOR, 0f),
                withAlpha(TERMINATOR_COLOR, TERMINATOR_ALPHA * f.alpha),
            ),
            TERMINATOR_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, r, plain)
        plain.shader = null

        // The fill, over the terminator it exists to rescue. One light in a
        // black room gives a crescent moon, and a crescent moon is not what
        // anybody means by "make it look 3d": the far side has to stay
        // readable, just cooler and dimmer than the near one.
        val fillRadius = r * SPHERE_LIT_R
        if (fillRadius < MIN_DRAW_PX) return
        additive.shader = RadialGradient(
            fillX(f, SPHERE_LIGHT_OFFSET), fillY(f, SPHERE_LIGHT_OFFSET), fillRadius,
            intArrayOf(
                withAlpha(f.rim, FILL_ALPHA * f.alpha),
                withAlpha(f.rim, FILL_ALPHA * 0.45f * f.alpha),
                withAlpha(f.rim, 0f),
            ),
            SPHERE_LIT_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, r, additive)
        additive.shader = null
    }

    /**
     * The recess the coils are bolted into, and the hub ring inside it.
     *
     * Drawn with [plain] rather than [additive], and that is not a detail —
     * it is the single biggest part of "layered". Screening a dark colour onto
     * anything is very nearly a no-op, so an additive housing is no housing at
     * all, and without it there is nothing behind the plates: they float in the
     * blob field with no depth to sit at.
     *
     * Outward: a dark gap that separates the core from the assembly, the metal
     * hub ring, then the recess floor the plates lie on.
     */
    private fun drawHousing(canvas: Canvas, f: Frame) {
        val r = f.radius
        val hIn = r * HOUSING_INNER
        val hOut = r * HOUSING_OUTER
        if (hIn < MIN_DRAW_PX || hOut <= hIn) return

        // The shadow the whole assembly drops on the ball behind it, and the
        // only reason the recess reads as being AT a depth rather than merely
        // being dark. Its ring is struck about a point pushed away from the
        // light, so it is a sliver on the lit side — where the housing covers
        // it up entirely — and a band on the far side, which is what a shadow
        // under an object lit from up and to the left looks like.
        //
        // Painted into a circle of the ball's radius: past the far limb the
        // ring would otherwise reach outside the ball and smear the bloom.
        val shadowR = hOut + r * HOUSING_SHADOW_SPREAD
        if (shadowR >= MIN_DRAW_PX) {
            // The band is struck ON the outer lip and falls off either side of
            // it, so its darkest line is exactly where the assembly meets the
            // ball. The stops are clamped rather than trusted: a retuned spread
            // that puts them out of order throws, and this is drawn 60 times a
            // second on the main thread.
            val ringIn = ((HOUSING_OUTER - HOUSING_SHADOW_SPREAD) * r / shadowR)
                .coerceIn(0.02f, 0.94f)
            val ringMid = (HOUSING_OUTER * r / shadowR).coerceIn(ringIn + 0.01f, 0.99f)
            plain.shader = RadialGradient(
                litX(f, -HOUSING_SHADOW_OFFSET), litY(f, -HOUSING_SHADOW_OFFSET), shadowR,
                intArrayOf(
                    withAlpha(HOUSING_COLOR, 0f),
                    withAlpha(HOUSING_COLOR, 0f),
                    withAlpha(HOUSING_COLOR, HOUSING_SHADOW_ALPHA * f.alpha),
                    withAlpha(HOUSING_COLOR, 0f),
                ),
                floatArrayOf(0f, ringIn, ringMid, 1f),
                Shader.TileMode.CLAMP,
            )
            canvas.drawCircle(f.cx, f.cy, r, plain)
            plain.shader = null
        }

        annulus.reset()
        annulus.fillType = Path.FillType.EVEN_ODD
        annulus.addCircle(f.cx, f.cy, hOut, Path.Direction.CW)
        annulus.addCircle(f.cx, f.cy, hIn, Path.Direction.CW)
        // Deepest at the floor, lifting toward the outer lip, so the recess has
        // a direction rather than being a flat dark washer.
        plain.shader = RadialGradient(
            f.cx, f.cy, hOut,
            intArrayOf(
                withAlpha(HOUSING_COLOR, HOUSING_ALPHA * f.alpha),
                withAlpha(HOUSING_COLOR, HOUSING_ALPHA * f.alpha),
                withAlpha(HOUSING_COLOR, HOUSING_ALPHA * 0.55f * f.alpha),
            ),
            HOUSING_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawPath(annulus, plain)
        plain.shader = null

        // ...and the floor's own occlusion, on the same path so it cannot leak
        // out of the recess. A washer that is uniformly dark is a hole cut out
        // of a picture; a recess has a wall on the light's side catching what
        // falls in and a wall opposite it in shadow, and the near-to-far ramp
        // across the floor is the whole of that read. Struck about the lit
        // point, exactly like the ball's own terminator.
        val floorR = r * TERMINATOR_R
        if (floorR >= MIN_DRAW_PX) {
            plain.shader = RadialGradient(
                litX(f, SPHERE_LIGHT_OFFSET), litY(f, SPHERE_LIGHT_OFFSET), floorR,
                intArrayOf(
                    withAlpha(HOUSING_COLOR, 0f),
                    withAlpha(HOUSING_COLOR, 0f),
                    withAlpha(HOUSING_COLOR, HOUSING_WALL_ALPHA * f.alpha),
                ),
                TERMINATOR_STOPS,
                Shader.TileMode.CLAMP,
            )
            canvas.drawPath(annulus, plain)
            plain.shader = null
        }

        val hubR = r * HUB_FACTOR
        if (hubR < MIN_DRAW_PX) return
        // Struck by the one light this whole file hangs off, along its actual
        // direction rather than along the diagonal of a bounding box, so the
        // hub is a turned ring catching light rather than a fourth circle
        // emitting it. Bright where the light lands, through to
        // [HUB_SHADOW_COLOR] directly opposite.
        metal.strokeWidth = dp(HUB_WIDTH_DP)
        metal.alpha = 255
        metal.shader = LinearGradient(
            litX(f, HUB_FACTOR), litY(f, HUB_FACTOR),
            litX(f, -HUB_FACTOR), litY(f, -HUB_FACTOR),
            intArrayOf(
                withAlpha(lighten(HUB_COLOR, HUB_SHEEN), HUB_ALPHA * f.alpha),
                withAlpha(HUB_COLOR, HUB_ALPHA * 0.45f * f.alpha),
                withAlpha(HUB_SHADOW_COLOR, HUB_ALPHA * 0.85f * f.alpha),
            ),
            RIM_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, hubR, metal)
        metal.shader = null
    }

    /**
     * The coils: ten trapezoidal plates recessed in a housing between an inner
     * and an outer seat ring.
     *
     * These used to be ten STROKED ARCS — one uniform band at one radius each,
     * every one of them additive, with nothing drawn behind them. A stroked arc
     * has no thickness to shade across and no housing to sit in, so the plates
     * melted into the blob field: *"the arc reactor isnt layerd and doesnt
     * really look like the arc reactor"*. Each plate is a FILLED wedge now,
     * carrying a gradient across its own thickness, sitting in [drawHousing]'s
     * recess with the outer seat's shadow falling across it.
     *
     * The plates keep their screen blend, so a blob drifting under one lights
     * that plate in its own colour. Painted over, the same shape reads as a
     * decal stuck on the front of a ball.
     *
     * They counter-rotate slowly against the chrome outside, which is what
     * stops the whole assembly from looking like one rigid disc.
     */
    private fun drawSpokes(canvas: Canvas, f: Frame) {
        val r = f.radius
        val rIn = r * SPOKE_INNER
        val rOut = r * SPOKE_OUTER
        if (rIn < MIN_DRAW_PX || rOut <= rIn) return

        drawHousing(canvas, f)

        val span = 360f / SPOKE_COUNT
        // A constant-WIDTH gap rather than a constant-ANGLE one. [SPOKE_GAP_DEG]
        // is the gap measured at the coils' centreline, and holding its arc
        // LENGTH fixed opens it out toward the middle — which is exactly what
        // gives each plate its keystone taper, wider at the outer seat than at
        // the inner one. A constant angle gives ten identical sectors, and ten
        // identical sectors is a pie chart.
        val gapArc = SPOKE_GAP_DEG * SPOKE_RADIUS
        val halfIn = (span - gapArc / SPOKE_INNER) / 2f
        val halfOut = (span - gapArc / SPOKE_OUTER) / 2f
        val spin = -f.spinDeg * SPOKE_SPIN_RATIO
        val lit = 0.70f + 0.30f * f.level

        // A gap wide enough to close the plate at the inner seat leaves ten
        // degenerate paths, which Skia will happily spend a frame on.
        if (halfIn > 0f && halfOut > 0f) {
            seatInRect.set(f.cx - rIn, f.cy - rIn, f.cx + rIn, f.cy + rIn)
            seatOutRect.set(f.cx - rOut, f.cy - rOut, f.cx + rOut, f.cy + rOut)
            // Across each plate's THICKNESS, bright along the inner edge: the
            // plates are lit by the core, so the face nearest it is the one that
            // catches light. This gradient is the thing a band stroked at one
            // radius could not have, and half of why the old coils read flat.
            // Shared by all ten — same centre, same radii, one shader a frame.
            val hot = lighten(f.core, PLATE_HOT_WHITENESS)
            val ratio = (rIn / rOut).coerceIn(0.05f, 0.95f)
            additive.shader = RadialGradient(
                f.cx, f.cy, rOut,
                intArrayOf(
                    withAlpha(hot, PLATE_INNER_ALPHA * lit * f.alpha),
                    withAlpha(hot, PLATE_INNER_ALPHA * lit * f.alpha),
                    withAlpha(f.core, PLATE_INNER_ALPHA * 0.45f * lit * f.alpha),
                    withAlpha(f.rim, PLATE_OUTER_ALPHA * lit * f.alpha),
                ),
                floatArrayOf(0f, ratio, ratio + (1f - ratio) * 0.30f, 1f),
                Shader.TileMode.CLAMP,
            )
            for (i in 0 until SPOKE_COUNT) {
                val mid = spin + i * span
                // One cosine per plate, and the cheapest depth in the assembly.
                // Ten plates at ten identical brightnesses are a printed ring
                // however well each one is shaded across itself; the ring only
                // becomes an object once the plate facing the light is visibly
                // the brightest of them and the plate opposite it is visibly
                // the dimmest.
                //
                // The shader states this same rule with the same two constants
                // and takes the same cosine — the plate's own outward direction
                // against the light flattened onto the screen — once per PIXEL
                // rather than once per wedge. `reactor_orb_test.py` compares
                // both halves of it. See [PLATE_LIGHT_BASE].
                val midRad = mid * RAD_PER_DEG
                val facing = cos(midRad) * LIGHT_DIR_X - sin(midRad) * LIGHT_DIR_Y
                val shade = PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN * facing.coerceAtLeast(0f)
                // Paint alpha modulates the shared shader, so the ten plates
                // still cost one gradient between them.
                additive.alpha = (255f * shade).toInt().coerceIn(0, 255)
                plate.reset()
                plate.arcTo(seatInRect, mid - halfIn, 2f * halfIn)
                plate.arcTo(seatOutRect, mid + halfOut, -2f * halfOut)
                plate.close()
                canvas.drawPath(plate, additive)
            }
            // Back to opaque, or every later user of this paint — the core, the
            // glass, the next frame's blobs — inherits the last plate's shade.
            additive.alpha = 255
            additive.shader = null
        }

        // The shadow the outer seat casts down the plates. An unlit band right
        // under the lip is what says the plates sit BELOW the ring rather than
        // level with it, and it is the cheapest occlusion in the assembly.
        val shadowSpan = (rOut - rIn) * SEAT_SHADOW_SPAN
        if (shadowSpan >= MIN_DRAW_PX) {
            val start = ((rOut - shadowSpan) / rOut).coerceIn(0.01f, 0.99f)
            plain.shader = RadialGradient(
                f.cx, f.cy, rOut,
                intArrayOf(
                    withAlpha(HOUSING_COLOR, 0f),
                    withAlpha(HOUSING_COLOR, 0f),
                    withAlpha(HOUSING_COLOR, SEAT_SHADOW_ALPHA * f.alpha),
                ),
                floatArrayOf(0f, start, 1f),
                Shader.TileMode.CLAMP,
            )
            canvas.drawCircle(f.cx, f.cy, rOut, plain)
            plain.shader = null
        }

        // The dividers, down the middle of each gap. Deliberately fainter than
        // the plates: lead with the dividers and the reactor reads as a
        // starburst, which is a different object and a much cheaper-looking one.
        strokeAdditive.strokeWidth = dp(SPOKE_DIVIDER_DP)
        strokeAdditive.color =
            withAlpha(f.rim, SPOKE_DIVIDER_ALPHA * f.alpha * lit)
        for (i in 0 until SPOKE_COUNT) {
            val a = ((spin + (i + 0.5f) * span) * RAD_PER_DEG)
            val ca = cos(a)
            val sa = sin(a)
            canvas.drawLine(
                f.cx + ca * rIn, f.cy + sa * rIn,
                f.cx + ca * rOut, f.cy + sa * rOut,
                strokeAdditive,
            )
        }

        // The two seat rings, drawn last so the lips stay bright over both the
        // plates and the shadow one of them casts. These are the edges of the
        // recess, and what make the annulus read as an assembly with an inside
        // and an outside rather than as ten highlights floating in the glow.
        //
        // Lit along the light rather than flat: these are the two hardest edges
        // in the assembly, and an edge of even brightness all the way round is
        // the one thing that will keep reading as a drawn circle no matter what
        // is shaded behind it.
        strokeAdditive.strokeWidth = dp(SPOKE_SEAT_DP)
        // Opaque paint, alpha carried by the gradient's own colours: paint alpha
        // modulates a shader rather than replacing it, and the dividers above
        // leave theirs at a quarter.
        strokeAdditive.alpha = 255
        strokeAdditive.shader = LinearGradient(
            litX(f, SPOKE_OUTER), litY(f, SPOKE_OUTER),
            litX(f, -SPOKE_OUTER), litY(f, -SPOKE_OUTER),
            intArrayOf(
                withAlpha(lighten(f.core, SEAT_SHEEN), SPOKE_SEAT_ALPHA * f.alpha),
                withAlpha(f.core, SPOKE_SEAT_ALPHA * 0.72f * f.alpha),
                withAlpha(f.rim, SPOKE_SEAT_ALPHA * PLATE_LIGHT_BASE * f.alpha),
            ),
            RIM_STOPS,
            Shader.TileMode.CLAMP,
        )
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
        // Short of white on purpose, and tight. Screen-blended over a colour
        // field that is already near its own ceiling, a white core clips the
        // middle of the orb flat and erases both the drifting colour and the
        // inner half of the coils — which is most of what there is to look at.
        // Spread wide it stops being the hot centre OF something and becomes
        // the ball's own colour, and then there is nothing for the plates to be
        // lit by and nothing for the glass to sit over. The shader keeps its
        // own core to the same tight falloff for the same reason.
        additive.shader = RadialGradient(
            f.cx, f.cy, coreRadius,
            intArrayOf(
                withAlpha(lighten(f.core, CORE_WHITENESS), CORE_ALPHA * f.alpha),
                withAlpha(f.core, CORE_ALPHA * 0.55f * f.alpha),
                withAlpha(f.core, 0f),
            ),
            CORE_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, coreRadius, additive)
        additive.shader = null
    }

    /**
     * The cover over the reactor: limb darkening, a fresnel arc and the
     * specular highlight, in that order.
     *
     * A self-luminous sphere has no terminator of its own — it is its own light
     * source — so the *surface* cues all have to come from the cover in front
     * of it, and these are the three that carry them:
     *
     *  * **limb darkening**, transparent through the middle and deepest at the
     *    edge. A ball is dimmer where you see it at a grazing angle;
     *  * **the fresnel arc**, thin, bright, and struck about the lit point so
     *    it lands on the limb AWAY from the light. An edge brighter than the
     *    middle is the single clearest statement that this is a surface curving
     *    out of view and not a gradient painted on a circle;
     *  * **the specular**, tight and offset. It sits at [SPECULAR_OFFSET],
     *    which is where the half-vector between the light and the viewer
     *    actually meets a unit sphere, and it drifts by [SPECULAR_DRIFT] so it
     *    is never quite nailed to one pixel.
     */
    private fun drawGlass(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX) return

        // The limb, painted over everything the reactor emits. Four stops, not
        // two: a single ramp into the last fifth of the ball is a dark rind
        // with a visible inside edge, and the thing being drawn here is a
        // curve, so it has to start early and shallow.
        plain.shader = RadialGradient(
            f.cx, f.cy, r,
            intArrayOf(
                withAlpha(EDGE_SHADOW_COLOR, 0f),
                withAlpha(EDGE_SHADOW_COLOR, 0f),
                withAlpha(EDGE_SHADOW_COLOR, EDGE_SHADOW_ALPHA * 0.42f * f.alpha),
                withAlpha(EDGE_SHADOW_COLOR, EDGE_SHADOW_ALPHA * f.alpha),
            ),
            EDGE_SHADOW_STOPS,
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(f.cx, f.cy, r, plain)
        plain.shader = null

        // The fresnel arc. Struck about the lit point with a radius of exactly
        // the far limb's distance from it, so the bright end of the gradient
        // lands on the limb opposite the light and the near limb — at roughly
        // half that distance — gets none of it.
        val fresnelR = r * (1f + FRESNEL_OFFSET)
        if (fresnelR >= MIN_DRAW_PX) {
            additive.shader = RadialGradient(
                litX(f, FRESNEL_OFFSET), litY(f, FRESNEL_OFFSET), fresnelR,
                intArrayOf(
                    withAlpha(f.rim, 0f),
                    withAlpha(f.rim, 0f),
                    withAlpha(f.rim, FRESNEL_ALPHA * f.alpha),
                ),
                FRESNEL_STOPS,
                Shader.TileMode.CLAMP,
            )
            canvas.drawCircle(f.cx, f.cy, r, additive)
            additive.shader = null
        }

        // Three lobes, and the drift is what stops any of them reading as a
        // decal: a slow figure a fraction of a percent of the ball wide, on two
        // rates that are not multiples of each other, so the highlight never
        // returns to a place it has been and there is no seam to catch. The
        // browser wanders its whole light for the same reason.
        val drift = r * SPECULAR_DRIFT
        val dx = drift * sin(f.phase * SPECULAR_DRIFT_RATE)
        val dy = drift * cos(f.phase * SPECULAR_DRIFT_RATE * 0.73f)

        // Wide first, so the tight one lands on top of its own sheen. Tinted:
        // it covers a good part of the face and white over a good part of the
        // face is how a coloured orb turns into a grey one.
        drawLobe(
            canvas, f,
            litX(f, SPECULAR_OFFSET) + dx, litY(f, SPECULAR_OFFSET) + dy,
            r * SPECULAR_WIDE_R, lighten(f.rim, SPECULAR_WIDE_TINT), SPECULAR_WIDE_ALPHA,
        )
        // The fill's catchlight, low and to the right, and the reason the ball
        // has two highlights rather than the one a drawing of a ball has.
        drawLobe(
            canvas, f,
            fillX(f, FILL_SPECULAR_OFFSET), fillY(f, FILL_SPECULAR_OFFSET),
            r * FILL_SPECULAR_R, lighten(f.rim, SPECULAR_WIDE_TINT), FILL_SPECULAR_ALPHA,
        )
        // ...and the point itself. The only white thing on the ball.
        drawLobe(
            canvas, f,
            litX(f, SPECULAR_OFFSET) + dx, litY(f, SPECULAR_OFFSET) + dy,
            r * SPECULAR_R, Color.WHITE, SPECULAR_ALPHA,
        )
    }

    // --- outside the layer -------------------------------------------------------

    /**
     * The bloom, which is what makes the ball read as light rather than paint.
     *
     * Two passes, and the second is the one that matters: a wide, very dim
     * skirt at [BLOOM_WIDE] of the halo's radius. Light in air does not stop at
     * a boundary — it falls off for a long way at an intensity you would not
     * notice if you looked for it — and a single tight halo is a painted ring
     * around the orb, which is the "sticker" read. Both are clamped to
     * [Frame.maxRadius]: the bloom is the only thing that can exceed the view,
     * and clipped, a gradient becomes a bright SQUARE.
     */
    private fun drawHalo(canvas: Canvas, f: Frame) {
        // Breathing, at a rate that is nothing else's, so the orb is never
        // completely static even in a silent room.
        val breath = 1f + HALO_BREATH * sin(f.phase * HALO_BREATH_RATE)
        val haloRadius = minOf(
            f.radius * (HALO_FRACTION + HALO_LEVEL_GAIN * f.level) * breath,
            f.maxRadius,
        )
        if (haloRadius < MIN_DRAW_PX) return

        val wide = minOf(haloRadius * BLOOM_WIDE, f.maxRadius)
        if (wide >= MIN_DRAW_PX) {
            plain.shader = RadialGradient(
                f.cx, f.cy, wide,
                intArrayOf(
                    withAlpha(f.rim, HALO_ALPHA * BLOOM_WIDE_ALPHA * f.alpha),
                    withAlpha(f.rim, HALO_ALPHA * BLOOM_WIDE_ALPHA * 0.40f * f.alpha),
                    withAlpha(f.rim, 0f),
                ),
                BLOOM_WIDE_STOPS,
                Shader.TileMode.CLAMP,
            )
            canvas.drawCircle(f.cx, f.cy, wide, plain)
            plain.shader = null
        }

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
     * light lands to bright directly opposite is the fresnel every rounded
     * transparent object has, and it costs one gradient — the same arc
     * [drawGlass] puts just inside the ball, stated again on the stroke so the
     * outline agrees with the surface it bounds.
     *
     * The gradient runs along the light's own direction. It used to run down
     * the diagonal of the bounding box, which is a different angle, and two
     * fresnels that disagree about where the light is read as neither.
     */
    private fun drawRim(canvas: Canvas, f: Frame) {
        val r = f.radius
        if (r < MIN_DRAW_PX) return
        val a = RIM_ALPHA * f.alpha * (0.7f + 0.3f * f.level)
        rimPaint.strokeWidth = dp(RIM_WIDTH_DP)
        rimPaint.shader = LinearGradient(
            litX(f, 1f), litY(f, 1f),
            litX(f, -1f), litY(f, -1f),
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
        const val SPOKE_DIVIDER_ALPHA = 0.28f
        const val SPOKE_DIVIDER_DP = 1.1f

        /**
         * The gap between two plates, in degrees, **at [SPOKE_RADIUS]**.
         *
         * Its arc length is what is held fixed, not its angle, so it opens out
         * toward the middle and each plate comes out a keystone rather than a
         * slice of pie. See [drawSpokes].
         */
        const val SPOKE_GAP_DEG = 9f

        /** The two rings the plates sit between. */
        const val SPOKE_SEAT_ALPHA = 0.55f
        const val SPOKE_SEAT_DP = 1.4f

        /**
         * The recess the plates lie in: [HOUSING_INNER]..[HOUSING_OUTER].
         *
         * Wider than the coil annulus at both ends. Inside [SPOKE_INNER] it is
         * the dark gap that separates the core from the assembly and the seat
         * for [HUB_FACTOR]; outside [SPOKE_OUTER] it is the lip whose shadow
         * falls back across the plates.
         */
        const val HOUSING_INNER = 0.34f
        const val HOUSING_OUTER = 0.965f
        const val HOUSING_ALPHA = 0.72f

        /** The metal hub ring, between the dark gap and the inner seat. */
        const val HUB_FACTOR = 0.385f
        const val HUB_WIDTH_DP = 2.2f
        const val HUB_ALPHA = 0.62f

        /**
         * How far down the plates the outer seat's shadow reaches, as a
         * fraction of the annulus' width.
         */
        const val SEAT_SHADOW_SPAN = 0.16f
        const val SEAT_SHADOW_ALPHA = 0.60f

        /** The plate face, inner edge to outer. Bright where the core lights it. */
        const val PLATE_INNER_ALPHA = 0.62f
        const val PLATE_OUTER_ALPHA = 0.20f

        /** How far the plate's inner edge is pushed toward white. */
        const val PLATE_HOT_WHITENESS = 0.25f

        /**
         * The coils turn against the chrome, slowly. Same clock, opposite sign:
         * two things moving together read as one rigid disc, and the reactor is
         * supposed to look like an assembly.
         */
        const val SPOKE_SPIN_RATIO = 0.35f

        /** The coil annulus' centreline, which is where [SPOKE_GAP_DEG] is measured. */
        const val SPOKE_RADIUS = (SPOKE_INNER + SPOKE_OUTER) / 2f

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
        const val CORE_ALPHA = 0.46f

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

        /**
         * The shaded side of the ball. A shade off [SUBSTRATE_COLOR] rather
         * than black: the terminator crosses the outer band where the substrate
         * has already faded out, so at the limb it is drawn against the bloom,
         * and black there is a smudge rather than a shadow.
         */
        val TERMINATOR_COLOR = 0xFF03060E.toInt()

        /**
         * The recess, and the machined ring in it.
         *
         * The housing is darker than [SUBSTRATE_COLOR] because it has to read as
         * a hole cut into that ball rather than as more of the same surface.
         */
        val HOUSING_COLOR = 0xFF01030A.toInt()
        val HUB_COLOR = 0xFFA8BDD2.toInt()
        val HUB_SHADOW_COLOR = 0xFF1B2836.toInt()

        // --- the light -------------------------------------------------------

        /**
         * The key light, as a direction in the ball's own space: x right, y UP,
         * z toward the viewer. Up, to the left, and slightly in front.
         *
         * Written out component by component because the web shader's
         * `normalize(vec3(-0.46 + kx, 0.54 + ky, 0.70))` is the same three
         * numbers — it wanders a couple of degrees about them — and
         * `reactor_orb_test.py` compares them. The shader has a real sphere
         * normal and takes `dot(n, L)` per pixel; this file has no per-pixel
         * shader at all and fakes that cosine with gradients whose centres are
         * offset along this direction. Faking it from a DIFFERENT direction is
         * the loudest drift available to us — a highlight up-left on the phone
         * and up-right in the browser is the first thing anybody would see.
         */
        const val LIGHT_X = -0.46f
        const val LIGHT_Y = 0.54f
        const val LIGHT_Z = 0.70f

        /**
         * The fill: dim, cool, and from the opposite corner.
         *
         * One light in a black room gives a crescent moon. The fill is what
         * leaves the far side of the ball readable, and its own small
         * catchlight low and to the right is the second one — a glass ball on a
         * desk has two, and a drawing of a glass ball has one.
         */
        const val FILL_X = 0.60f
        const val FILL_Y = -0.46f
        const val FILL_Z = 0.64f

        /**
         * The same direction flattened onto the screen and normalised, y still
         * UP. Skia's y points DOWN, so [litY] subtracts it; nothing else in
         * this file may touch it without going through [litX] and [litY].
         *
         * `normalize(LIGHT_X, LIGHT_Y)`, written out because `const val` wants
         * a literal. The test recomputes it.
         */
        const val LIGHT_DIR_X = -0.6485f
        const val LIGHT_DIR_Y = 0.7612f

        /** The fill, flattened the same way. */
        const val FILL_DIR_X = 0.7936f
        const val FILL_DIR_Y = -0.6084f

        /** How much the fill lifts the side the key cannot reach. */
        const val FILL_ALPHA = 0.14f

        /**
         * How far toward the light the ball's shading is struck, as a fraction
         * of its radius.
         *
         * The true diffuse pole — where `n` equals the light — is out at
         * `length(LIGHT_X, LIGHT_Y)`, about 0.71 of the radius. A gradient
         * centred out there spends all its falloff in the last third of the
         * ball and reads as a crescent moon, so it is pulled in. This is the
         * number that turns a disc into a ball; at zero the orb is concentric
         * rings again however much else is stacked on it.
         */
        const val SPHERE_LIGHT_OFFSET = 0.35f

        /** The lit near side: how far it reaches, and how hard it lifts. */
        const val SPHERE_LIT_R = 0.95f
        const val SPHERE_LIT_ALPHA = 0.26f
        const val SPHERE_LIT_WHITENESS = 0.35f

        /**
         * The terminator's radius, larger than the ball on purpose: the shading
         * is still climbing when it reaches the far limb, instead of having
         * bottomed out into a flat black rind partway across.
         */
        const val TERMINATOR_R = 1.55f
        const val TERMINATOR_ALPHA = 0.44f

        /**
         * How far the lit point wanders, and how fast. Both deliberately below
         * the threshold of anything you could point at: the orb must never be
         * completely still, and must never be seen to loop.
         */
        const val SPHERE_WANDER = 0.06f
        const val SPHERE_WANDER_RATE = 0.29f

        /** The assembly's shadow on the ball behind it. */
        const val HOUSING_SHADOW_OFFSET = 0.03f
        const val HOUSING_SHADOW_SPREAD = 0.05f
        const val HOUSING_SHADOW_ALPHA = 0.62f

        /**
         * How dark the recess floor goes on the side away from the light. The
         * recess's own occlusion: a wall catching light on one side and a wall
         * in shadow opposite it is the difference between a recess and a hole
         * cut out of a picture.
         */
        const val HOUSING_WALL_ALPHA = 0.38f

        /** How far the machined parts' lit edges are pushed toward white. */
        const val HUB_SHEEN = 0.30f
        const val SEAT_SHEEN = 0.30f

        /**
         * The plates' lighting rule, and one of the three things pinned across
         * both implementations: a plate's brightness is
         *
         *     PLATE_LIGHT_BASE + PLATE_LIGHT_GAIN * max(0, cos(plate - light))
         *
         * so the plate facing the light is fully lit and the plate opposite it
         * falls to the base. The two sum to 1: the brightest plate is exactly
         * as bright as every plate used to be, and the ring gains its depth by
         * the others giving some up rather than by the whole assembly getting
         * hotter.
         *
         * The shader carries these two numbers verbatim and takes the same
         * cosine per PIXEL — `radial` against `Lxy`, which is this file's
         * [LIGHT_DIR_X]/[LIGHT_DIR_Y]. Ten plates at ten identical brightnesses
         * are a printed ring, however carefully each one is shaded across its
         * own thickness.
         */
        const val PLATE_LIGHT_BASE = 0.50f
        const val PLATE_LIGHT_GAIN = 0.50f

        /**
         * The specular, and the second thing pinned across both.
         *
         * [SPECULAR_POWER] is the shader's tight Blinn-Phong exponent — it has
         * three lobes and this file has the same three, one gradient each. A
         * Canvas has no exponent to give, so each highlight is a gradient sized
         * off its lobe rather than by eye:
         *
         *  * [SPECULAR_OFFSET] is `length(normalize(L + view).xy)` — where the
         *    half-vector actually meets a unit sphere, which is where a
         *    highlight goes. It is not "about a third of the way out"; it is
         *    that number, and the shader puts its own highlight there because
         *    the same arithmetic happens per pixel;
         *  * [SPECULAR_R] is where a lobe of that exponent has fallen to 2% —
         *    `sqrt(2 * ln(50) / power)` — so the gradient ends where the lobe
         *    does;
         *  * [SPECULAR_HALF] is the fraction of that radius at which the lobe
         *    is at half intensity, `sqrt(2 * ln(2) / power) / SPECULAR_R`, and
         *    it is where the middle stop sits at half alpha. That is what makes
         *    the falloff steep: most of the brightness inside a fifth of the
         *    ball, and a thin tail after it.
         */
        const val SPECULAR_POWER = 96f
        const val SPECULAR_OFFSET = 0.386f
        const val SPECULAR_R = 0.285f
        const val SPECULAR_HALF = 0.421f
        const val SPECULAR_ALPHA = 0.55f

        /**
         * The second lobe, off the same key: wide enough to be the sheen around
         * the point rather than the point itself.
         *
         * Tinted rather than white. The tight lobe covers a hundredth of the
         * face and can be white; this one covers a good part of it, and white
         * over a good part of the face is how a coloured orb turns grey.
         */
        const val SPECULAR_WIDE_POWER = 16f
        const val SPECULAR_WIDE_R = 0.699f
        const val SPECULAR_WIDE_ALPHA = 0.16f
        const val SPECULAR_WIDE_TINT = 0.45f

        /** The fill's catchlight, low and to the right. */
        const val FILL_SPECULAR_POWER = 46f
        const val FILL_SPECULAR_OFFSET = 0.421f
        const val FILL_SPECULAR_R = 0.412f
        const val FILL_SPECULAR_ALPHA = 0.18f

        /**
         * How far the highlight drifts, and how fast. A specular nailed to one
         * pixel reads as a sticker on the glass; a specular that moves a
         * percent of the ball's width over a few seconds reads as glass.
         */
        const val SPECULAR_DRIFT = 0.018f
        const val SPECULAR_DRIFT_RATE = 0.19f

        /**
         * The fresnel arc just inside the ball's edge: how far its gradient is
         * struck from the lit point, and how bright it gets.
         *
         * At [FRESNEL_OFFSET] the far limb is at the gradient's full radius and
         * the near limb is at about half of it, so a stop late in the ramp
         * lands the bright arc on the side away from the light and nowhere
         * else.
         */
        const val FRESNEL_OFFSET = 0.30f
        const val FRESNEL_ALPHA = 0.30f

        /**
         * The wide skirt of the bloom, as a multiple of the halo's own radius,
         * and how much dimmer it is. Wider and dimmer than the object is what
         * makes light look like it is in the air rather than painted on.
         */
        const val BLOOM_WIDE = 1.55f
        const val BLOOM_WIDE_ALPHA = 0.38f

        /** How much the bloom breathes, and how fast. Nothing else's rate. */
        const val HALO_BREATH = 0.03f
        const val HALO_BREATH_RATE = 0.41f

        private val BLOB_STOPS = floatArrayOf(0f, 0.45f, 1f)
        private val CORE_STOPS = floatArrayOf(0f, 0.45f, 1f)
        private val HALO_STOPS = floatArrayOf(0f, 0.55f, 1f)
        private val BLOOM_WIDE_STOPS = floatArrayOf(0f, 0.42f, 1f)
        private val SPECULAR_STOPS = floatArrayOf(0f, SPECULAR_HALF, 1f)
        private val RIM_STOPS = floatArrayOf(0f, 0.5f, 1f)
        private val SWEEP_STOPS = floatArrayOf(0f, 0.62f, 0.92f, 1f)

        /** Transparent through the near side, then down into the terminator. */
        private val TERMINATOR_STOPS = floatArrayOf(0f, 0.45f, 1f)

        /** The lit near side, gone before it reaches the far one. */
        private val SPHERE_LIT_STOPS = floatArrayOf(0f, 0.45f, 1f)

        /** Nothing until the last seventh, so the arc stays thin. */
        private val FRESNEL_STOPS = floatArrayOf(0f, 0.86f, 1f)

        /**
         * The limb, in four stops rather than two: shallow from 58% of the ball
         * and steep at the edge. A curve is what is being drawn, and a single
         * ramp into the last fifth reads as a dark rind with an inside edge.
         */
        private val EDGE_SHADOW_STOPS = floatArrayOf(0f, 0.58f, 0.86f, 1f)

        /**
         * Flat across the recess floor, lifting over the last third toward the
         * outer lip. Clipped to the annulus, so the first stop is never seen.
         */
        private val HOUSING_STOPS = floatArrayOf(0f, 0.66f, 1f)

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
