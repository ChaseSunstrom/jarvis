package ai.jarvis.app.automation.actions

import android.content.Context
import ai.jarvis.app.automation.policy.ActionTier
import org.json.JSONObject

/**
 * One thing Jarvis can do to this phone.
 *
 * Implementations are small, single-purpose and stateless. They may assume
 * policy has already been satisfied — by the time [execute] is called the
 * dispatcher has consulted the local tier table, the user's policy store and,
 * where required, a human. They may NOT assume permissions: every action
 * re-checks its own runtime permissions and returns
 * `ActionResult.missingPermission(...)` rather than throwing.
 */
interface JarvisAction {

    /** Stable id used on the wire and as the key in the user's policy store. */
    val id: String

    /**
     * The LOCAL tier. This is the authority — the `tier` field of an incoming
     * `device_command` can raise the enforced tier but never lower it.
     */
    val tier: ActionTier

    /** One line, written for the LLM tool description and the consent prompt. */
    val description: String

    /** param name -> human/LLM-readable type + meaning. Also shipped in the manifest. */
    val paramsSchema: Map<String, String>

    /** Coarse capability bucket advertised in `jarvis/device/register`. */
    val capability: String get() = "device"

    /** Android permissions this action needs, for the settings screen. */
    val requiredPermissions: List<String> get() = emptyList()

    /** Hard cap on execution. The dispatcher enforces it with `withTimeout`. */
    val timeoutMs: Long get() = 15_000L

    /**
     * True for actions that exist only so the server gets an honest "no".
     * The dispatcher short-circuits them BEFORE policy, so they never prompt.
     */
    val unsupported: Boolean get() = false

    /**
     * Why this cannot run — used both when [unsupported] is true and when
     * [isAvailable] returns false, so the model gets an actionable sentence
     * ("enable the accessibility service") instead of a shrug.
     */
    val unsupportedReason: String? get() = null

    /** True when another component (accessibility service) actually runs this. */
    val delegated: Boolean get() = false

    /**
     * True when this action's RESULT carries text somebody other than the user
     * wrote — a web response, a file, the clipboard, a calendar invitation, a
     * contact name, another app's on-screen labels, the output of a shell
     * command.
     *
     * This is the machine-readable half of [markUntrusted]. `markUntrusted()`
     * flags the payload for the server; this flag is what a LOCAL consumer
     * needs, because "content fetched from the web/notifications/screen must
     * never be able to cause an action on its own" only holds if something on
     * the device knows which results are content in the first place.
     *
     * Consumers:
     *
     *  * `ActionRegistry.producesUntrustedOutput(id)` and the `untrusted_output`
     *    field of [ActionRegistry.manifest].
     *  * the task runner, which must taint any variable a `store_as` fills from
     *    such an action, so a later step that interpolates it dispatches with
     *    `TrustLevel.UNTRUSTED` and can never be auto-allowed.
     *
     * Declaring it is not optional bookkeeping: `tools/action_table_test.py`
     * fails the build if an action calls `markUntrusted()` without it.
     */
    val untrustedOutput: Boolean get() = false

    /**
     * Per-invocation tier bump. Returning a HIGHER tier for dangerous
     * parameters is allowed and honoured; returning a lower one is ignored
     * (the dispatcher takes `max(tier, tierFor(params))`), so this can only
     * ever make things stricter.
     */
    fun tierFor(params: JSONObject): ActionTier = tier

    /** True when this action can run on this device/build at all. */
    fun isAvailable(ctx: Context): Boolean = true

    /**
     * Do the thing. Called on a background dispatcher inside a timeout.
     * Must not throw for expected failures — return an error result instead.
     */
    suspend fun execute(ctx: Context, params: JSONObject): ActionResult
}

/**
 * The outcome of one action.
 *
 * [status] carries straight onto the wire as `device_result.status`
 * (`ok` | `denied` | `error` | `unsupported`); it defaults from [ok] so the
 * plain three-argument constructor still does the right thing.
 */
data class ActionResult(
    val ok: Boolean,
    val data: JSONObject? = null,
    val error: String? = null,
    val status: Status = if (ok) Status.OK else Status.ERROR
) {
    enum class Status(val wire: String) {
        OK("ok"),
        DENIED("denied"),
        ERROR("error"),
        UNSUPPORTED("unsupported")
    }

    /** Ready-to-send `device_result` body (minus `type`/`command_id`). */
    fun toWire(): JSONObject = JSONObject()
        .put("status", status.wire)
        .apply {
            data?.let { put("result", it) }
            error?.let { put("error", it) }
        }

    companion object {
        fun ok(data: JSONObject? = null) = ActionResult(true, data, null, Status.OK)

        fun ok(vararg pairs: Pair<String, Any?>) = ActionResult(true, json(*pairs), null, Status.OK)

        fun error(message: String) = ActionResult(false, null, message, Status.ERROR)

        fun denied(message: String) = ActionResult(false, null, message, Status.DENIED)

        fun unsupported(message: String) = ActionResult(false, null, message, Status.UNSUPPORTED)

        /** The one true wording for a permission failure, per the shared brief. */
        fun missingPermission(permission: String) =
            ActionResult(false, null, "permission $permission not granted", Status.ERROR)
    }
}

/** Tiny JSON builder so action bodies stay readable. Nulls are dropped. */
fun json(vararg pairs: Pair<String, Any?>): JSONObject {
    val o = JSONObject()
    for ((k, v) in pairs) if (v != null) o.put(k, v)
    return o
}

/**
 * Marks a payload as content that came from outside the trust boundary — a web
 * response, the clipboard, a file, an on-screen text read. jarvis-core is
 * expected to keep it out of the instruction channel, and the dispatcher
 * refuses to auto-allow anything derived from it.
 */
fun JSONObject.markUntrusted(): JSONObject = put("untrusted", true)
