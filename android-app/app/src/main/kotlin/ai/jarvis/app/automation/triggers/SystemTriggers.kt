package ai.jarvis.app.automation.triggers

import android.Manifest
import android.bluetooth.BluetoothDevice
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.os.BatteryManager
import android.util.Log
import org.json.JSONObject

/**
 * Every trigger that is really a broadcast in a coat.
 *
 * They all share [SystemEventBus]: one registered receiver, many triggers. That
 * keeps the registration cost flat as triggers are added, and it means
 * unregistering happens in exactly one place — [JarvisAutomationService]'s
 * `onDestroy` — rather than N places that can each be forgotten.
 */
private const val TAG = "JarvisTriggers"

/**
 * Base class: subscribe to the bus on [start], drop the subscription on [stop].
 *
 * [handle] returns a payload to emit, or null to ignore the intent. Returning
 * null is the normal case — every trigger sees every broadcast.
 */
abstract class BroadcastTrigger(final override val id: String) : JarvisTrigger {

    private var callback: ((JSONObject) -> Unit)? = null
    private var listener: ((Intent) -> Unit)? = null

    protected abstract fun handle(intent: Intent): JSONObject?

    override fun start(cb: (JSONObject) -> Unit) {
        stop() // replacing the callback must not double-register
        callback = cb
        val l: (Intent) -> Unit = { intent ->
            val payload = try {
                handle(intent)
            } catch (t: Throwable) {
                Log.w(TAG, "$id failed to read ${intent.action}", t)
                null
            }
            if (payload != null) callback?.invoke(payload)
        }
        listener = l
        SystemEventBus.addListener(l)
    }

    override fun stop() {
        listener?.let { SystemEventBus.removeListener(it) }
        listener = null
        callback = null
    }

    /** Convenience for the common "fire with these fields" case. */
    protected fun payload(vararg pairs: Pair<String, Any?>): JSONObject {
        val o = JSONObject()
        for ((k, v) in pairs) if (v != null) o.put(k, v)
        return o
    }
}

// --- power ------------------------------------------------------------------

/** Charger plugged in. Also reports which kind, which is how "in the car" is guessed. */
class PowerConnectedTrigger : BroadcastTrigger(TriggerIds.POWER_CONNECTED) {
    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_POWER_CONNECTED) return null
        return payload("connected" to true)
    }
}

class PowerDisconnectedTrigger : BroadcastTrigger(TriggerIds.POWER_DISCONNECTED) {
    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_POWER_DISCONNECTED) return null
        return payload("connected" to false)
    }
}

/**
 * Battery crossing a threshold.
 *
 * `ACTION_BATTERY_CHANGED` fires constantly, so the crossing is detected by
 * [LevelThreshold] — pure logic, hysteresis included, so one crossing is one
 * event rather than forty.
 */
class BatteryLevelTrigger(
    threshold: Int = 20,
    direction: LevelThreshold.Direction = LevelThreshold.Direction.BELOW,
    hysteresis: Int = 3
) : BroadcastTrigger(TriggerIds.BATTERY_LEVEL) {

    private val gate = LevelThreshold(threshold, direction, hysteresis)

    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_BATTERY_CHANGED) return null
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        if (level < 0 || scale <= 0) return null
        val percent = (level * 100) / scale
        if (!gate.accept(percent)) return null

        val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        return payload(
            "level" to percent,
            "threshold" to gate.threshold,
            "direction" to gate.direction.name.lowercase(),
            "charging" to (
                status == BatteryManager.BATTERY_STATUS_CHARGING ||
                    status == BatteryManager.BATTERY_STATUS_FULL
                )
        )
    }

    override fun stop() {
        gate.reset()
        super.stop()
    }
}

// --- connectivity -----------------------------------------------------------

/**
 * Network transport changed: wifi, cellular, ethernet, vpn or none.
 *
 * Uses a `NetworkCallback` rather than the deprecated `CONNECTIVITY_CHANGE`
 * broadcast, which has not been deliverable to manifest receivers since
 * Android 7 and reports far less. The SSID comes from the capabilities'
 * `TransportInfo`; without `ACCESS_FINE_LOCATION` the platform redacts it, so
 * the field is simply absent rather than wrong.
 */
