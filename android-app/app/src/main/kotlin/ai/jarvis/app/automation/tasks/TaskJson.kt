package ai.jarvis.app.automation.tasks

import org.json.JSONArray
import org.json.JSONObject

/**
 * The only place task JSON is read or written.
 *
 * Everything else in this package works on plain Kotlin types, which is what
 * lets the interesting parts — substitution, conditions, screening, scheduling
 * — stay free of `org.json` and therefore unit-testable on a plain JVM.
 *
 * ## Reading is defensive, on purpose
 *
 * This parses JSON written by a language model. Nothing here throws on bad
 * input: a malformed step is dropped, an unknown step type is dropped, a
 * missing id is generated. What it will NOT do is guess — an unknown step type
 * becomes nothing rather than becoming `action`, and `enabled_by_user` is
 * ignored entirely on import, because that flag records a human decision and a
 * remote document is not one.
 */
object TaskJson {

    /** Bump when the on-disk shape changes in a way that needs migrating. */
    const val SCHEMA_VERSION = 1

    private const val MAX_DEPTH = 12
    private const val MAX_STEPS = TaskLimits.MAX_STEPS_PER_RUN

    // --- task ---------------------------------------------------------------

    fun taskFromJson(o: JSONObject): TaskDefinition? {
        val id = o.optString("id").trim().ifEmpty { null } ?: return null
        val name = o.optString("name").trim().ifEmpty { id }
        return TaskDefinition(
            id = id,
            name = name,
            enabled = o.optBoolean("enabled", false),
            triggers = triggersFrom(o.optJSONArray("triggers")),
            conditions = conditionsFrom(o.optJSONArray("conditions"), 0),
            steps = stepsFrom(o.optJSONArray("steps"), 0),
            mode = TaskMode.fromName(o.optString("mode")),
            source = TaskSource.fromName(o.optString("source")),
            // NOT read from the wire. Only the store sets it, only from a tap.
            enabledByUser = false,
            description = o.optString("description").trim().ifEmpty { null },
            createdAtMs = o.optLong("created_at", 0L),
            updatedAtMs = o.optLong("updated_at", 0L)
        )
    }

    fun taskToJson(task: TaskDefinition): JSONObject = JSONObject()
        .put("id", task.id)
        .put("name", task.name)
        .put("enabled", task.enabled)
        .put("mode", task.mode.name)
        .put("source", task.source.name)
        .put("enabled_by_user", task.enabledByUser)
        .put("triggers", JSONArray().also { arr -> task.triggers.forEach { arr.put(triggerToJson(it)) } })
        .put("conditions", JSONArray().also { arr -> task.conditions.forEach { arr.put(conditionToJson(it)) } })
        .put("steps", JSONArray().also { arr -> task.steps.forEach { arr.put(stepToJson(it)) } })
        .apply {
            task.description?.let { put("description", it) }
            if (task.createdAtMs > 0) put("created_at", task.createdAtMs)
            if (task.updatedAtMs > 0) put("updated_at", task.updatedAtMs)
        }

    /** A whole store file or an import bundle: `{"version":1,"tasks":[…]}`. */
    fun bundleFromJson(o: JSONObject): List<TaskDefinition> {
        val arr = o.optJSONArray("tasks") ?: return emptyList()
        val out = ArrayList<TaskDefinition>(arr.length())
        for (i in 0 until arr.length()) {
            val obj = arr.optJSONObject(i) ?: continue
            taskFromJson(obj)?.let(out::add)
        }
        return out
    }

    fun bundleToJson(tasks: List<TaskDefinition>): JSONObject = JSONObject()
        .put("version", SCHEMA_VERSION)
        .put("tasks", JSONArray().also { arr -> tasks.forEach { arr.put(taskToJson(it)) } })

    // --- triggers -----------------------------------------------------------

    private fun triggersFrom(arr: JSONArray?): List<TriggerSpec> {
        if (arr == null) return emptyList()
        val out = ArrayList<TriggerSpec>(arr.length())
        for (i in 0 until arr.length()) {
            when (val item = arr.opt(i)) {
                // Shorthand: "screen_on" instead of {"type":"screen_on"}.
                is String -> item.trim().ifEmpty { null }?.let { out.add(TriggerSpec(it)) }
                is JSONObject -> {
                    val type = item.optString("type").trim()
                    if (type.isNotEmpty()) out.add(TriggerSpec(type, paramsOf(item)))
                }

                else -> Unit
            }
        }
        return out
    }

