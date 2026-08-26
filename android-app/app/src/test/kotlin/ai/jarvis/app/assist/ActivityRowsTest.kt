package ai.jarvis.app.assist

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The strip's arithmetic (M61), on the JVM; the vocabulary itself is pinned by the mirror. */
class ActivityRowsTest {
    private fun json(text: String) = JSONObject(text)

    @Test
    fun aToolCallIsOneRowFromStartToFinishUpdatedInPlace() {
        val rows = ActivityRows()
        assertTrue(rows.apply("jarvis_tool_started", json("""{"name":"get_state","arguments":{"entity_id":"light.hall"},"round":1,"index":0}"""), 1L))
        assertEquals(1, rows.rows.size)
        assertEquals(ActivityRows.State.LIVE, rows.rows[0].state)
        assertEquals("entity_id: light.hall", rows.rows[0].detail)
        assertTrue(rows.apply("jarvis_tool_finished", json("""{"name":"get_state","round":1,"index":0,"ok":true,"duration_ms":84}"""), 2L))
        assertEquals(1, rows.rows.size)
        assertEquals(ActivityRows.State.DONE, rows.rows[0].state)
        assertEquals("84 ms", rows.rows[0].detail)
    }

    @Test
    fun aButtonPressedTwiceIsTwoRows() {
        val rows = ActivityRows()
        rows.apply("jarvis_mqtt_event", json("""{"entity_id":"event.hall_remote_action","event_type":"on","at":1}"""), 1L)
        rows.apply("jarvis_mqtt_event", json("""{"entity_id":"event.hall_remote_action","event_type":"on","at":2}"""), 2L)
        assertEquals(2, rows.rows.size)
        assertEquals("hall remote action", rows.rows[0].title)
        assertEquals("pressed · on", rows.rows[0].detail)
    }

    @Test
    fun onlyReadingsAreSensorRowsAndACameraLookNamesItself() {
        val rows = ActivityRows()
        assertFalse(rows.apply("state_changed", json("""{"entity_id":"light.hall","new_state":{"state":"on"}}"""), 1L))
        assertTrue(rows.apply("state_changed", json("""{"entity_id":"sensor.garage_temperature","new_state":{"state":"12.5","attributes":{"friendly_name":"Garage temperature","unit_of_measurement":"°C"}}}"""), 1L))
        assertEquals("Garage temperature", rows.rows[0].title)
        assertEquals("12.5 °C", rows.rows[0].detail)
        rows.apply("vision_look_started", json("""{"id":"l1","camera":"Kitchen","question":"anyone?"}"""), 2L)
        assertEquals("looking · Kitchen", rows.lookingCaption())
        rows.apply("vision_look_finished", json("""{"id":"l1","camera":"Kitchen","duration_ms":620}"""), 3L)
        assertEquals("", rows.lookingCaption())
    }

    @Test
    fun theStripKeepsADozenNewestFirst() {
        val rows = ActivityRows()
        for (i in 0 until 15) rows.apply("jarvis_tool_started", json("""{"name":"tool_$i","round":1,"index":$i}"""), i.toLong())
        assertEquals(ActivityRows.CAP, rows.rows.size)
        assertEquals("tool_14", rows.rows[0].title)
        assertEquals("tool_3", rows.rows[ActivityRows.CAP - 1].title)
    }
}
