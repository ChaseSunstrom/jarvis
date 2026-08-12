package ai.jarvis.app.automation

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Reminders the user has asked for: a sentence, a time, and nothing else.
 *
 * ## Why this is not an alarm and not a calendar event
 *
 * The phone already had both, and neither is a reminder.
 *
 *  * `set_alarm` hands `AlarmClock.ACTION_SET_ALARM` to the clock app. It rings
 *    until dismissed, it lives in a list of alarms that are mostly recurring,
 *    and it carries a label rather than a sentence. "Remind me to take the
 *    bins out at six" is not something you want ringing.
 *  * `create_calendar_event` writes to the calendar provider, which means it is
 *    shared with everything that syncs that calendar and it shows up as an
 *    appointment. A reminder is not an appointment and does not belong to
 *    anyone else's view of your day.
 *
 * So a reminder is its own small thing: one notification, at one time, with the
 * words the user said, and then it is gone.
 *
 * ## Persistence, and why it has to exist
 *
 * `AlarmManager` alarms do not survive a reboot. Without a store, "remind me
 * tomorrow morning" is silently cancelled by a restart overnight — the failure
 * you would notice least and forgive least. So each reminder is written here
 * and [ai.jarvis.app.automation.triggers.BootReceiver] re-arms whatever is
 * still in the future.
 *
 * JSON in `SharedPreferences` rather than a database: this is a handful of rows
 * of a few hundred bytes each, read once at boot and once per change.
 */
class ReminderStore(context: Context) {

    private val prefs =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    data class Reminder(
        /** Stable id; also the `PendingIntent` identity, so it can be cancelled. */
        val id: String,
        val text: String,
        val dueAtMs: Long,
    ) {
        fun toJson(): JSONObject = JSONObject()
            .put("id", id)
            .put("text", text)
            .put("due_at_ms", dueAtMs)

        companion object {
            fun fromJson(o: JSONObject): Reminder? {
                val id = o.optString("id").takeIf { it.isNotEmpty() } ?: return null
                val text = o.optString("text").takeIf { it.isNotEmpty() } ?: return null
                val due = o.optLong("due_at_ms", 0L).takeIf { it > 0L } ?: return null
                return Reminder(id, text, due)
            }
        }
    }

    /** Everything still stored, soonest first. */
    fun all(): List<Reminder> {
        val raw = prefs.getString(KEY, null) ?: return emptyList()
        return try {
            val array = JSONArray(raw)
            (0 until array.length())
                .mapNotNull { index -> array.optJSONObject(index)?.let(Reminder::fromJson) }
                .sortedBy { it.dueAtMs }
        } catch (t: Throwable) {
            // A corrupt store must not make every reminder call fail forever.
            Log.w(TAG, "reminder store is unreadable; treating it as empty", t)
            emptyList()
        }
    }

    /** Those that have not fired yet, as of [nowMs]. */
    fun pending(nowMs: Long = System.currentTimeMillis()): List<Reminder> =
        all().filter { it.dueAtMs > nowMs }

    fun add(reminder: Reminder): Boolean {
        val current = all().filter { it.id != reminder.id }
        if (current.size >= MAX_REMINDERS) return false
        write(current + reminder)
        return true
    }

    fun remove(id: String): Reminder? {
        val current = all()
        val found = current.firstOrNull { it.id == id } ?: return null
        write(current.filter { it.id != id })
        return found
    }

    /**
     * Drop anything already due.
     *
     * Called after a reminder fires and when re-arming at boot. Without it the
     * store grows forever with reminders nobody will ever be shown — including
     * ones whose moment passed while the phone was off, which are the ones it
     * is most tempting to fire late. A reminder to leave for an appointment,
     * delivered after the appointment, is worse than none.
     */
    fun prune(nowMs: Long = System.currentTimeMillis()): Int {
        val current = all()
        val kept = current.filter { it.dueAtMs > nowMs }
        if (kept.size != current.size) write(kept)
        return current.size - kept.size
    }

    fun clear() = write(emptyList())

    private fun write(items: List<Reminder>) {
        val array = JSONArray()
        items.sortedBy { it.dueAtMs }.forEach { array.put(it.toJson()) }
        prefs.edit().putString(KEY, array.toString()).apply()
    }

    companion object {
        private const val TAG = "JarvisReminders"
        private const val FILE = "jarvis_reminders"
        private const val KEY = "reminders"

        /**
         * A ceiling, because the model can call `set_reminder` in a loop and
         * each one costs an `AlarmManager` slot. Well past what a person sets
         * by hand.
         */
        const val MAX_REMINDERS = 64
    }
}
