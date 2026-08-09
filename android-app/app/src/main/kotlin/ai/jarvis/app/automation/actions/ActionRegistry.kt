package ai.jarvis.app.automation.actions

import android.content.Context
import android.os.SystemClock
import android.util.Log
import ai.jarvis.app.automation.audit.AuditEntry
import ai.jarvis.app.automation.audit.AuditLog
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.Decision
import ai.jarvis.app.automation.policy.PolicyEngine
import ai.jarvis.app.automation.policy.PolicyProvider
import ai.jarvis.app.automation.policy.PolicyRequest
import ai.jarvis.app.automation.policy.TrustLevel
import ai.jarvis.app.automation.policy.UserPolicy
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject

/**
 * The registry of everything this device can do, and the single door every
 * command must come through.
 *
 * Nothing else in the app may call [JarvisAction.execute] directly. The order
 * inside [dispatch] is the security property:
 *
 *   look up -> local tier -> raise by requested tier -> user policy ->
 *   PolicyEngine -> (maybe) human -> execute under a timeout -> audit
 *
 * The server's `tier` field is advisory and can only make things stricter; the
 * local table in `builtin/` is the authority.
 */
class ActionRegistry(
    context: Context,
    private val policy: PolicyProvider,
    private val audit: AuditLog,
    private val approvals: ApprovalGateway
) {

    private val appContext: Context = context.applicationContext
    private val actions = LinkedHashMap<String, JarvisAction>()

    // --- registration -------------------------------------------------------

    fun register(action: JarvisAction): ActionRegistry = apply {
        require(!actions.containsKey(action.id)) { "duplicate action id: ${action.id}" }
        actions[action.id] = action
    }

    fun registerAll(vararg toAdd: JarvisAction): ActionRegistry = apply { toAdd.forEach(::register) }

    fun registerAll(toAdd: Iterable<JarvisAction>): ActionRegistry = apply { toAdd.forEach(::register) }

    operator fun get(actionId: String): JarvisAction? = actions[actionId]

    fun ids(): List<String> = actions.keys.toList()

    fun size(): Int = actions.size

    /**
     * True when this action returns content written by somebody other than the
     * user — a web page, a file, the clipboard, another app's screen text.
     *
     * The task runner uses this to taint the variable a `store_as` fills, so a
     * later step that interpolates it dispatches [TrustLevel.UNTRUSTED] and can
     * never be auto-allowed. An unknown id answers `true`: if we do not know
     * what an action returns, we must assume the worst.
     */
    fun producesUntrustedOutput(actionId: String): Boolean =
        actions[actionId]?.untrustedOutput ?: true

    // --- what we advertise to jarvis-core -----------------------------------

    /**
     * Capability strings for `jarvis/device/register`. Only capabilities that
     * are actually usable right now are listed, so enabling the accessibility
     * service or granting SMS should be followed by a re-register.
     */
    fun capabilities(): List<String> = actions.values
        .asSequence()
        .filter { !safeUnsupported(it) && safeAvailable(it) }
        .map { it.capability }
        .distinct()
        .sorted()
        .toList()

    /**
     * Full description of every action, for the server to turn into LLM tools.
     * Includes unsupported ones (marked) so the model learns not to ask.
     */
    fun manifest(): JSONArray {
        val arr = JSONArray()
        for (action in actions.values) {
            val params = JSONObject()
            for ((name, desc) in action.paramsSchema) params.put(name, desc)
            val unsupported = safeUnsupported(action)
            val entry = JSONObject()
                .put("id", action.id)
                .put("tier", action.tier.wire)
                .put("tier_name", action.tier.name)
                .put("description", action.description)
                .put("params", params)
                .put("capability", action.capability)
                .put("available", !unsupported && safeAvailable(action))
                .put("delegated", action.delegated)
                .put("requires_confirmation", action.tier == ActionTier.CONFIRM)
                // So the server knows which results are third-party content and
                // must stay out of its instruction channel.
                .put("untrusted_output", action.untrustedOutput)
            if (action.requiredPermissions.isNotEmpty()) {
                entry.put("android_permissions", action.requiredPermissions.toJsonArray())
            }
            if (unsupported) {
                entry.put("unsupported", true)
                action.unsupportedReason?.let { entry.put("unsupported_reason", it) }
            }
            arr.put(entry)
        }
        return arr
    }

    // --- dispatch -----------------------------------------------------------

    /**
     * Parse and run a `device_command` frame, returning the complete
     * `device_result` frame to send back. Provided so the WebSocket client
     * never has to interpret the `tier` field itself.
     */
    suspend fun handleCommand(command: JSONObject): JSONObject {
        val commandId = command.str("command_id") ?: ""
        val actionId = command.str("action").orEmpty()
        val params = command.optJSONObject("params") ?: JSONObject()
        val requested = ActionTier.fromWire(
            if (command.has("tier") && !command.isNull("tier")) command.optInt("tier", -1) else null
        )
        val reason = command.str("reason") ?: "(no reason given)"
        val result = dispatch(actionId, params, requested, reason, commandId)
        return JSONObject()
            .put("type", "device_result")
            .put("command_id", commandId)
            .put("status", result.status.wire)
            .apply {
                result.data?.let { put("result", it) }
                result.error?.let { put("error", it) }
            }
    }

    /**
     * Run one action, subject to policy.
     *
     * @param requestedTier the server's `tier`, already parsed. Only raises.
     * @param reason the server's human-readable why. UNTRUSTED TEXT: it is
     *   displayed in the consent prompt and written to the audit log, and is
     *   never consulted for a decision.
     * @param trust [TrustLevel.UNTRUSTED] for anything derived from page,
     *   notification, clipboard or screen content — such a request can never be
     *   auto-allowed.
     */
    suspend fun dispatch(
        actionId: String,
        params: JSONObject,
        requestedTier: ActionTier?,
        reason: String,
        commandId: String? = null,
        trust: TrustLevel = TrustLevel.TRUSTED,
        source: String = "server"
    ): ActionResult {
        val startedAt = SystemClock.elapsedRealtime()
        val wallClock = System.currentTimeMillis()

        suspend fun finish(
            result: ActionResult,
            tier: ActionTier,
            decision: Decision,
            note: String?
        ): ActionResult {
            audit.record(
                AuditEntry(
                    timestamp = wallClock,
                    actionId = actionId,
                    params = params,
                    tier = tier,
                    decision = decision,
                    status = result.status.wire,
                    ok = result.ok,
                    error = result.error,
                    source = source,
                    commandId = commandId,
                    durationMs = SystemClock.elapsedRealtime() - startedAt,
                    note = note
                )
            )
            return result
        }

        val action = actions[actionId]
            ?: return finish(
                ActionResult.unsupported("unknown action: $actionId"),
                ActionTier.CONFIRM,
                Decision.DENY,
                "not in the local action table"
            )

        // Honest "no" before any policy work, so these never prompt.
        if (safeUnsupported(action)) {
            return finish(
                ActionResult.unsupported(action.unsupportedReason ?: "not supported on this device"),
                action.tier,
                Decision.DENY,
                "action is declared unsupported"
            )
        }
        if (!safeAvailable(action)) {
            return finish(
                ActionResult.unsupported(
                    action.unsupportedReason
                        ?: "${action.id} is not available on this device right now"
                ),
                action.tier,
                Decision.DENY,
                "action reported unavailable"
            )
        }

        // LOCAL tier is the authority; tierFor() may only raise it further.
        val localTier = ActionTier.max(action.tier, safeTierFor(action, params))
        val effective = PolicyEngine.effectiveTier(localTier, requestedTier)
        val userPolicy = policy.policyFor(actionId)
        val request = PolicyRequest(
            actionId = actionId,
            localTier = localTier,
            requestedTier = requestedTier,
            userPolicy = userPolicy,
            automationEnabled = policy.automationEnabled,
            panic = policy.panic,
            trust = trust
        )
        val decision = PolicyEngine.decide(request)
        val explanation = PolicyEngine.explain(request, decision)

        when (decision) {
            Decision.DENY -> return finish(
                ActionResult.denied(denyMessage(request)),
                effective,
                decision,
                explanation
            )

            Decision.ASK -> {
                val rememberable = PolicyEngine.canRemember(effective, trust)
                val verdict = askHuman(
                    ApprovalRequest(
                        actionId = actionId,
                        description = action.description,
                        params = params, // VERBATIM — the prompt must show the truth
                        tier = effective,
                        reason = reason,
                        commandId = commandId,
                        rememberable = rememberable
                    )
                )
                if (!verdict.allowsExecution) {
                    return finish(
                        ActionResult.denied(
                            if (verdict == ApprovalVerdict.TIMEOUT) "no answer to the confirmation prompt"
                            else "denied by the user"
                        ),
                        effective,
                        Decision.DENY,
                        "$explanation, approval=$verdict"
                    )
                }
                if (verdict == ApprovalVerdict.APPROVED_ALWAYS && rememberable) {
                    runCatching { policy.remember(actionId, UserPolicy.ALLOW_ALWAYS, effective) }
                        .onFailure { Log.w(TAG, "could not persist allow-always for $actionId", it) }
                }

                // A consent prompt can sit on screen for a minute, and the user
                // may spend that minute hitting panic, killing the master
                // switch, or blocking this action outright. Re-read the store
                // and refuse if anything now says no — an approval is consent
                // to run, not a licence that outlives the kill switch.
                val fresh = request.copy(
                    userPolicy = policy.policyFor(actionId),
                    automationEnabled = policy.automationEnabled,
                    panic = policy.panic
                )
                if (PolicyEngine.decide(fresh) == Decision.DENY) {
                    return finish(
                        ActionResult.denied(denyMessage(fresh)),
                        effective,
                        Decision.DENY,
                        "$explanation, revoked while the prompt was up"
                    )
                }
            }

            Decision.ALLOW -> Unit
        }

        val result = try {
            withTimeout(action.timeoutMs) { action.execute(appContext, params) }
        } catch (t: TimeoutCancellationException) {
            ActionResult.error("$actionId timed out after ${action.timeoutMs} ms")
        } catch (t: CancellationException) {
            throw t
        } catch (t: Throwable) {
            Log.w(TAG, "action $actionId failed", t)
            ActionResult.error("${t.javaClass.simpleName}: ${t.message ?: "action failed"}")
        }

        return finish(result, effective, decision, explanation)
    }

    private suspend fun askHuman(request: ApprovalRequest): ApprovalVerdict {
        val verdict = try {
            withTimeoutOrNull(request.timeoutMs + APPROVAL_GRACE_MS) { approvals.request(request) }
        } catch (t: CancellationException) {
            throw t
        } catch (t: Throwable) {
            Log.w(TAG, "approval gateway failed for ${request.actionId}", t)
            null
        }
        // No answer, a hung UI, or a crashed gateway all fail closed.
        return verdict ?: ApprovalVerdict.TIMEOUT
    }

    /** A misbehaving action must not be able to lower its own tier by throwing. */
    private fun safeTierFor(action: JarvisAction, params: JSONObject): ActionTier =
        try {
            action.tierFor(params)
        } catch (t: Throwable) {
            Log.w(TAG, "tierFor threw for ${action.id}; assuming CONFIRM", t)
            ActionTier.CONFIRM
        }

    /**
     * `isAvailable` reaches out to PackageManager, to Shizuku over reflection
     * and — for the delegated UI actions — into an accessibility service owned
     * by another module. Any of those can throw, and a throw here would escape
     * [dispatch] before the audit line is written. Fail closed: an action we
     * cannot ask about is not available.
     */
    private fun safeAvailable(action: JarvisAction): Boolean =
        try {
            action.isAvailable(appContext)
        } catch (t: Throwable) {
            Log.w(TAG, "isAvailable threw for ${action.id}; treating it as unavailable", t)
            false
        }

    /** Same reasoning as [safeAvailable]; a throwing flag means "do not run it". */
    private fun safeUnsupported(action: JarvisAction): Boolean =
        try {
            action.unsupported
        } catch (t: Throwable) {
            Log.w(TAG, "unsupported threw for ${action.id}; treating it as unsupported", t)
            true
        }

    private fun denyMessage(request: PolicyRequest): String = when {
        request.panic -> "automation is in panic mode on this device"
        !request.automationEnabled -> "automation is switched off on this device"
        request.userPolicy == UserPolicy.NEVER ->
            "the user has blocked ${request.actionId} on this device"
        else -> "denied by device policy"
    }

    companion object {
        private const val TAG = "JarvisActions"
        /**
         * Outer watchdog slack. `ApprovalBridge` already applies its own
         * timeout plus a delivery grace; this only catches a gateway that
         * hangs forever, so it must be the looser of the two.
         */
        private const val APPROVAL_GRACE_MS = 10_000L
    }
}
