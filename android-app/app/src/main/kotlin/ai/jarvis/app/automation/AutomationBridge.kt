package ai.jarvis.app.automation

import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The meeting point for four modules that must not import each other.
 *
 * ```
 *        triggers ─┐                         ┌─ jarvis-core (WebSocket)
 *                  ├─► AutomationBridge ◄────┤
 *   accessibility ─┤        (this file)      └─ channel/JarvisChannel
 *     task runner ─┘              │
 *                                 ▼
 *                         ActionDispatcher  ──►  ActionRegistry
 * ```
 *
 * Everything here is an interface or a `@Volatile` slot. This file contains no
 * behaviour worth attacking: it decides nothing, enforces nothing, and holds no
 * secret. All of the policy lives behind [ActionDispatcher] in
 * `automation/actions/ActionRegistry.kt` and `automation/policy/PolicyEngine.kt`.
 *
 * ## Who implements what
 *
 * | interface | implemented by | consumed by |
 * |---|---|---|
 * | [ActionDispatcher] | **actions agent** — a thin adapter over `ActionRegistry` (see below) | the channel, the task runner |
 * | [ChannelHandle] | **channel agent** — `ai.jarvis.app.channel.JarvisChannel` | triggers, task runner, settings UI |
 * | [UiAutomationStatus] | **accessibility agent** — the `AccessibilityService` | the channel, which advertises `ui_automation` only while it says yes |
 * | [DeviceEventSubscriber] | **channel agent** (forwards to the server) and **task runner agent** (matches tasks) | [publishEvent] |
 *
 * Nothing in this file starts anything. The app's foreground service
 * (`ai.jarvis.app.automation.JarvisAutomationService`, declared in the manifest
 * and owned by the automation module) is what constructs the registry, the
 * channel and the triggers, and fills these slots in.
 *
 * ## The adapter the actions agent registers
 *
 * `ActionRegistry` already speaks this protocol; it just has a richer
 * signature. The wiring is:
 *
 * ```kotlin
 * import ai.jarvis.app.automation.policy.ActionTier
 *
 * AutomationBridge.dispatcher = object : AutomationBridge.ActionDispatcher {
 *     override fun manifest(): JSONArray = registry.manifest()
 *     override fun capabilities(): List<String> = registry.capabilities()
 *
 *     override suspend fun dispatch(
 *         actionId: String,
 *         params: JSONObject,
 *         tier: String,
 *         reason: String
 *     ): JSONObject {
 *         // `tier` is already max(local-from-manifest, incoming) — the channel
 *         // did that. ActionRegistry then does it AGAIN against the real local
 *         // table, which is the authority. Two independent raises, no lowers.
 *         val result = registry.dispatch(
 *             actionId = actionId,
 *             params = params,
 *             requestedTier = ActionTier.fromName(tier),
 *             reason = reason,
 *             commandId = null
 *         )
 *         return JSONObject()
 *             .put("status", result.status.wire)
 *             .apply {
 *                 result.data?.let { put("result", it) }
 *                 result.error?.let { put("error", it) }
 *             }
 *     }
 * }
 * ```
 *
 * A dispatcher that wants the originating `command_id` — so its audit log can
 * join a `device_result` back to the frame that caused it — implements
 * [CommandAwareDispatcher] instead. `JarvisChannel` type-checks for it and
 * calls the five-argument overload when it is there. `ActionRegistry` already
 * takes a `commandId`, so that is the overload to wire.
 */
object AutomationBridge {

    private const val TAG = "JarvisBridge"

    // --- the action door ----------------------------------------------------

    /**
     * The single entry point to everything this device can do.
     *
     * There is exactly one implementation and it is `ActionRegistry`. Nothing
     * may reach an action except through here, because this is the door that
     * has the policy engine, the consent prompt and the audit log bolted to it.
     */
    interface ActionDispatcher {

        /**
         * Every action this build knows, as the array sent at registration.
         *
         * One object per action; the fields the channel reads are `id` (string)
         * and `tier` (int 1|2|3). `ActionRegistry.manifest()` also supplies
         * `description`, `params`, `capability`, `available`, `delegated`,
         * `requires_confirmation` and, where relevant, `unsupported` — the
         * server turns those into LLM tool definitions.
         *
         * The channel keeps `id -> tier` from this and uses it as its own local
         * tier table, so a `device_command` can be tier-checked on the socket
         * thread before it goes anywhere near an executor.
         */
        fun manifest(): JSONArray

