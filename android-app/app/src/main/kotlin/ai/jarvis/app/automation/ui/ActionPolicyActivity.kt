package ai.jarvis.app.automation.ui

import ai.jarvis.app.automation.AutomationRuntime
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.PolicyStore
import ai.jarvis.app.automation.policy.UserPolicy
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * WHAT JARVIS MAY DO WITHOUT ASKING.
 *
 * ## Why this screen exists
 *
 * [PolicyStore] has had `setPolicy`, `clearPolicy`, `all()` and
 * `clearAllPolicies()` since it was written, and their KDoc says what they are
 * for: *"Direct set from the settings screen"*, *"Every explicitly stored
 * answer, for the settings UI"*. Nothing outside the unit tests ever called
 * any of them. The store, its Tier-3 guard, its change listeners and the whole
 * `UserPolicy` vocabulary were built, documented and tested, and the user had
 * no way to write a single value into it.
 *
 * So the only way an action could ever leave [UserPolicy.ASK] was the consent
 * prompt's own remember-this control on a Tier-2 action — and the effect was
 * that Jarvis asked, every time, about everything it was allowed to ask about,
 * with no way to say "yes, always, this one is fine". Reported exactly that
 * way: *"I should be able to determine whether or not Jarvis can do certain
 * tasks or whatever without approval"*.
 *
 * ## What it can and cannot offer
 *
 * The three-way choice is the store's own vocabulary and nothing is invented
 * here:
 *
 *  * **ASK** — the default. The consent prompt decides.
 *  * **ALWAYS** — run it, no human in the loop. Offered only where the store
 *    would accept it, which is Tier 1 and Tier 2. [PolicyStore.setPolicy]
 *    refuses it for Tier 3 and [ai.jarvis.app.automation.policy.PolicyEngine]
 *    ignores such a value even if one were somehow stored, so this screen not
 *    offering it is the third of three independent guards, not the only one.
 *  * **NEVER** — a hard no, offered for everything. Nothing overrides it: not
 *    a server command, not a task, not a trigger.
 *
 * NEVER is available on every action deliberately. Tightening must always be
 * possible; only loosening is restricted.
 */
class ActionPolicyActivity : Activity() {

    private var store: PolicyStore? = null
    private var rows: LinearLayout? = null

    /** Redraws when something else writes the store — the consent prompt does. */
    private val onStoreChanged = { runOnUiThread { refresh() } }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisUi.immersive(this)

        val column = JarvisUi.column(this)
        column.addView(JarvisUi.title(this, "WHAT JARVIS MAY DO"))
        column.addView(
            JarvisUi.hint(
                this,
                "Everything starts at ASK, which means the tier decides: Jarvis just does " +
                    "the harmless things, tells you about the middling ones, and stops for " +
                    "your approval before anything it cannot take back.\n\n" +
                    "Change one to ALWAYS and it stops asking. Change one to NEVER and " +
                    "nothing can run it — not a command from the server, not a task, not a " +
                    "trigger. The most dangerous actions cannot be set to ALWAYS at all; " +
                    "they are marked below."
            )
        )
        column.addView(JarvisUi.spacer(this, 12))

        rows = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(rows, matchWidth())

        column.addView(JarvisUi.spacer(this, 16))
        column.addView(
            JarvisUi.button(this, "BACK TO ASK FOR EVERYTHING") {
                store?.clearAllPolicies()
                toast("Every action is back to ASK.")
                refresh()
            },
            matchWidth(),
        )

        setContentView(
            ScrollView(this).apply {
                isFillViewport = true
                addView(column, matchWidth())
            }.also { JarvisUi.fitSystemBars(it) }
        )

        store = try {
            // The same table the dispatcher uses, so this screen can never
            // offer ALWAYS for something the engine classifies differently.
            PolicyStore(applicationContext) { id -> tierOf(id) }
        } catch (t: Throwable) {
            Log.w(TAG, "the policy store could not be opened", t)
            null
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        store?.addChangeListener(onStoreChanged)
        refresh()
    }

