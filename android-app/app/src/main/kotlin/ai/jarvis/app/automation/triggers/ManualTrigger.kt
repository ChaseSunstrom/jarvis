package ai.jarvis.app.automation.triggers

import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The one trigger the server may fire directly, and the one the user can tap in
 * the app: "run this now".
 *
 * TRUSTED — but that only means the run is not automatically degraded. Every
 * action inside it still goes through the local policy table, so a Tier-3 step
 * still asks, every time, with its real parameters. "The server asked for it"
 * has never been consent and is not consent here.
 */
class ManualTrigger : JarvisTrigger {

    override val id = TriggerIds.MANUAL

    private var callback: ((JSONObject) -> Unit)? = null

    override fun start(cb: (JSONObject) -> Unit) {
        callback = cb
        ManualTriggers.register(this)
    }

    override fun stop() {
        ManualTriggers.unregister(this)
        callback = null
    }

    internal fun fire(payload: JSONObject) {
        callback?.invoke(payload)
    }
}

/**
 * Registry for [ManualTrigger], so the WebSocket client and the UI can fire one
 * without holding a reference to the trigger instance — and so that firing one
 * after the automation service has stopped does nothing at all rather than
 * waking a half-torn-down engine.
 */
object ManualTriggers {

    private val instances = CopyOnWriteArrayList<ManualTrigger>()

    internal fun register(trigger: ManualTrigger) {
        instances.addIfAbsent(trigger)
    }

    internal fun unregister(trigger: ManualTrigger) {
        instances.remove(trigger)
    }

    internal fun clear() = instances.clear()

    /** True when a task is currently listening for manual triggers. */
    val isListening: Boolean get() = instances.isNotEmpty()

    /**
     * Fire the manual trigger.
     *
     * @param id names which manual trigger a task is watching for; tasks filter
     *   on it with `{"type": "manual", "id": "morning_brief"}`.
     * @param data extra values, available to the task as `{{trigger.*}}`.
     * @return true when something was listening.
     */
    fun fire(id: String, data: JSONObject? = null): Boolean {
        if (instances.isEmpty()) return false
        val payload = JSONObject()
        if (data != null) {
            val keys = data.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                payload.put(key, data.opt(key))
            }
        }
        // Written last so a payload cannot rename the trigger it is firing.
        payload.put("id", id)
        for (trigger in instances) runCatching { trigger.fire(payload) }
        return true
    }
}
