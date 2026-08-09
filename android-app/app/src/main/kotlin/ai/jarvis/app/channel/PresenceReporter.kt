package ai.jarvis.app.channel

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.BatteryManager
import android.os.Handler
import android.os.HandlerThread
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject

/**
 * Presence: telling jarvis-core whether the user is actually *here*.
 *
 * The server's `jarvis/presence.py` ranks devices by how likely you are to see
 * or hear something right now, and it can only rank what the devices report.
 * This is the phone's half — a handful of cheap signals, pushed up as a
 * `device_event`:
 *
 * ```json
 * {"type": "device_event", "event": "presence",
 *  "data": {"screen_on": true, "locked": false, "last_interaction": 1.7e9,
 *           "driving": false, "zone": "home", "audio_available": true,
 *           "muted": false, "battery": 82, "charging": true}}
 * ```
 *
 * The keys are exactly the attribute names on the server's `DevicePresence`,
 * whose `update()` sets what it recognises and ignores the rest — so a signal
 * this phone cannot determine is left out rather than guessed at.
 *
 * ## It is telemetry, and only telemetry
 *
 * One-way. It carries no user content, it is not a command, and nothing the
 * server sends back in response to it can run anything: the reply path for a
 * presence event is a `jarvis_message` (see
 * [ai.jarvis.app.companion.CompanionMessageHandler]) or a `device_command`,
 * both of which go through their own gates. This class holds no reference to
 * the action dispatcher, which is what makes that structural rather than a
 * convention.
 *
 * ## Not a firehose
 *
 * A report goes out on a *meaningful change* or once every
 * [PresenceThrottle.HEARTBEAT_MS], and never more often than
 * [PresenceThrottle.MIN_INTERVAL_MS]. "Battery went 81 -> 80" is not a change;
 * "the screen locked", "the car stereo connected", "you came back" are. The
 * comparison is always against the last snapshot that was actually *sent*, so a
 * change the rate floor suppressed is still pending on the next tick rather
 * than lost. The rules live in [PresenceThrottle], which is pure and mirrored
 * by `tools/presence_signals_test.py`.
 */
