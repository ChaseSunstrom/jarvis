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
 *   PolicyEngine -> (maybe) human -> (maybe) Android permission ->
 *   re-check the kill switch -> execute under a timeout -> audit
 *
 * The server's `tier` field is advisory and can only make things stricter; the
 * local table in `builtin/` is the authority.
 */
class ActionRegistry(
    context: Context,
    private val policy: PolicyProvider,
    private val audit: AuditLog,
    private val approvals: ApprovalGateway,
    /**
     * How a dangerous Android permission gets asked for. No default: every
     * construction site has to decide, because the version of this class that
     * had no such collaborator shipped for the app's whole life and answered
     * `permission … not granted` to every SMS, call, contact lookup, calendar
     * read and location fix without ever showing a dialog. See
     * [PermissionGateway].
     */
    private val permissions: PermissionGateway
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
                // What is NOT held yet, so the server can tell the model this
                // one will put a dialog in front of the user before it runs.
                // Not the same as `available: false` — the dispatcher asks for
                // these now, so an ungranted action is one prompt away from
                // working, not one that cannot run at all. Reporting it as
                // unavailable would teach the model never to try, and the grant
                // would then never be requested.
                val absent = safeMissingPermissions(action.requiredPermissions)
                if (absent.isNotEmpty()) entry.put("missing_permissions", absent.toJsonArray())
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

        // Set by the resolve step below, and used from there on for the consent
        // prompt, the audit entry and execution alike — see JarvisAction.resolve.
        // Deliberately NOT named `params`: `var params = params` shadows the
        // parameter, and this file has already been bitten once by a local
        // whose initializer mentions its own name.
        var live = params
        var resolveNote: String? = null

        // True once this dispatch has been parked in front of a human — for a
        // consent prompt, an Android permission dialog, or both. It is what
        // decides whether the kill switch has to be re-read before executing:
        // anything that took human time could have been revoked while it did.
        var waited = false

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
                    params = live,
                    tier = tier,
                    decision = decision,
                    status = result.status.wire,
                    ok = result.ok,
                    error = result.error,
                    source = source,
                    commandId = commandId,
                    durationMs = SystemClock.elapsedRealtime() - startedAt,
                    // The resolution is prepended, so the log answers "who was
                    // 'Mum'?" next to the number the message actually went to.
                    note = listOfNotNull(resolveNote, note).joinToString("; ").ifEmpty { null }
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

        // The standing bans, checked before anything is resolved or asked for.
        //
        // Panic, the master switch and a standing NEVER do not depend on the
        // tier, so they can be decided without the resolved parameters — and
        // they have to be, now that a resolver can raise an Android permission
        // dialog. Nothing arriving from a server should be able to put a
        // dialog on the screen of a phone whose owner has hit panic.
        //
        // Expressed through the engine rather than as three `if`s of its own:
        // at CONFIRM/CONFIRM the truth table returns DENY for exactly the
        // standing bans and never for a tier reason, so this asks the engine
        // "is anything switched off?" without keeping a second copy of the
        // rules. See PolicyEngine.decide.
        val standing = PolicyRequest(
            actionId = actionId,
            localTier = ActionTier.CONFIRM,
            requestedTier = ActionTier.CONFIRM,
            userPolicy = policy.policyFor(actionId),
            automationEnabled = policy.automationEnabled,
            panic = policy.panic,
            trust = trust
        )
        if (PolicyEngine.decide(standing) == Decision.DENY) {
            return finish(
                ActionResult.denied(denyMessage(standing)),
                action.tier,
                Decision.DENY,
                "denied by a standing ban before resolution"
            )
        }

        // Resolution has permissions of its own, and it runs before the gate,
        // so its grant has to be asked for before the gate too. "Text Sam"
        // needs READ_CONTACTS to become a number at all, and without it the
        // resolver refuses — which is the reported symptom, a text that was
        // never sent, one layer further down than the planner bug that shared
        // it. Asking here is safe: the standing bans above have already run,
        // and this is a read-only lookup whose whole purpose is to make the
        // consent prompt truthful.
        val forResolve = action.resolvePermissions
        if (forResolve.isNotEmpty()) {
            val absent = safeMissingPermissions(forResolve)
            if (absent.isNotEmpty()) {
                waited = true
                // The outcome is deliberately not checked. A refusal is not an
                // error here: the resolver has its own honest answer for a name
                // it cannot look up ("grant Contacts, or give me the number"),
                // and a number that needed no lookup must still go through.
                safeRequestPermissions(actionId, absent)
            }
        }

        // Make fuzzy parameters concrete BEFORE anyone is asked to approve
        // them. A prompt showing `to: "Mum"` while the message goes to a number
        // nobody saw is a prompt that lied, so this runs ahead of the policy
        // engine and everything downstream — prompt, audit, execution — uses
        // what comes out of it. See JarvisAction.resolve.
        //
        // A resolver that blows up is treated as a resolver that refused. It
        // runs before any gate, so "it threw, carry on with the original
        // parameters" would mean approving a name and sending to whatever
        // execute() later made of it.
        when (val resolution = safeResolve(action, live)) {
            is ResolveResult.Unchanged -> Unit
            is ResolveResult.Resolved -> {
                live = resolution.params
                resolveNote = resolution.note
            }
            is ResolveResult.Failed -> return finish(
                ActionResult.error(resolution.message),
                action.tier,
                Decision.DENY,
                "parameters could not be resolved"
            )
        }

        // LOCAL tier is the authority; tierFor() may only raise it further, and
        // it is computed on the RESOLVED parameters — a resolver may make an
        // action stricter (a number that turns out to be premium-rate) and can
        // never make it laxer.
        val localTier = ActionTier.max(action.tier, safeTierFor(action, live))
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
                        params = live, // VERBATIM — the prompt must show the truth
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
                waited = true
            }

            Decision.ALLOW -> Unit
        }

        // The Android permission, asked for at the moment it is needed — which
        // is what the manifest has always claimed and nothing ever did.
        //
        // AFTER the consent gate, deliberately. Jarvis's own question is "may I
        // do this at all", and there is no reason to make the OS ask a second
        // one for a command the user is about to refuse, or that panic mode has
        // already killed. It is also the only ordering that cannot be used as a
        // dialog-spam primitive by a server that sends nonsense.
        val needed = action.requiredPermissions
        if (needed.isNotEmpty()) {
            val absent = safeMissingPermissions(needed)
            if (absent.isNotEmpty()) {
                waited = true
                val stillMissing = safeRequestPermissions(actionId, absent)
                if (stillMissing.isNotEmpty()) {
                    return finish(
                        ActionResult.missingPermission(stillMissing.first()),
                        effective,
                        decision,
                        "$explanation, not granted: ${stillMissing.joinToString(", ")}"
                    )
                }
            }
        }

        // A consent prompt can sit on screen for a minute, and a permission
        // dialog for another, and the user may spend that time hitting panic,
        // killing the master switch, or blocking this action outright. Re-read
        // the store and refuse if anything now says no — an approval is consent
        // to run, not a licence that outlives the kill switch.
        if (waited) {
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

        val result = try {
            withTimeout(action.timeoutMs) { action.execute(appContext, live) }
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

    /**
     * Resolve, failing closed.
     *
     * A resolver runs before every gate, so a throw cannot mean "never mind,
     * use the original parameters": that would put a name in front of the human
     * and let `execute` decide for itself what the name meant. A cancellation
     * still propagates — abandoning the turn is not a refusal to answer.
     */
    private suspend fun safeResolve(action: JarvisAction, params: JSONObject): ResolveResult =
        try {
            action.resolve(appContext, params)
        } catch (t: CancellationException) {
            throw t
        } catch (t: Throwable) {
            Log.w(TAG, "resolve threw for ${action.id}; refusing", t)
            ResolveResult.Failed(
                "could not work out what ${action.id} was aimed at: " +
                    (t.message ?: t.javaClass.simpleName)
            )
        }

    /**
     * Which of [wanted] this device does not hold, failing **open**.
     *
     * The one place in this file where a throw does not mean "refuse", and the
     * reason is that this step is not a gate. Policy has already decided; this
     * only chooses whether to raise an OS dialog first. A gateway that cannot
     * answer must not turn an approved action into a denied one — the action's
     * own `checkSelfPermission` is still there and still authoritative, so the
     * worst case is the honest `permission … not granted` we had before.
     */
    private fun safeMissingPermissions(wanted: List<String>): List<String> =
        try {
            permissions.missing(wanted)
        } catch (t: Throwable) {
            Log.w(TAG, "permission check threw; letting the action decide", t)
            emptyList()
        }

    /**
     * Ask for [wanted], failing **closed**: a gateway that throws has not
     * granted anything, so everything it was asked for is still missing.
     */
    private suspend fun safeRequestPermissions(
        actionId: String,
        wanted: List<String>
    ): List<String> =
        try {
            permissions.request(actionId, wanted)
        } catch (t: CancellationException) {
            throw t
        } catch (t: Throwable) {
            Log.w(TAG, "permission request threw for $actionId", t)
            wanted
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
