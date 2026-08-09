package ai.jarvis.app.testing

import ai.jarvis.app.assist.MicStreamer
import kotlin.math.PI
import kotlin.math.sin

/**
 * DEBUG SOURCE SET ONLY — see the header of [TestHooks].
 *
 * A microphone that says something. Emits 16 kHz mono PCM16 in a repeating
 * pattern of "loud" and "silent", which is the only property of real speech the
 * app's energy VAD actually looks at:
 *
 * ```
 *   |<-- speechMs -->|<------ silenceMs ------>|<-- speechMs -->| …
 *    ~~~~~~~~~~~~~~~~ ------------------------- ~~~~~~~~~~~~~~~~
 *    level ≈ amplitude          level ≈ 0
 * ```
 *
 * `JarvisConversation` starts a turn when the smoothed RMS stays above
 * `START_THRESHOLD` for `START_DEBOUNCE_MS`, and ends it after `END_SILENCE_MS`
 * below `END_THRESHOLD`, provided at least `MIN_SPEECH_MS` of speech happened.
 * The defaults below clear both, at a level a person actually produces — see
 * [DEFAULT_AMPLITUDE] for why that matters more than the margin does.
 *
 * ## Why it repeats
 *
 * The socket is not ready the instant the mic starts: the app has to finish the
 * WebSocket handshake, authenticate, resolve the pipeline and receive
 * `run-start` before `AssistPipelineClient.sendAudio` has a binary handler id to
 * prefix frames with. Audio produced before that is dropped on the floor. A
 * one-shot utterance would therefore be a race between a fake microphone and a
 * real network; a repeating one simply tries again, and the silence gap is long
 * enough that each burst produces at most one end-of-speech.
 *
 * The waveform is a 220 Hz sine rather than white noise so that a captured PCM
 * dump is recognisable when a test fails and somebody goes looking.
 */
class SyntheticSpeech(
    private val speechMs: Long = DEFAULT_SPEECH_MS,
    private val silenceMs: Long = DEFAULT_SILENCE_MS,
    private val amplitude: Float = DEFAULT_AMPLITUDE,
    private val toneHz: Double = DEFAULT_TONE_HZ,
) : MicStreamer.PcmSource {

    /** Samples emitted so far, across every cycle. Drives both phase and phase-in-cycle. */
    private var samplesEmitted = 0L

    private val speechSamples = (speechMs * SAMPLE_RATE / 1000L).coerceAtLeast(1L)
    private val silenceSamples = (silenceMs * SAMPLE_RATE / 1000L).coerceAtLeast(1L)
    private val cycleSamples = speechSamples + silenceSamples

    /** True while the current position falls in the "speaking" half of the cycle. */
    val speaking: Boolean get() = (samplesEmitted % cycleSamples) < speechSamples

    override fun read(buffer: ByteArray): Int {
        val sampleCount = buffer.size / BYTES_PER_SAMPLE
        if (sampleCount == 0) return 0
        val peak = (amplitude.coerceIn(0f, 1f) * Short.MAX_VALUE).toInt()

        for (i in 0 until sampleCount) {
            val position = (samplesEmitted + i) % cycleSamples
            val value = if (position < speechSamples) {
                val t = (samplesEmitted + i).toDouble() / SAMPLE_RATE
                (peak * sin(2.0 * PI * toneHz * t)).toInt()
            } else {
                0
            }
            val clamped = value.coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
            // Little-endian, matching AudioRecord's native PCM16 byte order —
            // the bytes go onto the wire untouched, so getting this backwards
            // would produce audio that is loud in the RMS and noise to the STT.
            buffer[i * BYTES_PER_SAMPLE] = (clamped and 0xFF).toByte()
            buffer[i * BYTES_PER_SAMPLE + 1] = ((clamped shr 8) and 0xFF).toByte()
        }
        samplesEmitted += sampleCount
        return sampleCount * BYTES_PER_SAMPLE
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        private const val BYTES_PER_SAMPLE = 2

        /**
         * Comfortably past `MIN_SPEECH_MS` (300 ms) so a turn is never rejected
         * as too short, and short enough that a burst plus its silence fits in
         * a few seconds.
         */
        const val DEFAULT_SPEECH_MS = 900L

        /**
         * Comfortably past `END_SILENCE_MS` (900 ms), with room for the server
         * to answer before the next burst arrives.
         */
        const val DEFAULT_SILENCE_MS = 3_500L

        /**
         * RMS of a full-scale sine is 1/√2 ≈ 0.707 of its peak, so 0.12 peak
         * lands around 0.085 RMS.
         *
         * This used to be 0.45 peak — 0.32 RMS, five times the old
         * `START_THRESHOLD` of 0.06 — and that margin is exactly what let a
         * VAD threshold three times too high for a real microphone pass CI
         * unchanged for the whole life of the app. A synthetic voice that can
         * only pass at 16x the real threshold cannot catch that class of
         * regression, so this now sits where conversational speech actually
         * sits: a small multiple of the start edge, and deliberately BELOW
         * `BARGE_THRESHOLD` (0.10) so the fake microphone does not interrupt
         * the reply it just asked for.
         */
        const val DEFAULT_AMPLITUDE = 0.12f

        /** Low enough to survive any resampling, high enough to be audible. */
        const val DEFAULT_TONE_HZ = 220.0
    }
}
