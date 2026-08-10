package ai.jarvis.app.assist

/**
 * Turning a stream of wake-word scores into "they said it", once.
 *
 * openWakeWord emits a probability roughly every 80 ms. Firing on the first
 * frame over a threshold gives a detector that trips on a cough and then trips
 * again on the next frame, and the next — so what the rest of the app sees is
 * not a score but a single edge, debounced and then held off.
 *
 * Three separate ideas, and they are separate because each has its own failure:
 *
 *  * **Threshold.** Too low and the television sets it off; too high and you
 *    have to say it twice. 0.5 is openWakeWord's own default and the right
 *    place to start arguing from.
 *  * **Consecutive frames.** A single frame over the line is usually a
 *    transient — a door, a consonant. Requiring two in a row costs 80 ms of
 *    latency and removes most of them.
 *  * **Refractory period.** After a detection the score stays high for as long
 *    as the phrase is still in the model's window, so without this one "hey
 *    Jarvis" produces a handful of detections. This is what makes the edge an
 *    edge.
 *
 * Deliberately free of Android and of ONNX: this is the half of on-device
 * detection that can be proved on a JVM, and it is mirrored in
 * `android-app/tools/wake_score_test.py`. The half that cannot — tensor shapes,
 * session lifetime — is kept as small as possible in [OnDeviceWakeWord].
 */
class WakeScore(
    /** Score above which a frame counts as the wake word. */
    private val threshold: Float = DEFAULT_THRESHOLD,
    /** Consecutive frames required before it fires. */
    private val framesToFire: Int = DEFAULT_FRAMES,
    /** Silence after a detection, in milliseconds. */
    private val refractoryMs: Long = DEFAULT_REFRACTORY_MS,
) {

    private var above = 0
    private var lastFiredAt = 0L

    /** Highest score seen since the last [reset], for diagnostics. */
    var peak: Float = 0f
        private set

    /**
     * Feed one frame's probability.
     *
     * @return true exactly once per utterance — on the frame that completes the
     *   run, and not again until the refractory period has passed.
     */
    fun onScore(nowMs: Long, score: Float): Boolean {
        if (score > peak) peak = score

        if (score < threshold) {
            // One frame below the line breaks the run. Not a decay: the run is
            // "consecutive frames", and treating a gap as a partial credit is
            // how a detector starts firing on rhythmic noise.
            above = 0
            return false
        }

        above++
        if (above < framesToFire) return false

        // The backwards-clock guard comes FIRST, and the order is load-bearing.
        //
        // A clock that jumped back — a manual time change, a snapshot restore —
        // makes `nowMs - lastFiredAt` negative, which is less than any
        // refractory period, so the check below would return false for as long
        // as it took to reach the old timestamp again. With uptimeMillis that is
        // however long the phone had been awake: hours of a wake word that
        // silently does nothing. Re-seeding here costs one missed detection and
        // bounds the damage at one refractory period.
        if (nowMs < lastFiredAt) {
            lastFiredAt = nowMs
            return false
        }

        // Still inside the tail of the last detection. The count deliberately
        // keeps climbing rather than resetting, so the moment the refractory
        // period ends on a genuinely new utterance it fires immediately —
        // resetting here would silently swallow the first phrase after it.
        if (lastFiredAt != 0L && nowMs - lastFiredAt < refractoryMs) return false

        lastFiredAt = nowMs
        above = 0
        return true
    }

    /** Forget everything. Called when capture stops and restarts. */
    fun reset() {
        above = 0
        lastFiredAt = 0L
        peak = 0f
    }

    companion object {
        /** openWakeWord's own default. */
        const val DEFAULT_THRESHOLD = 0.5f

        /** 80 ms per frame, so two is 160 ms of evidence. */
        const val DEFAULT_FRAMES = 2

        /**
         * Long enough that one "hey Jarvis" is one detection — the phrase stays
         * in the model's window for well over a second — and short enough that
         * asking twice in a row works.
         */
        const val DEFAULT_REFRACTORY_MS = 2_000L
    }
}