    private fun triggerToJson(spec: TriggerSpec): JSONObject =
        JSONObject().put("type", spec.type).also { obj ->
            for ((k, v) in spec.params) obj.put(k, toJsonValue(v, 0))
        }

    // --- conditions ---------------------------------------------------------

    private fun conditionsFrom(arr: JSONArray?, depth: Int): List<ConditionSpec> {
        if (arr == null || depth > MAX_DEPTH) return emptyList()
        val out = ArrayList<ConditionSpec>(arr.length())
        for (i in 0 until arr.length()) {
            conditionFrom(arr.opt(i), depth)?.let(out::add)
        }
        return out
    }

    private fun conditionFrom(value: Any?, depth: Int): ConditionSpec? {
        if (depth > MAX_DEPTH) return null
        return when (value) {
            is String -> value.trim().ifEmpty { null }?.let { ConditionSpec(it) }
            is JSONObject -> {
                val type = value.optString("type").trim()
                if (type.isEmpty()) return null
                ConditionSpec(
                    type = type,
                    params = paramsOf(value, extraReserved = CONDITION_RESERVED),
                    negate = value.optBoolean("negate", false),
                    children = conditionsFrom(value.optJSONArray("conditions"), depth + 1)
                )
            }

            else -> null
        }
    }

    private fun conditionToJson(spec: ConditionSpec): JSONObject =
        JSONObject().put("type", spec.type).apply {
            if (spec.negate) put("negate", true)
            for ((k, v) in spec.params) put(k, toJsonValue(v, 0))
            if (spec.children.isNotEmpty()) {
                put("conditions", JSONArray().also { arr -> spec.children.forEach { arr.put(conditionToJson(it)) } })
            }
        }

    // --- steps --------------------------------------------------------------

    private fun stepsFrom(arr: JSONArray?, depth: Int): List<StepSpec> {
        if (arr == null || depth > TaskLimits.MAX_STEP_DEPTH) return emptyList()
        val out = ArrayList<StepSpec>(arr.length())
        for (i in 0 until arr.length()) {
            if (out.size >= MAX_STEPS) break
            val obj = arr.optJSONObject(i) ?: continue
            stepFrom(obj, depth)?.let(out::add)
        }
        return out
    }

    private fun stepFrom(o: JSONObject, depth: Int): StepSpec? {
        // An unknown type is DROPPED rather than defaulted. A newer server's
        // step this build cannot run must not silently become something else.
        val type = StepType.fromWire(o.optString("type")) ?: return null
        return StepSpec(
            type = type,
            action = o.optString("action").trim().ifEmpty { null },
            // `tier` is read back out of params by TaskRunner, where it goes
            // through max(local, declared) and can only make a step stricter.
            // In the nested-`params` form it would otherwise be dropped on the
            // floor and the raise would silently not happen, so it is hoisted:
            // a step that asks to be treated as CONFIRM gets to be treated as
            // CONFIRM whichever spelling it used.
            params = paramsOf(o, extraReserved = STEP_RESERVED).withHoistedTier(o),
            storeAs = o.optString("store_as").trim().ifEmpty { null },
            timeoutMs = if (o.has("timeout_ms")) o.optLong("timeout_ms") else null,
            condition = conditionFrom(o.opt("condition"), depth),
            then = stepsFrom(o.optJSONArray("then"), depth + 1),
            otherwise = stepsFrom(o.optJSONArray("else") ?: o.optJSONArray("otherwise"), depth + 1),
            steps = stepsFrom(o.optJSONArray("steps") ?: o.optJSONArray("do"), depth + 1),
            count = if (o.has("count")) o.optInt("count") else null,
            continueOnError = o.optBoolean("continue_on_error", false),
            label = o.optString("label").trim().ifEmpty { null }
        )
    }

