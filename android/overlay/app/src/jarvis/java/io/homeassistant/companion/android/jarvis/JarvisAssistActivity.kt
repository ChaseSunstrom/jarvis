package io.homeassistant.companion.android.jarvis

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.ViewGroup
import android.view.ViewTreeObserver
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import io.homeassistant.companion.android.jarvis.assist.AssistPipelineClient
import io.homeassistant.companion.android.jarvis.assist.MicStreamer
import io.homeassistant.companion.android.jarvis.assist.TtsPlayer

/**
 * Full-conversation Jarvis surface for ACTION_ASSIST / ACTION_VOICE_COMMAND.
 *
 * Unlike a thin trampoline, this activity OWNS the whole interaction and keeps
 * the cinematic orb on screen throughout: it captures the mic, streams to
 * Home Assistant's Assist pipeline over the WebSocket API (same protocol as
 * the web HUD), renders the streaming transcript + response, plays the TTS
 * reply, and loops for follow-up turns until dismissed. Nothing is handed off
 * to HA's own Assist UI, and it depends on no HA-app internals — only the
 * public WebSocket API plus a URL/token from [JarvisConfig].
 *
 * Turn cycle: LISTENING (VAD detects end of speech) -> THINKING -> SPEAKING
 * (TTS) -> LISTENING again. Barge-in: speaking over the reply cancels it and
 * starts a new turn. An inactivity timeout while listening closes the surface.
 */
class JarvisAssistActivity : Activity(), AssistPipelineClient.Callbacks {

    private lateinit var orbView: JarvisOrbView
    private lateinit var transcriptView: TextView
    private lateinit var responseView: TextView

    private lateinit var config: JarvisConfig
    private var client: AssistPipelineClient? = null
    private var mic: MicStreamer? = null
    private var tts: TtsPlayer? = null

    private val ui = Handler(Looper.getMainLooper())
    private var state = AssistPipelineClient.State.IDLE

    // simple energy VAD
    private var speechStartedAt = 0L
    private var lastVoiceAt = 0L
    private var sawSpeech = false
    private var turnActive = false
    private val inactivityRunnable = Runnable { if (isListening()) finishSession("no speech") }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        applyImmersive()

        config = JarvisConfig(this)
        setContentView(buildUi())

