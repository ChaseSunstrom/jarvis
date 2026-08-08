package ai.jarvis.app.automation.actions.builtin

import android.content.Context
import android.media.AudioManager
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.os.SystemClock
import android.view.KeyEvent
import ai.jarvis.app.automation.actions.ActionEnv
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.clampPercent
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.policy.ActionTier
import org.json.JSONObject

/**
 * Media transport. All Tier 1 — the brief names "media play/pause" and
 * "set volume" as Tier 1, and every one of these is undone by pressing the
 * opposite button.
 *
 * Two mechanisms, in order of preference:
 *
 *  1. `MediaSessionManager` — precise (targets the actual session, tells you
 *     which app answered) but needs notification-listener access, so it is used
 *     only when [ActionEnv.notificationListener] has been registered and the
 *     user has granted it.
 *  2. `AudioManager.dispatchMediaKeyEvent` — needs no special access at all and
 *     is what the physical headset buttons do. Always available fallback.
 */
internal class MediaKeyAction(
    override val id: String,
    override val description: String,
    private val keyCode: Int,
    private val transport: (MediaController.TransportControls) -> Unit
) : JarvisAction {
    override val tier = ActionTier.AUTO
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "media"
    override val timeoutMs = 5_000L

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        viaSession(ctx, transport) ?: viaMediaKey(ctx, keyCode)

    private fun viaSession(
        ctx: Context,
        apply: (MediaController.TransportControls) -> Unit
    ): ActionResult? {
        val component = ActionEnv.notificationListener ?: return null
        val msm = ctx.getSystemService(MediaSessionManager::class.java) ?: return null
        return try {
            val controller = msm.getActiveSessions(component).firstOrNull() ?: return null
            apply(controller.transportControls)
            ActionResult.ok(json("via" to "media_session", "app" to controller.packageName))
        } catch (e: SecurityException) {
            // Notification access not actually granted — fall back silently.
            null
        } catch (e: Exception) {
            null
        }
    }

    private fun viaMediaKey(ctx: Context, code: Int): ActionResult {
        val am = ctx.getSystemService(AudioManager::class.java)
            ?: return ActionResult.error("no audio service")
        val now = SystemClock.uptimeMillis()
        return try {
            am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0))
            am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, code, 0))
            ActionResult.ok(json("via" to "media_key", "key" to code))
        } catch (e: Exception) {
            ActionResult.error("media key dispatch failed: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}

/** Tier 1 — same thing as [SetVolume] with stream=music, kept as its own id for the model. */
object SetMediaVolume : JarvisAction {
    override val id = "set_media_volume"
    override val tier = ActionTier.AUTO
    override val description = "Set the media/music volume as a percentage."
    override val paramsSchema = mapOf("level" to "int 0-100")
    override val capability = "media"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val am = ctx.getSystemService(AudioManager::class.java)
            ?: return ActionResult.error("no audio service")
        if (!params.has("level")) return ActionResult.error("level (0-100) is required")
        val percent = params.intOr("level", 0).clampPercent()
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        return try {
            am.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(max * percent / 100f), 0)
            ActionResult.ok(json("level" to percent, "max" to max))
        } catch (e: SecurityException) {
            ActionResult.error("the system refused to change the media volume")
        }
    }
}

/** The transport set, built once. */
object MediaActions {
    val play = MediaKeyAction(
        "media_play", "Resume media playback.", KeyEvent.KEYCODE_MEDIA_PLAY
    ) { it.play() }

    val pause = MediaKeyAction(
        "media_pause", "Pause media playback.", KeyEvent.KEYCODE_MEDIA_PAUSE
    ) { it.pause() }

    val next = MediaKeyAction(
        "media_next", "Skip to the next track.", KeyEvent.KEYCODE_MEDIA_NEXT
    ) { it.skipToNext() }

    val previous = MediaKeyAction(
        "media_previous", "Go back to the previous track.", KeyEvent.KEYCODE_MEDIA_PREVIOUS
    ) { it.skipToPrevious() }

    val stop = MediaKeyAction(
        "media_stop", "Stop media playback.", KeyEvent.KEYCODE_MEDIA_STOP
    ) { it.stop() }

    val all: List<JarvisAction> = listOf(play, pause, next, previous, stop, SetMediaVolume)
}
