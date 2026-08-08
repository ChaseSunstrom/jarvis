package io.homeassistant.companion.android.jarvis.assist

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.util.Log

/**
 * Plays a Home Assistant TTS URL. HA's tts_output.url needs the bearer token,
 * so we pass it as a request header via MediaPlayer.setDataSource(context, uri,
 * headers). Fully self-contained; no extra playback deps.
 */
class TtsPlayer(private val context: Context, private val token: String) {

    private var player: MediaPlayer? = null

    fun play(url: String, onDone: () -> Unit) {
        stop()
        val mp = MediaPlayer()
        player = mp
        mp.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
        )
        try {
            mp.setDataSource(
                context,
                Uri.parse(url),
                mapOf("Authorization" to "Bearer $token")
            )
        } catch (e: Exception) {
            Log.e(TAG, "setDataSource failed", e)
            release(mp); onDone(); return
        }
        mp.setOnPreparedListener { it.start() }
        mp.setOnCompletionListener {
            release(mp)
            onDone()
        }
        mp.setOnErrorListener { _, what, extra ->
            Log.e(TAG, "playback error $what/$extra")
            release(mp)
            onDone()
            true
        }
        try {
            mp.prepareAsync()
        } catch (e: Exception) {
            Log.e(TAG, "prepareAsync failed", e)
            release(mp); onDone()
        }
    }

    val isPlaying: Boolean
        get() = try { player?.isPlaying == true } catch (_: Exception) { false }

    fun stop() {
        player?.let { release(it) }
        player = null
    }

    private fun release(mp: MediaPlayer) {
        try { if (mp.isPlaying) mp.stop() } catch (_: Exception) {}
        try { mp.reset() } catch (_: Exception) {}
        try { mp.release() } catch (_: Exception) {}
        if (player === mp) player = null
    }

    companion object {
        private const val TAG = "JarvisTts"
    }
}
