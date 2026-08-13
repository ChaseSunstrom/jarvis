package ai.jarvis.app.companion

import ai.jarvis.app.assist.ConversationRegistry
import ai.jarvis.app.assist.TtsPlayer
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.ReadabilityScrim
import android.Manifest
import android.app.Activity
import android.app.KeyguardManager
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.CountDownTimer
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * The question screen: the orb, what Jarvis asked, and a way to answer it.
 *
 * Two shapes, decided by the message:
 *
 *  * `options` present — one button per option. Tapping one sends that exact
 *    string back.
 *  * no options — a mic button that captures a spoken answer through
 *    [CompanionVoiceClient] (`stt`-only, so the answer is *transcribed* and
 *    never dispatched as a command), with a text field beside it for when the
 *    mic is unavailable or the room is not the place to talk.
 *
 * It is deliberately a close relative of [ai.jarvis.app.ApprovalActivity], and
 * the differences are the point:
 *
 *  * **Full-screen and `showWhenLocked`,** so the phone lights up and the
 *    question is not missed. But a locked phone is a phone in someone else's
 *    hand, so [CompanionAskGate] hides the text of a `high`/`critical` message
 *    behind "Jarvis has a question" until the keyguard is gone, and answering
 *    needs an unlocked phone in every case. FLAG_SECURE keeps the question out
 *    of screenshots and off the screen recorder, including this app's own
 *    accessibility path.
 *  * **DISMISS is live everywhere,** including over the keyguard. It reports
 *    `dismissed`, which makes the server escalate to whichever device the user
 *    is actually at — so refusing here is helpful rather than destructive, and
 *    it is safe from a stranger and from a stray tap.
 *  * **Doing nothing times out.** The countdown reflects the server's
 *    `timeout_s`; Back, a swipe and being destroyed all report `dismissed`.
 *    Every exit reports something, because a question nobody answers on this
 *    device has to move on rather than sit there.
 *
 * The same activity renders a `speak` message that arrived while the app was
 * closed: opening the notification lands here, the orb speaks it, and there is
 * nothing to answer — the handler already reported that one at post time.
 */
class CompanionAskActivity : Activity() {

    private var messageId: String? = null

    /**
     * The conversation this question belongs to, from `EXTRA_CONVERSATION_ID`.
     *
     * Kept as a field rather than used once in `onCreate` because [answer] needs
     * it: an answer given here is the user speaking in that thread, so the
     * thread has to still be current when the next thing is said on this phone.
     * See [ai.jarvis.app.assist.ConversationRegistry].
     *
     * It is deliberately NOT put on the `jarvis_message_result` frame. The
     * server matches an answer by `message_id` and holds the
     * message_id → conversation_id mapping itself — `CompanionManager
     * .on_device_answer` fires `EVENT_MESSAGE_ANSWERED` with the conversation id
     * off its own pending message — so a copy from this end would be a field
     * nothing reads, which is the exact shape of bug this repo keeps finding.
     */
    private var conversationId: String? = null

    private var mode: String = CompanionProtocol.MODE_ASK
    private var importance: String = "normal"
    private var questionText: String = ""
    private var options: List<String> = emptyList()
    private var timeoutMs: Long = CompanionProtocol.DEFAULT_ASK_TIMEOUT_MS

    private var answered = false
    private var locked = false
    private var armed = false

    /** So unlocking the phone does not re-read the question from the top. */
    private var spokeQuestion = false
    private var dismissRequested = false
    private var listening = false

    private var countdown: CountDownTimer? = null
    private var voice: CompanionVoiceClient? = null
    private var speechPlayer: TtsPlayer? = null

    private lateinit var config: JarvisConfig
    private lateinit var orb: JarvisOrbView
    private lateinit var questionView: TextView
    private lateinit var countdownView: TextView
    private lateinit var noteView: TextView
    private lateinit var dismissButton: Button
    private val answerControls = mutableListOf<View>()
    private var micButton: Button? = null
    private var answerField: EditText? = null

