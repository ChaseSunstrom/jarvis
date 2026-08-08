package ai.jarvis.app

import ai.jarvis.app.ui.ApprovalBridge
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.graphics.Color
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

/**
 * The Tier-3 consent screen.
 *
 * Everything dangerous the device can do funnels through this activity, so it
 * is written to be boring and unhelpful to an attacker:
 *
 *  * It shows the action id, the VERBATIM parameters and the server's reason.
 *    No summarising, no truncation — long payloads scroll, and RAW flips to the
 *    exact bytes that were handed to [ApprovalBridge].
 *  * There is no "remember this" affordance. Tier 3 asks every single time.
 *  * Doing nothing denies: a 60 s countdown auto-denies, Back denies, being
 *    destroyed for any reason denies. Only the APPROVE button approves.
 *  * APPROVE is inert for [ARM_MS] after the prompt appears and refuses touches
 *    that pass through another window, so an overlay or a stray tap landing
 *    exactly where the button appears cannot approve anything.
 *  * FLAG_SECURE keeps the parameters out of screenshots and off the screen
 *    recorder — including this app's own accessibility path.
 */
class ApprovalActivity : Activity() {

    private var requestId: String? = null
    private var answered = false
    private var countdown: CountDownTimer? = null

    private lateinit var countdownView: TextView
    private lateinit var approveButton: Button
    private lateinit var paramsView: TextView

    private var rawParams: String = ""
    private var prettyParams: String = ""
    private var showingRaw = false

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

        val actionId = intent?.getStringExtra(ApprovalBridge.EXTRA_ACTION_ID).orEmpty()
        val reason = intent?.getStringExtra(ApprovalBridge.EXTRA_REASON).orEmpty()
        rawParams = intent?.getStringExtra(ApprovalBridge.EXTRA_PARAMS).orEmpty()
        prettyParams = prettyPrint(rawParams)

        setContentView(buildUi(actionId, reason))
        startCountdown()
    }

    // --- UI -----------------------------------------------------------------

    private fun buildUi(actionId: String, reason: String): ViewGroup {
        val ctx = this
        val root = FrameLayout(ctx).apply { setBackgroundColor(0xF204070C.toInt()) }

        val column = JarvisUi.column(ctx, padDp = 24)

        column.addView(
            TextView(ctx).apply {
                text = "TIER 3 · CONFIRMATION REQUIRED"
                setTextColor(JarvisUi.DENY)
                textSize = 12f
                letterSpacing = 0.24f
                typeface = android.graphics.Typeface.create(
                    android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD
                )
                gravity = Gravity.CENTER
            }
        )

        column.addView(JarvisUi.spacer(ctx, 4))
        column.addView(
            TextView(ctx).apply {
                text = "Jarvis wants to do something that cannot be quietly undone."
                setTextColor(JarvisUi.DIM)
                textSize = 13f
                gravity = Gravity.CENTER
            }
        )

        column.addView(JarvisUi.label(ctx, "Action"))
        column.addView(
            TextView(ctx).apply {
                text = actionId.ifEmpty { "(no action id)" }
                setTextColor(Color.WHITE)
                textSize = 19f
                typeface = android.graphics.Typeface.create(
                    android.graphics.Typeface.MONOSPACE, android.graphics.Typeface.BOLD
                )
                setTextIsSelectable(true)
            }
        )

        column.addView(JarvisUi.label(ctx, "Why"))
        column.addView(
            TextView(ctx).apply {
                // The server's own words, rendered as text and nothing else.
                text = reason.ifEmpty { "(no reason given — treat that as suspicious)" }
                setTextColor(if (reason.isEmpty()) JarvisUi.DENY else Color.WHITE)
                textSize = 15f
                setTextIsSelectable(true)
                setLineSpacing(JarvisUi.dp(ctx, 3).toFloat(), 1f)
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
        val rawToggle = JarvisUi.ghost(ctx, "RAW") { toggleRaw() }
        paramsHeader.addView(rawToggle)
        column.addView(paramsHeader)

        paramsView = JarvisUi.mono(ctx, prettyParams.ifEmpty { "(no parameters)" })
        column.addView(paramsView)

        countdownView = TextView(ctx).apply {
            setTextColor(JarvisUi.FAINT)
            textSize = 12f
            typeface = android.graphics.Typeface.MONOSPACE
            gravity = Gravity.CENTER
            setPadding(0, JarvisUi.dp(ctx, 16), 0, JarvisUi.dp(ctx, 8))
        }
        column.addView(countdownView)

        val buttons = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }
        val deny = JarvisUi.consentButton(ctx, "DENY", JarvisUi.DENY) { answer(false) }
        approveButton = JarvisUi.consentButton(ctx, "APPROVE", JarvisUi.APPROVE) { answer(true) }
        // Armed only after the prompt has been on screen long enough to read.
        approveButton.isEnabled = false
        approveButton.alpha = 0.4f
        approveButton.postDelayed({
            if (!answered) {
                approveButton.isEnabled = true
                approveButton.alpha = 1f
            }
        }, ARM_MS)

        buttons.addView(
            deny,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        buttons.addView(
            View(ctx),
            LinearLayout.LayoutParams(JarvisUi.dp(ctx, 14), 1)
        )
        buttons.addView(
            approveButton,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        )
        column.addView(buttons)

        column.addView(
            JarvisUi.hint(
                ctx,
                "Tier 3 is asked every time — this answer is not remembered. " +
                    "If you did not just ask for this, deny it."
            )
        )

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
        root.addView(
            JarvisUi.CornerBrackets(ctx, JarvisUi.DENY),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        return root
    }

    private fun toggleRaw() {
        showingRaw = !showingRaw
        paramsView.text = when {
            showingRaw -> rawParams.ifEmpty { "(no parameters)" }
            else -> prettyParams.ifEmpty { "(no parameters)" }
        }
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
        countdown = object : CountDownTimer(ApprovalBridge.TIMEOUT_MS, 1000L) {
            override fun onTick(millisUntilFinished: Long) {
                val seconds = (millisUntilFinished / 1000L).coerceAtLeast(0L)
                countdownView.text = "AUTO-DENY IN ${seconds}s"
            }

            override fun onFinish() {
                countdownView.text = "AUTO-DENIED"
                answer(false)
            }
        }.also { it.start() }
    }

    // --- answering ----------------------------------------------------------

    private fun answer(approved: Boolean) {
        if (answered) return
        answered = true
        countdown?.cancel()
        countdown = null
        requestId?.let { ApprovalBridge.deliver(it, approved) }
        finish()
    }

    @Deprecated("Back must deny; the predictive-back callback is disabled in the manifest.")
    override fun onBackPressed() {
        answer(false)
        @Suppress("DEPRECATION")
        super.onBackPressed()
    }

    override fun onDestroy() {
        // Swiped away, killed, config-changed out of existence — all of it is a
        // denial. This is the last line of the fail-closed guarantee.
        countdown?.cancel()
        countdown = null
        if (!answered) {
            answered = true
            requestId?.let { ApprovalBridge.deliver(it, false) }
        }
        super.onDestroy()
    }

    companion object {
        /** How long APPROVE stays inert after the prompt appears. */
        private const val ARM_MS = 700L
    }
}
