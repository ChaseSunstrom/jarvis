package ai.jarvis.app.automation.triggers

import android.content.Context
import ai.jarvis.app.automation.accessibility.ScreenChangeEvent
import ai.jarvis.app.automation.accessibility.ScreenEvents
import ai.jarvis.app.automation.policy.TrustLevel
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * THE ONE PLACE the trigger layer touches the accessibility module.
 *
 * The accessibility service is owned by another agent. It publishes
 * `accessibility.ScreenEvents` — a plain listener list of
 * [ScreenChangeEvent] — and its documentation names the trigger owner as the
 * intended consumer. That published object is the seam; nothing else in this
 * package imports from `accessibility`, so if it is ever renamed, one file
 * changes.
 *
 * ## What comes through it, and what does not
 *
 * A [ScreenChangeEvent] carries a package name, an activity class name, and a
 * timestamp. No window title, no text, no content description — by their
 * design as much as ours. That is what makes "when I open the maps app" a
 * usable trigger while keeping the contents of the banking app that was open a
 * moment ago out of the automation path entirely.
 *
 * A package name is assigned by the installer rather than by content, so it is
 * the one thing on the screen an attacker cannot write. Even so, WHICH app is
 * in front is influenced by whatever the user (or a malicious app) just opened,
 * so the trigger is still classified [TrustLevel.UNTRUSTED] — a task started
 * this way cannot auto-approve anything.
 */
object ForegroundAppEvents {

    private val listeners = CopyOnWriteArrayList<(String?, String?) -> Unit>()

    /** Registered with the accessibility module only while someone is listening. */
    private val bridge = ScreenEvents.Listener { event -> deliver(event) }

    @Volatile
    private var subscribed = false

    /**
     * The app currently in front, for the `app_foreground` condition.
     *
     * Null when the accessibility service is off or has not seen a window
     * change yet — and per [ai.jarvis.app.automation.tasks.ConditionEvaluator],
     * a condition over an unknown value is false, so "only when Maps is open"
     * does not run when we cannot tell.
     */
    val currentPackage: String?
        get() = runCatching { ScreenEvents.current().packageName }
            .getOrNull()
            ?.takeIf { it.isNotBlank() }

    val currentActivity: String?
        get() = runCatching { ScreenEvents.current().activity }.getOrNull()

    @Synchronized
    fun addListener(listener: (String?, String?) -> Unit) {
        listeners.add(listener)
        if (!subscribed) {
            ScreenEvents.addListener(bridge)
            subscribed = true
        }
    }

    @Synchronized
    fun removeListener(listener: (String?, String?) -> Unit) {
        listeners.remove(listener)
        if (listeners.isEmpty() && subscribed) {
            ScreenEvents.removeListener(bridge)
            subscribed = false
        }
    }

    @Synchronized
    fun clear() {
        listeners.clear()
        if (subscribed) {
            ScreenEvents.removeListener(bridge)
            subscribed = false
        }
    }

    private fun deliver(event: ScreenChangeEvent) {
        if (!event.isKnown) return
        for (listener in listeners) {
            runCatching { listener(event.packageName, event.activity) }
        }
    }
}

/**
 * Fires when the app in front changes.
 *
 * UNTRUSTED, and a task started by it can never auto-approve an action — see
 * [ai.jarvis.app.automation.tasks.TaskRunner]. Useful for "when I open the maps
 * app, read me my next appointment"; deliberately useless for anything that
 * types, taps, sends or spends without a human saying yes first.
 */
class ForegroundAppTrigger : JarvisTrigger {

    override val id = TriggerIds.APP_FOREGROUND
    override val trust = TrustLevel.UNTRUSTED

    private var listener: ((String?, String?) -> Unit)? = null

    /**
     * Always "available": the accessibility service may be switched on after
     * the triggers are built, and [ForegroundAppEvents] simply goes quiet until
     * it is. Reporting unavailable would make the trigger vanish from a task
     * that is otherwise fine, which is a worse failure than one that is silent
     * until the user grants the thing the settings screen is already nagging
     * them about.
     */
    override fun isAvailable(ctx: Context): Boolean = true

    override val unavailableReason: String?
        get() = "needs the Jarvis accessibility service to be enabled"

    override fun start(cb: (JSONObject) -> Unit) {
        stop()
        val l: (String?, String?) -> Unit = { pkg, cls ->
            if (pkg != null) {
                cb(
                    JSONObject()
                        .put("package", pkg)
                        .put("class", cls ?: JSONObject.NULL)
                        // Marked on the payload as well as on the trigger, so a
                        // server reading the device_event is told too.
                        .put("untrusted", true)
                )
            }
        }
        listener = l
        ForegroundAppEvents.addListener(l)
    }

    override fun stop() {
        listener?.let { ForegroundAppEvents.removeListener(it) }
        listener = null
    }
}