class PresenceReporter(
    context: Context,
    /** Reaches [JarvisChannel.sendEvent] and nothing else. */
    private val emit: (String, JSONObject) -> Boolean,
    /** Monotonic milliseconds. Never wall clock — the throttle depends on it. */
    private val clock: () -> Long = { SystemClock.elapsedRealtime() },
    /** Epoch milliseconds, for `last_interaction`, which the server compares
     *  against its own wall clock. */
    private val wall: () -> Long = { System.currentTimeMillis() },
) {

    private val app = context.applicationContext

    private var thread: HandlerThread? = null
    private var handler: Handler? = null

    @Volatile private var running = false
    @Volatile private var lastSent: PresenceSignals? = null
    @Volatile private var lastSentAt = 0L
    @Volatile private var sendCount = 0

    /** Epoch millis of the last thing the user did on this phone. */
    @Volatile private var interactionAt = 0L

    /**
     * Car Bluetooth, pushed in from the trigger layer's `bluetooth_connected` /
     * `bluetooth_disconnected` broadcasts — the same signal
     * [ai.jarvis.app.config.WakeWordGate] takes as `carBtConnected`. Pushed
     * rather than polled because the broadcast is the authoritative edge and
     * because naming a car stereo needs BLUETOOTH_CONNECT, which the receiver
     * already holds.
     */
    @Volatile private var carBluetooth: Boolean? = null

    /** The zone the server told us we are in, when it has. */
    @Volatile private var zone: String? = null

    /** True while a Jarvis surface is on screen. */
    @Volatile private var jarvisForeground = false

    val sends: Int get() = sendCount
    val lastSignals: PresenceSignals? get() = lastSent

    // --- lifecycle ----------------------------------------------------------

    /** Begin polling. Idempotent. */
    fun start() {
        if (running) return
        running = true
        val worker = HandlerThread("jarvis-presence").also { it.start() }
        thread = worker
        val h = Handler(worker.looper)
        handler = h
        h.post(tick)
        Log.i(TAG, "presence reporting started")
    }

    /** Stop polling. Idempotent. Does not clear the last snapshot. */
    fun stop() {
        if (!running) return
        running = false
        handler?.removeCallbacksAndMessages(null)
        handler = null
        thread?.quitSafely()
        thread = null
    }

    /**
     * Forget what the server knows, so the next poll reports everything.
     *
     * Called when the socket reconnects: a fresh session means a fresh
     * `DevicePresence`, and continuing to compare against the old snapshot
     * would leave the server with defaults until something happened to change.
     */
    fun onReconnected() {
        lastSent = null
        lastSentAt = 0L
        handler?.post(tick)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            try {
                poll()
            } catch (t: Throwable) {
                // A probe must never kill the loop; a phone that stops
                // reporting presence quietly disappears from the server's
                // routing and every message lands somewhere else.
                Log.w(TAG, "presence poll failed", t)
            }
            handler?.postDelayed(this, PresenceThrottle.POLL_INTERVAL_MS)
        }
    }

    // --- signals the app pushes in -----------------------------------------

    /** The user just did something here. The strongest presence signal there is. */
    fun noteInteraction() {
        interactionAt = wall()
        handler?.post { poll() }
    }

    /** Car stereo connected/disconnected, from the Bluetooth trigger. */
    fun setCarBluetooth(connected: Boolean) {
        if (carBluetooth == connected) return
        carBluetooth = connected
        handler?.post { poll() }
    }

    /** The zone the server placed this device in, or null to forget it. */
    fun setZone(value: String?) {
        val clean = value?.trim()?.take(64)?.takeIf { it.isNotEmpty() }
        if (zone == clean) return
        zone = clean
        handler?.post { poll() }
    }

    /** A Jarvis surface came to the front, or left it. */
    fun setForeground(value: Boolean) {
        if (jarvisForeground == value) return
        jarvisForeground = value
        if (value) interactionAt = wall()
        handler?.post { poll() }
    }

    // --- the decision -------------------------------------------------------

    /**
     * Sample once and send if it is worth sending. Returns the reason it sent,
     * or null. Public so it can be driven directly from a test or from an
     * event that wants an immediate report.
     */
    fun poll(force: Boolean = false): String? {
        val signals = sample()
        val previous = lastSent
        val since = if (previous == null) 0L else clock() - lastSentAt
        val reason = when {
            force -> "forced"
            else -> PresenceThrottle.shouldSend(previous, signals, since)
        } ?: return null

        val delivered = try {
            emit(EVENT_PRESENCE, signals.toJson())
        } catch (t: Throwable) {
            Log.w(TAG, "could not emit presence", t)
            false
        }
        if (!delivered) {
            // Dropped by the rate limit or an offline socket. Do NOT record it
            // as sent: the server still holds the old snapshot, so the change
            // is still pending and the next tick tries again.
            return null
        }
        lastSent = signals
        lastSentAt = clock()
        sendCount++
        return reason
    }

    /** Read every probe. Each one is allowed to fail on its own. */
    fun sample(): PresenceSignals {
        val screenOn = probe(false) {
            app.getSystemService(PowerManager::class.java)?.isInteractive == true
        }
        val locked = probe(false) {
            app.getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true
        }
        val audio = app.getSystemService(AudioManager::class.java)
        val bluetoothAudio = probe(false) { hasBluetoothOutput(audio) }
        val (battery, charging) = batteryState()

        // An unlocked, interactive screen IS the user being here. Android gives
        // a third-party app no idle timer, so the honest signal is the screen
        // plus whatever the app itself has seen.
        if (screenOn && !locked) interactionAt = maxOf(interactionAt, wall())

        return PresenceSignals(
            screenOn = screenOn,
            locked = locked,
            lastInteractionEpochMs = interactionAt,
            active = PresenceThrottle.activeAt(interactionAt, wall()),
            driving = PresenceThrottle.driving(carBluetooth ?: bluetoothAudio),
            zone = zone,
            audioAvailable = probe(true) { audioAvailable(audio) },
            muted = probe(false) { muted(audio) },
            battery = battery,
            charging = charging,
            jarvisForeground = jarvisForeground,
        )
    }

    // --- probes -------------------------------------------------------------

    private fun hasBluetoothOutput(audio: AudioManager?): Boolean {
        val devices = audio?.getDevices(AudioManager.GET_DEVICES_OUTPUTS) ?: return false
        return devices.any {
            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
        }
    }

    /** Can this phone make a noise the user would hear right now? */
    private fun audioAvailable(audio: AudioManager?): Boolean {
        if (audio == null) return false
        // Mid-call, speech from an assistant is neither wanted nor audible.
        if (audio.mode == AudioManager.MODE_IN_CALL ||
            audio.mode == AudioManager.MODE_IN_COMMUNICATION
        ) {
            return false
        }
        val outputs = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        if (outputs.isEmpty()) return false
        return audio.getStreamVolume(AudioManager.STREAM_MUSIC) > 0
    }

    /** Has the user asked for quiet? Silent/vibrate or a zeroed media stream. */
    private fun muted(audio: AudioManager?): Boolean {
        if (audio == null) return true
        if (audio.ringerMode != AudioManager.RINGER_MODE_NORMAL) return true
        return audio.getStreamVolume(AudioManager.STREAM_MUSIC) <= 0
    }

    private fun batteryState(): Pair<Int?, Boolean?> = try {
        val intent = app.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        if (intent == null) {
            null to null
        } else {
            val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
            val percent =
                if (level >= 0 && scale > 0) (level * 100 / scale).coerceIn(0, 100) else null
            val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
            val plugged = intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0)
            val charging = when {
                status == BatteryManager.BATTERY_STATUS_CHARGING -> true
                status == BatteryManager.BATTERY_STATUS_FULL -> true
                status == BatteryManager.BATTERY_STATUS_DISCHARGING -> false
                status == BatteryManager.BATTERY_STATUS_NOT_CHARGING -> false
                else -> plugged != 0
            }
            percent to charging
        }
    } catch (t: Throwable) {
        Log.d(TAG, "battery state unavailable", t)
        null to null
    }

    private inline fun <T> probe(fallback: T, body: () -> T): T = try {
        body()
    } catch (t: Throwable) {
        Log.d(TAG, "a presence probe failed", t)
        fallback
    }

    companion object {
        private const val TAG = "JarvisPresence"

        /** The `device_event` name the server routes into its presence registry. */
        const val EVENT_PRESENCE = "presence"
    }
}

