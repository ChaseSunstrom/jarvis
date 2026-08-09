package ai.jarvis.app.automation.tasks

import ai.jarvis.app.automation.triggers.GeoPoint
import ai.jarvis.app.automation.triggers.GeofenceMath

/**
 * PURE LOGIC — no Android imports, no org.json, no clock, no I/O.
 *
 * Whether a task is allowed to run right now. Everything the evaluator can see
 * is in [ConditionContext], sampled once before the run so a condition and the
 * step that follows it agree about the world.
 *
 * ## Unknown conditions fail
 *
 * An unrecognised condition type evaluates to FALSE, not TRUE. A task pushed by
 * a newer server that names a condition this build has never heard of does not
 * run. The alternative — treating "I do not understand this restriction" as "no
 * restriction" — turns every forward-compatibility gap into a way to bypass the
 * user's guard rails.
 *
 * The same applies to a condition whose input is unknown: `battery_below` with
 * no battery reading is false, not true.
 */
object ConditionEvaluator {

    /** Every leaf and combinator this build understands. */
    val KNOWN_TYPES: Set<String> = setOf(
        "all", "any", "not",
        "time_window", "day_of_week",
        "battery_above", "battery_below", "charging",
        "network", "wifi_ssid",
        "app_foreground", "screen_on", "ringer_mode",
        "variable",
        "location_inside", "location_outside",
        "always", "never"
    )

    /**
     * Every task VARIABLE this condition tree reads, as root names.
     *
     * Only the `variable` leaf can see a task variable — every other leaf reads
     * [ConditionContext] fields the platform filled in — so this walk is short.
     * It exists because [TaskRunner] has to know when a decision was made using
     * tainted text: `if reply contains "yes" then <action>` routes untrusted
     * content into control flow, and the chosen action's own parameters can be
     * entirely constant. Interpolation taint alone would miss it.
     *
     * Root names, not full paths, because that is what
     * [TaskRunner]'s tainted set is keyed on ("reply", not "reply.body").
     */
    fun variableRoots(spec: ConditionSpec, depth: Int = 0): Set<String> {
        val out = LinkedHashSet<String>()
        collectVariableRoots(spec, out, depth)
        return out
    }

    /** [variableRoots] over a whole list, e.g. a task's top-level conditions. */
    fun variableRoots(specs: List<ConditionSpec>): Set<String> {
        val out = LinkedHashSet<String>()
        for (spec in specs) collectVariableRoots(spec, out, 0)
        return out
    }

    private fun collectVariableRoots(spec: ConditionSpec, into: MutableSet<String>, depth: Int) {
        if (depth > TaskLimits.MAX_STEP_DEPTH) return
        if (spec.type.trim().lowercase() == "variable") {
            val name = spec.params["name"] ?: spec.params["variable"]
            name?.toString()?.trim()?.substringBefore('.')?.takeIf { it.isNotEmpty() }
                ?.let(into::add)
        }
        for (child in spec.children) collectVariableRoots(child, into, depth + 1)
    }

    /** All of [specs] must pass. An empty list passes: no conditions, no obstacle. */
    fun evaluateAll(specs: List<ConditionSpec>, ctx: ConditionContext): ConditionOutcome {
        for (spec in specs) {
            val outcome = evaluate(spec, ctx)
            if (!outcome.passed) return outcome
        }
        return ConditionOutcome(true, "all conditions passed")
    }

    fun evaluate(spec: ConditionSpec, ctx: ConditionContext, depth: Int = 0): ConditionOutcome {
        if (depth > TaskLimits.MAX_STEP_DEPTH) {
            return ConditionOutcome(false, "condition nesting too deep")
        }
        val raw = evaluateInner(spec, ctx, depth)
        return if (spec.negate) {
            ConditionOutcome(!raw.passed, "not(${raw.reason})")
        } else {
            raw
        }
    }

