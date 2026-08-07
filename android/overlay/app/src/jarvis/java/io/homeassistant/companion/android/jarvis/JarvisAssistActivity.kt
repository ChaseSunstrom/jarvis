package io.homeassistant.companion.android.jarvis

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Vibrator
import android.os.VibratorManager
import android.os.VibrationEffect
import android.util.Log
import android.view.HapticFeedbackConstants
import android.view.ViewGroup
import android.view.ViewTreeObserver
import io.homeassistant.companion.android.assist.AssistActivity

/**
 * Siri-like activation surface for ACTION_ASSIST / ACTION_VOICE_COMMAND.
 *
 * Design (documented tradeoff, see docs/android.md):
 * This activity is deliberately dumb. It owns only the first ~250 ms of the
 * experience - haptic tick, edge-light sweep, orb rise - and then forwards to
 * the Home Assistant app's own [AssistActivity] with startListening=true,
 * finishing itself with a crossfade. We do NOT reach into AssistActivity's
 * ViewModel/pipeline internals from the overlay: those are private and churn
 * upstream. The handoff intent comes from AssistActivity.newJarvisIntent(...),
 * a two-line public helper that overlay/patches/apply.py adds to the fork's
 * AssistActivity companion (it just calls the existing newInstance() with
 * startListening = true, fromFrontend = false). If upstream renames things,
 * the build fails loudly at that one helper instead of breaking at runtime.
 *
 * Cost of the tradeoff: the handoff is a crossfade, not shared-element
 * continuity, and mic capture starts when AssistActivity starts it (~250 ms
 * in) rather than at frame zero. Measured cold-start budget is still <300 ms
 * to visible+haptic feedback; verify with:
 *   adb shell am start -W -a android.intent.action.ASSIST
 */
class JarvisAssistActivity : Activity() {

    private lateinit var orbView: JarvisOrbView
    private var forwarded = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }

        orbView = JarvisOrbView(this)
        setContentView(
            orbView,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        beginActivation()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // singleTask relaunch (assist gesture while already alive): replay.
        forwarded = false
        beginActivation()
    }

    private fun beginActivation() {
        orbView.startEntrance()
        // Haptic + forward are scheduled off the first drawn frame so the
        // user sees light before anything heavier happens.
        orbView.viewTreeObserver.addOnPreDrawListener(
            object : ViewTreeObserver.OnPreDrawListener {
                override fun onPreDraw(): Boolean {
                    orbView.viewTreeObserver.removeOnPreDrawListener(this)
                    performActivationHaptic()
                    orbView.postDelayed({ forwardToAssist() }, JarvisOrbView.ENTRANCE_MS)
                    return true
                }
            }
        )
    }

    private fun performActivationHaptic() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                vibrator()?.vibrate(
                    VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK)
                )
                return
            } catch (e: Exception) {
                Log.w(TAG, "Predefined tick failed, falling back to view haptic", e)
            }
        }
        @Suppress("DEPRECATION")
        orbView.performHapticFeedback(
            HapticFeedbackConstants.KEYBOARD_TAP,
            HapticFeedbackConstants.FLAG_IGNORE_GLOBAL_SETTING
        )
    }

    private fun vibrator(): Vibrator? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)
                ?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

    private fun forwardToAssist() {
        if (forwarded || isFinishing) return
        forwarded = true
        try {
            // Public helper added to the fork by overlay/patches/apply.py:
            // returns AssistActivity.newInstance(context, startListening = true,
            // fromFrontend = false).
            startActivity(AssistActivity.newJarvisIntent(this))
            @Suppress("DEPRECATION")
            overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch AssistActivity", e)
        }
        finish()
    }

    companion object {
        private const val TAG = "JarvisAssist"

        fun newIntent(context: Context): Intent =
            Intent(context, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
