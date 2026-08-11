package ai.jarvis.app.assist

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
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
    /**
     * True when this conversation was opened by a wake word.
     *
     * The user is mid-sentence when that happens — "Hey Jarvis, turn the
     * kitchen lights off" is one breath — so the first capture buffer is
     * speech rather than the room, and [VoiceActivity] must not measure the
     * room from it. See [VoiceActivity.seeded] for what goes wrong otherwise;
     * the short version is that the turn hears nothing at all.
     *
     * False for a turn the user started by tapping: no speech has happened
     * yet, the first buffer really is the room, and the ratios apply at once.
     */
    private val speechAlreadyUnderway: Boolean = false,
    /**
     * True when this conversation is the screen's resting state rather than
     * something the user started and will finish.
     *
     * [MainActivity][ai.jarvis.app.MainActivity] holds one of these open for as
     * long as it is in the foreground, so silence is the NORMAL condition here
     * — a room with nobody talking in it — where for a tapped or wake-word
     * conversation silence means the turn failed. That single difference is
     * what this flag buys, and it changes exactly one behaviour: what
     * [inactivity] does when it finds nothing was said.
     *
     * It does NOT mean "never time out". The dead-microphone diagnosis is worth
     * more here than anywhere else — a phone that is supposed to be listening
     * all the time and is in fact deaf looks precisely like a quiet room — so
     * the timer still runs. It reports and re-arms instead of tearing the
     * session down.
     */
    private val continuous: Boolean = false,
) : AssistPipelineClient.Callbacks {

    interface Ui {
        fun onMode(mode: JarvisOrbView.Mode, label: String)
        fun onAmplitude(level: Float)
        fun onTranscript(text: String)
        fun onResponse(text: String)
        fun onError(message: String)
        /** Conversation ended (inactivity/stop): no mic running anymore. */
        fun onIdle()

        /**
         * What the turn is touching, as it touches it.
         *
         * Called with the same [ToolRun] instance every time — it is a live
         * model, not a snapshot — so a surface that draws it should read it
         * immediately rather than keeping it for later. Defaulted: a surface
         * with nowhere to put this is a valid surface.
         */
        fun onTools(run: ToolRun) {}
    }

    private val main = Handler(Looper.getMainLooper())
    private var client: AssistPipelineClient? = null
    private var mic: MicStreamer? = null
    private var tts: TtsPlayer? = null

    /** Non-null only while this phone is transcribing a turn itself. */
    private var localStt: LocalTranscriber? = null

    /** What the current turn has called. Cleared when a new turn begins. */
    private val tools = ToolRun()

    /** Wipes the tool rows a while after the last one finished. See [ToolRun.holdMs]. */
    private val clearTools = Runnable {
        tools.clear()
        ui.onTools(tools)
    }

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

    /**
     * When speech starts and stops, measured against THIS room rather than
     * against a number chosen in another one. See [VoiceActivity].
     */
    private val vad = VoiceActivity(speechAlreadyUnderway = speechAlreadyUnderway)
    private var sawSpeech = false
    private var turnActive = false
    /** Whether [DEAD_MIC] has already been said this conversation. See [inactivity]. */
    private var reportedDeafness = false
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
    // The type is stated because the body re-posts THIS property when the
    // conversation is continuous, and a property whose initializer mentions the
    // property being declared has no type to infer from. Kotlin says so in
    // exactly those words — "type checking has run into a recursive problem" —
    // and it is a compile error, not a warning.
    private val inactivity: Runnable = Runnable {
        if (!isListening()) return@Runnable
        if (continuous) {
            // The screen is meant to be listening, so "nobody said anything"
            // is not a fault and must not close anything or say a word. The one
            // thing worth interrupting for is a microphone that is not merely
            // quiet but DEAD — handing back digital silence — because that is
            // indistinguishable from a quiet room from the outside and would
            // otherwise leave the phone looking attentive and stone deaf for as
            // long as the user cared to stare at it. Reported once per session
            // rather than every cycle: it is a standing condition, not news.
            if (vad.peak <= VoiceActivity.DEAD_MIC_LEVEL && !reportedDeafness) {
                reportedDeafness = true
                ui.onError(DEAD_MIC)
            }
            main.postDelayed(inactivity, inactivityMs)
            return@Runnable
        }
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
     * [VoiceActivity.peak] is the loudest smoothed RMS the mic produced since
     * the conversation started. It is the discriminator: capture runs
     * independently of the socket (see [start]), so a peak of a flat zero means
     * the recorder handed back digital silence, and any real peak means audio
     * arrived and it was the edge or the room that fell short.
     */
    private fun silenceDiagnosis(): String = when {
        vad.peak <= VoiceActivity.DEAD_MIC_LEVEL -> DEAD_MIC
        // Against the edge this room actually had, not a constant. "Needs
        // 0.0200" was a lie in a quiet room and an understatement in a loud one.
        vad.peak < vad.startEdge -> TOO_QUIET.format(vad.peak, vad.startEdge)
        else -> NOTHING_HEARD
    }

    /**
     * Backstop for a turn whose end-of-speech never arrives.
     *
     * [inactivity] is disarmed the moment speech is detected, so without this a
     * room sitting in [VoiceActivity]'s dead band would hold the turn open: the
     * level matches neither edge, `endAudio()` is never sent, and nothing is
     * left to time out. Ending the audio is the right response rather than
     * tearing the conversation down — we have the user's speech, so let the
     * server transcribe it.
     *
     * It is a BACKSTOP. While the edges were absolute it was the way nearly
     * every turn ended, at thirty seconds, which is the worst possible length
     * to hand a Whisper backend — its window filled with room noise.
     */
    private val turnCap = Runnable { if (isListening() && sawSpeech) endTurnAudio() }

    val isRunning: Boolean get() = running

    fun start() {
        if (running) return
        running = true
        responseBuffer = StringBuilder()

        if (startLocalTurn()) return

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
     * Transcribe on this phone, and send the server the sentence.
     *
     * The default path streams the microphone to jarvis-core for every turn —
     * the whole utterance, as PCM. When the phone can do it itself, it should:
     * the audio never leaves, only the words do. That is the same trade the
     * on-device wake word makes, one stage further along, and together they
     * mean a voice assistant that sends recordings of a house nowhere.
     *
     * @return true when the local path took the turn. False is normal — no
     *   on-device recogniser (a degoogled build with nothing providing one),
     *   or the user has not asked for it — and the caller carries on with the
     *   streaming path exactly as before.
     */
    private fun startLocalTurn(): Boolean {
        if (!config.sttOnDevice) return false
        if (!LocalTranscriber.isAvailable(context)) return false

        ui.onMode(JarvisOrbView.Mode.LISTENING, "LISTENING")
        val transcriber = LocalTranscriber(context)
        localStt = transcriber
        // There is no MicStreamer in this path — the platform recogniser owns
        // the microphone — so without this the orb never moves while somebody
        // is talking to it. It looked exactly like a surface that had stopped
        // listening, and a surface that looks like it stopped listening is one
        // people repeat themselves at, over the top of the recogniser.
        val progress = object : LocalTranscriber.Listener {
            override fun onLevel(level: Float) {
                if (running) ui.onAmplitude(level)
            }

            override fun onPartial(text: String) {
                if (running) ui.onTranscript(text)
            }

            override fun onSpeechEnd() {
                if (running) ui.onMode(JarvisOrbView.Mode.THINKING, "PROCESSING")
            }
        }
        transcriber.listen(config.sttLanguage, progress) { text, error ->
            localStt = null
            if (!running) return@listen
            ui.onAmplitude(0f)
            if (text == null) {
                // Named rather than generic, and NOT silently retried on the
                // server: falling back would send the audio after promising it
                // would not, which is the one failure this feature must never
                // have.
                ui.onError(error ?: "nothing was recognised on this phone")
                main.postDelayed({ if (running) stopWith(idle = true) }, ERROR_LINGER_MS)
                return@listen
            }
            ui.onTranscript(text)
            speakToServer(text)
        }
        return true
    }

    /** Hand the transcript to the assistant and play back what it says. */
    private fun speakToServer(text: String) {
        reachedListening = true  // there was never a listening stage to reach
        ui.onMode(JarvisOrbView.Mode.THINKING, "PROCESSING")
        tts = TtsPlayer(context, config.token, config.serverUrl)
        client = AssistPipelineClient(
            config.serverUrl,
            config.token,
            this,
            AssistPipelineClient.StartStage.INTENT,
            inputText = text,
            serverKind = config.serverKind,
            onKindResolved = { config.serverKind = it },
        ).also { it.connect(config.pipeline) }
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

    /**
     * Cancel a transcription in flight.
     *
     * Called from [stopWith]. A recogniser left running holds the microphone,
     * and the wake listener is about to ask for it back — two owners of one
     * AudioRecord is the coin toss this whole area exists to avoid.
     */
    private fun stopLocalStt() {
        localStt?.stop()
        localStt = null
    }

    private fun stopWith(idle: Boolean) {
        if (!running && !idle) return
        running = false
        vad.reset()
        reachedListening = false
        reportedDeafness = false
        main.removeCallbacks(inactivity)
        main.removeCallbacks(handshake)
        main.removeCallbacks(turnCap)
        main.removeCallbacks(clearTools)
        tools.clear()
        stopLocalStt()
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
        // The room did not change between turns, so the floor is kept.
        vad.newTurn()
        turnActive = true
        main.removeCallbacks(turnCap)
        main.removeCallbacks(clearTools)
        tools.clear()
        main.post {
            ui.onTranscript("")
            ui.onResponse("")
            ui.onTools(tools)
        }
        client?.startTurn()
    }

    private fun onMicLevel(level: Float) {
        ui.onAmplitude(level)
        val now = SystemClock.elapsedRealtime()

        // Barge-in: talking over the reply cancels it and starts a new turn.
        // Measured against the room like everything else — the old fixed 0.06
        // was six times what this file's own comment says speech reaches, so
        // interrupting Jarvis was not possible at all.
        if (state == AssistPipelineClient.State.SPEAKING &&
            level > maxOf(BARGE_MIN, vad.floor * BARGE_RATIO)
        ) {
            tts?.stop()
            beginNextTurn()
            return
        }
        if (!isListening()) return

        when (vad.onLevel(now, level)) {
            VoiceActivity.Verdict.STARTED -> {
                sawSpeech = true
                // The turn is real, so the "did the microphone produce
                // anything" timer is no longer the question. The cap still
                // runs, as a backstop rather than as the way turns normally
                // end.
                main.removeCallbacks(inactivity)
                main.removeCallbacks(turnCap)
                main.postDelayed(turnCap, MAX_TURN_MS)
            }
            VoiceActivity.Verdict.ENDED -> endTurnAudio()
            VoiceActivity.Verdict.SPEAKING, VoiceActivity.Verdict.QUIET -> Unit
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
                vad.newTurn()
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

    override fun onToolStarted(
        name: String,
        round: Int,
        index: Int,
        total: Int,
        arguments: List<Pair<String, String>>,
    ) {
        main.removeCallbacks(clearTools)
        tools.started(name, round, index, total, ToolRun.summarise(arguments))
        ui.onTools(tools)
    }

    override fun onToolFinished(
        name: String,
        round: Int,
        index: Int,
        total: Int,
        ok: Boolean,
        error: String?,
        durationMs: Int,
    ) {
        main.removeCallbacks(clearTools)
        tools.finished(name, round, index, total, ok, error, durationMs)
        ui.onTools(tools)
        // Leave the last round up for a moment once nothing is running, so what
        // just happened can be read. A failure gets longer — see ToolRun.holdMs.
        if (!tools.running) main.postDelayed(clearTools, tools.holdMs())
    }

    companion object {
        /**
         * Barge-in over the reply, relative to the room like every other edge.
         *
         * This was a fixed 0.06, which this same file's own comment described
         * as six times what conversational speech reaches through an
         * unprocessed phone mic — so interrupting Jarvis by talking over it was
         * not merely hard, it was unreachable.
         *
         * It still has to sit well above the ordinary start edge, because what
         * it must not answer is the phone's own speaker bleeding into the
         * microphone while the reply plays. Eight times the room, with a floor
         * of 0.02, is high enough for that and low enough for a raised voice at
         * arm's length.
         */
        private const val BARGE_RATIO = 8f
        private const val BARGE_MIN = 0.02f




        /**
         * Hard cap on one turn's audio. Only a backstop — see [turnCap].
         *
         * Twelve seconds, down from thirty. No spoken command runs longer, and
         * thirty was pathological for the recogniser: Whisper's window is
         * exactly thirty seconds, so a capped turn filled it edge to edge with
         * room noise and came back empty or invented. Twelve leaves margin
         * inside the window even when the cap is what ends the turn.
         */
        private const val MAX_TURN_MS = 12_000L

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
            // %.4f on both: the start edge is 0.002 now, and %.2f printed it as
            // "0.00" — a diagnostic that told the user the threshold was zero.
            "I heard sound, but too faintly to be sure it was speech (peak %.4f, " +
                "needs %.4f). Move closer, or check that the right microphone is selected."

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
