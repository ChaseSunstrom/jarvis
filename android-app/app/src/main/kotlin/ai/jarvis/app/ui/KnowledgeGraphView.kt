package ai.jarvis.app.ui

import ai.jarvis.app.assist.KnowledgeGraph
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.view.View
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The knowledge graph under the reactor, drawn the console's way (M61, then
 * M64 for the look): a hairline for a link, a fainter one for a shared tag; a
 * note as a panel-filled point on a dim ring with a dim core, a memory as a
 * faint solid dot; the name beneath in the body face at the `--jv-fs-xs` step
 * in the dim text colour, knocked out of whatever it crosses by a stroke in
 * the ground colour (`Graph.svelte`'s `paint-order: stroke`); and the accent
 * only on the points a turn touched. Memories were gold — a semantic colour
 * (held) on a thing that is not held — and the labels were mono, which is for
 * data, in the faint colour. The arithmetic is [KnowledgeGraph]; this only
 * paints, in tokens.
 *
 * GONE until there is a node — not merely blank: a blank 200 dp slot pushed
 * the home screen's nav row off a pixel_2's screen for the instrumented suite
 * — so a house with no notes shows the reactor alone, as it did. When it is
 * shown it sizes itself: the console's graph is a 2:1 box that takes the
 * panel's width, and a host that gives this a WRAP_CONTENT height gets the
 * same, rather than a number typed into each host.
 */
class KnowledgeGraphView(context: Context) : View(context) {

    private var layout: KnowledgeGraph.Layout? = null
    private var lit: Set<String> = emptySet()

    init {
        visibility = GONE
    }

    private val edgePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = JarvisUi.dp(context, JarvisUi.Space.HAIRLINE).toFloat()
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = JarvisUi.dp(context, JarvisUi.Space.HAIRLINE).toFloat()
    }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.BODY_FACE
        textSize = JarvisUi.sp(context, JarvisUi.Type.HINT)
        color = JarvisTokens.Color.TEXT_DIM
    }

    /** The knockout behind a label: the ground colour, a stroke wide, drawn first. */
    private val knockoutPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.BODY_FACE
        textSize = JarvisUi.sp(context, JarvisUi.Type.HINT)
        color = JarvisTokens.Color.BG
        style = Paint.Style.STROKE
        strokeWidth = JarvisUi.dp(context, JarvisUi.Space.TIGHT).toFloat()
        strokeJoin = Paint.Join.ROUND
    }

    fun render(nodes: List<KnowledgeGraph.Node>, edges: List<KnowledgeGraph.Edge>) {
        val w = if (width > 0) width.toFloat() else DEFAULT_WIDTH
        val h = if (height > 0) height.toFloat() else DEFAULT_HEIGHT
        layout = if (nodes.isEmpty()) null else KnowledgeGraph.layout(nodes, edges, w, h)
        visibility = if (layout == null) GONE else VISIBLE
        JarvisUi.describe(this, if (nodes.isEmpty()) null else "${nodes.size} things Jarvis knows, ${edges.size} connections")
        requestLayout()
        invalidate()
    }

    /** Light the nodes a tool call touched; they fade when the next turn starts. */
    fun pulse(ids: Collection<String>) {
        lit = ids.toSet()
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        val h = when (MeasureSpec.getMode(heightMeasureSpec)) {
            MeasureSpec.EXACTLY -> MeasureSpec.getSize(heightMeasureSpec)
            MeasureSpec.AT_MOST -> minOf(MeasureSpec.getSize(heightMeasureSpec), (w * ASPECT).toInt())
            else -> (w * ASPECT).toInt()
        }
        setMeasuredDimension(w, h)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        val current = layout ?: return
        if (w > 0 && h > 0) layout = KnowledgeGraph.layout(current.nodes.map { it.node }, current.edges, w.toFloat(), h.toFloat())
    }

    override fun onDraw(canvas: Canvas) {
        val current = layout ?: return
        val at = HashMap<String, KnowledgeGraph.Placed>()
        for (p in current.nodes) at[p.node.id] = p
        for (edge in current.edges) {
            val a = at[edge.from] ?: continue
            val b = at[edge.to] ?: continue
            val touched = edge.from in lit || edge.to in lit
            edgePaint.color = when {
                touched -> JarvisUi.ACCENT
                edge.kind == KnowledgeGraph.EdgeKind.LINK -> JarvisTokens.Color.LINE
                else -> JarvisTokens.Color.LINE_HAIR
            }
            canvas.drawLine(a.x, a.y, b.x, b.y, edgePaint)
        }
        val noteR = JarvisUi.dp(context, NOTE_DP).toFloat()
        val coreR = JarvisUi.dp(context, CORE_DP).toFloat()
        val memoryR = JarvisUi.dp(context, MEMORY_DP).toFloat()
        for (p in current.nodes) {
            val isLit = p.node.id in lit
            if (p.node.kind == KnowledgeGraph.NodeKind.NOTE) {
                // `.body`: the panel colour on a dim ring; lit, the accent on
                // the strong wash. `.core`: a dim dot in the middle.
                fillPaint.color = if (isLit) JarvisTokens.Color.WASH_STRONG else JarvisTokens.Color.PANEL
                ringPaint.color = if (isLit) JarvisUi.ACCENT else JarvisTokens.Color.TEXT_DIM
                canvas.drawCircle(p.x, p.y, noteR, fillPaint)
                canvas.drawCircle(p.x, p.y, noteR, ringPaint)
                fillPaint.color = if (isLit) JarvisUi.ACCENT else JarvisTokens.Color.TEXT_DIM
                canvas.drawCircle(p.x, p.y, coreR, fillPaint)
            } else {
                // `.memory .body`: a faint solid dot, the accent when lit.
                fillPaint.color = if (isLit) JarvisUi.ACCENT else JarvisTokens.Color.TEXT_FAINT
                canvas.drawCircle(p.x, p.y, memoryR, fillPaint)
            }
        }
        // Labels after every point, kept inside the box and off each other.
        // The console dodges a name that would land on an earlier one by
        // sending it above its node (M52) and runs every name towards the
        // middle of a box wide enough for them not to meet there. A phone's
        // box is not that wide: tried here, that rule piled five names up in
        // the centre. So a name is centred under its point as M61 had it,
        // goes above on a clash, then slides to whichever side of its point
        // is clear, and only then overlaps; and one that would leave the box
        // at any edge — top and bottom included, since the body face is
        // taller than the mono this used to draw in — is slid back in. A
        // name half off the edge is not a name.
        val placed = ArrayList<android.graphics.RectF>()
        val gap = JarvisUi.dp(context, JarvisUi.Space.MICRO).toFloat()
        val ascent = -labelPaint.ascent()
        val descent = labelPaint.descent()
        for (p in current.nodes.sortedWith(compareBy({ it.x }, { it.y }))) {
            val label = p.node.label
            val r = if (p.node.kind == KnowledgeGraph.NodeKind.NOTE) noteR else memoryR
            val w = labelPaint.measureText(label)
            val xMax = (width - w - gap).coerceAtLeast(gap)
            val centred = (p.x - w / 2).coerceIn(gap, xMax)
            val below = p.y + r + gap + ascent
            val above = p.y - r - gap - descent
            fun rectAt(x: Float, baseline: Float) =
                android.graphics.RectF(x, baseline - ascent, x + w, baseline + descent)
            fun fits(rect: android.graphics.RectF) =
                rect.top >= 0f && rect.bottom <= height && placed.none { android.graphics.RectF.intersects(it, rect) }
            var rect = rectAt(centred, below)
            if (!fits(rect)) {
                val candidates = sequenceOf(
                    rectAt(centred, above),
                    rectAt((p.x + r + gap).coerceIn(gap, xMax), p.y + ascent / 2f),
                    rectAt((p.x - r - gap - w).coerceIn(gap, xMax), p.y + ascent / 2f),
                )
                rect = candidates.firstOrNull { fits(it) } ?: rect
            }
            // Nowhere is clear: keep the box's edge, which a name must never
            // cross, and accept the overlap.
            val baseline = (rect.top + ascent).coerceIn(ascent + gap, (height - descent - gap).coerceAtLeast(ascent + gap))
            val x = rect.left
            rect = rectAt(x, baseline)
            placed.add(rect)
            labelPaint.color = if (p.node.id in lit) JarvisTokens.Color.TEXT_BRIGHT else JarvisTokens.Color.TEXT_DIM
            canvas.drawText(label, x, baseline, knockoutPaint)
            canvas.drawText(label, x, baseline, labelPaint)
        }
    }

    companion object {
        /** `Graph.svelte`'s radii: a note's body 6, its core 1.6, a memory 4 — in dp here. */
        const val NOTE_DP = JarvisUi.Space.SNUG
        const val CORE_DP = JarvisUi.Space.MICRO
        const val MEMORY_DP = JarvisUi.Space.TIGHT

        /** The console's graph box is twice as wide as it is tall. */
        const val ASPECT = 0.5f
        const val DEFAULT_WIDTH = 600f
        const val DEFAULT_HEIGHT = 300f
    }
}
