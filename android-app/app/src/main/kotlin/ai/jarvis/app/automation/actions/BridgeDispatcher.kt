package ai.jarvis.app.automation.actions

import ai.jarvis.app.automation.AutomationBridge
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.TrustLevel
import org.json.JSONArray
import org.json.JSONObject

/**
 * The adapter `AutomationBridge` documents and the channel looks for.
 *
 * `JarvisChannel` reads `AutomationBridge.dispatcher` for every incoming
 * `device_command`; with the slot empty it answers `unsupported` and the phone
 * can do nothing at all. Installing it is part of building the registry, so it
 * happens in [ai.jarvis.app.automation.actions.builtin.Builtins.standard]
 * rather than being left to whoever remembers.
 *
 * The adapter is deliberately thin and makes no decision of its own:
 *
 *  * `tier` arrives as a STRING the channel already raised to
 *    `max(local-from-manifest, incoming)`. It is fed back in as
 *    `requestedTier`, which [ActionRegistry.dispatch] can only use to raise
 *    again against the real local table. A tier string this side has never
 *    heard of parses to null and contributes nothing — it cannot lower.
 *  * `trust` is pinned to [TrustLevel.TRUSTED] because a `device_command`
 *    arrives on the authenticated jarvis-core socket. That is a statement about
 *    the CHANNEL, not about the content: `reason` and `params` are still
 *    untrusted text, which is why they are shown verbatim in the consent prompt
 *    and never parsed for a decision. Callers whose *content* came off a web
 *    page, a notification or the screen must call `registry.dispatch` directly
 *    with [TrustLevel.UNTRUSTED] — the task runner does exactly that.
 */
class RegistryDispatcher(
    private val registry: ActionRegistry
) : AutomationBridge.CommandAwareDispatcher {

    override fun manifest(): JSONArray = registry.manifest()

    override fun capabilities(): List<String> = registry.capabilities()

    override suspend fun dispatch(
        actionId: String,
        params: JSONObject,
        tier: String,
        reason: String
    ): JSONObject = dispatch(actionId, params, tier, reason, null)

    override suspend fun dispatch(
        actionId: String,
        params: JSONObject,
        tier: String,
        reason: String,
        commandId: String?
    ): JSONObject = registry.dispatch(
        actionId = actionId,
        params = params,
        requestedTier = ActionTier.fromName(tier),
        reason = reason,
        commandId = commandId,
        trust = TrustLevel.TRUSTED,
        source = "server"
    ).toWire()
}

/** The registry seen as the bridge's single action door. */
fun ActionRegistry.asBridgeDispatcher(): AutomationBridge.CommandAwareDispatcher =
    RegistryDispatcher(this)
