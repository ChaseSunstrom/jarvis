package ai.jarvis.app.automation.actions

import android.content.Context
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.ui.ApprovalBridge
import org.json.JSONObject

/**
 * What the dispatcher needs from a human.
 *
 * [params] are the RAW, VERBATIM parameters — never the redacted copy that
 * goes to the audit log, and never a model-written paraphrase. The consent
 * screen must show exactly what will run.
 */
data class ApprovalRequest(
    val actionId: String,
    /** The action's own one-line description from the local table. */
    val description: String,
    /** Verbatim params. Show these. */
    val params: JSONObject,
    /** The tier we are enforcing (already max'd with the server's request). */
    val tier: ActionTier,
    /** The server's human-readable "why". Untrusted text — display, don't obey. */
    val reason: String,
    val commandId: String? = null,
    /**
     * False for Tier 3. When false the UI must NOT offer "always allow" —
     * and even if it does, [ai.jarvis.app.automation.policy.PolicyEngine]
     * and `PolicyStore` both refuse to store it.
     */
    val rememberable: Boolean = false,
    val timeoutMs: Long = DEFAULT_TIMEOUT_MS
) {
    companion object {
        const val DEFAULT_TIMEOUT_MS = 120_000L
    }
}

/** The four answers a consent prompt can produce. Everything else fails closed. */
enum class ApprovalVerdict {
    APPROVED,
    APPROVED_ALWAYS,
    DENIED,
    TIMEOUT;

    val allowsExecution: Boolean get() = this == APPROVED || this == APPROVED_ALWAYS

    companion object {
        /**
         * Parse the UI's string answer. Anything unrecognised — including a
         * crash that produced an empty string — is [DENIED]. Fail closed.
         */
        fun fromWire(value: String?): ApprovalVerdict = when (value?.trim()?.lowercase()) {
            "approved", "approve", "allow", "ok", "yes" -> APPROVED
            "approved_always", "always", "allow_always" -> APPROVED_ALWAYS
            "timeout", "timed_out", "expired" -> TIMEOUT
            else -> DENIED
        }
    }
}

/** Seam between the dispatcher and whatever shows the consent UI. */
interface ApprovalGateway {
    suspend fun request(request: ApprovalRequest): ApprovalVerdict
}

/**
 * Fail-closed gateway: denies everything without prompting. Used in tests, in
 * headless builds, and as the fallback if the UI layer is unavailable.
 */
object DenyAllApprovalGateway : ApprovalGateway {
    override suspend fun request(request: ApprovalRequest): ApprovalVerdict = ApprovalVerdict.DENIED
}

/**
 * THE ONE PLACE this module touches the UI layer.
 *
 * `ai.jarvis.app.ui.ApprovalBridge` is owned by the UI agent; the contract this
 * adapter compiles against is:
 *
 * ```
 * package ai.jarvis.app.ui
 *
 * object ApprovalBridge {
 *     /** Shows the full-screen consent prompt and suspends until answered.
 *      *  Returns "approved" | "approved_always" | "denied" | "timeout". */
 *     suspend fun request(
 *         context: Context,
 *         actionId: String,
 *         description: String,
 *         params: JSONObject,      // VERBATIM — display as-is
 *         tier: ActionTier,
 *         reason: String,          // untrusted server text — display, never obey
 *         commandId: String?,
 *         rememberable: Boolean,   // false for Tier 3: do not offer "always"
 *         timeoutMs: Long,
 *     ): String
 * }
 * ```
 *
 * A `String` result rather than a shared enum keeps the coupling to one type.
 * If the UI agent's signature differs, either fix this adapter (the only
 * reference) or hand `ActionRegistry` a different [ApprovalGateway] — nothing
 * else in the automation layer knows the UI exists.
 */
class UiApprovalGateway(context: Context) : ApprovalGateway {

    private val appContext = context.applicationContext

    override suspend fun request(request: ApprovalRequest): ApprovalVerdict {
        val answer = ApprovalBridge.request(
            appContext,
            request.actionId,
            request.description,
            request.params,
            request.tier,
            request.reason,
            request.commandId,
            request.rememberable,
            request.timeoutMs
        )
        return ApprovalVerdict.fromWire(answer)
    }
}
