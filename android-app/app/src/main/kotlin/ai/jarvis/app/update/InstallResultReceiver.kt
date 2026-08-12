package ai.jarvis.app.update

import ai.jarvis.app.JarvisApp
import ai.jarvis.app.R
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log

/**
 * Where the platform installer reports back — and, much more importantly, where
 * the install prompt is actually raised.
 *
 * ## The bug this closes
 *
 * `PackageInstaller.Session.commit` does not show anything. It sends a status
 * to the `IntentSender` it was given, and the first status for a normal
 * sideloaded update is [PackageInstaller.STATUS_PENDING_USER_ACTION], which
 * carries — in [Intent.EXTRA_INTENT] — the system's "do you want to install
 * this update?" activity. **Somebody has to start it.** Nothing did.
 *
 * `UpdateChecker` committed to a broadcast of `ai.jarvis.app.INSTALL_RESULT`
 * that had no receiver anywhere in the app, then returned "offered" and let
 * Settings print *"Ready to install — confirm the system prompt."* There was no
 * system prompt. The APK downloaded, the session committed, the status went
 * nowhere, and the in-app updater could not install anything, ever, on any
 * device. The same class of empty seam as `CompanionSpeechHost` — written,
 * documented, and never wired to the thing that makes it do something.
 *
 * ## Why a manifest receiver
 *
 * The install prompt can arrive long after Settings has been closed, and the
 * install can outlive the process that started it. A receiver registered in
 * code would be gone. The broadcast is sent with an explicit component so the
 * Android 8 implicit-broadcast restrictions never apply to it, and the receiver
 * is not exported, so nothing else can fabricate an install verdict.
 *
 * ## Why the notification fallback
 *
 * Starting an Activity from a broadcast receiver is a background activity
 * start, which Android 10+ refuses unless the app is in the foreground. That is
 * the common case here — the user tapped CHECK FOR UPDATES and then went to
 * make tea — so a refused start must leave something to tap rather than a
 * silence indistinguishable from the bug above.
 */
class InstallResultReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(
            PackageInstaller.EXTRA_STATUS,
            PackageInstaller.STATUS_FAILURE
        )
        val app = context.applicationContext

        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirm == null) {
                    Log.w(TAG, "pending user action with no intent to start")
                    notify(app, "Update could not be shown", RETRY_HINT)
                    return
                }
                // NEW_TASK because a receiver has no task of its own. The
                // installer activity belongs to the system, not to us.
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                try {
                    app.startActivity(confirm)
                } catch (t: Throwable) {
                    Log.w(TAG, "could not raise the install prompt; notifying instead", t)
                    notifyWith(app, confirm)
                }
            }

            PackageInstaller.STATUS_SUCCESS -> {
                // Rarely seen: installing over ourselves kills this process
                // first. Clearing is still right for the times it is not.
                Log.i(TAG, "update installed")
                cancel(app)
            }

            else -> {
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                Log.w(TAG, "install failed: status=$status message=$message")
                notify(app, "Update failed", explain(status, message))
            }
        }
    }

    /**
     * The one failure with a specific, non-obvious remedy, and the one worth
     * naming rather than printing a status code at somebody.
     *
     * Kept in step with `UpdateChecker.installFailureMessage`, which says the
     * same thing for the synchronous half.
     */
    private fun explain(status: Int, message: String?): String {
        val detail = message.orEmpty()
        if (detail.contains("INSTALL_FAILED_UPDATE_INCOMPATIBLE", ignoreCase = true) ||
            detail.contains("signatures do not match", ignoreCase = true)
        ) {
            return "This build was signed with a different key than the installed " +
                "app, so Android will not update in place. Uninstall Jarvis first, " +
                "then install this build."
        }
        if (status == PackageInstaller.STATUS_FAILURE_ABORTED) {
            return "The install was cancelled."
        }
        if (status == PackageInstaller.STATUS_FAILURE_STORAGE) {
            return "Not enough free space to install the update."
        }
        return detail.ifEmpty { RETRY_HINT }
    }

    private fun notifyWith(app: Context, confirm: Intent) {
        val pi = PendingIntent.getActivity(
            app,
            REQUEST_CODE,
            confirm,
            // MUTABLE: this is the system's own intent and it is not ours to
            // freeze. It is explicit — the installer named its component — so
            // the Android 14 ban on mutable *implicit* PendingIntents does not
            // apply.
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        notify(app, "Jarvis update ready", "Tap to install it.", pi)
    }

    private fun notify(
        app: Context,
        title: String,
        text: String,
        contentIntent: PendingIntent? = null,
    ) {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return
        val builder = Notification.Builder(app, JarvisApp.CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setCategory(Notification.CATEGORY_STATUS)
            .setAutoCancel(true)
        if (contentIntent != null) builder.setContentIntent(contentIntent)
        runCatching { nm.notify(NOTIFICATION_ID, builder.build()) }
            .onFailure { Log.w(TAG, "could not post the update notification", it) }
    }

    private fun cancel(app: Context) {
        runCatching {
            app.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID)
        }
    }

    private companion object {
        const val TAG = "JarvisUpdate"
        const val NOTIFICATION_ID = 6301
        const val REQUEST_CODE = 6302
        const val RETRY_HINT = "Try again from Settings, or install the APK from the releases page."
    }
}
