package ai.jarvis.app.assist

import ai.jarvis.app.audio.HeadsetMonitor
import ai.jarvis.app.automation.AutomationBridge
import ai.jarvis.app.automation.triggers.GeoState
import ai.jarvis.app.automation.triggers.GeofenceStates
import ai.jarvis.app.automation.triggers.TriggerIds
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.WakeWordGate
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONObject
import java.util.Calendar

/**
 * The Android half of [WakeWordGate]: gather the signals, ask the gate, and say
 * when the answer changes.
 *
 * ## The bug this exists for
 *
 * `WakeWordGate` implemented exactly the battery policy anyone would want —
 * listen at home during waking hours, listen on car Bluetooth, listen through a
 * worn headset — and **nothing in the app ever called `shouldListen`**. It had a
 * unit test, a paragraph of reasoning per rule, four SharedPreferences keys and
 * a section on the settings screen, all of it inert. `DEVIATIONS.md` asserted
 * the car rule as shipped behaviour. `SettingsActivity` labelled its own section
 * *"When to listen — saved, not yet in effect"*, which is the app telling the
 * truth about a feature that was not there.
 *
 * The reason it was never wired is the honest one and it is written up in
 * [WakeWordGate.decide]: the gate wants to know whether the phone is at home,
 * and a phone usually cannot say. This class is what makes the question
 * answerable — sometimes definitely, and otherwise definitely *unknown*, which
 * is a different thing from "no".
 *
 * ## Where each signal comes from, and what it costs
 *
 *  * **Headset** — [HeadsetMonitor], which is already how the conversation
 *    engine discovers its route. `AudioManager.getDevices` reports device
 *    *types* with no permission at all: Jarvis never learns which headset, only
 *    what kind. Costs one registered `AudioDeviceCallback`.
 *  * **Car Bluetooth** — the same device list, asked for a Bluetooth output
 *    (A2DP or SCO). Read directly rather than through
 *    [ai.jarvis.app.audio.AudioRoute.kind], because that resolves to ONE kind by
 *    priority: an earpiece in the ear masks the car stereo behind it, and for
 *    this question both matter. It is the same probe
 *    [ai.jarvis.app.channel.PresenceReporter] uses for `driving`, with the same
 *    honest limit — a Bluetooth speaker in the kitchen looks like a car from
 *    here. Naming the device would need BLUETOOTH_CONNECT, which is a dangerous
 *    permission to spend on a battery heuristic.
 *  * **Home** — a geofence the user has already configured, whose id names home
 *    ([HOME_FENCE_IDS]). `GeofenceStates` is the phone's own inside/outside
 *    memory, kept by the trigger layer for automations. Nothing new is
 *    registered here and no location is requested: this reads state that either
 *    exists or does not. When it does not, the answer is `null` — **unknown** —
 *    and [WakeWordGate.decide] is explicit about what it does with that.
 *  * **The hour** — the wall clock, re-checked on the hour. The one signal that
 *    is never in doubt.
 *
 * ## Edges, not polling
 *
 * Everything above is push except the clock. The audio device callback fires on
 * a headset or a car stereo appearing and disappearing;
 * [AutomationBridge.publishEvent] fans the geofence and Bluetooth trigger edges
 * out to whoever subscribed, and this subscribes. The single timer is armed for
 * the next hour boundary and re-armed after it fires — a wake listener that
 * stops at 23:00 must not need an event from somewhere else to notice.
 *
 * The trigger-layer subscription is a fast path, not the mechanism: it only
 * carries anything while `JarvisAutomationService` is up with location
 * triggers configured. The audio callback and the hourly timer work regardless,
 * which is why the geofence state is *read* on each evaluation rather than
 * cached from an event.
 */
