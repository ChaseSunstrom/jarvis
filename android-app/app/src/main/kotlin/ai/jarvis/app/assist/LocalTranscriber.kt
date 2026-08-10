package ai.jarvis.app.assist

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log

/**
 * Speech to text, on the phone, with nothing leaving it.
 *
 * The pipeline this replaces streams raw microphone audio to jarvis-core for
 * every turn — the whole utterance, as PCM. That is a lot of somebody's house
 * going over the network to be turned into a sentence, when the phone can turn
 * it into a sentence itself and send the sentence.
 *
 * ## Why the platform recogniser and not a bundled model
 *
 * `SpeechRecognizer.createOnDeviceSpeechRecognizer` is an API-31 guarantee
 * rather than a hint: the on-device recogniser does not use the network, by
 * contract. Against the alternative — an ASR model plus a decoder in this app —
 * it costs no download, no ONNX decoder loop, no tokenizer, and no second
 * inference stack alongside the wake word's. For the one thing it is worse at,
 * see below.
 *
 * ## When it is not there
 *
 * On a phone with no on-device recogniser — a degoogled build with nothing
 * providing `RecognitionService`, which this project explicitly targets —
 * [isAvailable] is false. That is not an error and not a silent fallback: the
 * caller is told, so the app can say "transcription is happening on your
 * server" rather than implying a privacy property it does not have. Being
 * quiet about which of the two is running would be the worst outcome available.
 */
class LocalTranscriber(private val context: Context) {

    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var pending: ((String?, String?) -> Unit)? = null

    /**
     * Transcribe one utterance.
     *
     * @param onResult text, or (null, reason). Delivered on the main thread,
     *   exactly once — a recogniser that both errors and returns, or does
     *   neither, must not leave a conversation waiting forever.
     */
    fun listen(language: String, onResult: (String?, String?) -> Unit) {
        if (!isAvailable(context)) {
            onResult(null, "this phone has no on-device speech recognition")
            return
        }
        stop()
        pending = onResult

        val engine = try {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } catch (t: Throwable) {
            Log.w(TAG, "could not create the on-device recogniser", t)
            deliver(null, "the on-device recogniser would not start")
            return
        }
        recognizer = engine
        engine.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    ?.trim()
                if (text.isNullOrEmpty()) deliver(null, "nothing was recognised")
                else deliver(text, null)
            }

            override fun onError(error: Int) = deliver(null, describe(error))

            // Everything else is progress this class does not report. The
            // conversation already draws its own level meter from the same
            // microphone, so a second source of "still listening" would only be
            // a second thing to keep in sync.
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onPartialResults(partialResults: Bundle?) = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
            // Belt and braces. The on-device recogniser is offline by contract;
            // saying so again costs nothing and documents the intent at the
            // call site rather than in a comment.
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        try {
            engine.startListening(intent)
        } catch (t: Throwable) {
            Log.w(TAG, "could not start on-device recognition", t)
            deliver(null, "the on-device recogniser would not start")
        }
    }

    /** Exactly once, on the main thread, and the recogniser is released with it. */
    private fun deliver(text: String?, error: String?) {
        val callback = pending ?: return
        pending = null
        main.post {
            stop()
            callback(text, error)
        }
    }

    fun stop() {
        val engine = recognizer ?: return
        recognizer = null
        runCatching { engine.stopListening() }
        runCatching { engine.cancel() }
        runCatching { engine.destroy() }
    }

    private fun describe(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_AUDIO -> "the microphone could not be read"
        SpeechRecognizer.ERROR_NO_MATCH -> "nothing was recognised"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "nothing was said"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS ->
            "the microphone permission is not granted"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "the recogniser is busy"
        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED,
        SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE,
        ->
            // The most likely real failure, and the one with an actionable fix:
            // the language pack has to be downloaded in the system's own
            // settings before the offline recogniser can use it.
            "this language is not installed for offline recognition — add it in " +
                "Android's speech settings"
        else -> "on-device recognition failed ($error)"
    }

    companion object {
        private const val TAG = "JarvisLocalStt"

        /**
         * Whether this phone can transcribe without the network.
         *
         * API 31 for the call itself; `isOnDeviceRecognitionAvailable` for
         * whether anything actually provides it, which on a degoogled build is
         * commonly false. Both are checked because the answer decides what the
         * app is allowed to CLAIM, not just what it does.
         */
        fun isAvailable(context: Context): Boolean = try {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        } catch (t: Throwable) {
            Log.w(TAG, "could not ask about on-device recognition", t)
            false
        }
    }
}
