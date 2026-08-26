package ai.jarvis.app

import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.companion.CompanionMessageHandler
import ai.jarvis.app.companion.ConversationAskHost
import ai.jarvis.app.assist.ActivityRows
import ai.jarvis.app.assist.KnowledgeGraph
import ai.jarvis.app.assist.ToolActivityView
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.ApprovalBridge
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.ActivityStrip
import ai.jarvis.app.ui.KnowledgeGraphView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.PermissionBridge
import ai.jarvis.app.ui.ReadabilityScrim
import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
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
import ai.jarvis.app.ui.theme.JarvisTokens

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
    private lateinit var activityStrip: ActivityStrip
    private lateinit var knowledgeGraphView: KnowledgeGraphView
    private lateinit var config: JarvisConfig
    private var convo: JarvisConversation? = null

    /**
     * Lets Jarvis ask a question on THIS card instead of starting another
     * activity over the top of it. See [ConversationAskHost].
     */
    private var askHost: ConversationAskHost? = null

    private val main = Handler(Looper.getMainLooper())

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
            // NOT requestPermissions() from here, even though this activity is
            // no longer android:noHistory and could now survive the dialog.
            //
            // A first run with no microphone grant is not a conversation that
            // was interrupted — it is a conversation that has not started, and
            // there is nothing on this card worth returning to. The home screen
            // is where the rest of first-run setup lives, it can hold the round
            // trip, and it can say what else is missing. Same shape as the
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
        val width = minOf(screen - JarvisUi.dp(this, JarvisUi.Size.SHEET), JarvisUi.dp(this, JarvisUi.Size.PANEL_MAX))
        window.setLayout(width, ViewGroup.LayoutParams.WRAP_CONTENT)
        window.attributes = window.attributes.also {
            it.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            it.y = JarvisUi.dp(this, JarvisUi.Size.DROP)
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
        val pad = JarvisUi.dp(this, JarvisUi.Space.SCREEN)
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
            setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            letterSpacing = 0.2f
            typeface = Typeface.MONOSPACE
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, JarvisUi.Space.ROW), 0, 0)
            // "A real view, so it is in the accessibility tree" — it was in the
            // tree and nothing ever read it aloud, because being in the tree
            // only earns a description on focus and nothing focuses a caption.
            // This is the one line that says whether Jarvis is listening,
            // thinking or speaking, and it changes several times a turn.
            JarvisUi.liveRegion(this)
        }
        root.addView(captionView, fullWidth())

        // What the turn is DOING, above what it is saying. Hidden until there
        // is something to show, so an ordinary question looks exactly as it did.
        toolActivityView = ToolActivityView(this).apply {
            setPadding(0, JarvisUi.dp(this@JarvisAssistActivity, 10), 0, 0)
        }
        root.addView(toolActivityView, fullWidth())
        // And what the HOUSE did around the turn (M61): the same rows the console draws.
        activityStrip = ActivityStrip(this)
        root.addView(activityStrip, fullWidth())
        knowledgeGraphView = KnowledgeGraphView(this)
        root.addView(
            knowledgeGraphView,
            android.widget.LinearLayout.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                JarvisUi.dp(this@JarvisAssistActivity, 160)
            )
        )

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
                JarvisUi.dp(this, JarvisUi.Space.SNUG).toFloat(),
                0f,
                JarvisUi.dp(this, JarvisUi.Space.HAIRLINE).toFloat(),
                JarvisTokens.Color.SCRIM_HEAVY,
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
                    askHost = ConversationAskHost(
                        context = this@JarvisAssistActivity,
                        config = config,
                        conversation = { convo },
                        surface = askSurface,
                    ).also { CompanionMessageHandler.speechHost = it }
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
    override fun onActivity(rows: ActivityRows) = activityStrip.render(rows)
    override fun onKnowledge(nodes: List<KnowledgeGraph.Node>, edges: List<KnowledgeGraph.Edge>) = knowledgeGraphView.render(nodes, edges)
    override fun onKnowledgePulse(ids: List<String>) = knowledgeGraphView.pulse(ids)
    override fun onWork() = orbView.work()
    override fun onLooking(looking: Boolean) {
        orbView.looking = looking
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

    /** This card's view of a question, for [ConversationAskHost]. */
    private val askSurface = object : ConversationAskHost.Surface {
        override val isShowing: Boolean get() = !isFinishing && !isDestroyed

        override fun onMode(mode: JarvisOrbView.Mode, label: String) {
            orbView.setMode(mode)
            captionView.text = label
        }

        override fun onAmplitude(level: Float) = orbView.setAmplitude(level)

        override fun onQuestion(text: String) {
            responseView.text = text
            transcriptView.text = ""
        }

        override fun onAnswerTranscript(text: String) {
            transcriptView.text = text
        }

        override fun onResting() {
            orbView.setMode(JarvisOrbView.Mode.IDLE)
            captionView.text = "LISTENING"
        }
    }

    override fun onStart() {
        super.onStart()
        // Back on screen — either from the very first frame, or because a
        // prompt of ours has just been answered and handed the foreground back.
        main.removeCallbacks(giveUp)
        // Carry on from where the prompt interrupted — the same call the ask
        // host makes when a question is answered, declined or times out, and
        // for the same reason: the conversation is owed its microphone back in
        // all three cases. A no-op on the first frame, where nothing was held.
        if (held) {
            held = false
            convo?.resumeAfterQuestion()
        }
    }

    /**
     * What `android:noHistory` used to do, done deliberately.
     *
     * `noHistory` finished this activity whenever it stopped being visible and
     * could not tell why. Our own prompts — the consent screen, a question, the
     * permission trampoline — appear over it in tasks of their own, so every
     * one of them destroyed the conversation on the way up and answering it
     * returned to nothing. That is the reported *"it closes the Hey Jarvis
     * popup instead of persisting the conversation"*, and a turn needing one
     * approval could not be completed at all.
     *
     * So: still finish when the user has genuinely moved on — pressed home,
     * opened something else, turned the screen off — and stay for a prompt this
     * app itself raised.
     */
    override fun onStop() {
        super.onStop()
        if (isFinishing) return
        if (!ourOwnPromptIsUp()) {
            finish()
            return
        }
        // A prompt that is never answered must not leave this alive for ever.
        // Its own timeout plus slack: by then the bridge has settled the
        // request itself and there is nothing to come back to.
        Log.i(TAG, "staying alive behind a prompt of ours")
        main.postDelayed(giveUp, ApprovalBridge.TIMEOUT_MS + GIVE_UP_SLACK_MS)

        // PUT THE CONVERSATION DOWN while the prompt is up.
        //
        // `holdForQuestion` exists for exactly this and says why: "Give the
        // microphone up completely rather than muting it. Two owners of one
        // AudioRecord is the coin toss this whole area exists to avoid." It was
        // written for a question taking the mic, and a consent prompt covering
        // the screen is the same situation — the user is reading, not talking,
        // and `running` deliberately stays true so no inactivity timer pulls
        // the surface out from under them.
        //
        // This is not tidiness. Under `noHistory` the activity was destroyed
        // here and the microphone went back with it; now it can sit stopped for
        // over a minute, and a live AudioRecord behind a full-screen consent
        // prompt would be recording a room whose owner believes the
        // conversation is paused — while the inactivity timer and the VAD ran
        // against audio nobody meant to send.
        held = convo?.holdForQuestion() ?: false
    }

    /** True while [onStop] has parked the conversation behind a prompt. */
    private var held = false

    /**
     * True while something this app put on screen is waiting for the user.
     *
     * Deliberately asked of the bridges rather than tracked here: the prompt is
     * raised by the service that received the command, not by this activity, so
     * a flag set locally would be set by the wrong object at the wrong time.
     */
    private fun ourOwnPromptIsUp(): Boolean =
        ApprovalBridge.anyPending ||
            PermissionBridge.anyPending ||
            CompanionMessageHandler.ledger.inFlightCount > 0

    private val giveUp = Runnable {
        if (!isFinishing) {
            Log.i(TAG, "nothing came back from the prompt; closing the conversation")
            finish()
        }
    }

    override fun onDestroy() {
        main.removeCallbacks(giveUp)
        askHost?.let { CompanionMessageHandler.clearSpeechHost(it); it.stop() }
        askHost = null
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

        /**
         * Set when a headset button opened it. Carried for the audit trail and
         * for the transcript's own "how did this start" — the popup behaves the
         * same either way, because the button is a tap by another name.
         */
        const val EXTRA_FROM_HEADSET_BUTTON = "ai.jarvis.app.FROM_HEADSET_BUTTON"

        /** Side of the orb's slot in the card. The reactor sizes itself to it. */
        private const val ORB_DP = 200

        /**
         * How long past a prompt's own deadline to wait before closing anyway.
         *
         * The bridge has settled the request by then, so there is nothing left
         * to be returned to — this only stops a crashed or swiped-away prompt
         * from leaving an invisible conversation alive.
         */
        private const val GIVE_UP_SLACK_MS = 15_000L

        fun newIntent(context: Context): Intent =
            Intent(context, JarvisAssistActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
