package ai.jarvis.app.automation.notify

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Reads notifications so an automation can react to one. Nothing else.
 *
 * This service is the single most invasive grant in the app after
 * accessibility: with it, Jarvis sees every message, email and banking alert
 * on the phone. Four rules keep that proportionate, and all four are in this
 * file rather than in a document:
 *
 *  1. **Opt-in per package.** Nothing is reported unless a task named that
 *     package. [NotificationBus.allowedPackages] is recomputed from the enabled
 *     tasks; with no tasks, this service reads notifications and throws every
 *     one of them away. `"*"` opts into everything and is called out in
 *     `docs/automations.md` as exactly that.
 *  2. **Never our own.** Our package is filtered out, so a task that posts a
 *     notification cannot trigger itself, and a Tier-3 consent prompt — which
 *     is a notification — cannot start an automation.
 *  3. **Fenced.** Text goes through [NotificationFence] before it exists as
 *     anything else: control characters stripped, lengths capped, stamped
 *     `untrusted`.
 *  4. **Data, never instructions.** There is no code path from here to the
 *     action dispatcher. This class publishes to a bus; the bus reaches the
 *     trigger; the trigger is classified UNTRUSTED; and the task runner
 *     therefore dispatches every action of such a run as untrusted, which the
 *     policy engine can never auto-allow. The parcel-scam notification in
 *     [NotificationFence]'s comment gets a consent prompt at best.
 *
 * Removals are not reported at all. "A notification disappeared" is not a fact
 * a user asked to automate on, and it would double the volume of everything
 * above for no benefit.
 */
class JarvisNotificationListener : NotificationListenerService() {

    override fun onListenerConnected() {
        super.onListenerConnected()
        NotificationBus.connected = true
        Log.i(TAG, "notification access connected")
    }

    override fun onListenerDisconnected() {
        NotificationBus.connected = false
        Log.i(TAG, "notification access disconnected")
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val notification = sbn?.notification ?: return
        val pkg = sbn.packageName

        // Cheapest check first: most notifications on a phone are from apps no
        // task cares about, and the extras bundle is not free to read.
        if (!NotificationFence.isWanted(pkg, NotificationBus.allowedPackages)) return

        val extras = notification.extras
        val payload = NotificationFence.sanitize(
            packageName = pkg,
            title = extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString(),
            text = (
                extras?.getCharSequence(Notification.EXTRA_BIG_TEXT)
                    ?: extras?.getCharSequence(Notification.EXTRA_TEXT)
                )?.toString(),
            subText = extras?.getCharSequence(Notification.EXTRA_SUB_TEXT)?.toString(),
            category = notification.category,
            ongoing = (notification.flags and Notification.FLAG_ONGOING_EVENT) != 0,
            groupSummary = (notification.flags and Notification.FLAG_GROUP_SUMMARY) != 0,
            postedAtMs = sbn.postTime,
            ourPackage = packageName
        ) ?: return

        NotificationBus.publish(payload)
    }

    // onNotificationRemoved is deliberately not overridden. See the class docs.

    companion object {
        private const val TAG = "JarvisNotify"

        /** This service's component, for the settings deep link and `ActionEnv`. */
        fun component(context: Context): ComponentName =
            ComponentName(context.applicationContext, JarvisNotificationListener::class.java)

        /**
         * True when the user has granted notification access.
         *
         * Read from the Secure setting rather than trusting a flag we set
         * ourselves: the grant can be revoked in system settings while the
         * process is alive, and the service is not always told promptly.
         */
        fun isEnabled(context: Context): Boolean {
            val flat = runCatching {
                Settings.Secure.getString(
                    context.applicationContext.contentResolver,
                    "enabled_notification_listeners"
                )
            }.getOrNull() ?: return false
            val us = context.applicationContext.packageName
            return flat.split(':').any { entry ->
                ComponentName.unflattenFromString(entry.trim())?.packageName == us
            }
        }
    }
}

/**
 * Between the listener service and the trigger.
 *
 * A separate object because the two have different lifetimes: the system starts
 * and stops the listener service on its own schedule, while the trigger comes
 * and goes with [ai.jarvis.app.automation.JarvisAutomationService]. Neither
 * should hold a reference to the other.
 *
 * Carries plain maps rather than `StatusBarNotification`s — by the time
 * anything reaches here it has been through [NotificationFence] and there is no
 * way back to the original object, which is the point.
 */
object NotificationBus {

    private val listeners = CopyOnWriteArrayList<(Map<String, Any?>) -> Unit>()

    /** Set by the listener service. Purely informational. */
    @Volatile
    var connected: Boolean = false
        internal set

    /**
     * Packages any enabled task has asked about. Recomputed by
     * [ai.jarvis.app.automation.tasks.TaskEngine] whenever tasks change.
     *
     * Empty — the default — means nothing at all is reported. That is the
     * correct default for a service that can read every message on the phone.
     */
    @Volatile
    var allowedPackages: Set<String> = emptySet()
        private set

    /**
     * Not named `setAllowedPackages`: that is already the JVM name of the
     * property's own (private) setter, and the two would collide.
     */
    fun updateAllowedPackages(packages: Set<String>) {
        allowedPackages = packages
            .asSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .toSet()
    }

    fun addListener(listener: (Map<String, Any?>) -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: (Map<String, Any?>) -> Unit) {
        listeners.remove(listener)
    }

    fun clear() {
        listeners.clear()
        allowedPackages = emptySet()
    }

    internal fun publish(payload: Map<String, Any?>) {
        for (listener in listeners) {
            runCatching { listener(payload) }
                .onFailure { Log.w("JarvisNotify", "notification listener failed", it) }
        }
    }
}
