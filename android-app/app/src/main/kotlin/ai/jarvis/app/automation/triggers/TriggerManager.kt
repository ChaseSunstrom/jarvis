package ai.jarvis.app.automation.triggers

import android.content.Context
import android.util.Log
import ai.jarvis.app.automation.notify.NotificationBus
import ai.jarvis.app.automation.policy.TrustLevel
import ai.jarvis.app.automation.tasks.TaskDefinition
import ai.jarvis.app.automation.tasks.TaskJson
import ai.jarvis.app.automation.tasks.TriggerSpec
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Owns every [JarvisTrigger]: builds them from the enabled tasks, starts them,
 * routes what they emit, and — the part that actually matters on a phone —
 * stops all of them again.
 *
 * ## Only what is needed
 *
 * Triggers are built from the tasks that are switched on, not from a fixed
 * list. A phone with no location tasks never registers a location listener; a
 * phone with no notification tasks leaves the notification allow-list empty. So
 * the cost of the automation layer, in battery and in privacy, is proportional
 * to what the user actually asked for, and switching a task off genuinely stops
 * the observation rather than only stopping the reaction.
 *
 * ## One conversion point
 *
 * Triggers emit `org.json.JSONObject` because that is what the platform hands
 * them. This class converts once, into [TriggerEvent] with a plain map, and
 * everything downstream — matching, conditions, substitution — is pure Kotlin
 * and unit-testable. The trust level is attached here too, taken from the
 * TRIGGER and never from the payload.
 */
