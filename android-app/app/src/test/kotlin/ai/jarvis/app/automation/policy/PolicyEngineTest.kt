package ai.jarvis.app.automation.policy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Kotlin half of the policy spec. Its Python twin,
 * `android-app/tools/policy_truth_table_test.py`, asserts exactly the same
 * table and DOES run in CI, so if these two ever disagree the Python one is
 * the one that shouted first.
 */
class PolicyEngineTest {

    private val tiers = listOf(ActionTier.AUTO, ActionTier.NOTIFY, ActionTier.CONFIRM)
    private val requested = listOf(null, ActionTier.AUTO, ActionTier.NOTIFY, ActionTier.CONFIRM)
    private val policies = listOf(UserPolicy.ALLOW_ALWAYS, UserPolicy.ASK, UserPolicy.NEVER)

    /** The spec, written out by hand rather than derived from the code. */
    private val table = mapOf(
        (ActionTier.AUTO to UserPolicy.ALLOW_ALWAYS) to Decision.ALLOW,
        (ActionTier.AUTO to UserPolicy.ASK) to Decision.ALLOW,
        (ActionTier.AUTO to UserPolicy.NEVER) to Decision.DENY,
        (ActionTier.NOTIFY to UserPolicy.ALLOW_ALWAYS) to Decision.ALLOW,
        (ActionTier.NOTIFY to UserPolicy.ASK) to Decision.ASK,
        (ActionTier.NOTIFY to UserPolicy.NEVER) to Decision.DENY,
        (ActionTier.CONFIRM to UserPolicy.ALLOW_ALWAYS) to Decision.ASK,
        (ActionTier.CONFIRM to UserPolicy.ASK) to Decision.ASK,
        (ActionTier.CONFIRM to UserPolicy.NEVER) to Decision.DENY
    )

    @Test
    fun `every combination matches the table`() {
        for (local in tiers) for (req in requested) for (policy in policies) {
            val effective = PolicyEngine.effectiveTier(local, req)
            assertEquals(
                "local=$local requested=$req policy=$policy",
                table[effective to policy],
                PolicyEngine.decide("some_action", local, req, policy)
            )
        }
    }

    @Test
    fun `requested tier can only raise`() {
        for (local in tiers) for (req in requested) {
            val effective = PolicyEngine.effectiveTier(local, req)
            assertTrue(
                "requested=$req lowered local=$local to $effective",
                effective.ordinal >= local.ordinal
            )
            if (req != null) assertTrue(effective.ordinal >= req.ordinal)
        }
    }

    @Test
    fun `tier 3 asks every time even when the user said allow always`() {
        for (local in tiers) for (req in requested) {
            if (PolicyEngine.effectiveTier(local, req) != ActionTier.CONFIRM) continue
            assertEquals(
                Decision.ASK,
                PolicyEngine.decide("send_sms", local, req, UserPolicy.ALLOW_ALWAYS)
            )
            assertEquals(Decision.ASK, PolicyEngine.decide("send_sms", local, req, UserPolicy.ASK))
            assertEquals(Decision.DENY, PolicyEngine.decide("send_sms", local, req, UserPolicy.NEVER))
        }
    }

    @Test
    fun `a tier 3 answer can never be remembered`() {
        assertFalse(PolicyEngine.canRemember(ActionTier.CONFIRM))
        assertFalse(PolicyEngine.canRemember(ActionTier.CONFIRM, TrustLevel.UNTRUSTED))
        assertTrue(PolicyEngine.canRemember(ActionTier.NOTIFY))
        assertTrue(PolicyEngine.canRemember(ActionTier.AUTO))
        // consent obtained while looking at injected content is not a standing rule
        assertFalse(PolicyEngine.canRemember(ActionTier.NOTIFY, TrustLevel.UNTRUSTED))
    }

    @Test
    fun `never always denies`() {
        for (local in tiers) for (req in requested) {
            for (enabled in listOf(true, false)) for (panic in listOf(true, false)) {
                for (trust in TrustLevel.values()) {
                    assertEquals(
                        Decision.DENY,
                        PolicyEngine.decide(
                            PolicyRequest("x", local, req, UserPolicy.NEVER, enabled, panic, trust)
                        )
                    )
                }
            }
        }
    }

