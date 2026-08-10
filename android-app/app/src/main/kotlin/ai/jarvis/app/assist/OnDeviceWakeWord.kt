package ai.jarvis.app.assist

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.util.Log
import java.io.File
import java.nio.FloatBuffer

/**
 * "Hey Jarvis", decided on the phone.
 *
 * The alternative — and what the app did until now — is a permanently open
 * socket carrying 16 kHz PCM to the server so that openWakeWord there can
 * decide. That works, and it costs a continuous upload of everything the
 * microphone hears, forever. Doing the same arithmetic locally means audio
 * leaves the phone only after the name has been said.
 *
 * ## The pipeline
 *
 * openWakeWord is three ONNX models in a chain, and the split is not arbitrary:
 * the first two are shared by every wake word, and only the third knows what
 * "hey Jarvis" sounds like.
 *
 * ```
 *   1280 samples (80 ms)  -> melspectrogram  -> 5 mel frames of 32 bins
 *   76 mel frames         -> embedding       -> one 96-dim vector
 *   16 embeddings         -> hey_jarvis      -> P(the name was said)
 * ```
 *
 * Both intermediate stages are sliding windows, which is why this class keeps
 * two ring buffers rather than re-running the chain over a fresh window each
 * time: at 80 ms per step, recomputing 76 mel frames and 16 embeddings from
 * scratch every step is roughly twenty times the work for the same answer.
 *
 * ## Everything here fails safe
 *
 * The weights are fetched at runtime from the user's own server
 * ([ModelStore]), so on a phone that has not downloaded them — or one where
 * ONNX Runtime will not load, or a model that turns out to have the wrong
 * shape — [isReady] is false and the caller keeps using the server. Nothing in
 * this file is allowed to be the reason the wake word stops working; it is an
 * optimisation over a path that already works.
 */
