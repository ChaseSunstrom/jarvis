package ai.jarvis.app.channel

/**
 * PURE LOGIC — no Android imports, no org.json, no coroutines.
 *
 * Admission control for inbound `device_command` frames. It answers one
 * question — "may this command start right now?" — and remembers enough to
 * answer the same command twice the same way.
 *
 * Four things it enforces:
 *
 *  1. **Exactly-once execution per `command_id`.** A reconnect can redeliver;
 *     a retrying server can duplicate. Neither may run `sms.send` twice. A
 *     repeat of a finished command replays the stored reply instead.
 *  2. **One in-flight command per action id.** Two `ui_type`s racing into the
 *     same text field is not a feature. The second is refused, not queued —
 *     queueing would let a flood build a backlog that outlives the flood.
 *  3. **A global concurrency cap.** Actions hold real resources (a mic, a
 *     camera, an accessibility pass) and each one can be sitting on a consent
 *     prompt for a minute.
 *  4. **Bounded memory.** The dedupe history is a fixed-size LRU. An attacker
 *     who can send unlimited distinct `command_id`s gets a bounded map, not an
 *     OOM.
 *
 * Every method is `@Synchronized`: this is called from the OkHttp reader thread
 * and released from whatever coroutine dispatcher ran the action.
 */
class CommandGate(
    /** How many actions may run at once, across all action ids. */
    val maxConcurrent: Int = DEFAULT_MAX_CONCURRENT,
    /** How many finished `command_id`s to remember for replay. */
    val historySize: Int = DEFAULT_HISTORY,
    /** Replies larger than this are remembered by status only. */
    val maxCachedReplyChars: Int = DEFAULT_MAX_CACHED_REPLY_CHARS
) {

    /** What the gate decided. Every branch has a defined reply on the wire. */
    sealed class Admission {
        /** Go. The caller MUST eventually call [complete] or [abandon]. */
        object Accepted : Admission()

        /** Already answered once. Send [reply] again; execute nothing. */
        data class AlreadyAnswered(val reply: String) : Admission()

        /** Same `command_id` is running right now. Send nothing; it will answer. */
        object StillRunning : Admission()

        /** Another command for the same action is in flight. */
        data class ActionBusy(val actionId: String) : Admission()

        /** Global cap reached. */
        data class AtCapacity(val running: Int) : Admission()

        /** `command_id` or `action` was missing/blank — unanswerable. */
        data class Malformed(val why: String) : Admission()
    }

    private val runningByCommand = HashMap<String, String>()   // command_id -> action_id
    private val runningActions = HashSet<String>()

    /** command_id -> the exact reply frame we sent. Bounded LRU, newest last. */
    private val answered = object : LinkedHashMap<String, String>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>): Boolean =
            size > historySize
    }

    @get:Synchronized
    val inFlight: Int get() = runningByCommand.size

    @Synchronized
    fun admit(commandId: String?, actionId: String?): Admission {
        val id = commandId?.trim().orEmpty()
        val action = actionId?.trim().orEmpty()
        if (id.isEmpty()) return Admission.Malformed("device_command without a command_id")
        if (action.isEmpty()) return Admission.Malformed("device_command without an action")

        answered[id]?.let { return Admission.AlreadyAnswered(it) }
        if (runningByCommand.containsKey(id)) return Admission.StillRunning
        if (runningActions.contains(action)) return Admission.ActionBusy(action)
        if (runningByCommand.size >= maxConcurrent) return Admission.AtCapacity(runningByCommand.size)

        runningByCommand[id] = action
        runningActions.add(action)
        return Admission.Accepted
    }

    /**
     * Record the reply that was sent and free the slots.
     *
     * [reply] is the serialised `device_result` frame. Oversized ones are
     * remembered as a stub: the dedupe guarantee (never execute twice) is what
     * matters, and holding a megabyte of screen text in memory to make a replay
     * byte-identical is a bad trade. A stub still carries the `command_id` and
     * the status, which is what a retrying server is asking about.
     */
    @Synchronized
    fun complete(commandId: String, reply: String, stubIfTooLarge: (String) -> String) {
        release(commandId)
        val id = commandId.trim()
        if (id.isEmpty()) return
        answered[id] = if (reply.length <= maxCachedReplyChars) reply else stubIfTooLarge(id)
    }

    /**
     * Free the slots WITHOUT remembering an answer — for a command whose
     * coroutine was cancelled by a shutdown, where the server got no reply and
     * a redelivery after reconnect should be allowed to run.
     */
    @Synchronized
    fun abandon(commandId: String) {
        release(commandId)
    }

    /** Drop in-flight bookkeeping on socket loss. History survives on purpose. */
    @Synchronized
    fun clearInFlight() {
        runningByCommand.clear()
        runningActions.clear()
    }

    /** Full reset, including the replay history. Only on an explicit stop. */
    @Synchronized
    fun clearAll() {
        clearInFlight()
        answered.clear()
    }

    private fun release(commandId: String) {
        val action = runningByCommand.remove(commandId.trim()) ?: return
        runningActions.remove(action)
    }

    companion object {
        const val DEFAULT_MAX_CONCURRENT = 4
        const val DEFAULT_HISTORY = 128
        const val DEFAULT_MAX_CACHED_REPLY_CHARS = 8 * 1024
    }
}
