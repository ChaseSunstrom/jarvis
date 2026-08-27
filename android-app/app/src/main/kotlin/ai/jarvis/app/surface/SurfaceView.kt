package ai.jarvis.app.surface

import ai.jarvis.app.ui.JarvisUi
import android.content.Context
import android.text.TextUtils
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The house's surface on the voice screen (M103): one line per panel, in the
 * phone's vocabulary ([SurfaceWatch.line]), and a way to take a panel down
 * that goes through the same `jarvis/surface/remove` the console uses.
 *
 * Hidden until there is a panel, so a voice screen with nothing up looks
 * exactly as it did (the same rule [ai.jarvis.app.assist.ToolActivityView]
 * follows).
 */
class SurfaceView(context: Context) : LinearLayout(context) {
    /** Called with the panel's id when its × is tapped. Set by the screen. */
    var onDismiss: ((String) -> Unit)? = null

    /** The state of an entity, for the entity line; the screen wires it to its state map. */
    var stateOf: (String) -> String? = { null }

    private val label: TextView
    private val list: LinearLayout

    init {
        orientation = VERTICAL
        visibility = GONE
        contentDescription = "What Jarvis has put up"
        label = JarvisUi.labelText(context, "ON THE SURFACE", JarvisUi.DIM, JarvisUi.TRACK_CHROME)
        addView(label, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
            bottomMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
        })
        list = LinearLayout(context).apply { orientation = VERTICAL }
        addView(list, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }

    fun render(panels: List<SurfaceWatch.Panel>) {
        list.removeAllViews()
        for (panel in panels) {
            val row = LinearLayout(context).apply {
                orientation = HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                tag = "surface-panel-${panel.id}"
                contentDescription = SurfaceWatch.line(panel, stateOf)
            }
            val text = JarvisUi.readout(context, SurfaceWatch.line(panel, stateOf), JarvisUi.TEXT).apply {
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
            }
            row.addView(text, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
            val close = JarvisUi.readout(context, "×", JarvisUi.DIM).apply {
                contentDescription = "Take it down"
                tag = "surface-dismiss-${panel.id}"
                setPadding(JarvisUi.dp(context, JarvisUi.Space.ROW), 0, 0, 0)
                setOnClickListener { onDismiss?.invoke(panel.id) }
            }
            row.addView(close, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
            list.addView(row, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
                topMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG)
            })
        }
        visibility = if (panels.isEmpty()) GONE else VISIBLE
    }
}
