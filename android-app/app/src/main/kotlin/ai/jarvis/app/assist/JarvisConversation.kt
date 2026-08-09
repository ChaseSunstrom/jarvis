package ai.jarvis.app.assist

import android.content.Context
import android.os.Handler
import android.os.Looper
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView

/**
 * The Jarvis conversation engine, shared by the activation popup
 * ([ai.jarvis.app.JarvisAssistActivity]) and the home screen
 * ([ai.jarvis.app.MainActivity]) so both behave identically.
 *
 * Owns the mic, the pipeline WebSocket client, TTS playback, an energy VAD,
 * barge-in and the multi-turn loop:
 *   LISTENING → (VAD end-of-speech) → THINKING → SPEAKING (TTS) → LISTENING …
 * On [inactivityMs] with no speech while listening, it calls [Ui.onIdle] and
 * stops (the popup finishes; the home screen returns to a tap-to-speak state).
 *
 * All [Ui] callbacks are delivered on the main thread.
 */
class JarvisConversation(
    private val context: Context,
    private val config: JarvisConfig,
    private val ui: Ui,
    private val inactivityMs: Long = 8000L,
) : AssistPipelineClient.Callbacks {

    interface Ui {
        fun onMode(mode: JarvisOrbView.Mode, label: String)
        fun onAmplitude(level: Float)
        fun onTranscript(text: String)
        fun onResponse(text: String)
        fun onError(message: String)
        /** Conversation ended (inactivity/stop): no mic running anymore. */
        fun onIdle()
    }

    private val main = Handler(Looper.getMainLooper())
    private var client: AssistPipelineClient? = null
    private var mic: MicStreamer? = null
    private var tts: TtsPlayer? = null

    private var state = AssistPipelineClient.State.IDLE
    private var running = false

    // energy VAD
    private var speechStartedAt = 0L
    private var lastVoiceAt = 0L
    private var sawSpeech = false
    private var turnActive = false
    private var responseBuffer = StringBuilder()
    private val inactivity = Runnable { if (isListening()) stopWith(idle = true) }

    val isRunning: Boolean get() = running

    fun start() {
        if (running) return
        running = true
        responseBuffer = StringBuilder()
        tts = TtsPlayer(context, config.token, config.serverUrl)
        client = AssistPipelineClient(config.serverUrl, config.token, this).also {
            it.connect(config.pipeline)
        }
        mic = MicStreamer(
            onPcm = { buf, len -> client?.sendAudio(buf, len) },
            onLevel = ::onMicLevel,
        ).also { it.start() }
        // LISTENING is signalled once the pipeline run starts (onState).
    }

    fun stop() = stopWith(idle = false)

    private fun stopWith(idle: Boolean) {
        if (!running && !idle) return
        running = false
        main.removeCallbacks(inactivity)
        mic?.stop(); mic = null
        tts?.stop(); tts = null
        client?.close(); client = null
        state = AssistPipelineClient.State.IDLE
        if (idle) main.post { ui.onIdle() }
    }

    private fun beginNextTurn() {
        if (!running) return
        responseBuffer = StringBuilder()
        sawSpeech = false
        turnActive = true
        main.post {
            ui.onTranscript("")
            ui.onResponse("")
        }
        client?.startTurn()
    }

    private fun onMicLevel(level: Float) {
        ui.onAmplitude(level)
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
                main.removeCallbacks(inactivity)
            }
            lastVoiceAt = now
        } else if (sawSpeech &&
            now - speechStartedAt > MIN_SPEECH_MS &&
            now - lastVoiceAt > END_SILENCE_MS
        ) {
            turnActive = false
            client?.endAudio()
        }
    }

    private fun isListening() =
        running && state == AssistPipelineClient.State.LISTENING && turnActive

    // --- AssistPipelineClient.Callbacks (main thread) ---------------------

    override fun onState(newState: AssistPipelineClient.State) {
        state = newState
        when (newState) {
            AssistPipelineClient.State.LISTENING -> {
                turnActive = true
                sawSpeech = false
                ui.onMode(JarvisOrbView.Mode.LISTENING, "LISTENING")
                if (inactivityMs > 0) {
                    main.removeCallbacks(inactivity)
                    main.postDelayed(inactivity, inactivityMs)
                }
            }
            AssistPipelineClient.State.THINKING -> {
                ui.onMode(JarvisOrbView.Mode.THINKING, "PROCESSING")
                main.removeCallbacks(inactivity)
            }
            AssistPipelineClient.State.SPEAKING ->
                ui.onMode(JarvisOrbView.Mode.SPEAKING, "RESPONDING")
            AssistPipelineClient.State.IDLE -> {}
        }
    }

    override fun onTranscript(text: String) = ui.onTranscript(text)

    override fun onResponseDelta(delta: String) {
        responseBuffer.append(delta)
        ui.onResponse(responseBuffer.toString())
    }

    override fun onResponseFinal(text: String) {
        if (text.isNotEmpty()) {
            responseBuffer = StringBuilder(text)
            ui.onResponse(text)
        }
    }

    override fun onTtsUrl(absoluteUrl: String) {
        tts?.play(absoluteUrl) { if (running) beginNextTurn() }
    }

    override fun onRunEnd() {
        // If no TTS is coming, keep the loop alive from LISTENING.
        if (running && state != AssistPipelineClient.State.SPEAKING &&
            tts?.isPlaying != true
        ) {
            beginNextTurn()
        }
    }

    override fun onError(message: String) {
        ui.onError(message)
        main.postDelayed({ stopWith(idle = true) }, 2500)
    }

    companion object {
        private const val START_THRESHOLD = 0.06f
        private const val BARGE_THRESHOLD = 0.10f
        private const val MIN_SPEECH_MS = 300L
        private const val END_SILENCE_MS = 900L
    }
}
