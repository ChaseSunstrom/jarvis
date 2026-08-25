package ai.jarvis.app.assist

import ai.jarvis.app.R
import ai.jarvis.app.ui.JarvisUi
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * What Jarvis is doing, while it does it — the visible half, on the phone.
 *
 * The same rows and the same bar as the console's `ToolActivity.svelte`, drawn
 * under the orb in [AssistOverlay] and in the in-app conversation. Before this
 * existed, a turn that unlocked a door and set a thermostat said "PROCESSING"
 * for nine seconds and then spoke a sentence; what it had actually touched was
 * knowable only from the server's logs.
 *
 * Built in code with no background, like everything else in the overlay: this
 * floats over somebody's home screen, and a panel would put a box round it. Two
 * things carry legibility instead — a shadow under every glyph, and the fact
 * that nothing here is drawn at all unless a turn is running.
 *
 * The arithmetic lives in [ToolRun], which has no Android in it and is pinned
 * by `android-app/tools/tool_run_test.py`. This class only paints.
 */
class ToolActivityView(context: Context) : LinearLayout(context) {

    private val label: TextView
    private val count: TextView
    private val fill: View
    private val track: FrameLayout
    private val list: LinearLayout

    /** Row views, reused between updates so the pulse does not restart. */
    private val rowViews = ArrayList<RowView>()

    private var fillAnimator: ValueAnimator? = null
    /** The last percentage the server reported. -1 until a turn starts. */
    private var lastPercent = -1
    /** Where the bar is drawn right now, which lags [lastPercent] while easing. */
    private var shownPercent = 0

    init {
        orientation = VERTICAL
        visibility = GONE

        val header = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        label = chrome("WORKING", JarvisUi.DIM)
        count = chrome("0 / 0", JarvisUi.ACCENT)
        count.gravity = Gravity.END
        header.addView(label, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        header.addView(count, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
        addView(header, wide(bottom = 6))

        // The bar. `track` is the ground it runs on; `fill` is a child whose
        // width is the truth — see [setPercent], which animates between two
        // real values rather than running on a timer.
        track = FrameLayout(context).apply {
            background = GradientDrawable().apply {
                cornerRadius = JarvisUi.dp(context, JarvisUi.Space.MICRO).toFloat()
                setColor(TRACK_COLOR)
            }
        }
        fill = View(context).apply {
            background = GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(FILL_START, JarvisUi.ACCENT),
            ).apply { cornerRadius = JarvisUi.dp(context, JarvisUi.Space.MICRO).toFloat() }
        }
        track.addView(
            fill,
            FrameLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT),
        )
        // The width of the fill is a fraction of a width the track does not have
        // until it is measured, and the first event of a turn arrives before
        // that. Without this the bar is stuck at zero for the whole first round
        // — the exact case the panel exists for.
        track.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> applyFill() }
        addView(track, wide(height = TRACK_DP))

