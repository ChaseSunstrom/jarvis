package ai.jarvis.app.config

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import java.util.UUID

/**
 * Everything the app needs to reach the user's own Jarvis server, plus the
 * key namespace the automation module shares.
 *
 * SharedPreferences rather than DataStore on purpose: the assist path reads
 * this on the cold-start critical path and the automation path reads it from a
 * WebSocket callback thread. A synchronous, thread-safe, dependency-free store
 * is the right shape here; DataStore's suspend API would just add a coroutine
 * hop to both. Nothing in here is backed up (see AndroidManifest allowBackup +
 * res/xml/data_extraction_rules.xml) — it holds a bearer token.
 *
 * Everything is stored under one prefs file, [FILE]. The policy store lives in
 * a SEPARATE file, [Policy.FILE], so a "reset my connection settings" action
 * can never clear the user's `never` rules by accident.
 */
class JarvisConfig(context: Context) {

    private val app = context.applicationContext
    private val prefs: SharedPreferences =
        app.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    // --- connection --------------------------------------------------------

    /** Base URL of jarvis-core, e.g. `http://192.168.2.10:8123`. No trailing slash. */
    var serverUrl: String
        get() = ServerUrl.normalize(prefs.getString(KEY_SERVER_URL, "") ?: "")
        set(v) = prefs.edit().putString(KEY_SERVER_URL, ServerUrl.normalize(v)).apply()

    /** Long-lived access token issued by jarvis-core. */
    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(v) = prefs.edit().putString(KEY_TOKEN, v.trim()).apply()

    /** Named voice pipeline on the server. */
    var pipeline: String
        get() = prefs.getString(KEY_PIPELINE, DEFAULT_PIPELINE) ?: DEFAULT_PIPELINE
        set(v) = prefs.edit()
            .putString(KEY_PIPELINE, v.trim().ifEmpty { DEFAULT_PIPELINE })
            .apply()

    /** Human-readable name sent in `jarvis/device/register`. */
    var deviceName: String
        get() = prefs.getString(KEY_DEVICE_NAME, "")
            ?.takeIf { it.isNotEmpty() }
            ?: defaultDeviceName()
        set(v) = prefs.edit().putString(KEY_DEVICE_NAME, v.trim()).apply()

    /**
     * Stable per-install device id for `jarvis/device/register`. Generated once
     * and never derived from a hardware identifier: a reinstall should look
     * like a new device that has to be re-authorised, not like the old one.
     */
    val deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, null) ?: synchronized(this) {
            prefs.getString(KEY_DEVICE_ID, null) ?: UUID.randomUUID().toString().also {
                prefs.edit().putString(KEY_DEVICE_ID, it).apply()
            }
        }

    /** True once the minimum needed to connect is present. */
    val isConfigured: Boolean
        get() = serverUrl.isNotEmpty() && token.isNotEmpty()

    // --- wake-word gating --------------------------------------------------
    //
    // Third-party apps get no low-power DSP hotword path on Android, so
    // always-on detection costs real battery and holds an open mic. These
    // settings feed [WakeWordGate], which decides when it is worth it.

    /** Master switch for always-on "Hey Jarvis" detection. */
    var wakeWordEnabled: Boolean
        get() = prefs.getBoolean(KEY_WAKE_ENABLED, false)
        set(v) = prefs.edit().putBoolean(KEY_WAKE_ENABLED, v).apply()

    /** Listen whenever the car's Bluetooth is connected, hour of day ignored. */
    var wakeInCar: Boolean
        get() = prefs.getBoolean(KEY_WAKE_IN_CAR, true)
        set(v) = prefs.edit().putBoolean(KEY_WAKE_IN_CAR, v).apply()

    /** Listen at home, but only inside the waking-hours window below. */
    var wakeAtHome: Boolean
        get() = prefs.getBoolean(KEY_WAKE_AT_HOME, true)
        set(v) = prefs.edit().putBoolean(KEY_WAKE_AT_HOME, v).apply()

    /** First hour (inclusive, 0..23) of the waking window. */
    var wakingHourStart: Int
        get() = prefs.getInt(KEY_WAKE_HOUR_START, WakeWordGate.DEFAULT_WAKING_HOUR_START)
        set(v) = prefs.edit().putInt(KEY_WAKE_HOUR_START, v.coerceIn(0, 23)).apply()

    /** End hour (exclusive, 0..24) of the waking window. */
    var wakingHourEnd: Int
        get() = prefs.getInt(KEY_WAKE_HOUR_END, WakeWordGate.DEFAULT_WAKING_HOUR_END)
        set(v) = prefs.edit().putInt(KEY_WAKE_HOUR_END, v.coerceIn(0, 24)).apply()

    /** A gate built from the current settings. */
    fun wakeWordGate(): WakeWordGate = WakeWordGate(wakingHourStart, wakingHourEnd)

    private fun defaultDeviceName(): String {
        val model = (Build.MODEL ?: "").trim()
        return if (model.isEmpty()) "Android device" else model
    }

    companion object {
        private const val FILE = "jarvis_config"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_PIPELINE = "pipeline"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_WAKE_ENABLED = "wake_enabled"
        private const val KEY_WAKE_IN_CAR = "wake_in_car"
        private const val KEY_WAKE_AT_HOME = "wake_at_home"
        private const val KEY_WAKE_HOUR_START = "wake_hour_start"
        private const val KEY_WAKE_HOUR_END = "wake_hour_end"

        const val DEFAULT_PIPELINE = "Jarvis"
    }

    /**
     * The per-action policy namespace. Owned here so the automation module and
     * the settings UI cannot drift apart on key naming, and so the policy store
     * is discoverable from one place.
     *
     * One entry per action id: `action_policy.<action_id>` -> one of
     * [ALLOW_ALWAYS] / [ASK] / [NEVER]. Absent means [ASK].
     *
     * The rules the store must uphold (enforced by the automation module, not
     * by this object):
     *
     *  * [NEVER] beats everything, including a Tier-1 read and an incoming
     *    server command that claims a lower tier.
     *  * [ALLOW_ALWAYS] is only meaningful for Tier 2. A Tier-3 action is
     *    confirmed every single time; writing [ALLOW_ALWAYS] for one must be
     *    treated as [ASK].
     *  * The tier itself is NOT stored here. It comes from the device-local
     *    action table, and an incoming `tier` field may only raise it.
     */
    object Policy {
        /** SharedPreferences file holding the per-action policy. */
        const val FILE = "jarvis_policy"

        /** Prefix for per-action entries. */
        const val KEY_PREFIX = "action_policy."

        const val ALLOW_ALWAYS = "allow_always"
        const val ASK = "ask"
        const val NEVER = "never"

        /** SharedPreferences file holding the user-viewable audit log. */
        const val AUDIT_FILE = "jarvis_audit"

        /** Key for the JSON-encoded audit entries within [AUDIT_FILE]. */
        const val AUDIT_KEY = "entries"

        fun keyFor(actionId: String): String = KEY_PREFIX + actionId

        fun open(context: Context): SharedPreferences =
            context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

        fun openAudit(context: Context): SharedPreferences =
            context.applicationContext.getSharedPreferences(AUDIT_FILE, Context.MODE_PRIVATE)
    }
}
