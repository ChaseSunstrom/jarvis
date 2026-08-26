package ai.jarvis.app.ui

import android.app.Activity
import android.content.Context
import android.graphics.Color
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
import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * Shared look-and-feel for every Jarvis surface, on Reactor II: near-black
 * ground, flat hairline panels, one cyan spent on the one thing that is live,
 * the body face for words and mono only for data. Built programmatically on
 * purpose — the assist popup and the consent prompt must draw their first
 * frame without inflating a layout tree, and keeping every screen on the same
 * helpers is what stops the app from drifting into four different visual
 * languages.
 *
 * What is NOT here any more, and why (M51): the filled cyan pill, the ghost
 * outline with the rounded corners, and the corner brackets. They were the
 * previous direction — the console's own pages have already lost them — and a
 * phone that keeps them is the "kind of similar but not really" report in a
 * different shape. A control is [button] (a hairline, quiet) or [primary]
 * (filled, exactly one per screen); a surface is [panel] (flat, a hairline);
 * a frame is the screen's own edge.
 */
object JarvisUi {

    // Every one of these is a `--jv-*` token from `design/tokens.json`, reached
    // through the generated `JarvisTokens`; `design_token_test.py` checks the
    // aliases against the source.
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
    const val ACCENT = JarvisTokens.Color.ACCENT // --jv-accent
    /**
     * Body text. `--jv-text`.
     *
     * Added for the assist overlay, which floats over whatever the user was
     * looking at: DIM is the palette's quiet colour and reaches WCAG AA there
     * only if the scrim behind it is nearly opaque, which is the dark slab this
     * surface has already had removed twice. A brighter line needs less ground.
     */
    const val TEXT = JarvisTokens.Color.TEXT // --jv-text
    /** `--jv-text-dim`, at the 80% alpha the phone has always drawn it with. */
    const val DIM = JarvisTokens.Color.TEXT_DIM_80 // --jv-text-dim
    const val BG = JarvisTokens.Color.BG // --jv-bg
    const val SURFACE = JarvisTokens.Color.PANEL_SOLID // --jv-panel-solid
    const val FAINT = JarvisTokens.Color.TEXT_FAINT // --jv-text-faint
    const val APPROVE = JarvisTokens.Color.OK // --jv-ok
    const val DENY = JarvisTokens.Color.DANGER // --jv-danger

    /**
     * Wants attention, but nothing is broken.
     *
     * DENY reads as "this failed"; a permission you have simply not granted has
     * not failed. Matches `--jv-warn` in the console, which marks the same idea
     * there — held, not wrong.
     */
    const val GOLD = JarvisTokens.Color.WARN // --jv-warn

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
        const val LABEL = JarvisTokens.Type.LABEL

        /** Explanatory body copy, quiet buttons, status lines. */
        const val HINT = JarvisTokens.Type.HINT

        /** Verbatim machine text, checklist glyphs. */
        const val MONO = JarvisTokens.Type.MONO

        /** Ordinary interface text: switch rows, checklist titles, tags. */
        const val BODY = JarvisTokens.Type.BODY

        /** Text the user typed or is about to, plus consent buttons. */
        const val FIELD = JarvisTokens.Type.FIELD

        /** What Jarvis said. The largest thing on a conversation surface. */
        const val RESPONSE = JarvisTokens.Type.RESPONSE

