package ai.jarvis.app.companion

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.assist.TtsPlayer
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * A [CompanionSpeechHost] backed by a conversation that is already on screen.
 *
 * ## What this is for
 *
 * Jarvis asking you something used to close whatever you were talking to. The
 * question arrives out of band on the companion channel, and the only surface
 * that could present it was [CompanionAskActivity] — a full-screen activity,
 * started with `FLAG_ACTIVITY_NEW_TASK`. Starting it over the wake-word orb
 * took the orb down; answering it took the activity down too; and what the user
 * was left with was a phone that had silently ended their conversation twice in
 * a row.
 *
 * With one of these registered, the question is asked where the user is already
 * looking. The orb says it, listens for the answer, and goes back to the
 * conversation it interrupted.
 *
 * ## The sequence, and why each step is where it is
 *
 * ```
 *   hold the conversation      mic released; turn loop stopped; running stays true
 *     speak the question       tts-only run, so the text never re-enters the agent
 *     listen for the answer    stt-only run, so the answer is never dispatched
 *   resume the conversation    mic back, VAD floor re-measured, next turn begins
 * ```
 *
 * The hold is what keeps the surface alive: `running` stays true throughout, so
 * the inactivity timer cannot fire `onIdle` and pull the orb out from under a
 * question nobody has answered yet.
 *
 * Both legs are single-stage runs, and that is the point — a question spoken
 * back through the agent would be answered by the agent, and an answer sent
 * through the agent would be *executed*. "No, delete them" is a reply to a
 * question and must never be a command.
 *
 * ## Failure
 *
 * Every path calls back exactly once, and every failure calls back with null so
 * the ledger settles and the server can escalate to a notification. Nothing
 * here can leave a question in flight: if the socket dies, the microphone is
 * refused, or nothing intelligible is said, the conversation still gets its
 * microphone back and the caller still gets an answer.
 */
class ConversationAskHost(
    private val context: Context,
    private val config: JarvisConfig,
    private val conversation: () -> JarvisConversation?,
    /** Draw the question and the orb's state on whatever surface this is. */
    private val surface: Surface,
) : CompanionSpeechHost {

    /** The bit of the on-screen surface this host needs to drive. */
    interface Surface {
        fun onMode(mode: JarvisOrbView.Mode, label: String)
        fun onAmplitude(level: Float)

        /** The question, rendered as Jarvis's line. */
        fun onQuestion(text: String)

        /** What the user is saying back, as it is transcribed. */
        fun onAnswerTranscript(text: String)

        /**
         * Go back to whatever this surface looks like when nothing is
         * happening.
         *
         * The surface picks the wording, not this host. A screen that also has
         * a talk button must not be told to say "TAP TO SPEAK" next to it —
         * `MainActivity.showIdle` exists to avoid exactly that duplicate
         * affordance, and a constant here would reintroduce it on every screen
         * at once.
         */
        fun onResting()

        /** True only while this surface is actually in front of the user. */
        val isShowing: Boolean
    }

    private val main = Handler(Looper.getMainLooper())
    private var voice: CompanionVoiceClient? = null
    private var player: TtsPlayer? = null

    override val isForeground: Boolean
        get() = surface.isShowing && conversation()?.isRunning == true

    override fun speak(text: String, onDone: (Boolean) -> Unit): Boolean {
        val body = text.trim()
        if (body.isEmpty() || !isForeground || !config.isConfigured) return false
        val convo = conversation() ?: return false
        if (!convo.holdForQuestion()) return false

        say(body, onFinished = {
            convo.resumeAfterQuestion()
            onDone(it)
        })
        return true
    }

    override fun ask(question: String, onAnswer: (String?) -> Unit): Boolean {
        val body = question.trim()
        if (body.isEmpty() || !isForeground || !config.isConfigured) return false
        val convo = conversation() ?: return false
        if (!convo.holdForQuestion()) return false

        // One outcome, whatever happens after this point. The conversation is
        // owed its microphone back in every branch — answered, dismissed, timed
        // out or failed — and the caller is owed exactly one settle.
        var settled = false
        fun finish(answer: String?) {
            if (settled) return
            settled = true
            release()
            main.post {
                surface.onResting()
                convo.resumeAfterQuestion()
                onAnswer(answer)
            }
        }

        main.post { surface.onQuestion(body) }
        say(body, onFinished = { heard ->
            if (!heard) {
                // The question was never audible. Falling through to listening
                // would record an answer to something nobody was asked.
                Log.w(TAG, "could not speak the question; handing it back")
                finish(null)
                return@say
            }
            listen(::finish)
        })
        return true
    }

    /** Speak [text] with the server's voice; [onFinished] says whether it played. */
    private fun say(text: String, onFinished: (Boolean) -> Unit) {
        main.post { surface.onMode(JarvisOrbView.Mode.SPEAKING, "JARVIS") }
        val client = CompanionVoiceClient(config.serverUrl, config.token, config.serverKind)
        voice = client
        client.speak(text) { url ->
            if (url == null) {
                onFinished(false)
                return@speak
            }
            val tts = TtsPlayer(context, config.token, config.serverUrl).also { player = it }
            try {
                tts.play(url) { onFinished(true) }
            } catch (t: Throwable) {
                Log.w(TAG, "playback of the question failed", t)
                onFinished(false)
            }
        }
    }

    /** Take a spoken answer. Never reaches the conversation agent. */
    private fun listen(onText: (String?) -> Unit) {
        main.post { surface.onMode(JarvisOrbView.Mode.LISTENING, "LISTENING") }
        val client = CompanionVoiceClient(config.serverUrl, config.token, config.serverKind)
        voice = client
        client.listen(
            onLevel = { level -> surface.onAmplitude(level) },
            onText = { text ->
                main.post { surface.onAnswerTranscript(text.orEmpty()) }
                onText(text)
            },
        )
        // The answer has to end on its own: nobody is holding a button, and the
        // client streams until it is told the user stopped. A cap rather than a
        // VAD here on purpose — the conversation's own VAD is measured against
        // this room and this host does not have it, and a question that hangs
        // forever because a passing lorry kept the floor up is worse than one
        // that gives the user a fixed window to answer in.
        main.postDelayed({ client.endAudio() }, ANSWER_WINDOW_MS)
    }

    /** Drop the socket and the player. Safe to call more than once. */
    fun stop() {
        release()
        conversation()?.takeIf { it.isHeldForQuestion }?.resumeAfterQuestion()
    }

    private fun release() {
        main.removeCallbacksAndMessages(null)
        voice?.close()
        voice = null
        player?.stop()
        player = null
    }

    private companion object {
        const val TAG = "JarvisAskHost"

        /**
         * How long the user has to answer out loud before the capture is
         * closed and whatever was said is transcribed.
         */
        const val ANSWER_WINDOW_MS = 8_000L
    }
}
