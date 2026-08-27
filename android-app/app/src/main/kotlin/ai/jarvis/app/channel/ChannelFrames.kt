package ai.jarvis.app.channel

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * Every frame this device sends or accepts, in one file, so the wire format can
 * be reviewed against `android-app/docs/device-channel.md` without reading the
 * socket code.
 *
 * No Android imports (org.json only), so the shapes stay reviewable and the
 * builders could be exercised on a JVM with a real `org.json` on the classpath.
 *
 * Parsing rule for everything inbound: **read the fields we know, ignore the
 * rest, and never let an unknown field change behaviour.** A server that adds
 * `"skip_confirmation": true` or `"policy": "allow"` to a `device_command` is
 * describing a field this parser does not have and will not grow.
 */
object ChannelFrames {

    const val PLATFORM = "android"

    // Message types, outbound.
    const val TYPE_AUTH = "auth"
    const val TYPE_REGISTER = "jarvis/device/register"
    const val TYPE_DEVICE_RESULT = "device_result"
    const val TYPE_DEVICE_EVENT = "device_event"
    const val TYPE_PING = "ping"

    // Message types, inbound.
    const val TYPE_AUTH_REQUIRED = "auth_required"
    const val TYPE_AUTH_OK = "auth_ok"
    const val TYPE_AUTH_INVALID = "auth_invalid"
    const val TYPE_RESULT = "result"
    const val TYPE_PONG = "pong"
    const val TYPE_DEVICE_COMMAND = "device_command"
    /** A bus event this device subscribed to — see `subscribe_events`. */
    const val TYPE_EVENT = "event"

    // The four statuses. There is no fifth, and no "partial".
    const val STATUS_OK = "ok"
    const val STATUS_DENIED = "denied"
    const val STATUS_ERROR = "error"
    const val STATUS_UNSUPPORTED = "unsupported"

    private val VALID_STATUSES = setOf(STATUS_OK, STATUS_DENIED, STATUS_ERROR, STATUS_UNSUPPORTED)

    // --- outbound -----------------------------------------------------------

    /** `{"type":"auth","access_token":"..."}` — the only frame carrying the token. */
    fun auth(token: String): JSONObject = JSONObject()
        .put("type", TYPE_AUTH)
        .put("access_token", token)

    /**
     * ```json
     * {"id": 1, "type": "jarvis/device/register",
     *  "device": {"id": "...", "name": "Pixel 8", "platform": "android",
     *             "capabilities": ["ui_automation", "sms"],
     *             "app_version": "1.0.0"}}
     * ```
     *
     * [actions] is the action manifest. It rides inside `device` as an
     * ADDITIVE field: a server that does not know about it ignores an extra
     * dict key, whereas a separate message type would come back as
     * `unknown_command`. The five keys above are the contract; `actions` is how
     * the server learns what to build tools for without a second round trip.
     */
    fun register(
        requestId: Int,
        deviceId: String,
        deviceName: String,
        capabilities: List<String>,
        appVersion: String,
        actions: JSONArray? = null
    ): JSONObject {
        val device = JSONObject()
            .put("id", deviceId)
            .put("name", deviceName)
            .put("platform", PLATFORM)
            .put("capabilities", JSONArray(capabilities))
            .put("app_version", appVersion)
        if (actions != null) device.put("actions", actions)
        return JSONObject()
            .put("id", requestId)
            .put("type", TYPE_REGISTER)
            .put("device", device)
    }

    /**
     * ```json
     * {"type":"device_result","command_id":"c-123","status":"ok","result":{...}}
     * ```
     *
     * [body] is what [ai.jarvis.app.automation.AutomationBridge.ActionDispatcher]
     * returned. Only `status`, `result` and `error` are copied across, and an
     * unrecognised status becomes `error` — the executor does not get to invent
     * wire vocabulary, and a garbled answer must never read as success.
     */
    fun deviceResult(commandId: String, body: JSONObject): JSONObject {
        // Locale.ROOT so a Turkish-locale phone cannot fold "DENIED" into
        // "denıed" and report a refusal to the server as a generic error.
        val status = body.optString("status").trim().lowercase(Locale.ROOT)
            .takeIf { it in VALID_STATUSES }
        val out = JSONObject()
            .put("type", TYPE_DEVICE_RESULT)
            .put("command_id", commandId)
            .put("status", status ?: STATUS_ERROR)
        body.optJSONObject("result")?.let { out.put("result", it) }
        val error = body.optString("error").takeIf { it.isNotEmpty() }
        when {
            error != null -> out.put("error", error)
            status == null -> out.put(
                "error",
                "the device produced a result with no recognised status; treating it as an error"
            )
        }
        return out
    }

