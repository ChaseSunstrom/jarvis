package ai.jarvis.app

import android.app.Application
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.util.Log

/**
 * Application entry point. Its whole job is to make sure the notification
 * channels exist before anything tries to post to them — in particular the
 * approval channel, because a Tier-3 request that cannot be shown is a request
 * that gets denied.
 *
 * Channel ids are public constants: the automation module posts its foreground
 * service notification and its alerts through the same three channels, so the
 * user gets one coherent set of switches in system settings instead of a pile
 * of near-duplicates.
 */
class JarvisApp : Application() {

    override fun onCreate() {
        super.onCreate()
        createChannels()
    }

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm == null) {
            Log.w(TAG, "no NotificationManager; channels not created")
            return
        }

        val approval = NotificationChannel(
            CHANNEL_APPROVAL,
            getString(R.string.notification_channel_approval),
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = getString(R.string.notification_channel_approval_desc)
            enableVibration(true)
            setShowBadge(true)
            // Parameters can name people and places; keep them off the lock
            // screen until the user is past the keyguard.
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            // Only takes effect once the user grants notification-policy
            // access. Harmless without it, and correct with it: a consent
            // request that Do Not Disturb swallows is a silent denial.
            setBypassDnd(true)
        }

        val service = NotificationChannel(
            CHANNEL_SERVICE,
            getString(R.string.notification_channel_service),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notification_channel_service_desc)
            setShowBadge(false)
            enableVibration(false)
            setSound(null, null)
        }

        val alerts = NotificationChannel(
            CHANNEL_ALERTS,
            getString(R.string.notification_channel_alerts),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = getString(R.string.notification_channel_alerts_desc)
            setShowBadge(true)
        }

        nm.createNotificationChannels(listOf(approval, service, alerts))
    }

    companion object {
        private const val TAG = "JarvisApp"

        /** Tier-3 consent requests. High importance, bypasses DND when allowed. */
        const val CHANNEL_APPROVAL = "jarvis_approval"

        /** The automation module's foreground-service notification. */
        const val CHANNEL_SERVICE = "jarvis_service"

        /** Anything Jarvis posts on the user's behalf. */
        const val CHANNEL_ALERTS = "jarvis_alerts"
    }
}
