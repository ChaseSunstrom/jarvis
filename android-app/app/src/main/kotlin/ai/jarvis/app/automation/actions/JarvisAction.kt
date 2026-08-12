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

    /**
     * Android permissions this action needs.
     *
     * The dispatcher asks for the ones that are missing and can be granted from
     * a dialog, at the moment this action is dispatched, after the consent gate
     * and before [execute] — see
     * [ai.jarvis.app.compat.RuntimePermissions]. They are also published in the
     * device manifest so the server knows what a phone can do.
     *
     * This is NOT a substitute for checking inside [execute]. A grant can be
     * revoked between the request and the call, the request may have been
     * refused, and a permission that needs a Settings trip is never requested
     * at all. Every action re-checks.
     */
    val requiredPermissions: List<String> get() = emptyList()

    /**
     * Permissions [resolve] itself needs, as opposed to [execute].
     *
     * Split out because the two are asked for at different moments. Resolution
     * runs *before* the consent prompt — that is what makes the prompt truthful
     * — so a permission it needs has to be granted before the prompt too, and
     * a permission `execute` needs must not be, or the OS would ask about
     * sending an SMS before the user has said whether to send one.
     *
     * The one real case is contacts: `send_sms` and `place_call` turn "Sam"
     * into a number here, and without `READ_CONTACTS` they refuse — which is a
     * text that never gets sent, with nothing in the log about a permission.
     *
     * Refusing this one is not fatal. The resolver has an honest answer for a
     * name it cannot look up, and a request that already carried a number needs
     * no lookup at all.
     */
    val resolvePermissions: List<String> get() = emptyList()

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

    /**
     * True when this action can run on this device/build **at all** — no
     * telephony hardware, no camera, no accessibility service, no Shizuku.
     *
     * Deliberately NOT a permission check, even though the dispatcher
     * short-circuits `unsupported` before anything else and it would be an easy
     * place to hang one. An ungranted permission is one dialog away from
     * working; reporting it as unavailable would put `available: false` in the
     * device manifest, teach the model never to call the action, and so
     * guarantee the grant is never requested — a permission that is missing
     * *because* it is missing. The dispatcher asks for it instead; see
     * [requiredPermissions].
     */
    fun isAvailable(ctx: Context): Boolean = true

    /**
     * Turn fuzzy parameters into concrete ones, BEFORE a human is asked.
     *
     * The project's rule is that *what was approved is what runs*: a consent
     * prompt reading `to: "Mum"` and an SMS going to a number nobody was shown
     * is a prompt that lied. So anything that resolves a name to a thing —
     * contact to number, app label to package — happens here, and the
     * dispatcher uses the result for the prompt, the audit entry and
     * [execute] alike.
     *
     * It also exists because the alternative does not work in practice. Making
     * the model call `read_contacts`, read a number out of it, and pass that
     * to `send_sms` is two device round trips with two consent surfaces
     * between the request and the message, and an 8B planner drops it: the
     * reported symptom was "I asked it to text someone and it never did".
     * Resolving on the device makes the one call the model actually emits the
     * one that works.
     *
     * Constraints, all load-bearing:
     *
     *  * **Read-only.** This runs before any policy decision, so a resolver
     *    that changed something would be an un-gated side effect. Look things
     *    up; change nothing.
     *  * **It cannot lower a tier.** The dispatcher recomputes
     *    `tierFor(resolved)` and takes the max with the declared tier, so a
     *    resolver may make an action stricter and never laxer.
     *  * **Failure is an honest error, not a prompt.** A name that matches
     *    nothing, or matches three people, returns
     *    [ResolveResult.Failed] and the human is never asked to approve
     *    something ambiguous. The message is written for the model to act on —
     *    it can come back through `ask_user`.
     *  * **Permissions are re-checked.** Resolving may need one (contacts),
     *    and it must return [ResolveResult.Failed] rather than throw.
     */
    suspend fun resolve(ctx: Context, params: JSONObject): ResolveResult =
        ResolveResult.Unchanged

    /**
     * Do the thing. Called on a background dispatcher inside a timeout.
     * Must not throw for expected failures — return an error result instead.
     */
    suspend fun execute(ctx: Context, params: JSONObject): ActionResult
}

/** What [JarvisAction.resolve] can say. */
sealed class ResolveResult {

    /** Nothing to resolve; the parameters go through as they arrived. */
    object Unchanged : ResolveResult()

    /**
     * Use [params] from here on — for the consent prompt, the audit entry and
     * execution. [note] is a short human sentence about what was resolved,
     * recorded in the audit log so "who was "Mum"?" is answerable later.
     */
    data class Resolved(val params: JSONObject, val note: String? = null) : ResolveResult()

    /** Could not be made concrete. [message] is shown to the model, not to a human. */
    data class Failed(val message: String) : ResolveResult()
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