class ConnectivityTrigger(context: Context) : JarvisTrigger {

    override val id = TriggerIds.CONNECTIVITY_CHANGED
    override val requiredPermissions = listOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private val app = context.applicationContext
    private val cm = app.getSystemService(ConnectivityManager::class.java)
    private var callback: ((JSONObject) -> Unit)? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    /** Last transport we reported, so a capability churn is not four events. */
    private var lastTransport: String? = null

    override fun isAvailable(ctx: Context): Boolean = cm != null

    override val unavailableReason: String?
        get() = if (cm == null) "no ConnectivityManager on this device" else null

    override fun start(cb: (JSONObject) -> Unit) {
        stop()
        val manager = cm ?: return
        callback = cb

        val cbImpl = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                emit(caps)
            }

            override fun onLost(network: Network) {
                // Only report "none" once the last network is gone.
                if (activeTransport(manager) == null) emit(null)
            }
        }
        networkCallback = cbImpl

        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        runCatching { manager.registerNetworkCallback(request, cbImpl) }
            .onFailure {
                Log.w(TAG, "could not register the network callback", it)
                networkCallback = null
            }
    }

    override fun stop() {
        val manager = cm
        networkCallback?.let { cb ->
            if (manager != null) runCatching { manager.unregisterNetworkCallback(cb) }
        }
        networkCallback = null
        callback = null
        lastTransport = null
    }

    private fun emit(caps: NetworkCapabilities?) {
        val transport = caps?.let { transportName(it) } ?: "none"
        if (transport == lastTransport) return
        lastTransport = transport

        val out = JSONObject()
            .put("transport", transport)
            .put("connected", transport != "none")
        if (caps != null) {
            out.put("metered", !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED))
            out.put("vpn", caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN))
            ssidOf(caps)?.let { out.put("ssid", it) }
        }
        callback?.invoke(out)
    }

    private fun activeTransport(manager: ConnectivityManager): String? {
        val active = manager.activeNetwork ?: return null
        val caps = manager.getNetworkCapabilities(active) ?: return null
        return transportName(caps)
    }

    private fun transportName(caps: NetworkCapabilities): String = when {
        caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
        caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
        caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
        caps.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH) -> "bluetooth"
        caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
        else -> "other"
    }

    /**
     * SSID, when the platform is willing to tell us. It is redacted to
     * `<unknown ssid>` without location permission, which we translate to
     * "absent" rather than reporting a placeholder as if it were a network name.
     */
    private fun ssidOf(caps: NetworkCapabilities): String? {
        if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return null
        if (app.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return null
        }
        val info = caps.transportInfo as? WifiInfo ?: return null
        val ssid = info.ssid?.trim()?.trim('"').orEmpty()
        if (ssid.isEmpty() || ssid == "<unknown ssid>" || ssid == "0x") return null
        return ssid
    }
}

/** Airplane mode switched on or off. */
class AirplaneModeTrigger : BroadcastTrigger(TriggerIds.AIRPLANE_MODE) {
    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_AIRPLANE_MODE_CHANGED) return null
        return payload("enabled" to intent.getBooleanExtra("state", false))
    }
}

// --- audio routing ----------------------------------------------------------

/**
 * Wired headset in or out.
 *
 * `ACTION_HEADSET_PLUG` is sticky, so the first delivery after registering
 * describes the CURRENT state rather than a change. That first one is
 * swallowed: plugging the phone in should fire, opening the app should not.
 */
class HeadsetTrigger(private val plugged: Boolean) : BroadcastTrigger(
    if (plugged) TriggerIds.HEADSET_PLUGGED else TriggerIds.HEADSET_UNPLUGGED
) {
    private var seenFirst = false

    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_HEADSET_PLUG) return null
        val state = intent.getIntExtra("state", -1)
        if (state < 0) return null
        if (!seenFirst) {
            seenFirst = true
            return null // sticky replay of the current state, not a change
        }
        if ((state == 1) != plugged) return null
        return payload(
            "plugged" to plugged,
            "name" to intent.getStringExtra("name"),
            "microphone" to (intent.getIntExtra("microphone", 0) == 1)
        )
    }

    override fun stop() {
        seenFirst = false
        super.stop()
    }
}

