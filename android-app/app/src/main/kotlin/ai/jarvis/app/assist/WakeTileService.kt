package ai.jarvis.app.assist

import ai.jarvis.app.ListenTrampolineActivity
import ai.jarvis.app.config.JarvisConfig
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.util.Log

/**
 * "Hey Jarvis" as a quick-settings tile.
 *
 * The switch for always-on listening lives in Settings behind a save button,
 * which is the right home for a preference and the wrong one for something the
 * user has to reach for after every reboot — and, until the rest of this
 * change landed, they did. The tile is the one-swipe version: pull down, tap,
 * listening.
 *
 * A tile click is not a foreground Activity, so it cannot start a
 * microphone-typed service directly any more than a boot receiver can. It goes
 * through [ListenTrampolineActivity] for the same reason everything else does.
 * Turning listening *off* has no such problem — stopping a service is always
 * allowed — so that half is immediate.
 */
class WakeTileService : TileService() {

    override fun onStartListening() {
        super.onStartListening()
        refresh()
    }

    override fun onTileAdded() {
        super.onTileAdded()
        refresh()
    }

    override fun onClick() {
        super.onClick()
        val config = JarvisConfig(this)
        if (config.wakeWordEnabled) {
            config.wakeWordEnabled = false
            WakeWordService.cancelHeartbeat(this)
            WakeWordService.clearAttention(this)
            try {
                stopService(Intent(this, WakeWordService::class.java))
            } catch (t: Throwable) {
                Log.w(TAG, "could not stop the wake listener", t)
            }
            refresh()
            return
        }
        // Optimistic: paint the tile on before the trampoline runs, so the tap
        // feels like a switch rather than a request. The trampoline sets the
        // same flag, and onStartListening re-reads it when the shade comes
        // back, so a refused start corrects itself rather than lying.
        config.wakeWordEnabled = true
        refresh()
        launchTrampoline()
    }

    private fun launchTrampoline() {
        val intent = ListenTrampolineActivity.intent(this, enable = true)
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                // The Intent overload throws for apps targeting 34+; this is
                // the replacement, and it is the only one that exists there.
                startActivityAndCollapse(
                    PendingIntent.getActivity(
                        this,
                        5,
                        intent,
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                    )
                )
            } else {
                // Deprecated in 34 and the ONLY one that exists below it: the
                // PendingIntent overload was added in 34. Both suppressions
                // name the same fact from lint's two directions.
                @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
                startActivityAndCollapse(intent)
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not open the listener from the tile", t)
            // Last resort: the service start will be refused on 12+ from here,
            // but ensureRunning turns that into a notification the user can
            // tap, which still beats a tile that does nothing.
            WakeWordService.ensureRunning(this)
        }
    }

    private fun refresh() {
        val tile = qsTile ?: return
        val on = JarvisConfig(this).wakeWordEnabled
        tile.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        if (Build.VERSION.SDK_INT >= 29) {
            tile.subtitle = if (on) "Listening" else "Off"
        }
        tile.updateTile()
    }

    companion object {
        private const val TAG = "JarvisWakeTile"
    }
}