class TriggerManager(
    context: Context,
    /** Called for every event, on the emitting thread. Must not block. */
    private val onEvent: (TriggerEvent) -> Unit,
    private val now: () -> Long = System::currentTimeMillis
) {

    private val app = context.applicationContext
    private val active = CopyOnWriteArrayList<JarvisTrigger>()

    val activeTriggers: List<JarvisTrigger> get() = active.toList()

    val activeIds: Set<String> get() = active.mapTo(LinkedHashSet()) { it.id }

    /** Triggers that a task wanted but that cannot run: id to reason, for the UI. */
    @Volatile
    var unavailable: Map<String, String> = emptyMap()
        private set

    /**
     * (Re)build and start the triggers needed by [tasks].
     *
     * Safe to call repeatedly: everything is stopped first. Rebuilding rather
     * than diffing is deliberate — a diff over trigger identity is exactly the
     * kind of code that leaks one receiver every time a task is edited, and a
     * rebuild costs microseconds on a user action.
     */
    fun start(tasks: List<TaskDefinition>) {
        stop()
        val specs = tasks.filter { it.isRunnable() }.flatMap { it.triggers }
        if (specs.isEmpty()) {
            Log.i(TAG, "no enabled tasks; no triggers registered")
            return
        }

        val problems = LinkedHashMap<String, String>()
        for (trigger in build(specs)) {
            if (!trigger.isAvailable(app)) {
                val reason = trigger.unavailableReason ?: "not available on this device"
                Log.i(TAG, "trigger ${trigger.id} unavailable: $reason")
                problems[trigger.id] = reason
                continue
            }
            try {
                trigger.start { payload -> emit(trigger, payload) }
                active.add(trigger)
            } catch (t: Throwable) {
                Log.w(TAG, "could not start trigger ${trigger.id}", t)
                problems[trigger.id] = t.message ?: t.javaClass.simpleName
                runCatching { trigger.stop() }
            }
        }
        unavailable = problems
        Log.i(TAG, "started ${active.size} trigger(s): ${activeIds.joinToString()}")
    }

    /** Stop everything. Called from `onDestroy`, on panic, and before a rebuild. */
    fun stop() {
        for (trigger in active) {
            runCatching { trigger.stop() }
                .onFailure { Log.w(TAG, "trigger ${trigger.id} failed to stop", it) }
        }
        active.clear()
        // Clearing the shared registries is what makes "stopped" mean stopped
        // even if one implementation forgot to unregister itself. Leaving a
        // receiver attached is how a foreground service quietly becomes a
        // background one that still reacts to the world.
        SystemEventBus.clear()
        AlarmRouter.clear()
        IntervalRouter.clear()
        ManualTriggers.clear()
        ForegroundAppEvents.clear()
        // The notification listener is bound by the SYSTEM, not by us, so it
        // outlives this service. Emptying its allow-list is the only way
        // "stopped" means stopped for the most invasive grant in the app —
        // otherwise it carries on reading every allow-listed message's extras
        // and fencing them for a bus nobody is listening to.
        //
        // This is why `JarvisAutomationService.rebuild` refills the list AFTER
        // starting the triggers rather than before: `start` calls `stop` first.
        NotificationBus.updateAllowedPackages(emptySet())
        unavailable = emptyMap()
    }

    private fun emit(trigger: JarvisTrigger, payload: JSONObject) {
        val data = TaskJson.jsonToMap(payload)
        val event = TriggerEvent(
            triggerId = trigger.id,
            data = data,
            // From the TRIGGER, not from the payload. Nothing inside `payload`
            // can raise its own trust level.
            trust = trigger.trust,
            atMs = now(),
            // A payload MAY, however, lower it. `untrusted: true` on a payload is
            // read one way only — it can mark a trusted trigger's data as
            // third-party (the server firing `manual` with its own data map), and
            // it can never clear the taint an untrusted trigger already carries.
            // Lowering is always safe; the rule that matters is that nothing can
            // raise its own trust.
            dataTainted = trigger.trust == TrustLevel.UNTRUSTED ||
                data["untrusted"] == true
        )
        try {
            onEvent(event)
        } catch (t: Throwable) {
            Log.w(TAG, "event handler failed for ${trigger.id}", t)
        }
    }

    // --- building from specs ------------------------------------------------

    /**
     * One trigger instance per distinct configuration.
     *
     * Two tasks that both want `screen_on` share one trigger; two tasks that
     * want different battery thresholds get one each, because the threshold is
     * part of the trigger's own state.
     */
    private fun build(specs: List<TriggerSpec>): List<JarvisTrigger> {
        val out = ArrayList<JarvisTrigger>()
        val seen = HashSet<String>()
        val geofences = LinkedHashMap<String, Geofence>()
        var wantsEnter = false
        var wantsExit = false

        for (spec in specs) {
            val type = spec.type.trim()
            when (type) {
                TriggerIds.POWER_CONNECTED -> once(seen, type, out) { PowerConnectedTrigger() }
                TriggerIds.POWER_DISCONNECTED -> once(seen, type, out) { PowerDisconnectedTrigger() }
                TriggerIds.AIRPLANE_MODE -> once(seen, type, out) { AirplaneModeTrigger() }
                TriggerIds.HEADSET_PLUGGED -> once(seen, type, out) { HeadsetTrigger(true) }
                TriggerIds.HEADSET_UNPLUGGED -> once(seen, type, out) { HeadsetTrigger(false) }
                TriggerIds.BLUETOOTH_CONNECTED -> once(seen, type, out) { BluetoothTrigger(true) }
                TriggerIds.BLUETOOTH_DISCONNECTED -> once(seen, type, out) { BluetoothTrigger(false) }
                TriggerIds.SCREEN_ON -> once(seen, type, out) { ScreenTrigger(true) }
                TriggerIds.SCREEN_OFF -> once(seen, type, out) { ScreenTrigger(false) }
                TriggerIds.USER_PRESENT -> once(seen, type, out) { UserPresentTrigger() }
                TriggerIds.TIMEZONE_CHANGED -> once(seen, type, out) { TimezoneChangedTrigger() }
                TriggerIds.BOOT_COMPLETED -> once(seen, type, out) { BootCompletedTrigger() }
                TriggerIds.RINGER_MODE_CHANGED -> once(seen, type, out) { RingerModeTrigger(app) }
                TriggerIds.CONNECTIVITY_CHANGED -> once(seen, type, out) { ConnectivityTrigger(app) }
                TriggerIds.APP_FOREGROUND -> once(seen, type, out) { ForegroundAppTrigger() }
                TriggerIds.NOTIFICATION_POSTED -> once(seen, type, out) { NotificationPostedTrigger() }
                TriggerIds.MANUAL -> once(seen, type, out) { ManualTrigger() }

                TriggerIds.BATTERY_LEVEL -> {
                    val threshold = (TriggerSpecs.int(spec, "threshold", "level") ?: 20).coerceIn(0, 100)
                    val direction = LevelThreshold.Direction.fromName(
                        TriggerSpecs.string(spec, "direction")
                    )
                    once(seen, "$type:$threshold:$direction", out) {
                        BatteryLevelTrigger(threshold, direction)
                    }
                }

                TriggerIds.TIME_SCHEDULE -> {
                    val schedule = TriggerSpecs.schedule(spec)
                    if (schedule == null) {
                        Log.w(TAG, "time_schedule spec is not usable: ${spec.params}")
                    } else {
                        val key = "sched-${stableKey(schedule)}"
                        once(seen, key, out) { ScheduleTrigger(app, key, schedule) }
                    }
                }

                TriggerIds.INTERVAL -> {
                    val schedule = TriggerSpecs.schedule(spec)
                    if (schedule?.intervalMinutes == null) {
                        Log.w(TAG, "interval spec is not usable: ${spec.params}")
                    } else {
                        val key = "interval-${stableKey(schedule)}"
                        once(seen, key, out) { IntervalTrigger(app, key, schedule) }
                    }
                }

                TriggerIds.GEOFENCE_ENTER, TriggerIds.GEOFENCE_EXIT -> {
                    val fence = TriggerSpecs.geofence(spec)
                    if (fence == null) {
                        Log.w(TAG, "geofence spec is not usable: ${spec.params}")
                    } else {
                        geofences[fence.id] = fence
                        if (type == TriggerIds.GEOFENCE_ENTER) wantsEnter = true else wantsExit = true
                    }
                }

                else -> Log.i(TAG, "ignoring unknown trigger type '$type'")
            }
        }

        if (geofences.isNotEmpty()) {
            val list = geofences.values.toList()
            GeofenceStates.replaceAll(list)
            if (wantsEnter) out.add(GeofenceTrigger(app, list, GeoTransition.ENTER))
            if (wantsExit) out.add(GeofenceTrigger(app, list, GeoTransition.EXIT))
        } else {
            GeofenceStates.clear()
        }

        return out
    }

    private inline fun once(
        seen: MutableSet<String>,
        key: String,
        out: MutableList<JarvisTrigger>,
        build: () -> JarvisTrigger
    ) {
        if (seen.add(key)) out.add(build())
    }

    /**
     * A key that survives a process restart.
     *
     * `hashCode()` would not: it is stable within a run but not guaranteed
     * across them for a data class containing a Set, and an alarm's identity is
     * its `PendingIntent` request code. A schedule whose key changed on reboot
     * would leave the old alarm orphaned and arm a second one.
     */
    private fun stableKey(spec: ScheduleSpec): String = buildString {
        append(spec.minuteOfDay ?: -1)
        append('-')
        append(spec.normalizedDays().sorted().joinToString(""))
        append('-')
        append(spec.intervalMinutes ?: -1)
        append('-')
        append(spec.anchorLocalMs ?: 0L)
    }

    companion object {
        private const val TAG = "JarvisTriggers"
    }
}