    private fun evaluateInner(
        spec: ConditionSpec,
        ctx: ConditionContext,
        depth: Int
    ): ConditionOutcome = when (spec.type.trim().lowercase()) {

        "all" -> {
            val failed = spec.children.map { evaluate(it, ctx, depth + 1) }.firstOrNull { !it.passed }
            if (failed == null) ConditionOutcome(true, "all(${spec.children.size}) passed")
            else ConditionOutcome(false, "all: ${failed.reason}")
        }

        "any" -> {
            if (spec.children.isEmpty()) {
                ConditionOutcome(false, "any: no conditions given")
            } else {
                val passed = spec.children.map { evaluate(it, ctx, depth + 1) }.firstOrNull { it.passed }
                if (passed != null) ConditionOutcome(true, "any: ${passed.reason}")
                else ConditionOutcome(false, "any: nothing matched")
            }
        }

        "not" -> {
            val inner = spec.children.firstOrNull()
            if (inner == null) ConditionOutcome(false, "not: nothing to negate")
            else {
                val r = evaluate(inner, ctx, depth + 1)
                ConditionOutcome(!r.passed, "not(${r.reason})")
            }
        }

        "always" -> ConditionOutcome(true, "always")
        "never" -> ConditionOutcome(false, "never")

        "time_window" -> timeWindow(spec, ctx)
        "day_of_week" -> dayOfWeek(spec, ctx)

        "battery_above" -> compareInt(
            actual = ctx.batteryPercent,
            wanted = spec.params.int("level") ?: spec.params.int("percent"),
            name = "battery"
        ) { a, w -> a > w }

        "battery_below" -> compareInt(
            actual = ctx.batteryPercent,
            wanted = spec.params.int("level") ?: spec.params.int("percent"),
            name = "battery"
        ) { a, w -> a < w }

        "charging" -> {
            val wanted = spec.params.bool("value") ?: true
            when (ctx.charging) {
                null -> ConditionOutcome(false, "charging state unknown")
                wanted -> ConditionOutcome(true, "charging=${ctx.charging}")
                else -> ConditionOutcome(false, "charging=${ctx.charging}, wanted $wanted")
            }
        }

        "network" -> {
            val wanted = spec.params.stringList("transport", "type", "value")
            when {
                wanted.isEmpty() -> ConditionOutcome(false, "network: no transport given")
                ctx.networkTransport == null -> ConditionOutcome(false, "network state unknown")
                wanted.any { it.equals(ctx.networkTransport, ignoreCase = true) } ->
                    ConditionOutcome(true, "network=${ctx.networkTransport}")

                else -> ConditionOutcome(
                    false,
                    "network=${ctx.networkTransport}, wanted ${wanted.joinToString("/")}"
                )
            }
        }

        "wifi_ssid" -> {
            val wanted = spec.params.stringList("ssid", "value")
            when {
                wanted.isEmpty() -> ConditionOutcome(false, "wifi_ssid: nothing to match")
                ctx.wifiSsid == null -> ConditionOutcome(false, "ssid unknown or not permitted")
                wanted.any { it.equals(ctx.wifiSsid, ignoreCase = true) } ->
                    ConditionOutcome(true, "ssid=${ctx.wifiSsid}")

                else -> ConditionOutcome(false, "ssid=${ctx.wifiSsid} did not match")
            }
        }

        "app_foreground" -> {
            val wanted = spec.params.stringList("package", "packages", "value")
            when {
                wanted.isEmpty() -> ConditionOutcome(false, "app_foreground: no package given")
                ctx.foregroundPackage == null ->
                    ConditionOutcome(false, "foreground app unknown (accessibility service off?)")

                wanted.any { it.equals(ctx.foregroundPackage, ignoreCase = true) } ->
                    ConditionOutcome(true, "foreground=${ctx.foregroundPackage}")

                else -> ConditionOutcome(false, "foreground=${ctx.foregroundPackage}")
            }
        }

        "screen_on" -> {
            val wanted = spec.params.bool("value") ?: true
            when (ctx.screenOn) {
                null -> ConditionOutcome(false, "screen state unknown")
                wanted -> ConditionOutcome(true, "screen_on=${ctx.screenOn}")
                else -> ConditionOutcome(false, "screen_on=${ctx.screenOn}, wanted $wanted")
            }
        }

        "ringer_mode" -> {
            val wanted = spec.params.stringList("mode", "value")
            when {
                wanted.isEmpty() -> ConditionOutcome(false, "ringer_mode: no mode given")
                ctx.ringerMode == null -> ConditionOutcome(false, "ringer mode unknown")
                wanted.any { it.equals(ctx.ringerMode, ignoreCase = true) } ->
                    ConditionOutcome(true, "ringer=${ctx.ringerMode}")

                else -> ConditionOutcome(false, "ringer=${ctx.ringerMode}")
            }
        }

        "variable" -> variable(spec, ctx)
        "location_inside" -> location(spec, ctx, inside = true)
        "location_outside" -> location(spec, ctx, inside = false)

        else -> ConditionOutcome(
            false,
            "unknown condition type '${spec.type}' — refused rather than ignored"
        )
    }

