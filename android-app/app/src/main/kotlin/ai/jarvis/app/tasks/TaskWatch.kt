package ai.jarvis.app.tasks

import android.util.Log
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The one board every surface on the phone reads.
 *
 * The device channel is the only socket that is always up, so it is the only
 * thing that can know a research run started while the app was in somebody's
 * pocket. It feeds this; the overlay, the notification and the console tab
 * read it. Nothing here opens a socket or draws a view.
 *
 * A singleton for the same reason [ai.jarvis.app.ui.PromptPresence] is one:
 * the producer is a service and the consumers are a window, a notification and
 * an Activity, none of which can hold a reference to the others. Two boards
 * would mean the overlay and the notification disagreeing about what Jarvis is
 * doing, which is worse than either being slightly wrong.
 *
 * Access is synchronised because the producer is OkHttp's reader thread and the
 * consumers are the main thread. [TaskBoard] itself is a plain model and makes
 * no thread-safety claim.
 */
object TaskWatch {

    private const val TAG = "JarvisTasks"

    private val board = TaskBoard()
    private val lock = Any()
    private val listeners = CopyOnWriteArrayList<(List<TaskBoard.Row>) -> Unit>()

    /** What to draw right now: live work, plus what has just ended. */
    fun visible(): List<TaskBoard.Row> = synchronized(lock) { board.visible() }

    fun headline(): String = synchronized(lock) { board.headline() }

    /** When a surface should redraw because a finished task has aged out. */
    fun nextExpiryMs(): Long? = synchronized(lock) { board.nextExpiryMs() }

    val anyRunning: Boolean get() = synchronized(lock) { board.running }

    val anyWaiting: Boolean get() = synchronized(lock) { board.waiting }

    /**
     * One `event` frame off the channel.
     *
     * @return true if it was a task event, so the caller can stop looking.
     */
    fun onEvent(frame: JSONObject?): Boolean {
        val type = TaskFrames.eventTypeOf(frame)
        if (type !in TaskBoard.EVENTS) return false
        val row = TaskFrames.rowFromEvent(frame)
        if (row == null) {
            Log.d(TAG, "a $type event carried no usable task")
            return true
        }
        synchronized(lock) {
            if (type == TaskBoard.EVENT_REMOVED) board.remove(row.id) else board.upsert(row)
        }
        publish()
        return true
    }

    /** The one listing sent per connection, to catch work already under way. */
    fun onListing(result: JSONObject?) {
        val rows = TaskFrames.rowsFromList(result)
        synchronized(lock) { board.replaceAll(rows) }
        publish()
    }

    /**
     * The socket went away.
     *
     * The board is NOT cleared. What was running a second ago is still the best
     * information anybody has, and blanking the overlay on every wifi handover
     * would make it flicker rather than inform. The reconnect's listing
     * replaces it wholesale.
     */
    fun onDisconnected() = Unit

    /** Forget everything. For sign-out and for tests. */
    fun reset() {
        synchronized(lock) { board.clear() }
        publish()
    }

    fun listen(listener: (List<TaskBoard.Row>) -> Unit): () -> Unit {
        listeners.add(listener)
        listener(visible())
        return { listeners.remove(listener) }
    }

    private fun publish() {
        val rows = visible()
        for (listener in listeners) {
            try {
                listener(rows)
            } catch (t: Throwable) {
                // A surface that has gone away must never be able to break the
                // channel thread that is feeding it.
                Log.d(TAG, "a task listener raised; ignoring", t)
            }
        }
    }
}
