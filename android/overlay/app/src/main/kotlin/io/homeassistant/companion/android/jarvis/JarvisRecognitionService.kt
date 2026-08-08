package io.homeassistant.companion.android.jarvis

import android.content.Intent
import android.os.RemoteException
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.util.Log

/**
 * Stub recognizer. The `android:recognitionService` attribute in
 * res/xml/jarvis_voice_interaction_service.xml must reference a resolvable
 * RecognitionService component on several OS versions, but Jarvis never does
 * on-device STT - speech goes to the Home Assistant Assist pipeline
 * (Whisper/Voice PE stack) on the server. Any client binding here gets
 * ERROR_CLIENT immediately.
 */
class JarvisRecognitionService : RecognitionService() {

    override fun onStartListening(recognizerIntent: Intent?, listener: Callback?) {
        try {
            listener?.error(SpeechRecognizer.ERROR_CLIENT)
        } catch (e: RemoteException) {
            Log.w(TAG, "Client went away while reporting stub error", e)
        }
    }

    override fun onCancel(listener: Callback?) {
        // Nothing to cancel.
    }

    override fun onStopListening(listener: Callback?) {
        try {
            listener?.error(SpeechRecognizer.ERROR_CLIENT)
        } catch (e: RemoteException) {
            Log.w(TAG, "Client went away while reporting stub error", e)
        }
    }

    companion object {
        private const val TAG = "JarvisStubSTT"
    }
}
