package io.homeassistant.companion.android.jarvis

import android.service.voice.VoiceInteractionService
import android.util.Log

/**
 * Minimal VoiceInteractionService so the jarvis flavor can hold
 * ROLE_ASSISTANT / the `voice_interaction_service` Secure Setting on
 * GrapheneOS. All real behavior lives in [JarvisVoiceInteractionSession],
 * which trampolines to [JarvisAssistActivity].
 *
 * Note: on GrapheneOS the Secure Settings that point at this service are
 * CLEARED on every reinstall/update - re-run scripts/adb-jarvis-role.sh
 * after each Obtainium update (see docs/android.md).
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
