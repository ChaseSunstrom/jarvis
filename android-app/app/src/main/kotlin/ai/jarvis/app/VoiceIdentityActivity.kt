package ai.jarvis.app

import ai.jarvis.app.assist.MicStreamer
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.VoiceIdentityClient
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.TypedValue
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.io.ByteArrayOutputStream
import kotlin.concurrent.thread

/**
 * Teaching Jarvis your voice, and checking it still knows it.
 *
 * ## Why this screen has phrases on it
 *
 * Not decoration. The profile's denominator is how much the owner varies
 * between utterances, so a set of five calm, identical-sounding phrases teaches
 * it that the owner *never* varies — and then the first question you ask, or
 * the first order you snap, reads as a stranger. The server measured this:
 * narrow enrolment overlaps the nearest impostor, and enrolment that moves
 * pitch and length separates them cleanly.
 *
 * So the phrases come from the server (`prompts` in the status payload) rather
 * than being typed in here, both surfaces read the same list, and each one is
 * chosen to move something — a question, an order, a count, a long sentence.
 *
 * ## Why the numbers are on screen
 *
 * Because the threshold is not knowable in advance and the owner is the one who
 * has to set it. After enrolment this shows what the owner's own worst sample
 * scored and what threshold that implies, and TEST MY VOICE scores a fresh
 * utterance against it. A biometric gate whose threshold was guessed is a gate
 * that locks you out on the first cold morning, and the only defence is being
 * able to see the numbers before you turn it on.
 *
 * Nothing here enables enforcement. That is a line in `configuration.yaml` on
 * the server, deliberately: turning on the thing that can refuse you should not
 * be a switch you can hit by accident on a phone.
 */
class VoiceIdentityActivity : Activity() {

    private lateinit var config: JarvisConfig

    /**
     * Nullable, not `lateinit`.
     *
     * It was `lateinit`, assigned after the unconfigured early return — and
     * that return disabled the record button only, because the other two are
     * built by `JarvisUi.button`, which does not touch `isEnabled`. So on a
     * phone that had never been paired, FORGET MY VOICE was live, read an
     * uninitialised property inside a worker thread, and killed the process.
     *
     * Both halves are fixed: every button is disabled below, and this is
     * nullable so a button added to this screen later cannot bring the crash
     * back — [offMainThread] short-circuits instead.
     */
    private var client: VoiceIdentityClient? = null

    private val main = Handler(Looper.getMainLooper())

    private lateinit var orb: JarvisOrbView
    private lateinit var promptView: TextView
    private lateinit var statusView: TextView
    private lateinit var detailView: TextView
    private lateinit var recordButton: Button
    private lateinit var testButton: Button
    private lateinit var forgetButton: Button
    private lateinit var redoButton: Button

    /** One row per phrase: what it says and whether it has been given. */
    private lateinit var stepList: LinearLayout

    private var status: VoiceIdentityClient.Status? = null

    /**
     * Which phrase to read next.
     *
     * **Derived from the server's sample count, not counted here.** It was a
     * plain field starting at 0 and reset only by FORGET MY VOICE, so rotating
     * the phone, taking a call, or coming back to finish enrolment tomorrow
     * restarted the list from the top — while the server's count kept climbing.
     * The user re-read phrases they had already given, which is the one thing
     * this screen must not ask for: the profile's whole value is that the
     * samples differ from each other.
     *
     * Persisting a local counter would have been the obvious fix and the wrong
     * one. Two devices can enrol into one profile, `FORGET MY VOICE` is not the
     * only way samples go away, and a local number is a second opinion about
     * something the server already knows exactly. `samples` IS the index: with
     * three stored, the next phrase to read is the fourth.
     *
     * [redo] is the one thing that can move it backwards, and only by one.
     */
    private val promptIndex: Int
        get() = ((status?.samples ?: 0) - redo).coerceAtLeast(0)

    /**
     * How far REDO has stepped the phrase list back.
     *
     * Not a sample count and never negative: it exists so "say that one again"
     * re-offers the phrase just read. Cleared whenever a sample is accepted,
     * because the list has moved on.
     */
    private var redo = 0

    /** What the server said about the last sample. Cleared when a new one starts. */
    private var lastNote: String? = null

    private var mic: MicStreamer? = null
    private var capture: ByteArrayOutputStream? = null
    private var busy = false

    /** What the current capture is for. */
    private enum class Mode { ENROL, TEST }

    private var mode = Mode.ENROL

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisUi.immersive(this)
        config = JarvisConfig(this)
        setContentView(buildUi())

