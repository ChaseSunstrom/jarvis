package ai.jarvis.app.automation.triggers

import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * PURE LOGIC — no Android imports, no org.json, no I/O.
 *
 * GrapheneOS is degoogled, so there is no fused geofencing API and no
 * `GeofencingClient`. Jarvis polls a coarse fix and decides for itself whether
 * the phone is inside a circle. Mirrored by the executable spec at
 * `android-app/tools/geofence_test.py`:
 *
 *     python3 android-app/tools/geofence_test.py
 *
 * Three decisions carry the weight:
 *
 *  * **Hysteresis.** A fix that jitters across the boundary would otherwise
 *    fire "arrived home" / "left home" repeatedly. Entering needs
 *    `distance <= radius - h`, leaving needs `distance >= radius + h`, and
 *    inside that band the previous state survives untouched.
 *  * **Accuracy.** A 500 m network fix says nothing useful about a 100 m
 *    circle, so it is discarded rather than believed. A fix with no accuracy
 *    at all IS believed — refusing every such fix would mean never firing.
 *  * **The first fix is a baseline, not an arrival.** Rebooting inside the
 *    geofence must not announce that you have just got home.
 */
object GeofenceMath {

    /** WGS84 mean radius, in metres. */
    const val EARTH_RADIUS_M = 6_371_008.8

    /** Below this a hysteresis band would swallow the whole circle. */
    const val MIN_RADIUS_M = 10.0

    /** Roughly a city block: enough to absorb network-provider jitter. */
    const val DEFAULT_HYSTERESIS_M = 50.0

    /** Great-circle distance in metres. */
    fun haversineMeters(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val phi1 = Math.toRadians(lat1)
        val phi2 = Math.toRadians(lat2)
        val dPhi = Math.toRadians(lat2 - lat1)
        val dLambda = Math.toRadians(lon2 - lon1)
        val a = sin(dPhi / 2) * sin(dPhi / 2) +
            cos(phi1) * cos(phi2) * sin(dLambda / 2) * sin(dLambda / 2)
        // Clamping before asin keeps floating point from handing it a value
        // just above 1 for antipodal points, which would return NaN.
        return 2 * EARTH_RADIUS_M * asin(sqrt(a).coerceAtMost(1.0))
    }

    /**
     * Hysteresis clamped to half the radius, so the inner threshold can never
     * collapse to zero: a 100 m circle with 200 m of hysteresis would
     * otherwise need a fix at the exact centre to ever read "inside".
     */
    fun effectiveHysteresis(radiusM: Double, hysteresisM: Double): Double =
        if (hysteresisM <= 0.0) 0.0 else minOf(hysteresisM, radiusM / 2.0)

    /**
     * A fix is believed when its error is smaller than the circle it is being
     * tested against. Unknown accuracy (null, NaN or negative — all of which
     * `Location.getAccuracy()` can effectively produce) is believed.
     */
    fun isFixUsable(accuracyM: Double?, radiusM: Double): Boolean {
        if (accuracyM == null || accuracyM.isNaN() || accuracyM < 0.0) return true
        return accuracyM <= radiusM
    }

    /** State after this fix. Inside the hysteresis band, [previous] survives. */
    fun classify(
        distanceM: Double,
        radiusM: Double,
        hysteresisM: Double,
        previous: GeoState
    ): GeoState {
        val h = effectiveHysteresis(radiusM, hysteresisM)
        if (distanceM <= radiusM - h) return GeoState.INSIDE
        if (distanceM >= radiusM + h) return GeoState.OUTSIDE
        return previous
    }

    /**
     * The whole decision for one fix.
     *
     * A transition is reported only when the state actually changed AND there
     * was a state to change from: the first fix establishes where you are, it
     * does not claim you just arrived.
     */
    fun update(
        previous: GeoState,
        distanceM: Double,
        radiusM: Double,
        hysteresisM: Double = DEFAULT_HYSTERESIS_M,
        accuracyM: Double? = null
    ): GeofenceUpdate {
        // A circle that small is GPS noise, not a place: accepting it would
        // fire enter/exit continuously while the phone sits on a table.
        if (radiusM < MIN_RADIUS_M) return GeofenceUpdate(previous, null)
        if (!isFixUsable(accuracyM, radiusM)) return GeofenceUpdate(previous, null)

        val state = classify(distanceM, radiusM, hysteresisM, previous)
        if (state == previous || previous == GeoState.UNKNOWN) return GeofenceUpdate(state, null)
        return GeofenceUpdate(
            state,
            if (state == GeoState.INSIDE) GeoTransition.ENTER else GeoTransition.EXIT
        )
    }

    /** Convenience: distance from a fix to a circle's centre. */
    fun distanceTo(fix: GeoPoint, centre: GeoPoint): Double =
        haversineMeters(fix.latitude, fix.longitude, centre.latitude, centre.longitude)

    /** True when a coordinate pair is a coordinate pair. */
    fun isValidCoordinate(latitude: Double, longitude: Double): Boolean =
        !latitude.isNaN() && !longitude.isNaN() &&
            latitude in -90.0..90.0 && longitude in -180.0..180.0
}

/** Where the phone is relative to one circle. [UNKNOWN] until the first usable fix. */
enum class GeoState {
    UNKNOWN,
    INSIDE,
    OUTSIDE;

    companion object {
        fun fromStored(value: String?): GeoState = when (value?.trim()?.uppercase()) {
            "INSIDE" -> INSIDE
            "OUTSIDE" -> OUTSIDE
            else -> UNKNOWN
        }
    }
}

/** The event worth telling anyone about. */
enum class GeoTransition {
    ENTER,
    EXIT;

    /** Trigger id this transition fires. */
    val triggerId: String
        get() = if (this == ENTER) TriggerIds.GEOFENCE_ENTER else TriggerIds.GEOFENCE_EXIT
}

/** Result of one fix: the new state, and the transition if there was one. */
data class GeofenceUpdate(val state: GeoState, val transition: GeoTransition?)

/** A place, as configured by the user. Pure data. */
data class GeoPoint(val latitude: Double, val longitude: Double)

/**
 * One configured circle plus the state machine's memory of it. Immutable:
 * [withUpdate] returns the next fence, so the caller can keep a map of them
 * without worrying about which thread last touched one.
 */
data class Geofence(
    val id: String,
    val centre: GeoPoint,
    val radiusM: Double,
    val hysteresisM: Double = GeofenceMath.DEFAULT_HYSTERESIS_M,
    val state: GeoState = GeoState.UNKNOWN
) {
    fun evaluate(fix: GeoPoint, accuracyM: Double?): GeofenceUpdate =
        GeofenceMath.update(
            state,
            GeofenceMath.distanceTo(fix, centre),
            radiusM,
            hysteresisM,
            accuracyM
        )

    fun withUpdate(update: GeofenceUpdate): Geofence = copy(state = update.state)
}