        if (!config.isConfigured) {
            startActivity(Intent(this, JarvisSettingsActivity::class.java))
            finish()
            return
        }
        if (!hasMicPermission()) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        beginActivation()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (config.isConfigured && hasMicPermission()) beginActivation()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
                beginActivation()
            } else {
                finishSession("microphone permission denied")
            }
        }
    }

    // --- UI ----------------------------------------------------------------

    private fun buildUi(): ViewGroup {
        val root = FrameLayout(this)
        orbView = JarvisOrbView(this).apply {
            chromeEnabled = true
            setStateLabel("LISTENING")
        }
        root.addView(
            orbView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        val texts = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            val m = dp(24)
            setPadding(m, 0, m, dp(96))
        }
        transcriptView = TextView(this).apply {
            setTextColor(0xCC7FD7EA.toInt())
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            gravity = Gravity.CENTER
            typeface = android.graphics.Typeface.MONOSPACE
        }
        responseView = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            gravity = Gravity.CENTER
            setPadding(0, dp(10), 0, 0)
        }
        texts.addView(transcriptView)
        texts.addView(responseView)
        root.addView(
            texts,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
            )
        )

        // Tap the backdrop to dismiss.
        root.setOnClickListener { finishSession("dismissed") }
        return root
    }

    private fun applyImmersive() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.let { c ->
                c.hide(android.view.WindowInsets.Type.systemBars())
                c.systemBarsBehavior =
                    android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (
                android.view.View.SYSTEM_UI_FLAG_FULLSCREEN
                    or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                )
        }
    }

    // --- activation + conversation ----------------------------------------

    private fun beginActivation() {
        orbView.startEntrance()
        orbView.viewTreeObserver.addOnPreDrawListener(
            object : ViewTreeObserver.OnPreDrawListener {
                override fun onPreDraw(): Boolean {
                    orbView.viewTreeObserver.removeOnPreDrawListener(this)
                    performActivationHaptic()
                    startConversation()
                    return true
                }
            }
        )
    }

    private fun startConversation() {
        tts = TtsPlayer(this, config.token)
        client = AssistPipelineClient(config.haUrl, config.token, this).also {
            it.connect(config.pipeline)
        }
        mic = MicStreamer(
            onPcm = { buf, len -> client?.sendAudio(buf, len) },
            onLevel = ::onMicLevel
        ).also { it.start() }
        // client.onState(LISTENING) is emitted once the run starts.
    }

    private fun beginNextTurn() {
        transcriptView.text = ""
        responseView.text = ""
        sawSpeech = false
        turnActive = true
        client?.startTurn()
    }

    private fun onMicLevel(level: Float) {
        orbView.setAmplitude(level)
        val now = System.currentTimeMillis()

        // Barge-in: talking over the reply cancels it and starts a new turn.
        if (state == AssistPipelineClient.State.SPEAKING && level > BARGE_THRESHOLD) {
            tts?.stop()
            beginNextTurn()
            return
        }
        if (!isListening()) return

        if (level > START_THRESHOLD) {
            if (!sawSpeech) {
                sawSpeech = true
                speechStartedAt = now
                ui.removeCallbacks(inactivityRunnable)
            }
            lastVoiceAt = now
        } else if (sawSpeech &&
            now - speechStartedAt > MIN_SPEECH_MS &&
            now - lastVoiceAt > END_SILENCE_MS
        ) {
            // End of the user's utterance.
            turnActive = false
            client?.endAudio()
        }
    }

    private fun isListening() = state == AssistPipelineClient.State.LISTENING && turnActive

    // --- AssistPipelineClient.Callbacks (all on main thread) --------------

    override fun onState(newState: AssistPipelineClient.State) {
        state = newState
        when (newState) {
            AssistPipelineClient.State.LISTENING -> {
                turnActive = true
                sawSpeech = false
                orbView.setMode(JarvisOrbView.Mode.LISTENING)
                orbView.setStateLabel("LISTENING")
                ui.removeCallbacks(inactivityRunnable)
                ui.postDelayed(inactivityRunnable, INACTIVITY_MS)
            }
            AssistPipelineClient.State.THINKING -> {
                orbView.setMode(JarvisOrbView.Mode.THINKING)
                orbView.setStateLabel("PROCESSING")
                ui.removeCallbacks(inactivityRunnable)
            }
            AssistPipelineClient.State.SPEAKING -> {
                orbView.setMode(JarvisOrbView.Mode.SPEAKING)
                orbView.setStateLabel("RESPONDING")
            }
            AssistPipelineClient.State.IDLE -> {}
        }
    }

    override fun onTranscript(text: String) { transcriptView.text = text }

    override fun onResponseDelta(delta: String) {
        responseView.text = (responseView.text?.toString() ?: "") + delta
    }

    override fun onResponseFinal(text: String) {
        if (text.isNotEmpty()) responseView.text = text
    }

    override fun onTtsUrl(absoluteUrl: String) {
        tts?.play(absoluteUrl) {
            // After the reply, listen again for a follow-up.
            if (!isFinishing) beginNextTurn()
        }
    }

    override fun onRunEnd() {
        // If TTS produced nothing, still continue the loop from LISTENING.
        if (state != AssistPipelineClient.State.SPEAKING && tts?.isPlaying != true) {
            if (!isFinishing) beginNextTurn()
        }
    }

    override fun onError(message: String) {
        Log.w(TAG, "assist error: $message")
        responseView.text = message
        orbView.setStateLabel("ERROR")
        ui.postDelayed({ finishSession("error") }, 2500)
    }

    // --- haptics / lifecycle ----------------------------------------------

    private fun performActivationHaptic() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                vibrator()?.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK))
                return
            } catch (e: Exception) {
                Log.w(TAG, "predefined tick failed", e)
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
            (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

    private fun finishSession(reason: String) {
        Log.d(TAG, "finishing: $reason")
        teardown()
        if (!isFinishing) finish()
    }

    private fun teardown() {
        ui.removeCallbacks(inactivityRunnable)
        mic?.stop(); mic = null
        tts?.stop(); tts = null
        client?.close(); client = null
    }

    override fun onDestroy() {
        teardown()
        super.onDestroy()
    }

    private fun hasMicPermission() =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val TAG = "JarvisAssist"
        private const val REQ_MIC = 4711

        // VAD tuning (RMS 0..1).
        private const val START_THRESHOLD = 0.06f
        private const val BARGE_THRESHOLD = 0.10f
        private const val MIN_SPEECH_MS = 300L
        private const val END_SILENCE_MS = 900L
        private const val INACTIVITY_MS = 8000L

        fun newIntent(context: Context): Intent =
            Intent(context, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
