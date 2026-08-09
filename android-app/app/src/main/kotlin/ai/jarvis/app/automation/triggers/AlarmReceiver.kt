package ai.jarvis.app.automation.triggers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.concurrent.ConcurrentHashMap

/**
 * Receives this app's own `AlarmManager` `PendingIntent`s.
 *
 * `exported=false` in the manifest, so only this app can fire it. That matters:
 * a time trigger is the one trigger another app could otherwise forge, and a
 * forged fire would run whatever task the user wired to it.
 *
 * The receiver itself decides nothing. It looks up the alarm's owner in
 * [AlarmRouter] and hands over; if the owner has gone (the service stopped, the
 * task was deleted) the alarm is simply dropped. A stale alarm firing into
 * nothing is the correct outcome.
 */
class AlarmReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        val key = intent?.getStringExtra(EXTRA_ALARM_KEY) ?: return
        // Keep the process alive across the hand-off. This runs on the main
        // thread with the usual ~10 s receiver budget; everything downstream is
        // a post to a coroutine, so the budget is never the constraint.
        val handled = AlarmRouter.deliver(key, intent)
        if (!handled) {
            Log.d(TAG, "alarm $key fired with no owner; starting the service")
            AutomationServiceStarter.start(context.applicationContext, "alarm:$key")
            AlarmRouter.park(key, intent)
        }
    }

    companion object {
        private const val TAG = "JarvisAlarm"

        /** Identifies which schedule fired. Also the `PendingIntent` request id source. */
        const val EXTRA_ALARM_KEY = "ai.jarvis.app.automation.ALARM_KEY"

        /** Set when the alarm is the repeating leg of a sub-15-minute interval. */
        const val EXTRA_INTERVAL = "ai.jarvis.app.automation.ALARM_INTERVAL"
    }
}

/**
 * Where a fired alarm goes.
 *
 * A tiny registry rather than a static reference to the trigger, so a trigger
 * that has been stopped cannot be resurrected by an alarm the system had
 * already queued. Same cold-start parking as [SystemEventBus]: an alarm that
 * arrives before its owner exists waits briefly instead of being lost.
 */
object AlarmRouter {

    private const val PARK_TTL_MS = 60_000L

    private val owners = ConcurrentHashMap<String, (Intent) -> Unit>()
    private val parked = ConcurrentHashMap<String, Parked>()

    private class Parked(val intent: Intent, val atMs: Long)

    fun register(key: String, owner: (Intent) -> Unit) {
        owners[key] = owner
        // Deliver anything that fired while we were not yet listening.
        parked.remove(key)?.let { held ->
            if (System.currentTimeMillis() - held.atMs <= PARK_TTL_MS) {
                runCatching { owner(held.intent) }
            }
        }
    }

    fun unregister(key: String) {
        owners.remove(key)
    }

    fun clear() {
        owners.clear()
        parked.clear()
    }

    /** True when someone owned this alarm and took it. */
    fun deliver(key: String, intent: Intent): Boolean {
        val owner = owners[key] ?: return false
        return runCatching { owner(intent) }.isSuccess
    }

    fun park(key: String, intent: Intent) {
        parked[key] = Parked(Intent(intent), System.currentTimeMillis())
    }
}
