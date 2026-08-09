package ai.jarvis.app.automation.triggers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.util.Log
import java.util.concurrent.CopyOnWriteArrayList

// `SystemEventBus.DYNAMIC_ACTIONS` and `ACCEPTED_BROADCASTS` are declared at the
// bottom of this file; `SystemEventReceiver.onReceive` reads the second of them.

/**
 * The single `BroadcastReceiver` behind every system-event trigger.
 *
 * It is used two ways at once, which is the subtlety in this file:
 *
 *  * **Declared in `AndroidManifest.xml`**, so that a handful of broadcasts can
 *    wake the app from cold — `ACL_CONNECTED` when you get in the car,
 *    `POWER_CONNECTED` when you plug in.
 *  * **Registered dynamically** by `JarvisAutomationService`, because most of
 *    the interesting ones (`SCREEN_ON`, `USER_PRESENT`, `HEADSET_PLUG`,
 *    `BATTERY_CHANGED`, `AIRPLANE_MODE`) are registered-only or blocked from
 *    manifest delivery, and a manifest entry for them simply never fires.
 *
 * Those two overlap. `ACTION_POWER_CONNECTED` is both manifest-deliverable and
 * dynamically registered, so while the service is running the system delivers
 * it **twice** — once to each registration. That would run every matching task
 * twice. [dynamic] is how the two are told apart: the manifest instance stands
 * down whenever the live one is listening.
 *
 * The no-argument constructor the system needs comes free, because the only
 * parameter has a default.
 *
 * Nothing is decided here, and no action can be dispatched from here: this
 * class has no reference to the action registry, by construction.
 */
class SystemEventReceiver(
    /** True for the instance `JarvisAutomationService` registered itself. */
    private val dynamic: Boolean = false
) : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        // The manifest copy of this receiver is android:exported="true" — it has
        // to be, or the system will not deliver to it. An intent-filter only
        // constrains IMPLICIT intents, so any app on the phone can address this
        // component explicitly with an action of its choosing. The filter's own
        // actions are all protected broadcasts and the platform refuses to let a
        // third party send those, but nothing stops one sending
        // `ai.jarvis.app.automation.SYNTHETIC_BOOT` — an app-private action that
        // `BootCompletedTrigger` accepts — and forging a boot event runs whatever
        // the user wired to `boot_completed`, and parks an attacker-chosen intent
        // on the replay buffer while force-starting the service.
        //
        // So the broadcast door takes an allow-list, and the synthetic boot is
        // deliberately not on it: that one is only ever injected in-process, via
        // SystemEventBus.publish, by our own BootReceiver.
        if (action !in SystemEventBus.ACCEPTED_BROADCASTS) {
            Log.w(TAG, "ignoring unexpected broadcast: $action")
            return
        }
        val app = context.applicationContext
        if (dynamic) {
            SystemEventBus.deliverLive(intent)
        } else {
            SystemEventBus.deliverCold(app, intent)
        }
    }

    private companion object {
        const val TAG = "JarvisTriggers"
    }
}

/**
 * Fan-out from broadcasts to whichever [JarvisTrigger]s are currently started.
 *
 * The replay buffer solves a real cold-start problem: a manifest-declared
 * broadcast (`ACL_CONNECTED` when you get in the car) can start this app's
 * process, and the receiver runs long before the service has built its
 * triggers. Rather than lose that event, it is parked for a few seconds and
 * delivered as soon as a listener appears.
 */
object SystemEventBus {

    private const val TAG = "JarvisTriggers"

    /** Small and short-lived on purpose: this is a hand-off, not a queue. */
    private const val REPLAY_CAPACITY = 16
    private const val REPLAY_TTL_MS = 30_000L

    private val listeners = CopyOnWriteArrayList<(Intent) -> Unit>()
    private val replay = ArrayDeque<Parked>()

    private class Parked(val intent: Intent, val atUptimeMs: Long)

    /**
     * From the dynamically registered receiver: the service is up, so this is
     * the authoritative delivery.
     *
     * With no listeners it drops rather than parking. That only happens in the
     * millisecond-wide window between the receiver being registered and the
     * triggers being built, and parking here would race the manifest copy into
     * queueing the same broadcast twice. Losing one screen-on during startup is
     * the cheaper mistake.
     */
    fun deliverLive(intent: Intent) {
        deliver(intent)
    }

    /**
     * From the manifest-declared receiver.
     *
     * If anything is listening, the dynamic registration has this covered and
     * this copy is a duplicate — the system delivered the same broadcast to
     * both registrations. Dropping it here is what stops every task firing
     * twice while the service is running.
     *
     * If nothing is listening we are cold: park the intent and ask the service
     * to start, which drains it as soon as the triggers exist.
     */
    fun deliverCold(appContext: Context, intent: Intent) {
        if (listeners.isNotEmpty()) return
        park(intent)
        requestServiceStart(appContext)
    }

    /**
     * Inject an intent that did not come from the system — currently only
     * [BootReceiver]'s synthetic boot. Same cold/live handling.
     */
    fun publish(appContext: Context, intent: Intent) {
        if (listeners.isEmpty()) {
            park(intent)
            requestServiceStart(appContext)
            return
        }
        deliver(intent)
    }

    private fun deliver(intent: Intent) {
        for (listener in listeners) {
            // One misbehaving trigger must not stop the others from seeing the
            // event, and must never take the receiver down with it.
            runCatching { listener(intent) }
                .onFailure { Log.w(TAG, "trigger listener failed for ${intent.action}", it) }
        }
    }

