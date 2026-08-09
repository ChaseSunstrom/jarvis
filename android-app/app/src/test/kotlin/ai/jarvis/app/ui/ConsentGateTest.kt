package ai.jarvis.app.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Tier-3 consent screen used to render the verbatim parameters and accept
 * APPROVE over the keyguard (`setShowWhenLocked(true)` with no lock check), so
 * "a human approved it" meant "whoever was holding the phone approved it" — and
 * an SMS body, a phone number or a shell command was readable off a locked
 * screen. [ConsentGate] is the rule that fixed it; this is its spec.
 */
class ConsentGateTest {

    private val states = listOf(true, false)

    @Test
    fun `approve needs unlocked, armed and unanswered — all three`() {
        for (locked in states) {
            for (armed in states) {
                for (answered in states) {
                    val expected = !locked && armed && !answered
                    assertEquals(
                        "locked=$locked armed=$armed answered=$answered",
                        expected,
                        ConsentGate.approveEnabled(locked, armed, answered)
                    )
                }
            }
        }
    }

    @Test
    fun `a locked screen can never approve, however long it waits`() {
        assertFalse(ConsentGate.approveEnabled(locked = true, armed = true, answered = false))
    }

    @Test
    fun `deny works from a locked screen and from an unarmed one`() {
        assertTrue(ConsentGate.denyEnabled(answered = false))
        // Refusing is safe in every state; only answering twice is not.
        assertFalse(ConsentGate.denyEnabled(answered = true))
    }

    @Test
    fun `parameters are never rendered while locked`() {
        assertFalse(ConsentGate.paramsVisible(locked = true))
        assertTrue(ConsentGate.paramsVisible(locked = false))

        val secret = """{"to":"+441234567890","body":"the front door code is 1234"}"""
        val shown = ConsentGate.paramsText(locked = true, params = secret)
        assertEquals(ConsentGate.LOCKED_PARAMS, shown)
        assertFalse(shown.contains("441234567890"))
        assertFalse(shown.contains("1234"))
    }

    @Test
    fun `parameters are shown verbatim once unlocked`() {
        val params = """{"to":"+441234567890","body":"On my way"}"""
        assertEquals(params, ConsentGate.paramsText(locked = false, params = params))
        assertEquals(ConsentGate.NO_PARAMS, ConsentGate.paramsText(locked = false, params = ""))
        // Even "no parameters" stays hidden while locked — the absence of
        // parameters is itself information about what is being asked.
        assertEquals(ConsentGate.LOCKED_PARAMS, ConsentGate.paramsText(locked = true, params = ""))
    }

    @Test
    fun `the blocked reason explains the lock before it explains the delay`() {
        assertEquals(
            "Unlock this phone to see what it wants to do and to approve it.",
            ConsentGate.blockedReason(locked = true, armed = true)
        )
        assertEquals("Reading…", ConsentGate.blockedReason(locked = false, armed = false))
        assertEquals(null, ConsentGate.blockedReason(locked = false, armed = true))
    }

    @Test
    fun `the arming delay is long enough to notice and short enough to use`() {
        assertTrue(ConsentGate.ARM_MS in 250..2_000)
    }
}