        list = LinearLayout(context).apply { orientation = VERTICAL }
        addView(list, wide(top = 6))
    }

    /**
     * Draw a snapshot, or hide if there is nothing to draw.
     *
     * Idempotent and cheap to call on every event: rows are reused in place,
     * so a running row keeps its pulse rather than restarting it four times a
     * second.
     */
    fun render(run: ToolRun) {
        val rows = run.visible(MAX_ROWS)
        if (rows.isEmpty()) {
            hide()
            return
        }
        visibility = VISIBLE

        label.text = when {
            run.running -> "WORKING"
            run.failed > 0 -> "FINISHED WITH ERRORS"
            else -> "DONE"
        }
        label.setTextColor(if (run.failed > 0 && !run.running) JarvisUi.GOLD else JarvisUi.DIM)
        count.text = "${run.done} / ${run.total}"
        setPercent(run.percent, failed = run.failed > 0)

        // WHAT JARVIS IS TOUCHING, out loud.
        //
        // This whole panel — a header, a progress track and up to four rows of
        // tool calls — was invisible to TalkBack: a `View` progress bar with no
        // description, and rows made of three TextViews that are each a
        // fragment. Read one at a time they are noise ("weather", "…",
        // "kitchen"); read as a row they are the sentence. So the ROW carries
        // the description and its parts stay silent, and the header is a live
        // region because "3 / 5" moving is the only thing that says progress is
        // happening at all.
        JarvisUi.describe(
            this,
            context.getString(R.string.a11y_tool_activity, "${label.text} ${count.text}"),
        )

        while (rowViews.size < rows.size) {
            val row = RowView(context)
            rowViews.add(row)
            list.addView(row, wide())
        }
        for ((i, view) in rowViews.withIndex()) {
            if (i < rows.size) {
                view.visibility = VISIBLE
                view.bind(rows[i])
            } else {
                view.visibility = GONE
                view.bind(null)
            }
        }
    }

    fun hide() {
        if (visibility == GONE) return
        visibility = GONE
        fillAnimator?.cancel()
        fillAnimator = null
        lastPercent = -1
        shownPercent = 0
        applyFill()
        for (view in rowViews) view.bind(null)
    }

    /**
     * Move the bar to [percent].
     *
     * Animated only between two values the server actually reported, and only
     * forwards from where it already was. A bar that eased along on its own
     * clock would be a decoration that lies during exactly the seconds anybody
     * is looking at it.
     */
    private fun setPercent(percent: Int, failed: Boolean) {
        (fill.background as? GradientDrawable)?.colors =
            if (failed) intArrayOf(JarvisUi.GOLD, JarvisUi.DENY)
            else intArrayOf(FILL_START, JarvisUi.ACCENT)
        if (percent == lastPercent) return
        val from = lastPercent.coerceAtLeast(0)
        lastPercent = percent
        fillAnimator?.cancel()
        fillAnimator = ValueAnimator.ofInt(from, percent).apply {
            duration = FILL_MS
            addUpdateListener { a ->
                shownPercent = a.animatedValue as Int
                applyFill()
            }
            start()
        }
    }

    /** Give the fill the width [shownPercent] says it has, if the track has one yet. */
    private fun applyFill() {
        val width = track.width
        if (width <= 0) return
        val params = fill.layoutParams ?: return
        val target = width * shownPercent / 100
        if (params.width == target) return
        params.width = target
        fill.layoutParams = params
    }

    private fun chrome(text: String, color: Int): TextView = TextView(context).apply {
        this.text = text
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
        letterSpacing = 0.14f
        typeface = Typeface.MONOSPACE
        shadow(this)
    }

    private fun wide(top: Int = 0, bottom: Int = 0, height: Int = LayoutParams.WRAP_CONTENT) =
        LayoutParams(LayoutParams.MATCH_PARENT, height).apply {
            topMargin = JarvisUi.dp(context, top)
            bottomMargin = JarvisUi.dp(context, bottom)
        }

    /** One tool call: a state dot, the name, its arguments, and the outcome. */
    private inner class RowView(context: Context) : LinearLayout(context) {
        private val dot = View(context)
        private val name = TextView(context)
        private val args = TextView(context)
        private val meta = TextView(context)
        private var pulsing = false

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL

            val side = JarvisUi.dp(context, JarvisUi.Space.SNUG)
            dot.background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(JarvisUi.FAINT)
            }
            addView(
                dot,
                LayoutParams(side, side).apply {
                    rightMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
                },
            )

            style(name, Color.WHITE, bold = true)
            style(args, JarvisUi.FAINT)
            style(meta, JarvisUi.FAINT)
            meta.gravity = Gravity.END

            addView(name, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
            // The arguments take the slack and lose it first: a long prompt must
            // not push the outcome off the right edge, because the outcome is
            // the half that says whether the house changed.
            addView(
                args,
                LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f).apply {
                    leftMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
                    rightMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
                },
            )
            addView(meta, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))

            // The row is the unit of meaning; its three text fragments are not.
            // Read separately TalkBack says "weather", "kitchen", "412ms" as
            // three unrelated things.
            isFocusable = true
            name.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            args.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            meta.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            dot.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }

        fun bind(row: ToolRun.Row?) {
            if (row == null) {
                stopPulse()
                return
            }
            name.text = row.name
            args.text = row.summary
            args.visibility = if (row.summary.isEmpty()) GONE else VISIBLE

            when (row.state) {
                ToolRun.State.RUNNING -> {
                    tint(JarvisUi.ACCENT)
                    meta.text = "…"
                    meta.setTextColor(JarvisUi.FAINT)
                    startPulse()
                }
                ToolRun.State.OK -> {
                    stopPulse()
                    tint(JarvisUi.APPROVE)
                    meta.text = "${row.durationMs}ms"
                    meta.setTextColor(JarvisUi.FAINT)
                }
                ToolRun.State.FAILED -> {
                    stopPulse()
                    tint(JarvisUi.DENY)
                    // The reason, not a red dot. "It failed" is a fact the user
                    // can already see; which entity was missing is the answer.
                    meta.text = row.error ?: "failed"
                    meta.setTextColor(JarvisUi.DENY)
                }
            }
            JarvisUi.describe(this, spokenRow(row))
        }

        /**
         * The row as one English sentence.
         *
         * Deliberately says the STATE first. A blind user scanning this list
         * wants "failed" before the tool name, the same way the red dot on the
         * left is the first thing a sighted one sees.
         */
        private fun spokenRow(row: ToolRun.Row): String {
            val state = when (row.state) {
                ToolRun.State.RUNNING -> "running"
                ToolRun.State.OK -> "done in ${row.durationMs} milliseconds"
                ToolRun.State.FAILED -> "failed: ${row.error ?: "no reason given"}"
            }
            val what = if (row.summary.isEmpty()) row.name else "${row.name}, ${row.summary}"
            return "$state — $what"
        }

        private fun tint(color: Int) {
            (dot.background as? GradientDrawable)?.setColor(color)
        }

        private fun startPulse() {
            if (pulsing) return
            pulsing = true
            dot.animate().cancel()
            pulse()
        }

        private fun pulse() {
            if (!pulsing) return
            dot.animate()
                .alpha(0.25f)
                .setDuration(PULSE_MS)
                .withEndAction {
                    if (!pulsing) return@withEndAction
                    dot.animate()
                        .alpha(1f)
                        .setDuration(PULSE_MS)
                        .withEndAction { pulse() }
                        .start()
                }
                .start()
        }

        private fun stopPulse() {
            pulsing = false
            dot.animate().cancel()
            dot.alpha = 1f
        }

        private fun style(view: TextView, color: Int, bold: Boolean = false) {
            view.setTextColor(color)
            view.setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            view.typeface =
                if (bold) Typeface.create(Typeface.MONOSPACE, Typeface.BOLD) else Typeface.MONOSPACE
            view.maxLines = 1
            view.ellipsize = TextUtils.TruncateAt.END
            shadow(view)
        }
    }

    companion object {
        /**
         * How many calls fit under the orb before the rest are only counted.
         *
         * Four, and the header still says how many there really are. The orb is
         * the surface; a list that grew without limit would bury it.
         */
        const val MAX_ROWS = 4

        private const val TRACK_DP = 3
        private const val FILL_MS = 220L
        private const val PULSE_MS = 500L

        private val TRACK_COLOR = JarvisTokens.Color.TRACK
        private val FILL_START = JarvisTokens.Color.ACCENT_DEEP

        /**
         * What replaces a panel: a hard shadow under the glyphs, so text drawn
         * over a white app, a photo or a video still reads. The same trick
         * [AssistOverlay] uses on its caption, and for the same reason.
         */
        fun shadow(view: TextView) {
            val context = view.context
            view.setShadowLayer(
                JarvisUi.dp(context, JarvisUi.Space.TIGHT).toFloat(),
                0f,
                JarvisUi.dp(context, JarvisUi.Space.HAIRLINE).toFloat(),
                JarvisTokens.Color.SCRIM_HEAVY,
            )
        }
    }
}
