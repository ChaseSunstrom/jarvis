package ai.jarvis.app.assist

import ai.jarvis.app.config.ServerUrl
import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.util.Log

/**
 * Plays a TTS URL served by jarvis-core. The server's `tts_output.url` needs
 * the bearer token, so it goes in a request header via
 * MediaPlayer.setDataSource(context, uri, headers). Fully self-contained; no
 * extra playback deps.
 *
 * The URL comes from the server, and the token is the key to the whole house.
 * So the origin is checked HERE too, not only in [AssistPipelineClient]: a
 * second caller that forgets must not be able to post the bearer token to a
 * host the user never configured. Off-origin URLs are refused outright rather
 * than played without the header — Jarvis has no business fetching media from
 * somewhere else, and a silent fetch would still leak the device's IP.
 */
class TtsPlayer(
    private val context: Context,
    private val token: String,
    /** The configured jarvis-core base URL. Playback is pinned to its origin. */
    private val serverUrl: String,
) {

    private var player: MediaPlayer? = null

    fun play(url: String, onDone: () -> Unit) {
        stop()
        if (ServerUrl.resolveOnServer(serverUrl, url) != url) {
            Log.w(TAG, "refusing to play a TTS URL that is not on the configured server")
            onDone()
            return
        }
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
