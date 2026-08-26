package ai.jarvis.app

import ai.jarvis.app.ui.ApprovalBridge
import ai.jarvis.app.ui.ConsentGate
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.app.KeyguardManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.CountDownTimer
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The Tier-3 consent screen.
 *
 * Everything dangerous the device can do funnels through this activity, so it
 * is written to be boring and unhelpful to an attacker:
 *
 *  * It shows the action id, the VERBATIM parameters and the server's reason.
 *    No summarising, no truncation — long payloads scroll, and RAW flips to the
 *    exact text that was handed to [ApprovalBridge].
 *  * The action's own description and the server's reason are shown separately
 *    and labelled differently. One comes from the device-local action table and
 *    is trustworthy; the other is remote text that may have been written by a
 *    web page the model read. Blending them would hide exactly that difference.
 *  * There is no "remember this" affordance. Tier 3 asks every single time.
 *  * Doing nothing denies: the countdown auto-denies, Back denies, being
 *    destroyed for any reason denies. Only the APPROVE button approves.
 *  * **The keyguard is part of the gate.** The prompt shows over a locked
 *    screen so the phone lights up and the question is not missed, but while
 *    the keyguard is up the parameters stay hidden and APPROVE is inert; the
 *    activity asks the system to dismiss the keyguard and only opens up once
 *    the real user is through it. Otherwise "a human approved it" would mean
 *    "whoever was holding the phone approved it", and a stranger could read an
 *    SMS body or a shell command off the lock screen. DENY stays live
 *    throughout — refusing is safe from anywhere.
 *  * APPROVE is inert for [ConsentGate.ARM_MS] after the prompt becomes
 *    readable and refuses touches that pass through another window, so an
 *    overlay or a stray tap landing exactly where the button appears cannot
 *    approve anything.
 *  * FLAG_SECURE keeps the parameters out of screenshots and off the screen
 *    recorder — including this app's own accessibility path.
 */
class ApprovalActivity : Activity() {

    private var requestId: String? = null
    private var answered = false
    private var countdown: CountDownTimer? = null

    private lateinit var countdownView: TextView
    private lateinit var approveButton: Button
    private lateinit var denyButton: Button
    private lateinit var paramsView: TextView
    private lateinit var gateNoteView: TextView

    private var rawParams: String = ""
    private var prettyParams: String = ""
    private var showingRaw = false
    private var timeoutMs: Long = ApprovalBridge.TIMEOUT_MS

    /** Keyguard state, re-read on every resume rather than cached at create. */
    private var locked = false

    /** True once the prompt has been readable (i.e. unlocked) for ARM_MS. */
    private var armed = false

    /** Set while a dismiss request is outstanding, so we ask exactly once. */
    private var dismissRequested = false

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

        val id = intent?.getStringExtra(ApprovalBridge.EXTRA_REQUEST_ID)
        if (id.isNullOrEmpty()) {
            // No request to answer — nothing to approve, so nothing happens.
            finish()
            return
        }
        requestId = id
        // Reached the screen. `startActivity` returning tells the bridge
        // nothing — a background start the platform refuses does not throw, and
        // a full-screen intent degrades to a heads-up notification whenever the
        // screen is on and unlocked. This is the only positive evidence, and
        // without it the app could not tell "the user is reading the prompt"
        // from "the prompt is sitting in the shade waiting to be tapped".
        ApprovalBridge.raised(id)

        val actionId = intent?.getStringExtra(ApprovalBridge.EXTRA_ACTION_ID).orEmpty()
        val reason = intent?.getStringExtra(ApprovalBridge.EXTRA_REASON).orEmpty()
        val description = intent?.getStringExtra(ApprovalBridge.EXTRA_DESCRIPTION).orEmpty()
        val commandId = intent?.getStringExtra(ApprovalBridge.EXTRA_COMMAND_ID).orEmpty()
        val tierLabel = intent?.getStringExtra(ApprovalBridge.EXTRA_TIER_LABEL)
            ?.takeIf { it.isNotEmpty() }
            ?: "TIER 3 · CONFIRMATION REQUIRED"
        timeoutMs = ApprovalBridge.clampTimeout(
            intent?.getLongExtra(ApprovalBridge.EXTRA_TIMEOUT_MS, ApprovalBridge.TIMEOUT_MS)
                ?: ApprovalBridge.TIMEOUT_MS
        )

        rawParams = intent?.getStringExtra(ApprovalBridge.EXTRA_PARAMS).orEmpty()
        prettyParams = prettyPrint(rawParams)

        setContentView(buildUi(actionId, description, reason, commandId, tierLabel).also { JarvisUi.fitSystemBars(it) })
        refreshGate()
        startCountdown()
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

