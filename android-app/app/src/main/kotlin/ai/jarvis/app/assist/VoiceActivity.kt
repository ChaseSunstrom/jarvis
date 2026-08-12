package ai.jarvis.app.assist

/**
 * When someone started talking, and when they stopped.
 *
 * Pure logic, no Android, so the decision that governs every turn can be
 * exercised on a JVM and mirrored in `android-app/tools/voice_activity_test.py`.
 *
 * ## Why this exists rather than two constants
 *
 * It replaces a fixed pair of thresholds, and both values that pair ever held
 * were wrong in opposite directions — which is the tell that no fixed pair can
 * be right.
 *
 * At 0.02 / 0.01 the start edge sat at the TOP of the range conversational
 * speech reaches through an unprocessed phone mic, so you had to be close to
 * the phone or raise your voice. Lowering it ten times, to 0.002 / 0.001, put
 * the END edge at or below the noise floor of an ordinary room — a fan, a
 * laptop, traffic. The consequences were worse than the original complaint and
 * much harder to see:
 *
 *  * the hangover could never elapse, because a level that never drops below
 *    the end edge keeps refreshing "I last heard speech just now", so every
 *    turn ran to the 30-second cap;
 *  * thirty seconds is the worst possible length to hand a Whisper backend: it
 *    exactly fills the model's window with about twenty-eight seconds of room
 *    noise, which produces empty text or invention;
 *  * and the start edge latched on the room itself, which disarmed the
 *    inactivity timeout and deleted every diagnostic the conversation had for
 *    telling a dead microphone from a quiet one.
 *
 * The room is the problem, so the room is the reference. [floor] tracks the
 * quietest the room has recently been, and both edges are multiples of it. A
 * silent study and a kitchen with an extractor fan get the same *ratio* of
 * speech to background, which is the thing that actually distinguishes a voice
 * from a room.
 *
 * ## How the floor is tracked
 *
 * A leaky minimum: it follows the level down immediately and is allowed to
 * creep up only slowly, so a passing lorry raises it over seconds rather than
 * instantly, and a room going quiet is noticed at once.
 *
 * It is **frozen while speech is latched**. Without that, a long sentence drags
 * the floor up behind it and the speaker talks themselves over their own end
 * edge — the turn would end mid-word on a rising floor.
 *
 * ## The absolute minimums
 *
 * [MIN_START] and [MIN_END] are a lower bound on the edges, not the edges. In a
 * genuinely silent room the floor approaches zero, and a pure ratio would then
 * make any sound at all count as speech — including the recorder's own dither.
 */