    private val armRunnable = Runnable {
        armed = true
        refreshGate()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SECURE
        )
        setFinishOnTouchOutside(false)
        JarvisUi.immersive(this)
        config = JarvisConfig(this)

        val id = intent?.getStringExtra(CompanionMessageHandler.EXTRA_MESSAGE_ID)
        if (id.isNullOrEmpty()) {
            // Nothing to answer. Nothing happens.
            finish()
            return
        }
        messageId = id
        mode = intent?.getStringExtra(CompanionMessageHandler.EXTRA_MODE)
            ?.takeIf { it in CompanionProtocol.VALID_MODES }
            ?: CompanionProtocol.MODE_ASK
        questionText = intent?.getStringExtra(CompanionMessageHandler.EXTRA_TEXT).orEmpty()
        importance = intent?.getStringExtra(CompanionMessageHandler.EXTRA_IMPORTANCE)
            ?.takeIf { it in CompanionProtocol.VALID_IMPORTANCE }
            ?: "normal"
        options = intent?.getStringArrayExtra(CompanionMessageHandler.EXTRA_OPTIONS)
            ?.filter { it.isNotBlank() }
            ?.take(CompanionProtocol.MAX_OPTIONS)
            ?: emptyList()
        timeoutMs = CompanionProtocol.clampTimeout(
            (intent?.getLongExtra(
                CompanionMessageHandler.EXTRA_TIMEOUT_MS,
                CompanionProtocol.DEFAULT_ASK_TIMEOUT_MS
            ) ?: CompanionProtocol.DEFAULT_ASK_TIMEOUT_MS) / 1000.0,
            CompanionProtocol.DEFAULT_ASK_TIMEOUT_MS
        )
        // THE EXTRA NOTHING READ.
        //
        // `CompanionMessageHandler.askIntent` has put `EXTRA_CONVERSATION_ID` on
        // this intent since the extra existed, and this activity read
        // MESSAGE_ID, MODE, TEXT, IMPORTANCE, OPTIONS and TIMEOUT_MS — six of
        // the seven. So the phone was handed the conversation the question came
        // from and threw it away, which is the Android half of
        // `docs/cross-device.md`'s *"answer on your phone and the reply lands
        // back in the same conversation the desktop started"* not being true.
        //
        // Read here as well as in the handler, because this activity has a
        // second entrance: a notification the user taps minutes later, after the
        // process has been killed and restarted, at which point the handler's
        // adoption is gone with the process that made it.
        conversationId = intent?.getStringExtra(CompanionMessageHandler.EXTRA_CONVERSATION_ID)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
        ConversationRegistry.remember(this, conversationId)

        // A question is only answerable while it is genuinely in flight, and
        // there are two different ways for that not to be true:
        //
        //  * ALREADY SETTLED — the countdown ran out, the watchdog reported
        //    `timeout`, or another copy of this screen answered it. The ledger
        //    refuses a second answer, so rendering the buttons here would let
        //    the user choose, watch the screen close, and have nothing happen.
        //    Close instead of lying about it.
        //  * NEVER ADMITTED — the process was killed and restarted, or the
        //    socket dropped everything in flight. Nobody is waiting for this
        //    answer, so say `undeliverable` and let the server escalate now
        //    rather than burn the whole timeout first.
        if (mode == CompanionProtocol.MODE_ASK) {
            val settledAs = CompanionMessageHandler.ledger.statusOf(id)
            if (settledAs != null) {
                Log.i(TAG, "$id was already reported as $settledAs; nothing left to answer")
                finish()
                return
            }
            if (!CompanionMessageHandler.ledger.isInFlight(id)) {
                CompanionMessageHandler.reportUndeliverable(this, id)
                finish()
                return
            }
        }

