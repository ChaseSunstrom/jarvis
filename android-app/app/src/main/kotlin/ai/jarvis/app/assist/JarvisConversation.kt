package ai.jarvis.app.assist

import android.content.Context
import android.os.Handler
import android.os.Looper
import ai.jarvis.app.audio.CaptureProfile
import ai.jarvis.app.audio.HeadsetMonitor
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

    /**
     * Headset discovery. Started with the conversation and stopped with it, so
     * Jarvis is not registered for audio-device callbacks while idle.
     */
    private val headsets = HeadsetMonitor(context) { /* re-read on next turn */ }
        .also { it.headsetModeEnabled = config.headsetMode }

    /** True while [HeadsetMonitor.clearCommunicationRoute] is owed. */
    private var routeApplied = false

    private var state = AssistPipelineClient.State.IDLE
    private var running = false

    // energy VAD
    private var speechStartedAt = 0L
    private var lastVoiceAt = 0L
    /** When the level first went above [START_THRESHOLD] in the current run. */
    private var aboveSince = 0L
    private var sawSpeech = false
    private var turnActive = false
    /** Loudest smoothed level seen this conversation, for the dead-mic hint. */
    private var peakLevel = 0f
    /** True once the pipeline reached LISTENING at least once this conversation. */
    private var reachedListening = false
    private var responseBuffer = StringBuilder()

    /**
     * Nothing was heard within [inactivityMs] of the pipeline asking for audio.
     *
     * Two outcomes, deliberately different. If speech was detected the turn is
     * simply over and this is a normal end. If it was NOT, the microphone
     * produced [inactivityMs] of nothing usable, and saying so is the whole
     * point: without it a revoked permission, a muted mic, a mic another app is
     * holding and a perfectly working mic in a quiet room all look identical —
     * the surface just closes.
     */
    private val inactivity = Runnable {
        if (!isListening()) return@Runnable
        if (sawSpeech) {
            stopWith(idle = true)
        } else {
            // Three different faults used to arrive here wearing the same
            // sentence, which sent the owner of this app after a microphone
            // permission that was never the problem. They are distinguishable
            // and so they are distinguished: the peak level says whether any
            // audio arrived at all, which is the fork nothing else can tell you
            // from the outside.
            ui.onError(silenceDiagnosis())
            main.postDelayed({ if (running) stopWith(idle = true) }, ERROR_LINGER_MS)
        }
    }

    /**
     * Why a turn ended with no speech, told apart by the evidence available.
     *
     * [peakLevel] is the loudest smoothed RMS the mic produced since the
     * conversation started. It is the discriminator: capture runs independently
     * of the socket (see [start]), so a peak of a flat zero means the recorder
     * handed back digital silence, and any real peak means audio arrived and it
     * was the threshold or the room that fell short.
     */
    private fun silenceDiagnosis(): String = when {
        peakLevel <= DEAD_MIC_LEVEL -> DEAD_MIC
        peakLevel < START_THRESHOLD -> TOO_QUIET.format(peakLevel, START_THRESHOLD)
        else -> NOTHING_HEARD
    }

    /**
     * Backstop for a turn whose end-of-speech never arrives.
     *
     * [inactivity] is disarmed the moment speech is detected, so without this a
     * room whose noise floor sits in the [END_THRESHOLD]..[START_THRESHOLD]
     * dead band after one cough would hold the turn open forever: the level
     * matches neither edge, `endAudio()` is never sent, and nothing is left to
     * time out. Ending the audio is the right response rather than tearing the
     * conversation down — we have the user's speech, so let the server
     * transcribe it.
     */
    private val turnCap = Runnable { if (isListening() && sawSpeech) endTurnAudio() }

    val isRunning: Boolean get() = running

    fun start() {
        if (running) return
        running = true
        responseBuffer = StringBuilder()

        // Resolve the audio route once per conversation. A headset connected
        // mid-turn takes effect on the next one rather than tearing this one
        // down under the user.
        headsets.headsetModeEnabled = config.headsetMode
        headsets.start()
        val route = headsets.route
        val profile = CaptureProfile.forRoute(route)
        routeApplied = headsets.applyCommunicationRoute(profile)

        tts = TtsPlayer(context, config.token, config.serverUrl).also {
            // Capture source and playback usage are one decision: an AEC with
            // no reference signal cancels nothing. See TtsPlayer.communicationRoute.
            it.communicationRoute = profile.useVoiceCommunication
        }
        client = AssistPipelineClient(
            config.serverUrl,
            config.token,
            this,
            serverKind = config.serverKind,
            onKindResolved = { config.serverKind = it },
        ).also {
            it.connect(config.pipeline)
        }
        mic = MicStreamer(
            onPcm = { buf, len -> client?.sendAudio(buf, len) },
            onLevel = ::onMicLevel,
            captureProfile = { profile },
            // Capture never started. Say why, then end the conversation rather
            // than leaving the orb listening to a microphone that is not there.
            onUnavailable = { reason ->
                ui.onError(reason)
                main.postDelayed({ if (running) stopWith(idle = true) }, ERROR_LINGER_MS)
            },
        ).also { it.start() }

        // LISTENING is signalled once the pipeline run starts (onState). If it
        // never arrives, nothing used to say so: the mic ran, the orb sat there,
        // and the surface closed on its own timer with no message, which is
        // indistinguishable from "it did not hear me" and was read that way for
        // a long time. The VAD is gated on LISTENING (see isListening), so a
        // pipeline that never starts can never produce speech no matter how
        // loudly anyone talks — that is a connection fault and it should say so.
        main.postDelayed(handshake, HANDSHAKE_MS)
    }

    /**
     * The pipeline did not start in time. Names the server, because the usual
     * cause is the app pointed at the wrong one — jarvis-web's console on 8199
     * rather than jarvis-core's API on 8080 — and the two are easy to confuse
     * when both are "Jarvis" and both answer on the same host.
     */
    private val handshake = Runnable {
        if (!running || reachedListening) return@Runnable
        ui.onError(NO_PIPELINE.format(config.serverUrl))
        main.postDelayed({ if (running) stopWith(idle = true) }, ERROR_LINGER_MS)
    }

    fun stop() = stopWith(idle = false)

    private fun stopWith(idle: Boolean) {
        if (!running && !idle) return
        running = false
        peakLevel = 0f
        reachedListening = false
        main.removeCallbacks(inactivity)
        main.removeCallbacks(handshake)
        main.removeCallbacks(turnCap)
        mic?.stop(); mic = null
        tts?.stop(); tts = null
        client?.close(); client = null
        // Unconditionally, and before anything that could throw: on API < 31 a
        // leaked SCO link holds the headset in call mode, which silences music
        // system-wide until something else tears it down. Releasing a route we
        // never applied is a no-op, so the asymmetric case is safe too.
        if (routeApplied) {
            headsets.clearCommunicationRoute()
            routeApplied = false
        }
        headsets.stop()
        state = AssistPipelineClient.State.IDLE
        if (idle) main.post { ui.onIdle() }
    }

    private fun beginNextTurn() {
        if (!running) return
        responseBuffer = StringBuilder()
        sawSpeech = false
        aboveSince = 0L
        turnActive = true
        main.removeCallbacks(turnCap)
        main.post {
            ui.onTranscript("")
            ui.onResponse("")
        }
        client?.startTurn()
    }

    private fun onMicLevel(level: Float) {
        ui.onAmplitude(level)
        if (level > peakLevel) peakLevel = level
        val now = System.currentTimeMillis()

        // Barge-in: talking over the reply cancels it and starts a new turn.
        if (state == AssistPipelineClient.State.SPEAKING && level > BARGE_THRESHOLD) {
            tts?.stop()
            beginNextTurn()
            return
        }
        if (!isListening()) return

        if (level > START_THRESHOLD) {
            // Start edge, debounced. MIN_SPEECH_MS is a minimum turn LENGTH,
            // not a start guard, so without this a single 64 ms transient — a
            // cough, a chair, a door — latches the turn and disarms the
            // inactivity timeout. jarvis-web gates the same edge on 120 ms of
            // sustained energy (lib/wake.ts minSpeechMs).
            if (aboveSince == 0L) aboveSince = now
            if (!sawSpeech && now - aboveSince >= START_DEBOUNCE_MS) {
                sawSpeech = true
                speechStartedAt = now
                main.removeCallbacks(inactivity)
                main.removeCallbacks(turnCap)
                main.postDelayed(turnCap, MAX_TURN_MS)
            }
            if (sawSpeech) lastVoiceAt = now
        } else {
            aboveSince = 0L
            if (level >= END_THRESHOLD) {
                // The dead band between the two thresholds is ambiguous audio,
                // not silence, so it must not advance the hangover — the same
                // rule as jarvis-web, which clears `belowSince` whenever the
                // level is not below endThreshold (lib/wake.ts).
                lastVoiceAt = now
            } else if (sawSpeech &&
                now - speechStartedAt > MIN_SPEECH_MS &&
                now - lastVoiceAt > END_SILENCE_MS
            ) {
                endTurnAudio()
            }
        }
    }

    /** End-of-audio for this turn: the sentinel jarvis-core waits on. */
    private fun endTurnAudio() {
        if (!turnActive) return
        turnActive = false
        main.removeCallbacks(turnCap)
        client?.endAudio()
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
                aboveSince = 0L
                reachedListening = true
                main.removeCallbacks(handshake)
                main.removeCallbacks(turnCap)
                ui.onMode(JarvisOrbView.Mode.LISTENING, "LISTENING")
                if (inactivityMs > 0) {
                    main.removeCallbacks(inactivity)
                    main.postDelayed(inactivity, inactivityMs)
                }
            }
            AssistPipelineClient.State.THINKING -> {
                ui.onMode(JarvisOrbView.Mode.THINKING, "PROCESSING")
                main.removeCallbacks(inactivity)
                main.removeCallbacks(turnCap)
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
        /**
         * Energy VAD thresholds, on the same 0..1 smoothed-RMS scale
         * [MicStreamer] reports.
         *
         * These were 0.06 for both edges, which is -24 dBFS, and no
         * conversational speech off a phone mic at arm's length ever reached
         * it: real speech lands around 0.01-0.03 raw, and MicStreamer's
         * one-pole smoother (alpha 0.3 per 64 ms chunk) tracks the quarter-
         * second mean of the envelope rather than the vowel peaks, pulling it
         * down further. The capture source makes it worse rather than better —
         * VOICE_RECOGNITION is chosen precisely because it is un-AGC'd (see
         * AudioRoute), so nothing lifts the level towards the threshold.
         *
         * The only signal that ever exercised this VAD was SyntheticSpeech, a
         * continuous 220 Hz sine deliberately sitting five times above it, so
         * the emulator suite could not see the problem.
         *
         * These are jarvis-web's numbers (`src/lib/wake.ts`: startThreshold
         * 0.02, endThreshold 0.01), which is the client that demonstrably
         * works — and it feeds on a getUserMedia stream with autoGainControl
         * enabled, i.e. a signal that is already louder than this one.
         *
         * Separate start and end edges give the hysteresis a single threshold
         * cannot: crossing 0.02 starts a turn, and only falling below 0.01
         * counts towards the hangover.
         */
        private const val START_THRESHOLD = 0.02f
        private const val END_THRESHOLD = 0.01f

        /**
         * Barge-in over the reply. Must stay well above [START_THRESHOLD]: it
         * is answered by cancelling TTS and starting a new turn, so a value
         * near the start edge would make the phone interrupt itself.
         */
        private const val BARGE_THRESHOLD = 0.10f

        /** Sustained energy required to latch the start edge (jarvis-web: 120 ms). */
        private const val START_DEBOUNCE_MS = 120L

        /** Minimum length of a turn before its end may be declared. */
        private const val MIN_SPEECH_MS = 300L

        /** Trailing silence that ends a turn. */
        private const val END_SILENCE_MS = 900L

        /**
         * Hard cap on one turn's audio. Only a backstop — see [turnCap]. Long
         * enough that no real utterance is cut off.
         */
        private const val MAX_TURN_MS = 30_000L

        /** How long an error stays on screen before the surface closes. */
        private const val ERROR_LINGER_MS = 2_000L

        /**
         * How long the pipeline has to reach LISTENING before the surface says
         * it did not. Generous: a cold jarvis-core loading a wake-word model
         * takes a couple of seconds, and a false accusation about the server is
         * worse than a slow true one.
         */
        private const val HANDSHAKE_MS = 6_000L

        /**
         * A peak at or below this is digital silence, not a quiet room. Not
         * exactly zero: a recorder that is "working" but muted still emits
         * dither and the odd non-zero sample, and calling that a live mic would
         * put the user back to hunting a permission that is already granted.
         */
        private const val DEAD_MIC_LEVEL = 0.0005f

        /**
         * Said when the microphone ran and the VAD heard nothing at all. Not a
         * pipeline error — an honest report that there was no audio, which is
         * the one thing the old silent-close path never told anybody.
         */
        private const val NOTHING_HEARD =
            "I did not hear anything. Try speaking a little closer to the phone."

        /**
         * Said when capture produced literal silence. On GrapheneOS the usual
         * cause is not the Microphone permission — which the user has almost
         * certainly already granted, and been told to check again by every
         * previous version of this message — but the separate per-app Sensors
         * toggle, which is off by default and yields empty buffers rather than
         * an error.
         */
        private const val DEAD_MIC =
            "The microphone produced no sound at all. On GrapheneOS check " +
                "Settings → Apps → Jarvis → Sensors, which is separate from the " +
                "Microphone permission, then make sure nothing else is holding the mic."

        /** Audio arrived, and it never reached the start threshold. */
        private const val TOO_QUIET =
            "I heard sound, but too faintly to be sure it was speech (peak %.3f, " +
                "needs %.2f). Move closer, or check that the right microphone is selected."

        /**
         * Said when the socket never got as far as listening. Names the server,
         * because the usual cause is the app pointed at jarvis-web's console
         * (8199) instead of jarvis-core's API (8080).
         */
        private const val NO_PIPELINE =
            "%s never started listening. Check that this is jarvis-core's address " +
                "(usually port 8080, not the web console on 8199) and that the token is valid."
    }
}