class VoiceActivity(
    /** Speech is this many times the room. */
    private val startRatio: Float = START_RATIO,
    /** Silence is anything below this many times the room. */
    private val endRatio: Float = END_RATIO,
    private val minStart: Float = MIN_START,
    private val minEnd: Float = MIN_END,
    /** How fast the floor may creep upward, per capture buffer. */
    private val floorRise: Float = FLOOR_RISE_PER_CHUNK,
    /**
     * True when the caller knows the user is ALREADY talking — a wake-word
     * turn. See [seeded]; it is the difference between hearing the command
     * after the name and hearing nothing at all.
     */
    private val speechAlreadyUnderway: Boolean = false,
    /**
     * How long a wake-word turn may go unseeded before the room is inferred
     * from what has been heard rather than waited for. See the seeding branch
     * in [onLevel].
     */
    private val seedWindowMs: Long = SEED_WINDOW_MS,
) {

    /** The quietest the room has recently been, on MicStreamer's 0..1 scale. */
    var floor: Float = 0f
        private set

    /**
     * Whether any buffer seen so far can be trusted as the room.
     *
     * True for an ordinary turn, where the first buffer really is the room —
     * somebody tapped the orb and has not started talking yet.
     *
     * False after a wake word, and that case is the whole reason this exists.
     * **The microphone opens while the user is already mid-sentence**: "Hey
     * Jarvis, turn the kitchen lights off" is one breath, and capture starts
     * inside it. Taking that buffer for the room put [floor] at speech level
     * and the start edge at FOUR TIMES speech level, so nothing said afterwards
     * could cross it — the turn heard nothing, ran to its inactivity timeout,
     * and told the user to check their microphone. That is the field report: "I
     * was talking to it and it wasn't really able to hear me."
     *
     * `SyntheticSpeech` emits its tone from sample zero, so the same fault was
     * in front of `ConversationE2ETest`.
     *
     * While unseeded the edges are the absolute minimums, which is what lets
     * speech in the very first buffer latch at all, and the floor creeps up
     * from zero — INCLUDING while speech is latched, unlike the ordinary case.
     * That asymmetry is deliberate: nothing can separate a room humming at
     * 0.012 from a voice at 0.012 by level alone, so a wake-word turn in a loud
     * room may well latch on the room, and the thing it must not do is stay
     * latched.
     *
     * ## How seeding ends
     *
     * Two ways, and the second was added because the first was almost never
     * reached:
     *
     *  1. **A buffer at or below [MIN_START]** — 0.004, a genuinely silent
     *     room. That IS the room, so take it and carry on normally.
     *  2. **[SEED_WINDOW_MS] elapses** — infer the room from the quietest
     *     buffer heard, capped at [SEED_PEAK_FRACTION] of the loudest.
     *
     * Rule 1 alone meant that in any room above 0.004 — which is most rooms —
     * a wake-word turn never seeded, and the end edge could only arrive by the
     * slow climb: 1.9 s in a quiet room, 3.8 s in a normal one, 6.4 s in a
     * kitchen, and then the 900 ms hangover on top. Reported as *"I say
     * something, and it takes a while for it to stop listening"*. The climb was
     * meant to be the safe fallback for a turn latched on noise; it had quietly
     * become the ordinary path for every wake-word turn.
     */
    private var seeded = !speechAlreadyUnderway

    /** Loudest level seen since [reset]. The dead-microphone diagnostic. */
    var peak: Float = 0f
        private set

    /** True between a latched start edge and the end of the utterance. */
    var speaking: Boolean = false
        private set

    /** When the current run of above-start audio began, or 0. */
    private var aboveSince = 0L

    /** The loudest level within the current above-edge run. See [SUSTAIN_RATIO]. */
    private var candidatePeak = 0f

    /** The quietest buffer seen while unseeded — the best estimate of the room. */
    private var quietest = Float.MAX_VALUE

    /** When the first unseeded buffer arrived, so the seed window can elapse. */
    private var firstUnseededAt = 0L

    /** When speech was last genuinely present. Drives the hangover. */
    private var lastVoiceAt = 0L
    private var startedAt = 0L

    /** The edge that begins a turn. Always above [endEdge]. */
    val startEdge: Float get() = maxOf(minStart, floor * startRatio)

    /** The edge below which the hangover runs. */
    val endEdge: Float get() = maxOf(minEnd, floor * endRatio)

    /** What one capture buffer means. */
    enum class Verdict {
        /** Nothing to do. */
        QUIET,

        /** Speech has just been confirmed; the caller should mark the turn live. */
        STARTED,

        /** Still talking. */
        SPEAKING,

        /** The hangover elapsed; the caller should end the audio. */
        ENDED,
    }

    /**
     * Feed one smoothed level.
     *
     * @param nowMs a monotonic clock. Never wall clock: a turn measured against
     *   a clock that can step is a turn that can end in the past.
     */
    fun onLevel(nowMs: Long, level: Float, debounceMs: Long = START_DEBOUNCE_MS,
                minSpeechMs: Long = MIN_SPEECH_MS, hangoverMs: Long = END_SILENCE_MS): Verdict {
        if (level > peak) peak = level

        val edge = startEdge
        when {
            // Speech was already under way when the microphone opened, so no
            // buffer yet seen can be trusted as the room. See [seeded].
            !seeded -> {
                if (firstUnseededAt == 0L || nowMs < firstUnseededAt) firstUnseededAt = nowMs
                if (level < quietest) quietest = level

                if (level <= minStart) {
                    // A genuinely silent buffer. Nothing to infer — that IS the
                    // room, and the ordinary rules can start at once.
                    floor = level
                    seeded = true
                } else if (nowMs - firstUnseededAt >= seedWindowMs) {
                    // Reported as: *"I say something, and it takes a while for
                    // it to stop listening and hear what I said."*
                    //
                    // The branch above is the only way a wake-word turn used to
                    // become seeded, and it needs a buffer at or below
                    // MIN_START — 0.004, which is a recording studio. An
                    // ordinary room sits above that, so a wake-word turn stayed
                    // unseeded and the end edge could only arrive by the slow
                    // climb below: 1.9 s of climbing in a quiet room, 3.8 s in a
                    // normal one, 6.4 s in a kitchen — and THEN the 900 ms
                    // hangover. The climb was designed as the safe fallback for
                    // a turn latched on a noisy room; it had become the ordinary
                    // path.
                    //
                    // So measure the room instead of waiting to be given it.
                    // After a second the quietest buffer seen is either the room
                    // or a gap between two words, and both are far below speech.
                    //
                    // The QUIETEST buffer, and nothing cleverer. A first
                    // attempt capped this at a fraction of the loudest, to be
                    // safe against a window made entirely of speech — and the
                    // spec's noisy-room case rejected it immediately, because
                    // that cap fires just as hard on a window made entirely of
                    // ROOM: it seeded the floor below the room, left the end
                    // edge under it, and the turn ran to the cap with twelve
                    // seconds of noise. Which is the failure the slow climb was
                    // put there to prevent in the first place.
                    //
                    // Level alone cannot separate 1.5 s of steady room from
                    // 1.5 s of steady speech, and no threshold on it will. What
                    // separates them is that speech is not steady: over a
                    // second and a half it has syllable boundaries, and the
                    // quietest of them sits near the room. Room noise is
                    // stationary, so its quietest IS the room. Taking the
                    // minimum is therefore right in both cases at once.
                    //
                    // The case it does not solve is a microphone that opened
                    // mid-syllable into 1.5 s of unbroken speech. That seeds
                    // high and ends the turn early — the user repeats, and the
                    // audio already captured still goes to the recogniser.
                    // Bounded, self-correcting, and rare, where the bug being
                    // fixed was several seconds on every single turn.
                    floor = quietest
                    seeded = true
                } else {
                    // Note this runs while speech is latched too, which the
                    // branch below deliberately does not. It is the backstop
                    // for the first second, before there is enough of a sample
                    // to seed from.
                    floor = minOf(floor + floorRise, level)
                }
            }

            // The first buffer IS the room. Starting from zero would mean the
            // edges spend their first second at the absolute minimums, and a
            // room already above those would latch a turn on itself before the
            // floor had a chance to describe it.
            floor <= 0f -> floor = level

            // Anything above the start edge might be speech, so it must not be
            // allowed to raise the floor — not while it is only a candidate,
            // and not once it has latched. Both matter, and the first is
            // subtler: during the debounce window a rising floor drags the edge
            // up behind it and can overtake the very speech it is measuring,
            // so an ordinary sentence in a quiet room is never confirmed.
            level <= edge ->
                floor = if (level < floor) level else minOf(floor + floorRise, level)
        }

        if (level > edge) {
            if (aboveSince == 0L) {
                aboveSince = nowMs
                candidatePeak = level
            }
            if (level > candidatePeak) candidatePeak = level
            // Guard a clock that went backwards, before anything compares
            // against it: a negative delta is smaller than any window and
            // would latch instantly, or never.
            if (nowMs < aboveSince) aboveSince = nowMs
            if (!speaking && nowMs - aboveSince >= debounceMs) {
                // Sustained, or merely loud once?
                //
                // Duration alone cannot answer this, and that is the whole
                // reason for the test. MicStreamer smooths at alpha 0.3, so a
                // single bang at 0.05 stays above a 0.004 edge for eight
                // buffers — half a second — and a louder one for longer. Any
                // debounce long enough to outlast a door is long enough to
                // make speech feel broken.
                //
                // Shape tells them apart. A transient DECAYS: by the end of
                // the window it is a small fraction of its own peak. Speech is
                // several syllables and stays near it. Failing the test
                // restarts the window rather than abandoning it, so a bang
                // followed by somebody actually talking still latches on the
                // talking.
                if (level >= candidatePeak * SUSTAIN_RATIO) {
                    speaking = true
                    startedAt = nowMs
                    lastVoiceAt = nowMs
                    return Verdict.STARTED
                }
                aboveSince = nowMs
                candidatePeak = level
            }
            if (speaking) {
                lastVoiceAt = nowMs
                return Verdict.SPEAKING
            }
            return Verdict.QUIET
        }

        aboveSince = 0L
        candidatePeak = 0f
        if (!speaking) return Verdict.QUIET

        // The dead band between the two edges is ambiguous audio, not silence,
        // so it holds the turn open without extending it indefinitely — that is
        // what the two edges are FOR.
        if (level >= endEdge) {
            lastVoiceAt = nowMs
            return Verdict.SPEAKING
        }
        if (nowMs < lastVoiceAt) {                    // clock went backwards
            lastVoiceAt = nowMs
            return Verdict.SPEAKING
        }
        if (nowMs - startedAt > minSpeechMs && nowMs - lastVoiceAt > hangoverMs) {
            speaking = false
            aboveSince = 0L
            return Verdict.ENDED
        }
        return Verdict.SPEAKING
    }

    /** A new turn. The floor is deliberately KEPT: the room did not change. */
    fun newTurn() {
        speaking = false
        aboveSince = 0L
        candidatePeak = 0f
        lastVoiceAt = 0L
        startedAt = 0L
    }

    /**
     * A new conversation, in a room we know nothing about yet.
     *
     * @param speechUnderway whether the user is already talking, as they are
     *   after a wake word. Defaults to this detector's construction-time
     *   answer, so a caller that never thought about it keeps its behaviour.
     */
    fun reset(speechUnderway: Boolean = speechAlreadyUnderway) {
        newTurn()
        floor = 0f
        peak = 0f
        seeded = !speechUnderway
        // The seed evidence belongs to the session that gathered it. Carrying
        // a previous room measurement into a fresh unseeded turn would seed it
        // instantly from a room that may no longer be there.
        quietest = Float.MAX_VALUE
        firstUnseededAt = 0L
    }

    companion object {
        /**
         * Speech is four times the room, silence is twice it.
         *
         * Twelve and six decibels. The gap between them is the hysteresis, and
         * it has to be wide enough that the natural dip between two words does
         * not read as the end of a sentence.
         */
        const val START_RATIO = 4.0f
        const val END_RATIO = 2.0f

        /**
         * Floors on the edges themselves, for a room quiet enough that a pure
         * ratio would promote the recorder's own dither to speech.
         *
         * MIN_START is deliberately a tenth of the 0.02 that was too high to
         * reach, and five times the 0.002 that latched on nothing.
         */
        const val MIN_START = 0.004f
        const val MIN_END = 0.002f

        /**
         * How fast the floor may rise, per 64 ms buffer. Roughly 0.0016 per
         * second: a room that genuinely gets noisier is tracked within a few
         * seconds, and a single loud event cannot drag the edges up with it.
         */
        const val FLOOR_RISE_PER_CHUNK = 0.0001f

        /**
         * Sustained energy required to latch the start edge.
         *
         * Three buffers. Deliberately short, because rejecting transients is
         * [SUSTAIN_RATIO]'s job rather than this one's — and duration cannot do
         * it anyway. MicStreamer smooths at alpha 0.3, so a single bang at 0.05
         * stays above a 0.004 edge for eight buffers, and a louder one for
         * longer; any debounce that outlasts a door also swallows a half-second
         * word. The old 120 ms was too short to require anything at all.
         *
         * It costs no audio either way. Capture streams from the moment the run
         * opens (see `AssistPipelineClient.sendAudio`), so this decides when
         * the turn is considered live, never what the recogniser receives.
         */
        const val START_DEBOUNCE_MS = 200L

        /**
         * How much of its own peak a candidate must still hold when the
         * debounce elapses, to count as speech rather than as a bang.
         *
         * A one-buffer impulse through MicStreamer's smoother retains 0.7 per
         * buffer, so across the three buffers of [START_DEBOUNCE_MS] it falls
         * to about a third of its peak, and keeps falling. Speech over the same
         * 200 ms is a syllable or two and stays near its own peak.
         *
         * A failed test restarts the window rather than abandoning it, so a
         * bang followed by somebody actually talking still latches on the
         * talking — 200 ms later, which costs nothing that is recorded.
         */
        const val SUSTAIN_RATIO = 0.40f

        /** Minimum length of a turn before its end may be declared. */
        const val MIN_SPEECH_MS = 300L

        /** Trailing silence that ends a turn. */
        const val END_SILENCE_MS = 900L

        /**
         * How long a wake-word turn waits before inferring the room.
         *
         * A second and a half is long enough to hold "Hey Jarvis, turn the
         * kitchen lights off" — so the quietest buffer in it is either the room
         * or a gap between two words — and short enough that the seeding
         * happens while the sentence is still going, rather than seconds after
         * it ends.
         */
        const val SEED_WINDOW_MS = 1_500L


        /**
         * A peak at or below this is digital silence, not a quiet room.
         *
         * Not exactly zero: a recorder that is "working" but muted still emits
         * dither and the odd non-zero sample.
         */
        const val DEAD_MIC_LEVEL = 0.0005f
    }
}
