package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.ViewGroup
import android.view.ViewTreeObserver
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Siri-like activation surface for ACTION_ASSIST / ACTION_VOICE_COMMAND — the
 * transparent, lock-screen-capable popup shown by the assist gesture, the
 * assistant role, or a wake word (via the VoiceInteractionService). It hosts
 * the cinematic Jarvis orb and runs the whole conversation itself through the
 * shared [JarvisConversation] engine. Auto-closes on inactivity, a tap, or Back.
 */
class JarvisAssistActivity : Activity(), JarvisConversation.Ui {

    private lateinit var orbView: JarvisOrbView
    private lateinit var transcriptView: TextView
    private lateinit var responseView: TextView
    private lateinit var config: JarvisConfig
    private var convo: JarvisConversation? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        JarvisUi.immersive(this)

        config = JarvisConfig(this)
        setContentView(buildUi())

        if (!config.isConfigured) {
            startActivity(Intent(this, SettingsActivity::class.java))
            finish(); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        begin()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (config.isConfigured &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) begin()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) begin()
            else finish()
        }
    }

    private fun buildUi(): ViewGroup {
        val root = FrameLayout(this)
        orbView = JarvisOrbView(this).apply {
            chromeEnabled = true
            setStateLabel("LISTENING")
        }
        root.addView(
            orbView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        val texts = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            val m = JarvisUi.dp(this@JarvisAssistActivity, 24)
            setPadding(m, 0, m, JarvisUi.dp(this@JarvisAssistActivity, 96))
        }
        transcriptView = JarvisUi.transcriptView(this)
        responseView = JarvisUi.responseView(this)
        texts.addView(transcriptView)
        texts.addView(responseView)
        root.addView(
            texts,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        )

        root.setOnClickListener { finish() }
        return root
    }

    private fun begin() {
        // A second assist invocation is delivered to this same instance
        // (singleTask), and starting another conversation without ending the
        // first leaves an AudioRecord, a capture thread and a WebSocket running
        // with nothing holding them — two open mics for one question.
        convo?.stop()
        convo = null
        orbView.startEntrance()
        orbView.viewTreeObserver.addOnPreDrawListener(
            object : ViewTreeObserver.OnPreDrawListener {
                override fun onPreDraw(): Boolean {
                    orbView.viewTreeObserver.removeOnPreDrawListener(this)
                    haptic()
                    convo = JarvisConversation(
                        this@JarvisAssistActivity, config, this@JarvisAssistActivity,
                        inactivityMs = 8000L,
                    ).also { it.start() }
                    return true
                }
            }
        )
    }

    // --- JarvisConversation.Ui (main thread) ------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        orbView.setMode(mode); orbView.setStateLabel(label)
    }

    override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

    override fun onTranscript(text: String) { transcriptView.text = text }

    override fun onResponse(text: String) { responseView.text = text }

    override fun onError(message: String) {
        responseView.text = message; orbView.setStateLabel("ERROR")
    }

    override fun onIdle() { if (!isFinishing) finish() }

    // --- haptics / lifecycle ---------------------------------------------

    private fun haptic() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                jarvisVibrator()?.vibrate(
                    VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK)
                )
                return
            } catch (e: Exception) { Log.w(TAG, "tick failed", e) }
        }
        @Suppress("DEPRECATION")
        orbView.performHapticFeedback(
            HapticFeedbackConstants.KEYBOARD_TAP,
            HapticFeedbackConstants.FLAG_IGNORE_GLOBAL_SETTING
        )
    }

    private fun jarvisVibrator(): Vibrator? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

    override fun onDestroy() {
        convo?.stop(); convo = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JarvisAssist"
        private const val REQ_MIC = 4711

        fun newIntent(context: Context): Intent =
            Intent(context, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
