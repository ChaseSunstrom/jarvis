package ai.jarvis.app.ui

import android.app.Activity
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Shared look-and-feel for every Jarvis surface: deep navy ground, arc-reactor
 * cyan, monospace chrome, corner brackets. Built programmatically on purpose —
 * the assist popup and the consent prompt must draw their first frame without
 * inflating a layout tree, and keeping every screen on the same helpers is what
 * stops the app from drifting into four different visual languages.
 */
object JarvisUi {

    const val ACCENT = 0xFF3FD8FF.toInt()
    const val DIM = 0xCC7FD7EA.toInt()
    const val BG = 0xFF04070C.toInt()
    const val SURFACE = 0xFF0A0F16.toInt()
    const val FAINT = 0xFF5A7A86.toInt()
    const val APPROVE = 0xFF35D08A.toInt()
    const val DENY = 0xFFFF5C5C.toInt()

    fun dp(context: Context, v: Int): Int =
        (v * context.resources.displayMetrics.density).toInt()

    /** Edge-to-edge, system bars hidden (swipe to reveal). */
    fun immersive(activity: Activity) {
        val w = activity.window
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            w.setDecorFitsSystemWindows(false)
            w.insetsController?.let { c ->
                c.hide(android.view.WindowInsets.Type.systemBars())
                c.systemBarsBehavior =
                    android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            w.decorView.systemUiVisibility = (
                android.view.View.SYSTEM_UI_FLAG_FULLSCREEN
                    or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
        }
    }

    fun transcriptView(context: Context): TextView = TextView(context).apply {
        setTextColor(DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        gravity = Gravity.CENTER
        typeface = Typeface.MONOSPACE
    }

    fun responseView(context: Context): TextView = TextView(context).apply {
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
        gravity = Gravity.CENTER
        setPadding(0, dp(context, 10), 0, 0)
    }

    // --- text ---------------------------------------------------------------

    /** The JARVIS wordmark / screen title. */
    fun title(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(ACCENT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 22f)
        letterSpacing = 0.32f
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        gravity = Gravity.CENTER
    }

    /** Small all-caps label above a field or a block. */
    fun label(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text.uppercase()
        setTextColor(ACCENT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
        letterSpacing = 0.2f
        typeface = Typeface.MONOSPACE
        setPadding(0, dp(context, 14), 0, dp(context, 4))
    }

    /** Explanatory body copy. */
    fun hint(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(FAINT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
        setLineSpacing(dp(context, 3).toFloat(), 1f)
        setPadding(0, dp(context, 6), 0, 0)
    }

    /** Monospace block for verbatim machine text (params, log lines). */
    fun mono(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        typeface = Typeface.MONOSPACE
        setTextIsSelectable(true)
        setPadding(dp(context, 12), dp(context, 12), dp(context, 12), dp(context, 12))
        background = panel(context)
    }

    // --- containers ---------------------------------------------------------

    /** Rounded translucent panel with a hairline accent stroke. */
    fun panel(context: Context, fill: Int = SURFACE, stroke: Int = 0x553FD8FF): GradientDrawable =
        GradientDrawable().apply {
            cornerRadius = dp(context, 10).toFloat()
            setColor(fill)
            setStroke(dp(context, 1), stroke)
        }

    /** Vertical column with the standard screen padding. */
    fun column(context: Context, padDp: Int = 20): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        val p = dp(context, padDp)
        setPadding(p, p, p, p)
    }

    fun spacer(context: Context, heightDp: Int): View = View(context).apply {
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(context, heightDp)
        )
    }

    /**
     * A warning strip with one action — used for "Network permission denied",
     * which on GrapheneOS is by far the most common reason nothing works.
     *
     * Amber rather than red: this is a thing the user can fix in two taps, not
     * a failure. The button is the whole point; a banner that only complains
     * makes the user go hunting through Settings themselves.
     */
    fun banner(
        context: Context,
        text: String,
        actionLabel: String,
        onAction: () -> Unit,
    ): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = GradientDrawable().apply {
            cornerRadius = dp(context, 10).toFloat()
            setColor(0x22FF9E2C)
            setStroke(dp(context, 1), 0x88FF9E2C.toInt())
        }
        val p = dp(context, 12)
        setPadding(p, p, p, p)
        addView(
            TextView(context).apply {
                this.text = text
                setTextColor(0xFFFFC773.toInt())
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                setLineSpacing(dp(context, 3).toFloat(), 1f)
            }
        )
        addView(
            ghost(context, actionLabel, onAction),
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(context, 10) }
        )
    }

    /**
     * One line of a checklist: a state glyph, a label, and an explanation.
     * The glyph is text rather than an icon so it survives any font and any
     * accessibility scale, and so it copies into a bug report as-is.
     */
    fun checkRow(
        context: Context,
        satisfied: Boolean,
        essential: Boolean,
        label: String,
        why: String,
        onClick: (() -> Unit)?,
    ): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        val p = dp(context, 10)
        setPadding(p, p, p, p)
        background = panel(
            context,
            fill = SURFACE,
            stroke = if (!satisfied && essential) 0x66FF9E2C else 0x333FD8FF
        )

