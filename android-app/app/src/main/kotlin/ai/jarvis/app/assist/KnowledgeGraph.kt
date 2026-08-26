package ai.jarvis.app.assist

import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * The knowledge graph's arithmetic, as pure functions (M61).
 *
 * A port of the console's `jarvis-web/src/lib/knowledge/graph.ts`: notes and
 * memory entries are nodes, `[[links]]` and shared tags are edges, and the
 * layout is the same seeded force simulation — so the phone draws the SAME
 * picture as the console for the same house. `tests/contracts/knowledge_graph.json`
 * pins the nodes and edges both produce; `android-app/tools/knowledge_graph_mirror_test.py`
 * pins the constants that shape the layout. No Android in it.
 */
object KnowledgeGraph {
    enum class NodeKind { NOTE, MEMORY }
    enum class EdgeKind { LINK, TAG }

    data class Node(val id: String, val label: String, val kind: NodeKind, val tags: List<String> = emptyList())
    data class Edge(val from: String, val to: String, val kind: EdgeKind)
    data class Placed(val node: Node, val x: Float, val y: Float)
    data class Layout(val nodes: List<Placed>, val edges: List<Edge>, val width: Float, val height: Float)

    data class NoteLike(val id: String, val title: String, val tags: List<String> = emptyList(), val links: List<String> = emptyList(), val backlinks: List<String> = emptyList())
    data class MemoryLike(val id: String, val text: String, val tags: List<String> = emptyList())

    /** A tag shared by more nodes than this says nothing about any two of them. */
    const val TAG_FANOUT = 8
    /** A memory's label is a name, not the fact. */
    const val LABEL_CHARS = 30
    const val ITERATIONS = 220
    const val LINK_WEIGHT = 0.5f
    const val TAG_WEIGHT = 0.2f

    /** Nodes and edges from what the two lists say. */
    fun build(notes: List<NoteLike>, memory: List<MemoryLike>): Pair<List<Node>, List<Edge>> {
        val nodes = ArrayList<Node>()
        for (n in notes) nodes.add(Node("note:${n.id}", n.title, NodeKind.NOTE, n.tags))
        for (m in memory) {
            val label = if (m.text.length > LABEL_CHARS) m.text.substring(0, LABEL_CHARS - 1) + "…" else m.text
            nodes.add(Node("memory:${m.id}", label, NodeKind.MEMORY, m.tags))
        }
        val known = nodes.map { it.id }.toHashSet()
        val byTitle = HashMap<String, String>()
        for (n in notes) byTitle[n.title.lowercase()] = "note:${n.id}"
        val seen = HashSet<String>()
        val edges = ArrayList<Edge>()
        fun add(from: String, to: String, kind: EdgeKind) {
            if (from == to || from !in known || to !in known) return
            val key = listOf(from, to).sorted().joinToString("|")
            if (!seen.add(key)) return
            edges.add(Edge(from, to, kind))
        }
        for (n in notes) {
            for (target in n.links + n.backlinks) {
                val to = if ("note:$target" in known) "note:$target" else byTitle[target.lowercase()]
                if (to != null) add("note:${n.id}", to, EdgeKind.LINK)
            }
        }
        val byTag = LinkedHashMap<String, ArrayList<String>>()
        for (node in nodes) for (tag in node.tags) byTag.getOrPut(tag) { ArrayList() }.add(node.id)
        for (ids in byTag.values) {
            if (ids.size > TAG_FANOUT) continue
            for (i in ids.indices) for (j in i + 1 until ids.size) add(ids[i], ids[j], EdgeKind.TAG)
        }
        return nodes to edges
    }

    /** The console's PRNG, bit for bit: FNV-1a seeded, xorshift-multiply stepped. */
    class Seeded(text: String) {
        private var h: Int = 2166136261.toInt()

        init {
            for (ch in text) h = (h xor ch.code) * 16777619
        }

        fun next(): Float {
            h = (h xor (h ushr 15)) * 2246822507.toInt()
            h = (h xor (h ushr 13)) * 3266489909.toInt()
            h = h xor (h ushr 16)
            return ((h.toLong() and 0xFFFFFFFFL).toDouble() / 4294967296.0).toFloat()
        }
    }

