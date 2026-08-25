package ai.jarvis.app.tasks

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.ManagementActivity
import ai.jarvis.app.R
import ai.jarvis.app.ui.ConsoleTab
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * The same progress, as a notification — the route that always works.
 *
 * [TaskOverlay] is the good surface and it has two hard limits: it needs
 * SYSTEM_ALERT_WINDOW, which is a Settings trip the user may never make, and
 * overlay windows are not drawn above the keyguard. Both cases are exactly when
 * somebody most wants to know that Jarvis is doing something — the phone is in
 * a pocket, locked. A notification has neither limit, and Android's own
 * progress notification is what users already read as "this is working".
 *
 * The two surfaces are deliberately not exclusive. Running both is how a
 * platform behaviour like "overlays are suppressed in this OEM's game mode"
 * degrades to something rather than to nothing.
 *
 * **The indeterminate case is the platform's own.** `setProgress(0, 0, true)`
 * is Android's indeterminate bar; `setProgress(100, 0, false)` is a bar at
 * zero. Using the second for a task whose progress is unknown is the same
 * mistake as `Number(null)` in the console and `optDouble` on the phone, and it
 * is the one that looks completely fine.
 */
object TaskNotifier {

    private const val TAG = "JarvisTaskNotify"
    private const val ID = 0x7A5C

    private var showing = false

    /** Draw the current board, or take the notification down. */
    fun render(context: Context, rows: List<TaskBoard.Row>, headline: String) {
        val app = context.applicationContext
        val nm = app.getSystemService(NotificationManager::class.java) ?: return
        val live = rows.firstOrNull { !it.finished } ?: rows.firstOrNull()
        if (live == null) {
            clear(context)
            return
        }

        val builder = Notification.Builder(app, JarvisApp.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle(live.title.ifEmpty { "Working" })
            .setContentText(live.says)
            .setOnlyAlertOnce(true)
            // Not `ongoing` for finished work: a task that has ended must be
            // dismissable, and an ongoing notification the user cannot swipe
            // away is how an app teaches people to turn its notifications off.
            .setOngoing(!live.finished)
            .setShowWhen(false)
            .setContentIntent(openTasks(app))

        if (headline.isNotEmpty()) builder.setSubText(headline)

        when (live.bar) {
            TaskBoard.Bar.DETERMINATE -> builder.setProgress(100, live.percent, false)
            // The platform's own indeterminate bar. A bar at zero would say
            // "nothing has happened yet" about a task that is working.
            TaskBoard.Bar.INDETERMINATE -> builder.setProgress(0, 0, true)
            TaskBoard.Bar.NONE -> builder.setProgress(0, 0, false)
        }

        if (rows.size > 1) {
            val style = Notification.InboxStyle()
            for (row in rows.take(INBOX_ROWS)) {
                style.addLine("${row.label}  ${row.title}")
            }
            builder.setStyle(style)
        }

        try {
            nm.notify(ID, builder.build())
            showing = true
        } catch (t: Throwable) {
            // Notifications can be refused: the permission is not granted, or
            // the channel is blocked. Neither is a reason to fail the work.
            Log.d(TAG, "could not post the task notification", t)
        }
    }

    fun clear(context: Context) {
        if (!showing) return
        showing = false
        try {
            context.applicationContext
                .getSystemService(NotificationManager::class.java)
                ?.cancel(ID)
        } catch (t: Throwable) {
            Log.d(TAG, "could not clear the task notification", t)
        }
    }

    private fun openTasks(app: Context): PendingIntent? = try {
        val intent = ManagementActivity.intent(app, ConsoleTab.WORK)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        // IMMUTABLE because nothing may rewrite where this lands: a mutable
        // PendingIntent handed to the notification shade is a handle any app
        // holding it could re-aim at another of ours.
        PendingIntent.getActivity(
            app, ID, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    } catch (t: Throwable) {
        Log.d(TAG, "no activity to open for the task notification", t)
        null
    }

    private const val INBOX_ROWS = 4
}
