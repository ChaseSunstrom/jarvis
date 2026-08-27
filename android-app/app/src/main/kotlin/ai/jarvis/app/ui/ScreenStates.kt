package ai.jarvis.app.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The four states every screen owes its user — loading, empty, error,
 * offline — drawn the way the console draws them (`$lib/ui/ScreenState.svelte`
 * and the four components under it), so a phone screen that cannot show its
 * content shows a real moment on the design system and never a blank, a
 * toast, or a centred accent word in bold mono.
 *
 * What each one is, and why it looks the way it does:
 *
 *  * **[loading]** — skeleton rows in the rhythm of the real ones, so the page
 *    does not jump when the data lands and an empty screen never flashes
 *    "nothing here" at somebody who is still connecting. Announced as busy.
 *  * **[empty]** — a dashed hairline box on the panel colour, the `[ ]` mark in
 *    the accent at rest, a bright title that says what would be here and one
 *    sentence on how it gets here. Dashed because the box is a placeholder for
 *    a thing, not a thing.
 *  * **[error]** — what went wrong and what to do. A 2 dp danger rule down the
 *    left of a danger-tinted panel, the title in the danger TEXT colour (the
 *    mark colour is under AA as words), the machine's sentence in mono under
 *    it, and a Retry.
 *  * **[offline]** — the link is down. The warn colour, not danger: a link
 *    that dropped is a thing to fix, not a thing that broke. It says what is
 *    on screen meanwhile and offers Reconnect now.
 *
 * `ManagementActivity` and `CrashLogActivity` were the two screens that had
 * their own: the console's error and offline moments were a centred accent
 * title in bold mono over a hint, and the crash log's empty state was a faint
 * mono sentence. Both are these now.
 */
object ScreenStates {

