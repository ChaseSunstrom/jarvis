package ai.jarvis.app.automation.policy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The rule the POLICY STORE has to uphold on its own.
 *
 * `PolicyEngine.decide` already ignores a stored `ALLOW_ALWAYS` for a Tier-3
 * action, so this is the second of two independent guards. It only counts as a
 * second guard if it works without the caller remembering to arm it — which is
 * exactly what `PolicyStore.setPolicy(id, ALLOW_ALWAYS)` used to get wrong: an
 * omitted `effectiveTier` meant "skip the check", so a settings screen calling
 * the two-argument form could write a standing yes for `send_sms`.
 *
 * `PolicyStore` itself needs a Context, so the decision lives in
 * [PolicyEngine.mayStore] where a plain JVM test can reach it.
 */
class PolicyStoreRulesTest {

    @Test
    fun `allow-always is refused for tier 3 however the tier arrives`() {
        // explicit tier from the dispatcher
        assertFalse(PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM))
        // tier looked up in the local action table by the store
        assertFalse(PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, null, ActionTier.CONFIRM))
    }

    @Test
    fun `an unknown tier is treated as tier 3`() {
        // This is the regression: no explicit tier AND no table entry used to
        // mean "no check at all".
        assertFalse(
            "an action of unknown tier must not get a standing allow",
            PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, null, null)
        )
    }

    @Test
    fun `an explicit tier wins over the table`() {
        // http_request is NOTIFY in the table but CONFIRM for a POST; the
        // dispatcher passes the tier it actually enforced, and that is the one
        // that decides.
        assertFalse(
            PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM, ActionTier.NOTIFY)
        )
        assertTrue(
            PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY, ActionTier.CONFIRM)
        )
    }

    @Test
    fun `tier 1 and tier 2 may still be remembered`() {
        assertTrue(PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, ActionTier.AUTO))
        assertTrue(PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY))
        assertTrue(PolicyEngine.mayStore(UserPolicy.ALLOW_ALWAYS, null, ActionTier.NOTIFY))
    }

    @Test
    fun `ask and never are storable at every tier, including unknown`() {
        for (policy in listOf(UserPolicy.ASK, UserPolicy.NEVER)) {
            for (tier in listOf(null, ActionTier.AUTO, ActionTier.NOTIFY, ActionTier.CONFIRM)) {
                assertTrue(
                    "$policy at tier $tier must be storable",
                    PolicyEngine.mayStore(policy, tier)
                )
            }
        }
    }

    @Test
    fun `a stored tier 3 allow-always would still be ignored by the engine`() {
        // Belt and braces: even if something got past the store, the decision
        // does not change.
        assertEquals(
            Decision.ASK,
            PolicyEngine.decide("send_sms", ActionTier.CONFIRM, null, UserPolicy.ALLOW_ALWAYS)
        )
    }

    @Test
    fun `the in-memory provider refuses the same writes`() {
        val store = InMemoryPolicyProvider()
        store.remember("send_sms", UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM)
        assertEquals(UserPolicy.ASK, store.policyFor("send_sms"))

        store.remember("set_alarm", UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY)
        assertEquals(UserPolicy.ALLOW_ALWAYS, store.policyFor("set_alarm"))
    }
}
