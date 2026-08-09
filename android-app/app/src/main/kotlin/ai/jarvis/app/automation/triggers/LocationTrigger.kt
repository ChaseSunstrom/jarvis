package ai.jarvis.app.automation.triggers

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Looper
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

private const val TAG = "JarvisTriggers"

/**
 * Geofencing without Google Play Services.
 *
 * GrapheneOS is degoogled, so `GeofencingClient` does not exist. What does
 * exist is `LocationManager`, and the honest way to use it is to ask for coarse
 * updates and do the arithmetic ourselves — which is [GeofenceMath], pure and
 * mirrored by `tools/geofence_test.py`.
 *
 * `addProximityAlert` was considered and rejected: it needs fine location, its
 * behaviour under Doze is undocumented and inconsistent across OEMs, and it
 * gives no way to apply hysteresis, so a jittering fix produces a stream of
 * enter/exit alerts. Polling costs more battery and is predictable, and
 * predictable is what an automation needs.
 *
 * ## What this deliberately does not do
 *
 * It does not request high-accuracy GPS. A geofence of a few hundred metres
 * does not need it, and holding GPS open in the background is both a battery
 * problem and a privacy one. `NETWORK_PROVIDER` first, `GPS_PROVIDER` only if
 * that is all the device has.
 */
class GeofenceTrigger(
    context: Context,
    /** The circles to watch. Keyed by [Geofence.id]. */
    fences: List<Geofence>,
    /** ENTER or EXIT — one trigger instance per direction, as the ids differ. */
    private val direction: GeoTransition,
    private val minIntervalMs: Long = DEFAULT_MIN_INTERVAL_MS,
    private val minDistanceM: Float = DEFAULT_MIN_DISTANCE_M
) : JarvisTrigger {

    override val id: String = direction.triggerId

    override val requiredPermissions = listOf(
        Manifest.permission.ACCESS_COARSE_LOCATION,
        Manifest.permission.ACCESS_BACKGROUND_LOCATION
    )

    private val app = context.applicationContext
    private val lm = app.getSystemService(LocationManager::class.java)
    private var callback: ((JSONObject) -> Unit)? = null
    private var listener: LocationListener? = null

    /** Shared across both direction instances, so ENTER and EXIT agree. */
    private val states = GeofenceStates

    init {
        states.seed(fences)
    }

    override fun isAvailable(ctx: Context): Boolean =
        lm != null && ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    override val unavailableReason: String?
        get() = when {
            lm == null -> "no LocationManager on this device"
            app.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) !=
                PackageManager.PERMISSION_GRANTED ->
                "grant Jarvis location access, then \"Allow all the time\" for geofences to work " +
                    "while the screen is off"

            else -> null
        }

    override fun start(cb: (JSONObject) -> Unit) {
        stop()
        val manager = lm ?: return
        if (!isAvailable(app)) {
            Log.i(TAG, "geofence trigger not started: ${unavailableReason}")
            return
        }
        callback = cb

        // Written out rather than SAM-converted on purpose. `LocationListener`
        // only gained default methods in API 30; on API 29 all four are
        // abstract, and a lambda would compile here and then throw
        // AbstractMethodError on the oldest device this app supports.
        val l = object : LocationListener {
            override fun onLocationChanged(location: Location) = onFix(location)

            @Deprecated("Deprecated in the platform, still abstract on API 29")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit

            override fun onProviderEnabled(provider: String) = Unit

            override fun onProviderDisabled(provider: String) = Unit
        }
        listener = l

        val provider = pickProvider(manager)
        if (provider == null) {
            Log.w(TAG, "no enabled location provider; geofences are idle")
            return
        }
        runCatching {
            manager.requestLocationUpdates(provider, minIntervalMs, minDistanceM, l, Looper.getMainLooper())
        }.onFailure {
            Log.w(TAG, "could not request location updates", it)
            listener = null
        }

        // Seed from the last known fix so a restart does not sit blind until
        // the first update. It establishes state; per GeofenceMath it cannot
        // itself produce a transition.
        runCatching { manager.getLastKnownLocation(provider) }
            .getOrNull()
            ?.let { onFix(it) }
    }

    override fun stop() {
        val l = listener
        if (l != null) runCatching { lm?.removeUpdates(l) }
        listener = null
        callback = null
    }

    private fun pickProvider(manager: LocationManager): String? {
        val candidates = listOf(LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
        return candidates.firstOrNull { provider ->
            runCatching { manager.isProviderEnabled(provider) }.getOrDefault(false)
        }
    }

    private fun onFix(location: Location) {
        val point = GeoPoint(location.latitude, location.longitude)
        if (!GeofenceMath.isValidCoordinate(point.latitude, point.longitude)) return
        val accuracy = if (location.hasAccuracy()) location.accuracy.toDouble() else null

        for ((fence, transition) in states.onFix(point, accuracy)) {
            if (transition != direction) continue
            callback?.invoke(
                JSONObject()
                    .put("id", fence.id)
                    .put("transition", direction.name.lowercase())
                    .put("latitude", round(point.latitude))
                    .put("longitude", round(point.longitude))
                    .put("accuracy_m", accuracy ?: JSONObject.NULL)
                    .put("radius_m", fence.radiusM)
                    .put(
                        "distance_m",
                        round(GeofenceMath.distanceTo(point, fence.centre))
                    )
            )
        }
    }

    /** Five decimal places is about a metre. More would be false precision. */
    private fun round(value: Double): Double = Math.round(value * 100_000.0) / 100_000.0

    companion object {
        /** Two minutes: often enough to catch an arrival, rarely enough to be cheap. */
        const val DEFAULT_MIN_INTERVAL_MS = 120_000L

        /** Do not bother waking us for less than this much movement. */
        const val DEFAULT_MIN_DISTANCE_M = 100f
    }
}

/**
 * The shared inside/outside memory for every configured circle.
 *
 * It lives outside the trigger instances because ENTER and EXIT are two
 * triggers over one state machine: if each kept its own copy, a fix would be
 * evaluated twice and the second evaluation would see a state the first had
 * already advanced, producing an event on one side and silence on the other.
 */
object GeofenceStates {

    private val fences = ConcurrentHashMap<String, Geofence>()

    fun seed(incoming: List<Geofence>) {
        for (fence in incoming) {
            // Keep the remembered state when a fence is re-declared with the
            // same geometry; reset it when the geometry moved, because "inside"
            // no longer means the same thing.
            val existing = fences[fence.id]
            fences[fence.id] = if (
                existing != null &&
                existing.centre == fence.centre &&
                existing.radiusM == fence.radiusM
            ) {
                fence.copy(state = existing.state)
            } else {
                fence
            }
        }
    }

    fun replaceAll(incoming: List<Geofence>) {
        val keep = incoming.map { it.id }.toSet()
        fences.keys.retainAll(keep)
        seed(incoming)
    }

    fun clear() = fences.clear()

    fun snapshot(): List<Geofence> = fences.values.toList()

    /** Feed one fix to every circle; returns the transitions it produced. */
    fun onFix(point: GeoPoint, accuracyM: Double?): List<Pair<Geofence, GeoTransition>> {
        val out = ArrayList<Pair<Geofence, GeoTransition>>()
        for (fence in fences.values) {
            val update = fence.evaluate(point, accuracyM)
            val next = fence.withUpdate(update)
            fences[fence.id] = next
            update.transition?.let { out.add(next to it) }
        }
        return out
    }
}