class WakeListenWatch(
    context: Context,
    private val config: JarvisConfig,
    /** Called on the main thread whenever the gate's answer changes. */
    private val onChanged: (WakeWordGate.Decision) -> Unit,
    /** Local hour of day, 0..23. Injectable so a test can move the clock. */
    private val hourOfDay: () -> Int = { Calendar.getInstance().get(Calendar.HOUR_OF_DAY) },
) : AutomationBridge.DeviceEventSubscriber {

    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val audio = app.getSystemService(AudioManager::class.java)

    /**
     * Started with this watch and stopped with it, so Jarvis is not registered
     * for audio-device callbacks while nothing is listening.
     */
    private val headsets = HeadsetMonitor(app) { reevaluate() }

    @Volatile
    private var last: WakeWordGate.Decision? = null

    private var running = false

    /** Re-check on the hour, because the waking-hours window has edges too. */
    private val onTheHour = object : Runnable {
        override fun run() {
            if (!running) return
            reevaluate()
            main.postDelayed(this, msUntilNextHour())
        }
    }

    /**
     * The gate's answer right now, evaluated fresh.
     *
     * Callable without [start] — [WakeWordService] asks before it opens a
     * microphone, and that has to be a straight question with an answer rather
     * than a subscription that may not have delivered yet.
     */
    fun decide(): WakeWordGate.Decision = config.wakeWordGate().decide(
        atHome = atHome(),
        carBtConnected = carBluetoothConnected(),
        headsetCapture = headsetCapture(),
        hour = hourOfDay().coerceIn(0, 23),
        listenAtHome = config.wakeAtHome,
        listenInCar = config.wakeInCar,
    )

    /** Begin watching. Idempotent. */
    fun start() {
        if (running) return
        running = true
        headsets.headsetModeEnabled = config.headsetMode
        headsets.start()
        AutomationBridge.subscribe(this)
        last = decide()
        main.postDelayed(onTheHour, msUntilNextHour())
    }

    /** Stop watching. Idempotent. Releases the audio-device callback. */
    fun stop() {
        if (!running) return
        running = false
        main.removeCallbacks(onTheHour)
        AutomationBridge.unsubscribe(this)
        headsets.stop()
    }

    /**
     * A trigger fired. Only the four that can move this decision are acted on;
     * everything else on that bus is somebody else's business.
     *
     * The payload is deliberately ignored — an event is a *prod to re-read the
     * signals*, never the signal itself. A `geofence_enter` carries an id and a
     * distance written by the trigger layer, and taking the decision from it
     * would mean two places that believe they know where the phone is.
     */
    override fun onDeviceEvent(event: String, data: JSONObject, untrusted: Boolean) {
        when (event) {
            TriggerIds.BLUETOOTH_CONNECTED,
            TriggerIds.BLUETOOTH_DISCONNECTED,
            TriggerIds.GEOFENCE_ENTER,
            TriggerIds.GEOFENCE_EXIT,
            -> main.post { reevaluate() }
        }
    }

    /** Re-read everything; call [onChanged] only if the verdict actually moved. */
    private fun reevaluate() {
        val next = try {
            decide()
        } catch (t: Throwable) {
            // A probe that throws must never take the wake listener down with
            // it. Staying on the last verdict is the conservative answer: it is
            // whatever the user's settings last produced.
            Log.w(TAG, "could not evaluate the listening gate", t)
            return
        }
        val previous = last
        last = next
        if (previous != null && previous == next) return
        Log.i(TAG, "listening gate: ${next.listen} (${next.reason})")
        main.post { onChanged(next) }
    }

    // --- the signals --------------------------------------------------------

    /**
     * Inside the home zone, outside it, or **null when this phone cannot say**.
     *
     * Null is the ordinary answer and it is not a failure — see
     * [WakeWordGate.decide], which is where the consequence of not knowing is
     * decided. A fence that exists but has never had a usable fix is
     * [GeoState.UNKNOWN], which is also null: "configured" is not "answered".
     */
    private fun atHome(): Boolean? {
        val fence = try {
            GeofenceStates.snapshot().firstOrNull { it.id.trim().lowercase() in HOME_FENCE_IDS }
        } catch (t: Throwable) {
            Log.d(TAG, "no geofence state to read", t)
            null
        } ?: return null
        return when (fence.state) {
            GeoState.INSIDE -> true
            GeoState.OUTSIDE -> false
            GeoState.UNKNOWN -> null
        }
    }

    /** A Bluetooth audio output is connected. See the class doc for the limit. */
    private fun carBluetoothConnected(): Boolean = try {
        val devices = audio?.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        devices != null && devices.any {
            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP ||
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
        }
    } catch (t: Throwable) {
        Log.d(TAG, "could not read the audio device list", t)
        false
    }

    /**
     * Jarvis is capturing through a worn headset.
     *
     * Straight off [ai.jarvis.app.audio.AudioRoute.capturesThroughHeadset],
     * which already requires the user's `headsetMode` opt-in — so this rule,
     * the only one that opens a microphone in public, cannot fire for somebody
     * who never turned headset capture on.
     */
    private fun headsetCapture(): Boolean {
        headsets.headsetModeEnabled = config.headsetMode
        return headsets.route.capturesThroughHeadset
    }

    private fun msUntilNextHour(): Long {
        val now = Calendar.getInstance()
        val minutes = now.get(Calendar.MINUTE)
        val seconds = now.get(Calendar.SECOND)
        val remaining = ((59 - minutes) * 60L + (60 - seconds)) * 1000L
        // Never zero: a timer that re-arms for "now" is a spin loop on the main
        // thread, and the arithmetic above lands on zero exactly at :00:00.
        return remaining.coerceAtLeast(1_000L)
    }

    companion object {
        private const val TAG = "JarvisWakeGate"

        /**
         * Geofence ids that mean "home".
         *
         * Matched by name because that is all a user-defined fence has: the id
         * comes from the task's own `id`/`name` param (see
         * `TriggerSpecs.geofence`). A short, obvious list rather than a fuzzy
         * match — a fence called "homebrew shop" must not silence the wake word
         * every time the user leaves it.
         */
        val HOME_FENCE_IDS = setOf("home", "house", "casa")
    }
}
