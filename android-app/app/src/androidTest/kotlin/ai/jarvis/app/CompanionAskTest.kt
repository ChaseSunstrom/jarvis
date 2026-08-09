package ai.jarvis.app

import ai.jarvis.app.channel.JarvisChannel
import ai.jarvis.app.companion.CompanionAskActivity
import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.FakeJarvisServer
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiObject2
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/**
 * Jarvis asks the user a question, and gets exactly one answer.
 *
 * The other direction of the device protocol: not the server telling the phone
 * to do something, but the server asking the person holding it something. One
 * inbound `jarvis_message` in, exactly one `jarvis_message_result` out.
 *
 * ```
 *   <- {"type":"jarvis_message","message_id":"m-1","kind":"ask","mode":"ask",
 *       "text":"Deploy to production?","options":["yes","no"]}
 *   -> {"type":"jarvis_message_result","message_id":"m-1",
 *       "status":"answered","answer":"no"}
 * ```
 *
 * ## Why "exactly one" is the interesting part
 *
 * The server redelivers anything it did not hear an answer to. If a redelivery
 * raised a second question, the user would be asked the same thing twice and the
 * server could receive two different answers to one question — and since only
 * `answered` stops it escalating, a second, contradictory reply would push a
 * question the user already dealt with onto another one of their devices.
 * `CompanionLedger` is what prevents that, and this test is what proves it on a
 * device: the redelivery replays the stored reply verbatim and prompts nobody.
 *
 * ## What an answer is NOT
 *
 * It is data. `CompanionProtocol.Message` has no field for an action, params or
 * a tier, and the handler imports neither the dispatcher nor the policy store —
 * so answering "yes" cannot itself run anything. Acting on that answer comes
 * back as a `device_command` with the full Tier-1/2/3 treatment, which is what
 * `ConsentGateTest` covers.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class CompanionAskTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var server: FakeJarvisServer
    private lateinit var channel: JarvisChannel

    @Before
    fun connect() {
        server = FakeJarvisServer().start()
        TestHooks.configure(context, server.baseUrl, server.expectedToken)

        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        channel = TestHooks.startChannel(context)
        server.awaitRegistration()
        Waits.until("the channel to reach READY") {
            channel.status.value.state == JarvisChannel.State.READY
        }
    }

    @After
    fun disconnect() {
        TestHooks.stopChannel(context)
        server.close()
    }

    @Test
    fun aQuestionRendersAndTappingAnOptionSendsThatExactAnswer() {
        val messageId = "msg-${UUID.randomUUID()}"
        val ask = sendAndAwaitQuestion(messageId)

        assertOnScreen("JARVIS ASKS", "the screen must say who is asking")
        assertOnScreen(QUESTION, "the question text must be rendered verbatim")
        for (option in OPTIONS) {
            assertOnScreen(option, "every option the server offered must be tappable")
        }

        Screenshots.take("CompanionAskTest-question")

        // The option buttons stay inert for CompanionAskGate.ARM_MS after the
        // question becomes readable, so a tap already in flight — or an overlay
        // timing one — cannot land on a control that only just appeared. Wait
        // for the gate to open rather than hammering the button.
        val chosen = OPTIONS[1]
        val button = Waits.untilPresent("the \"$chosen\" option to become answerable") {
            findByText(chosen)?.takeIf { it.isEnabled }
        }
        button.click()

        Activities.awaitFinished(ask)

        val result = server.awaitMessageResult(messageId)
        assertEquals(
            "The reply must answer the question it was sent for",
            messageId,
            result.optString("message_id"),
        )
        assertEquals(
            "Tapping an option is a person answering, which is the only thing that " +
                "produces `answered`. Frame: $result",
            "answered",
            result.optString("status"),
        )
        assertEquals(
            "…and the answer must be the exact option string the server offered, " +
                "not the button's display text. JarvisUi.pill upper-cases its label " +
                "for display only; sending \"${chosen.uppercase()}\" back would be a " +
                "different answer to the one the server can match.",
            chosen,
            result.optString("answer"),
        )

        Screenshots.take("CompanionAskTest-answered")
    }

    @Test
    fun aDuplicateDeliveryReplaysTheAnswerAndAsksNobodyAgain() {
        val messageId = "msg-${UUID.randomUUID()}"
        val ask = sendAndAwaitQuestion(messageId)

        val chosen = OPTIONS[0]
        Waits.untilPresent("the \"$chosen\" option to become answerable") {
            findByText(chosen)?.takeIf { it.isEnabled }
        }.click()
        Activities.awaitFinished(ask)

        val first = server.awaitMessageResult(messageId)
        assertEquals("answered", first.optString("status"))
        assertEquals(chosen, first.optString("answer"))

        // The redelivery. The socket may have died between the answer and the
        // server reading it, so this is normal traffic and not an attack — but
        // it must not reach a human.
        Activities.assertDoesNotStart(
            CompanionAskActivity::class.java,
            window = NO_SECOND_PROMPT_WINDOW_MS,
            what = "a redelivered question must be answered from the ledger, not " +
                "asked again — the user already dealt with it",
        ) {
            server.sendCompanionMessage(
                messageId = messageId,
                text = QUESTION,
                options = OPTIONS,
            )
        }

        val results = server.awaitMessageResultCount(messageId, count = 2)
        assertEquals(
            "The redelivery must be answered, not ignored: silence leaves the server " +
                "waiting on a reply that will never come. Frames: $results",
            2,
            results.size,
        )
        assertEquals(
            "…and it must be the SAME reply, replayed verbatim. A second, different " +
                "answer would resolve the question differently on the server and could " +
                "push it onto another device.",
            results[0].toString(),
            results[1].toString(),
        )
        assertEquals("answered", results[1].optString("status"))
        assertEquals(chosen, results[1].optString("answer"))

        Screenshots.take("CompanionAskTest-duplicate-delivery")
    }

    @Test
    fun dismissingReportsDismissedSoTheServerCanEscalate() {
        // "Not now" is not a refusal of the question, it is a statement about
        // this device. The server reads `dismissed` as "ask somewhere else",
        // which is why the control stays live in states where answering does not.
        val messageId = "msg-${UUID.randomUUID()}"
        val ask = sendAndAwaitQuestion(messageId)

        Screenshots.take("CompanionAskTest-before-dismiss")

        Waits.untilPresent("the NOT NOW control") { findByText("NOT NOW") }.click()
        Activities.awaitFinished(ask)

        val result = server.awaitMessageResult(messageId)
        assertEquals(
            "Dismissing must report `dismissed`, never `answered` — `answered` is " +
                "the only status that stops the server escalating, and nobody " +
                "answered. Frame: $result",
            "dismissed",
            result.optString("status"),
        )
        assertTrue(
            "…and a dismissal must not smuggle an answer along with it",
            result.optString("answer").isEmpty(),
        )
    }

    @Test
    fun backReportsSomethingRatherThanGoingQuiet() {
        val messageId = "msg-${UUID.randomUUID()}"
        val ask = sendAndAwaitQuestion(messageId)

        Device.ui.pressBack()
        Activities.awaitFinished(ask)

        val result = server.awaitMessageResult(messageId)
        assertEquals(
            "Back must report `dismissed`. Every exit reports something, because a " +
                "question nobody answers on this device has to move on rather than " +
                "sit there. Frame: $result",
            "dismissed",
            result.optString("status"),
        )
    }

    // --- helpers ------------------------------------------------------------

    /**
     * Send a `jarvis_message` and return the question screen it raised.
     *
     * The monitor goes in before the frame does. `CompanionMessageHandler`
     * admits the message on the socket reader thread and posts straight to the
     * main thread, so the activity can exist before an after-the-fact poll looks
     * for it.
     */
    private fun sendAndAwaitQuestion(messageId: String): CompanionAskActivity {
        val ask = Activities.expect(
            CompanionAskActivity::class.java,
            timeoutMs = Waits.NETWORK_TIMEOUT_MS,
        ) {
            server.sendCompanionMessage(
                messageId = messageId,
                text = QUESTION,
                options = OPTIONS,
                // `normal`, not `high`: CompanionAskGate hides the text of a
                // high/critical question behind "Jarvis has a question" until
                // the phone is unlocked, and this test is about the question
                // rendering, not about the keyguard.
                importance = "normal",
            )
        }
        Activities.awaitResumed(ask)
        Waits.until("the question screen to render") {
            findByText("NOT NOW") != null
        }
        return ask
    }

    /** Scroll-aware: the question screen is a ScrollView and options may stack. */
    private fun findByText(text: String): UiObject2? =
        Views.findScrolling(By.text(Views.textIgnoringCase(text)))

    private fun assertOnScreen(text: String, why: String) {
        Waits.until("\"$text\" to appear on the question screen — $why") {
            Views.findScrolling(By.text(Views.containingIgnoringCase(text))) != null
        }
    }

    private companion object {
        const val QUESTION = "Deploy the release build to production?"

        /**
         * Lower case on purpose. `JarvisUi.pill` sets `isAllCaps`, which is a
         * DISPLAY transformation — the accessibility node still carries the
         * original string — and the answer that goes back on the wire must be
         * the option the server offered, character for character.
         */
        val OPTIONS = listOf("yes", "not right now")

        /**
         * How long to watch for a second question screen after a redelivery.
         * A real wait, because there is no event for the absence of an activity;
         * kept short because the handler's admission decision is taken inline on
         * the reader thread, so a second prompt would appear almost immediately.
         */
        const val NO_SECOND_PROMPT_WINDOW_MS = 5_000L
    }
}