        /** The JARVIS wordmark and screen titles. */
        const val TITLE = JarvisTokens.Type.TITLE
    }

    /**
     * The spacing scale, in dp, for use with [dp].
     *
     * Same argument as [Type]: `dp(ctx, Space.ROW)`, `dp(ctx, Space.GAP)`, `dp(ctx, Size.CHIP)`,
     * `dp(ctx, Space.SECTION)`, `dp(ctx, Space.SCREEN)` and `dp(ctx, Space.WIDE)` all appeared across the
     * screens with nothing to say which was which. These are the steps that were
     * actually in use, named — not a new rhythm imposed on a working layout,
     * which is why the numbers are unchanged.
     */
    object Space {
        /** A hairline stroke. */
        const val HAIRLINE = JarvisTokens.Space.HAIRLINE

        /** Between a line of text and its own line spacing. */
        const val TIGHT = JarvisTokens.Space.TIGHT

        /** Line spacing, and the inset on a small radius. */
        const val MICRO = JarvisTokens.Space.MICRO

        /** Between a label and the thing it labels. */
        const val SNUG = JarvisTokens.Space.SNUG

        /** Between a row and the next thing that is not part of it. */
        const val STEP = JarvisTokens.Space.STEP

        /** Inside a row: a glyph and its text. */
        const val ROW = JarvisTokens.Space.ROW

        /** Between two controls. */
        const val GAP = JarvisTokens.Space.GAP

        /** Between one section and the next. */
        const val SECTION = JarvisTokens.Space.SECTION

        /** A screen's own margin. */
        const val SCREEN = JarvisTokens.Space.SCREEN

        /** A screen's margin where the content is a single centred column. */
        const val WIDE = JarvisTokens.Space.WIDE
    }

    /**
     * How big a THING is, as distinct from the gap beside it.
     *
     * A size snapped to the spacing scale is how a 34 dp consent button — wide
     * on purpose, because it is the one nobody may press by accident — quietly
     * becomes a 32 dp one. Named in `design/tokens.json` like everything else
     * here, and generated into [JarvisTokens].
     */
    object Size {
        /** A spacer between two buttons, and the smallest tap ornament. */
        const val CHIP = JarvisTokens.Size.CHIP

        /** A sheet's own padding, inset from the screen's. */
        const val INSET = JarvisTokens.Size.INSET

        /** A dialog's side gutter. */
        const val GUTTER = JarvisTokens.Size.GUTTER

        /** The consent button's corner radius (kept for callers; buttons are `Radius.MD` now). */
        const val EDGE = JarvisTokens.Size.EDGE

        /** The margin a sheet keeps from the screen edge. */
        const val SHEET = JarvisTokens.Size.SHEET

        /** The side padding of a consent button. */
        const val WIDE_BUTTON = JarvisTokens.Size.WIDE_BUTTON

        /** How far below the top edge a floating panel sits. */
        const val DROP = JarvisTokens.Size.DROP

        /** The widest a floating panel gets, whatever the screen. */
        const val PANEL_MAX = JarvisTokens.Size.PANEL_MAX

        /** The shortest a sheet gets before it scrolls. */
        const val SHEET_MIN = JarvisTokens.Size.SHEET_MIN
    }

    fun dp(context: Context, v: Int): Int =
        (v * context.resources.displayMetrics.density).toInt()

    // --- the faces ------------------------------------------------------------
    //
    // Reactor II sets interface text in a body face and data in mono. The phone
    // bundles no font, so the body face is the platform sans in the weights the
    // web uses: regular for words, medium for labels and controls, light for
    // the one line to read first (the reply, a title).

    /** Words: interface text and prose. */
    val BODY_FACE: Typeface = Typeface.SANS_SERIF

    /** Chrome labels and controls: uppercase, tracked, a shade heavier. */
    val LABEL_FACE: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)

    /** The one line to read first: the reply, a screen title. */
    val DISPLAY_FACE: Typeface = Typeface.create("sans-serif-light", Typeface.NORMAL)

    /** Data: ids, timings, parameters, log lines. Never prose. */
    val MONO_FACE: Typeface = Typeface.MONOSPACE

    /** The tracking on an uppercase label — the web's `--jv-track-chrome`. */
    const val TRACK_CHROME = 0.16f

    /** The tracking on a wider label — the web's `--jv-track-wide`. */
    const val TRACK_WIDE = 0.24f

    /** A hair of tracking on running text set in the display face. */
    const val TRACK_SNUG = 0.04f

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
        typeface = BODY_FACE
        liveRegion(this)
    }

    /**
     * What Jarvis said. Also a live region, for the same reason. The display
     * face, light and bright: the one line on the screen to read first.
     */
    fun responseView(context: Context): TextView = TextView(context).apply {
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.RESPONSE)
        gravity = Gravity.CENTER
        typeface = DISPLAY_FACE
        letterSpacing = TRACK_SNUG
        setPadding(0, dp(context, Space.ROW), 0, 0)
        liveRegion(this)
    }

    // --- text ---------------------------------------------------------------

    /** The JARVIS wordmark / screen title: the display face, bright, no glow. */
    fun title(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.TITLE)
        letterSpacing = TRACK_SNUG
        typeface = DISPLAY_FACE
        gravity = Gravity.CENTER
    }

    /** Small all-caps label above a field or a block: tracked, dim, the label face. */
    fun label(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text.uppercase()
        setTextColor(JarvisTokens.Color.TEXT_DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
        letterSpacing = TRACK_WIDE
        typeface = LABEL_FACE
        setPadding(0, dp(context, Space.GAP), 0, dp(context, Space.TIGHT))
    }

    /** Explanatory body copy. */
    fun hint(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(JarvisTokens.Color.TEXT_DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.HINT)
        typeface = BODY_FACE
        setLineSpacing(dp(context, Space.TIGHT).toFloat(), 1f)
        setPadding(0, dp(context, Space.SNUG), 0, 0)
    }

    /**
     * Monospace block for verbatim machine text (params, log lines): the one
     * place mono belongs, inset on the sunken surface the console uses for
     * output.
     */
    fun mono(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(TEXT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.MONO)
        typeface = MONO_FACE
        setTextIsSelectable(true)
        val p = dp(context, Space.GAP)
        setPadding(p, p, p, p)
        background = panel(context, fill = JarvisTokens.Color.SURFACE_SUNKEN, stroke = JarvisTokens.Color.LINE_HAIR)
    }

    // --- containers ---------------------------------------------------------

    /**
     * A flat panel: the panel colour on a hairline, Reactor II's one radius.
     * Nothing translucent and nothing glowing — depth comes from the hairline.
     */
    fun panel(
        context: Context,
        fill: Int = JarvisTokens.Color.PANEL,
        stroke: Int = JarvisTokens.Color.LINE_HAIR,
    ): GradientDrawable =
        GradientDrawable().apply {
            cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
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
     * A held bar: something is waiting on the person, with one action.
     *
     * Used for "Network permission denied", which on GrapheneOS is by far the
     * most common reason nothing works. The warn colour as an INSET rule down
     * the left — a stripe, not a border, so it never moves the layout — over a
     * flat panel, the words in the body face, and the action as a quiet
     * button. Amber rather than red: this is a thing the user can fix in two
     * taps, not a failure. The button is the whole point; a bar that only
     * complains makes the user go hunting through Settings themselves.
     */
    fun banner(
        context: Context,
        text: String,
        actionLabel: String,
        onAction: () -> Unit,
    ): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        background = panel(context)
        addView(
            View(context).apply { setBackgroundColor(GOLD) },
            LinearLayout.LayoutParams(dp(context, Space.MICRO), ViewGroup.LayoutParams.MATCH_PARENT)
        )
        val body = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            val p = dp(context, Space.GAP)
            setPadding(p, p, p, p)
        }
        body.addView(
            TextView(context).apply {
                this.text = HELD_LABEL
                setTextColor(GOLD)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
                letterSpacing = TRACK_WIDE
                typeface = LABEL_FACE
            }
        )
        body.addView(
            TextView(context).apply {
                this.text = text
                setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.BODY)
                typeface = BODY_FACE
                setLineSpacing(dp(context, Space.TIGHT).toFloat(), 1f)
                setPadding(0, dp(context, Space.TIGHT), 0, 0)
            }
        )
        body.addView(
            button(context, actionLabel, onAction),
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(context, Space.ROW) }
        )
        addView(body, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
    }

    /**
     * One line of a checklist: a state glyph, a label, and an explanation, as
     * a hairline row. The glyph is text rather than an icon so it survives any
     * font and any accessibility scale, and so it copies into a bug report
     * as-is.
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
        val p = dp(context, Space.ROW)
        setPadding(p, p, p, p)
        // A missing essential is the one row that is not quiet: the warn
        // colour on its hairline says "held" where every other row says
        // nothing.
        background = panel(
            context,
            stroke = if (!satisfied && essential) JarvisTokens.Color.WARN_40 else JarvisTokens.Color.LINE_HAIR
        )

        val tone = when {
            satisfied -> APPROVE
            essential -> JarvisTokens.Color.AMBER
            else -> FAINT
        }
        addView(
            TextView(context).apply {
                text = if (satisfied) "[ok]" else if (essential) "[--]" else "[  ]"
                setTextColor(tone)
                typeface = MONO_FACE
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
                setTextColor(if (satisfied) JarvisTokens.Color.TEXT_BRIGHT else tone)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.BODY)
                typeface = LABEL_FACE
            }
        )
        col.addView(
            TextView(context).apply {
                this.text = why
                setTextColor(JarvisTokens.Color.TEXT_DIM)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.HINT)
                typeface = BODY_FACE
                setLineSpacing(dp(context, Space.MICRO).toFloat(), 1f)
                setPadding(0, dp(context, Space.TIGHT), 0, 0)
            }
        )
        addView(col, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        if (onClick != null) {
            addView(
                TextView(context).apply {
                    text = if (satisfied) "" else "OPEN"
                    setTextColor(JarvisTokens.Color.TEXT_DIM)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
                    letterSpacing = TRACK_CHROME
                    typeface = LABEL_FACE
                    setPadding(dp(context, Space.STEP), 0, 0, 0)
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
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setHintTextColor(FAINT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
        typeface = BODY_FACE
        inputType = if (secret) {
            // VISIBLE_PASSWORD: a token is pasted and eyeballed, not typed from
            // memory, and hiding it just invites paste errors nobody can debug.
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
        } else {
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        setSingleLine(!secret)
        background = panel(context, fill = JarvisTokens.Color.FIELD, stroke = JarvisTokens.Color.LINE_SOFT)
        val p = dp(context, Space.GAP)
        setPadding(p, p, p, p)
        // An EditText with a hint is announced by the hint, and every field on
        // every screen has one — so the label above it would be read twice.
        // Stated here so a later screen does not add a second one.
    }

    /**
     * The quiet control — Reactor II's `.btn`: an uppercase label on a hairline,
     * dim until pressed, the one radius. Most buttons on a screen are this.
     */
    fun button(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(JarvisTokens.Color.TEXT_DIM)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
            letterSpacing = TRACK_WIDE
            typeface = LABEL_FACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(Color.TRANSPARENT)
                setStroke(dp(context, Space.HAIRLINE), JarvisTokens.Color.LINE)
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
     * The one filled control on a screen: the thing the screen is for. The
     * accent as a fill with the ink on it, and nothing else on the screen may
     * be filled — that is what makes it readable as the primary action.
     */
    fun primary(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(JarvisTokens.Color.ACCENT_INK)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
            letterSpacing = TRACK_WIDE
            typeface = LABEL_FACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(ACCENT)
                setStroke(dp(context, Space.HAIRLINE), ACCENT)
            }
            setPadding(
                dp(context, Size.WIDE_BUTTON),
                dp(context, Space.GAP),
                dp(context, Size.WIDE_BUTTON),
                dp(context, Space.GAP),
            )
            setOnClickListener { onClick() }
        }

    /**
     * A tab in a strip: the label alone, tracked, with no box of its own. The
     * strip draws the one underline under whichever tab is current
     * ([ConsoleFrame]), so a tab is not a button that happens to sit in a row.
     */
    fun tab(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(JarvisTokens.Color.TEXT_DIM)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
            letterSpacing = TRACK_CHROME
            typeface = LABEL_FACE
            background = null
            minWidth = 0
            minimumWidth = 0
            setPadding(
                dp(context, Space.GAP),
                dp(context, Space.GAP),
                dp(context, Space.GAP),
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
    ): Button = button(context, labels.getOrNull(selected) ?: "—", {}).apply {
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

    /**
     * Consent buttons. [tone] is [APPROVE] or [DENY].
     *
     * The yes half is FILLED in its tone with the ink on it — the one primary
     * on a screen whose only purpose is this answer — and the no half is a
     * quiet hairline in the danger text colour, so saying yes and saying no
     * are not two equally loud controls and a thumb cannot mistake one for the
     * other by shape.
     */
    fun consentButton(context: Context, label: String, tone: Int, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            val yes = tone == APPROVE
            setTextColor(if (yes) JarvisTokens.Color.ACCENT_INK else JarvisTokens.Color.DANGER_TEXT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
            letterSpacing = TRACK_CHROME
            typeface = LABEL_FACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(if (yes) tone else Color.TRANSPARENT)
                setStroke(dp(context, Space.HAIRLINE), if (yes) tone else atAlpha(tone, FILL_ALPHA_STRONG))
            }
            setPadding(dp(context, Space.SCREEN), dp(context, Size.INSET), dp(context, Space.SCREEN), dp(context, Size.INSET))
            // Refuse taps that arrive through another window sitting on top of
            // this one. A consent screen is exactly what a tapjacking overlay
            // wants to sit on.
            filterTouchesWhenObscured = true
            setOnClickListener { onClick() }
        }

    /** A colour at an opacity, without writing either of them down twice. */
    private fun atAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or (alpha.coerceIn(0, 255) shl 24)

    /** The stroke on a quiet danger control: enough to read the tone, not to shout. */
    private const val FILL_ALPHA_STRONG = 0x73

    /** What a held bar says above its message. */
    private const val HELD_LABEL = "HELD · ASKS BEFORE IT GOES ON"
}
