package ai.jarvis.app.ui

import ai.jarvis.app.assist.ActivityRows
import android.content.Context
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The living activity under the reactor (M61): the same rows the console's
 * strip draws, painted the console's way (M64) — ONE panel with an ACTIVITY
 * head, a hairline between rows, and in each row a state dot, the kind as a
 * hairline tag, the title in the body face and the datum in mono with tabular
 * digits. The accent is spent on the dot: it glows and pulses while the thing
 * is live, the danger mark when it failed, the tick colour otherwise. It was a
 * 64 dp bold mono kind tag tinted accent for every live row, which lit the
 * whole strip whenever anything was happening.
 *
 * Only the newest row moves (`Activity.svelte`): it enters from below over
 * `motion.dur.enter`; the first paint of a strip staggers its rows on
 * `motion.stagger.step`. Under reduced motion nothing moves at all. Nothing is
 * drawn unless there is a row; the arithmetic is [ActivityRows], pinned by
 * `android-app/tools/activity_mirror_test.py`. This class only paints.
 */
class ActivityStrip(context: Context) : LinearLayout(context) {

    private val rowViews = ArrayList<RowView>()
    private val list: LinearLayout
    private var newestId: String? = null

    init {
        orientation = VERTICAL
        visibility = GONE
        background = JarvisUi.panel(context)
        JarvisUi.describe(this, "what Jarvis is doing")

        // `Panel.svelte`'s head: the label recipe, wide tracking, on its own
        // hairline.
        val head = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(
                JarvisUi.dp(context, JarvisUi.Space.SECTION),
                JarvisUi.dp(context, JarvisUi.Space.ROW),
                JarvisUi.dp(context, JarvisUi.Space.SECTION),
                JarvisUi.dp(context, JarvisUi.Space.ROW),
            )
            addView(JarvisUi.labelText(context, HEAD), LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        }
        addView(head, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        addView(JarvisUi.hairline(context))

        list = LinearLayout(context).apply {
            orientation = VERTICAL
            setPadding(
                JarvisUi.dp(context, JarvisUi.Space.SECTION),
                0,
                JarvisUi.dp(context, JarvisUi.Space.SECTION),
                JarvisUi.dp(context, JarvisUi.Space.STEP),
            )
            showDividers = SHOW_DIVIDER_MIDDLE
            dividerDrawable = GradientDrawable().apply {
                setSize(0, JarvisUi.dp(context, JarvisUi.Space.HAIRLINE))
                setColor(JarvisTokens.Color.LINE_HAIR)
            }
        }
        addView(list, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }

    fun render(rows: ActivityRows) {
        val shown = rows.rows.take(SHOWN)
        val wasShowing = visibility == VISIBLE
        visibility = if (shown.isEmpty()) GONE else VISIBLE
        while (rowViews.size < shown.size) {
            val view = RowView(context)
            rowViews.add(view)
            list.addView(view, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        }
        rowViews.forEachIndexed { index, view ->
            val row = shown.getOrNull(index)
            view.visibility = if (row == null) GONE else VISIBLE
            view.bind(row)
        }
        // The first paint staggers every row; after that only a row that is
        // genuinely new enters, and it is always at the top.
        val newest = shown.firstOrNull()?.id
        if (shown.isNotEmpty() && !wasShowing) {
            for (i in shown.indices) JarvisUi.enter(rowViews[i], i)
        } else if (newest != null && newest != newestId) {
            JarvisUi.enter(rowViews[0])
        }
        newestId = newest
    }

    private class RowView(context: Context) : LinearLayout(context) {
        private val dot = StateDot(context)
        private val tag = JarvisUi.statusTag(context, "")
        private val title = TextView(context)
        private val detail = TextView(context)

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, JarvisUi.dp(context, JarvisUi.Space.STEP))
            title.setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.BODY)
            title.typeface = JarvisUi.BODY_FACE
            title.setTextColor(JarvisUi.TEXT)
            title.maxLines = 1
            title.ellipsize = TextUtils.TruncateAt.END
            detail.setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            detail.typeface = JarvisUi.MONO_FACE
            detail.fontFeatureSettings = TABULAR
            detail.setTextColor(JarvisTokens.Color.TEXT_DIM)
            detail.maxLines = 1
            detail.ellipsize = TextUtils.TruncateAt.END
            val gap = JarvisUi.dp(context, JarvisUi.Space.STEP)
            addView(
                dot,
                LayoutParams(JarvisUi.dp(context, JarvisUi.Space.STEP), JarvisUi.dp(context, JarvisUi.Space.STEP))
                    .apply { rightMargin = gap }
            )
            addView(tag, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { rightMargin = gap })
            addView(title, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = gap })
            addView(detail, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
            tag.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            title.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            detail.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            isFocusable = true
        }

        fun bind(row: ActivityRows.Row?) {
            if (row == null) {
                dot.set(StateDot.Tone.REST)
                return
            }
            tag.text = row.kind.name
            title.text = row.title
            detail.text = row.detail
            detail.visibility = if (row.detail.isEmpty()) GONE else VISIBLE
            dot.set(
                when (row.state) {
                    ActivityRows.State.LIVE -> StateDot.Tone.LIVE
                    ActivityRows.State.DONE -> StateDot.Tone.REST
                    ActivityRows.State.FAILED -> StateDot.Tone.FAILED
                }
            )
            // The row's one line to read is brighter while it is happening and
            // for a moment, as on the console; the rest is the text colour.
            title.setTextColor(
                if (row.state == ActivityRows.State.LIVE || row.kind == ActivityRows.Kind.MOMENT) {
                    JarvisTokens.Color.TEXT_BRIGHT
                } else {
                    JarvisUi.TEXT
                }
            )
            val state = when (row.state) {
                ActivityRows.State.LIVE -> "live"
                ActivityRows.State.DONE -> "done"
                ActivityRows.State.FAILED -> "failed"
            }
            JarvisUi.describe(this, "$state: ${row.kind.name.lowercase()} ${row.title} ${row.detail}")
        }
    }

    companion object {
        /** The console shows a dozen; the phone's strip has room for half under the reactor. */
        const val SHOWN = 6

        /** The panel's head, as the console names it. */
        const val HEAD = "ACTIVITY"

        /** `font-variant-numeric: tabular-nums` on the datum, so "84 ms" and "1240 ms" line up. */
        private const val TABULAR = "tnum"
    }
}
