package ai.jarvis.app.assist

/**
 * Notices a microphone that is open, healthy, and delivering nothing.
 *
 * `AudioRecord` has two ways to fail and only one of them is visible. It can
 * refuse to open — which [MicStreamer] already reports — or it can open,
 * `read()` happily, and hand back frames of digital zero forever. The second is
 * what Android does when a while-in-use foreground service (microphone, camera,
 * location) was started while the app was in the background: no exception, no
 * error callback, just silence. It is also what a GrapheneOS per-app *Sensors*
 * toggle looks like, and what a phone whose mic another app has muted looks
 * like.
 *
 * Without this, all three are indistinguishable from a quiet room, and the
 * always-on listener sits there with a notification saying "Jarvis is
 * listening" while nothing can ever reach it.
 *
 * Pure arithmetic over (timestamp, level) so it is testable on the JVM; mirrored
 * in `android-app/tools/mic_silence_test.py`.
 */
class MicSilenceWatch(
    /** How long a run of digital silence has to last before it is a fault. */
    private val mutedAfterMs: Long = MUTED_AFTER_MS,
) {

    /** Wall clock at which the current run of silence began; 0 = not in one. */
    private var silentSince = 0L

    /** True once this run of silence has been reported. */
    private var reported = false

    /** True while the microphone is believed to be producing nothing. */
    val muted: Boolean get() = reported

    /**
     * Feed one frame's smoothed level.
     *
     * @return true exactly once per run of silence — on the frame that crosses
     *   [mutedAfterMs]. Callers use that edge to raise the notification without
     *   re-raising it sixty times a second.
     */
    fun onLevel(nowMs: Long, level: Float): Boolean {
        // `!=` rather than `>`: the test is "is this arithmetically zero", so a
        // negative level — which would be a caller bug, not a mute — must reset
        // the run rather than extend it.
        if (level != DIGITAL_SILENCE) {
            reset()
            return false
        }
        if (silentSince == 0L) {
            // Seed on the first silent frame rather than reporting immediately:
            // a conversation that starts in a quiet room is not a fault.
            silentSince = nowMs
            return false
        }
        if (reported) return false
        // A clock that went backwards (a manual time change, an emulator
        // snapshot) must not be read as "silent since the epoch": re-seed
        // instead of firing.
        if (nowMs < silentSince) {
            silentSince = nowMs
            return false
        }
        if (nowMs - silentSince < mutedAfterMs) return false
        reported = true
        return true
    }

    /** Back to believing the microphone works. Called on any real audio. */
    fun reset() {
        silentSince = 0L
        reported = false
    }

    companion object {
        /**
         * Exactly zero, and not a small threshold.
         *
         * `JarvisConversation` uses 0.0005 to tell a dead mic from a quiet one,
         * but it only has to hold that judgement for a few seconds of one
         * conversation. This watch runs for hours in whatever room the phone is
         * in, and a quiet room's RMS genuinely does sit near that figure — a
         * threshold here would cry wolf every night. A *muted* recorder is not
         * quiet, it is arithmetically zero: `AudioRecord` hands back buffers of
         * zeroes, `rms16` returns 0f, and the caller's exponential smoother
         * keeps returning 0f because it is multiplying zero by a constant. Any
         * real microphone, in any real room, breaks that within a frame or two.
         */
        const val DIGITAL_SILENCE = 0f

        /**
         * Long enough that nothing transient — a phone call, a moment of the
         * recorder warming up — is mistaken for a mute, short enough that the
         * user learns about it the same evening.
         */
        const val MUTED_AFTER_MS = 90_000L

        /** What the notification says when [muted] goes true. */
        const val MUTED_MESSAGE =
            "The microphone is open but hearing nothing. Tap to restart it."
    }
}