        /**
         * Run one action and return a `device_result` **body**.
         *
         * Return shape — the channel copies these straight onto the wire and
         * adds `type` and `command_id`:
         *
         * ```json
         * {"status": "ok" | "denied" | "error" | "unsupported",
         *  "result": { ... },      // optional, on success
         *  "error":  "..."}        // optional, on failure
         * ```
         *
         * `status` is required. Anything else the channel coerces to `"error"`,
         * because an unparsable answer from the executor is a bug, and a bug
         * must not be reported to the server as success.
         *
         * @param tier `"AUTO"` | `"NOTIFY"` | `"CONFIRM"` — already raised by
         *   the channel to `max(local, incoming)`. Treat it as a request that
         *   may only make things stricter. The implementation MUST re-derive
         *   the tier from its own table and MUST NOT lower anything on the
         *   strength of this string.
         * @param reason the server's human-readable justification. **UNTRUSTED
         *   TEXT.** It is written by an LLM that may have read a hostile web
         *   page. Show it to the user, log it, and never parse a decision out
         *   of it.
         *
         * Implementations must not throw: return `{"status":"error"}`. The
         * channel enforces its own hard timeout regardless.
         */
        suspend fun dispatch(
            actionId: String,
            params: JSONObject,
            tier: String,
            reason: String
        ): JSONObject

        /**
         * Capability strings for `jarvis/device/register`, e.g.
         * `["ui_automation", "sms", "media"]`.
         *
         * Default derives them from [manifest] so a minimal implementation
         * (and a test double) does not have to keep two lists in step. Only
         * entries marked `available` count — advertising `sms` on a tablet with
         * no radio just teaches the model to ask for things that will fail.
         */
        fun capabilities(): List<String> {
            val out = sortedSetOf<String>()
            val arr = manifest()
            for (i in 0 until arr.length()) {
                val entry = arr.optJSONObject(i) ?: continue
                if (!entry.optBoolean("available", true)) continue
                if (entry.optBoolean("unsupported", false)) continue
                entry.optString("capability").takeIf { it.isNotEmpty() }?.let { out.add(it) }
            }
            return out.toList()
        }
    }

    /** Set once at startup by the automation module's foreground service. */
    @Volatile
    var dispatcher: ActionDispatcher? = null

    // --- the channel --------------------------------------------------------

    /**
     * What the rest of the app may ask of the command channel.
     *
     * Deliberately tiny, and deliberately one-way: nothing here lets a caller
     * send arbitrary frames to the server. Triggers report facts; they do not
     * get a socket.
     */
    interface ChannelHandle {
        /** Socket is open and authenticated. */
        val isConnected: Boolean

        /** Authenticated *and* `jarvis/device/register` was acknowledged. */
        val isRegistered: Boolean

        /** One line for the settings screen: state, host, last error. */
        fun describe(): String

        /**
         * Queue a `device_event` for the server.
         *
         * Returns false when this event was refused outright — a blank event
         * name, or the outbound rate limit is spent. It returns true when the
         * event went onto the socket **or** onto the offline queue; a full
         * offline queue drops the OLDEST entry to make room, so a true here is
         * "this one was accepted", not "nothing was lost".
         *
         * Events are lossy by design: a trigger that fired while the phone was
         * in a tunnel is stale by the time the tunnel ends, and a caller that
         * cannot lose an event should be a task, not an event.
         */
        fun sendEvent(event: String, data: JSONObject, untrusted: Boolean = false): Boolean

        /** Re-send the registration frame, e.g. after a capability change. */
        fun requestReregister()
    }

    @Volatile
    var channel: ChannelHandle? = null

    // --- accessibility ------------------------------------------------------

    /**
     * Just enough of the accessibility service for the channel to describe the
     * device honestly. Screen reading and tapping live behind
     * `automation.actions.UiAutomationDelegate`, which the accessibility agent
     * registers on `ActionEnv.uiDelegate`; this is the status half, kept
     * separate so the channel does not have to import the actions package.
     */
    interface UiAutomationStatus {
        /**
         * True when the service is enabled AND currently connected.
         *
         * Read by [ai.jarvis.app.channel.JarvisChannel], which folds it into
         * the capability list it registers with. That list is what the server
         * hands the model, so this is the difference between "the phone has UI
         * actions in its build" and "the phone can drive another app's screen
         * right now" — a switch in system settings that can change while the
         * socket is open.
         */
        fun isReady(): Boolean
    }

