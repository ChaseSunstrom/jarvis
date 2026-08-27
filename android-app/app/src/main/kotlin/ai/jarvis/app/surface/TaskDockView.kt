package ai.jarvis.app.surface

import ai.jarvis.app.tasks.TaskBoard
import ai.jarvis.app.ui.JarvisUi
import android.content.Context
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The task dock under the instrument (M76 on the console, M103 here): the
 * board's visible tasks, each a title, a status readout and a thin bar —
 * determinate when the job reports a fraction, a faint full track when it
 * cannot know, nothing when it is over. The rows are [TaskBoard]'s, the same
 * ones the overlay chip and the notifications read; this only draws them.
 */
class TaskDockView(context: Context) : LinearLayout(context) {
    private val label: TextView
    private val list: LinearLayout

    init {
        orientation = VERTICAL
        visibility = GONE
        contentDescription = "What Jarvis is working on"
        label = JarvisUi.labelText(context, "WORKING ON", JarvisUi.DIM, JarvisUi.TRACK_CHROME)
        addView(label, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
            bottomMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
        })
        list = LinearLayout(context).apply { orientation = VERTICAL }
        addView(list, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }

    fun render(rows: List<TaskBoard.Row>) {
        list.removeAllViews()
        for (row in rows) {
            val item = LinearLayout(context).apply {
                orientation = VERTICAL
                tag = "task-dock-${row.id}"
                contentDescription = "${row.title}: ${row.status.name.lowercase()}"
            }
            val head = LinearLayout(context).apply {
                orientation = HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val title = JarvisUi.readout(context, row.title, JarvisUi.TEXT).apply {
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
            }
            head.addView(title, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            val status = JarvisUi.readout(
                context,
                when {
                    row.error.isNotEmpty() -> "FAILED"
                    row.finished -> row.status.name
                    row.fraction != null -> "${(row.fraction * 100).toInt()} %"
                    else -> row.status.name
                },
                if (row.error.isNotEmpty()) JarvisUi.DIM else JarvisUi.ACCENT,
            )
            head.addView(status, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
            item.addView(head, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
            if (row.bar != TaskBoard.Bar.NONE) {
                val track = FrameLayout(context).apply {
                    background = GradientDrawable().apply { setColor(JarvisUi.FAINT); cornerRadius = 2f }
                }
                val fill = View(context).apply {
                    background = GradientDrawable().apply { setColor(JarvisUi.ACCENT); cornerRadius = 2f }
                    alpha = if (row.bar == TaskBoard.Bar.INDETERMINATE) 0.35f else 1f
                }
                val weight = if (row.bar == TaskBoard.Bar.DETERMINATE) (row.fraction ?: 0.0).coerceIn(0.0, 1.0).toFloat() else 1f
                val bar = LinearLayout(context).apply { orientation = HORIZONTAL }
                bar.addView(fill, LayoutParams(0, JarvisUi.dp(context, 2), weight))
                bar.addView(View(context), LayoutParams(0, JarvisUi.dp(context, 2), 1f - weight))
                track.addView(bar, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, JarvisUi.dp(context, 2)))
                item.addView(track, LayoutParams(LayoutParams.MATCH_PARENT, JarvisUi.dp(context, 2)).apply {
                    topMargin = JarvisUi.dp(context, 3)
                })
            }
            list.addView(item, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
                topMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
            })
        }
        visibility = if (rows.isEmpty()) GONE else VISIBLE
    }
}