/**
 * A Bluetooth device connected or disconnected — the car-stereo trigger.
 *
 * The device name needs `BLUETOOTH_CONNECT` on API 31+. Without it the platform
 * hands back nulls and a placeholder address, so the fields are reported as
 * absent instead of as facts.
 */
class BluetoothTrigger(private val connected: Boolean) : BroadcastTrigger(
    if (connected) TriggerIds.BLUETOOTH_CONNECTED else TriggerIds.BLUETOOTH_DISCONNECTED
) {
    override val requiredPermissions = listOf(Manifest.permission.BLUETOOTH_CONNECT)

    private val wanted = if (connected) {
        "android.bluetooth.device.action.ACL_CONNECTED"
    } else {
        "android.bluetooth.device.action.ACL_DISCONNECTED"
    }

    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != wanted) return null
        val device = deviceOf(intent)
        val out = payload("connected" to connected)
        if (device != null) {
            runCatching { device.name }.getOrNull()?.let { out.put("name", it) }
            runCatching { device.address }.getOrNull()
                ?.takeIf { it != PLACEHOLDER_ADDRESS }
                ?.let { out.put("address", it) }
        }
        return out
    }

    @Suppress("DEPRECATION")
    private fun deviceOf(intent: Intent): BluetoothDevice? =
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
        } else {
            intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
        }

    private companion object {
        /** What Android hands unprivileged apps instead of a real MAC. */
        const val PLACEHOLDER_ADDRESS = "02:00:00:00:00:00"
    }
}

// --- screen and session -----------------------------------------------------

/** Screen turned on or off. Registered-only broadcasts, so the service must be alive. */
class ScreenTrigger(private val on: Boolean) : BroadcastTrigger(
    if (on) TriggerIds.SCREEN_ON else TriggerIds.SCREEN_OFF
) {
    private val wanted = if (on) Intent.ACTION_SCREEN_ON else Intent.ACTION_SCREEN_OFF

    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != wanted) return null
        return payload("screen_on" to on)
    }
}

/** The user unlocked the device. The closest thing Android gives to "I am here". */
class UserPresentTrigger : BroadcastTrigger(TriggerIds.USER_PRESENT) {
    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_USER_PRESENT) return null
        return payload("unlocked" to true)
    }
}

// --- system state -----------------------------------------------------------

/** Ringer switched between normal, vibrate and silent. */
class RingerModeTrigger(context: Context) : BroadcastTrigger(TriggerIds.RINGER_MODE_CHANGED) {

    private val audio = context.applicationContext.getSystemService(AudioManager::class.java)
    private var last: Int? = null

    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != "android.media.RINGER_MODE_CHANGED") return null
        val mode = intent.getIntExtra("android.media.EXTRA_RINGER_MODE", audio?.ringerMode ?: -1)
        if (mode < 0 || mode == last) return null
        last = mode
        return payload("mode" to modeName(mode))
    }

    override fun stop() {
        last = null
        super.stop()
    }

    private fun modeName(mode: Int): String = when (mode) {
        AudioManager.RINGER_MODE_SILENT -> "silent"
        AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
        AudioManager.RINGER_MODE_NORMAL -> "normal"
        else -> "unknown"
    }
}

/** Timezone changed — travel, or a DST jump. Reschedules every time trigger. */
class TimezoneChangedTrigger : BroadcastTrigger(TriggerIds.TIMEZONE_CHANGED) {
    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_TIMEZONE_CHANGED) return null
        return payload("timezone" to java.util.TimeZone.getDefault().id)
    }
}

/**
 * Boot completed.
 *
 * Fired by [BootReceiver] through [SystemEventBus] rather than observed
 * directly, because by the time the service has started the real broadcast is
 * long gone.
 */
class BootCompletedTrigger : BroadcastTrigger(TriggerIds.BOOT_COMPLETED) {
    override fun handle(intent: Intent): JSONObject? {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != BootReceiver.ACTION_SYNTHETIC_BOOT
        ) {
            return null
        }
        return payload("boot" to true)
    }
}
