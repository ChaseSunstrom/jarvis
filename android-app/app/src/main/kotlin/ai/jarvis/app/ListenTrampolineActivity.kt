package ai.jarvis.app

import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.config.JarvisConfig
import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle

/**
 * An Activity whose entire job is to exist for one frame so that starting the
 * microphone service is legal.
 *
 * Android refuses a background start of a foreground service typed `microphone`
 * (see [ai.jarvis.app.assist.WakeStartPolicy]), and the refusal is an exception
 * on 12+ and silence before that — neither of which the user can see. A start
 * from a **resumed Activity** is always permitted, and it also clears the
 * while-in-use restriction that would otherwise leave the recorder handing back
 * digital zero. So every one-tap repair in the app — the "tap to start
 * listening" notification, the quick-settings tile — points here rather than at
 * the service, and this finishes immediately.
 *
 * There is no UI and no theme background: with `Theme.JarvisInvisible` and no
 * window animation, the user sees the notification shade close and the
 * listening notification appear. Nothing flashes.
 */
class ListenTrampolineActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val config = JarvisConfig(this)
        // The tile and the "you turned this off" notification both arrive here
        // meaning "yes, listen" — the switch is the setting, so set it.
        if (intent?.getBooleanExtra(EXTRA_ENABLE, false) == true) {
            config.wakeWordEnabled = true
        }

        if (!config.wakeWordEnabled) {
            finish(); return
        }

        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            // Not requestPermissions() from here: this activity finishes on the
            // same frame it starts, so the result would land on a dead window.
            // The home screen is an ordinary activity that can hold the round
            // trip, and it calls ensureRunning again on the way back.
            startActivity(
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            )
            finish(); return
        }

        // The whole point: this call is being made from a resumed Activity.
        WakeWordService.ensureRunning(this, fromForeground = true)
        finish()
    }

    override fun finish() {
        super.finish()
        // No slide, no fade. A repair tap should look like the thing it
        // repaired coming back, not like an app opening.
        @Suppress("DEPRECATION")
        overridePendingTransition(0, 0)
    }

    companion object {
        /** Turn the wake-word setting on as well as starting the service. */
        const val EXTRA_ENABLE = "ai.jarvis.app.ENABLE_WAKE_WORD"

        fun intent(context: Context, enable: Boolean = false): Intent =
            Intent(context, ListenTrampolineActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(EXTRA_ENABLE, enable)
    }
}