        val tone = when {
            satisfied -> APPROVE
            essential -> 0xFFFF9E2C.toInt()
            else -> FAINT
        }
        addView(
            TextView(context).apply {
                text = if (satisfied) "[ok]" else if (essential) "[--]" else "[  ]"
                setTextColor(tone)
                typeface = Typeface.MONOSPACE
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
                setPadding(0, 0, dp(context, 10), 0)
            }
        )

        val col = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        col.addView(
            TextView(context).apply {
                text = if (essential) label else "$label (optional)"
                setTextColor(if (satisfied) Color.WHITE else tone)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
                typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            }
        )
        col.addView(
            TextView(context).apply {
                this.text = why
                setTextColor(FAINT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
                setLineSpacing(dp(context, 2).toFloat(), 1f)
                setPadding(0, dp(context, 3), 0, 0)
            }
        )
        addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        if (onClick != null) {
            addView(
                TextView(context).apply {
                    text = if (satisfied) "" else "OPEN >"
                    setTextColor(ACCENT)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
                    typeface = Typeface.MONOSPACE
                    setPadding(dp(context, 8), 0, 0, 0)
                }
            )
            setOnClickListener { onClick() }
        }
    }

    // --- controls -----------------------------------------------------------

    fun field(
        context: Context,
        hint: String,
        value: String,
        secret: Boolean = false,
    ): EditText = EditText(context).apply {
        this.hint = hint
        setText(value)
        setTextColor(Color.WHITE)
        setHintTextColor(FAINT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
        inputType = if (secret) {
            // VISIBLE_PASSWORD: a token is pasted and eyeballed, not typed from
            // memory, and hiding it just invites paste errors nobody can debug.
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
        } else {
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        setSingleLine(!secret)
        background = panel(context, fill = 0xFF080D13.toInt(), stroke = 0x443FD8FF)
        setPadding(dp(context, 12), dp(context, 12), dp(context, 12), dp(context, 12))
    }

    /** Primary action: filled accent pill. */
    fun pill(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(ACCENT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            letterSpacing = 0.15f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            background = GradientDrawable().apply {
                cornerRadius = dp(context, 26).toFloat()
                setColor(0x2233D8FF)
                setStroke(dp(context, 1), ACCENT)
            }
            setPadding(dp(context, 34), dp(context, 16), dp(context, 34), dp(context, 16))
            setOnClickListener { onClick() }
        }

    /** Secondary action: outlined, dim. */
    fun ghost(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(DIM)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            letterSpacing = 0.12f
            typeface = Typeface.MONOSPACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, 22).toFloat()
                setColor(Color.TRANSPARENT)
                setStroke(dp(context, 1), 0x5533D8FF)
            }
            setPadding(dp(context, 20), dp(context, 12), dp(context, 20), dp(context, 12))
            setOnClickListener { onClick() }
        }

    /** Consent buttons. [tone] is [APPROVE] or [DENY]. */
    fun consentButton(context: Context, label: String, tone: Int, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(tone)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            letterSpacing = 0.2f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            background = GradientDrawable().apply {
                cornerRadius = dp(context, 12).toFloat()
                setColor((tone and 0x00FFFFFF) or 0x22000000)
                setStroke(dp(context, 1), tone)
            }
            setPadding(dp(context, 20), dp(context, 18), dp(context, 20), dp(context, 18))
            // Refuse taps that arrive through another window sitting on top of
            // this one. A consent screen is exactly what a tapjacking overlay
            // wants to sit on.
            filterTouchesWhenObscured = true
            setOnClickListener { onClick() }
        }

    /**
     * Non-interactive overlay drawing the HUD corner brackets. Add it last in a
     * FrameLayout so the brackets sit above the content; it never consumes
     * touches (it is not clickable, so onTouchEvent returns false).
     */
    class CornerBrackets(
        context: Context,
        tint: Int = JarvisUi.ACCENT,
    ) : View(context) {

        private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = JarvisUi.dp(context, 2).toFloat()
            color = (tint and 0x00FFFFFF) or 0x66000000
        }
        private val margin = JarvisUi.dp(context, 14).toFloat()
        private val len = JarvisUi.dp(context, 24).toFloat()

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val w = width.toFloat()
            val h = height.toFloat()
            val m = margin
            canvas.drawLine(m, m, m + len, m, paint)
            canvas.drawLine(m, m, m, m + len, paint)
            canvas.drawLine(w - m, m, w - m - len, m, paint)
            canvas.drawLine(w - m, m, w - m, m + len, paint)
            canvas.drawLine(m, h - m, m + len, h - m, paint)
            canvas.drawLine(m, h - m, m, h - m - len, paint)
            canvas.drawLine(w - m, h - m, w - m - len, h - m, paint)
            canvas.drawLine(w - m, h - m, w - m, h - m - len, paint)
        }
    }
}