    private fun stepToJson(step: StepSpec): JSONObject = JSONObject()
        .put("type", step.type.wire)
        .apply {
            step.action?.let { put("action", it) }
            step.storeAs?.let { put("store_as", it) }
            step.timeoutMs?.let { put("timeout_ms", it) }
            step.count?.let { put("count", it) }
            step.label?.let { put("label", it) }
            if (step.continueOnError) put("continue_on_error", true)
            step.condition?.let { put("condition", conditionToJson(it)) }
            if (step.params.isNotEmpty()) put("params", toJsonValue(step.params, 0))
            if (step.then.isNotEmpty()) {
                put("then", JSONArray().also { arr -> step.then.forEach { arr.put(stepToJson(it)) } })
            }
            if (step.otherwise.isNotEmpty()) {
                put("else", JSONArray().also { arr -> step.otherwise.forEach { arr.put(stepToJson(it)) } })
            }
            if (step.steps.isNotEmpty()) {
                put("steps", JSONArray().also { arr -> step.steps.forEach { arr.put(stepToJson(it)) } })
            }
        }

    /**
     * Params come either from a nested `params` object or from the step's own
     * remaining keys, so both of these mean the same thing:
     *
     * ```json
     * {"type": "wait", "params": {"ms": 500}}
     * {"type": "wait", "ms": 500}
     * ```
     *
     * The flat form is what a model writes when left to itself, and rejecting
     * it would produce tasks that silently do nothing.
     */
    private fun paramsOf(o: JSONObject, extraReserved: Set<String> = emptySet()): Map<String, Any?> {
        val nested = o.optJSONObject("params")
        if (nested != null) return jsonToMap(nested, 0)
        val reserved = BASE_RESERVED + extraReserved
        val out = LinkedHashMap<String, Any?>()
        val keys = o.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            if (key in reserved) continue
            out[key] = fromJsonValue(o.opt(key), 0)
        }
        return out
    }

    /**
     * Carry a step-level `tier` into the params map when the step used the
     * nested-`params` form, which otherwise discards every sibling key.
     *
     * Only ever ADDS the key, and only when params does not already carry one,
     * so the nested form still wins where both are present.
     */
    private fun Map<String, Any?>.withHoistedTier(o: JSONObject): Map<String, Any?> {
        if (containsKey("tier")) return this
        if (!o.has("tier") || o.isNull("tier")) return this
        return this + ("tier" to fromJsonValue(o.opt("tier"), 0))
    }

    private val BASE_RESERVED = setOf("type", "params")

    private val CONDITION_RESERVED = setOf("negate", "conditions")

    private val STEP_RESERVED = setOf(
        "action", "store_as", "timeout_ms", "condition", "then", "else", "otherwise",
        "steps", "do", "count", "continue_on_error", "label"
    )

    // --- generic JSON <-> Kotlin -------------------------------------------

    /** `JSONObject` to a plain map. `JSONObject.NULL` becomes a Kotlin null. */
    fun jsonToMap(o: JSONObject?, depth: Int = 0): Map<String, Any?> {
        if (o == null || depth > MAX_DEPTH) return emptyMap()
        val out = LinkedHashMap<String, Any?>(o.length())
        val keys = o.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            out[key] = fromJsonValue(o.opt(key), depth + 1)
        }
        return out
    }

    fun jsonToList(arr: JSONArray?, depth: Int = 0): List<Any?> {
        if (arr == null || depth > MAX_DEPTH) return emptyList()
        val out = ArrayList<Any?>(arr.length())
        for (i in 0 until arr.length()) out.add(fromJsonValue(arr.opt(i), depth + 1))
        return out
    }

    private fun fromJsonValue(value: Any?, depth: Int): Any? = when {
        value == null || value === JSONObject.NULL -> null
        value is JSONObject -> jsonToMap(value, depth)
        value is JSONArray -> jsonToList(value, depth)
        else -> value
    }

    /** A plain map back to `JSONObject`. Nulls become `JSONObject.NULL`. */
    fun mapToJson(map: Map<String, Any?>?): JSONObject {
        val out = JSONObject()
        if (map == null) return out
        for ((k, v) in map) out.put(k, toJsonValue(v, 0))
        return out
    }

    private fun toJsonValue(value: Any?, depth: Int): Any = when {
        depth > MAX_DEPTH -> JSONObject.NULL
        value == null -> JSONObject.NULL
        value is Map<*, *> -> JSONObject().also { obj ->
            for ((k, v) in value) obj.put(k.toString(), toJsonValue(v, depth + 1))
        }

        value is List<*> -> JSONArray().also { arr ->
            for (item in value) arr.put(toJsonValue(item, depth + 1))
        }

        value is Array<*> -> toJsonValue(value.toList(), depth)
        else -> value
    }
}
