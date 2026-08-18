package ai.jarvis.app.tasks

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Reading a task off the wire, against the REAL org.json.
 *
 * The Python mirror (`android-app/tools/task_board_test.py`) covers the model's
 * arithmetic and can pin the constants, but it cannot reproduce the one thing
 * that actually goes wrong here — `org.json`'s own handling of a JSON null.
 *
 * jarvis-core sends `"fraction": null` whenever a percentage would be a guess.
 * `optDouble("fraction")` answers **NaN** for that; `optDouble("fraction", 0.0)`
 * answers **0.0**. Both are plausible-looking code, and both turn "we do not
 * know how far along this is" into a bar that has visibly not moved. That is
 * indistinguishable from a task which has not started, on the exact surface
 * somebody is watching to find out which.
 *
 * So these are assertions about a library's behaviour as much as about ours,
 * which is why they need the real jar rather than the stubbed android.jar.
 */
class TaskFramesTest {

    private fun task(vararg pairs: Pair<String, Any?>): JSONObject {
        val json = JSONObject().put("id", "t1").put("title", "Read twelve pages")
        for ((k, v) in pairs) json.put(k, v ?: JSONObject.NULL)
        return json
    }

    @Test
    fun `an explicit null fraction reads as no fraction, not as zero`() {
        val row = TaskFrames.row(task("fraction" to null, "status" to "running"))!!
        assertNull(row.fraction)
        assertEquals(TaskBoard.Bar.INDETERMINATE, row.bar)
    }

    @Test
    fun `a missing fraction reads as no fraction`() {
        val row = TaskFrames.row(task("status" to "running"))!!
        assertNull(row.fraction)
    }

    @Test
    fun `the library really does answer NaN for a JSON null`() {
        // The assumption the parser is written around, asserted rather than
        // assumed. If org.json ever changed this, the guard above would become
        // dead code and nobody would notice.
        val raw = task("fraction" to null)
        assertTrue(raw.optDouble("fraction").isNaN())
        assertEquals(0.0, raw.optDouble("fraction", 0.0), 0.0)
        assertTrue(raw.isNull("fraction"))
    }

    @Test
    fun `a real fraction survives, including a real zero`() {
        assertEquals(0.5, TaskFrames.row(task("fraction" to 0.5))!!.fraction!!, 1e-9)
        val zero = TaskFrames.row(task("fraction" to 0.0, "status" to "running"))!!
        assertEquals(0.0, zero.fraction!!, 1e-9)
        assertEquals(TaskBoard.Bar.DETERMINATE, zero.bar)
    }

    @Test
    fun `a record with no id is refused rather than drawn`() {
        assertNull(TaskFrames.row(JSONObject().put("title", "x")))
        assertNull(TaskFrames.row(null))
    }

    @Test
    fun `an unknown status falls back to something drawable`() {
        assertEquals(TaskBoard.Status.QUEUED, TaskFrames.row(task("status" to "exploded"))!!.status)
    }

    @Test
    fun `every status jarvis-core can send is understood`() {
        for (name in listOf("queued", "running", "blocked", "done", "error", "cancelled")) {
            val row = TaskFrames.row(task("status" to name))!!
            assertEquals(name.uppercase(), row.status.name)
        }
    }

    @Test
    fun `an event frame is unwrapped down to its task`() {
        val frame = JSONObject()
            .put("id", 3)
            .put("type", "event")
            .put(
                "event",
                JSONObject()
                    .put("event_type", TaskBoard.EVENT_UPDATED)
                    .put("data", JSONObject().put("task", task("status" to "running"))),
            )
        assertEquals(TaskBoard.EVENT_UPDATED, TaskFrames.eventTypeOf(frame))
        assertEquals("t1", TaskFrames.rowFromEvent(frame)!!.id)
    }

    @Test
    fun `an event with no task does not blow up`() {
        val frame = JSONObject().put("event", JSONObject().put("event_type", "jarvis_task_added"))
        assertNull(TaskFrames.rowFromEvent(frame))
        assertEquals("", TaskFrames.eventTypeOf(null))
    }

    @Test
    fun `a listing drops the unusable rows and keeps the rest`() {
        val result = JSONObject().put(
            "tasks",
            org.json.JSONArray()
                .put(task("status" to "running"))
                .put(JSONObject().put("title", "no id"))
                .put(JSONObject.NULL),
        )
        val rows = TaskFrames.rowsFromList(result)
        assertEquals(1, rows.size)
        assertEquals("t1", rows[0].id)
    }

    @Test
    fun `a listing that is not one is empty rather than a crash`() {
        assertEquals(emptyList<TaskBoard.Row>(), TaskFrames.rowsFromList(null))
        assertEquals(emptyList<TaskBoard.Row>(), TaskFrames.rowsFromList(JSONObject()))
    }

    @Test
    fun `the frames it sends are the ones the server documents`() {
        assertEquals("jarvis/tasks/list", TaskFrames.TYPE_LIST)
        assertTrue(TaskFrames.listArgs().getBoolean("active"))
        assertEquals("subscribe_events", TaskFrames.subscribe(2, "x").getString("type"))
        assertEquals("x", TaskFrames.subscribe(2, "x").getString("event_type"))
        assertEquals(2, TaskFrames.subscribe(2, "x").getInt("id"))
    }

    @Test
    fun `the listing carries no id of its own`() {
        // `JarvisChannel.request` allocates the id and merges a payload over
        // the top of its own frame. An `id` in here would overwrite the real
        // one and orphan the pending entry for ever — the same trap the channel
        // already documents at its `request` builder.
        assertTrue(!TaskFrames.listArgs().has("id"))
        assertTrue(!TaskFrames.listArgs().has("type"))
    }

    @Test
    fun `steps and counts come through for the row under the bar`() {
        val row = TaskFrames.row(
            task("done_steps" to 3, "total_steps" to 8, "detail" to "reading page 4")
        )!!
        assertEquals("3 of 8", row.steps)
        assertEquals("reading page 4", row.detail)
    }

    @Test
    fun `a board fed from the wire shows what is running`() {
        val board = TaskBoard { 1_000_000L }
        board.upsert(TaskFrames.row(task("status" to "running", "updated" to 999.0))!!)
        assertNotNull(board.visible().firstOrNull())
        assertTrue(board.running)
        assertEquals("1 running", board.headline())
    }
}