/**
 * One sample. Immutable, so a stored snapshot cannot drift under comparison.
 *
 * `lastInteractionEpochMs` is wall-clock on purpose: the server compares it to
 * *its* `time.time()`, so a monotonic value would be meaningless there. Every
 * other timing decision in this file uses the monotonic clock.
 */
data class PresenceSignals(
    val screenOn: Boolean = false,
    val locked: Boolean = true,
    val lastInteractionEpochMs: Long = 0L,
    /** Derived at sample time: did the user touch this phone recently? Null
     *  when it has never seen an interaction, so an unknown cannot flap. */
    val active: Boolean? = null,
    val driving: Boolean = false,
    val zone: String? = null,
    val audioAvailable: Boolean = true,
    val muted: Boolean = false,
    val battery: Int? = null,
    val charging: Boolean? = null,
    val jarvisForeground: Boolean = false,
) {
    /**
     * The `data` block of the `presence` device_event.
     *
     * Fields this phone could not determine are omitted rather than sent as
     * null: the server skips nulls anyway, and leaving them out keeps the frame
     * honest about what was actually measured.
     */
    fun toJson(): JSONObject {
        val out = JSONObject()
            .put("screen_on", screenOn)
            .put("locked", locked)
            .put("driving", driving)
            .put("audio_available", audioAvailable)
            .put("muted", muted)
            .put("jarvis_foreground", jarvisForeground)
        if (lastInteractionEpochMs > 0L) {
            // Seconds, because that is what the server's DevicePresence stores.
            out.put("last_interaction", lastInteractionEpochMs / 1000.0)
        }
        battery?.let { out.put("battery", it) }
        charging?.let { out.put("charging", it) }
        zone?.let { out.put("zone", it) }
        return out
    }
}