    /**
     * Recompute the lock state and apply [ConsentGate] to the UI.
     *
     * Called at create, on resume, when the arm timer fires and when the
     * keyguard reports itself dismissed. Every path lands here, so there is one
     * place where "may this button do anything" is decided.
     */
    private fun refreshGate() {
        // onCreate can bail out before the views exist (no request id).
        if (!::approveButton.isInitialized) return
        if (answered) return
        val wasLocked = locked
        locked = isLocked()

        if (locked) {
            // Arming restarts once the prompt is actually readable.
            armed = false
            approveButton.removeCallbacks(armRunnable)
            requestUnlock()
        } else if (wasLocked || !armed) {
            // Freshly readable: start (or restart) the arming delay once.
            approveButton.removeCallbacks(armRunnable)
            approveButton.postDelayed(armRunnable, ConsentGate.ARM_MS)
        }

        paramsView.text = ConsentGate.paramsText(
            locked,
            if (showingRaw) rawParams else prettyParams
        )
        paramsView.isEnabled = ConsentGate.paramsVisible(locked)

        val approve = ConsentGate.approveEnabled(locked, armed, answered)
        approveButton.isEnabled = approve
        approveButton.alpha = if (approve) 1f else 0.4f

        val deny = ConsentGate.denyEnabled(answered)
        denyButton.isEnabled = deny
        denyButton.alpha = if (deny) 1f else 0.4f

        val note = ConsentGate.blockedReason(locked, armed)
        gateNoteView.text = note.orEmpty()
        gateNoteView.visibility = if (note == null) View.GONE else View.VISIBLE
    }