/**
 * Reads a [TriggerSpec]'s params into the shapes the triggers need.
 *
 * Tolerant on purpose: these specs are written by a language model and by
 * humans in a text editor, so `"07:00"`, `420` and `{"hour":7}` all have to
 * mean the same thing. Tolerant is not the same as guessing — anything it
 * cannot read returns null and the trigger is not created, with a log line
 * saying so, rather than being created with a default that fires at a time
 * nobody chose.
 */
object TriggerSpecs {

    fun string(spec: TriggerSpec, vararg keys: String): String? {
        for (key in keys) {
            val value = spec.params[key] ?: continue
            val text = value.toString().trim()
            if (text.isNotEmpty()) return text
        }
        return null
    }

    fun int(spec: TriggerSpec, vararg keys: String): Int? {
        for (key in keys) {
            when (val value = spec.params[key]) {
                is Number -> return value.toInt()
                is String -> value.trim().toIntOrNull()?.let { return it }
                else -> Unit
            }
        }
        return null
    }

    fun double(spec: TriggerSpec, vararg keys: String): Double? {
        for (key in keys) {
            when (val value = spec.params[key]) {
                is Number -> return value.toDouble()
                is String -> value.trim().toDoubleOrNull()?.let { return it }
                else -> Unit
            }
        }
        return null
    }