    /** Skeleton rows, [rows] of them; [label] is what a screen reader is told. */
    fun loading(context: Context, rows: Int = 4, label: String = "Loading"): View =
        LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
            dividerDrawable = GradientDrawable().apply {
                setSize(0, JarvisUi.dp(context, JarvisUi.Space.HAIRLINE))
                setColor(JarvisTokens.Color.LINE_HAIR)
            }
            JarvisUi.describe(this, label)
            // `role="status" aria-busy="true"`: read once, and left alone
            // while it pulses — a live region here would narrate every frame.
            isFocusable = true
            val bars = ArrayList<View>()
            for (i in 0 until rows) {
                val row = LinearLayout(context).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(
                        JarvisUi.dp(context, JarvisUi.Space.SECTION),
                        JarvisUi.dp(context, JarvisUi.Space.STEP),
                        JarvisUi.dp(context, JarvisUi.Space.SECTION),
                        JarvisUi.dp(context, JarvisUi.Space.STEP),
                    )
                }
                val long = bar(context)
                val short = bar(context)
                row.addView(long, LinearLayout.LayoutParams(0, JarvisUi.dp(context, JarvisUi.Space.ROW), SKELETON_WIDTHS[i % SKELETON_WIDTHS.size]))
                row.addView(View(context), LinearLayout.LayoutParams(0, 1, 1f - SKELETON_WIDTHS[i % SKELETON_WIDTHS.size]))
                row.addView(
                    short,
                    LinearLayout.LayoutParams(JarvisUi.dp(context, JarvisUi.Size.SHEET), JarvisUi.dp(context, JarvisUi.Space.ROW))
                        .apply { leftMargin = JarvisUi.dp(context, JarvisUi.Space.ROW) }
                )
                bars += long
                bars += short
                addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            }
            pulse(this, bars)
        }

    /** Nothing here yet: [title] says what is missing, [body] how it arrives. */
    fun empty(context: Context, title: String, body: String = "", action: View? = null): View =
        LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            background = GradientDrawable().apply {
                cornerRadius = JarvisUi.dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(JarvisTokens.Color.PANEL)
                setStroke(
                    JarvisUi.dp(context, JarvisUi.Space.HAIRLINE),
                    JarvisTokens.Color.LINE_SOFT,
                    JarvisUi.dp(context, JarvisUi.Space.TIGHT).toFloat(),
                    JarvisUi.dp(context, JarvisUi.Space.TIGHT).toFloat(),
                )
            }
            setPadding(
                JarvisUi.dp(context, JarvisUi.Space.SECTION),
                JarvisUi.dp(context, JarvisUi.Space.WIDE),
                JarvisUi.dp(context, JarvisUi.Space.SECTION),
                JarvisUi.dp(context, JarvisUi.Space.WIDE),
            )
            addView(
                TextView(context).apply {
                    text = EMPTY_MARK
                    setTextColor(JarvisTokens.Color.ACCENT_DEEP)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.RESPONSE)
                    letterSpacing = JarvisUi.TRACK_WIDE
                    typeface = JarvisUi.MONO_FACE
                    // A drawing, not a word: TalkBack would read it as brackets.
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                }
            )
            addView(
                TextView(context).apply {
                    text = title
                    setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.FIELD)
                    typeface = JarvisUi.BODY_FACE
                    gravity = Gravity.CENTER
                    setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, 0)
                }
            )
            if (body.isNotEmpty()) {
                addView(
                    TextView(context).apply {
                        text = body
                        setTextColor(JarvisTokens.Color.TEXT_DIM)
                        setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.BODY)
                        typeface = JarvisUi.BODY_FACE
                        gravity = Gravity.CENTER
                        setLineSpacing(JarvisUi.dp(context, JarvisUi.Space.TIGHT).toFloat(), 1f)
                        setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, 0)
                    }
                )
            }
            if (action != null) {
                addView(
                    action,
                    LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    ).apply { topMargin = JarvisUi.dp(context, JarvisUi.Space.GAP) }
                )
            }
        }

    /**
     * What went wrong and what to do. [title] in the user's terms, [detail]
     * the machine's words, [onRetry] the one action that might fix it.
     */
    fun error(context: Context, title: String, detail: String = "", onRetry: (() -> Unit)? = null): View =
        LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            background = GradientDrawable().apply {
                cornerRadius = JarvisUi.dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(mix(JarvisTokens.Color.DANGER, JarvisTokens.Color.PANEL, ERROR_TINT))
                setStroke(JarvisUi.dp(context, JarvisUi.Space.HAIRLINE), JarvisUi.atAlpha(JarvisTokens.Color.DANGER, ERROR_EDGE_ALPHA))
            }
            addView(
                View(context).apply { setBackgroundColor(JarvisTokens.Color.DANGER) },
                LinearLayout.LayoutParams(JarvisUi.dp(context, JarvisUi.Space.MICRO), ViewGroup.LayoutParams.MATCH_PARENT)
            )
            val column = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                val p = JarvisUi.dp(context, JarvisUi.Space.SECTION)
                setPadding(p, p, p, p)
            }
            column.addView(
                TextView(context).apply {
                    text = title
                    setTextColor(JarvisTokens.Color.DANGER_TEXT)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.FIELD)
                    typeface = JarvisUi.BODY_FACE
                    // `role="alert"`, in the register the rest of the phone
                    // uses: polite, so it queues behind whatever Jarvis is
                    // saying rather than cutting it off.
                    JarvisUi.liveRegion(this)
                }
            )
            if (detail.isNotEmpty()) {
                column.addView(
                    TextView(context).apply {
                        text = detail
                        setTextColor(JarvisTokens.Color.TEXT_DIM)
                        setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
                        typeface = JarvisUi.MONO_FACE
                        setLineSpacing(JarvisUi.dp(context, JarvisUi.Space.TIGHT).toFloat(), 1f)
                        setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, 0)
                    }
                )
            }
            if (onRetry != null) {
                column.addView(
                    JarvisUi.button(context, RETRY_LABEL, onRetry),
                    LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    ).apply { topMargin = JarvisUi.dp(context, JarvisUi.Space.GAP) }
                )
            }
            addView(column, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }

    /**
     * The link is down. [body] says what is on screen meanwhile; [onReconnect]
     * re-dials. [busy] while it is trying, so the button says so and stops
     * taking taps.
     */
    fun offline(
        context: Context,
        body: String = OFFLINE_BODY,
        onReconnect: (() -> Unit)? = null,
        busy: Boolean = false,
    ): View = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        background = GradientDrawable().apply {
            cornerRadius = JarvisUi.dp(context, JarvisTokens.Radius.MD).toFloat()
            setColor(JarvisTokens.Color.PANEL)
            setStroke(JarvisUi.dp(context, JarvisUi.Space.HAIRLINE), JarvisTokens.Color.LINE)
        }
        addView(
            View(context).apply { setBackgroundColor(JarvisTokens.Color.WARN) },
            LinearLayout.LayoutParams(JarvisUi.dp(context, JarvisUi.Space.MICRO), ViewGroup.LayoutParams.MATCH_PARENT)
        )
        val dot = StateDot(context).apply { set(StateDot.Tone.WARN) }
        addView(
            dot,
            LinearLayout.LayoutParams(JarvisUi.dp(context, JarvisUi.Space.STEP), JarvisUi.dp(context, JarvisUi.Space.STEP))
                .apply { leftMargin = JarvisUi.dp(context, JarvisUi.Space.SECTION) }
        )
        val text = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(
                JarvisUi.dp(context, JarvisUi.Space.ROW),
                JarvisUi.dp(context, JarvisUi.Space.ROW),
                JarvisUi.dp(context, JarvisUi.Space.ROW),
                JarvisUi.dp(context, JarvisUi.Space.ROW),
            )
        }
        text.addView(
            TextView(context).apply {
                this.text = OFFLINE_TITLE
                setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.BODY)
                typeface = JarvisUi.BODY_FACE
                JarvisUi.liveRegion(this)
            }
        )
        text.addView(
            TextView(context).apply {
                this.text = body
                setTextColor(JarvisTokens.Color.TEXT_DIM)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.LABEL)
                typeface = JarvisUi.BODY_FACE
                setLineSpacing(JarvisUi.dp(context, JarvisUi.Space.MICRO).toFloat(), 1f)
                setPadding(0, JarvisUi.dp(context, JarvisUi.Space.TIGHT), 0, 0)
            }
        )
        addView(text, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (onReconnect != null) {
            addView(
                JarvisUi.button(context, if (busy) RECONNECTING_LABEL else RECONNECT_LABEL, onReconnect).apply {
                    isEnabled = !busy
                    if (busy) alpha = BUSY_ALPHA
                },
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { rightMargin = JarvisUi.dp(context, JarvisUi.Space.ROW) }
            )
        }
    }

    // --- pieces -----------------------------------------------------------------

    private fun bar(context: Context): View = View(context).apply {
        background = GradientDrawable().apply {
            cornerRadius = JarvisUi.dp(context, JarvisTokens.Radius.SM).toFloat()
            setColor(JarvisTokens.Color.LINE_SOFT)
        }
    }

    /**
     * The skeleton's breath: every bar between 0.45 and 0.9, on
     * `motion.dur.pulse`, and not at all under reduced motion. Started on
     * attach and stopped on detach, so a skeleton left behind by a screen that
     * finished loading is not still animating in a view nobody can see.
     */
    private fun pulse(host: View, bars: List<View>) {
        var animator: ValueAnimator? = null
        host.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: View) {
                if (JarvisUi.reducedMotion(v.context) || !ValueAnimator.areAnimatorsEnabled()) return
                animator = ValueAnimator.ofFloat(SKELETON_FLOOR, SKELETON_CEILING).apply {
                    duration = JarvisTokens.Motion.Dur.PULSE.toLong()
                    repeatCount = ValueAnimator.INFINITE
                    repeatMode = ValueAnimator.REVERSE
                    interpolator = JarvisUi.EASE_IN_OUT
                    addUpdateListener { a -> for (bar in bars) bar.alpha = a.animatedValue as Float }
                    start()
                }
            }

            override fun onViewDetachedFromWindow(v: View) {
                animator?.cancel()
                animator = null
            }
        })
    }

    /** `color-mix(in srgb, a p%, b)`: [a] at [p] over [b], per channel. */
    private fun mix(a: Int, b: Int, p: Float): Int {
        fun ch(shift: Int) = (((a shr shift) and 0xFF) * p + ((b shr shift) and 0xFF) * (1f - p)).toInt()
        return Color.rgb(ch(16), ch(8), ch(0))
    }

    /** `EmptyState.svelte`'s mark. */
    private const val EMPTY_MARK = "[ ]"

    const val RETRY_LABEL = "RETRY"
    const val OFFLINE_TITLE = "No link to Jarvis"
    const val RECONNECT_LABEL = "RECONNECT NOW"
    const val RECONNECTING_LABEL = "RECONNECTING…"

    /** `OfflineState.svelte`'s default body. */
    const val OFFLINE_BODY =
        "The link to the backend closed, so nothing below is live any more. " +
            "It will not come back on its own."

    /** `ErrorState.svelte`: the panel is the danger colour at 7 % over the panel colour, its edge at 35 %. */
    private const val ERROR_TINT = 0.07f
    private const val ERROR_EDGE_ALPHA = 0x59

    /** `SkeletonRows.svelte`: the long bar's share of the row, per row, and the breath's range. */
    private val SKELETON_WIDTHS = floatArrayOf(0.62f, 0.45f, 0.55f, 0.38f, 0.50f)
    private const val SKELETON_FLOOR = 0.45f
    private const val SKELETON_CEILING = 0.9f

    /** `OfflineState.svelte`'s disabled Reconnect: `.now:disabled { opacity: 0.55 }`. */
    private const val BUSY_ALPHA = 0.55f
}
