package ai.jarvis.app.companion

/**
 * PURE LOGIC — no Android imports, no I/O, no clock. The "exactly one answer
 * per `message_id`" rule, and nothing else.
 *
 * It matters because of what the server does with the answer: anything that is
 * not `answered` makes it escalate the message to the next device. So a second,
 * *different* reply for one id either sends a question the user already dealt
 * with to another device, or resurrects one they dismissed. And silence is just
 * as bad in the other direction — a message nobody replies to sits waiting out
 * its whole timeout before it moves on.
 *
 * The state machine, which `tools/presence_signals_test.py` mirrors:
 *
 * ```
 *                     admit()
 *   (unknown) ──────────────────► IN_FLIGHT ──── settle(status) ───► SETTLED
 *        ▲                            │                                 │
 *        └────── abandon() ───────────┘                    admit() ─────┤
 *          (cancelled: never answered,                     replays the  │
 *           so a redelivery may ask again)                 stored reply ┘
 * ```
 *
 * Three rules:
 *
 *  1. **A redelivery of a settled id replays the stored reply and prompts
 *     nothing.** The socket can die between our answer and the server's read of
 *     it, so the reply has to survive the id — the same idempotency
 *     [ai.jarvis.app.channel.CommandGate] gives `device_command`, for the same
 *     reason.
 *  2. **A redelivery of an in-flight id does nothing at all.** The question is
 *     already on screen; showing it twice would let one message collect two
 *     answers.
 *  3. **[settle] is the only way to answer, and it succeeds once.** Every later
 *     call for that id — a countdown that fires after a tap, an activity being
 *     destroyed after the user chose — is refused.
 */
class CompanionLedger(private val maxRemembered: Int = DEFAULT_MAX_REMEMBERED) {

    /** What [admit] decided about an arriving message. */
    sealed class Admission {
        /** Never seen: show it. */
        object Fresh : Admission()

        /** Already on screen: ignore this delivery entirely. */
        object InFlight : Admission()

        /** Already answered: re-send [reply] verbatim and prompt nothing. */
        data class Settled(val status: String, val reply: String) : Admission()
    }

    private data class Answer(val status: String, val reply: String)

    private val inFlight = LinkedHashSet<String>()

    /** Insertion-ordered, so trimming drops the oldest answer first. */
    private val settled = object : LinkedHashMap<String, Answer>(16, 0.75f, false) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Answer>?): Boolean =
            size > maxRemembered.coerceAtLeast(1)
    }

    /** Decide what to do with an arriving message. Never throws. */
    @Synchronized
    fun admit(messageId: String): Admission {
        val id = messageId.trim()
        if (id.isEmpty()) return Admission.InFlight
        settled[id]?.let { return Admission.Settled(it.status, it.reply) }
        if (!inFlight.add(id)) return Admission.InFlight
        return Admission.Fresh
    }

    /**
     * Record the one answer for [messageId]. Returns false when this id has
     * already been answered, in which case the caller must send nothing.
     */
    @Synchronized
    fun settle(messageId: String, status: String, reply: String): Boolean {
        val id = messageId.trim()
        if (id.isEmpty()) return false
        if (settled.containsKey(id)) return false
        inFlight.remove(id)
        settled[id] = Answer(status, reply)
        return true
    }

    /**
     * Forget an in-flight id without answering it.
     *
     * Used when delivery is cancelled rather than decided — the process is
     * shutting down, the socket went away mid-question. The server got no
     * answer, so a redelivery after reconnect is free to ask again; recording a
     * decision nobody made would be worse than saying nothing.
     */
    @Synchronized
    fun abandon(messageId: String) {
        inFlight.remove(messageId.trim())
    }

    /** The status already reported for [messageId], or null. */
    @Synchronized
    fun statusOf(messageId: String): String? = settled[messageId.trim()]?.status

    /** The stored reply for [messageId], or null. */
    @Synchronized
    fun replyFor(messageId: String): String? = settled[messageId.trim()]?.reply

    @Synchronized
    fun isInFlight(messageId: String): Boolean = messageId.trim() in inFlight

    @Synchronized
    fun clear() {
        inFlight.clear()
        settled.clear()
    }

    val inFlightCount: Int
        @Synchronized get() = inFlight.size

    val settledCount: Int
        @Synchronized get() = settled.size

    companion object {
        /**
         * How many answered ids to keep for replay. Bounded because the input
         * is a network socket: an unbounded map here is a memory leak a server
         * could drive.
         */
        const val DEFAULT_MAX_REMEMBERED = 256
    }
}