        if (!config.isConfigured) {
            statusView.text = "Pair this phone with Jarvis first."
            // All three, via the shared helper. Disabling the record button
            // alone left TEST and FORGET live on a screen that has no server
            // to talk to.
            syncButtons()
            return
        }
        client = VoiceIdentityClient(config.serverUrl, config.token)
        syncButtons()
        refresh()
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }

    // --- UI -----------------------------------------------------------------
    private fun buildUi(): ViewGroup {
        val column = JarvisUi.column(this)
        column.addView(JarvisUi.screenTitle(this, "Your voice", "Teach Jarvis what you sound like, so it answers you and not the television."))
        column.addView(
            JarvisUi.hint(
                this,
                "Jarvis can be told to answer only you. Say each line below in the " +
                    "voice you would actually use — the question as a question, the " +
                    "order as an order. That variation is what the check is built from; " +
                    "five identical calm phrases teach it that you never sound different, " +
                    "and then it stops recognising you when you do."
            )
        )

        // `startEntrance()` IS WHAT MAKES THIS VISIBLE, and it was missing.
        //
        // JarvisOrbView draws everything through a master alpha that is its
        // `entranceProgress`, and starts that at 0. The three methods that move
        // it off 0 — startEntrance, beginBoot, endBoot — are also the only three
        // that start the view's frame clock, and the clock is what integrates
        // the breathing, the ring rotation and the mic amplitude and issues the
        // one `invalidate` per frame. So a JarvisOrbView nobody starts is not a
        // still orb: it is a 160dp hole. It laid out, it reserved its space, it
        // received every `setMode`/`setAmplitude`/`setStateLabel` call this
        // screen makes, and it painted nothing at all, ever.
        //
        // Reported as *"in the enrolment on the phone, there's no animation or
        // ANY indicator if Jarvis is listening"*. Every other host of this view
        // (MainActivity, JarvisAssistActivity, CompanionAskActivity) starts it
        // in the same breath as constructing it; `orb_is_started_test.py` now
        // fails the build for one that does not.
        orb = JarvisOrbView(this).apply {
            // NO CHROME. Reported as *"in the teach Jarvis voice, Jarvis is
            // overlayed by text of Jarvis"* — which is exactly what it looked
            // like: the wordmark is anchored to the resting outer radius and
            // the corner brackets sit 18dp from the VIEW edges, both measured
            // for a screen-sized surface. In a 200dp slot on a scrolling
            // settings page the wordmark lands on top of the orb it is meant
            // to sit above.
            //
            // `JarvisAssistActivity` turns it off for the same reason, in the
            // same words: "both of which are nonsense inside a 200dp box". This
            // screen is the third host of this view and the second to need it,
            // so `orb_is_started_test` now checks for it rather than leaving
            // the fourth to rediscover it.
            //
            // The caption goes with the chrome, so the countdown this screen
            // used to put under the orb now goes in the status line, where
            // there are already words and where it does not depend on a custom
            // view drawing at all.
            chromeEnabled = false
            // The card supplies its own ground, so the full-view vignette would
            // just be a dark rectangle across the middle of a settings screen.
            scrimEnabled = false
            setMode(JarvisOrbView.Mode.IDLE)
            startEntrance()
        }
        column.addView(
            orb,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                JarvisUi.dp(this, JarvisUi.Size.SHEET_MIN)
            )
        )

        column.addView(JarvisUi.spacer(this, JarvisUi.Space.GAP))
        column.addView(JarvisUi.label(this, "Say this"))
        promptView = JarvisUi.responseView(this)
        column.addView(promptView)

        // THE STEP LIST, which this screen did not have.
        //
        // Progress was one line of text — "3 of 20 samples · gate is observe" —
        // so the only way to know which phrases had been given was to remember.
        // With the phrase list restarting from the top on every rotation (see
        // [promptIndex]) that was not a small gap: the screen and the server
        // disagreed about where enrolment had got to, and nothing on screen
        // showed the disagreement.
        //
        // Built once and re-bound in [renderSteps], because the phrases arrive
        // from the server and can change under a running screen.
        stepList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(stepList, matchWidth())

        column.addView(JarvisUi.spacer(this, JarvisUi.Space.GAP))
        // TAP, not hold.
        //
        // It was push-to-talk, on the reasoning that a VAD which clips the last
        // word poisons a profile. That reasoning is about *automatic* ending and
        // it survives intact — nothing here listens for silence. What does not
        // survive is making the user hold a button through a whole spoken
        // sentence while watching an orb that was not drawing: hold-to-talk
        // gives no feedback of its own, so the one broken indicator was the
        // entire interface. Tap to start, tap to stop, and the countdown below
        // is a backstop rather than the normal way a capture ends.
        recordButton = JarvisUi.primary(this, RECORD_START) { toggleRecording() }
        column.addView(recordButton, matchWidth())

        statusView = JarvisUi.mono(this, "")
        column.addView(statusView)
        detailView = JarvisUi.hint(this, "")
        column.addView(detailView)

        column.addView(JarvisUi.spacer(this, JarvisUi.Space.SECTION))
        redoButton = JarvisUi.button(this, "SAY THAT ONE AGAIN") { redo() }
        column.addView(redoButton, matchWidth())
        testButton = JarvisUi.button(this, "TEST MY VOICE") { startTest() }
        column.addView(testButton, matchWidth())
        forgetButton = JarvisUi.button(this, "FORGET MY VOICE") { forget() }
        column.addView(forgetButton, matchWidth())

        column.addView(JarvisUi.spacer(this, JarvisUi.Space.GAP))
        column.addView(
            JarvisUi.hint(
                this,
                "Enrolling does not switch anything on. Whether Jarvis refuses other " +
                    "voices is set on the server (voice: speaker: mode), and the honest " +
                    "order is: enrol, leave it in observe for a few days, read the scores, " +
                    "then enforce."
            )
        )

        return ScrollView(this).apply {
            isFillViewport = true
            addView(
                column,
                ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            )
        }
    }

    private fun matchWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

    // --- capture ------------------------------------------------------------

    /** True between [startCapture] and [stopCapture]. The mic is the state. */
    private val recording: Boolean get() = mic != null

    /** The record button's one job, whichever half of it is showing. */
    private fun toggleRecording() {
        if (recording) finishCapture() else startCapture(Mode.ENROL)
    }

    /**
     * Ends a capture that the user did not.
     *
     * Not a silence detector — see the record button. This is for the phone put
     * down mid-phrase, and it fires far enough out that a person reading a line
     * aloud will never meet it.
     */
    private val autoStop = Runnable { finishCapture() }

    /**
     * The one moving thing that says how much time is left.
     *
     * Posted on the same handler as [autoStop] and cancelled by the same call,
     * so a capture that ends early cannot leave a countdown ticking against a
     * dead microphone.
     */
    private val countdown = object : Runnable {
        override fun run() {
            if (!recording) return
            val left = captureEndsAt - SystemClock.uptimeMillis()
            val seconds = ((left + 999L) / 1000L).coerceAtLeast(0L)
            // The status line, not the orb's caption. The caption is part of
            // the orb's chrome, which this screen turns off because at 200dp
            // the wordmark lands on top of the orb — so a countdown drawn there
            // would not appear at all.
            statusView.text = "$listeningPrompt  ${seconds}s"
            main.postDelayed(this, 200L)
        }
    }

    /** The first half of the listening line; the countdown appends to it. */
    private var listeningPrompt = ""

    private var captureEndsAt = 0L

    private fun startTest() {
        if (busy || recording) return
        val current = status
        if (current == null || !current.usable) {
            toast("Enrol at least ${current?.minSamples ?: 3} phrases first.")
            return
        }
        promptView.text = "Say anything at all, in your ordinary voice."
        startCapture(Mode.TEST)
    }

    private fun startCapture(which: Mode) {
        if (busy || recording) return
        // Recorded BEFORE the permission check, because the grant callback
        // resumes this call and has only `mode` to tell it which capture the
        // user asked for. Set it after, and TEST-then-grant silently enrolled
        // an "say anything at all" utterance as a training sample.
        mode = which
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        capture = ByteArrayOutputStream()
        val streamer = MicStreamer(
            onPcm = { buffer, length ->
                val sink = capture ?: return@MicStreamer
                // Bounded: a capture left running by a phone in a pocket must
                // not grow the heap until the process dies.
                if (sink.size() < MAX_SAMPLE_BYTES) sink.write(buffer, 0, length)
            },
            onLevel = { level -> orb.setAmplitude(level) },
            onUnavailable = { reason ->
                main.post {
                    stopCapture()
                    statusView.text = reason
                    detailView.text = ""
                }
            },
        )
        // Assigned BEFORE start, so the failure path below has something to
        // release. Started inside `also` and assigned after, a throw left the
        // half-opened AudioRecord with no reference anywhere: `stopCapture`
        // stopped the `mic` field, which was still null.
        mic = streamer
        try {
            streamer.start()
        } catch (t: Throwable) {
            stopCapture()
            statusView.text = "Could not open the microphone."
            return
        }

        // Only once the mic is genuinely open. Showing LISTENING beside a
        // microphone that failed to start is the same lie the dead orb told.
        val window = if (which == Mode.TEST) TEST_WINDOW_MS else ENROL_WINDOW_MS
        captureEndsAt = SystemClock.uptimeMillis() + window
        orb.setMode(JarvisOrbView.Mode.LISTENING)
        listeningPrompt = if (which == Mode.TEST) {
            "Listening — say anything, then tap again."
        } else {
            "Listening — say the line, then tap again."
        }
        statusView.text = listeningPrompt
        detailView.text = ""
        // The previous sample's verdict is about the previous sample.
        lastNote = null
        main.postDelayed(autoStop, window)
        main.post(countdown)
        syncButtons()
    }

    private fun finishCapture() {
        val pcm = capture?.toByteArray()
        val which = mode
        stopCapture()
        if (pcm == null || pcm.size < MIN_SAMPLE_BYTES) {
            statusView.text = "That was too short — tap, say the whole line, then tap again."
            return
        }
        // The orb keeps moving across the round trip. A screen that goes still
        // the moment you stop talking reads as a screen that has stopped
        // working, which is most of what the original bug felt like.
        orb.setMode(JarvisOrbView.Mode.THINKING)
        statusView.text = if (which == Mode.TEST) "Checking…" else "Learning your voice…"
        if (which == Mode.TEST) submitTest(pcm) else submitEnrolment(pcm)
    }

    private fun stopCapture() {
        // Named callbacks, not `removeCallbacksAndMessages(null)`. That cleared
        // EVERY message on the main handler, including the `main.post` a worker
        // thread uses to hand a server response back — and that post is what
        // clears `busy` and re-enables the buttons. Losing one left the screen
        // permanently inert with no error anywhere.
        main.removeCallbacks(autoStop)
        main.removeCallbacks(countdown)
        captureEndsAt = 0L
        mic?.stop()
        mic = null
        capture = null
        orb.setAmplitude(0f)
        orb.setMode(JarvisOrbView.Mode.IDLE)
        syncButtons()
    }

    // --- server round trips -------------------------------------------------
    private fun refresh() = offMainThread({ it.status() }) { render(it) }

    private fun submitEnrolment(pcm: ByteArray) =
        offMainThread({ it.enrol(pcm) }) { enrolment ->
            // The phrase list advances because the SERVER's count advanced —
            // see [promptIndex]. Nothing is counted here.
            redo = 0
            // The per-sample verdict the API exists to provide, said out loud
            // for the first time. Null when there is nothing wrong with it.
            lastNote = enrolment.note()
            render(enrolment.status)
        }

    private fun forget() = offMainThread({ it.forget() }) { fresh ->
        redo = 0
        lastNote = null
        toast("Voiceprint deleted.")
        render(fresh)
    }

    /**
     * "That one was not my best — let me say it again."
     *
     * ## What this can and cannot do
     *
     * It re-offers the phrase just read, so the next sample is another go at
     * the same line. It does **not** delete the sample already stored, because
     * `/api/voice/speaker` has no way to: the four endpoints are status, enrol,
     * verify and forget-everything, and there is no per-sample delete to call.
     * Saying so on screen is the honest half of this button — a REDO that
     * quietly left the bad sample in the profile would be worse than none.
     *
     * A sample the server REFUSED — too quiet, no measurable pitch — was never
     * added, so for that case redo genuinely is a clean second attempt, and
     * that is the case this button is mostly for.
     */
    private fun redo() {
        if (busy || recording) return
        val phrases = status?.prompts.orEmpty()
        if (promptIndex <= 0 || phrases.isEmpty()) {
            toast("There is no earlier phrase to go back to.")
            return
        }
        redo += 1
        lastNote = "Reading that line again. The sample you already gave stays in " +
            "the profile — the server has no way to remove just one — so this " +
            "adds to it rather than replacing it. FORGET MY VOICE is the only " +
            "clean restart."
        status?.let { render(it) }
    }

    private fun submitTest(pcm: ByteArray) = offMainThread({ it.verify(pcm) }) { result ->
        val verdict = result.optJSONObject("verdict")
        val accepted = verdict?.optBoolean("accepted") ?: false
        val score = verdict?.optDouble("score") ?: Double.NaN
        val threshold = verdict?.optDouble("threshold") ?: Double.NaN
        val blocked = result.optBoolean("would_block", false)
        statusView.text = if (accepted) "RECOGNISED" else "NOT RECOGNISED"
        detailView.text = buildString {
            append(String.format("score %.2f against threshold %.2f", score, threshold))
            append(if (blocked) "\nWith enforcement on, that turn would have been refused."
                   else "\nThat turn would have been allowed.")
            if (!accepted && !blocked) {
                append("\n(The gate is not enforcing, so nothing was blocked.)")
            }
        }
        refresh()
    }

    /**
     * Run one network call off the main thread and hand the result back on it.
     *
     * Buttons are disabled for the duration rather than debounced: two
     * enrolments in flight at once would race on the profile, and the second
     * response would overwrite the first's view of how many samples exist.
     */
    private fun <T> offMainThread(
        call: (VoiceIdentityClient) -> VoiceIdentityClient.Result<T>,
        onOk: (T) -> Unit,
    ) {
        if (busy) return
        // No client means this phone is not paired. Taking the lambda a client
        // rather than closing over one is what makes that a message instead of
        // an UninitializedPropertyAccessException on a worker thread.
        val live = client
        if (live == null) {
            statusView.text = "Pair this phone with Jarvis first."
            detailView.text = ""
            return
        }
        busy = true
        syncButtons()
        thread {
            val result = call(live)
            main.post {
                busy = false
                // Whatever the answer, the orb stops pretending to think.
                if (!recording) orb.setMode(JarvisOrbView.Mode.IDLE)
                syncButtons()
                when (result) {
                    is VoiceIdentityClient.Result.Ok -> onOk(result.value)
                    is VoiceIdentityClient.Result.Failed -> {
                        // The headline is the client's, not a constant. "Could
                        // not reach Jarvis" was shown for a 404 from a server
                        // that answered in 20 ms, which sent people to look at
                        // their network instead of at their jarvis-core
                        // version. See VoiceIdentityClient.failureFor.
                        statusView.text = result.headline
                        detailView.text = result.message
                        if (!recording) orb.setMode(JarvisOrbView.Mode.ERROR)
                    }
                }
            }
        }
    }

    /**
     * One place that decides what may be pressed, called from every transition.
     *
     * It replaces a boolean `setButtonsEnabled`, which could not express the
     * state this screen now has: while a capture is live the record button must
     * stay pressable — it is the only way to stop — and the other two must not,
     * because TEST mid-enrolment used to rewrite the prompt and then bail out
     * of `startCapture`, leaving the screen recording an enrolment sample under
     * a caption asking for a test one.
     */
    private fun syncButtons() {
        val paired = client != null
        recordButton.isEnabled = paired && (recording || !busy)
        recordButton.text = if (recording) RECORD_STOP else RECORD_START
        testButton.isEnabled = paired && !busy && !recording
        forgetButton.isEnabled = paired && !busy && !recording && (status?.enrolled == true)
        // Only once there is an earlier phrase to go back to. Live from the
        // FIRST accepted sample, which is exactly when "that was too quiet"
        // first becomes possible to think.
        redoButton.isEnabled = paired && !busy && !recording && promptIndex > 0
        redoButton.alpha = if (redoButton.isEnabled) 1f else 0.4f
    }

    /**
     * Draw one row per phrase, marked with what has happened to it.
     *
     * Text glyphs rather than icons, for the same reason [JarvisUi.checkRow]
     * uses them: they survive any font and any accessibility scale, and they
     * copy into a bug report as-is.
     */
    private fun renderSteps(fresh: VoiceIdentityClient.Status) {
        stepList.removeAllViews()
        val phrases = fresh.prompts
        if (phrases.isEmpty()) return
        val next = promptIndex
        for ((index, phrase) in phrases.withIndex()) {
            val given = index < next
            val current = index == next
            val glyph = when {
                given -> "[ok]"
                current -> "[>>]"
                else -> "[  ]"
            }
            val tone = when {
                given -> JarvisUi.APPROVE
                current -> JarvisUi.ACCENT
                else -> JarvisUi.FAINT
            }
            stepList.addView(
                TextView(this).apply {
                    text = "$glyph  $phrase"
                    setTextColor(tone)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.HINT)
                    typeface = Typeface.MONOSPACE
                    setPadding(0, JarvisUi.dp(this@VoiceIdentityActivity, JarvisUi.Space.MICRO), 0, 0)
                    // The glyphs are drawings. TalkBack reads "[ok]" as
                    // bracket-o-k-bracket, so the row says it in English
                    // instead — and the current one says so, because "which
                    // line am I meant to be reading" is the entire question
                    // this list answers.
                    JarvisUi.describe(
                        this,
                        when {
                            given -> "Given: $phrase"
                            current -> "Say this now: $phrase"
                            else -> "Still to say: $phrase"
                        },
                    )
                },
                matchWidth(),
            )
        }
        if (next >= phrases.size) {
            stepList.addView(
                JarvisUi.hint(
                    this,
                    "Every phrase has been given. More samples only help if Jarvis " +
                        "stops recognising you — say something different each time.",
                ),
                matchWidth(),
            )
        }
    }

    private fun render(fresh: VoiceIdentityClient.Status) {
        status = fresh
        // Cache what the server is doing, so a turn starting later does not
        // need a round trip to find out whether on-device transcription would
        // bypass the gate. See JarvisConfig.speakerGateEnforcing.
        config.speakerGateEnforcing = fresh.mode == "enforce" && fresh.enrolled
        val prompts = fresh.prompts
        // Read AFTER `status` is assigned: promptIndex is derived from it.
        val index = promptIndex
        promptView.text = when {
            prompts.isEmpty() -> "Say a sentence in your ordinary voice."
            index < prompts.size -> prompts[index]
            else -> "That is enough — add more only if it stops recognising you."
        }
        renderSteps(fresh)
        statusView.text = if (fresh.enrolled) {
            "${fresh.samples} of ${fresh.maxSamples} samples · gate is ${fresh.mode}"
        } else {
            "Not enrolled — ${fresh.minSamples} phrases needed"
        }
        val worst = fresh.worstSelfScore
        detailView.text = when {
            !fresh.usable ->
                "Nothing is checked until there are ${fresh.minSamples} samples."
            // A threshold that was never measured must not be reported as one.
            // The screen used to print "enrolment suggests 4.00" on a profile
            // whose leave-one-out scores were every one of them infinite —
            // 4.00 is the server's default — while telling the owner to read
            // the scores before enforcing. There were no scores.
            !fresh.thresholdMeasured || worst == null -> String.format(
                "%d samples is enough to check against, but not enough to measure " +
                    "against: scoring one of your samples needs the other %d. Record " +
                    "%d more and this will say what your own worst sample scores. " +
                    "Until then %.2f is the server's default, not a measurement.",
                fresh.samples,
                fresh.minSamples,
                (fresh.measureSamples - fresh.samples).coerceAtLeast(1),
                fresh.suggestedThreshold,
            )
            else -> String.format(
                "Your own worst sample scores %.2f. Enrolment suggests a threshold of " +
                    "%.2f, which is what is in force unless the server names one.",
                worst, fresh.suggestedThreshold,
            )
        }
        // The per-sample verdict goes FIRST and above the profile-wide numbers.
        // It is about the thing the user just did, and it is the reason this API
        // is one sample per request; burying it under a threshold calculation
        // would be throwing it away a second time.
        lastNote?.let { detailView.text = "$it\n\n${detailView.text}" }
        syncButtons()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        if (grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            statusView.text = "Jarvis cannot learn a voice it is not allowed to hear."
            return
        }
        // The tap that raised the dialog was a tap meaning "listen now".
        // Answering it and then finding the screen exactly as it was — no orb,
        // no countdown, no recording — reads as a grant that did not take.
        startCapture(mode)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private companion object {
        const val REQ_MIC = 6001

        /** 16 kHz mono 16-bit. Half a second is below anything usable. */
        const val MIN_SAMPLE_BYTES = 16000 * 2 / 2

        /** 25 s, comfortably under the server's own limit. */
        const val MAX_SAMPLE_BYTES = 16000 * 2 * 25

        /**
         * How long a test capture runs before ending itself.
         *
         * Both windows are backstops now rather than the mechanism: the user
         * taps to stop. They are still generous, because a window that expires
         * mid-sentence truncates the sample, and a truncated ENROLMENT sample
         * is written into the profile and skews it — the same reason nothing
         * here listens for silence.
         */
        const val TEST_WINDOW_MS = 15_000L

        /** Under [MAX_SAMPLE_BYTES] (25 s), so the cap is never what ends one. */
        const val ENROL_WINDOW_MS = 20_000L

        const val RECORD_START = "TAP TO SPEAK"
        const val RECORD_STOP = "TAP TO STOP"
    }
}
