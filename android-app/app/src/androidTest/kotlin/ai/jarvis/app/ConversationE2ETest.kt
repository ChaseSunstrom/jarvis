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
 *  * Neither may be an error string — otherwise "the response rendered" would be
 *    satisfied by the app rendering the words "connection error".
 *
 * ## The orb's caption is deliberately NOT the error assertion
 *
 * `JarvisOrbView` draws its caption onto a `Canvas` and exposes no getter, so
 * the only readable proxy is the talk button's label. That label is NOT a proxy
 * for the error state: `MainActivity.onError` sets `orbView.setStateLabel(
 * "ERROR")` and leaves the button alone, and `MainActivity.onMode` — the only
 * thing that writes the button — is ever called with just LISTENING, PROCESSING
 * and RESPONDING. An assertion that the button label does not contain "ERROR"
 * therefore cannot fail on any build, which is worse than no assertion at all.
 * The error sink that IS readable is `responseView`, which `onError` writes, and
 * that is what this test asserts against.
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
        // Checked, not assumed. `MainActivity.toggleTalk` puts up the system
        // permission dialog instead of starting a conversation when this is
        // missing, and the symptom — no transcript for ninety seconds — points
        // at the server rather than at the phone.
        Device.requireGranted(android.Manifest.permission.RECORD_AUDIO)
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

        // Nothing is tapped. The home screen opens its microphone on resume
        // now — there is no talk button to press — so being resumed IS the
        // start of the conversation, and that is the code path under test.

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

        // The latched values, AND whatever is on the response view right now —
        // `MainActivity.onError` writes that view, so an error raised after the
        // reply rendered would otherwise pass unnoticed.
        val finalResponse = Activities.onMain { readTurn(main).response }
        for (text in listOf(transcript.get(), response.get(), finalResponse)) {
            for (marker in ERROR_MARKERS) {
                assertFalse(
                    "The screen is showing an error, not a conversation: \"$text\"",
                    text.contains(marker, ignoreCase = true),
                )
            }
        }

        Harness.expectedTranscript()?.let { expected ->
            assertTrue(
                "Expected the transcript to contain \"$expected\"; it read " +
                    "\"${transcript.get()}\"",
                transcript.get().contains(expected, ignoreCase = true),
            )
        }
    }

    @Test
    fun theServerAcceptsTheRunAndTheTranscriptRenders() {
        // The cheap half of the round trip, asserted on the FIRST thing on
        // screen that only the server can cause.
        //
        // Deliberately not the mute pill's label, which says LISTENING as soon
        // as the screen resumes — before a socket is even opened — so it would
        // be satisfied by the activity launching and would pass against a
        // harness that refused the token.
        // A transcript cannot appear without `stt-end`, and `stt-end` cannot
        // arrive without auth_ok, a resolved pipeline, an accepted
        // `assist_pipeline/run`, audio frames prefixed with the run's
        // stt_binary_handler_id, and the end-of-audio byte.
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        val transcript = AtomicReference("")
        Waits.until(
            "a transcript to render from ${Harness.baseUrl}. Empty means the run was " +
                "never accepted: the harness refused the token, has no pipeline named " +
                "\"${Harness.pipeline}\", or is not speaking the pipeline protocol.",
            Waits.CONVERSATION_TIMEOUT_MS,
            pollMs = POLL_MS,
        ) {
            val turn = Activities.onMain { readTurn(main) }
            if (turn.transcript.isNotBlank()) transcript.compareAndSet("", turn.transcript)
            transcript.get().isNotBlank()
        }

        Screenshots.take("ConversationE2ETest-transcript")

        // `MainActivity.onError` writes the response view — the only readable
        // error sink on this screen. See the class header for why the orb's
        // caption is not used here.
        val response = Activities.onMain { readTurn(main).response }
        for (marker in ERROR_MARKERS) {
            assertFalse(
                "The pipeline reported an error rather than running: \"$response\"",
                response.contains(marker, ignoreCase = true),
            )
            assertFalse(
                "The transcript is an error string, not speech: \"${transcript.get()}\"",
                transcript.get().contains(marker, ignoreCase = true),
            )
        }
    }

    // --- reading the screen -------------------------------------------------

    private data class Turn(val transcript: String, val response: String)

    /**
     * The transcript and the reply, read off the two views that carry them.
     *
     * Found structurally rather than by text, because the text is the thing
     * under test. `MainActivity.buildUi` puts these in its controls column, in
     * this order: the banner slot (a `FrameLayout`), the tool activity view,
     * the activity strip and the knowledge graph (none of them a `TextView`),
     * the transcript (`TextView`), the response (`TextView`), the mute (a
     * `Button`), the listen control and its reason line, and the nav row. So:
     * find the mute button, take its parent, and the first two plain `TextView`
     * children of that parent — plain because `Button` extends `TextView` and
     * the controls would otherwise be counted as a transcript. A `TextView`
     * inserted ABOVE the transcript would silently shift this read; new text
     * goes below the mute. MAIN THREAD ONLY.
     */
    private fun readTurn(main: MainActivity): Turn {
        val column = muteButton(main)?.parent as? LinearLayout ?: return Turn("", "")
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
     * The mute pill, whatever it currently says.
     *
     * All four of its labels, because they have no common substring — the two
     * mute states share "TAP TO" and the two can't-listen states share nothing
     * with them. Only ever used to FIND the controls column, never to assert
     * anything, so a label added here and not there costs a clear
     * "no mute control" failure rather than a wrong reading.
     */
    private fun muteButton(main: MainActivity): Button? =
        Views.allOfType(main.window.decorView, Button::class.java)
            .firstOrNull { button ->
                val text = button.text?.toString().orEmpty()
                MUTE_LABELS.any { text.contains(it, ignoreCase = true) }
            }

    private companion object {
        const val POLL_MS = 100L

        /**
         * Every label `MainActivity.refreshMuteButton` can set. Used only to
         * locate the controls column; see [muteButton].
         */
        val MUTE_LABELS = listOf(
            "TAP TO MUTE",
            "TAP TO LISTEN",
            "SET UP JARVIS",
            "GRANT THE MICROPHONE",
        )

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
