package ai.jarvis.app.tasks

import ai.jarvis.app.R
import ai.jarvis.app.ui.JarvisUi
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.LinearInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Long work, drawn — the same three bar modes as the console's `TaskBar.svelte`.
 *
 * Built in code with no background, like the rest of the overlay furniture: this
 * floats over somebody's home screen, and a panel would put a box round it.
 *
 * **The indeterminate bar is a real one, not a fill at zero.** A task whose
 * progress is genuinely unknowable has to look DIFFERENT from one that has done
 * nothing, and the version of this that draws an empty track for both is the
 * failure the whole task model is arranged to avoid. So: a sweep, and no
 * percentage in the accessibility description either, because a screen reader
 * announcing "0 percent" about a task that is working is the same lie out loud.
 *
 * All arithmetic is in [TaskBoard], which has no Android in it and is pinned by
 * `android-app/tools/task_board_test.py`. This class only paints.
 */
class TaskProgressView(context: Context) : LinearLayout(context) {

    private val label: TextView
    private val headline: TextView
    private val list: LinearLayout
    private val rowViews = ArrayList<RowView>()

    init {
        orientation = VERTICAL
        visibility = GONE

        val header = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        label = chrome("TASKS", JarvisUi.DIM)
        headline = chrome("", JarvisUi.ACCENT)
        headline.gravity = Gravity.END
        header.addView(label, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        header.addView(headline, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
        addView(header, wide(bottom = 6))

        list = LinearLayout(context).apply { orientation = VERTICAL }
        addView(list, wide())
    }

    /** Draw a snapshot, or hide when there is nothing running. */
    fun render(rows: List<TaskBoard.Row>, summary: String) {
        val shown = rows.take(TaskBoard.OVERLAY_ROWS)
        if (shown.isEmpty()) {
            hide()
            return
        }
        visibility = VISIBLE
        headline.text = summary
        // A live region, because the summary moving is the only thing that says
        // progress is happening at all.
        JarvisUi.describe(this, context.getString(R.string.a11y_task_progress, summary))

        while (rowViews.size < shown.size) {
            val row = RowView(context)
            rowViews.add(row)
            list.addView(row, wide(top = 6))
        }
        for ((i, view) in rowViews.withIndex()) {
            if (i < shown.size) {
                view.visibility = VISIBLE
                view.bind(shown[i])
            } else {
                view.visibility = GONE
                view.bind(null)
            }
        }
    }

    fun hide() {
        if (visibility == GONE) return
        visibility = GONE
        for (view in rowViews) view.bind(null)
    }

    private fun chrome(text: String, color: Int): TextView = TextView(context).apply {
        this.text = text
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
        typeface = Typeface.MONOSPACE
        letterSpacing = 0.16f
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
    }

    private fun wide(top: Int = 0, bottom: Int = 0, height: Int = LayoutParams.WRAP_CONTENT) =
        LayoutParams(LayoutParams.MATCH_PARENT, height).apply {
            topMargin = JarvisUi.dp(context, top)
            bottomMargin = JarvisUi.dp(context, bottom)
        }

    /** One task: its title, its bar, and what it is doing. */
    private inner class RowView(context: Context) : LinearLayout(context) {
        private val title: TextView
        private val badge: TextView
        private val says: TextView
        private val bar: BarView

        init {
            orientation = VERTICAL

            val top = LinearLayout(context).apply {
                orientation = HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            title = TextView(context).apply {
                setTextColor(JarvisUi.TEXT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.BODY)
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
                // Silent to a screen reader: the ROW carries the description,
                // because read one at a time these fragments are noise and read
                // together they are the sentence.
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }
            badge = chrome("", JarvisUi.DIM).apply {
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }
            top.addView(title, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            top.addView(
                badge,
                LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply {
                    leftMargin = JarvisUi.dp(context, 8)
                },
            )
            addView(top, wide())

            bar = BarView(context)
            addView(bar, wide(top = 4, height = JarvisUi.dp(context, TRACK_DP)))

            says = TextView(context).apply {
                setTextColor(JarvisUi.FAINT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
                typeface = Typeface.MONOSPACE
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }
            addView(says, wide(top = 2))
        }

        fun bind(row: TaskBoard.Row?) {
            if (row == null) {
                bar.stop()
                return
            }
            title.text = row.title
            badge.text = if (row.steps.isEmpty()) row.label else "${row.steps}  ${row.label}"
            badge.setTextColor(
                when (row.status) {
                    TaskBoard.Status.RUNNING -> JarvisUi.ACCENT
                    TaskBoard.Status.BLOCKED -> JarvisUi.GOLD
                    TaskBoard.Status.DONE -> JarvisUi.APPROVE
                    TaskBoard.Status.ERROR -> JarvisUi.DENY
                    else -> JarvisUi.DIM
                }
            )
            says.text = row.says
            says.setTextColor(
                if (row.status == TaskBoard.Status.ERROR) JarvisUi.DENY else JarvisUi.FAINT
            )
            bar.show(row)

            // Spoken as a sentence, and WITHOUT a number when nobody computed
            // one. "Reading twelve pages, working" beats "…, 0 percent", which
            // is what an indeterminate bar would say if the percentage were
            // read out unconditionally.
            val progress = when (row.bar) {
                TaskBoard.Bar.DETERMINATE -> "${row.percent}%"
                TaskBoard.Bar.INDETERMINATE -> context.getString(R.string.a11y_task_working)
                TaskBoard.Bar.NONE -> row.label.lowercase()
            }
            JarvisUi.describe(
                this,
                context.getString(R.string.a11y_task_row, row.title, progress, row.says),
            )
        }
    }

    /**
     * The bar itself: a fill, a sweep, or a bare rail.
     *
     * The sweep runs on a timer, and that is correct here in a way it never is
     * for a determinate bar: it is not claiming progress, it is saying "this is
     * alive and how far along is unknown". A determinate fill on a timer would
     * be a decoration that lies; a sweep on a timer is the honest drawing of
     * "no number exists".
     */
    private inner class BarView(context: Context) : FrameLayout(context) {
        private val fill: View
        private var sweeper: ValueAnimator? = null
        private var mode: TaskBoard.Bar = TaskBoard.Bar.NONE
        private var percent = 0

        init {
            background = GradientDrawable().apply {
                cornerRadius = JarvisUi.dp(context, 2).toFloat()
                setColor(TRACK_COLOR)
            }
            fill = View(context)
            addView(fill, LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT))
            // The first event arrives before the track has been measured, so
            // without this the bar is stuck at zero for exactly the run it
            // exists to show.
            addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> apply() }
        }

        fun show(row: TaskBoard.Row) {
            val colours = when (row.status) {
                TaskBoard.Status.ERROR -> intArrayOf(JarvisUi.GOLD, JarvisUi.DENY)
                TaskBoard.Status.CANCELLED -> intArrayOf(TRACK_COLOR, TRACK_COLOR)
                else -> intArrayOf(FILL_START, JarvisUi.ACCENT)
            }
            fill.background = GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT, colours
            ).apply { cornerRadius = JarvisUi.dp(context, 2).toFloat() }

            mode = row.bar
            percent = row.percent
            when (mode) {
                TaskBoard.Bar.DETERMINATE -> {
                    stopSweep()
                    fill.visibility = VISIBLE
                    apply()
                }
                TaskBoard.Bar.INDETERMINATE -> {
                    fill.visibility = VISIBLE
                    apply()
                    startSweep()
                }
                TaskBoard.Bar.NONE -> {
                    stopSweep()
                    fill.visibility = GONE
                }
            }
        }

        fun stop() = stopSweep()

        private fun startSweep() {
            if (sweeper != null) return
            val animator = ValueAnimator.ofFloat(0f, 1f).apply {
                duration = SWEEP_MS
                repeatCount = ValueAnimator.INFINITE
                interpolator = LinearInterpolator()
                addUpdateListener { a ->
                    val span = width.toFloat()
                    if (span <= 0f) return@addUpdateListener
                    val at = a.animatedValue as Float
                    fill.translationX = -fill.width + at * (span + fill.width)
                }
            }
            sweeper = animator
            animator.start()
        }

        private fun stopSweep() {
            sweeper?.cancel()
            sweeper = null
            fill.translationX = 0f
        }

        override fun onDetachedFromWindow() {
            // An animator on a detached view is a leak that keeps the whole
            // overlay tree alive, on a surface whose entire point is to come
            // and go.
            stopSweep()
            super.onDetachedFromWindow()
        }

        private fun apply() {
            val span = width
            if (span <= 0) return
            val params = fill.layoutParams ?: return
            val target = when (mode) {
                TaskBoard.Bar.DETERMINATE -> span * percent / 100
                TaskBoard.Bar.INDETERMINATE -> (span * SWEEP_FRACTION).toInt()
                TaskBoard.Bar.NONE -> 0
            }
            if (params.width == target) return
            params.width = target
            fill.layoutParams = params
        }
    }

    companion object {
        private const val TRACK_DP = 3
        private const val SWEEP_MS = 1_400L
        /** How much of the track the sweep occupies. */
        private const val SWEEP_FRACTION = 0.4f
        private const val TRACK_COLOR = 0x33FFFFFF
        private val FILL_START = 0xFF0E7C99.toInt()
    }
}