class OnDeviceWakeWord private constructor(
    private val environment: OrtEnvironment,
    private val mels: OrtSession,
    private val embeddings: OrtSession,
    private val wakeWord: OrtSession,
) {

    /** 76 mel frames of 32 bins, oldest first. */
    private val melWindow = ArrayDeque<FloatArray>(MEL_WINDOW)

    /** 16 embedding vectors of 96, oldest first. */
    private val embeddingWindow = ArrayDeque<FloatArray>(EMBEDDING_WINDOW)

    /** Samples not yet consumed by a full 1280-sample step. */
    private val pending = ArrayList<Float>(CHUNK_SAMPLES * 2)

    val isReady: Boolean get() = true

    /**
     * Feed 16 kHz mono PCM16.
     *
     * @return the newest wake-word probability, or null when there is not yet
     *   enough audio for one. Nulls are normal and frequent: a score exists
     *   only every 80 ms, and only once both windows have filled — about 1.4
     *   seconds after capture starts.
     */
    fun score(pcm: ByteArray, length: Int): Float? {
        var i = 0
        while (i + 1 < length) {
            // Little-endian int16 to float in -1..1, which is what the
            // melspectrogram model was trained on.
            val sample = ((pcm[i + 1].toInt() shl 8) or (pcm[i].toInt() and 0xFF)).toShort()
            pending.add(sample.toFloat() / 32768f)
            i += 2
        }

        var latest: Float? = null
        while (pending.size >= CHUNK_SAMPLES) {
            val chunk = FloatArray(CHUNK_SAMPLES) { pending[it] }
            repeat(CHUNK_SAMPLES) { pending.removeAt(0) }
            latest = step(chunk) ?: latest
        }
        return latest
    }

    private fun step(chunk: FloatArray): Float? {
        val melFrames = runMels(chunk) ?: return null
        for (frame in melFrames) {
            if (melWindow.size == MEL_WINDOW) melWindow.removeFirst()
            melWindow.addLast(frame)
        }
        if (melWindow.size < MEL_WINDOW) return null

        val embedding = runEmbedding() ?: return null
        if (embeddingWindow.size == EMBEDDING_WINDOW) embeddingWindow.removeFirst()
        embeddingWindow.addLast(embedding)
        if (embeddingWindow.size < EMBEDDING_WINDOW) return null

        return runWakeWord()
    }

    private fun runMels(chunk: FloatArray): List<FloatArray>? = try {
        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(chunk),
            longArrayOf(1, chunk.size.toLong()),
        ).use { input ->
            mels.run(mapOf(mels.inputNames.first() to input)).use { result ->
                // (1, 1, frames, 32), and the model's own scaling: openWakeWord
                // applies `x / 10 + 2` between this stage and the next. Skipping
                // it produces a model that runs, returns numbers, and never
                // detects anything.
                @Suppress("UNCHECKED_CAST")
                val raw = result[0].value as Array<Array<Array<FloatArray>>>
                raw[0][0].map { frame -> FloatArray(frame.size) { frame[it] / 10f + 2f } }
            }
        }
    } catch (t: Throwable) {
        Log.w(TAG, "melspectrogram failed", t)
        null
    }

    private fun runEmbedding(): FloatArray? = try {
        val flat = FloatArray(MEL_WINDOW * MEL_BINS)
        var at = 0
        for (frame in melWindow) {
            System.arraycopy(frame, 0, flat, at, MEL_BINS)
            at += MEL_BINS
        }
        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(flat),
            longArrayOf(1, MEL_WINDOW.toLong(), MEL_BINS.toLong(), 1),
        ).use { input ->
            embeddings.run(mapOf(embeddings.inputNames.first() to input)).use { result ->
                @Suppress("UNCHECKED_CAST")
                val raw = result[0].value as Array<Array<Array<FloatArray>>>
                raw[0][0][0].copyOf()
            }
        }
    } catch (t: Throwable) {
        Log.w(TAG, "embedding failed", t)
        null
    }

    private fun runWakeWord(): Float? = try {
        val flat = FloatArray(EMBEDDING_WINDOW * EMBEDDING_SIZE)
        var at = 0
        for (vector in embeddingWindow) {
            System.arraycopy(vector, 0, flat, at, EMBEDDING_SIZE)
            at += EMBEDDING_SIZE
        }
        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(flat),
            longArrayOf(1, EMBEDDING_WINDOW.toLong(), EMBEDDING_SIZE.toLong()),
        ).use { input ->
            wakeWord.run(mapOf(wakeWord.inputNames.first() to input)).use { result ->
                @Suppress("UNCHECKED_CAST")
                (result[0].value as Array<FloatArray>)[0][0]
            }
        }
    } catch (t: Throwable) {
        Log.w(TAG, "wake word model failed", t)
        null
    }

    /** Drop the windows. Called when capture stops, so stale audio cannot fire. */
    fun reset() {
        melWindow.clear()
        embeddingWindow.clear()
        pending.clear()
    }

    fun close() {
        runCatching { wakeWord.close() }
        runCatching { embeddings.close() }
        runCatching { mels.close() }
    }

    companion object {
        private const val TAG = "JarvisWakeOnDevice"

        /** 80 ms at 16 kHz — one step of the pipeline. */
        const val CHUNK_SAMPLES = 1280

        const val MEL_BINS = 32
        const val MEL_WINDOW = 76
        const val EMBEDDING_SIZE = 96
        const val EMBEDDING_WINDOW = 16

        /** The three files, in the order they are chained. */
        val REQUIRED_MODELS = listOf(
            "melspectrogram.onnx",
            "embedding_model.onnx",
            "hey_jarvis_v0.1.onnx",
        )

        /**
         * Load the chain, or return null.
         *
         * Null is a supported, expected answer — the weights may not have been
         * downloaded, ONNX Runtime may not have a build for this ABI, a file
         * may be truncated. Every one of those means "keep using the server",
         * never "the wake word is broken".
         */
        fun open(directory: File): OnDeviceWakeWord? {
            val files = REQUIRED_MODELS.map { File(directory, it) }
            if (files.any { !it.isFile || it.length() == 0L }) {
                Log.i(TAG, "on-device models are not downloaded yet")
                return null
            }
            return try {
                val env = OrtEnvironment.getEnvironment()
                val options = OrtSession.SessionOptions().apply {
                    // One thread. This runs continuously in the background on a
                    // phone; taking every core to shave milliseconds off a
                    // detection nobody is waiting for is the wrong trade.
                    setIntraOpNumThreads(1)
                    setInterOpNumThreads(1)
                }
                OnDeviceWakeWord(
                    env,
                    env.createSession(files[0].absolutePath, options),
                    env.createSession(files[1].absolutePath, options),
                    env.createSession(files[2].absolutePath, options),
                )
            } catch (t: Throwable) {
                Log.w(TAG, "could not open the on-device wake word models", t)
                null
            }
        }
    }
}
