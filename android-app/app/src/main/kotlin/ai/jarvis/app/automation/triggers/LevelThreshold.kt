package ai.jarvis.app.automation.triggers

/**
 * PURE LOGIC — no Android imports, no org.json.
 *
 * `ACTION_BATTERY_CHANGED` arrives every few seconds. A naive "fire when the
 * level is below 20" would fire dozens of times, and a level sitting exactly on
 * the line would flap between fire and not-fire as the reading wobbles.
 *
 * This is the same shape as [GeofenceMath]'s hysteresis, one dimension down:
 * the trigger arms only after the level has come back past the threshold by
 * [hysteresis], so one crossing produces exactly one event.
 */
class LevelThreshold(
    val threshold: Int,
    val direction: Direction,
    /** How far back past the threshold the level must go before re-arming. */
    val hysteresis: Int = 3
) {

    enum class Direction {
        /** Fire when the level falls to or below the threshold. */
        BELOW,

        /** Fire when the level rises to or above the threshold. */
        ABOVE;

        companion object {
            fun fromName(name: String?): Direction = when (name?.trim()?.lowercase()) {
                "above", "over", "up", "rising" -> ABOVE
                else -> BELOW
            }
        }
    }

    /** True once the level has crossed; cleared when it comes back. */
    private var fired = false

    /** Whether the first reading has been seen. */
    private var primed = false

    /**
     * Feed one reading. Returns true exactly on the crossing.
     *
     * The first reading only primes the gate: booting with the battery already
     * at 8% should not announce that it has just fallen below 20%. The state is
     * established, and the event comes on the next real crossing.
     */
    fun accept(level: Int): Boolean {
        val crossed = when (direction) {
            Direction.BELOW -> level <= threshold
            Direction.ABOVE -> level >= threshold
        }
        val rearmed = when (direction) {
            Direction.BELOW -> level >= threshold + hysteresis
            Direction.ABOVE -> level <= threshold - hysteresis
        }

        if (!primed) {
            primed = true
            fired = crossed
            return false
        }

        if (crossed && !fired) {
            fired = true
            return true
        }
        if (rearmed) fired = false
        return false
    }

    /** Forget everything, e.g. after the master switch is toggled back on. */
    fun reset() {
        fired = false
        primed = false
    }

    /** For the audit note and the settings screen. */
    override fun toString(): String =
        "level ${if (direction == Direction.BELOW) "<=" else ">="} $threshold (±$hysteresis)"
}
