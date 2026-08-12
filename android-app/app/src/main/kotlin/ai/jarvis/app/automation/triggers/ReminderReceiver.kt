package ai.jarvis.app.automation.triggers

import ai.jarvis.app.MainActivity
import ai.jarvis.app.R
import ai.jarvis.app.automation.ReminderStore
import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * Delivers a reminder, and arms the ones that are still to come.
 *
 * Two entry points, and they are deliberately the same class: arming and firing
 * share the store, the id scheme and the `PendingIntent` identity, and splitting
 * them is how a cancel stops matching the alarm it was meant to cancel.
 *
 *  * [ACTION_FIRE] — one reminder is due. Post it, drop it from the store.
 *  * [arm] / [cancel] / [rearmAll] — scheduling, called by the action and by
 *    [BootReceiver].
 *
 * `setExactAndAllowWhileIdle` for the same reason `ScheduleTrigger` uses it: it
 * is the only API that survives Doze at the accuracy a reminder needs. Without
 * `SCHEDULE_EXACT_ALARM` it degrades to `setAndAllowWhileIdle`, which the
 * system may delay by minutes — and the action says so in its result rather
 * than letting the reminder quietly drift.
 */
class ReminderReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_FIRE) return
        val app = context.applicationContext
        val id = intent.getStringExtra(EXTRA_ID) ?: return
        val store = ReminderStore(app)

        // Take it out of the store FIRST. A notification that fails to post is
        // a reminder the user missed once; a reminder left in the store after
        // firing is one that comes back at every reboot until it is deleted by
        // hand.
        val reminder = store.remove(id)
        val text = reminder?.text ?: intent.getStringExtra(EXTRA_TEXT).orEmpty()
        if (text.isBlank()) {
            Log.w(TAG, "reminder $id fired with nothing to say")
            return
        }
        post(app, id, text)
    }

    private fun post(app: Context, id: String, text: String) {
        val manager = app.getSystemService(NotificationManager::class.java) ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Reminders",
                    // HIGH, not DEFAULT: a reminder that does not interrupt is
                    // a reminder you read tomorrow with the rest of the tray.
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply { description = "Things you asked Jarvis to remind you about" }
            )
        }
        val open = PendingIntent.getActivity(
            app,
            id.hashCode(),
            Intent(app, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = Notification.Builder(app, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Reminder")
            .setContentText(text)
            // The text is the whole point and reminders are often a sentence,
            // so it must not be truncated to one line in the tray.
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .build()
        try {
            manager.notify(NOTIFICATION_TAG, id.hashCode(), notification)
        } catch (t: Throwable) {
            // POST_NOTIFICATIONS denied, or the channel blocked. Nothing to be
            // done at this point; the log is the only honest record.
            Log.w(TAG, "could not post reminder $id", t)
        }
    }

    companion object {
        private const val TAG = "JarvisReminders"
        private const val CHANNEL_ID = "jarvis_reminders"
        private const val NOTIFICATION_TAG = "jarvis-reminder"

        const val ACTION_FIRE = "ai.jarvis.app.automation.REMINDER_FIRE"
        const val EXTRA_ID = "ai.jarvis.app.automation.REMINDER_ID"
        const val EXTRA_TEXT = "ai.jarvis.app.automation.REMINDER_TEXT"

        /**
         * Schedule one reminder.
         *
         * @return true when the alarm was set exactly; false when it was set
         *   inexactly (no `SCHEDULE_EXACT_ALARM`) or could not be set at all.
         *   The caller tells the user which, because "remind me in five
         *   minutes" arriving in twenty is a different product.
         */
        fun arm(context: Context, reminder: ReminderStore.Reminder): Boolean {
            val app = context.applicationContext
            val alarms = app.getSystemService(AlarmManager::class.java) ?: return false
            val exact =
                if (Build.VERSION.SDK_INT >= 31) alarms.canScheduleExactAlarms() else true
            val pending = pendingIntent(app, reminder)
            return try {
                if (exact) {
                    alarms.setExactAndAllowWhileIdle(
                        AlarmManager.RTC_WAKEUP, reminder.dueAtMs, pending
                    )
                    true
                } else {
                    alarms.setAndAllowWhileIdle(
                        AlarmManager.RTC_WAKEUP, reminder.dueAtMs, pending
                    )
                    false
                }
            } catch (t: SecurityException) {
                // The grant can be revoked between the check and the call.
                Log.w(TAG, "exact alarm refused; falling back to inexact", t)
                runCatching {
                    alarms.setAndAllowWhileIdle(
                        AlarmManager.RTC_WAKEUP, reminder.dueAtMs, pending
                    )
                }
                false
            }
        }

        fun cancel(context: Context, reminder: ReminderStore.Reminder) {
            val app = context.applicationContext
            val alarms = app.getSystemService(AlarmManager::class.java) ?: return
            runCatching { alarms.cancel(pendingIntent(app, reminder)) }
        }

        /**
         * Put every still-future reminder back after a reboot.
         *
         * `AlarmManager` alarms do not survive a restart, so without this
         * "remind me tomorrow morning" is silently cancelled by a phone that
         * rebooted overnight.
         *
         * @return how many were re-armed.
         */
        fun rearmAll(context: Context): Int {
            val store = ReminderStore(context)
            store.prune()
            val pending = store.pending()
            pending.forEach { arm(context, it) }
            if (pending.isNotEmpty()) Log.i(TAG, "re-armed ${pending.size} reminder(s)")
            return pending.size
        }

        private fun pendingIntent(app: Context, reminder: ReminderStore.Reminder): PendingIntent {
            val intent = Intent(app, ReminderReceiver::class.java)
                .setAction(ACTION_FIRE)
                .putExtra(EXTRA_ID, reminder.id)
                // Carried as well as stored, so a reminder still says something
                // if the store is unreadable when it fires.
                .putExtra(EXTRA_TEXT, reminder.text)
            return PendingIntent.getBroadcast(
                app,
                reminder.id.hashCode(),
                intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }
    }
}
