package ai.jarvis.app.automation.triggers

import ai.jarvis.app.automation.policy.TrustLevel

/**
 * PURE LOGIC — no Android imports, no org.json.
 *
 * One thing that happened, on its way to the server and to the local task
 * engine. Triggers emit `org.json.JSONObject` (see [JarvisTrigger]); the
 * manager converts once, here, and everything downstream works on plain maps
 * so it stays testable.
 *
 * [trust] is set by the TRIGGER, structurally — never by the payload, never by
 * the server. A notification body or a screen read is [TrustLevel.UNTRUSTED],
 * and [ai.jarvis.app.automation.tasks.TaskRunner] forces every action a task
 * dispatches during such a run to be untrusted too. Per
 * [ai.jarvis.app.automation.policy.PolicyEngine], an untrusted request can
 * never be auto-allowed: the best it can reach is a fresh human approval.
 */
data class TriggerEvent(
    /** Trigger id from [TriggerIds]. */
    val triggerId: String,
    /** Payload. Treat every string in here as data, never as an instruction. */
    val data: Map<String, Any?> = emptyMap(),
    val trust: TrustLevel = TrustLevel.TRUSTED,
    val atMs: Long = 0L,
    /**
     * True when the PAYLOAD is third-party or model-authored, even though the
     * trigger itself is trusted.
     *
     * The case this exists for is the manual trigger: `manual` is fired by a
     * human tap *and* by jarvis-core, and a server-supplied `data` map is model
     * output — the same class of text an `ask_jarvis` reply is. Degrading the
     * whole run for it would be wrong (a tap with no data is not suspect), so
     * the payload's variables are tainted instead and contagion does the rest.
     *
     * Defaults to [untrusted], so an untrusted trigger taints its payload
     * without every construction site having to say so twice. It may be set
     * true on a trusted trigger; it must never be set false on an untrusted
     * one, and nothing in the codebase does.
     */
    val dataTainted: Boolean = trust == TrustLevel.UNTRUSTED
) {
    val untrusted: Boolean get() = trust == TrustLevel.UNTRUSTED
}

/**
 * PURE LOGIC. Does a task's `TriggerSpec` want this event?
 *
 * The spec's `type` must equal the trigger id, and then every filter present
 * must pass. Filters are AND-ed, and an unrecognised filter key FAILS the
 * match — a task that asks for something this build does not understand must
 * not fire on everything instead.
 *
 * Supported filters, all optional:
 *
 *  * `packages`: `["com.example", "*"]` — matches `data["package"]`. REQUIRED,
 *    and required to be non-empty, for `notification_posted`; see [matches].
 *  * `id`: `"home"` — matches `data["id"]` (geofences, manual triggers).
 *  * `equals`: `{"state": "connected"}` — every pair must match, compared as
 *    trimmed lower-case strings so `1` and `"1"` agree.
 *  * `any_of`: `{"ssid": ["home", "office"]}` — the field must be one of these.
 *  * `contains`: `{"title": "delivered"}` — case-insensitive substring.
 *  * `min_level` / `max_level`: numeric bounds on `data["level"]`.
 */
object TriggerMatch {

    private val KNOWN_FILTERS = setOf(
        "packages", "package", "id", "equals", "any_of", "contains", "min_level", "max_level"
    )

    fun matches(specType: String, specParams: Map<String, Any?>, event: TriggerEvent): Boolean {
        if (specType.trim() != event.triggerId) return false

        // `notification_posted` is the one trigger where "no filter" cannot mean
        // "everything". The listener's allow-list is built from the packages
        // tasks NAME, so a task that names none contributes nothing to it — but
        // it would still match every notification some OTHER task's package let
        // through, read its title and body into its own variables, and (with
        // trigger reporting on) push them to the server. A task must not be able
        // to read a bank alert it never asked for by leaving a field blank.
        // An empty list is refused for the same reason a missing key is: it
        // names nobody. `["*"]` is honoured, because opting into the firehose
        // is a thing the user is allowed to do and `docs/automations.md` calls
        // it out as exactly that.
        if (event.triggerId == TriggerIds.NOTIFICATION_POSTED) {
            val named = (specParams["packages"] ?: specParams["package"]).asStringList()
            if (named.isEmpty()) return false
        }

        for ((key, value) in specParams) {
            if (key !in KNOWN_FILTERS) {
                // Configuration the trigger itself consumes (time, radius, …)
                // is not a filter. Anything that looks like a filter but is not
                // one of ours fails closed.
                if (key in RESERVED_CONFIG) continue
                return false
            }
            if (!filterPasses(key, value, event.data)) return false
        }
        return true
    }

