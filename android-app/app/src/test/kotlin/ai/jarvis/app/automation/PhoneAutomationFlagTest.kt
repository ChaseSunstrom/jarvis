package ai.jarvis.app.automation

import ai.jarvis.app.BuildConfig
import ai.jarvis.app.automation.phone.PhoneAutomation
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Phone automation is off, and this is what says so.
 *
 * Driving the phone's other apps — reading their screens through an
 * accessibility service, reading notifications, injecting taps — is designed
 * and scaffolded and **not shipped**. The interfaces exist so the shape is
 * decided rather than improvised later; nothing behind them runs.
 *
 * The one thing a test can assert about a compile-time flag is what it is
 * compiled to, which is exactly what makes it worth asserting: a default that
 * drifts to `true` in a build file nobody reads is how a feature like this
 * ships by accident.
 */
class PhoneAutomationFlagTest {

    @Test
    fun `the flag is off in this build`() {
        assertFalse(
            "BuildConfig.PHONE_AUTOMATION is true. That turns on reading every " +
                "screen on this phone; it is not a default anything may change.",
            BuildConfig.PHONE_AUTOMATION,
        )
    }

    @Test
    fun `and the feature reports itself unavailable`() {
        assertFalse(PhoneAutomation.available)
    }

    @Test
    fun `a delegate set anyway is not handed back`() {
        // The getter is the belt to the flag's braces: even if something wires
        // an implementation in — a test, a fork, a mistake — nothing can reach
        // it while the flag is off.
        PhoneAutomation.delegate = object : PhoneAutomation {
            override fun capabilities() = listOf("ui_tap")
            override suspend fun readScreen(): PhoneAutomation.ScreenSnapshot? = null
            override suspend fun act(request: PhoneAutomation.Interaction) =
                PhoneAutomation.Outcome(false, "should never run")
        }
        try {
            assertNull(PhoneAutomation.delegate)
        } finally {
            PhoneAutomation.clearForTest()
        }
    }
}
