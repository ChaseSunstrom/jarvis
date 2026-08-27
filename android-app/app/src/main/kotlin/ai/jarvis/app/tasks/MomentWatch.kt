package ai.jarvis.app.tasks

import android.util.Log
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The things Jarvis said while nobody was looking, on the phone.
 *
 * jarvis-core keeps a record of every proactive message —
 * `jarvis-core/jarvis/integrations/notifications/` — and fires
 * `jarvis_notification` as each one is made. The console draws them as an
 * inbox; this is the phone's half, and the phone is the surface that matters
 * most for them: a finished research run at three in the afternoon is not
 * something anybody was watching a screen for.
 *
 * Deliberately NOT part of [TaskWatch]. A task is work in progress, drawn as a
 * progress bar that comes and goes; a moment is a thing that already happened
 * and stays until it is read. Folding them into one board would mean one of
 * the two behaviours winning, and the loser being wrong.
 *
 * Nothing here draws a view or opens a socket: the device channel feeds it and
 * [MomentNotifier] renders it, the same split every other surface here uses.
 */
object MomentWatch {

    private const val TAG = "JarvisMoments"

    /** The bus event, matching `EVENT_NOTIFICATION` in the integration. */
    const val EVENT = "jarvis_notification"

    /** The listing command, for what arrived while the socket was down. */
    const val TYPE_LIST = "jarvis/notifications/list"

    /** How many are kept in memory. The record on the server is the archive. */
    private const val MAX_KEPT = 50

    data class Moment(
        val id: String,
        val kind: String,
        val title: String,
        val body: String,
        val at: Double,
        val read: Boolean,
        /** The bus event that produced it — "why am I seeing this", answered. */
        val source: String,
        val link: String,
    )

    private val lock = Any()
    private val moments = ArrayList<Moment>()
    private val listeners = CopyOnWriteArrayList<(List<Moment>) -> Unit>()

    fun visible(): List<Moment> = synchronized(lock) { moments.toList() }

    val unread: Int get() = synchronized(lock) { moments.count { !it.read } }

    /** Arguments for [TYPE_LIST] — not a whole frame; see [TaskFrames]. */
    fun listArgs(unreadOnly: Boolean = false): JSONObject =
        JSONObject().put("unread", unreadOnly).put("limit", MAX_KEPT)

    /**
     * One `event` frame off the channel.
     *
     * @return true if it was a notification event, so the caller can stop looking.
     */
    fun onEvent(frame: JSONObject?): Boolean {
        if (frame == null) return false
        val event = frame.optJSONObject("event") ?: return false
        if (event.optString("event_type") != EVENT) return false
        val moment = momentOf(event.optJSONObject("data")?.optJSONObject("notification"))
        if (moment == null) {
            Log.d(TAG, "a $EVENT event carried no usable record")
            return true
        }
        synchronized(lock) {
            moments.removeAll { it.id == moment.id }
            moments.add(0, moment)
            while (moments.size > MAX_KEPT) moments.removeAt(moments.size - 1)
        }
        publish()
        return true
    }

    /** The one listing per connection, for what arrived while it was down. */
    fun onListing(result: JSONObject?) {
        val array = result?.optJSONArray("notifications") ?: return
        val fresh = ArrayList<Moment>(array.length())
        for (index in 0 until array.length()) {
            momentOf(array.optJSONObject(index))?.let(fresh::add)
        }
        synchronized(lock) {
            moments.clear()
            moments.addAll(fresh.take(MAX_KEPT))
        }
        publish()
    }

    fun momentOf(raw: JSONObject?): Moment? {
        if (raw == null) return null
        val id = raw.optString("id").orEmpty()
        val title = raw.optString("title").orEmpty()
        if (id.isEmpty() || title.isEmpty()) return null
        return Moment(
            id = id,
            kind = raw.optString("kind").ifEmpty { "task" },
            title = title,
            body = raw.optString("body").orEmpty(),
            // `optDouble(key, 0.0)`, not `optDouble(key)`: the second answers
            // NaN for a missing key, and NaN through a date formatter is a
            // crash rather than a missing timestamp. Same trap TaskFrames
            // documents for `fraction`.
            at = raw.optDouble("at", 0.0),
            read = raw.optBoolean("read", false),
            source = raw.optString("source").orEmpty(),
            link = raw.optString("link").orEmpty(),
        )
    }

    /** Forget everything. For sign-out and for tests. */
    fun reset() {
        synchronized(lock) { moments.clear() }
        publish()
    }

    fun listen(listener: (List<Moment>) -> Unit): () -> Unit {
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
                Log.d(TAG, "a moment listener raised; ignoring", t)
            }
        }
    }
}
