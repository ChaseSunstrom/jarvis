package ai.jarvis.app.companion

/**
 * The seam a visible Jarvis surface fills in so a proactive message can be
 * *spoken*, and a question *asked*, rather than merely posted.
 *
 * The one implementation is [ConversationAskHost], and every surface that owns
 * a [ai.jarvis.app.assist.JarvisConversation] registers one:
 *
 * ```kotlin
 * private var askHost: ConversationAskHost? = null
 *
 * // where the conversation starts
 * askHost = ConversationAskHost(this, config, { convo }, askSurface)
 *     .also { CompanionMessageHandler.speechHost = it }
 *
 * // where it ends
 * askHost?.let { CompanionMessageHandler.clearSpeechHost(it); it.stop() }
 * askHost = null
 * ```
 *
 * With no host registered — the app is closed, or in the background — the
 * handler posts a notification, and a question falls back to
 * [CompanionAskActivity]. That is the whole reason this is a seam and not a
 * static call into an activity: an activity that is not on screen cannot
 * speak, and pretending otherwise would lose the message.
 *
 * **This seam sat empty for its whole life.** It was written, documented and
 * tested, and nothing ever constructed an implementation — so every proactive
 * line became a notification and every question took over the screen, which is
 * the designed *fallback* behaviour and therefore looked deliberate.
 * `tools/speech_host_test.py` now fails if the slot goes unfilled again.
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

    /**
     * Put a question to the user **on the surface that is already up**, and
     * hand back what they said.
     *
     * The behaviour this exists to fix: `ask_user` arrives out of band while a
     * conversation is on screen, and the only way to present it was
     * [CompanionAskActivity] — a separate full-screen surface. Starting it tore
     * down whatever was there. Reported as: the wake-word orb closes when
     * Jarvis asks something, and closes again when you answer, instead of the
     * conversation carrying on.
     *
     * A host that implements this speaks the question where the user is
     * already looking, takes the answer by voice, and puts the microphone back
     * where it found it.
     *
     * Two things it must get right, both of which the fallback activity also
     * gets right and which are easy to lose here:
     *
     *  * **The answer is not a command.** "No, delete them" is a reply, and
     *    running it through the conversation agent would execute it. The
     *    capture has to be an `end_stage: "stt"` run — see
     *    [CompanionVoiceClient] — not another conversation turn.
     *  * **Exactly one outcome.** [onAnswer] is called once, with the
     *    transcript, or with null for dismissed/timed out/failed. The caller's
     *    ledger settles on it and a second call would answer a question that
     *    has already been answered.
     *
     * @return false when this surface cannot take a spoken answer right now,
     *   in which case the caller falls back to the activity and the
     *   notification exactly as before. Defaulted to false so a host that has
     *   nowhere to put a question stays a valid host.
     */
    fun ask(question: String, onAnswer: (String?) -> Unit): Boolean = false
}
