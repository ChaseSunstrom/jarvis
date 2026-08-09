package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.Harness
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.atomic.AtomicReference

/**
 * A real voice turn, end to end, against the real server.
 *
 * The one test here that talks to the jarvis-core harness rather than to a fake.
 * Everything between the microphone and the screen is production code and a real
 * socket:
 *
 * ```
 *   SyntheticSpeech -> MicStreamer -> JarvisConversation (energy VAD)
 *      -> AssistPipelineClient -> ws://harness/api/websocket
 *      -> auth -> assist_pipeline/pipeline/list -> assist_pipeline/run
 *      -> stt-end         -> MainActivity.onTranscript -> transcript view
 *      -> intent-progress -> MainActivity.onResponse   -> response view
 * ```
 *
 * ## The microphone
 *
 * An emulator has none. `AudioRecord` initialises happily and then returns
 * silence forever, so the energy VAD in `JarvisConversation` never sees speech,
 * never sends end-of-audio, and the pipeline never produces a transcript. There
 * is no way to test the voice path on an emulator without giving the app
 * something to hear, which is the entire reason `MicStreamer.debugPcmSource`
 * exists. It replaces the input device and nothing else: every byte still
 * travels the same path through the same client to the same socket.
 *
 * ## What the assertions actually prove
 *
 * They are about the shape of the round trip, not the words in it — this suite
 * does not own the harness's canned STT and LLM responses, and asserting them
 * would report an unrelated harness edit as an app regression. What is asserted
 * cannot happen by accident:
 *
 *  * A non-empty TRANSCRIPT can only come from an `stt-end` event, and the
 *    server only sends one after the app streamed audio frames prefixed with
 *    the run's `stt_binary_handler_id` AND sent the lone end-of-audio byte. One
 *    assertion covers the entire capture-and-stream path, including the VAD
 *    deciding the user stopped talking.
 *  * A non-empty RESPONSE can only come from `intent-progress` or `intent-end`,
 *    which happen after the pipeline ran.
 *  * Neither may be an error string, and the orb may not be in its ERROR state
 *    — otherwise "the response rendered" would be satisfied by the app
 *    rendering the words "connection error".
 *
 * Pass `-e jarvisExpectedTranscript <text>` to tighten the transcript assertion
 * to a substring match when the harness's canned phrase is known.
 *
 * ## Why the values are latched
 *
 * `JarvisConversation` is a multi-turn loop: when the next turn begins it clears
 * both views, and the synthetic microphone keeps talking. Polling and latching
 * the first non-empty value of each is not a workaround — reading once at the
 * end would be a race against the app doing exactly what it is supposed to do.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class ConversationE2ETest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Before
    fun pointTheAppAtTheHarness() {
        Harness.requireReachable()
        TestHooks.configure(
            context = context,
            serverUrl = Harness.baseUrl,
            token = Harness.token,
            pipeline = Harness.pipeline,
            deviceName = "instrumented-conversation-test",
        )
        TestHooks.feedSyntheticSpeech()
    }

    @Test
    fun speakingProducesATranscriptAndAStreamedResponse() {
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        // Tapped, not injected: the conversation starts through the same code
        // path the button always uses.
        tapTalk()

        val transcript = AtomicReference("")
        val response = AtomicReference("")

        Waits.until(
            "a transcript and a streamed response to render from ${Harness.baseUrl}",
            Waits.CONVERSATION_TIMEOUT_MS,
            pollMs = POLL_MS,
        ) {
            val turn = Activities.onMain { readTurn(main) }
            if (turn.transcript.isNotBlank()) transcript.compareAndSet("", turn.transcript)
            if (turn.response.isNotBlank()) response.compareAndSet("", turn.response)
            transcript.get().isNotBlank() && response.get().isNotBlank()
        }

        Screenshots.take("ConversationE2ETest-transcript-and-response")

        assertTrue(
            "The transcript must not be empty. Empty means the server never sent " +
                "stt-end, which means the audio never got there: check that the " +
                "synthetic microphone is installed and that a pipeline named " +
                "\"${Harness.pipeline}\" exists on the harness.",
            transcript.get().isNotBlank(),
        )
        assertTrue(
            "The response must not be empty; got \"${response.get()}\"",
            response.get().isNotBlank(),
        )

        for (text in listOf(transcript.get(), response.get())) {
            for (marker in ERROR_MARKERS) {
                assertFalse(
                    "The screen is showing an error, not a conversation: \"$text\"",
                    text.contains(marker, ignoreCase = true),
                )
            }
        }

        val label = Activities.onMain { stateLabel(main) }
        assertFalse(
            "The orb must not be in its ERROR state at the end of a successful turn " +
                "(it read \"$label\")",
            label.contains("ERROR", ignoreCase = true),
        )

        Harness.expectedTranscript()?.let { expected ->
            assertTrue(
                "Expected the transcript to contain \"$expected\"; it read " +
                    "\"${transcript.get()}\"",
                transcript.get().contains(expected, ignoreCase = true),
            )
        }
    }

    @Test
    fun theOrbLeavesIdleOnceTheServerAcceptsTheRun() {
        // The caption is the only feedback a user gets about what the app is
        // doing with an open microphone. Leaving TAP TO SPEAK is a real claim:
        // MainActivity.onMode is driven by AssistPipelineClient.onState, which
        // only reports LISTENING after `assist_pipeline/run` went out on an
        // authenticated socket.
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        tapTalk()

        Waits.until(
            "the orb to leave its idle state once the pipeline run starts",
            Waits.NETWORK_TIMEOUT_MS,
            pollMs = POLL_MS,
        ) {
            val label = Activities.onMain { stateLabel(main) }
            label.isNotBlank() && label != IDLE_LABEL
        }

        Screenshots.take("ConversationE2ETest-listening")

        val label = Activities.onMain { stateLabel(main) }
        assertFalse(
            "The orb must not go straight to ERROR; the harness at ${Harness.baseUrl} " +
                "either refused the token or is not speaking the pipeline protocol " +
                "(caption read \"$label\")",
            label.contains("ERROR", ignoreCase = true),
        )
    }

    // --- reading the screen -------------------------------------------------

    private data class Turn(val transcript: String, val response: String)

    /**
     * The transcript and the reply, read off the two views that carry them.
     *
     * Found structurally rather than by text, because the text is the thing
     * under test. `MainActivity.buildUi` puts exactly this in its controls
     * column, in this order: the banner slot (a `FrameLayout`), the transcript
     * (`TextView`), the response (`TextView`), the talk control (a `Button`) and
     * the nav row (a `LinearLayout`). So: find the talk button, take its parent,
     * and the plain `TextView` children of that parent — plain because `Button`
     * extends `TextView` and the talk control would otherwise be counted as a
     * transcript. MAIN THREAD ONLY.
     */
    private fun readTurn(main: MainActivity): Turn {
        val column = talkButton(main)?.parent as? LinearLayout ?: return Turn("", "")
        val plain = (0 until column.childCount)
            .map { column.getChildAt(it) }
            .filterIsInstance<TextView>()
            .filter { it !is Button }
        return Turn(
            transcript = plain.getOrNull(0)?.text?.toString().orEmpty(),
            response = plain.getOrNull(1)?.text?.toString().orEmpty(),
        )
    }

    /**
     * The orb's state caption.
     *
     * `JarvisOrbView` draws its own caption straight onto a `Canvas`, so there
     * is no accessibility node and no `TextView` for it. The talk button's label
     * tracks it one for one — see `MainActivity.onMode` — and that is a real
     * view. MAIN THREAD ONLY.
     */
    private fun stateLabel(main: MainActivity): String =
        talkButton(main)?.text?.toString()?.substringBefore('…')?.trim().orEmpty()

    private fun talkButton(main: MainActivity): Button? =
        Views.allOfType(main.window.decorView, Button::class.java)
            .firstOrNull { button ->
                val text = button.text?.toString().orEmpty()
                text == IDLE_LABEL || text.contains(TALKING_MARKER)
            }

    private fun tapTalk() {
        val button = Device.ui.findObject(By.text(Views.textIgnoringCase(IDLE_LABEL)))
        requireNotNull(button) {
            "No \"$IDLE_LABEL\" control on the home screen.\n${Device.windowDump()}"
        }.click()
    }

    private companion object {
        const val POLL_MS = 100L

        /** `MainActivity.showIdle` sets exactly this on both orb and button. */
        const val IDLE_LABEL = "TAP TO SPEAK"

        /** Present in every non-idle label: "LISTENING… (TAP TO STOP)" and friends. */
        const val TALKING_MARKER = "TAP TO STOP"

        /**
         * Substrings `JarvisConversation.onError` and `AssistPipelineClient` put
         * on screen. A test that only asserted "the response view is not empty"
         * would pass on every one of these.
         */
        val ERROR_MARKERS = listOf(
            "connection error",
            "auth failed",
            "refused a TTS URL",
        )
    }
}
