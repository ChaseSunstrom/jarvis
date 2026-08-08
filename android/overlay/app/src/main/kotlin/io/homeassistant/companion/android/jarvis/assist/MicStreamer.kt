package io.homeassistant.companion.android.jarvis.assist

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlin.concurrent.thread
import kotlin.math.min
import kotlin.math.sqrt

/**
 * 16 kHz mono PCM16 mic capture. Streams raw little-endian frames (matching
 * the pipeline's Int16LE framing) and reports a smoothed RMS level (0..1) for
 * the orb and for VAD. Native Android PCM16 is little-endian, so the bytes go
 * straight onto the wire.
 */
class MicStreamer(
    private val onPcm: (ByteArray, Int) -> Unit,
    private val onLevel: (Float) -> Unit
) {
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    private var record: AudioRecord? = null
    private var worker: Thread? = null

    fun start() {
        if (running) return
        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL, ENCODING)
        if (minBuf <= 0) {
            Log.e(TAG, "invalid min buffer size: $minBuf")
            return
        }
        val bufSize = min(minBuf * 2, SAMPLE_RATE) // ~<=0.5s
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE, CHANNEL, ENCODING, minBuf * 2
            )
        } catch (e: Exception) {
            Log.e(TAG, "AudioRecord init failed", e); return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord not initialized"); rec.release(); return
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

    companion object {
        private const val TAG = "JarvisMic"
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        private const val CHUNK_BYTES = 2048 // 1024 samples ~64ms
    }
}
