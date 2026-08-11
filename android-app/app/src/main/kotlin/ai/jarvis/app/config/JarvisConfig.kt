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

    /**
     * Base URL of the Jarvis server, e.g. `http://192.168.2.10:8080`.
     * No trailing slash.
     *
     * May be either jarvis-core or the jarvis-web console — [serverKind]
     * records which, once discovered.
     */
    var serverUrl: String
        get() = ServerUrl.normalize(prefs.getString(KEY_SERVER_URL, "") ?: "")
        set(v) {
            val next = ServerUrl.normalize(v)
            val changed = next != ServerUrl.normalize(prefs.getString(KEY_SERVER_URL, "") ?: "")
            prefs.edit().apply {
                putString(KEY_SERVER_URL, next)
                // A remembered kind describes the OLD address. Carrying it over
                // would make the app skip discovery and dial a path the new
                // server may not have — the exact failure this pair exists to
                // prevent, reintroduced by an edit.
                if (changed) remove(KEY_SERVER_KIND)
            }.apply()
        }

    /** Long-lived access token issued by jarvis-core. */
    var token: String
        get() = prefs.getString(KEY_TOKEN, "") ?: ""
        set(v) = prefs.edit().putString(KEY_TOKEN, v.trim()).apply()

    /**
     * Which server is at [serverUrl], once discovered — see [ServerKind].
     *
     * Remembered rather than re-probed so the common case is one connect
     * instead of a failed one followed by a good one. Cleared whenever
     * [serverUrl] changes, because the answer belongs to that address and
     * keeping it across an edit is how the app would confidently dial the
     * wrong path at a new server.
     */
    var serverKind: ServerKind?
        get() = prefs.getString(KEY_SERVER_KIND, null)
            ?.let { name -> ServerKind.entries.firstOrNull { it.name == name } }
        set(v) = prefs.edit().apply {
            if (v == null) remove(KEY_SERVER_KIND) else putString(KEY_SERVER_KIND, v.name)
        }.apply()

    /**
     * Whether the updater offers the per-push builds CI publishes.
     *
     * Off by default: those are every commit, not a considered release. On,
     * the user is asking to run whatever was built last, which is a reasonable
     * thing to want from a box you own and a bad default for one you do not
     * watch.
     */
    var allowPrereleaseUpdates: Boolean
        get() = prefs.getBoolean(KEY_PRERELEASE, false)
        set(v) = prefs.edit().putBoolean(KEY_PRERELEASE, v).apply()

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

    /**
     * How many actions this device registered with the server the last time
     * `jarvis/device/register` succeeded; 0 if it never has.
     *
     * Written by [ai.jarvis.app.channel.JarvisChannel] on registration and read
     * by the power-on sequence, which types it as its third system-check line.
     * It is a display value and nothing else — no policy, no tier and no
     * dispatch decision reads it, so a stale or absent count costs one line of
     * the boot animation and nothing more.
     */
    var lastActionCount: Int
        get() = try {
            prefs.getInt(KEY_ACTION_COUNT, 0).coerceAtLeast(0)
        } catch (t: ClassCastException) {
            // Something wrote a non-int under this key. A cosmetic count is not
            // worth throwing on the cold-start path.
            0
        }
        set(v) = prefs.edit().putInt(KEY_ACTION_COUNT, v.coerceAtLeast(0)).apply()

    // --- wake-word gating --------------------------------------------------
    //
    // Third-party apps get no low-power DSP hotword path on Android, so
    // always-on detection costs real battery and holds an open mic. These
    // settings feed [WakeWordGate], which decides when it is worth it.

    /**
     * Whether the first-run checklist has been shown.
     *
     * Not "setup is complete" — the user may have looked at it and decided they
     * do not want Jarvis drawing over other apps, and that is their call. This
     * only stops the home screen opening it a second time; the banner is what
     * carries the message from then on.
     */
    var setupChecklistShown: Boolean
        get() = prefs.getBoolean(KEY_SETUP_SHOWN, false)
        set(v) = prefs.edit().putBoolean(KEY_SETUP_SHOWN, v).apply()

    /**
     * Detect the wake word on this phone instead of on the server.
     *
     * Off by default, and deliberately: it needs weights the user has to
     * download first, and a feature that silently depends on a file that may
     * not be there is one that silently stops working. On, and with the models
     * present, nothing leaves the phone until the name has been said — which is
     * the whole point, since the alternative is a permanently open socket
     * carrying everything the microphone hears.
     */
    var wakeWordOnDevice: Boolean
        get() = prefs.getBoolean(KEY_WAKE_ON_DEVICE, false)
        set(v) = prefs.edit().putBoolean(KEY_WAKE_ON_DEVICE, v).apply()

    /**
     * Transcribe on this phone rather than streaming the audio to the server.
     *
     * Defaults ON. The streaming path sends the whole utterance as PCM for
     * every turn, and a phone that can turn it into a sentence itself should
     * send the sentence. Where no on-device recogniser exists — a degoogled
     * build with nothing providing one — `LocalTranscriber.isAvailable` is
     * false and the streaming path runs anyway; the setting is what the user
     * WANTS, and the app says which is actually happening rather than letting
     * the two be confused.
     */
    var sttOnDevice: Boolean
        get() = prefs.getBoolean(KEY_STT_ON_DEVICE, true)
        set(v) = prefs.edit().putBoolean(KEY_STT_ON_DEVICE, v).apply()

    /** BCP-47 tag the on-device recogniser is asked for. */
    var sttLanguage: String
        get() = prefs.getString(KEY_STT_LANGUAGE, null)?.takeIf { it.isNotBlank() }
            ?: java.util.Locale.getDefault().toLanguageTag()
        set(v) = prefs.edit().putString(KEY_STT_LANGUAGE, v.trim()).apply()

    /** Master switch for always-on "Hey Jarvis" detection. */
    var wakeWordEnabled: Boolean
        get() = prefs.getBoolean(KEY_WAKE_ENABLED, false)
        set(v) = prefs.edit().putBoolean(KEY_WAKE_ENABLED, v).apply()

    /**
     * The microphone is off, by the user's choice, on the home screen.
     *
     * [ai.jarvis.app.MainActivity] has no talk button — opening it opens the
     * mic — so this is the off switch, and it is persisted because a kill
     * switch that forgets across a restart is not a kill switch. It governs
     * that screen only: "Hey Jarvis" has its own master switch in
     * [wakeWordEnabled], and muting the screen you are looking at should not
     * silently disarm the listener that works when you are not.
     *
     * Defaults to false, which is what "just listen when the app is open"
     * means. The permission is still the real gate — this cannot open a
     * microphone Android has not granted.
     */
    var micMuted: Boolean
        get() = prefs.getBoolean(KEY_MIC_MUTED, false)
        set(v) = prefs.edit().putBoolean(KEY_MIC_MUTED, v).apply()

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

    // --- headset / earpiece ------------------------------------------------
    //
    // See [ai.jarvis.app.audio.AudioRoute]. Defaults to OFF: plugging in a
    // headset must never silently move the microphone off the phone. The user
    // opts in once, and only then does Jarvis capture through worn hardware,
    // take over the headset button, or offer warm-link.

    /** Capture through a connected headset rather than the phone microphone. */
    var headsetMode: Boolean
        get() = prefs.getBoolean(KEY_HEADSET_MODE, false)
        set(v) = prefs.edit().putBoolean(KEY_HEADSET_MODE, v).apply()

    /**
     * Keep listening after a reply while a worn headset is connected, so a
     * follow-up needs no wake word.
     *
     * Requires [headsetMode] AND an echo-cancelled route — `AudioRoute
     * .warmLinkEligible` is the authority and this flag can only ever narrow
     * it, never widen it. Without cancellation an open mic hears the tail of
     * Jarvis's own reply and starts a turn against itself.
     */
    var warmLink: Boolean
        get() = headsetMode && prefs.getBoolean(KEY_WARM_LINK, false)
        set(v) = prefs.edit().putBoolean(KEY_WARM_LINK, v).apply()

    /** Let the headset's button summon Jarvis. See [ai.jarvis.app.audio.MediaButtonGate]. */
    var headsetButton: Boolean
        get() = headsetMode && prefs.getBoolean(KEY_HEADSET_BUTTON, true)
        set(v) = prefs.edit().putBoolean(KEY_HEADSET_BUTTON, v).apply()

    private fun defaultDeviceName(): String {
        val model = (Build.MODEL ?: "").trim()
        return if (model.isEmpty()) "Android device" else model
    }

    companion object {
        private const val FILE = "jarvis_config"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_SERVER_KIND = "server_kind"
        private const val KEY_PRERELEASE = "allow_prerelease_updates"
        private const val KEY_PIPELINE = "pipeline"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_SETUP_SHOWN = "setup_checklist_shown"
        private const val KEY_WAKE_ON_DEVICE = "wake_on_device"
        private const val KEY_STT_ON_DEVICE = "stt_on_device"
        private const val KEY_STT_LANGUAGE = "stt_language"
        private const val KEY_WAKE_ENABLED = "wake_enabled"
        private const val KEY_MIC_MUTED = "mic_muted"
        private const val KEY_WAKE_IN_CAR = "wake_in_car"
        private const val KEY_WAKE_AT_HOME = "wake_at_home"
        private const val KEY_WAKE_HOUR_START = "wake_hour_start"
        private const val KEY_WAKE_HOUR_END = "wake_hour_end"
        private const val KEY_HEADSET_MODE = "headset_mode"
        private const val KEY_WARM_LINK = "headset_warm_link"
        private const val KEY_HEADSET_BUTTON = "headset_button"
        private const val KEY_ACTION_COUNT = "last_action_count"

        const val DEFAULT_PIPELINE = "Jarvis"
    }

    /**
     * The per-action policy namespace, so the automation module and the
     * settings UI cannot drift apart on key naming and the store is
     * discoverable from one place.
     *
     * These constants MIRROR `ai.jarvis.app.automation.policy.PolicyStore`,
     * which is the implementation and the authority. If the two ever disagree,
     * PolicyStore wins and this block is the bug.
     *
     * One entry per action id: `policy.<action_id>` -> one of [ALLOW_ALWAYS] /
     * [ASK] / [NEVER]. Absent means [ASK].
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

        /** Prefix for per-action entries: `policy.<action_id>`. */
        const val KEY_PREFIX = "policy."

        const val ALLOW_ALWAYS = "allow_always"
        const val ASK = "ask"
        const val NEVER = "never"

        /** Master switch: all automation on/off. */
        const val KEY_AUTOMATION_ENABLED = "automation_enabled"

        /** Panic switch: refuse everything until cleared. */
        const val KEY_PANIC = "panic"

        /** Directory under `filesDir` holding the append-only audit log. */
        const val AUDIT_DIR = "jarvis"

        /** One JSON object per line, newest last. */
        const val AUDIT_FILE_NAME = "audit.jsonl"

        fun keyFor(actionId: String): String = KEY_PREFIX + actionId

        fun open(context: Context): SharedPreferences =
            context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)
    }
}
