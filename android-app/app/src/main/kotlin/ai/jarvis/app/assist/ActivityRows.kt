package ai.jarvis.app.assist

import org.json.JSONObject

/**
 * The living activity around the reactor, on the phone (M61).
 *
 * The same vocabulary as the console's `activity.svelte.ts`: which bus events
 * make a row, what kind each makes, and the cap. Both read
 * `tests/contracts/activity_rows.json`; `android-app/tools/activity_mirror_test.py`
 * fails when this file and the contract disagree. No Android in it, so the
 * arithmetic is testable on the JVM and the view only paints.
 */
class ActivityRows {
    enum class Kind { TOOL, TASK, SENSOR, CAMERA, MEMORY, MOMENT, APPROVAL, ERROR }
    enum class State { LIVE, DONE, FAILED }

    data class Row(
        val id: String,
        val kind: Kind,
        val title: String,
        val detail: String,
        val state: State,
        val at: Long,
    )

    private val entries = ArrayList<Row>()

    val rows: List<Row> get() = entries.toList()
    val isEmpty: Boolean get() = entries.isEmpty()

    /** The rows after `event`: updated in place by id, newest first, capped. */
    fun apply(type: String, data: JSONObject, now: Long = System.currentTimeMillis()): Boolean {
        val row = rowFrom(type, data, now) ?: return false
        entries.removeAll { it.id == row.id }
        entries.add(0, row)
        while (entries.size > CAP) entries.removeAt(entries.size - 1)
        return true
    }

    fun clear() = entries.clear()

    /** What the reactor's caption says while a camera is being looked at, or "". */
    fun lookingCaption(): String {
        val live = entries.firstOrNull { it.kind == Kind.CAMERA && it.state == State.LIVE } ?: return ""
        return "looking · ${live.title}"
    }

    companion object {
        const val CAP = 12

        /** The bus events that make a row, and the kind each makes — the contract's table. */
        val EVENTS: Map<String, Kind> = linkedMapOf(
            "jarvis_tool_started" to Kind.TOOL,
            "jarvis_tool_finished" to Kind.TOOL,
            "jarvis_task_added" to Kind.TASK,
            "jarvis_task_updated" to Kind.TASK,
            "state_changed" to Kind.SENSOR,
            "jarvis_mqtt_event" to Kind.SENSOR,
            "vision_look_started" to Kind.CAMERA,
            "vision_look_finished" to Kind.CAMERA,
            "vision_look_denied" to Kind.CAMERA,
            "memory_changed" to Kind.MEMORY,
            "jarvis_notification" to Kind.MOMENT,
            "jarvis_approval_required" to Kind.APPROVAL,
            "jarvis_approval_resolved" to Kind.APPROVAL,
        )

        /** Domains whose `state_changed` is a reading worth a row. */
        val SENSOR_DOMAINS = setOf("sensor", "binary_sensor", "climate", "weather", "number", "event", "device_tracker")

        private fun friendly(state: JSONObject?, entityId: String): String {
            val name = state?.optJSONObject("attributes")?.optString("friendly_name").orEmpty()
            return if (name.isNotEmpty()) name else entityId
        }

        private fun reading(state: JSONObject?): String {
            if (state == null) return ""
            val value = state.optString("state")
            val unit = state.optJSONObject("attributes")?.optString("unit_of_measurement").orEmpty()
            return if (unit.isEmpty()) value else "$value $unit"
        }

        fun rowFrom(type: String, data: JSONObject, at: Long): Row? {
            val kind = EVENTS[type] ?: return null
            return when (type) {
                "jarvis_tool_started" -> {
                    val name = data.optString("name")
                    val args = data.optJSONObject("arguments")
                    val summary = args?.keys()?.asSequence()?.joinToString(", ") { "$it: ${args.opt(it)}" }.orEmpty()
                    Row("tool:${data.optInt("round")}:${data.optInt("index")}:$name", kind, name, summary, State.LIVE, at)
                }
                "jarvis_tool_finished" -> {
                    val name = data.optString("name")
                    val ok = data.optBoolean("ok", true)
                    val detail = if (ok) "${data.optInt("duration_ms")} ms" else data.optString("error").ifEmpty { "failed" }
                    Row("tool:${data.optInt("round")}:${data.optInt("index")}:$name", kind, name, detail, if (ok) State.DONE else State.FAILED, at)
                }
                "jarvis_task_added", "jarvis_task_updated" -> {
                    val task = data.optJSONObject("task") ?: return null
                    val id = task.optString("id")
                    val status = task.optString("status")
                    val steps = task.optJSONArray("steps")
                    var done = 0
                    if (steps != null) for (i in 0 until steps.length()) if (steps.optJSONObject(i)?.optString("status") == "done") done++
                    val detail = if (steps != null && steps.length() > 0) "$done/${steps.length()} steps · $status" else status
                    val state = when (status) {
                        "done", "completed" -> State.DONE
                        "error", "failed", "cancelled" -> State.FAILED
                        else -> State.LIVE
                    }
                    Row("task:$id", kind, task.optString("title").ifEmpty { id }, detail, state, at)
                }
                "state_changed" -> {
                    val entityId = data.optString("entity_id")
                    val domain = entityId.substringBefore('.')
                    if (domain !in SENSOR_DOMAINS) return null
                    val next = data.optJSONObject("new_state")
                    Row("sensor:$entityId", kind, friendly(next, entityId), reading(next), State.DONE, at)
                }
                "jarvis_mqtt_event" -> {
                    // A button pressed twice is two rows: the id carries the time.
                    val entityId = data.optString("entity_id")
                    val pressed = data.optString("event_type")
                    val title = entityId.substringAfter('.', entityId).replace('_', ' ')
                    Row("press:$entityId:${data.opt("at") ?: at}", kind, title, if (pressed.isEmpty()) "pressed" else "pressed · $pressed", State.DONE, at)
                }
                "vision_look_started" -> Row("look:${data.optString("id")}", kind, data.optString("camera"), data.optString("question"), State.LIVE, at)
                "vision_look_finished" -> Row("look:${data.optString("id")}", kind, data.optString("camera"), "${data.optInt("duration_ms")} ms", State.DONE, at)
                "vision_look_denied" -> Row("look:${data.optString("id")}", kind, data.optString("camera"), data.optString("reason"), State.FAILED, at)
                "memory_changed" -> {
                    val entry = data.optJSONObject("entry")
                    Row("memory:${entry?.optString("id") ?: at}", kind, data.optString("action"), entry?.optString("text").orEmpty(), State.DONE, at)
                }
                "jarvis_notification" -> {
                    val n = data.optJSONObject("notification") ?: return null
                    Row("moment:${n.optString("id")}", kind, n.optString("title"), n.optString("kind"), State.DONE, at)
                }
                "jarvis_approval_required" -> Row("approval:${data.optString("id")}", kind, data.optString("tool"), "waiting for you", State.LIVE, at)
                "jarvis_approval_resolved" -> Row("approval:${data.optString("id")}", kind, data.optString("tool"), data.optString("decision"), State.DONE, at)
                else -> null
            }
        }
    }
}
