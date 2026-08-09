package ai.jarvis.app.companion

import ai.jarvis.app.assist.TtsPlayer
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import android.app.Activity
import android.util.Log
import java.lang.ref.WeakReference

/**
 * The seam a visible Jarvis surface fills in so a proactive message can be
 * *spoken* rather than merely posted.
 *
 * ```kotlin
 * // in MainActivity / JarvisAssistActivity
 * private var speechHost: OrbSpeechHost? = null
 *
 * override fun onResume() {
 *     super.onResume()
 *     speechHost = OrbSpeechHost(this, orbView, config)
 *         .also { CompanionMessageHandler.speechHost = it }
 * }
 * override fun onPause() {
 *     super.onPause()
 *     CompanionMessageHandler.clearSpeechHost(speechHost)
 *     speechHost?.stop()
 *     speechHost = null
 * }
 * ```
 *
 * With no host registered — the app is closed, or in the background — the
 * handler posts a notification that speaks when it is opened instead. That is
 * the whole reason this is a seam and not a static call into an activity: an
 * activity that is not on screen cannot speak, and pretending otherwise would
 * lose the message.
 */
interface CompanionSpeechHost {

    /** True only while this surface is actually in front of the user. */
    val isForeground: Boolean

    /**
     * Speak [text] through the orb.
     *
     * Returns false when this host cannot even try, in which case the caller
     * falls back to a notification. Otherwise it returns true and calls
     * [onDone] exactly once with whether the user actually heard it.
     */
    fun speak(text: String, onDone: (Boolean) -> Unit): Boolean
}

/**
 * The real one: jarvis-core synthesises, [TtsPlayer] plays, and the arc-reactor
 * orb goes to SPEAKING while it does.
 *
 * The voice is the server's, deliberately — the same Piper voice the assist
 * pipeline uses, so a proactive line sounds like Jarvis rather than like the
 * phone's default engine. [CompanionVoiceClient] asks for a `tts`-only run, so
 * the text jarvis-core sent never re-enters the conversation agent on its way
 * back out of the speaker.
 */
class OrbSpeechHost(
    activity: Activity,
    orb: JarvisOrbView,
    private val config: JarvisConfig,
) : CompanionSpeechHost {

    private val activityRef = WeakReference(activity)
    private val orbRef = WeakReference(orb)

    private var player: TtsPlayer? = null
    private var voice: CompanionVoiceClient? = null

    override val isForeground: Boolean
        get() {
            val activity = activityRef.get() ?: return false
            return !activity.isFinishing && !activity.isDestroyed
        }

    override fun speak(text: String, onDone: (Boolean) -> Unit): Boolean {
        val body = text.trim()
        if (body.isEmpty()) return false
        val activity = activityRef.get() ?: return false
        if (!config.isConfigured) return false

        val orb = orbRef.get()
        orb?.setMode(JarvisOrbView.Mode.SPEAKING)
        orb?.setStateLabel("JARVIS")

        fun restore() {
            orb?.setAmplitude(0f)
            orb?.setMode(JarvisOrbView.Mode.LISTENING)
            orb?.setStateLabel(IDLE_LABEL)
        }

        val client = CompanionVoiceClient(config.serverUrl, config.token)
        voice = client
        client.speak(body) { url ->
            if (url == null) {
                restore()
                stop()
                onDone(false)
                return@speak
            }
            val tts = TtsPlayer(activity, config.token, config.serverUrl).also { player = it }
            try {
                tts.play(url) {
                    restore()
                    stop()
                    onDone(true)
                }
            } catch (t: Throwable) {
                Log.w(TAG, "playback failed", t)
                restore()
                stop()
                onDone(false)
            }
        }
        return true
    }

    /** Drop the socket and the player. Safe to call more than once. */
    fun stop() {
        voice?.close()
        voice = null
        player?.stop()
        player = null
    }

    private companion object {
        private const val TAG = "JarvisOrbSpeech"

        /** What the orb caption goes back to once Jarvis has finished. */
        private const val IDLE_LABEL = "TAP TO SPEAK"
    }
}
