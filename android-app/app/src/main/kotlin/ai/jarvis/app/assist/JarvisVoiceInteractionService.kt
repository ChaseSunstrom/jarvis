package ai.jarvis.app.assist

import android.service.voice.VoiceInteractionService
import android.util.Log

/**
 * Minimal VoiceInteractionService so the app can hold ROLE_ASSISTANT / the
 * `voice_interaction_service` Secure Setting on GrapheneOS. All real behaviour
 * lives in [JarvisVoiceInteractionSession], which trampolines to
 * [ai.jarvis.app.JarvisAssistActivity].
 *
 * Note: on GrapheneOS the Secure Settings that point at this service are
 * CLEARED on every reinstall/update — re-run the adb commands in the README
 * after each Obtainium update.
 */
class JarvisVoiceInteractionService : VoiceInteractionService() {

    override fun onReady() {
        super.onReady()
        Log.i(TAG, "Jarvis voice interaction service ready")
    }

    companion object {
        private const val TAG = "JarvisVIS"
    }
}