    /** Ask the system to take the user through the keyguard. Once per prompt. */
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
                        // The user chose not to unlock. Nothing opens up; the
                        // countdown will auto-deny. Allow another attempt.
                        dismissRequested = false
                    }
                }
            )
        } catch (t: Throwable) {
            // Never let a keyguard quirk take the prompt down with it: the
            // parameters stay hidden and APPROVE stays inert either way.
            dismissRequested = false
        }
    }

    // --- UI -----------------------------------------------------------------

    private fun buildUi(
        actionId: String,
        description: String,
        reason: String,
        commandId: String,
        tierLabel: String,
    ): ViewGroup {
        val ctx = this
        val root = FrameLayout(ctx).apply { setBackgroundColor(JarvisTokens.Color.SCRIM_APPROVAL) }
        val column = JarvisUi.column(ctx, padDp = JarvisUi.Space.WIDE)

        // The tier, in the held colour: this is a HELD action, which on every
        // other surface is gold with a rule beside it — not red, which is what
        // a refusal looks like, and nothing has been refused yet.
        column.addView(
            TextView(ctx).apply {
                text = tierLabel
                setTextColor(JarvisUi.GOLD)
                textSize = JarvisUi.Type.LABEL
                letterSpacing = JarvisUi.TRACK_WIDE
                typeface = JarvisUi.LABEL_FACE
                gravity = Gravity.CENTER
            }
        )

        column.addView(JarvisUi.spacer(ctx, 4))
        column.addView(
            TextView(ctx).apply {
                text = "Jarvis wants to do something that cannot be quietly undone."
                setTextColor(JarvisUi.DIM)
                textSize = JarvisUi.Type.BODY
                typeface = JarvisUi.BODY_FACE
                gravity = Gravity.CENTER
            }
        )

        column.addView(JarvisUi.label(ctx, "Action"))
        column.addView(
            TextView(ctx).apply {
                text = actionId.ifEmpty { "(no action id)" }
                setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
                textSize = JarvisUi.Type.RESPONSE
                // Mono: an action id is data, and it is the one line on this
                // screen a reader has to match character for character.
                typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
                setTextIsSelectable(true)
                // AN ACTION ID IS NOT A SENTENCE. TalkBack reads
                // `media.play_on_speaker` as one run-on word; the underscores
                // and dots are what a sighted reader sees as separators, so
                // they become spaces for a listener. This is the screen that
                // decides whether something irreversible happens, and mishearing
                // WHICH thing is the failure that matters here.
                JarvisUi.describe(
                    this,
                    ctx.getString(
                        R.string.a11y_approval,
                        actionId.ifEmpty { "no action id" }.replace(Regex("[._]"), " "),
                    ),
                )
            }
        )
        if (description.isNotEmpty()) {
            column.addView(
                TextView(ctx).apply {
                    // From the device-local action table: this text is ours.
                    text = description
                    setTextColor(JarvisUi.DIM)
                    textSize = JarvisUi.Type.BODY
                    setPadding(0, JarvisUi.dp(ctx, JarvisUi.Space.TIGHT), 0, 0)
                }
            )
        }

        column.addView(JarvisUi.label(ctx, "Why the server says so"))
        column.addView(
            TextView(ctx).apply {
                // Remote text. Rendered as text and nothing else.
                text = reason.ifEmpty { "(no reason given — treat that as suspicious)" }
                setTextColor(if (reason.isEmpty()) JarvisUi.DENY else JarvisTokens.Color.TEXT_BRIGHT)
                textSize = JarvisUi.Type.FIELD
                typeface = JarvisUi.BODY_FACE
                setTextIsSelectable(true)
                setLineSpacing(JarvisUi.dp(ctx, JarvisUi.Space.TIGHT).toFloat(), 1f)
            }
        )

        val paramsHeader = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        paramsHeader.addView(
            JarvisUi.label(ctx, "Exactly what will run"),
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        paramsHeader.addView(JarvisUi.button(ctx, "RAW") { toggleRaw() })
        column.addView(paramsHeader)

        // Starts hidden; refreshGate() fills it in once the device is unlocked.
        paramsView = JarvisUi.mono(ctx, ConsentGate.LOCKED_PARAMS)
        column.addView(paramsView)

        countdownView = TextView(ctx).apply {
            setTextColor(JarvisUi.FAINT)
            textSize = JarvisUi.Type.HINT
            typeface = Typeface.MONOSPACE
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(ctx, JarvisUi.Space.SECTION), 0, JarvisUi.dp(ctx, JarvisUi.Space.STEP))
            // A prompt that auto-denies is one a user has a limited time to
            // answer, and the only thing saying so is this line. Nothing read
            // it out; the countdown ran to zero in silence and the action was
            // refused with no explanation a listener ever received.
            JarvisUi.liveRegion(this)
        }
        column.addView(countdownView)

        val buttons = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }
        denyButton = JarvisUi.consentButton(ctx, "DENY", JarvisUi.DENY) { answer(false) }
        approveButton = JarvisUi.consentButton(ctx, "APPROVE", JarvisUi.APPROVE) {
            // Belt and braces: the enabled state is the gate, and this is the
            // same question asked again at the moment of the tap.
            if (ConsentGate.approveEnabled(isLocked(), armed, answered)) answer(true)
        }
        // Both start inert; refreshGate() is the only thing that opens them.
        approveButton.isEnabled = false
        approveButton.alpha = 0.4f

        buttons.addView(
            denyButton,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        buttons.addView(View(ctx), LinearLayout.LayoutParams(JarvisUi.dp(ctx, JarvisUi.Size.CHIP), 1))
        buttons.addView(
            approveButton,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        column.addView(buttons)

        gateNoteView = JarvisUi.hint(ctx, "").apply {
            gravity = Gravity.CENTER
            // Says WHY the buttons are dead — locked phone, arming delay. A
            // disabled button with an unspoken reason beside it is a screen
            // that appears to have stopped working.
            JarvisUi.liveRegion(this)
        }
        column.addView(gateNoteView)

        val footer = StringBuilder(
            "Tier 3 is asked every time — this answer is not remembered. " +
                "If you did not just ask for this, deny it."
        )
        if (commandId.isNotEmpty()) footer.append("\nCommand ").append(commandId)
        column.addView(JarvisUi.hint(ctx, footer.toString()))

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
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        return root
    }

    private fun toggleRaw() {
        showingRaw = !showingRaw
        // Through the gate, so RAW cannot be used to reveal the parameters
        // while the keyguard is still up.
        refreshGate()
    }

    /**
     * Indent JSON for readability, falling back to the untouched string for
     * anything that does not parse. Re-serialising can lose duplicate keys in a
     * hostile payload, which is exactly why the RAW toggle exists and why the
     * fallback is the original text rather than an error message.
     */
    private fun prettyPrint(raw: String): String {
        val trimmed = raw.trim()
        return try {
            when {
                trimmed.startsWith("{") -> JSONObject(trimmed).toString(2)
                trimmed.startsWith("[") -> JSONArray(trimmed).toString(2)
                else -> raw
            }
        } catch (e: Exception) {
            raw
        }
    }

    // --- countdown ----------------------------------------------------------

    private fun startCountdown() {
        countdown?.cancel()
        countdown = object : CountDownTimer(timeoutMs, 1000L) {
            override fun onTick(millisUntilFinished: Long) {
                val seconds = (millisUntilFinished / 1000L).coerceAtLeast(0L)
                countdownView.text = "AUTO-DENY IN ${seconds}s"
            }

            override fun onFinish() {
                countdownView.text = "AUTO-DENIED"
                answer(approved = false, timedOut = true)
            }
        }.also { it.start() }
    }

    // --- answering ----------------------------------------------------------

    private fun answer(approved: Boolean, timedOut: Boolean = false) {
        if (answered) return
        // The last line of defence: an APPROVE that reaches here without the
        // gate open is turned into a denial rather than trusted.
        if (approved && !ConsentGate.approveEnabled(isLocked(), armed, false)) return
        answered = true
        if (::approveButton.isInitialized) approveButton.removeCallbacks(armRunnable)
        countdown?.cancel()
        countdown = null
        requestId?.let { id ->
            if (timedOut) ApprovalBridge.deliverTimeout(id)
            else ApprovalBridge.deliver(id, approved)
        }
        finish()
    }

    @Deprecated("Back must deny. Predictive back is disabled in the manifest, so this is the path.")
    override fun onBackPressed() {
        // Deliberately does not call super: answer() finishes the activity, and
        // the one thing Back must never do here is fall through to a default
        // that leaves the request unanswered.
        answer(false)
    }

    override fun onDestroy() {
        // Swiped away, killed, config-changed out of existence — all of it is a
        // denial. This is the last line of the fail-closed guarantee.
        countdown?.cancel()
        countdown = null
        if (::approveButton.isInitialized) approveButton.removeCallbacks(armRunnable)
        if (!answered) {
            answered = true
            requestId?.let { ApprovalBridge.deliver(it, false) }
        }
        super.onDestroy()
    }
}
