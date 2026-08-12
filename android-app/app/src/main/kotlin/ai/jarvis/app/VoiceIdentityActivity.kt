package ai.jarvis.app

import ai.jarvis.app.assist.MicStreamer
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.VoiceIdentityClient
import ai.jarvis.app.ui.JarvisOrbView
import ai.jarvis.app.ui.JarvisUi
import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
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
     * built by `JarvisUi.ghost`, which does not touch `isEnabled`. So on a
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

    private var status: VoiceIdentityClient.Status? = null
    private var promptIndex = 0
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
            setButtonsEnabled(false)
            return
        }
        client = VoiceIdentityClient(config.serverUrl, config.token)
        refresh()
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }

    // --- UI -----------------------------------------------------------------
    private fun buildUi(): ViewGroup {
        val column = JarvisUi.column(this)
        column.addView(JarvisUi.title(this, "YOUR VOICE"))
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

        orb = JarvisOrbView(this).apply { setMode(JarvisOrbView.Mode.IDLE) }
        column.addView(
            orb,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                JarvisUi.dp(this, 160)
            )
        )

        column.addView(JarvisUi.spacer(this, 12))
        column.addView(JarvisUi.label(this, "Say this"))
        promptView = JarvisUi.responseView(this)
        column.addView(promptView)

        column.addView(JarvisUi.spacer(this, 12))
        recordButton = JarvisUi.pill(this, "HOLD TO RECORD") { }
        recordButton.setOnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    // Push-to-talk rather than a VAD. The phrase is known, the
                    // user is reading it, and a silence detector that cuts the
                    // last word off an enrolment sample poisons the profile
                    // rather than merely annoying somebody.
                    if (!busy) startCapture(Mode.ENROL)
                    true
                }
                MotionEvent.ACTION_UP,
                MotionEvent.ACTION_CANCEL -> {
                    view.performClick()
                    finishCapture()
                    true
                }
                else -> false
            }
        }
        column.addView(recordButton, matchWidth())

        statusView = JarvisUi.mono(this, "")
        column.addView(statusView)
        detailView = JarvisUi.hint(this, "")
        column.addView(detailView)

        column.addView(JarvisUi.spacer(this, 16))
        testButton = JarvisUi.ghost(this, "TEST MY VOICE") { startTest() }
        column.addView(testButton, matchWidth())
        forgetButton = JarvisUi.ghost(this, "FORGET MY VOICE") { forget() }
        column.addView(forgetButton, matchWidth())

        column.addView(JarvisUi.spacer(this, 12))
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
    private fun startTest() {
        if (busy) return
        val current = status
        if (current == null || !current.usable) {
            toast("Enrol at least ${current?.minSamples ?: 3} phrases first.")
            return
        }
        promptView.text = "Say anything at all, in your ordinary voice."
        startCapture(Mode.TEST)
        // No hold-to-talk for the test: the point is an ordinary utterance, so
        // it is a fixed window the user talks into.
        main.postDelayed({ finishCapture() }, TEST_WINDOW_MS)
    }

    private fun startCapture(which: Mode) {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (mic != null) return
        mode = which
        capture = ByteArrayOutputStream()
        orb.setMode(JarvisOrbView.Mode.LISTENING)
        orb.setStateLabel("LISTENING")
        mic = MicStreamer(
            onPcm = { buffer, length ->
                val sink = capture ?: return@MicStreamer
                // Bounded: a button held down by a pocket must not grow the
                // heap until the process dies.
                if (sink.size() < MAX_SAMPLE_BYTES) sink.write(buffer, 0, length)
            },
            onLevel = { level -> orb.setAmplitude(level) },
            onUnavailable = { reason ->
                main.post {
                    stopCapture()
                    statusView.text = reason
                }
            },
        ).also {
            try {
                it.start()
            } catch (t: Throwable) {
                stopCapture()
                statusView.text = "Could not open the microphone."
            }
        }
    }

    private fun finishCapture() {
        val pcm = capture?.toByteArray()
        stopCapture()
        if (pcm == null || pcm.size < MIN_SAMPLE_BYTES) {
            statusView.text = "That was too short — hold the button while you say the line."
            return
        }
        if (mode == Mode.TEST) submitTest(pcm) else submitEnrolment(pcm)
    }

    private fun stopCapture() {
        main.removeCallbacksAndMessages(null)
        mic?.stop()
        mic = null
        capture = null
        orb.setAmplitude(0f)
        orb.setMode(JarvisOrbView.Mode.IDLE)
        orb.setStateLabel("")
    }

    // --- server round trips -------------------------------------------------
    private fun refresh() = offMainThread({ it.status() }) { render(it) }

    private fun submitEnrolment(pcm: ByteArray) =
        offMainThread({ it.enrol(pcm) }) { fresh ->
            promptIndex += 1
            render(fresh)
        }

    private fun forget() = offMainThread({ it.forget() }) { fresh ->
        promptIndex = 0
        toast("Voiceprint deleted.")
        render(fresh)
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
        setButtonsEnabled(false)
        thread {
            val result = call(live)
            main.post {
                busy = false
                setButtonsEnabled(true)
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
                    }
                }
            }
        }
    }

    private fun setButtonsEnabled(enabled: Boolean) {
        recordButton.isEnabled = enabled
        testButton.isEnabled = enabled
        forgetButton.isEnabled = enabled
    }

    private fun render(fresh: VoiceIdentityClient.Status) {
        status = fresh
        // Cache what the server is doing, so a turn starting later does not
        // need a round trip to find out whether on-device transcription would
        // bypass the gate. See JarvisConfig.speakerGateEnforcing.
        config.speakerGateEnforcing = fresh.mode == "enforce" && fresh.enrolled
        val prompts = fresh.prompts
        promptView.text = when {
            prompts.isEmpty() -> "Say a sentence in your ordinary voice."
            promptIndex < prompts.size -> prompts[promptIndex]
            else -> "That is enough — add more only if it stops recognising you."
        }
        statusView.text = if (fresh.enrolled) {
            "${fresh.samples} of ${fresh.maxSamples} samples · gate is ${fresh.mode}"
        } else {
            "Not enrolled — ${fresh.minSamples} phrases needed"
        }
        val worst = fresh.worstSelfScore
        detailView.text = when {
            !fresh.usable ->
                "Nothing is checked until there are ${fresh.minSamples} samples."
            worst != null -> String.format(
                "Your own worst sample scores %.2f. Enrolment suggests a threshold of " +
                    "%.2f, which is what is in force unless the server names one.",
                worst, fresh.suggestedThreshold,
            )
            else -> ""
        }
        forgetButton.isEnabled = fresh.enrolled
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC && grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            statusView.text = "Jarvis cannot learn a voice it is not allowed to hear."
        }
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private companion object {
        const val REQ_MIC = 6001

        /** 16 kHz mono 16-bit. Half a second is below anything usable. */
        const val MIN_SAMPLE_BYTES = 16000 * 2 / 2

        /** 25 s, comfortably under the server's own limit. */
        const val MAX_SAMPLE_BYTES = 16000 * 2 * 25

        const val TEST_WINDOW_MS = 4_000L
    }
}