    /** Place the nodes: Fruchterman–Reingold in miniature, seeded, fitted to the box. */
    fun layout(nodes: List<Node>, edges: List<Edge>, width: Float = 600f, height: Float = 400f, iterations: Int = ITERATIONS): Layout {
        val n = nodes.size
        if (n == 0) return Layout(emptyList(), emptyList(), width, height)
        val rand = Seeded(nodes.joinToString("\n") { it.id })
        val pad = max(30f, min(width, height) * 0.12f)
        val xs = FloatArray(n) { pad + rand.next() * (width - 2 * pad) }
        val ys = FloatArray(n) { pad + rand.next() * (height - 2 * pad) }
        val index = HashMap<String, Int>()
        nodes.forEachIndexed { i, node -> index[node.id] = i }
        val links = edges.mapNotNull { e ->
            val a = index[e.from]; val b = index[e.to]
            if (a == null || b == null) null else Triple(a, b, if (e.kind == EdgeKind.LINK) LINK_WEIGHT else TAG_WEIGHT)
        }
        val k = sqrt(width * height / n) * 0.5f
        val gravity = (1.4f * k) / max(width, height)
        var temperature = min(width, height) / 6f
        val cool = temperature / iterations
        val dx = FloatArray(n)
        val dy = FloatArray(n)
        repeat(iterations) {
            dx.fill(0f); dy.fill(0f)
            for (i in 0 until n) for (j in i + 1 until n) {
                var ddx = xs[i] - xs[j]
                var ddy = ys[i] - ys[j]
                var d = hypot(ddx, ddy)
                if (d < 0.01f) {
                    ddx = (i - j) * 0.01f; ddy = 0.01f; d = hypot(ddx, ddy)
                }
                if (d > 3 * k) continue
                val force = (k * k) / d
                dx[i] += (ddx / d) * force; dy[i] += (ddy / d) * force
                dx[j] -= (ddx / d) * force; dy[j] -= (ddy / d) * force
            }
            for ((a, b, weight) in links) {
                val ddx = xs[a] - xs[b]
                val ddy = ys[a] - ys[b]
                val d = hypot(ddx, ddy).let { if (it == 0f) 0.01f else it }
                val force = ((d * d) / k) * weight
                dx[a] -= (ddx / d) * force; dy[a] -= (ddy / d) * force
                dx[b] += (ddx / d) * force; dy[b] += (ddy / d) * force
            }
            for (i in 0 until n) {
                dx[i] += (width / 2 - xs[i]) * gravity
                dy[i] += (height / 2 - ys[i]) * gravity
                val d = hypot(dx[i], dy[i]).let { if (it == 0f) 0.01f else it }
                val capped = min(d, temperature)
                xs[i] += (dx[i] / d) * capped
                ys[i] += (dy[i] / d) * capped
            }
            temperature = max(0.5f, temperature - cool)
        }
        val minX = xs.min(); val maxX = xs.max(); val minY = ys.min(); val maxY = ys.max()
        val spanX = maxX - minX; val spanY = maxY - minY
        val scale = min(
            if (spanX > 1f) (width - 2 * pad) / spanX else Float.POSITIVE_INFINITY,
            if (spanY > 1f) (height - 2 * pad) / spanY else Float.POSITIVE_INFINITY,
        )
        val cap = if (n <= 2) 1.5f else 3f
        val fit = if (scale.isFinite()) min(scale, cap) else 1f
        val offX = width / 2 - ((minX + maxX) / 2) * fit
        val offY = height / 2 - ((minY + maxY) / 2) * fit
        val placed = nodes.mapIndexed { i, node ->
            Placed(node, ((xs[i] * fit + offX) * 10).roundToInt() / 10f, ((ys[i] * fit + offY) * 10).roundToInt() / 10f)
        }
        return Layout(placed, edges.filter { it.from in index && it.to in index }, width, height)
    }

    /** Which nodes a tool call touched, from its name and arguments: graph node ids. */
    fun touchedBy(name: String, args: Map<String, Any?>, nodes: List<Node>): List<String> {
        if (!name.startsWith("note_")) return emptyList()
        val noteId = (args["note_id"] as? String) ?: (args["id"] as? String) ?: ""
        if (noteId.isNotEmpty() && nodes.any { it.id == "note:$noteId" }) return listOf("note:$noteId")
        val query = (args["query"] as? String) ?: (args["title"] as? String) ?: ""
        if (query.isEmpty()) return emptyList()
        val words = query.lowercase().split(Regex("[^a-z0-9]+")).filter { it.length > 2 }
        return nodes.filter { node -> node.kind == NodeKind.NOTE && words.any { node.label.lowercase().contains(it) } }.map { it.id }
    }
}
