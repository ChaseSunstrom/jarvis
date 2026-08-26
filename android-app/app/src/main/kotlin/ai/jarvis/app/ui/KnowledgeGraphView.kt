package ai.jarvis.app.ui

import ai.jarvis.app.assist.KnowledgeGraph
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.util.TypedValue
import android.view.View

/**
 * The knowledge graph under the reactor, drawn the console's way (M61): a
 * hairline for a link, a fainter one for a shared tag, a point per note or
 * memory with its name beneath, and the points a turn touched lit in the
 * accent. The arithmetic is [KnowledgeGraph]; this only paints, in tokens.
 * GONE until there is a node — not merely blank: a blank 200 dp slot pushed
 * the home screen's nav row off a pixel_2's screen for the instrumented suite
 * — so a house with no notes shows the reactor alone, as it did.
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
    private val nodePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.MONOSPACE
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL, context.resources.displayMetrics)
        color = JarvisUi.FAINT
    }

    fun render(nodes: List<KnowledgeGraph.Node>, edges: List<KnowledgeGraph.Edge>) {
        val w = if (width > 0) width.toFloat() else DEFAULT_WIDTH
        val h = if (height > 0) height.toFloat() else DEFAULT_HEIGHT
        layout = if (nodes.isEmpty()) null else KnowledgeGraph.layout(nodes, edges, w, h)
        visibility = if (layout == null) GONE else VISIBLE
        JarvisUi.describe(this, if (nodes.isEmpty()) null else "${nodes.size} things Jarvis knows, ${edges.size} connections")
        invalidate()
    }

    /** Light the nodes a tool call touched; they fade when the next turn starts. */
    fun pulse(ids: Collection<String>) {
        lit = ids.toSet()
        invalidate()
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
            edgePaint.color = if (edge.kind == KnowledgeGraph.EdgeKind.LINK) JarvisUi.DIM else JarvisUi.FAINT
            canvas.drawLine(a.x, a.y, b.x, b.y, edgePaint)
        }
        val r = JarvisUi.dp(context, NODE_DP).toFloat()
        for (p in current.nodes) {
            nodePaint.color = when {
                p.node.id in lit -> JarvisUi.ACCENT
                p.node.kind == KnowledgeGraph.NodeKind.NOTE -> JarvisUi.TEXT
                else -> JarvisUi.GOLD
            }
            canvas.drawCircle(p.x, p.y, r, nodePaint)
        }
        // Labels after every point, kept inside the box and off each other: the
        // console dodges a label that would land on another (M52); here one
        // that would is drawn above its point instead, and one that would
        // leave the box is slid back in. Ten names in a phone's width collide
        // otherwise, and a name half off the edge is not a name.
        val placed = ArrayList<android.graphics.RectF>()
        val gap = JarvisUi.dp(context, JarvisUi.Space.MICRO).toFloat()
        val ascent = -labelPaint.ascent()
        for (p in current.nodes) {
            val label = p.node.label
            val w = labelPaint.measureText(label)
            val x = (p.x - w / 2).coerceIn(gap, (width - w - gap).coerceAtLeast(gap))
            var baseline = p.y + r + gap + ascent
            var rect = android.graphics.RectF(x, baseline - ascent, x + w, baseline + labelPaint.descent())
            if (placed.any { android.graphics.RectF.intersects(it, rect) }) {
                baseline = p.y - r - gap - labelPaint.descent()
                rect = android.graphics.RectF(x, baseline - ascent, x + w, baseline + labelPaint.descent())
            }
            placed.add(rect)
            canvas.drawText(label, x, baseline, labelPaint)
        }
    }

    companion object {
        const val NODE_DP = 3
        const val DEFAULT_WIDTH = 600f
        const val DEFAULT_HEIGHT = 300f
    }
}