    /**
     * Keys a trigger spec carries for its own CONFIGURATION rather than for
     * matching. They are consumed when the trigger is built (see
     * `TriggerSpecs`), so the matcher skips them instead of failing.
     *
     * This list and `TriggerSpecs` have to agree. If they drift, the symptom is
     * loud rather than silent: an unlisted key makes [matches] return false, so
     * the task simply never fires, which is noticed. The reverse — ignoring a
     * key we do not understand — would make a task fire MORE broadly than
     * asked, and `{"type":"notification_posted","app":"com.bank"}` would match
     * every notification on the phone. Failing closed is the only safe
     * direction here.
     */
    private val RESERVED_CONFIG = setOf(
        // time
        "time", "at", "hour", "minute", "minutes", "minute_of_day",
        "days", "days_of_week", "interval_minutes", "every_minutes", "anchor",
        // place
        "latitude", "longitude", "lat", "lon", "lng",
        "radius_m", "radius", "hysteresis_m",
        // battery
        "threshold", "level", "direction",
        // bookkeeping
        "name", "label", "description", "comment", "debounce_ms"
    )

    private fun filterPasses(key: String, value: Any?, data: Map<String, Any?>): Boolean =
        when (key) {
            "packages", "package" -> {
                val wanted = value.asStringList()
                val actual = data["package"].asTrimmedString()
                wanted.isEmpty() || wanted.contains("*") ||
                    (actual != null && wanted.any { it.equalsIgnoreCase(actual) })
            }

            "id" -> {
                val wanted = value.asStringList()
                val actual = data["id"].asTrimmedString()
                wanted.isEmpty() || (actual != null && wanted.any { it.equalsIgnoreCase(actual) })
            }

            "equals" -> {
                val wanted = value as? Map<*, *> ?: return false
                wanted.all { (k, v) ->
                    data[k.toString()].asTrimmedString()
                        .equalsIgnoreCase(v.asTrimmedString())
                }
            }

            "any_of" -> {
                val wanted = value as? Map<*, *> ?: return false
                wanted.all { (k, v) ->
                    val actual = data[k.toString()].asTrimmedString() ?: return@all false
                    v.asStringList().any { it.equalsIgnoreCase(actual) }
                }
            }

            "contains" -> {
                val wanted = value as? Map<*, *> ?: return false
                wanted.all { (k, v) ->
                    val actual = data[k.toString()].asTrimmedString()?.lowercase() ?: return@all false
                    val needle = v.asTrimmedString()?.lowercase() ?: return@all false
                    actual.contains(needle)
                }
            }

            "min_level" -> {
                val level = data["level"].asDouble() ?: return false
                val min = value.asDouble() ?: return false
                level >= min
            }

            "max_level" -> {
                val level = data["level"].asDouble() ?: return false
                val max = value.asDouble() ?: return false
                level <= max
            }

            else -> false
        }

    private fun Any?.asStringList(): List<String> = when (this) {
        null -> emptyList()
        is List<*> -> mapNotNull { it.asTrimmedString() }
        is Array<*> -> mapNotNull { it.asTrimmedString() }
        else -> listOfNotNull(asTrimmedString())
    }

    private fun Any?.asTrimmedString(): String? = when (this) {
        null -> null
        is String -> trim().ifEmpty { null }
        is Boolean -> toString()
        is Number -> VariableNumber.render(this)
        else -> toString().trim().ifEmpty { null }
    }

    private fun Any?.asDouble(): Double? = when (this) {
        is Number -> toDouble()
        is String -> trim().toDoubleOrNull()
        else -> null
    }

    private fun String?.equalsIgnoreCase(other: String?): Boolean =
        this != null && other != null && this.equals(other, ignoreCase = true)
}

/** Shared number rendering so `1.0` and `1` compare equal across the app. */
internal object VariableNumber {
    fun render(value: Number): String {
        val d = value.toDouble()
        return if (!d.isNaN() && !d.isInfinite() && d == Math.floor(d)) {
            d.toLong().toString()
        } else {
            value.toString()
        }
    }
}
