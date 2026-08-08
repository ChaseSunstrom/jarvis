package ai.jarvis.app.automation.policy

import android.content.Context
import android.content.SharedPreferences

/**
 * The user's local policy: one [UserPolicy] per action id, plus two global
 * switches. SharedPreferences-backed (no androidx.datastore dependency — this
 * module deliberately pulls in nothing but the platform SDK and coroutines).
 *
 * This is the ONLY writable input to [PolicyEngine] besides the local action
 * table, and it is written only in response to a human tapping something. The
 * server cannot reach it: there is no action that mutates the policy store.
 */
class PolicyStore(context: Context) : PolicyProvider {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    private val listeners = java.util.concurrent.CopyOnWriteArrayList<() -> Unit>()

    // --- per-action policy --------------------------------------------------

    override fun policyFor(actionId: String): UserPolicy =
        UserPolicy.fromStored(prefs.getString(key(actionId), null))

    /**
     * Persist a standing answer for an action.
     *
     * Refuses ALLOW_ALWAYS for Tier 3: a CONFIRM action must be approved every
     * single time, so there is nothing to remember. The refusal is silent by
     * design (the caller is a UI checkbox that should not have been offered);
     * [PolicyEngine.decide] would ignore the stored value anyway, so this is
     * the second of two independent guards.
     */
    override fun remember(actionId: String, policy: UserPolicy, effectiveTier: ActionTier) {
        if (policy == UserPolicy.ALLOW_ALWAYS && !PolicyEngine.canRemember(effectiveTier)) return
        setPolicy(actionId, policy)
    }

    /** Direct set from the settings screen. Same Tier-3 guard as [remember]. */
    fun setPolicy(actionId: String, policy: UserPolicy, effectiveTier: ActionTier? = null) {
        if (policy == UserPolicy.ALLOW_ALWAYS &&
            effectiveTier != null &&
            !PolicyEngine.canRemember(effectiveTier)
        ) {
            return
        }
        prefs.edit().putString(key(actionId), policy.name).apply()
        notifyChanged()
    }

    /** Forget the standing answer for one action (back to [UserPolicy.ASK]). */
    fun clearPolicy(actionId: String) {
        prefs.edit().remove(key(actionId)).apply()
        notifyChanged()
    }

    /** Every explicitly stored answer, for the settings UI. */
    fun all(): Map<String, UserPolicy> {
        val out = LinkedHashMap<String, UserPolicy>()
        for ((k, v) in prefs.all) {
            if (!k.startsWith(PREFIX)) continue
            out[k.removePrefix(PREFIX)] = UserPolicy.fromStored(v as? String)
        }
        return out
    }

    /** Drop every remembered answer. Global switches are left alone. */
    fun clearAllPolicies() {
        val editor = prefs.edit()
        for (k in prefs.all.keys) if (k.startsWith(PREFIX)) editor.remove(k)
        editor.apply()
        notifyChanged()
    }

    // --- global switches ----------------------------------------------------

    /**
     * Master switch for the whole automation layer. Off => every dispatch is
     * denied before any action code runs. Defaults to ON so a fresh install is
     * still useful; the per-action tiers are what keep it safe.
     */
    override var automationEnabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, true)
        set(value) {
            prefs.edit().putBoolean(KEY_ENABLED, value).commit()
            notifyChanged()
        }

    /**
     * Panic: disable everything. Outranks the master switch, every remembered
     * ALLOW_ALWAYS and every incoming command. Written with `commit()` so it is
     * durable the instant the user taps it, even if the process is killed
     * immediately afterwards. Only a human can clear it.
     */
    override var panic: Boolean
        get() = prefs.getBoolean(KEY_PANIC, false)
        set(value) {
            prefs.edit().putBoolean(KEY_PANIC, value).commit()
            notifyChanged()
        }

    /** Convenience for the UI: true when anything at all can run. */
    val automationLive: Boolean get() = automationEnabled && !panic

    // --- change notification for the settings UI ----------------------------

    fun addChangeListener(listener: () -> Unit) { listeners.add(listener) }
    fun removeChangeListener(listener: () -> Unit) { listeners.remove(listener) }
    private fun notifyChanged() { for (l in listeners) runCatching { l() } }

    private fun key(actionId: String) = PREFIX + actionId

    companion object {
        private const val FILE = "jarvis_policy"
        private const val PREFIX = "policy."
        private const val KEY_ENABLED = "automation_enabled"
        private const val KEY_PANIC = "panic"
    }
}
