package ai.jarvis.app.assist

import android.content.Context
import android.os.SystemClock
import android.util.Log

/**
 * The one conversation this device is in, wherever it is being had.
 *
 * ## The three state machines this replaces
 *
 * `docs/cross-device.md` promises: *"Messages carry a `conversation_id`. Answer
 * on your phone and the reply lands back in the same conversation the desktop
 * started — so 'yes' means the right thing without re-establishing context."*
 * On this phone there were three separate, unconnected memories of what
 * conversation was in progress, and a fourth place where one was thrown away:
 *
 *  * `AssistPipelineClient.conversationId` — a `private var` with no constructor
 *    parameter and no setter. It could learn an id from an `intent-end` event
 *    and could never be *given* one, so a conversation could not be seeded from
 *    anywhere. Every new client started from nothing.
 *  * `DeviceLink.conversationId` — the same idea again for `ask_jarvis`, kept
 *    privately by the automation link.
 *  * `CompanionProtocol.Message.conversationId` — parsed off the wire, put in
 *    the ask activity's intent as `EXTRA_CONVERSATION_ID`, and **read by
 *    nothing**. The documented cross-device thread reached the phone and was
 *    dropped on the floor.
 *
 * And locally it was worse than merely fragmented. `JarvisConversation
 * .speakToServer` builds a *second* `AssistPipelineClient` for the on-device
 * transcription path, so a turn this phone transcribed started a brand new
 * conversation — the voice turn before it was forgotten mid-sentence. Separately,
 * `WakeWordService` and `JarvisAssistActivity` each construct their own
 * `JarvisConversation`, so speaking to the wake orb and then opening the assist
 * card lost the thread on one device with one user.
 *
 * ## Why persisted
 *
 * Because the surfaces are separate processes' worth of lifecycle even when they
 * are not separate processes: a service, an activity started from a
 * notification, and an activity the system may kill between two sentences.
 * Anything held in memory is lost by exactly the transitions this is for.
 *
 * ## Why it expires
 *
 * A conversation id from this morning is not the conversation you are in. The
 * server keeps its own history and its own expiry; this is the phone's opinion
 * about whether continuing is still the right thing, and after [IDLE_TIMEOUT_MS]
 * of nobody saying anything it is not. Continuing a day-old thread is worse than
 * starting a new one — it hands the model context the user has forgotten
 * providing, which is how "yes" comes to mean something nobody meant.
 *
 * Stored in a file of its own rather than in `JarvisConfig`: this is session
 * state with a clock on it, not a preference. Keeping it out means "reset my
 * connection settings" and "which conversation am I in" cannot clear each other
 * by accident, and it keeps `JarvisConfig`'s settings surface — which
 * `no_empty_seams_test.py` audits key by key — free of a field no user ever sets.
 */
object ConversationRegistry {

    private const val TAG = "JarvisConversationId"
    private const val FILE = "jarvis_conversation"
    private const val KEY_ID = "conversation_id"
    private const val KEY_TOUCHED_AT = "touched_at"

    /**
     * How long a thread survives silence.
     *
     * Half an hour: long enough that answering a question you were notified
     * about over lunch continues the thread it came from, short enough that
     * tomorrow's first sentence is a fresh start.
     */
    const val IDLE_TIMEOUT_MS = 30 * 60 * 1000L

    /** Ids longer than this are not ids. Matches `CompanionProtocol.MAX_ID`. */
    private const val MAX_ID = 128

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /**
     * The conversation to continue, or null to start a fresh one.
     *
     * Wall clock rather than [SystemClock.elapsedRealtime]: the phone can be
     * rebooted between two turns, and a monotonic clock that restarted at zero
     * would make an ancient id look like it was touched a moment ago.
     */
    fun current(context: Context): String? {
        val store = prefs(context)
        val id = store.getString(KEY_ID, null)?.trim().orEmpty()
        if (id.isEmpty()) return null
        val touched = store.getLong(KEY_TOUCHED_AT, 0L)
        val age = System.currentTimeMillis() - touched
        // A negative age means the clock moved backwards (a manual change, an
        // NTP correction). Treat that as expired rather than as fresh: the
        // failure that matters is continuing something stale, not starting
        // something new.
        if (touched <= 0L || age < 0L || age > IDLE_TIMEOUT_MS) {
            Log.i(TAG, "the remembered conversation has gone stale; starting a new one")
            clear(context)
            return null
        }
        return id
    }

    /**
     * Remember [id], and mark the thread as active now.
     *
     * A blank or absent id is not a reason to forget the current one — the
     * server omits `conversation_id` from plenty of frames — so it is ignored.
     * [clear] is the deliberate way to end a thread, and it is spelled
     * differently precisely so that "this frame did not mention one" cannot be
     * mistaken for "this frame ended one".
     */
    fun remember(context: Context, id: String?) {
        val clean = id?.trim()?.take(MAX_ID).orEmpty()
        if (clean.isEmpty()) return
        prefs(context).edit()
            .putString(KEY_ID, clean)
            .putLong(KEY_TOUCHED_AT, System.currentTimeMillis())
            .apply()
    }

    /**
     * Keep the current thread alive without changing it.
     *
     * Called when a turn happens that produced no new id — a local
     * transcription that the server answered from the same conversation, an
     * answer sent back to a question. Without it a long exchange in which the
     * server stops repeating the id would expire mid-conversation.
     */
    fun touch(context: Context) {
        val store = prefs(context)
        if (store.getString(KEY_ID, null).isNullOrBlank()) return
        store.edit().putLong(KEY_TOUCHED_AT, System.currentTimeMillis()).apply()
    }

    /** Forget the thread. The next turn starts a new conversation. */
    fun clear(context: Context) {
        prefs(context).edit().remove(KEY_ID).remove(KEY_TOUCHED_AT).apply()
    }
}