    fun addListener(listener: (Intent) -> Unit) {
        listeners.add(listener)
        drainReplay(listener)
    }

    fun removeListener(listener: (Intent) -> Unit) {
        listeners.remove(listener)
    }

    /** Drop everything. Called when the service stops, so nothing lingers. */
    fun clear() {
        listeners.clear()
        synchronized(replay) { replay.clear() }
    }

    private fun park(intent: Intent) {
        synchronized(replay) {
            while (replay.size >= REPLAY_CAPACITY) replay.removeFirst()
            replay.addLast(Parked(Intent(intent), SystemClock.elapsedRealtime()))
        }
    }

    private fun drainReplay(listener: (Intent) -> Unit) {
        val now = SystemClock.elapsedRealtime()
        val pending: List<Intent> = synchronized(replay) {
            val fresh = replay.filter { now - it.atUptimeMs <= REPLAY_TTL_MS }.map { it.intent }
            replay.clear()
            fresh
        }
        for (intent in pending) {
            runCatching { listener(intent) }
                .onFailure { Log.w(TAG, "replay failed for ${intent.action}", it) }
        }
    }

    /**
     * Ask the automation service to come up so the parked event gets consumed.
     *
     * Deliberately best-effort. Android 12+ refuses a background
     * `startForegroundService` outside a handful of exemptions, and a refusal
     * here is not an error worth crashing over — it means this particular
     * broadcast cannot start an automation on this device, which is the
     * platform's call to make, not ours.
     */
    private fun requestServiceStart(appContext: Context) {
        runCatching { AutomationServiceStarter.start(appContext, "broadcast") }
            .onFailure { Log.d(TAG, "could not start the automation service from a broadcast", it) }
    }

    /**
     * The intents the dynamic registration covers.
     *
     * It deliberately includes actions the manifest also declares. The manifest
     * copy is there to wake a cold process; this one is there because a live
     * registration is the only way several of these are delivered at all. The
     * overlap is handled by [deliverCold] standing down, not by trimming this
     * list — trimming it would mean choosing, per action and per API level,
     * which delivery path the platform actually honours, and being wrong about
     * one of them is a trigger that silently never fires.
     */
    val DYNAMIC_ACTIONS: List<String> = listOf(
        Intent.ACTION_POWER_CONNECTED,
        Intent.ACTION_POWER_DISCONNECTED,
        Intent.ACTION_BATTERY_CHANGED,
        Intent.ACTION_BATTERY_LOW,
        Intent.ACTION_BATTERY_OKAY,
        Intent.ACTION_SCREEN_ON,
        Intent.ACTION_SCREEN_OFF,
        Intent.ACTION_USER_PRESENT,
        Intent.ACTION_AIRPLANE_MODE_CHANGED,
        Intent.ACTION_HEADSET_PLUG,
        Intent.ACTION_TIMEZONE_CHANGED,
        "android.media.RINGER_MODE_CHANGED",
        "android.media.AUDIO_BECOMING_NOISY",
        "android.bluetooth.device.action.ACL_CONNECTED",
        "android.bluetooth.device.action.ACL_DISCONNECTED"
    )

    /**
     * Everything [SystemEventReceiver] will accept off a real broadcast.
     *
     * [DYNAMIC_ACTIONS] plus the two the manifest declares that the dynamic
     * registration does not need. Every entry is a PROTECTED broadcast, which
     * means the platform itself refuses to deliver one sent by an ordinary app —
     * so an allow-list drawn from this set is also an "only the system may
     * originate these" check.
     *
     * `BootReceiver.ACTION_SYNTHETIC_BOOT` is deliberately absent. It is an
     * app-private action with no protection at all, and it reaches the bus
     * through `SystemEventBus.publish` from inside this process, never through a
     * broadcast we accept.
     */
    val ACCEPTED_BROADCASTS: Set<String> = buildSet {
        addAll(DYNAMIC_ACTIONS)
        add(Intent.ACTION_BOOT_COMPLETED)
        add("android.net.conn.CONNECTIVITY_CHANGE")
    }
}

/**
 * Starts the automation service.
 *
 * A one-function object rather than a call scattered through the package,
 * because "start the service" has a policy attached to it that is easy to get
 * wrong: on Android 12+ a background `startForegroundService` is refused
 * outside a short list of exemptions, and that refusal is the platform's
 * decision rather than an error. Having one place to fail quietly beats five
 * places that each throw.
 *
 * The service is named by class, not by string, so the compiler checks it. The
 * component is declared in `AndroidManifest.xml` by the app module.
 */
object AutomationServiceStarter {

    /** Extra naming who asked, for the service's own log line. */
    const val EXTRA_REASON = "ai.jarvis.app.automation.REASON"

    fun start(context: Context, reason: String) {
        val app = context.applicationContext
        val intent = Intent(app, ai.jarvis.app.automation.JarvisAutomationService::class.java)
            .putExtra(EXTRA_REASON, reason)
        // startForegroundService is refused from the background on Android 12+
        // outside a short list of exemptions. That refusal is the platform's
        // call, not an error: it means this broadcast cannot start an
        // automation on this device right now.
        runCatching { app.startForegroundService(intent) }
            .onFailure { runCatching { app.startService(intent) } }
    }
}