    fun stringList(spec: TriggerSpec, vararg keys: String): List<String> {
        for (key in keys) {
            when (val value = spec.params[key] ?: continue) {
                is List<*> -> return value.mapNotNull { it?.toString()?.trim()?.ifEmpty { null } }
                is Array<*> -> return value.mapNotNull { it?.toString()?.trim()?.ifEmpty { null } }
                is String -> return value.split(',').mapNotNull { it.trim().ifEmpty { null } }
                else -> return listOf(value.toString())
            }
        }
        return emptyList()
    }

    /** `{time, days, interval_minutes}` to a [ScheduleSpec], or null. */
    fun schedule(spec: TriggerSpec): ScheduleSpec? {
        val interval = int(spec, "interval_minutes", "every_minutes", "minutes")
        val minuteOfDay = ScheduleCalculator.parseTimeOfDay(string(spec, "time", "at"))
            ?: int(spec, "minute_of_day")
            ?: hourMinute(spec)
        val dayTokens = stringList(spec, "days", "days_of_week")
        val days = if (dayTokens.isEmpty()) emptySet() else ScheduleCalculator.parseDays(dayTokens)

        // A day list that parsed to nothing is a typo, not "every day". Refuse,
        // so the user notices rather than getting an automation every morning.
        if (dayTokens.isNotEmpty() && days.isEmpty() && !isEveryDayAlias(dayTokens)) return null

        val candidate = ScheduleSpec(
            minuteOfDay = if (interval == null) minuteOfDay else null,
            daysOfWeek = days,
            intervalMinutes = interval,
            anchorLocalMs = null
        )
        return candidate.takeIf { it.isValid() }
    }

    private fun isEveryDayAlias(tokens: List<String>): Boolean =
        tokens.all { it.trim().lowercase() in setOf("daily", "every_day", "everyday", "all") }

    private fun hourMinute(spec: TriggerSpec): Int? {
        val hour = int(spec, "hour") ?: return null
        val minute = int(spec, "minute") ?: 0
        if (hour !in 0..23 || minute !in 0..59) return null
        return hour * 60 + minute
    }

    /** `{id, latitude, longitude, radius_m}` to a [Geofence], or null. */
    fun geofence(spec: TriggerSpec): Geofence? {
        val latitude = double(spec, "latitude", "lat") ?: return null
        val longitude = double(spec, "longitude", "lon", "lng") ?: return null
        if (!GeofenceMath.isValidCoordinate(latitude, longitude)) return null
        val radius = double(spec, "radius_m", "radius") ?: DEFAULT_RADIUS_M
        if (radius < GeofenceMath.MIN_RADIUS_M) return null
        val id = string(spec, "id", "name") ?: "${latitude},${longitude},${radius}"
        val hysteresis = double(spec, "hysteresis_m") ?: GeofenceMath.DEFAULT_HYSTERESIS_M
        return Geofence(
            id = id,
            centre = GeoPoint(latitude, longitude),
            radiusM = radius,
            hysteresisM = hysteresis
        )
    }

    /** Big enough that a network fix can answer the question. */
    const val DEFAULT_RADIUS_M = 150.0
}