    // --- leaves ------------------------------------------------------------

    /**
     * A window on the local wall clock, midnight-wrapping.
     *
     * `{"start": "22:00", "end": "06:00"}` means the night, not the empty set.
     * `start == end` is a zero-width window and is false, because a user who
     * means "always" has `always`.
     */
    private fun timeWindow(spec: ConditionSpec, ctx: ConditionContext): ConditionOutcome {
        val start = parseMinute(spec.params["start"]) ?: spec.params.int("start_minute")
        val end = parseMinute(spec.params["end"]) ?: spec.params.int("end_minute")
        if (start == null || end == null) {
            return ConditionOutcome(false, "time_window needs start and end as HH:MM")
        }
        if (start !in 0..1439 || end !in 0..1439) {
            return ConditionOutcome(false, "time_window out of range")
        }
        val now = ctx.minuteOfDay
        val inside = if (start <= end) {
            now >= start && now < end
        } else {
            now >= start || now < end // wraps midnight
        }
        return ConditionOutcome(
            inside,
            "time ${format(now)} ${if (inside) "in" else "outside"} ${format(start)}-${format(end)}"
        )
    }

    private fun dayOfWeek(spec: ConditionSpec, ctx: ConditionContext): ConditionOutcome {
        val tokens = spec.params.stringList("days", "days_of_week", "value")
        if (tokens.isEmpty()) return ConditionOutcome(false, "day_of_week: no days given")
        val days = ai.jarvis.app.automation.triggers.ScheduleCalculator.parseDays(tokens)
        if (days.isEmpty()) {
            // parseDays returns the empty set for the "every day" aliases AND
            // for tokens it could not read. Those two mean opposite things, so
            // they are told apart here: "daily" passes, a typo refuses.
            val everyDay = tokens.all { it.trim().lowercase() in EVERY_DAY_ALIASES }
            return if (everyDay) {
                ConditionOutcome(true, "day_of_week: every day")
            } else {
                ConditionOutcome(false, "day_of_week: could not read ${tokens.joinToString()}")
            }
        }
        val passed = ctx.isoWeekday in days
        return ConditionOutcome(passed, "weekday ${ctx.isoWeekday} in ${days.joinToString()} = $passed")
    }

    private val EVERY_DAY_ALIASES = setOf("daily", "every_day", "everyday", "all")

