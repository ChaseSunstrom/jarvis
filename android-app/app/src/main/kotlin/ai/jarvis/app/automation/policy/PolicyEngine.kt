package ai.jarvis.app.automation.policy

/**
 * PURE LOGIC — no Android imports, no org.json, no I/O, no clock.
 *
 * This is the whole safety story of the Android side, so it is kept small
 * enough to read in one sitting and is mirrored line-for-line by
 * `android-app/tools/policy_truth_table_test.py`, which is the executable spec.
 * If you change a rule here, change it there and re-run:
 *
 *     python3 android-app/tools/policy_truth_table_test.py
 *
 * The rules, in order of precedence:
 *
 *  1. panic flag set                       -> DENY   (kill switch, nothing runs)
 *  2. automation master switch off         -> DENY
 *  3. UserPolicy.NEVER                     -> DENY   (beats everything below)
 *  4. effective tier = max(local, requested); the server can only RAISE.
 *  5. CONFIRM                              -> ASK    ALWAYS. ALLOW_ALWAYS does
 *                                                    NOT bypass it. This is the
 *                                                    critical invariant.
 *  6. NOTIFY                               -> ALLOW if ALLOW_ALWAYS, else ASK
 *  7. AUTO                                 -> ALLOW
 *  8. …except an UNTRUSTED request is never ALLOWed: it degrades to ASK.
 */
object PolicyEngine {

    /**
     * The tier we actually enforce.
     *
     * The `tier` field in an incoming `device_command` is a HINT FROM A
     * MACHINE THAT MAY BE WRONG OR PROMPT-INJECTED. It is used only through
     * [ActionTier.max], so it can raise the tier and can never lower it. A
     * missing/garbage value (null) contributes [ActionTier.AUTO] and therefore
     * changes nothing.
     */
    fun effectiveTier(localTier: ActionTier, requestedTier: ActionTier?): ActionTier =
        ActionTier.max(localTier, requestedTier ?: ActionTier.AUTO)

    /**
     * The core truth table. `actionId` participates only in the human-readable
     * explanation — the decision is a function of the tiers and the user's
     * standing answer, nothing else.
     */
    fun decide(
        actionId: String,
        localTier: ActionTier,
        requestedTier: ActionTier?,
        userPolicy: UserPolicy
    ): Decision {
        if (userPolicy == UserPolicy.NEVER) return Decision.DENY
        return when (effectiveTier(localTier, requestedTier)) {
            // Tier 3 asks every single time. ALLOW_ALWAYS is deliberately
            // ignored here; see canRemember() — it can never be stored either.
            ActionTier.CONFIRM -> Decision.ASK
            ActionTier.NOTIFY ->
                if (userPolicy == UserPolicy.ALLOW_ALWAYS) Decision.ALLOW else Decision.ASK
            ActionTier.AUTO -> Decision.ALLOW
        }
    }

    /**
     * The full decision used by the dispatcher: the core table plus the two
     * global switches and the trust level of the request.
     */
    fun decide(request: PolicyRequest): Decision {
        if (request.panic) return Decision.DENY
        if (!request.automationEnabled) return Decision.DENY

        val base = decide(
            request.actionId,
            request.localTier,
            request.requestedTier,
            request.userPolicy
        )

        // Untrusted content (web page, notification, screen text, clipboard,
        // HTTP body) may never cause an action on its own. The strongest
        // outcome it can produce is a fresh human approval.
        if (request.trust == TrustLevel.UNTRUSTED && base == Decision.ALLOW) return Decision.ASK
        return base
    }

    /**
     * May an "allow always" answer be persisted for this action?
     *
     * Never for Tier 3 — a CONFIRM action must be re-approved every time, so
     * remembering it would be indistinguishable from bypassing it. Never for
     * an untrusted-sourced approval either: consent given while looking at a
     * prompt driven by injected content should not become a standing rule.
     */
    fun canRemember(effectiveTier: ActionTier, trust: TrustLevel = TrustLevel.TRUSTED): Boolean =
        effectiveTier != ActionTier.CONFIRM && trust == TrustLevel.TRUSTED

    /** One-line human-readable reason, for the audit log and the consent UI. */
    fun explain(request: PolicyRequest, decision: Decision): String {
        val effective = effectiveTier(request.localTier, request.requestedTier)
        val raised = request.requestedTier != null &&
            request.requestedTier.ordinal > request.localTier.ordinal
        val parts = mutableListOf<String>()
        parts += "${request.actionId} local=${request.localTier} " +
            "requested=${request.requestedTier ?: "none"} effective=$effective"
        if (raised) parts += "raised by server"
        parts += "policy=${request.userPolicy}"
        if (request.trust == TrustLevel.UNTRUSTED) parts += "untrusted source"
        if (request.panic) parts += "PANIC"
        if (!request.automationEnabled) parts += "automation disabled"
        parts += "-> $decision"
        return parts.joinToString(", ")
    }
}

/**
 * Everything the engine is allowed to look at. Constructed by the dispatcher
 * from the local action table, the incoming command, and the user's store —
 * never from anything the model wrote.
 */
data class PolicyRequest(
    val actionId: String,
    /** From the LOCAL action table on this device. The authority. */
    val localTier: ActionTier,
    /** From the incoming message. A hint. Can only raise. */
    val requestedTier: ActionTier?,
    val userPolicy: UserPolicy,
    val automationEnabled: Boolean = true,
    val panic: Boolean = false,
    val trust: TrustLevel = TrustLevel.TRUSTED
)

/**
 * Storage seam so [PolicyEngine] and the dispatcher stay unit-testable without
 * Android. `PolicyStore` is the SharedPreferences-backed implementation.
 */
interface PolicyProvider {
    /** The user's standing answer for this action id; [UserPolicy.ASK] by default. */
    fun policyFor(actionId: String): UserPolicy

    /**
     * Persist a standing answer. Implementations MUST refuse to store
     * [UserPolicy.ALLOW_ALWAYS] when `effectiveTier` is [ActionTier.CONFIRM]
     * (belt and braces — the engine ignores it anyway).
     */
    fun remember(actionId: String, policy: UserPolicy, effectiveTier: ActionTier)

    /** Master switch. False => everything is denied. */
    val automationEnabled: Boolean

    /** Panic kill switch. True => everything is denied, outranks all else. */
    val panic: Boolean
}

/** In-memory [PolicyProvider] for tests and for a registry built before storage exists. */
class InMemoryPolicyProvider(
    initial: Map<String, UserPolicy> = emptyMap(),
    override var automationEnabled: Boolean = true,
    override var panic: Boolean = false
) : PolicyProvider {
    private val map = HashMap<String, UserPolicy>(initial)

    override fun policyFor(actionId: String): UserPolicy = map[actionId] ?: UserPolicy.ASK

    override fun remember(actionId: String, policy: UserPolicy, effectiveTier: ActionTier) {
        if (policy == UserPolicy.ALLOW_ALWAYS && !PolicyEngine.canRemember(effectiveTier)) return
        map[actionId] = policy
    }

    fun snapshot(): Map<String, UserPolicy> = map.toMap()
}