        setContentView(buildUi())
        refreshGate()
        when (mode) {
            CompanionProtocol.MODE_ASK -> {
                startCountdown()
                // AND SAY IT OUT LOUD. Only `speak` messages were ever spoken,
                // so Jarvis asking a question produced a silent card the user
                // had to notice and read — on a phone that may be in a pocket,
                // in a car, or across the room, which is where a voice
                // assistant asking a question is most likely to be useful.
                // Reported as *"it should be able to ask me questions over
                // voice"*.
                askAloud()
            }
            // A message the server wanted spoken, opened from the notification
            // it fell back to. Say it now.
            CompanionProtocol.MODE_SPEAK -> speakIt()
            // A quiet `notify` opened from its notification is exactly that:
            // quiet. Reading it aloud would be the opposite of what the server
            // asked for.
            else -> Unit
        }
    }

    override fun onResume() {
        super.onResume()
        // The user may have unlocked by any route — this activity's dismiss
        // request, the power button, a fingerprint. Re-read, do not assume.
        refreshGate()
    }

    // --- the keyguard half of the gate --------------------------------------

    private fun isLocked(): Boolean =
        getSystemService(KeyguardManager::class.java)?.isKeyguardLocked == true

    private fun refreshGate() {
        if (!::questionView.isInitialized || answered) return
        val wasLocked = locked
        locked = isLocked()

        if (locked) {
            armed = false
            questionView.removeCallbacks(armRunnable)
            requestUnlock()
        } else if (wasLocked || !armed) {
            questionView.removeCallbacks(armRunnable)
            questionView.postDelayed(armRunnable, CompanionAskGate.ARM_MS)
        }

        questionView.text = CompanionAskGate.textFor(locked, importance, questionText)

        val canAnswer = CompanionAskGate.answerEnabled(locked, armed, answered, importance)
        for (control in answerControls) {
            control.isEnabled = canAnswer
            control.alpha = if (canAnswer) 1f else 0.4f
        }
        dismissButton.isEnabled = CompanionAskGate.dismissEnabled(answered)

        val note = CompanionAskGate.blockedReason(locked, armed, importance)
        noteView.text = note.orEmpty()
        noteView.visibility = if (note == null) View.GONE else View.VISIBLE

        // A question this screen declined to read out because the phone was
        // locked gets its voice back the moment it is not. `spokeQuestion`
        // keeps that to once.
        if (mode == CompanionProtocol.MODE_ASK && !locked) askAloud()
    }

    /** Ask the system to take the user through the keyguard. Once per screen. */
    private fun requestUnlock() {
        if (dismissRequested) return
        val km = getSystemService(KeyguardManager::class.java) ?: return
        dismissRequested = true
        try {
            km.requestDismissKeyguard(
                this,
                object : KeyguardManager.KeyguardDismissCallback() {
                    override fun onDismissSucceeded() {
                        dismissRequested = false
                        runOnUiThread { refreshGate() }
                    }

                    override fun onDismissError() {
                        dismissRequested = false
                    }

                    override fun onDismissCancelled() {
                        // The user chose not to unlock. The question stays
                        // hidden and the countdown keeps running.
                        dismissRequested = false
                    }
                }
            )
        } catch (t: Throwable) {
            // Never let a keyguard quirk take the screen down with it.
            dismissRequested = false
        }
    }

    // --- UI -----------------------------------------------------------------

    private fun buildUi(): ViewGroup {
        val ctx = this
        val root = FrameLayout(ctx).apply { setBackgroundColor(JarvisUi.BG) }

        orb = JarvisOrbView(ctx).apply {
            chromeEnabled = false
            setMode(
                if (mode == CompanionProtocol.MODE_ASK) JarvisOrbView.Mode.THINKING
                else JarvisOrbView.Mode.SPEAKING
            )
            startEntrance()
        }
        root.addView(
            orb,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )

        val column = JarvisUi.column(ctx, padDp = 24).apply {
            // The third orb surface, and the one that was left out when the
            // other two got a ground. The orb is drawn FULL-BLEED behind this
            // column, so every line of the question sits directly on top of its
            // plates — brightest exactly where the text is largest. An opaque
            // window background does not help with that: the competing thing is
            // in front of it, not behind.
            //
            // Same gradient as AssistOverlay and JarvisAssistActivity, so the
            // three surfaces a user meets interchangeably read as one thing.
            // Not a card: this surface has never had one and the other two had
            // theirs removed twice, because a frame becomes the first thing you
            // see. See ReadabilityScrim.
            background = ReadabilityScrim()
        }

        column.addView(
            TextView(ctx).apply {
                text = if (mode == CompanionProtocol.MODE_ASK) "JARVIS ASKS" else "JARVIS"
                setTextColor(JarvisUi.ACCENT)
                textSize = 12f
                letterSpacing = 0.24f
                typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
                gravity = Gravity.CENTER
            }
        )

        questionView = TextView(ctx).apply {
            text = CompanionAskGate.HIDDEN_TEXT
            setTextColor(Color.WHITE)
            textSize = 21f
            gravity = Gravity.CENTER
            setLineSpacing(JarvisUi.dp(ctx, 4).toFloat(), 1f)
            setPadding(0, JarvisUi.dp(ctx, 18), 0, JarvisUi.dp(ctx, 10))
            // Remote text: rendered as text and nothing else.
            setTextIsSelectable(false)
        }
        column.addView(questionView)

        countdownView = TextView(ctx).apply {
            setTextColor(JarvisUi.FAINT)
            textSize = 12f
            typeface = Typeface.MONOSPACE
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, JarvisUi.dp(ctx, 12))
        }
        column.addView(countdownView)

        if (mode == CompanionProtocol.MODE_ASK) {
            if (options.isNotEmpty()) column.addView(buildOptions())
            else column.addView(buildFreeAnswer())
        }

        dismissButton = JarvisUi.ghost(
            ctx,
            if (mode == CompanionProtocol.MODE_ASK) "NOT NOW" else "CLOSE"
        ) { answer(CompanionProtocol.STATUS_DISMISSED) }
        val dismissRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(ctx, 14), 0, 0)
            addView(dismissButton)
        }
        column.addView(dismissRow)

        noteView = JarvisUi.hint(ctx, "").apply { gravity = Gravity.CENTER }
        column.addView(noteView)

        if (mode == CompanionProtocol.MODE_ASK) {
            column.addView(
                JarvisUi.hint(
                    ctx,
                    "\"Not now\" sends this to whichever device you are at instead. " +
                        "An answer is just an answer — nothing runs because of it."
                ).apply { gravity = Gravity.CENTER }
            )
        }

        val scroll = ScrollView(ctx).apply {
            isFillViewport = true
            addView(
                column,
                ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }
        root.addView(
            scroll,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
                Gravity.CENTER
            )
        )
        root.addView(
            JarvisUi.CornerBrackets(ctx, JarvisUi.ACCENT),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        return root
    }

    private fun buildOptions(): ViewGroup {
        val ctx = this
        val row = LinearLayout(ctx).apply {
            orientation = if (options.size > 3) LinearLayout.VERTICAL else LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }
        for (option in options) {
            val button = JarvisUi.pill(ctx, option) {
                // Belt and braces: the enabled state is the gate, and this is
                // the same question asked again at the moment of the tap.
                if (CompanionAskGate.answerEnabled(isLocked(), armed, answered, importance)) {
                    answer(CompanionProtocol.STATUS_ANSWERED, option)
                }
            }.apply {
                isEnabled = false
                alpha = 0.4f
                // A question screen is exactly what a tapjacking overlay wants
                // to sit on.
                filterTouchesWhenObscured = true
            }
            answerControls.add(button)
            row.addView(
                button,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply {
                    val m = JarvisUi.dp(ctx, 6)
                    setMargins(m, m, m, m)
                }
            )
        }
        return row
    }

    private fun buildFreeAnswer(): ViewGroup {
        val ctx = this
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }

        // Tap to start, tap again when you have finished — not press-and-hold.
        // The label used to say HOLD, which is a control this screen does not
        // have: holding does nothing and the user gets no transcript.
        val mic = JarvisUi.pill(ctx, MIC_IDLE_LABEL) { toggleListening() }.apply {
            isEnabled = false
            alpha = 0.4f
            filterTouchesWhenObscured = true
        }
        micButton = mic
        answerControls.add(mic)
        column.addView(mic)

        column.addView(JarvisUi.spacer(ctx, 10))

        val field = JarvisUi.field(ctx, "…or type an answer", "").apply {
            isEnabled = false
            alpha = 0.4f
            setOnEditorActionListener { view, _, _ ->
                submitTyped(view.text?.toString().orEmpty())
                true
            }
        }
        answerField = field
        answerControls.add(field)
        column.addView(
            field,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        val send = JarvisUi.ghost(ctx, "SEND") {
            submitTyped(answerField?.text?.toString().orEmpty())
        }.apply {
            isEnabled = false
            alpha = 0.4f
            filterTouchesWhenObscured = true
        }
        answerControls.add(send)
        column.addView(send)
        return column
    }

    private fun submitTyped(raw: String) {
        val text = raw.trim()
        if (text.isEmpty()) return
        if (!CompanionAskGate.answerEnabled(isLocked(), armed, answered, importance)) return
        answer(CompanionProtocol.STATUS_ANSWERED, text)
    }

    // --- the spoken answer --------------------------------------------------

    private fun toggleListening() {
        if (!CompanionAskGate.answerEnabled(isLocked(), armed, answered, importance)) return
        if (listening) {
            // Second tap: the user has finished speaking.
            voice?.endAudio()
            micButton?.text = "TRANSCRIBING…"
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (!config.isConfigured) {
            noteView.text = "Set the server URL and token in Settings to answer by voice."
            noteView.visibility = View.VISIBLE
            return
        }
        listening = true
        micButton?.text = MIC_LISTENING_LABEL
        orb.setMode(JarvisOrbView.Mode.LISTENING)
        val client = CompanionVoiceClient(config.serverUrl, config.token, config.serverKind)
        voice = client
        client.listen(
            onLevel = { level -> orb.setAmplitude(level) },
            onText = { text ->
                listening = false
                orb.setAmplitude(0f)
                orb.setMode(JarvisOrbView.Mode.THINKING)
                if (text.isNullOrBlank()) {
                    micButton?.text = MIC_IDLE_LABEL
                    noteView.text = "I did not catch that. Try again, or type it."
                    noteView.visibility = View.VISIBLE
                } else {
                    // Transcribed, not executed: the run ended at the STT stage,
                    // so this string is the user's answer and nothing else.
                    answer(CompanionProtocol.STATUS_ANSWERED, text)
                }
            }
        )
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
        ) {
            toggleListening()
        } else if (requestCode == REQ_MIC) {
            noteView.text = "No microphone access — type your answer instead."
            noteView.visibility = View.VISIBLE
        }
    }

    // --- asking out loud ----------------------------------------------------

    /**
     * Read the question aloud, then open the microphone for the answer.
     *
     * ## Why the keyguard decides whether this happens
     *
     * The notification for this card is `VISIBILITY_PRIVATE` and
     * [CompanionAskGate] hides the text of a `high`/`critical` question while
     * the phone is locked, both for the same reason: a question can name a
     * person, a place or an amount, and a locked phone is one anybody may be
     * holding. Speaking it aloud is a strictly louder version of printing it on
     * the lock screen, so it obeys the same rule — [CompanionAskGate.textVisible]
     * is the single authority, and if the text may not be shown it may not be
     * said.
     *
     * When the phone is unlocked later, [refreshGate] runs and this is retried
     * once, so the question is not lost — it just waits for its owner.
     */
    private fun askAloud() {
        if (spokeQuestion || answered) return
        if (questionText.isBlank() || !config.isConfigured) return
        if (!CompanionAskGate.textVisible(isLocked(), importance)) return
        spokeQuestion = true

        val client = CompanionVoiceClient(config.serverUrl, config.token, config.serverKind)
        voice = client
        orb.setMode(JarvisOrbView.Mode.SPEAKING)
        client.speak(questionText) { url ->
            if (url == null) {
                // No voice available — the card is still on screen and still
                // answerable. A failed round trip to the TTS server is not a
                // reason to make the question disappear.
                orb.setMode(JarvisOrbView.Mode.IDLE)
                return@speak
            }
            val player = TtsPlayer(this, config.token, config.serverUrl)
            speechPlayer = player
            player.play(url) {
                orb.setMode(JarvisOrbView.Mode.IDLE)
                // Straight into listening, so answering is not a second thing
                // to notice and tap — but ONLY for a question with no options.
                //
                // A question with options is answered by tapping one of them,
                // and the answer has to be one of those exact strings. Opening
                // the microphone over the top of that offers a way to reply
                // that the caller cannot accept, and it changes the screen
                // underneath a user who is already reaching for a button.
                //
                // It also stopped `CompanionAskTest` answering at all, which is
                // the same fact from the other side: the option path is the one
                // that must not be disturbed.
                //
                // Through the same gate as the mic button either way: if the
                // user cannot answer right now, nothing opens the microphone.
                if (options.isEmpty() &&
                    CompanionAskGate.answerEnabled(isLocked(), armed, answered, importance) &&
                    checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                    PackageManager.PERMISSION_GRANTED
                ) {
                    toggleListening()
                }
            }
        }
    }

    // --- speaking a backgrounded `speak` message ----------------------------

    private fun speakIt() {
        if (questionText.isBlank() || !config.isConfigured) return
        val client = CompanionVoiceClient(config.serverUrl, config.token, config.serverKind)
        voice = client
        client.speak(questionText) { url ->
            if (url == null) return@speak
            val player = TtsPlayer(this, config.token, config.serverUrl)
            speechPlayer = player
            player.play(url) { orb.setMode(JarvisOrbView.Mode.LISTENING) }
        }
    }

    // --- countdown ----------------------------------------------------------

    private fun startCountdown() {
        countdown?.cancel()
        countdown = object : CountDownTimer(timeoutMs, 1000L) {
            override fun onTick(millisUntilFinished: Long) {
                countdownView.text =
                    "${CompanionAskGate.secondsLeft(millisUntilFinished)}s"
            }

            override fun onFinish() {
                countdownView.text = "NO ANSWER"
                answer(CompanionProtocol.STATUS_TIMEOUT)
            }
        }.also { it.start() }
    }

    // --- answering ----------------------------------------------------------

    private fun answer(status: String, text: String? = null) {
        if (answered) return
        answered = true
        questionView.removeCallbacks(armRunnable)
        countdown?.cancel()
        countdown = null
        stopVoice()
        if (status == CompanionProtocol.STATUS_ANSWERED) {
            // Answering is the user speaking in this thread, so the thread is
            // live and belongs to this phone now. Without this the clock started
            // when the question ARRIVED, and a question read after lunch would
            // be answered into a conversation that had already expired.
            ConversationRegistry.remember(this, conversationId)
        }
        messageId?.let { CompanionMessageHandler.onAnswer(this, it, status, text) }
        finish()
    }

    private fun stopVoice() {
        listening = false
        voice?.close()
        voice = null
        speechPlayer?.stop()
        speechPlayer = null
    }

    @Deprecated("Back must report something. Predictive back is off in the manifest.")
    override fun onBackPressed() {
        // Deliberately does not call super: answer() finishes the activity, and
        // the one thing Back must never do is leave the question unanswered.
        answer(CompanionAskGate.IMPLICIT_STATUS)
    }

    override fun onDestroy() {
        countdown?.cancel()
        countdown = null
        stopVoice()
        if (::questionView.isInitialized) questionView.removeCallbacks(armRunnable)
        if (!answered && mode == CompanionProtocol.MODE_ASK) {
            // Swiped away, killed, config-changed out of existence — all of it
            // is "not dealt with here", so the server escalates. This is the
            // last line of the never-go-quiet guarantee.
            answered = true
            messageId?.let {
                CompanionMessageHandler.onAnswer(this, it, CompanionAskGate.IMPLICIT_STATUS)
            }
        }
        super.onDestroy()
    }

    private companion object {
        private const val TAG = "JarvisCompanionAsk"
        private const val REQ_MIC = 4711

        private const val MIC_IDLE_LABEL = "TAP TO ANSWER"
        private const val MIC_LISTENING_LABEL = "LISTENING — TAP WHEN DONE"
    }
}
