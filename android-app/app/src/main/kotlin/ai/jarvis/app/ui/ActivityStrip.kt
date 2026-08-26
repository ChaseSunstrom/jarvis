package ai.jarvis.app.ui

import ai.jarvis.app.assist.ActivityRows
import android.content.Context
import android.graphics.Typeface
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The living activity under the reactor (M61): the same rows the console's
 * strip draws, painted the phone's way — hairline rows, the kind as a small
 * uppercase tag, the title in body type, the detail in mono. Nothing is drawn
 * unless there is a row; the arithmetic is [ActivityRows], pinned by
 * `android-app/tools/activity_mirror_test.py`. This class only paints.
 */
class ActivityStrip(context: Context) : LinearLayout(context) {

    private val rowViews = ArrayList<RowView>()

    init {
        orientation = VERTICAL
        visibility = GONE
        JarvisUi.describe(this, "what Jarvis is doing")
    }

    fun render(rows: ActivityRows) {
        val shown = rows.rows.take(SHOWN)
        visibility = if (shown.isEmpty()) GONE else VISIBLE
        while (rowViews.size < shown.size) {
            val view = RowView(context)
            rowViews.add(view)
            addView(view)
        }
        rowViews.forEachIndexed { index, view ->
            val row = shown.getOrNull(index)
            view.visibility = if (row == null) GONE else VISIBLE
            view.bind(row)
        }
    }

    private class RowView(context: Context) : LinearLayout(context) {
        private val tag = TextView(context)
        private val title = TextView(context)
        private val detail = TextView(context)

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.MICRO), 0, JarvisUi.dp(context, JarvisUi.Space.MICRO))
            tag.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            tag.setTypeface(Typeface.MONOSPACE, Typeface.BOLD)
            tag.minWidth = JarvisUi.dp(context, TAG_WIDTH_DP)
            title.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            title.setTextColor(JarvisUi.TEXT)
            title.maxLines = 1
            title.ellipsize = TextUtils.TruncateAt.END
            detail.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
            detail.typeface = Typeface.MONOSPACE
            detail.setTextColor(JarvisUi.FAINT)
            detail.maxLines = 1
            detail.ellipsize = TextUtils.TruncateAt.END
            addView(tag, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
            addView(title, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            addView(detail, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
            tag.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            title.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            detail.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            isFocusable = true
        }

        fun bind(row: ActivityRows.Row?) {
            if (row == null) return
            tag.text = row.kind.name
            title.text = row.title
            detail.text = row.detail
            tag.setTextColor(
                when (row.state) {
                    ActivityRows.State.LIVE -> JarvisUi.ACCENT
                    ActivityRows.State.DONE -> JarvisUi.DIM
                    ActivityRows.State.FAILED -> JarvisUi.DENY
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
        const val TAG_WIDTH_DP = 64
    }
}
