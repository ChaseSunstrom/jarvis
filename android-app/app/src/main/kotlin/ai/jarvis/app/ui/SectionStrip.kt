package ai.jarvis.app.ui

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The console's `SectionStrip.svelte`: a segmented control — a hairline box,
 * one segment per section, the current one raised on `--jv-surface-2` — for
 * moving about inside one destination. It is deliberately not the top bar's
 * tab strip: they look different because they ARE different — one chooses a
 * place, the other a view within it.
 *
 * On the phone it is an in-page strip: tapping a segment scrolls the screen's
 * one column to that section, and the current segment follows the column as
 * it scrolls. Everything stays in the one scrollable column on purpose — the
 * instrumented suite reaches every control on the settings screen by
 * scrolling that column, and a strip that swapped the content under it would
 * hide the literals those tests look for behind a tap they do not know to
 * make.
 */
class SectionStrip(context: Context, labels: List<String>, private val onPick: (Int) -> Unit) :
    HorizontalScrollView(context) {

    private val segments = ArrayList<TextView>()
    private var current = -1

    init {
        isHorizontalScrollBarEnabled = false
        isHorizontalFadingEdgeEnabled = true
        setFadingEdgeLength(JarvisUi.dp(context, JarvisUi.Space.SECTION))
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            background = GradientDrawable().apply {
                cornerRadius = JarvisUi.dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(Color.TRANSPARENT)
                setStroke(JarvisUi.dp(context, JarvisUi.Space.HAIRLINE), JarvisTokens.Color.LINE_HAIR)
            }
            // Segments are separated by a hairline, as `a { border-right }`
            // separates the console's; the box's own edge does the last one.
            showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
            dividerDrawable = GradientDrawable().apply {
                setSize(JarvisUi.dp(context, JarvisUi.Space.HAIRLINE), 0)
                setColor(JarvisTokens.Color.LINE_HAIR)
            }
            // The box's corners must clip the raised segment, or a square
            // surface-2 block pokes out of a rounded hairline.
            clipToOutline = true
        }
        for ((index, label) in labels.withIndex()) {
            val segment = JarvisUi.labelText(context, label, JarvisTokens.Color.TEXT_FAINT, JarvisUi.TRACK_CHROME).apply {
                setPadding(
                    JarvisUi.dp(context, JarvisUi.Space.SECTION),
                    JarvisUi.dp(context, JarvisUi.Space.ROW),
                    JarvisUi.dp(context, JarvisUi.Space.SECTION),
                    JarvisUi.dp(context, JarvisUi.Space.ROW),
                )
                maxLines = 1
                isClickable = true
                isFocusable = true
                setOnClickListener {
                    select(index)
                    onPick(index)
                }
            }
            segments += segment
            row.addView(segment, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        addView(row, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        JarvisUi.describe(this, "Sections")
        if (segments.isNotEmpty()) select(0)
    }

    /** The segment for the section on screen. Safe to call with what is already current. */
    fun select(index: Int) {
        if (index == current || index !in segments.indices) return
        current = index
        for ((i, segment) in segments.withIndex()) {
            val on = i == index
            segment.setTextColor(if (on) JarvisTokens.Color.TEXT_BRIGHT else JarvisTokens.Color.TEXT_FAINT)
            segment.setBackgroundColor(if (on) JarvisTokens.Color.SURFACE_2 else Color.TRANSPARENT)
            segment.isSelected = on
        }
        // Keep the current segment in view: a strip wider than the screen
        // that says "you are in UPDATES" off its right edge says nothing.
        val segment = segments[index]
        post {
            val left = segment.left - JarvisUi.dp(context, JarvisUi.Space.SECTION)
            val right = segment.right + JarvisUi.dp(context, JarvisUi.Space.SECTION)
            when {
                left < scrollX -> smoothScrollTo(left, 0)
                right > scrollX + width -> smoothScrollTo(right - width, 0)
            }
        }
    }

    /**
     * The fade at an overflowing edge is the ground colour, not a scrollbar.
     * Returning the ground lets the platform draw the fade as a solid ramp
     * rather than compositing a layer per frame.
     */
    override fun getSolidColor(): Int = JarvisTokens.Color.BG

    /** The strip's own tag, for a test to find it by. */
    companion object {
        const val TAG = "section-strip"

        /**
         * Which section is on screen, for a column at [scrollY]: the last
         * anchor whose top is at or above the fold, with a step of slack so a
         * section whose heading has just scrolled under the strip counts as
         * the one being read. Pure, and separate from the view, so it can be
         * checked without a layout pass.
         */
        fun sectionAt(anchorTops: List<Int>, scrollY: Int, slack: Int): Int {
            var at = 0
            for ((i, top) in anchorTops.withIndex()) {
                if (top <= scrollY + slack) at = i
            }
            return at
        }
    }
}