    private fun variable(spec: ConditionSpec, ctx: ConditionContext): ConditionOutcome {
        val name = spec.params.string("name") ?: spec.params.string("variable")
            ?: return ConditionOutcome(false, "variable: no name")
        val resolved = VariableSubstitution.resolvePath(name, ctx.variables)
        val present = resolved !== VariableSubstitution.MISSING
        val actual = if (present) resolved else null
        val op = (spec.params.string("op") ?: "eq").trim().lowercase()
        val wanted = spec.params["value"]

        return when (op) {
            "exists", "is_set" -> ConditionOutcome(present, "$name exists=$present")
            "missing", "unset" -> ConditionOutcome(!present, "$name missing=${!present}")
            "empty" -> ConditionOutcome(
                !present || VariableSubstitution.renderValue(actual).isEmpty(),
                "$name empty"
            )

            "eq", "==", "equals" -> textCompare(name, actual, wanted, present) { a, b -> a == b }
            "ne", "!=" -> textCompare(name, actual, wanted, present) { a, b -> a != b }
            "contains" -> textCompare(name, actual, wanted, present) { a, b -> a.contains(b) }
            "starts_with" -> textCompare(name, actual, wanted, present) { a, b -> a.startsWith(b) }
            "ends_with" -> textCompare(name, actual, wanted, present) { a, b -> a.endsWith(b) }
            "gt", ">" -> numberCompare(name, actual, wanted) { a, b -> a > b }
            "gte", ">=" -> numberCompare(name, actual, wanted) { a, b -> a >= b }
            "lt", "<" -> numberCompare(name, actual, wanted) { a, b -> a < b }
            "lte", "<=" -> numberCompare(name, actual, wanted) { a, b -> a <= b }
            else -> ConditionOutcome(false, "variable: unknown operator '$op'")
        }
    }

    private fun location(
        spec: ConditionSpec,
        ctx: ConditionContext,
        inside: Boolean
    ): ConditionOutcome {
        val lat = spec.params.double("latitude") ?: spec.params.double("lat")
        val lon = spec.params.double("longitude") ?: spec.params.double("lon")
        val radius = spec.params.double("radius_m") ?: spec.params.double("radius")
        if (lat == null || lon == null || radius == null) {
            return ConditionOutcome(false, "location needs latitude, longitude and radius_m")
        }
        if (!GeofenceMath.isValidCoordinate(lat, lon)) {
            return ConditionOutcome(false, "location: not a coordinate")
        }
        val fixLat = ctx.latitude
        val fixLon = ctx.longitude
        if (fixLat == null || fixLon == null) {
            return ConditionOutcome(false, "location unknown")
        }
        // A fix too coarse to answer the question is not an answer. Same rule
        // as the geofence trigger, and the same reason.
        if (!GeofenceMath.isFixUsable(ctx.locationAccuracyM, radius)) {
            return ConditionOutcome(false, "location fix too coarse for a ${radius.toInt()} m radius")
        }
        val distance = GeofenceMath.haversineMeters(fixLat, fixLon, lat, lon)
        val isInside = distance <= radius
        val passed = isInside == inside
        return ConditionOutcome(
            passed,
            "${distance.toInt()} m from the point, radius ${radius.toInt()} m, " +
                "${if (isInside) "inside" else "outside"}"
        )
    }

    // --- helpers -----------------------------------------------------------

    private inline fun compareInt(
        actual: Int?,
        wanted: Int?,
        name: String,
        compare: (Int, Int) -> Boolean
    ): ConditionOutcome {
        if (wanted == null) return ConditionOutcome(false, "$name: no level given")
        if (actual == null) return ConditionOutcome(false, "$name unknown")
        val passed = compare(actual, wanted)
        return ConditionOutcome(passed, "$name=$actual vs $wanted = $passed")
    }

    private inline fun textCompare(
        name: String,
        actual: Any?,
        wanted: Any?,
        present: Boolean,
        compare: (String, String) -> Boolean
    ): ConditionOutcome {
        if (!present) return ConditionOutcome(false, "$name is not set")
        val a = VariableSubstitution.renderValue(actual).trim().lowercase()
        val b = VariableSubstitution.renderValue(wanted).trim().lowercase()
        val passed = compare(a, b)
        return ConditionOutcome(passed, "$name '$a' vs '$b' = $passed")
    }

