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
     * The gate applied to what a phone can actually observe, plus the user's
     * two opt-ins. **This is the production entry point**; [shouldListen] is
     * the policy underneath it.
     *
     * ## Why this exists at all
     *
     * [shouldListen] takes `isHome: Boolean` — a fact — and for the life of this
     * app no caller could supply one, so the whole gate sat unwired: the policy
     * was written, unit-tested and described in `DEVIATIONS.md` as shipped
     * behaviour, and `shouldListen` had no production caller. That is the
     * `MediaButtonGate` shape exactly, and `no_empty_seams_test.py` carried four
     * settings in its exceptions list admitting it.
     *
     * The missing half was never the policy. It was that "am I at home" has
     * three answers on a phone, not two, and a `Boolean` cannot hold the third.
     *
     * ## The three answers, and what each one means here
     *
     *  * **yes / no** — this phone has a place signal and it is definite. The
     *    home rule applies as written.
     *  * **unknown** ([atHome] null) — nothing on this device can say. That is
     *    the ordinary case: a home signal exists only when the user has
     *    configured a geofence for it (see
     *    [ai.jarvis.app.assist.WakeListenWatch]), and most have not.
     *
     * An unknown is resolved as *at home*, deliberately, and it is the one
     * decision in this file worth arguing with. The alternative — resolving it
     * as away — reads "we cannot tell where you are, so stop listening", which
     * silences always-on detection everywhere except a car for every user who
     * has not drawn a circle on a map. That is not a battery policy, it is the
     * feature switched off. Resolving it as home instead means the *hour* window
     * still applies, which is the half of the rule this phone genuinely knows
     * the answer to: the clock is not in any doubt.
     *
     * @param atHome inside the home zone, or null when this device has no place
     *   signal at all.
     * @param listenAtHome the user's "while at home, during waking hours" switch.
     *   Off, the home rule cannot open the microphone whatever the place signal
     *   says, and only a car or a worn headset can.
     * @param listenInCar the user's "while car Bluetooth is connected" switch.
     */
    fun decide(
        atHome: Boolean?,
        carBtConnected: Boolean,
        headsetCapture: Boolean,
        hour: Int,
        listenAtHome: Boolean,
        listenInCar: Boolean,
    ): Decision {
        val car = carBtConnected && listenInCar
        val home = listenAtHome && (atHome ?: true)
        val listen = shouldListen(
            isHome = home,
            carBtConnected = car,
            hour = hour,
            headsetCapture = headsetCapture,
        )
        val reason = when {
            headsetCapture -> Reason.HEADSET
            car -> Reason.CAR
            carBtConnected -> Reason.CAR_RULE_OFF
            !listenAtHome -> Reason.HOME_RULE_OFF
            atHome == false -> Reason.AWAY
            listen -> Reason.WAKING_HOURS
            else -> Reason.QUIET_HOURS
        }
        return Decision(listen, reason)
    }

    /**
     * Why the gate said what it said.
     *
     * Carried rather than derived by the caller because the notification is the
     * only place a user ever sees this: a wake listener that has quietly gone
     * silent because it is 03:00 is indistinguishable from one that is broken,
     * and "Quiet until 07:00" is the difference between a policy and a bug.
     */
    data class Decision(val listen: Boolean, val reason: Reason) {

        /**
         * One line for the foreground notification. Present tense and specific:
         * the user is reading it while the thing it describes is happening.
         */
        fun explain(wakingHourStart: Int, wakingHourEnd: Int): String = when (reason) {
            Reason.HEADSET -> "Listening — your headset is in."
            Reason.CAR -> "Listening — connected to the car."
            Reason.CAR_RULE_OFF ->
                "Not listening in the car — that switch is off in Settings."
            Reason.HOME_RULE_OFF ->
                "Not listening — “while at home” is off, so only the car or a " +
                    "headset opens the microphone."
            Reason.AWAY -> "Not listening — you are away from home."
            Reason.WAKING_HOURS -> "Say “Hey Jarvis” at any time"
            Reason.QUIET_HOURS -> "Quiet until %02d:00 — these are your waking hours (%02d:00–%02d:00)."
                .format(wakingHourStart, wakingHourStart, wakingHourEnd % 24)
        }
    }

    enum class Reason {
        /** A worn headset with the user's capture opt-in. Beats everything. */
        HEADSET,

        /** Car Bluetooth, with the car switch on. */
        CAR,

        /** Car Bluetooth is connected but the user turned that rule off. */
        CAR_RULE_OFF,

        /** The "while at home" switch is off. */
        HOME_RULE_OFF,

        /** A place signal says this phone is not at home. */
        AWAY,

        /** Inside the waking-hours window. */
        WAKING_HOURS,

        /** Outside it. */
        QUIET_HOURS,
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
