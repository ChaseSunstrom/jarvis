package ai.jarvis.app.tasks

import org.json.JSONObject

/**
 * Reading a task off the wire, and asking the server about them.
 *
 * Kept apart from [TaskBoard] so the model stays JSON-free and testable, and so
 * the wire shape can be reviewed against `jarvis-core/docs/clients.md` without
 * reading the socket code.
 *
 * **The trap this file exists to avoid** is `optDouble`. jarvis-core sends
 * `"fraction": null` whenever a percentage would be a guess, and `optDouble`
 * answers `NaN` for a JSON null while `optDouble(key, 0.0)` answers 0.0. Both
 * silently turn "do not draw a number" into a bar that reads as "nothing has
 * happened", which is precisely the case the server went to the trouble of
 * distinguishing. So the null is checked for explicitly, before any read.
 */
object TaskFrames {

    const val TYPE_LIST = "jarvis/tasks/list"
    const val TYPE_SUBSCRIBE = "subscribe_events"

    /**
     * The arguments for `jarvis/tasks/list` — and deliberately NOT a whole
     * frame.
     *
     * `JarvisChannel.request` allocates the id and sets the type itself, then
     * merges a payload over the top. Handing it a complete frame would let this
     * function's `id` overwrite the real one and orphan the pending entry for
     * ever. So the two halves are kept apart: [TYPE_LIST] names the command,
     * this names its arguments.
     */
    fun listArgs(activeOnly: Boolean = true): JSONObject =
        JSONObject().put("active", activeOnly)

    /**
     * A whole `subscribe_events` frame, id included.
     *
     * Whole, unlike the listing above, because a subscription's id is not a
     * request id to be matched — it is the handle jarvis-core files the
     * subscription under and stamps on every event it sends. It must come from
     * the channel's counter and it must be sent as written.
     */
    fun subscribe(id: Int, eventType: String): JSONObject = JSONObject()
        .put("id", id)
        .put("type", TYPE_SUBSCRIBE)
        .put("event_type", eventType)

    // No `cancel` frame here on purpose. The phone's console tab loads the
    // real `/tasks` page, which already has the button — and an untested
    // second path to a destructive verb is worse than one door.

    /**
     * One task object -> a row, or null if it is not one.
     *
     * A malformed record costs one row rather than the whole overlay: the list
     * is built from whatever survives this.
     */
    fun row(task: JSONObject?): TaskBoard.Row? {
        if (task == null) return null
        val id = task.optString("id").orEmpty()
        if (id.isEmpty()) return null
        return TaskBoard.Row(
            id = id,
            title = task.optString("title").orEmpty(),
            kind = task.optString("kind").ifEmpty { "background" },
            status = TaskBoard.Status.of(task.optString("status")),
            fraction = fractionOf(task),
            detail = task.optString("detail").orEmpty(),
            result = task.optString("result").orEmpty(),
            error = task.optString("error").orEmpty(),
            doneSteps = task.optInt("done_steps", 0),
            totalSteps = task.optInt("total_steps", 0),
            created = task.optDouble("created", 0.0).let { if (it.isNaN()) 0.0 else it },
            updated = task.optDouble("updated", 0.0).let { if (it.isNaN()) 0.0 else it },
        )
    }

    /**
     * The fraction, or null — never NaN, and never a substituted zero.
     *
     * `isNull` first, because that is the case the server means. The NaN guard
     * after it catches a value that is present but not a number, which is the
     * same "we do not know" and must be drawn the same way.
     */
    fun fractionOf(task: JSONObject): Double? {
        if (!task.has("fraction") || task.isNull("fraction")) return null
        val value = task.optDouble("fraction", Double.NaN)
        return if (value.isNaN()) null else value
    }

    /** The task carried by a `jarvis_task_*` event frame, if there is one. */
    fun rowFromEvent(frame: JSONObject?): TaskBoard.Row? =
        row(frame?.optJSONObject("event")?.optJSONObject("data")?.optJSONObject("task"))

    /** The event's own name, so a caller can tell an add from a removal. */
    fun eventTypeOf(frame: JSONObject?): String =
        frame?.optJSONObject("event")?.optString("event_type").orEmpty()

    /** Every task in a `jarvis/tasks/list` result. */
    fun rowsFromList(result: JSONObject?): List<TaskBoard.Row> {
        val array = result?.optJSONArray("tasks") ?: return emptyList()
        val out = ArrayList<TaskBoard.Row>(array.length())
        for (i in 0 until array.length()) {
            row(array.optJSONObject(i))?.let { out.add(it) }
        }
        return out
    }
}
