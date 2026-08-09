package ai.jarvis.app.assist

import ai.jarvis.app.BuildConfig
import ai.jarvis.app.audio.AudioRoute
import ai.jarvis.app.audio.CaptureProfile
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlin.concurrent.thread
import kotlin.math.sqrt

/**
 * 16 kHz mono PCM16 mic capture. Streams raw little-endian frames (matching
 * the pipeline's Int16LE framing) and reports a smoothed RMS level (0..1) for
 * the orb and for VAD. Native Android PCM16 is little-endian, so the bytes go
 * straight onto the wire.
 *
 * Nothing is written to disk at any point: audio goes from AudioRecord into the
 * socket and is gone.
 */
class MicStreamer(
    private val onPcm: (ByteArray, Int) -> Unit,
    private val onLevel: (Float) -> Unit,
    /**
     * How to open the mic, from [ai.jarvis.app.audio.CaptureProfile]. Read once
     * per [start] so a headset connected mid-conversation takes effect on the
     * next turn rather than tearing down the current one.
     *
     * Defaults to the phone-mic profile, which is the behaviour every existing
     * caller had before headsets existed.
     */
    private val captureProfile: () -> CaptureProfile = {
        CaptureProfile.forRoute(AudioRoute())
    },
    /**
     * Capture could not start, with a sentence for the user.
     *
     * Every failure below used to be `Log.e(...); return`, which made a dead
     * microphone indistinguishable from a working one: the caller saw a
     * streamer that had "started", the orb said LISTENING, and the only
     * evidence was a logcat line on a phone nobody has a cable for. A silent
     * failure is a bug in itself, so the reason has to reach a screen.
     *
     * Delivered on the main thread. Defaults to a no-op so the callers that
     * have nowhere to put it (the companion prompt handles its own failure)
     * are unaffected.
     */
    private val onUnavailable: (String) -> Unit = {}
) {
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    private var record: AudioRecord? = null
    private var worker: Thread? = null

    fun start() {
        if (running) return
        // TEST SEAM, debug builds only. See [debugPcmSource].
        val injected = if (BuildConfig.DEBUG) debugPcmSource?.invoke() else null
        if (injected != null) {
            startInjected(injected)
            return
        }
        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL, ENCODING)
        if (minBuf <= 0) {
            Log.e(TAG, "invalid min buffer size: $minBuf")
            fail("This device will not open a 16 kHz mono recorder.")
            return
        }
        // VOICE_COMMUNICATION when the reply would otherwise be heard as a new
        // question (worn headset), VOICE_RECOGNITION otherwise because it is
        // unprocessed and the STT model scores better on it. The choice is
        // AudioRoute's; this class only obeys it. See CaptureProfile.forRoute.
        val profile = try {
            captureProfile()
        } catch (t: Throwable) {
            Log.w(TAG, "capture profile lookup failed; using the phone mic", t)
            CaptureProfile.forRoute(AudioRoute())
        }
        val source = if (profile.useVoiceCommunication) {
            MediaRecorder.AudioSource.VOICE_COMMUNICATION
        } else {
            MediaRecorder.AudioSource.VOICE_RECOGNITION
        }
        Log.i(TAG, "capture source=${if (profile.useVoiceCommunication) "VOICE_COMMUNICATION" else "VOICE_RECOGNITION"}: ${profile.reason}")

        val rec = try {
            AudioRecord(source, SAMPLE_RATE, CHANNEL, ENCODING, minBuf * 2)
        } catch (e: Exception) {
            Log.e(TAG, "AudioRecord init failed", e)
            // The usual cause is a revoked RECORD_AUDIO, which on GrapheneOS
            // can also be "granted" while the per-app Sensors toggle is off.
            fail("The microphone could not be opened. Check the Microphone permission for Jarvis.")
            return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord not initialized")
            rec.release()
            fail("The microphone is busy — another app may be holding it.")
            return
        }
        record = rec
        running = true
        rec.startRecording()
        worker = thread(name = "jarvis-mic", isDaemon = true) {
            val chunk = ByteArray(CHUNK_BYTES)
            var smooth = 0f
            while (running) {
                val n = rec.read(chunk, 0, chunk.size)
                if (n <= 0) continue
                onPcm(chunk, n)
                val level = rms16(chunk, n)
                smooth += (level - smooth) * 0.3f
                val out = smooth
                main.post { onLevel(out) }
            }
        }
    }

    /** Report a capture failure to the caller, on the main thread. */
    private fun fail(reason: String) {
        main.post { onUnavailable(reason) }
    }

    fun stop() {
        running = false
        worker?.let { try { it.join(200) } catch (_: InterruptedException) {} }
        worker = null
        record?.let {
            try { if (it.recordingState == AudioRecord.RECORDSTATE_RECORDING) it.stop() } catch (_: Exception) {}
            it.release()
        }
        record = null
    }

    /**
     * The [debugPcmSource] path. Identical downstream behaviour to the
     * AudioRecord loop above — same chunk size, same RMS, same callbacks, same
     * `stop()` — with the capture device swapped for a caller-supplied buffer.
     *
     * Paced in real time (one 64 ms chunk per 64 ms) rather than as fast as the
     * CPU allows, because [JarvisConversation]'s VAD measures speech and silence
     * against the wall clock. A source that dumped a second of audio in a
     * millisecond would never cross END_SILENCE_MS and the turn would never end.
     */
    private fun startInjected(source: PcmSource) {
        running = true
        worker = thread(name = "jarvis-mic-injected", isDaemon = true) {
            val chunk = ByteArray(CHUNK_BYTES)
            var smooth = 0f
            try {
                while (running) {
                    val n = try {
                        source.read(chunk)
                    } catch (t: Throwable) {
                        Log.w(TAG, "injected PCM source threw", t)
                        break
                    }
                    if (n < 0) break
                    if (n > 0) {
                        onPcm(chunk, n)
                        val level = rms16(chunk, n)
                        smooth += (level - smooth) * 0.3f
                        val out = smooth
                        main.post { onLevel(out) }
                    }
                    Thread.sleep(CHUNK_MS)
                }
            } catch (_: InterruptedException) {
                // stop() is joining us; nothing to clean up but the source.
            } finally {
                try {
                    source.close()
                } catch (t: Throwable) {
                    Log.d(TAG, "injected PCM source close failed", t)
                }
            }
        }
    }

    /** RMS of little-endian int16 samples, normalised to ~0..1. */
    private fun rms16(buf: ByteArray, len: Int): Float {
        var sum = 0.0
        var i = 0
        val count = len - (len % 2)
        while (i < count) {
            val s = (buf[i].toInt() and 0xff) or (buf[i + 1].toInt() shl 8)
            val v = s.toShort().toInt()
            sum += (v * v).toDouble()
            i += 2
        }
        val samples = count / 2
        if (samples == 0) return 0f
        val rms = sqrt(sum / samples) / 32768.0
        return rms.toFloat().coerceIn(0f, 1f)
    }

    /**
     * Where 16 kHz mono PCM16 comes from when it does not come from the mic.
     *
     * The narrowest shape that lets an instrumented test drive a real
     * conversation on an emulator, which has no microphone: one blocking read
     * that fills a caller-owned buffer, exactly like `AudioRecord.read`.
     */
    interface PcmSource {
        /**
         * Fill [buffer] with up to `buffer.size` bytes of little-endian PCM16.
         *
         * @return bytes written, 0 for "nothing right now, ask again", or a
         *   negative value for end-of-stream.
         */
        fun read(buffer: ByteArray): Int

        /** Released when the streamer stops. */
        fun close() {}
    }

    companion object {
        private const val TAG = "JarvisMic"
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        private const val CHUNK_BYTES = 2048 // 1024 samples ~64ms

        /** Wall-clock duration of one [CHUNK_BYTES] chunk at [SAMPLE_RATE]. */
        private const val CHUNK_MS = 64L

        /**
         * TEST SEAM — **debug builds only**, and the only line of this class
         * that is not about capturing audio.
         *
         * An emulator has no microphone: `AudioRecord` initialises and then
         * returns silence forever, so the energy VAD in [JarvisConversation]
         * never sees speech, never sends end-of-audio, and no instrumented test
         * can drive a real voice round trip against a real server. Setting this
         * to a factory makes [start] take its audio from that factory instead.
         *
         * Deliberately kept to a factory of a one-method interface. It replaces
         * the *input device* and nothing else — every byte still travels the
         * same path through the same client to the same socket. It cannot skip
         * a consent prompt, change a tier, or read anything: the Tier-1/2/3 gate
         * lives in `automation/policy` and `ui/ApprovalBridge`, which this file
         * does not import and has no way to reach.
         *
         * Read through `BuildConfig.DEBUG` at the point of use, so R8 folds the
         * branch away and a release build has no reachable path to it. The only
         * writer is `ai.jarvis.app.testing.TestHooks`, which exists solely in
         * the debug source set (`assertNoTestHooksInRelease` in
         * app/build.gradle.kts fails the build if that ever stops being true).
         */
        @Volatile
        @JvmStatic
        var debugPcmSource: (() -> PcmSource?)? = null
    }
}
