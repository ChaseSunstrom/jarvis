package ai.jarvis.app.config

/**
 * Pure-logic gate deciding whether always-on "Hey Jarvis" wake word
 * detection should be running right now. Third-party apps get no DSP
 * low-power hotword path on Android, so open-mic detection costs real
 * battery — this gate keeps it limited to situations where it pays off.
 *
 * Policy:
 *  - Headset mic in use       -> listen, regardless of hour or place.
 *  - Car Bluetooth connected  -> listen, regardless of hour (night drives).
 *  - At home                  -> listen only during waking hours.
 *  - None of those            -> never listen (pocket/away = wasted battery
 *                                and an open mic in public).
 *
 * The headset rule is the one that needs justifying, since it is the only one
 * that opens the mic in public. It is there because a worn earpiece inverts both
 * reasons the other rules are cautious:
 *
 *  * **Battery.** The expensive part of open-mic detection is the phone's
 *    application processor waking for every frame. Capture through a headset is
 *    already an active audio path the moment the user is wearing it for calls or
 *    music, so wake detection rides a stream that is running anyway instead of
 *    starting one.
 *  * **The open mic in public.** The objection to listening away from home is a
 *    phone in a pocket hearing a room nobody thinks is being heard. A headset is
 *    worn, visible, and deliberate — the user put it in their ear. That is a far
 *    clearer signal of intent than a geofence.
 *
 * It is still gated on the user's headset-mode opt-in upstream (see
 * `AudioRoute.capturesThroughHeadset`), so this rule can only fire for someone
 * who has explicitly turned headset capture on.
 *
 * Deliberately standalone: callers inject plain booleans (home-zone state from
 * the server, car BT from BluetoothProfile callbacks, headset capture from
 * [ai.jarvis.app.audio.AudioRoute]) and the current hour. No Android imports, so
 * the class is trivially unit-testable on the JVM.
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
     * @param headsetCapture Jarvis is capturing through a worn headset — i.e.
     *   [ai.jarvis.app.audio.AudioRoute.capturesThroughHeadset], which already
     *   requires the user's opt-in. Defaults to false so every existing caller
     *   keeps its current behaviour.
     */
    fun shouldListen(
        isHome: Boolean,
        carBtConnected: Boolean,
        hour: Int,
        headsetCapture: Boolean = false
    ): Boolean {
        require(hour in 0..23) { "hour must be 0..23" }
        if (headsetCapture) return true
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