    private inline fun numberCompare(
        name: String,
        actual: Any?,
        wanted: Any?,
        compare: (Double, Double) -> Boolean
    ): ConditionOutcome {
        val a = toDouble(actual) ?: return ConditionOutcome(false, "$name is not a number")
        val b = toDouble(wanted) ?: return ConditionOutcome(false, "$name: comparand is not a number")
        val passed = compare(a, b)
        return ConditionOutcome(passed, "$name $a vs $b = $passed")
    }

    private fun toDouble(value: Any?): Double? = when (value) {
        is Number -> value.toDouble()
        is Boolean -> if (value) 1.0 else 0.0
        is String -> value.trim().toDoubleOrNull()
        else -> null
    }

    private fun parseMinute(value: Any?): Int? = when (value) {
        null -> null
        is Number -> value.toInt().takeIf { it in 0..1439 }
        is String -> ai.jarvis.app.automation.triggers.ScheduleCalculator.parseTimeOfDay(value)
        else -> null
    }

    private fun format(minuteOfDay: Int): String =
        "%02d:%02d".format(minuteOfDay / 60, minuteOfDay % 60)

    // --- small typed readers over the params map ---------------------------

    private fun Map<String, Any?>.string(vararg keys: String): String? {
        for (key in keys) {
            val v = this[key] ?: continue
            val s = VariableSubstitution.renderValue(v).trim()
            if (s.isNotEmpty()) return s
        }
        return null
    }

    private fun Map<String, Any?>.stringList(vararg keys: String): List<String> {
        for (key in keys) {
            when (val v = this[key] ?: continue) {
                is List<*> -> return v.mapNotNull { it?.toString()?.trim()?.ifEmpty { null } }
                is Array<*> -> return v.mapNotNull { it?.toString()?.trim()?.ifEmpty { null } }
                else -> {
                    val s = VariableSubstitution.renderValue(v).trim()
                    if (s.isNotEmpty()) return listOf(s)
                }
            }
        }
        return emptyList()
    }

    private fun Map<String, Any?>.int(key: String): Int? = when (val v = this[key]) {
        is Number -> v.toInt()
        is String -> v.trim().toIntOrNull()
        else -> null
    }

    private fun Map<String, Any?>.double(key: String): Double? = when (val v = this[key]) {
        is Number -> v.toDouble()
        is String -> v.trim().toDoubleOrNull()
        else -> null
    }

    private fun Map<String, Any?>.bool(key: String): Boolean? = when (val v = this[key]) {
        is Boolean -> v
        is Number -> v.toInt() != 0
        is String -> when (v.trim().lowercase()) {
            "true", "yes", "on", "1" -> true
            "false", "no", "off", "0" -> false
            else -> null
        }

        else -> null
    }
}

/** The answer, plus a sentence for the audit log and the task list. */
data class ConditionOutcome(val passed: Boolean, val reason: String)

/**
 * PURE DATA. Everything the evaluator may look at, sampled once per run.
 *
 * Every field is nullable because every one of them can genuinely be unknown —
 * no location permission, no accessibility service, no battery reading yet —
 * and a condition over an unknown input is FALSE. `ConditionProbe` fills this
 * from the platform; tests fill it by hand.
 */
data class ConditionContext(
    val nowEpochMs: Long,
    /** Local wall-clock minute of day, 0..1439. */
    val minuteOfDay: Int,
    /** ISO 1=Mon..7=Sun. */
    val isoWeekday: Int,
    val batteryPercent: Int? = null,
    val charging: Boolean? = null,
    /** wifi | cellular | ethernet | bluetooth | vpn | other | none */
    val networkTransport: String? = null,
    val wifiSsid: String? = null,
    val foregroundPackage: String? = null,
    val screenOn: Boolean? = null,
    /** normal | vibrate | silent */
    val ringerMode: String? = null,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val locationAccuracyM: Double? = null,
    /** Task variables, so a condition can test one. */
    val variables: Map<String, Any?> = emptyMap()
) {
    fun fix(): GeoPoint? =
        if (latitude != null && longitude != null) GeoPoint(latitude, longitude) else null

    fun withVariables(vars: Map<String, Any?>): ConditionContext = copy(variables = vars)
}
