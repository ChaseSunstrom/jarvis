package io.homeassistant.companion.android.jarvis

import android.content.Context
import android.os.Build
import android.os.Bundle
import android.service.voice.VoiceInteractionSession
import android.util.Log

/**
 * Trampoline session: shows no UI of its own; on assist invocation it
 * launches [JarvisAssistActivity] and immediately finishes. This is the
 * standard pattern for apps whose assist experience is an activity rather
 * than a system overlay panel.
 */
class JarvisVoiceInteractionSession(context: Context) : VoiceInteractionSession(context) {

    override fun onCreate() {
        super.onCreate()
        // We never render session UI.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            setUiEnabled(false)
        }
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        val intent = JarvisAssistActivity.newIntent(context)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // Assistant-privileged start: allowed from the background and
                // over the keyguard.
                startAssistantActivity(intent)
            } else {
                context.startActivity(intent)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch JarvisAssistActivity from session", e)
        }
        // Nothing to keep alive here; the activity owns the experience.
        finish()
    }

    companion object {
        private const val TAG = "JarvisVISession"
    }
}
