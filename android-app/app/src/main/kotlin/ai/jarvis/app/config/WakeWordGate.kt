package ai.jarvis.app.config

/**
 * Pure-logic gate deciding whether always-on "Hey Jarvis" wake word
 * detection should be running right now. Third-party apps get no DSP
 * low-power hotword path on Android, so open-mic detection costs real
 * battery — this gate keeps it limited to situations where it pays off.
 *
 * Policy:
 *  - Car Bluetooth connected  -> listen, regardless of hour (night drives).
 *  - At home                  -> listen only during waking hours.
 *  - Neither                  -> never listen (pocket/away = wasted battery
 *                                and an open mic in public).
 *
 * Deliberately standalone: callers inject plain booleans (home-zone state from
 * the server, car BT from BluetoothProfile callbacks) and the current hour. No
 * Android imports, so the class is trivially unit-testable on the JVM.
 */
class WakeWordGate(
    /** First hour (inclusive, 0-23) of the allowed listening window. */
    private val wakingHourStart: Int = DEFAULT_WAKING_HOUR_START,
    /** End hour (exclusive, 0-24) of the allowed listening window. */
    private val wakingHourEnd: Int = DEFAULT_WAKING_HOUR_END
) {

    init {
        require(wakingHourStart in 0..23) { "wakingHourStart must be 0..23" }
        require(wakingHourEnd in 0..24) { "wakingHourEnd must be 0..24" }
    }

    /**
     * @param isHome device is inside the "home" zone
     * @param carBtConnected phone is connected to the car's Bluetooth
     * @param hour current local hour of day, 0..23
     */
    fun shouldListen(isHome: Boolean, carBtConnected: Boolean, hour: Int): Boolean {
        require(hour in 0..23) { "hour must be 0..23" }
        if (carBtConnected) return true
        if (!isHome) return false
        return isWakingHour(hour)
    }

    /**
     * True if [hour] falls inside [wakingHourStart, wakingHourEnd).
     * Windows that wrap midnight (start > end, e.g. 22..6) are supported.
     */
    fun isWakingHour(hour: Int): Boolean {
        require(hour in 0..23) { "hour must be 0..23" }
        return if (wakingHourStart <= wakingHourEnd) {
            hour >= wakingHourStart && hour < wakingHourEnd
        } else {
            hour >= wakingHourStart || hour < wakingHourEnd
        }
    }

    companion object {
        /** Default window: 07:00-22:59 local time. */
        const val DEFAULT_WAKING_HOUR_START = 7
        const val DEFAULT_WAKING_HOUR_END = 23
    }
}
