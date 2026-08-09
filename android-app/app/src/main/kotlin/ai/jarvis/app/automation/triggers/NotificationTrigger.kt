package ai.jarvis.app.automation.triggers

import android.content.Context
import ai.jarvis.app.automation.notify.JarvisNotificationListener
import ai.jarvis.app.automation.notify.NotificationBus
import ai.jarvis.app.automation.notify.NotificationDeduper
import ai.jarvis.app.automation.notify.NotificationFence
import ai.jarvis.app.automation.policy.TrustLevel
import org.json.JSONObject

/**
 * "When a notification arrives from …".
 *
 * Everything invasive about this trigger is handled before it: the listener
 * service filters by package and [NotificationFence] strips and caps the text.
 * What is left here is the dedupe — apps redraw a notification constantly, and
 * a task should run when a parcel arrives, not once per progress-bar update —
 * and the trust classification.
 *
 * ## The trust classification is the whole point
 *
 * [TrustLevel.UNTRUSTED] is not advisory. The task runner reads it and forces
 * every action dispatched during such a run to be untrusted; the policy engine
 * turns any ALLOW into an ASK for those. So a notification body can cause a
 * consent prompt to appear, and it can never cause anything to happen.
 *
 * That means the "when a package notification arrives, tell me" task in
 * `docs/automations.md` still works — `send_notification` is Tier 1, so the
 * prompt is the only cost — while "when a notification arrives, send an SMS to
 * the number in it" cannot be automated silently by anybody, including the
 * user. That asymmetry is deliberate.
 */
class NotificationPostedTrigger(
    private val deduper: NotificationDeduper = NotificationDeduper(),
    private val now: () -> Long = System::currentTimeMillis
) : JarvisTrigger {

    override val id = TriggerIds.NOTIFICATION_POSTED
    override val trust = TrustLevel.UNTRUSTED

    private var listener: ((Map<String, Any?>) -> Unit)? = null

    override fun isAvailable(ctx: Context): Boolean = JarvisNotificationListener.isEnabled(ctx)

    override val unavailableReason: String?
        get() = "grant Jarvis notification access in Settings > Notifications > " +
            "Device & app notifications > Notification access"

    override fun start(cb: (JSONObject) -> Unit) {
        stop()
        val l: (Map<String, Any?>) -> Unit = { payload ->
            if (deduper.accept(NotificationFence.dedupeKey(payload), now())) {
                cb(payload.toJson())
            }
        }
        listener = l
        NotificationBus.addListener(l)
    }

    override fun stop() {
        listener?.let { NotificationBus.removeListener(it) }
        listener = null
        deduper.clear()
    }

    private fun Map<String, Any?>.toJson(): JSONObject {
        val out = JSONObject()
        for ((k, v) in this) out.put(k, v ?: JSONObject.NULL)
        // Belt and braces: the fence sets this, and it is set again here so
        // that no refactor of the fence can quietly drop the label.
        out.put("untrusted", true)
        return out
    }
}
