package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone builds the graph the console builds (M61).
 *
 * The fixture and the expectations are `tests/contracts/knowledge_graph.json`,
 * carried here verbatim because a JVM test's working directory is not the
 * repository; `android-app/tools/knowledge_graph_mirror_test.py` fails if this
 * file and the contract drift apart.
 */
class KnowledgeGraphTest {
    private val notes = listOf(
        KnowledgeGraph.NoteLike("n1", "Boiler service", tags = listOf("house"), links = listOf("Meter readings")),
        KnowledgeGraph.NoteLike("n2", "Meter readings", tags = listOf("house", "energy")),
        KnowledgeGraph.NoteLike("n3", "Garden plan", tags = listOf("garden")),
    )
    private val memory = listOf(
        KnowledgeGraph.MemoryLike("m1", "The spare key is under the blue pot", tags = listOf("house")),
        KnowledgeGraph.MemoryLike("m2", "Boiler serviced 2026-08-26", tags = listOf("energy")),
    )

    @Test
    fun buildsExactlyTheContractsNodesAndEdges() {
        val (nodes, edges) = KnowledgeGraph.build(notes, memory)
        assertEquals(
            listOf("note:n1", "note:n2", "note:n3", "memory:m1", "memory:m2"),
            nodes.map { it.id },
        )
        assertEquals("The spare key is under the bl…", nodes[3].label)
        assertEquals("Boiler serviced 2026-08-26", nodes[4].label)
        assertEquals(
            listOf(
                KnowledgeGraph.Edge("note:n1", "note:n2", KnowledgeGraph.EdgeKind.LINK),
                KnowledgeGraph.Edge("note:n1", "memory:m1", KnowledgeGraph.EdgeKind.TAG),
                KnowledgeGraph.Edge("note:n2", "memory:m1", KnowledgeGraph.EdgeKind.TAG),
                KnowledgeGraph.Edge("note:n2", "memory:m2", KnowledgeGraph.EdgeKind.TAG),
            ),
            edges,
        )
    }

    @Test
    fun theLayoutIsAFunctionOfItsInputAndKeepsNodesApart() {
        val (nodes, edges) = KnowledgeGraph.build(notes, memory)
        val first = KnowledgeGraph.layout(nodes, edges, 600f, 400f)
        val again = KnowledgeGraph.layout(nodes, edges, 600f, 400f)
        assertEquals(first.nodes, again.nodes)
        for (i in first.nodes.indices) for (j in i + 1 until first.nodes.size) {
            val a = first.nodes[i]; val b = first.nodes[j]
            assertNotEquals("two nodes on one spot", 0f, (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y))
        }
        val pad = 30f
        assertTrue(first.nodes.all { it.x >= pad - 1 && it.x <= 600f - pad + 1 && it.y >= pad - 1 && it.y <= 400f - pad + 1 })
    }

    @Test
    fun aNoteToolTouchesTheNoteItNames() {
        val (nodes, _) = KnowledgeGraph.build(notes, memory)
        assertEquals(listOf("note:n2"), KnowledgeGraph.touchedBy("note_append", mapOf("note_id" to "n2"), nodes))
        assertEquals(listOf("note:n1"), KnowledgeGraph.touchedBy("note_search", mapOf("query" to "boiler"), nodes))
        assertEquals(emptyList<String>(), KnowledgeGraph.touchedBy("get_state", mapOf("entity_id" to "light.hall"), nodes))
    }
}