    @Volatile
    var uiAutomation: UiAutomationStatus? = null

    /**
     * Call after anything that changes what this device can do: the
     * accessibility service connecting or disconnecting, notification access
     * being granted, a runtime permission result, Shizuku binding.
     *
     * The capability list is a promise to the server about what the model may
     * ask for. A stale promise means the model plans around abilities the phone
     * no longer has.
     */
    fun onCapabilitiesChanged() {
        val handle = channel
        if (handle == null) {
            Log.d(TAG, "capabilities changed with no channel attached")
            return
        }
        handle.requestReregister()
    }

    // --- events -------------------------------------------------------------

    /**
     * A consumer of trigger events. There are two: the channel (which forwards
     * them to the server as `device_event`) and the task runner (which matches
     * them against local task definitions).
     */
    interface DeviceEventSubscriber {
        /**
         * @param untrusted true when the payload contains text written by
         *   somebody else — a notification body, screen content, a web
         *   response. Set structurally by the TRIGGER, never by the payload and
         *   never by the server. Maps to
         *   `automation.policy.TrustLevel.UNTRUSTED`, which the policy engine
         *   refuses to auto-allow.
         */
        fun onDeviceEvent(event: String, data: JSONObject, untrusted: Boolean)
    }

    private val subscribers = CopyOnWriteArrayList<DeviceEventSubscriber>()

    fun subscribe(subscriber: DeviceEventSubscriber) {
        if (!subscribers.contains(subscriber)) subscribers.add(subscriber)
    }

    fun unsubscribe(subscriber: DeviceEventSubscriber) {
        subscribers.remove(subscriber)
    }

    /**
     * Fan one trigger event out to every subscriber.
     *
     * A throwing subscriber is logged and skipped — one broken consumer must
     * not stop the others, and it certainly must not kill the trigger that
     * called this from a broadcast receiver.
     *
     * The automation module has its own, older seam for the same job —
     * `AutomationRuntime.deviceEvents`, filled by
     * `ai.jarvis.app.channel.DeviceLink`. **Use one or the other.** If the
     * service wires `DeviceLink`, start the channel with
     * `start(subscribeToBridgeEvents = false)`, or every event reaches the
     * server twice.
     *
     * This is a *report*, not a request. Publishing an event can never, on its
     * own, cause an action: the subscribers either serialise it onto a socket
     * or hand it to the task runner, and the task runner dispatches through
     * [ActionDispatcher] like everyone else — with the trust level attached, so
     * an untrusted event can at best produce a fresh consent prompt.
     */
    fun publishEvent(event: String, data: JSONObject, untrusted: Boolean = false) {
        for (subscriber in subscribers) {
            try {
                subscriber.onDeviceEvent(event, data, untrusted)
            } catch (t: Throwable) {
                Log.w(TAG, "event subscriber ${subscriber.javaClass.simpleName} threw on $event", t)
            }
        }
    }

    // --- convenience --------------------------------------------------------

    /**
     * Dispatch with a `command_id`, picking the richer overload when the
     * registered dispatcher supports it.
     *
     * For callers that are not the channel — the task runner, a UI tap — so
     * they do not each have to repeat the type check. Returns null when no
     * dispatcher is registered, which means automation is not running.
     */
    suspend fun dispatchCommand(
        actionId: String,
        params: JSONObject,
        tier: String,
        reason: String,
        commandId: String?
    ): JSONObject? {
        val target = dispatcher ?: return null
        val body = if (target is CommandAwareDispatcher) {
            target.dispatch(actionId, params, tier, reason, commandId)
        } else {
            target.dispatch(actionId, params, tier, reason)
        }
        return body
    }

    /** Opt-in extension for a dispatcher that wants the originating frame id. */
    interface CommandAwareDispatcher : ActionDispatcher {
        suspend fun dispatch(
            actionId: String,
            params: JSONObject,
            tier: String,
            reason: String,
            commandId: String?
        ): JSONObject
    }

    /** Wipe every slot. Used by the service's `onDestroy` and by tests. */
    fun reset() {
        dispatcher = null
        channel = null
        uiAutomation = null
        subscribers.clear()
    }
}
