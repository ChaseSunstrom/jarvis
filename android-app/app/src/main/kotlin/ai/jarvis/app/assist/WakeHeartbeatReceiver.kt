package ai.jarvis.app.assist

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * The quarter-hourly "is it still listening?" check.
 *
 * Always-on listening had exactly one thing keeping it up — `START_STICKY` —
 * and on Android 12+ that is not enough: the restart the system performs after
 * killing the process is itself a *background* start of a microphone-typed
 * foreground service, which the platform is entitled to refuse. Nothing then
 * tried again until the user opened the app, which is why the switch said
 * "listening" while the phone was not.
 *
 * This is the second chance. It is deliberately dumb: call
 * [WakeWordService.ensureRunning] and let the policy decide. If listening is
 * off, the alarm cancels itself; if it is on and allowed, the service starts
 * (and starting an already-running service is a no-op that re-checks its own
 * preconditions); if it is on and refused, the user gets a notification they
 * can tap. There is no state here to get out of step.
 *
 * Not exported, and the alarm's `PendingIntent` is explicit and immutable, so
 * nothing outside this app can fire it.
 */
class WakeHeartbeatReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != ACTION_CHECK) return
        val route = WakeWordService.ensureRunning(context.applicationContext)
        Log.i(TAG, "heartbeat: $route")
    }

    companion object {
        private const val TAG = "JarvisWake"

        /** App-private and never broadcast to the system. */
        const val ACTION_CHECK = "ai.jarvis.app.WAKE_HEARTBEAT"
    }
}
