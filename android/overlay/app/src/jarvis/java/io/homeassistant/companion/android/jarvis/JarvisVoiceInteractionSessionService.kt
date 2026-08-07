package io.homeassistant.companion.android.jarvis

import android.os.Bundle
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService

/**
 * Session factory referenced from res/xml/jarvis_voice_interaction_service.xml
 * (android:sessionService). One session per assist invocation.
 */
class JarvisVoiceInteractionSessionService : VoiceInteractionSessionService() {

    override fun onNewSession(args: Bundle?): VoiceInteractionSession =
        JarvisVoiceInteractionSession(this)
}
