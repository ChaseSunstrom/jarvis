package ai.jarvis.app

import ai.jarvis.app.crash.JarvisCrashHandler
import android.app.Application
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Application entry point. Two jobs.
 *
 * First, install the crash handler — before anything else, including the
 * notification channels, because the crashes most worth catching are the ones
 * during startup and a handler installed after them catches nothing.
 *
 * Second, make sure the notification channels exist before anything tries to
 * post to them, in particular the approval channel: a Tier-3 request that
 * cannot be shown is a request that gets denied.
 *
 * Channel ids are public constants: the automation module posts its foreground
 * service notification and its alerts through the same three channels, so the
 * user gets one coherent set of switches in system settings instead of a pile
 * of near-duplicates.
 */
class JarvisApp : Application() {

    /**
     * False once the boot animation has played. It lives here, not in the
     * Activity, because "cold start" means *this process* started — a rotation,
     * a return from Settings or a resume from recents must not replay it.
     */
    private val coldStart = AtomicBoolean(true)

    /**
     * True exactly once per process, for the first Activity that asks. The
     * caller that gets `true` owns the boot animation.
     */
    fun consumeColdStart(): Boolean = coldStart.getAndSet(false)

    override fun onCreate() {
        // FIRST. Anything above this line is a crash nobody can diagnose.
        JarvisCrashHandler.install(this)
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

        /**
         * True the first time any Activity asks, in this process. Falls back to
         * false when the Application object is not ours (instrumentation, a
         * stripped test harness) — showing no boot animation is always safe,
         * showing one twice is not.
         */
        fun consumeColdStart(context: android.content.Context): Boolean =
            (context.applicationContext as? JarvisApp)?.consumeColdStart() ?: false

        /** Tier-3 consent requests. High importance, bypasses DND when allowed. */
        const val CHANNEL_APPROVAL = "jarvis_approval"

        /** The automation module's foreground-service notification. */
        const val CHANNEL_SERVICE = "jarvis_service"

        /** Anything Jarvis posts on the user's behalf. */
        const val CHANNEL_ALERTS = "jarvis_alerts"
    }
}