    /** Terminal reply built by the channel itself (refusals, timeouts, floods). */
    fun deviceResult(commandId: String, status: String, error: String? = null): JSONObject {
        val out = JSONObject()
            .put("type", TYPE_DEVICE_RESULT)
            .put("command_id", commandId)
            .put("status", if (status in VALID_STATUSES) status else STATUS_ERROR)
        error?.let { out.put("error", it) }
        return out
    }

    /**
     * ```json
     * {"type":"device_event","event":"geofence_enter","data":{"id":"home"}}
     * ```
     *
     * `trust` is added only for untrusted sources (notification bodies, screen
     * text). Additive and ignorable, but worth sending: the server should know
     * which of its inputs were written by a stranger before it feeds them to a
     * model.
     */
    fun deviceEvent(event: String, data: JSONObject, untrusted: Boolean = false): JSONObject {
        val out = JSONObject()
            .put("type", TYPE_DEVICE_EVENT)
            .put("event", event)
            .put("data", data)
        if (untrusted) out.put("trust", "untrusted")
        return out
    }

    /** `{"id":N,"type":"ping"}` — jarvis-core answers `{"id":N,"type":"pong"}`. */
    fun ping(requestId: Int): JSONObject = JSONObject()
        .put("id", requestId)
        .put("type", TYPE_PING)

    // --- inbound ------------------------------------------------------------

    /**
     * A `device_command` after parsing. Note what is NOT here: no policy field,
     * no "remember this", no timeout override, no trust level. Those are the
     * device's business, and a struct with no slot for them cannot be talked
     * into one.
     */
    data class Command(
        val commandId: String,
        val action: String,
        val params: JSONObject,
        /** Raw wire tier. Advisory. Folded in through [TierGuard.effective] only. */
        val requestedTier: WireTier?,
        /** Human-readable why, shown verbatim in the consent prompt. UNTRUSTED. */
        val reason: String
    )

    /** Null when the frame is not a usable `device_command`. */
    fun parseCommand(msg: JSONObject): Command? {
        if (msg.optString("type") != TYPE_DEVICE_COMMAND) return null
        val commandId = msg.optString("command_id").trim()
        val action = msg.optString("action").trim()
        if (commandId.isEmpty() || action.isEmpty()) return null
        return Command(
            commandId = commandId,
            action = action,
            params = msg.optJSONObject("params") ?: JSONObject(),
            requestedTier = TierGuard.parse(if (msg.isNull("tier")) null else msg.opt("tier")),
            reason = msg.optString("reason").trim().ifEmpty { "(the server gave no reason)" }
        )
    }

    /**
     * `id -> tier` from a manifest, for the channel's own tier table.
     *
     * A malformed or missing `tier` maps to [WireTier.CONFIRM]: the action
     * exists, we just cannot tell how dangerous it is, so it is treated as the
     * most dangerous. [TierGuard.forAction] already does that for ids that are
     * absent entirely.
     */
    fun tierTable(manifest: JSONArray?): Map<String, WireTier> {
        if (manifest == null) return emptyMap()
        val out = LinkedHashMap<String, WireTier>(manifest.length())
        for (i in 0 until manifest.length()) {
            val entry = manifest.optJSONObject(i) ?: continue
            val id = entry.optString("id").trim()
            if (id.isEmpty()) continue
            val tier = TierGuard.parse(if (entry.isNull("tier")) null else entry.opt("tier"))
                ?: TierGuard.fromName(entry.optString("tier_name"))
                ?: WireTier.CONFIRM
            out[id] = tier
        }
        return out
    }

    /** True when a `result` frame reports success. */
    fun isSuccess(msg: JSONObject): Boolean = msg.optBoolean("success", false)

    /** Best-effort human-readable error out of a failed `result` frame. */
    fun errorOf(msg: JSONObject): String {
        val err = msg.optJSONObject("error")
        val code = err?.optString("code").orEmpty()
        val message = err?.optString("message").orEmpty()
        return when {
            code.isNotEmpty() && message.isNotEmpty() -> "$code: $message"
            message.isNotEmpty() -> message
            code.isNotEmpty() -> code
            else -> "the server refused the request without saying why"
        }
    }
}
