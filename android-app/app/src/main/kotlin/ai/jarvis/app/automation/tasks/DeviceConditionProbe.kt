package ai.jarvis.app.automation.tasks

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiInfo
import android.os.BatteryManager
import android.os.PowerManager
import android.util.Log
import ai.jarvis.app.automation.triggers.ForegroundAppEvents
import ai.jarvis.app.automation.triggers.ScheduleCalculator
import java.time.ZoneId

/**
 * Fills a [ConditionContext] from the platform.
 *
 * The evaluator itself is pure and knows nothing about Android; this is the
 * one place that reads the device, and it is written so that every failure
 * produces a NULL rather than an exception or a plausible-looking default. A
 * null makes the condition evaluate to false, which is the safe direction: a
 * task guarded by "only when I am at home" must not run when the location is
 * unknown.
 *
 * Reading location deserves a note. It uses `getLastKnownLocation` only — it
 * never requests a fresh fix. A condition check happens on every matching
 * trigger event, and turning that into a GPS request would be both a battery
 * disaster and a privacy one. A stale fix that fails the freshness check is
 * reported as no fix at all.
 */
class DeviceConditionProbe(
    context: Context,
    private val zone: () -> ZoneId = { ZoneId.systemDefault() },
    private val now: () -> Long = System::currentTimeMillis
) : ConditionProbe {

    private val app = context.applicationContext

    override fun sample(): ConditionContext {
        val nowMs = now()
        val z = runCatching { zone() }.getOrDefault(ZoneId.systemDefault())
        val battery = readBattery()
        val network = readNetwork()
        val location = readLocation(nowMs)

        return ConditionContext(
            nowEpochMs = nowMs,
            minuteOfDay = ScheduleCalculator.minuteOfDay(nowMs, z),
            isoWeekday = ScheduleCalculator.weekdayOf(nowMs, z),
            batteryPercent = battery?.first,
            charging = battery?.second,
            networkTransport = network?.first,
            wifiSsid = network?.second,
            foregroundPackage = ForegroundAppEvents.currentPackage,
            screenOn = readScreenOn(),
            ringerMode = readRingerMode(),
            latitude = location?.first,
            longitude = location?.second,
            locationAccuracyM = location?.third
        )
    }

    /** (percent, charging) or null. */
    private fun readBattery(): Pair<Int, Boolean>? = try {
        // A null-receiver registerReceiver on a sticky broadcast is a read, not
        // a subscription: nothing to unregister, nothing leaked.
        val intent = app.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        if (level < 0 || scale <= 0) {
            null
        } else {
            // Safe call, not `!!`: `level >= 0` already implies the intent was
            // non-null, but that is not a smart cast the compiler can see.
            val status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            (level * 100 / scale) to (
                status == BatteryManager.BATTERY_STATUS_CHARGING ||
                    status == BatteryManager.BATTERY_STATUS_FULL
                )
        }
    } catch (t: Throwable) {
        Log.d(TAG, "battery unreadable", t)
        null
    }

    /** (transport, ssid) — transport is never null on success, ssid usually is. */
    private fun readNetwork(): Pair<String, String?>? = try {
        val cm = app.getSystemService(ConnectivityManager::class.java)
        val active = cm?.activeNetwork
        val caps = active?.let { cm.getNetworkCapabilities(it) }
        if (caps == null) {
            "none" to null
        } else {
            val transport = when {
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH) -> "bluetooth"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
                else -> "other"
            }
            transport to ssidOf(caps)
        }
    } catch (t: Throwable) {
        Log.d(TAG, "network unreadable", t)
        null
    }

    private fun ssidOf(caps: NetworkCapabilities): String? {
        if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return null
        if (!granted(Manifest.permission.ACCESS_FINE_LOCATION)) return null
        val info = caps.transportInfo as? WifiInfo ?: return null
        val ssid = info.ssid?.trim()?.trim('"').orEmpty()
        return ssid.takeIf { it.isNotEmpty() && it != "<unknown ssid>" && it != "0x" }
    }

    private fun readScreenOn(): Boolean? = try {
        app.getSystemService(PowerManager::class.java)?.isInteractive
    } catch (t: Throwable) {
        null
    }

    private fun readRingerMode(): String? = try {
        when (app.getSystemService(AudioManager::class.java)?.ringerMode) {
            AudioManager.RINGER_MODE_SILENT -> "silent"
            AudioManager.RINGER_MODE_VIBRATE -> "vibrate"
            AudioManager.RINGER_MODE_NORMAL -> "normal"
            else -> null
        }
    } catch (t: Throwable) {
        null
    }

    /** (lat, lon, accuracy) from the freshest cached fix, or null. */
    private fun readLocation(nowMs: Long): Triple<Double, Double, Double?>? {
        if (!granted(Manifest.permission.ACCESS_COARSE_LOCATION)) return null
        return try {
            val lm = app.getSystemService(LocationManager::class.java) ?: return null
            val providers = listOf(LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
            val best = providers
                .asSequence()
                .mapNotNull { provider ->
                    runCatching { lm.getLastKnownLocation(provider) }.getOrNull()
                }
                .maxByOrNull { it.time }
                ?: return null
            // A fix from an hour ago says where the phone was, not where it is.
            if (nowMs - best.time > MAX_FIX_AGE_MS) return null
            Triple(
                best.latitude,
                best.longitude,
                if (best.hasAccuracy()) best.accuracy.toDouble() else null
            )
        } catch (t: Throwable) {
            Log.d(TAG, "location unreadable", t)
            null
        }
    }

    private fun granted(permission: String): Boolean =
        app.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    private companion object {
        const val TAG = "JarvisTasks"

        /** Older than this and the fix is history, not a condition. */
        const val MAX_FIX_AGE_MS = 10 * 60_000L
    }
}