/**
 * PURE LOGIC — no Android imports, no I/O, no clock. When a presence sample is
 * worth a frame.
 *
 * Split out for the same reason the policy table is: it is the part that can be
 * wrong in a way nobody notices (a phone that reports too much drains the
 * battery; one that reports too little silently drops out of the server's
 * routing), and it is the part `tools/presence_signals_test.py` can execute.
 */
object PresenceThrottle {

    /** Send at least this often. The server calls a device ABSENT after 15
     *  minutes of silence, so this has to sit well inside that. */
    const val HEARTBEAT_MS = 60_000L

    /** Never two reports closer together than this, whatever changed. */
    const val MIN_INTERVAL_MS = 3_000L

    /** How often to look. Looking is cheap; sending is what is throttled. */
    const val POLL_INTERVAL_MS = 5_000L

    /** Mirrors the server's `ACTIVE_WITHIN`. Crossing it is the difference
     *  between "in your hand" and "in the room", so it is a real change. */
    const val ACTIVE_WITHIN_MS = 120_000L

    /** Battery moves constantly; only a step this large is worth a frame. */
    const val BATTERY_STEP = 5

    /**
     * The signals compared by [meaningfulChange], in order. The mirror in
     * `tools/presence_signals_test.py` checks this list against its own, so the
     * two copies cannot drift apart quietly.
     */
    val TRACKED = listOf(
        "screen_on", "locked", "driving", "zone",
        "audio_available", "muted", "charging", "jarvis_foreground",
        "active", "battery",
    )

    /**
     * Is the user active, given `now` and the last interaction? Null when the
     * phone has never seen one, so an unknown value cannot flap the edge.
     */
    fun activeAt(lastInteractionEpochMs: Long, nowEpochMs: Long): Boolean? {
        if (lastInteractionEpochMs <= 0L) return null
        return nowEpochMs - lastInteractionEpochMs <= ACTIVE_WITHIN_MS
    }

    /** Driving, as far as this phone can tell: the car stereo is connected. */
    fun driving(carBluetoothConnected: Boolean): Boolean = carBluetoothConnected

    /**
     * The first signal worth a frame, or null if nothing moved.
     *
     * Deliberately does NOT compare `lastInteractionEpochMs` directly: it moves
     * every second the phone is in use, and reporting each tick is the firehose
     * this object exists to prevent. What matters is the *edge* — screen on/off,
     * lock/unlock, the car, going quiet — and the raw timestamp rides along
     * with whichever frame those produce.
     */
    fun meaningfulChange(before: PresenceSignals?, after: PresenceSignals): String? {
        if (before == null) return "first report"
        if (before.screenOn != after.screenOn) return "screen_on"
        if (before.locked != after.locked) return "locked"
        if (before.driving != after.driving) return "driving"
        if (before.zone != after.zone) return "zone"
        if (before.audioAvailable != after.audioAvailable) return "audio_available"
        if (before.muted != after.muted) return "muted"
        if (before.charging != after.charging) return "charging"
        if (before.jarvisForeground != after.jarvisForeground) return "jarvis_foreground"
        if (before.active != after.active) return "active"
        if (batteryMoved(before.battery, after.battery)) return "battery"
        return null
    }

    fun batteryMoved(before: Int?, after: Int?): Boolean = when {
        after == null -> false
        before == null -> true
        else -> kotlin.math.abs(after - before) >= BATTERY_STEP
    }

    /**
     * Why this sample should be sent, or null to stay quiet.
     *
     * Order is the policy:
     *  1. nothing reported yet -> send;
     *  2. the heartbeat is due -> send, changed or not;
     *  3. inside the rate floor -> stay quiet *whatever* changed (it is still
     *     pending against the last sent snapshot, so the next tick sends it);
     *  4. otherwise, only on a meaningful change.
     */
    fun shouldSend(
        before: PresenceSignals?,
        after: PresenceSignals,
        sinceSentMs: Long,
        heartbeatMs: Long = HEARTBEAT_MS,
        minIntervalMs: Long = MIN_INTERVAL_MS,
    ): String? {
        if (before == null) return "first report"
        if (sinceSentMs >= heartbeatMs) return "heartbeat"
        if (sinceSentMs < minIntervalMs) return null
        return meaningfulChange(before, after)
    }
}
