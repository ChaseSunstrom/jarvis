package ai.jarvis.app.channel

import android.content.Context
import android.util.Log
import ai.jarvis.app.assist.ConversationRegistry
import ai.jarvis.app.automation.policy.TrustLevel
import ai.jarvis.app.automation.tasks.AskJarvisClient
import ai.jarvis.app.automation.tasks.DeviceEventSink
import ai.jarvis.app.automation.triggers.TriggerIds
import org.json.JSONArray
import org.json.JSONObject

/**
 * Plugs [JarvisChannel] into the automation module's seams.
 *
 * `AutomationRuntime` (in `automation/JarvisAutomationService.kt`) leaves two
 * slots for whoever owns the WebSocket, both defaulting to no-ops so the phone
 * runs its automations, enforces its policy and writes its audit log with no
 * server attached at all. This class fills them:
 *
 * ```kotlin
 * val link = DeviceLink(channel, appContext)
 * AutomationRuntime.deviceEvents = link
 * AutomationRuntime.askJarvis = link
 * channel.start(subscribeToBridgeEvents = false)   // ← note the false
 * ```
 *
 * **Wire one event path, not two.** [JarvisChannel.start] subscribes the
 * channel to `AutomationBridge.publishEvent` by default. If trigger events
 * reach the server through this class instead, pass
 * `subscribeToBridgeEvents = false`, or every event goes up twice.
 *
 * The adapter is deliberately thin, and lives in the channel package rather
 * than in the automation one: the dependency points from the socket towards the
 * automation module, never back, so nothing in `automation/` needs to know a
 * WebSocket exists.
 */
class DeviceLink(
    private val channel: JarvisChannel,
    /**
     * For [ConversationRegistry]. Application context — this object outlives
     * every screen.
     */
    private val context: Context,
) : DeviceEventSink, AskJarvisClient {

    override val isConnected: Boolean get() = channel.isRegistered

    /**
     * Forward one trigger event.
     *
     * The trust marker is derived from the trigger id via
     * [TriggerIds.trustFor] — the same classification the policy engine uses —
     * so a notification body still arrives at the server labelled as text a
     * stranger wrote, even though this interface has no trust parameter. The
     * classification stays in one place, and this path cannot silently
     * launder an untrusted payload into a trusted-looking one.
     */
    override fun sendEvent(event: String, data: Map<String, Any?>): Boolean {
        val untrusted = TriggerIds.trustFor(event) == TrustLevel.UNTRUSTED
        return channel.sendEvent(event, toJson(data), untrusted)
    }

    /**
     * Ask the server's conversation agent and return its text.
     *
     * ```json
     * {"id": 7, "type": "conversation/process", "text": "…", "conversation_id": "…"}
     * ```
     * ```json
     * {"id": 7, "type": "result", "success": true,
     *  "result": {"response": {"speech": {"plain": {"speech": "…"}}},
     *             "conversation_id": "…"}}
     * ```
     *
     * The reply is **LLM output**: untrusted text, possibly shaped by a web page
     * the model just read. It comes back as a String precisely so the caller
     * has to do something deliberate with it. It must not be parsed into an
     * action, an action id, or a set of parameters. The task runner treats a
     * run that consumed it as untrusted, which means nothing downstream can be
     * auto-allowed — see `automation/policy/PolicyEngine.kt`.
     *
     * Null on every failure: not connected, send failed, timed out, the server
     * refused, or no conversation agent configured.
     */
    override suspend fun ask(prompt: String, timeoutMs: Long): String? {
        val text = prompt.trim()
        if (text.isEmpty()) return null

        val payload = JSONObject().put("text", text)
        // THE SHARED THREAD, not a third private copy of one.
        //
        // This used to be a `@Volatile private var conversationId` of its own —
        // the second of three unconnected conversation-id state machines on this
        // device (the others being `AssistPipelineClient`'s and the companion
        // field nothing read). A task asking Jarvis something and the user
        // asking Jarvis something were two separate conversations on one phone,
        // which is exactly what `docs/cross-device.md` says does not happen.
        ConversationRegistry.current(context)?.let { payload.put("conversation_id", it) }

        val reply = channel.request(TYPE_CONVERSATION, payload, timeoutMs) ?: return null
        if (!ChannelFrames.isSuccess(reply)) {
            Log.w(TAG, "ask_jarvis refused: ${ChannelFrames.errorOf(reply)}")
            return null
        }
        val result = reply.optJSONObject("result") ?: return null
        result.optString("conversation_id").takeIf { it.isNotEmpty() }
            ?.let { ConversationRegistry.remember(context, it) }

        val speech = result.optJSONObject("response")
            ?.optJSONObject("speech")
            ?.optJSONObject("plain")
            ?.optString("speech")
            .orEmpty()
        return speech.takeIf { it.isNotEmpty() }
    }

    /** Forget the conversation thread, e.g. when the user clears history. */
    fun resetConversation() {
        ConversationRegistry.clear(context)
    }

    // --- Map -> JSON ---------------------------------------------------------

    private fun toJson(data: Map<String, Any?>): JSONObject {
        val out = JSONObject()
        for ((key, value) in data) {
            if (key.isEmpty()) continue
            try {
                out.put(key, jsonValue(value))
            } catch (t: Throwable) {
                // One unrepresentable field must not lose the whole event.
                Log.d(TAG, "dropping event field $key", t)
            }
        }
        return out
    }

    private fun jsonValue(value: Any?): Any = when (value) {
        null -> JSONObject.NULL
        is String, is Boolean, is Int, is Long, is Short, is Byte -> value
        // org.json rejects NaN and infinities outright; a stringified one is
        // better than a dropped field.
        is Double -> if (value.isFinite()) value else value.toString()
        is Float -> if (value.isFinite()) value.toDouble() else value.toString()
        is Map<*, *> -> JSONObject().also { obj ->
            for ((k, v) in value) {
                val name = k?.toString() ?: continue
                if (name.isNotEmpty()) obj.put(name, jsonValue(v))
            }
        }
        is Collection<*> -> JSONArray().also { arr -> value.forEach { arr.put(jsonValue(it)) } }
        is Array<*> -> JSONArray().also { arr -> value.forEach { arr.put(jsonValue(it)) } }
        is JSONObject, is JSONArray -> value
        else -> value.toString()
    }

    companion object {
        private const val TAG = "JarvisDeviceLink"

        /** jarvis-core's conversation entry point on the WebSocket API. */
        const val TYPE_CONVERSATION = "conversation/process"
    }
}