    @Test
    fun `panic and the master switch deny everything`() {
        for (local in tiers) for (req in requested) for (policy in policies) {
            assertEquals(
                Decision.DENY,
                PolicyEngine.decide(PolicyRequest("x", local, req, policy, panic = true))
            )
            assertEquals(
                Decision.DENY,
                PolicyEngine.decide(PolicyRequest("x", local, req, policy, automationEnabled = false))
            )
        }
    }

    @Test
    fun `untrusted content is never auto-allowed`() {
        for (local in tiers) for (req in requested) for (policy in policies) {
            val outcome = PolicyEngine.decide(
                PolicyRequest("x", local, req, policy, trust = TrustLevel.UNTRUSTED)
            )
            assertNotEquals("untrusted $local/$req/$policy was auto-allowed", Decision.ALLOW, outcome)
            val trusted = PolicyEngine.decide("x", local, req, policy)
            assertEquals(if (trusted == Decision.ALLOW) Decision.ASK else trusted, outcome)
        }
    }

    @Test
    fun `a garbage tier field changes nothing`() {
        assertNull(ActionTier.fromWire(0))
        assertNull(ActionTier.fromWire(4))
        assertNull(ActionTier.fromWire(null))
        assertNull(ActionTier.fromWire(-1))
        for (local in tiers) for (policy in policies) {
            assertEquals(
                PolicyEngine.decide("x", local, null, policy),
                PolicyEngine.decide("x", local, ActionTier.AUTO, policy)
            )
        }
    }

    @Test
    fun `wire tiers map to the right severity`() {
        assertEquals(ActionTier.AUTO, ActionTier.fromWire(1))
        assertEquals(ActionTier.NOTIFY, ActionTier.fromWire(2))
        assertEquals(ActionTier.CONFIRM, ActionTier.fromWire(3))
        assertEquals(1, ActionTier.AUTO.wire)
        assertEquals(3, ActionTier.CONFIRM.wire)
    }

    @Test
    fun `corrupt stored policy fails closed to ask`() {
        assertEquals(UserPolicy.ASK, UserPolicy.fromStored(null))
        assertEquals(UserPolicy.ASK, UserPolicy.fromStored(""))
        assertEquals(UserPolicy.ASK, UserPolicy.fromStored("¯\\_(ツ)_/¯"))
        assertEquals(UserPolicy.ALLOW_ALWAYS, UserPolicy.fromStored("allow_always"))
        assertEquals(UserPolicy.NEVER, UserPolicy.fromStored(" never "))
    }

    @Test
    fun `the store refuses to remember allow-always for tier 3`() {
        val store = InMemoryPolicyProvider()
        store.remember("send_sms", UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM)
        assertEquals(UserPolicy.ASK, store.policyFor("send_sms"))

        store.remember("set_alarm", UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY)
        assertEquals(UserPolicy.ALLOW_ALWAYS, store.policyFor("set_alarm"))

        // NEVER is always storable, at any tier.
        store.remember("send_sms", UserPolicy.NEVER, ActionTier.CONFIRM)
        assertEquals(UserPolicy.NEVER, store.policyFor("send_sms"))
        assertEquals(
            Decision.DENY,
            PolicyEngine.decide("send_sms", ActionTier.CONFIRM, null, store.policyFor("send_sms"))
        )
    }

    @Test
    fun `explain mentions the raise and the outcome`() {
        val request = PolicyRequest(
            "open_url", ActionTier.AUTO, ActionTier.CONFIRM, UserPolicy.ALLOW_ALWAYS
        )
        val text = PolicyEngine.explain(request, PolicyEngine.decide(request))
        assertTrue(text, text.contains("open_url"))
        assertTrue(text, text.contains("raised by server"))
        assertTrue(text, text.contains("effective=CONFIRM"))
        assertTrue(text, text.contains("ASK"))
    }
}