    override fun onPause() {
        store?.removeChangeListener(onStoreChanged)
        super.onPause()
    }

    // --- the list -----------------------------------------------------------

    private fun tierOf(actionId: String): ActionTier? =
        runCatching { AutomationRuntime.ensure(applicationContext).registry[actionId]?.tier }
            .getOrNull()

    private fun refresh() {
        val host = rows ?: return
        host.removeAllViews()

        val registry = runCatching { AutomationRuntime.ensure(applicationContext).registry }
            .onFailure { Log.w(TAG, "the action table could not be built", it) }
            .getOrNull()
        val live = store
        if (registry == null || live == null) {
            host.addView(
                JarvisUi.hint(this, "The automation layer is not available on this phone.")
            )
            return
        }

        val stored = live.all()
        var drawn = 0
        for (id in registry.ids().sorted()) {
            val action = registry[id] ?: continue
            host.addView(row(id, action.description, action.tier, stored[id] ?: UserPolicy.ASK))
            drawn += 1
        }
        if (drawn == 0) {
            host.addView(JarvisUi.hint(this, "No actions are registered on this phone."))
        }
    }

    private fun row(
        actionId: String,
        description: String,
        tier: ActionTier,
        current: UserPolicy,
    ): ViewGroup {
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = JarvisUi.panel(this@ActionPolicyActivity)
            val pad = JarvisUi.dp(this@ActionPolicyActivity, 14)
            setPadding(pad, pad, pad, pad)
        }
        panel.addView(JarvisUi.label(this, actionId))
        panel.addView(
            JarvisUi.hint(this, description.ifBlank { "No description." })
        )
        panel.addView(
            TextView(this).apply {
                text = tierBlurb(tier)
                setTextColor(JarvisUi.DIM)
                textSize = JarvisUi.Type.LABEL
            }
        )

        // ALWAYS is dropped from the list rather than shown-and-refused: an
        // option that is offered and then silently ignored is worse than one
        // that was never there, and the sentence above says why it is missing.
        val allowAlways = tier != ActionTier.CONFIRM
        val choices = buildList {
            add(UserPolicy.ASK)
            if (allowAlways) add(UserPolicy.ALLOW_ALWAYS)
            add(UserPolicy.NEVER)
        }
        val labels = choices.map(::labelOf)
        val selected = choices.indexOf(current).takeIf { it >= 0 } ?: 0

        panel.addView(
            JarvisUi.chooser(this, actionId, labels, selected) { which ->
                val picked = choices.getOrNull(which) ?: return@chooser
                applyPolicy(actionId, picked, tier)
            },
            matchWidth(),
        )
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(panel, matchWidth())
            addView(JarvisUi.spacer(this@ActionPolicyActivity, 10))
        }
    }

    private fun applyPolicy(actionId: String, picked: UserPolicy, tier: ActionTier) {
        val live = store ?: return
        if (picked == UserPolicy.ASK) {
            live.clearPolicy(actionId)
            return
        }
        // The store's own answer, not this screen's guess at it. If it refuses,
        // say so — the alternative is a control that appears to have taken.
        if (!live.setPolicy(actionId, picked, tier)) {
            toast("“$actionId” always needs your approval; that cannot be turned off.")
            refresh()
        }
    }

    private fun labelOf(policy: UserPolicy): String = when (policy) {
        UserPolicy.ASK -> "Ask me (the tier decides)"
        UserPolicy.ALLOW_ALWAYS -> "Always — do it without asking"
        UserPolicy.NEVER -> "Never — refuse this always"
    }

    private fun tierBlurb(tier: ActionTier): String = when (tier) {
        ActionTier.AUTO -> "Tier 1 · harmless; runs silently unless you say otherwise"
        ActionTier.NOTIFY -> "Tier 2 · runs and tells you afterwards"
        ActionTier.CONFIRM -> "Tier 3 · always stops for your approval, and cannot be set to ALWAYS"
    }

    private fun matchWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    private companion object {
        const val TAG = "JarvisActionPolicy"
    }
}
