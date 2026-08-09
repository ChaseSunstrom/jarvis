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
        /** Matches `ApprovalBridge.TIMEOUT_MS`, which clamps anything longer. */
        const val DEFAULT_TIMEOUT_MS = 60_000L
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
 * `ai.jarvis.app.ui.ApprovalBridge` is owned by the UI agent. The contract this
 * adapter compiles against, in its words:
 *
 * ```
 * suspend fun request(
 *     context: Context,
 *     actionId: String,
 *     description: String,
 *     params: Any?,          // VERBATIM — serialised and displayed as-is
 *     tier: Any?,            // display only; ActionTier is fine
 *     reason: String,        // untrusted server text — displayed, never obeyed
 *     commandId: String?,
 *     rememberable: Boolean,
 *     timeoutMs: Long,       // clamped to at most ApprovalBridge.TIMEOUT_MS
 * ): String                  // "approved" | "denied" | "timeout"
 * ```
 *
 * A `String` result rather than a shared enum keeps the coupling to one type,
 * and the bridge deliberately never answers "approved_always" — its prompt has
 * no "always allow" control, so a Tier-2 caller that hoped for a remembered
 * answer is simply asked again next time. Erring toward more prompting is the
 * only safe direction.
 *
 * Nothing else in the automation layer knows the UI exists. To swap it out,
 * hand `ActionRegistry` a different [ApprovalGateway].
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
