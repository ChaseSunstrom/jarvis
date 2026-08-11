package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.assist.ToolActivityView
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.ReadabilityScrim
import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.graphics.Typeface
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.text.TextUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.ViewGroup
import android.view.WindowManager
import android.view.ViewTreeObserver
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
    private lateinit var captionView: TextView
    private lateinit var transcriptView: TextView
    private lateinit var responseView: TextView
    private lateinit var toolActivityView: ToolActivityView
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
        sizeAsCard()

        if (!config.isConfigured) {
            startActivity(Intent(this, SettingsActivity::class.java))
            finish(); return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            // NOT requestPermissions() from here. This activity is
            // android:noHistory, so the platform finishes it the moment the
            // permission dialog covers it — onRequestPermissionsResult would
            // never run, and the first grant would land on a dead activity.
            // That is a first-run dead end: the assist gesture appears to do
            // nothing, forever, until the user finds the app icon.
            //
            // So hand off to the home screen, which is an ordinary activity
            // that can hold a permission round trip — the same shape as the
            // not-configured hand-off above.
            startActivity(
                Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            )
            finish(); return
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

    /**
     * A small card near the bottom of the screen, not a full-screen surface.
     *
     * The theme carries `windowIsFloating`; without it this call is a no-op
     * because the platform forces an Activity window to MATCH_PARENT. Bottom
     * gravity because this is a thumb-reachable popup, and the point of a
     * dimmed floating window is that the user can still see what it
     * interrupted.
     */
    private fun sizeAsCard() {
        val screen = resources.displayMetrics.widthPixels
        val width = minOf(screen - JarvisUi.dp(this, 32), JarvisUi.dp(this, 340))
        window.setLayout(width, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.attributes = window.attributes.also {
            it.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            it.y = JarvisUi.dp(this, 56)
        }
        blurBehind()
    }

    /**
     * Blur what is behind the card, where the platform can.
     *
     * This surface already had the theme's 0.5 dim, which is why the popup on
     * a LOCKED phone reads better than the overlay did on an unlocked one —
     * two paths to the same orb, only one of them with a ground. They match
     * now: the same blur here, the same [ReadabilityScrim] under the content.
     *
     * `setBackgroundBlurRadius` blurs within this window's own bounds, which
     * for a floating translucent window is exactly the card. API 31+, and a
     * request even there — `isCrossWindowBlurEnabled` is false under battery
     * saver and on hardware that cannot — so nothing depends on it landing.
     */
    private fun blurBehind() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        runCatching {
            window.addFlags(WindowManager.LayoutParams.FLAG_BLUR_BEHIND)
            window.attributes = window.attributes.also {
                it.blurBehindRadius = JarvisUi.dp(this, BLUR_DP)
            }
            window.setBackgroundBlurRadius(JarvisUi.dp(this, BLUR_DP))
        }.onFailure { Log.w(TAG, "the platform refused a background blur", it) }
    }

    private fun buildUi(): ViewGroup {
        val pad = JarvisUi.dp(this, 20)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, pad, pad, pad)
            // Not a panel — see ReadabilityScrim. The theme's dim is a flat
            // wash over the whole screen, which stops the app behind competing
            // but does nothing for the orb's own edge; this is the gradient
            // that gives the orb and the words a ground of their own, with no
            // border anywhere. The text keeps its shadow on top of both.
            background = ReadabilityScrim()
        }

        orbView = JarvisOrbView(this).apply {
            // The chrome is screen-scale: corner brackets 18dp from the VIEW
            // edges and a wordmark anchored to the resting outer radius, both
            // of which are nonsense inside a 200dp box.
            chromeEnabled = false
            // The scrim stays off with it. A near-opaque vignette the size of
            // the view is what turned this popup into a blackout, and now that
            // there is no card behind the orb it would BE the box this surface
            // just lost. The window's dim is the ground.
            scrimEnabled = false
        }
        root.addView(
            orbView,
            LinearLayout.LayoutParams(
                JarvisUi.dp(this, ORB_DP),
                JarvisUi.dp(this, ORB_DP)
            )
        )

        // The state readout the orb used to paint itself. A real view, so it is
        // in the accessibility tree and a test can read it.
        captionView = TextView(this).apply {
            text = "LISTENING"
            setTextColor(JarvisOrbView.Mode.LISTENING.color)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            letterSpacing = 0.2f
            typeface = Typeface.MONOSPACE
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, 10), 0, 0)
        }
        root.addView(captionView, fullWidth())

        // What the turn is DOING, above what it is saying. Hidden until there
        // is something to show, so an ordinary question looks exactly as it did.
        toolActivityView = ToolActivityView(this).apply {
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, 10), 0, 0)
        }
        root.addView(toolActivityView, fullWidth())

        transcriptView = JarvisUi.transcriptView(this).apply {
            // Brighter than the shared transcript colour, for the same reason
            // AssistOverlay does it — see overlay_scrim_test.py.
            setTextColor(JarvisUi.TEXT)
            maxLines = 3
            ellipsize = TextUtils.TruncateAt.END
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, 12), 0, 0)
        }
        responseView = JarvisUi.responseView(this).apply {
            maxLines = 4
            ellipsize = TextUtils.TruncateAt.END
        }
        for (view in arrayOf(captionView, transcriptView, responseView)) {
            // What replaces the card: a hard shadow, so the text survives being
            // drawn over whatever the dim is over rather than needing a slab
            // behind it.
            view.setShadowLayer(
                JarvisUi.dp(this, 6).toFloat(),
                0f,
                JarvisUi.dp(this, 1).toFloat(),
                0xF0000308.toInt(),
            )
        }
        root.addView(transcriptView, fullWidth())
        root.addView(responseView, fullWidth())

        root.setOnClickListener { finish() }
        return root
    }

    private fun fullWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

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
                    // Take the microphone off the wake listener before opening
                    // one of our own. Two AudioRecords on one device is a coin
                    // toss over which gets the audio, and losing it means the
                    // conversation the user just triggered hears nothing.
                    WakeWordService.pause(this@JarvisAssistActivity)
                    convo = JarvisConversation(
                        this@JarvisAssistActivity, config, this@JarvisAssistActivity,
                        inactivityMs = 8000L,
                        // This screen is the wake word's fallback surface — a
                        // full-screen intent on a locked phone — so the user is
                        // already talking here too.
                        speechAlreadyUnderway = true,
                    ).also { it.start() }
                    return true
                }
            }
        )
    }

    // --- JarvisConversation.Ui (main thread) ------------------------------

    override fun onMode(mode: JarvisOrbView.Mode, label: String) {
        orbView.setMode(mode)
        captionView.text = label
        captionView.setTextColor(mode.color)
    }

    override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

    override fun onTranscript(text: String) { transcriptView.text = text }

    override fun onResponse(text: String) { responseView.text = text }

    override fun onError(message: String) {
        responseView.text = message
        captionView.text = "ERROR"
        captionView.setTextColor(JarvisOrbView.Mode.ERROR.color)
        orbView.setMode(JarvisOrbView.Mode.ERROR)
    }

    override fun onTools(run: ToolRun) = toolActivityView.render(run)

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
        // Give the microphone back. Unconditional and after the stop, so the
        // listener never re-opens it while this one is still closing — and a
        // resume with the setting off is a no-op, so the asymmetric case where
        // the user turned wake-word off mid-conversation is safe too.
        WakeWordService.resume(this)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JarvisAssist"

        /** Blur radius behind the card, in dp. Matches AssistOverlay's. */
        private const val BLUR_DP = 28
        private const val REQ_MIC = 4711

        /** Set when the popup was opened by the wake word rather than by a tap. */
        const val EXTRA_FROM_WAKE_WORD = "ai.jarvis.app.FROM_WAKE_WORD"

        /** Side of the orb's slot in the card. The reactor sizes itself to it. */
        private const val ORB_DP = 200

        fun newIntent(context: Context): Intent =
            Intent(context, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
