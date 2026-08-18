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

    // Every one of these is a `--jv-*` token from
    // `jarvis-web/src/lib/tokens.ts`, and `design_token_test.py` checks them
    // against it.
    //
    // They were a second palette that happened to look similar: three of the
    // eight matched a web token and five were near misses nobody could see
    // were misses, because the two are never on screen together. The phone's
    // settings sit inside the console's frame now, under the console's own nav
    // — *"have the settings for the android app be in that same web view look?
    // so we can dedup the things"* — and one nav over two palettes is exactly
    // the drift that produces.
    //
    // One of the five was not merely different. FAINT was #5A7A86, which is
    // 4.38:1 on this ground — under WCAG AA — and it is the colour every hint
    // on every screen is drawn in. `--jv-text-faint` is 9.01:1.
    const val ACCENT = 0xFF3FD8FF.toInt() // --jv-accent
    /**
     * Body text. `--jv-text`.
     *
     * Added for the assist overlay, which floats over whatever the user was
     * looking at: DIM is the palette's quiet colour and reaches WCAG AA there
     * only if the scrim behind it is nearly opaque, which is the dark slab this
     * surface has already had removed twice. A brighter line needs less ground.
     */
    const val TEXT = 0xFFD7EDF5.toInt()
    /** `--jv-text-dim`, at the 80% alpha the phone has always drawn it with. */
    const val DIM = 0xCC9FC0CC.toInt()
    const val BG = 0xFF04070C.toInt() // --jv-bg
    const val SURFACE = 0xFF06121A.toInt() // --jv-panel-solid
    const val FAINT = 0xFF8FB3C0.toInt() // --jv-text-faint
    const val APPROVE = 0xFF6FF2C0.toInt() // --jv-ok
    const val DENY = 0xFFFF6B5C.toInt() // --jv-danger

    /**
     * Wants attention, but nothing is broken.
     *
     * DENY reads as "this failed"; a permission you have simply not granted has
     * not failed. Matches `--jv-warn` in the console, which marks the same idea
     * there — held, not wrong.
     */
    const val GOLD = 0xFFFFB347.toInt()

    /**
     * The type scale.
     *
     * The colours in this file have been tokenised — and pinned against
     * `jarvis-web/src/lib/tokens.ts` by `tools/design_token_test.py` — since the
     * phone and the console were found to be running two palettes that merely
     * looked alike. Type had exactly the same problem and no such treatment:
     * sizes were inline SP literals (11, 12, 13, 14, 15, 20, 21, 22) scattered
     * across every activity, so "the same size as a hint" was a number somebody
     * remembered rather than a name, and `CompanionAskActivity`'s question was
     * 21sp against `JarvisUi.responseView`'s 20sp for no reason anybody could
     * state.
     *
     * Seven steps, named for the job rather than for the number, so a screen
     * asks for "a label" and gets whatever a label is.
     */
    object Type {
        /** All-caps section labels and chrome captions. */
        const val LABEL = 11f

        /** Explanatory body copy, ghost buttons, status lines. */
        const val HINT = 12f

        /** Verbatim machine text, checklist glyphs. */
        const val MONO = 13f

        /** Ordinary interface text: switch rows, checklist titles, pills. */
        const val BODY = 14f

        /** Text the user typed or is about to, plus consent buttons. */
        const val FIELD = 15f

        /** What Jarvis said. The largest thing on a conversation surface. */
        const val RESPONSE = 20f

        /** The JARVIS wordmark and screen titles. */
        const val TITLE = 22f
    }

    /**
     * The spacing scale, in dp, for use with [dp].
     *
     * Same argument as [Type]: `dp(ctx, 10)`, `dp(ctx, 12)`, `dp(ctx, 14)`,
     * `dp(ctx, 16)`, `dp(ctx, 20)` and `dp(ctx, 24)` all appeared across the
     * screens with nothing to say which was which. These are the steps that were
     * actually in use, named — not a new rhythm imposed on a working layout,
     * which is why the numbers are unchanged.
     */
    object Space {
        /** A hairline stroke. */
        const val HAIRLINE = 1

        /** Between a line of text and its own line spacing. */
        const val TIGHT = 4

        /** Between a label and the thing it labels. */
        const val SNUG = 6

        /** Inside a row: a glyph and its text. */
        const val ROW = 10

        /** Between two controls. */
        const val GAP = 12

        /** Between one section and the next. */
        const val SECTION = 16

        /** A screen's own margin. */
        const val SCREEN = 20

        /** A screen's margin where the content is a single centred column. */
        const val WIDE = 24
    }

    fun dp(context: Context, v: Int): Int =
        (v * context.resources.displayMetrics.density).toInt()

    // --- accessibility --------------------------------------------------------
    //
    // There were NO `contentDescription`, `announceForAccessibility` or
    // `accessibilityLiveRegion` calls anywhere in `app/src/main/kotlin` outside
    // `automation/accessibility/`, which is the module that READS OTHER APPS'
    // screens for the automation engine. Jarvis could drive another app's UI for
    // a blind user and could not describe its own: the orb was an unlabelled
    // custom View, every state caption was silent, tool-activity rows were
    // silent, and a pipeline moving from listening to thinking to speaking
    // announced nothing at all — on a voice assistant, whose users include
    // people who cannot see the screen it is drawing.
    //
    // These three helpers are deliberately tiny and deliberately here rather
    // than per screen: `tools/accessibility_labels_test.py` requires every
    // surface to use them, and a screen that has to write its own
    // `sendAccessibilityEvent` boilerplate is a screen that will not.

    /**
     * Mark [view] as a region TalkBack re-reads whenever its text changes.
     *
     * `POLITE`, never `ASSERTIVE`: a pipeline state changes several times a
     * turn, and assertive interrupts whatever the user is currently listening to
     * — including Jarvis's own reply. Polite queues behind it, which is the
     * behaviour the words themselves have.
     */
    fun liveRegion(view: View) {
        view.accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
    }

    /**
     * Say [text] out loud now, whether or not anything on screen changed.
     *
     * For the transitions that are not a text change: the orb entering, a turn
     * ending, a question arriving on a surface that was already up. A live
     * region cannot cover those because nothing it contains moved.
     *
     * A no-op when the text is blank, so a caller may pass a value that is
     * sometimes empty without guarding it.
     */
    fun announce(view: View, text: String) {
        if (text.isBlank()) return
        view.announceForAccessibility(text)
    }

    /**
     * Give [view] a spoken label, or take one away.
     *
     * Blank clears it rather than setting an empty description — an empty string
     * is a description, and TalkBack reads a control with one as unlabelled
     * *and* silent, which is worse than the default.
     */
    fun describe(view: View, text: String?) {
        view.contentDescription = text?.takeIf { it.isNotBlank() }
    }

    /**
     * Inset a screen's root so its content is not under the status bar or the
     * navigation bar.
     *
     * ## Why every ordinary screen needs this now
     *
     * Reported as *"the tabs on the settings for the android app are too high
     * up, and I can't click on them"*, which is exactly what it looks like:
     * the nav strip is the first thing in the layout, so it was drawn beneath
     * the status bar, and a tap up there goes to the system rather than to the
     * button under it.
     *
     * The cause is `targetSdk = 35`. **Android 15 enforces edge-to-edge for
     * apps that target it**: the window is laid out behind the system bars
     * whether or not the app asked, and `android:statusBarColor` /
     * `android:navigationBarColor` — which `Theme.JarvisBase` sets, and which
     * used to reserve that space — are deprecated and ignored. Nothing warns.
     * The screens that hide the bars outright ([immersive]) were unaffected and
     * still are, which is why this only ever showed up on Settings, Manage,
     * SYSTEM CHECK, the crash log and the two automation screens.
     *
     * Padding rather than `setDecorFitsSystemWindows(true)`: the latter is
     * deprecated on the same release, and this keeps the window background
     * drawn edge to edge — so the bars still sit over Jarvis's own black rather
     * than over a strip of grey.
     *
     * `displayCutout` is folded in because a punch-hole or notch in landscape
     * eats the left or right edge in exactly the same way.
     */
    fun fitSystemBars(root: android.view.View) {
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(
                    android.view.WindowInsets.Type.systemBars() or
                        android.view.WindowInsets.Type.displayCutout()
                )
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                // minSdk is 29, where `getInsets(int)` does not exist yet.
                @Suppress("DEPRECATION")
                view.setPadding(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom,
                )
            }
            insets
        }
        // A view that is already attached has had its insets dispatched, and
        // the listener above has just missed them.
        if (root.isAttachedToWindow) root.requestApplyInsets()
    }

    /**
     * Edge-to-edge, system bars hidden (swipe to reveal).
     *
     * Every caller runs this from `onCreate`, **before** `setContentView` — so
     * the controller has to be fetched through the decor view and never through
     * `Window.insetsController`. On API 30 `PhoneWindow.getInsetsController()`
     * is a bare `return mDecor.getWindowInsetsController();`, and until a
     * `setContentView` or a `getDecorView()` has installed the decor, `mDecor`
     * is null. The NPE is thrown *inside the getter*, so the `?.` below cannot
     * catch it and the Activity dies before its first frame:
     *
     *     java.lang.RuntimeException: Unable to start activity
     *       ComponentInfo{ai.jarvis.app/ai.jarvis.app.MainActivity}:
     *     java.lang.NullPointerException: Attempt to invoke virtual method
     *       'android.view.WindowInsetsController
     *        com.android.internal.policy.DecorView.getWindowInsetsController()'
     *       on a null object reference
     *       at com.android.internal.policy.PhoneWindow.getInsetsController(…)
     *       at ai.jarvis.app.ui.JarvisUi.immersive(JarvisUi.kt:46)
     *
     * That is a real crash on Android 11, caught by `AppLaunchTest` the first
     * time the instrumented suite ever ran on an emulator.
     *
     * Asking the window for its decor installs it, and a DecorView that is not
     * attached yet hands back a *pending* controller which replays these two
     * calls the moment the window is attached — which is exactly the ordering
     * this call site wants. `android-app/tools/window_insets_test.py` pins it.
     */
    fun immersive(activity: Activity) {
        val w = activity.window
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            w.setDecorFitsSystemWindows(false)
            w.decorView.windowInsetsController?.let { c ->
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

    /**
     * What the user said, as it is transcribed.
     *
     * A live region: the words appear one partial at a time, and a blind user
     * needs to hear that the microphone is hearing them at all — which is the
     * whole job this view does for a sighted one.
     */
    fun transcriptView(context: Context): TextView = TextView(context).apply {
        setTextColor(DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
        gravity = Gravity.CENTER
        typeface = Typeface.MONOSPACE
        liveRegion(this)
    }

    /** What Jarvis said. Also a live region, for the same reason. */
    fun responseView(context: Context): TextView = TextView(context).apply {
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.RESPONSE)
        gravity = Gravity.CENTER
        setPadding(0, dp(context, Space.ROW), 0, 0)
        liveRegion(this)
    }

    // --- text ---------------------------------------------------------------

    /** The JARVIS wordmark / screen title. */
    fun title(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(ACCENT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.TITLE)
        letterSpacing = 0.32f
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        gravity = Gravity.CENTER
    }

    /** Small all-caps label above a field or a block. */
    fun label(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text.uppercase()
        setTextColor(ACCENT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
        letterSpacing = 0.2f
        typeface = Typeface.MONOSPACE
        setPadding(0, dp(context, Space.GAP), 0, dp(context, Space.TIGHT))
    }

    /** Explanatory body copy. */
    fun hint(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(FAINT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.HINT)
        setLineSpacing(dp(context, 3).toFloat(), 1f)
        setPadding(0, dp(context, Space.SNUG), 0, 0)
    }

    /** Monospace block for verbatim machine text (params, log lines). */
    fun mono(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(Color.WHITE)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.MONO)
        typeface = Typeface.MONOSPACE
        setTextIsSelectable(true)
        val p = dp(context, Space.GAP)
        setPadding(p, p, p, p)
        background = panel(context)
    }

    // --- containers ---------------------------------------------------------

    /** Rounded translucent panel with a hairline accent stroke. */
    fun panel(context: Context, fill: Int = SURFACE, stroke: Int = 0x553FD8FF): GradientDrawable =
        GradientDrawable().apply {
            cornerRadius = dp(context, Space.ROW).toFloat()
            setColor(fill)
            setStroke(dp(context, Space.HAIRLINE), stroke)
        }

    /** Vertical column with the standard screen padding. */
    fun column(context: Context, padDp: Int = Space.SCREEN): LinearLayout = LinearLayout(context).apply {
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
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.MONO)
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
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.MONO)
                setPadding(0, 0, dp(context, Space.ROW), 0)
                // "[ok]" and "[--]" are drawings, not words. TalkBack reads
                // them as punctuation soup; the row's own description below
                // says the same thing in English, so this stays silent.
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }
        )

        val col = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        col.addView(
            TextView(context).apply {
                text = if (essential) label else "$label (optional)"
                setTextColor(if (satisfied) Color.WHITE else tone)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.BODY)
                typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            }
        )
        col.addView(
            TextView(context).apply {
                this.text = why
                setTextColor(FAINT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.HINT)
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
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
                    typeface = Typeface.MONOSPACE
                    setPadding(dp(context, 8), 0, 0, 0)
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
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
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
        inputType = if (secret) {
            // VISIBLE_PASSWORD: a token is pasted and eyeballed, not typed from
            // memory, and hiding it just invites paste errors nobody can debug.
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
        } else {
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        setSingleLine(!secret)
        background = panel(context, fill = 0xFF080D13.toInt(), stroke = 0x443FD8FF)
        val p = dp(context, Space.GAP)
        setPadding(p, p, p, p)
        // An EditText with a hint is announced by the hint, and every field on
        // every screen has one — so the label above it would be read twice.
        // Stated here so a later screen does not add a second one.
    }

    /** Primary action: filled accent pill. */
    fun pill(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(ACCENT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.BODY)
            letterSpacing = 0.15f
            typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            background = GradientDrawable().apply {
                cornerRadius = dp(context, 26).toFloat()
                setColor(0x2233D8FF)
                setStroke(dp(context, 1), ACCENT)
            }
            setPadding(
                dp(context, 34),
                dp(context, Space.SECTION),
                dp(context, 34),
                dp(context, Space.SECTION),
            )
            setOnClickListener { onClick() }
        }

    /** Secondary action: outlined, dim. */
    fun ghost(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(DIM)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.HINT)
            letterSpacing = 0.12f
            typeface = Typeface.MONOSPACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, 22).toFloat()
                setColor(Color.TRANSPARENT)
                setStroke(dp(context, 1), 0x5533D8FF)
            }
            setPadding(
                dp(context, Space.SCREEN),
                dp(context, Space.GAP),
                dp(context, Space.SCREEN),
                dp(context, Space.GAP),
            )
            setOnClickListener { onClick() }
        }

    /**
     * A value with a knowable set of options: pick from a list, do not type it.
     *
     * A free-text box for "hour of the day" accepts `25`, `nine`, and the empty
     * string, and every one of those has to be validated, rejected and
     * explained — for a value with exactly twenty-four possibilities. The web
     * console made the same move for the same reason; this is its counterpart.
     *
     * A dialog rather than a `Spinner`: the platform Spinner's dropdown takes
     * its colours from the theme's popup attributes, which this app does not
     * set (it parents off the bare platform theme so the assist popup can draw
     * without inflating AppCompat), so it renders as black text on black.
     *
     * @param labels what the user reads, in the order they are offered.
     * @param selected index into [labels], or -1 for "nothing chosen".
     * @param onPick the chosen index. The caller updates its own state; this
     *   view only reports.
     */
    fun chooser(
        context: Context,
        title: String,
        labels: List<String>,
        selected: Int,
        onPick: (Int) -> Unit,
    ): Button = ghost(context, labels.getOrNull(selected) ?: "—", {}).apply {
        isAllCaps = false
        var current = selected
        setOnClickListener {
            android.app.AlertDialog.Builder(context)
                .setTitle(title)
                .setSingleChoiceItems(labels.toTypedArray(), current) { dialog, which ->
                    current = which
                    text = labels[which]
                    onPick(which)
                    dialog.dismiss()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }
    }

    /** Consent buttons. [tone] is [APPROVE] or [DENY]. */
    fun consentButton(context: Context, label: String, tone: Int, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(tone)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
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
