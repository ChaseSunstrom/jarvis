package ai.jarvis.app.automation.triggers

import android.content.Context
import ai.jarvis.app.automation.policy.TrustLevel
import org.json.JSONObject

/**
 * One thing that can start an automation.
 *
 * A trigger is a source of facts, never of decisions. It observes something —
 * a broadcast, an alarm, a location fix, a notification — and hands the
 * observation to its callback. What happens next is decided by the task engine
 * and, for anything that touches the world, by the policy engine.
 *
 * Contract:
 *
 *  * [start] is called once with a callback; calling it again replaces the
 *    callback and must not double-register the underlying listener.
 *  * [stop] must release everything: receivers, alarms, location listeners. It
 *    is called from `onDestroy`, on panic, and whenever the master switch goes
 *    off, and it must be safe to call when never started.
 *  * Neither method may block. Callbacks arrive on whatever thread the platform
 *    used; [TriggerManager] does the hop.
 *  * A trigger NEVER dispatches an action. It cannot: it has no reference to
 *    the registry.
 */
interface JarvisTrigger {

    /** Stable id from [TriggerIds]. Used on the wire and in task specs. */
    val id: String

    /** Begin observing. The callback receives the event payload. */
    fun start(cb: (JSONObject) -> Unit)

    /** Stop observing and release everything. Idempotent. */
    fun stop()

    /**
     * How far this trigger's payload can be trusted.
     *
     * [TrustLevel.UNTRUSTED] for anything whose content is written by someone
     * else — a notification body, screen text, a web response. The task runner
     * forces every action in a run started by such a trigger to dispatch as
     * untrusted, and the policy engine can never auto-allow one of those.
     *
     * This is a property of the SOURCE, not of the payload. No field in an
     * event can raise its own trust.
     */
    val trust: TrustLevel get() = TrustLevel.TRUSTED

    /** Runtime permissions this trigger needs, for the settings screen. */
    val requiredPermissions: List<String> get() = emptyList()

    /** False when this device or this grant state cannot support the trigger. */
    fun isAvailable(ctx: Context): Boolean = true

    /** Why it is unavailable, phrased as something the user can act on. */
    val unavailableReason: String? get() = null
}

/**
 * The trigger catalogue. Ids are wire-visible: they appear in task JSON, in
 * `device_event.event`, and in the audit log, so they are append-only.
 */
object TriggerIds {

    // --- power and battery ---
    const val POWER_CONNECTED = "power_connected"
    const val POWER_DISCONNECTED = "power_disconnected"
    const val BATTERY_LEVEL = "battery_level"

    // --- connectivity ---
    const val CONNECTIVITY_CHANGED = "connectivity_changed"
    const val AIRPLANE_MODE = "airplane_mode"

    // --- audio routing ---
    const val HEADSET_PLUGGED = "headset_plugged"
    const val HEADSET_UNPLUGGED = "headset_unplugged"
    const val BLUETOOTH_CONNECTED = "bluetooth_connected"
    const val BLUETOOTH_DISCONNECTED = "bluetooth_disconnected"

    // --- screen and session ---
    const val SCREEN_ON = "screen_on"
    const val SCREEN_OFF = "screen_off"
    const val USER_PRESENT = "user_present"

    // --- system state ---
    const val RINGER_MODE_CHANGED = "ringer_mode_changed"
    const val TIMEZONE_CHANGED = "timezone_changed"
    const val BOOT_COMPLETED = "boot_completed"

    // --- time ---
    const val TIME_SCHEDULE = "time_schedule"
    const val INTERVAL = "interval"

    // --- place ---
    const val GEOFENCE_ENTER = "geofence_enter"
    const val GEOFENCE_EXIT = "geofence_exit"

    // --- apps and notifications (UNTRUSTED payloads) ---
    const val APP_FOREGROUND = "app_foreground"
    const val NOTIFICATION_POSTED = "notification_posted"

    // --- explicit ---
    const val MANUAL = "manual"

    /** Every id, for the settings screen and the server-side catalogue. */
    val ALL: List<String> = listOf(
        POWER_CONNECTED, POWER_DISCONNECTED, BATTERY_LEVEL,
        CONNECTIVITY_CHANGED, AIRPLANE_MODE,
        HEADSET_PLUGGED, HEADSET_UNPLUGGED, BLUETOOTH_CONNECTED, BLUETOOTH_DISCONNECTED,
        SCREEN_ON, SCREEN_OFF, USER_PRESENT,
        RINGER_MODE_CHANGED, TIMEZONE_CHANGED, BOOT_COMPLETED,
        TIME_SCHEDULE, INTERVAL,
        GEOFENCE_ENTER, GEOFENCE_EXIT,
        APP_FOREGROUND, NOTIFICATION_POSTED,
        MANUAL
    )

    /**
     * Triggers whose payload is written by someone other than the user or the
     * platform. Kept as one list so the classification is reviewable in a
     * glance rather than scattered across implementations.
     */
    val UNTRUSTED_SOURCES: Set<String> = setOf(NOTIFICATION_POSTED, APP_FOREGROUND)

    fun trustFor(triggerId: String): TrustLevel =
        if (triggerId in UNTRUSTED_SOURCES) TrustLevel.UNTRUSTED else TrustLevel.TRUSTED

    fun isKnown(triggerId: String?): Boolean = triggerId != null && triggerId in ALL
}
