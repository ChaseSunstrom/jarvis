package ai.jarvis.app.ui

import android.app.Activity
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens
import kotlin.math.min

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
     * Danger as WORDS. `--jv-danger-text`.
     *
     * [DENY] is the mark colour — a dot, a stroke, the fill of the no button's
     * edge — and the console never sets a sentence in it (`CallLine.svelte`
     * draws the failed dot in `--jv-danger` and the reason beside it in
     * `--jv-danger-text`). The phone did: every "it failed" line was in the
     * mark colour, one step short of the text colour that was made for it.
     */
    const val DENY_TEXT = JarvisTokens.Color.DANGER_TEXT // --jv-danger-text

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

    /** A type step in pixels, for a `Paint` that draws its own text. */
    fun sp(context: Context, v: Float): Float =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, v, context.resources.displayMetrics)

    // --- motion ---------------------------------------------------------------

    /** `--jv-ease-out`, for an animator: a tab sliding, a row entering. */
    val EASE_OUT: PathInterpolator
        get() = JarvisTokens.Motion.Ease.OUT.let { PathInterpolator(it[0], it[1], it[2], it[3]) }

    /** `--jv-ease-in-out`, for something that breathes. */
    val EASE_IN_OUT: PathInterpolator
        get() = JarvisTokens.Motion.Ease.IN_OUT.let { PathInterpolator(it[0], it[1], it[2], it[3]) }

    /**
     * The person asked for no motion.
     *
     * Android has no `prefers-reduced-motion`. What it has is two scales in
     * developer options that a battery saver also drives to zero: the animator
     * duration scale, which kills every `ValueAnimator` on its first frame, and
     * the transition scale, which the boot sequence already reads as "do not
     * play this". Either at zero is the only way the platform lets somebody
     * say it, so either counts. Nothing decorative moves when this is true —
     * the reactor's drift, a live dot's pulse, the task sweep, a row's
     * entrance — while the things that carry information (the level arc,
     * a colour change, a bar's width) still change, without easing.
     *
     * Read at the moment it is needed rather than cached for the process: a
     * user flips the setting and comes back to the app, and a value read at
     * startup would describe the phone they no longer have. Callers that draw
     * sixty times a second read it once per attach, not per frame — this is a
     * content-provider query.
     */
    fun reducedMotion(context: Context): Boolean = try {
        val resolver = context.contentResolver
        Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) <= 0f ||
            Settings.Global.getFloat(resolver, Settings.Global.TRANSITION_ANIMATION_SCALE, 1f) <= 0f
    } catch (t: Throwable) {
        // GrapheneOS is strict about what a third-party app may read out of
        // Settings, and a decoration must never be the thing that throws.
        false
    }

    /**
     * Bring [view] in the way the console's panels arrive: from a step below,
     * fading up over `--jv-dur-enter`, [index] steps of `--jv-stagger-step`
     * after the first, capped at `--jv-stagger-cap`.
     *
     * The stagger tokens were generated into [JarvisTokens.Motion.Stagger]
     * and read by nothing; every list on the phone simply appeared. Starts on
     * attach when the view is not on screen yet — a screen builds its rows
     * before `setContentView` — and does nothing at all when the view is
     * never attached (a golden is drawn off a window), so a screenshot cannot
     * record a row at the alpha it started from. Under reduced motion the row
     * is simply there.
     */
    fun enter(view: View, index: Int = 0) {
        if (reducedMotion(view.context)) return
        val start = {
            view.alpha = 0f
            view.translationY = dp(view.context, Space.STEP).toFloat()
            view.animate()
                .alpha(1f)
                .translationY(0f)
                .setStartDelay(
                    min(index * JarvisTokens.Motion.Stagger.STEP, JarvisTokens.Motion.Stagger.CAP)
                        .toLong()
                )
                .setDuration(JarvisTokens.Motion.Dur.ENTER.toLong())
                .setInterpolator(EASE_OUT)
                .start()
        }
        if (view.isAttachedToWindow) {
            start()
            return
        }
        view.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: View) {
                v.removeOnAttachStateChangeListener(this)
                start()
            }

            override fun onViewDetachedFromWindow(v: View) = Unit
        })
    }

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

    /**
     * The tracking a tab label gives up to on a phone — the web's
     * `--jv-track-tight`. `TopBar.svelte` drops its tabs from chrome to tight
     * tracking under 720px because five tracked words do not fit across a
     * handset; the phone kept chrome tracking at every width, which is what
     * put the strip behind an invisible scroll.
     */
    const val TRACK_TIGHT = 0.08f

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

    /**
     * A screen's title: the display face, light, bright, at the left — the
     * console's `ScreenTitle.svelte` h1. Sentence case is the caller's: the
     * console titles a page "Settings", not "SETTINGS", and a caps title over
     * caps labels made every phone screen one shout.
     *
     * Was centred. Nothing else on a screen is centred — the labels, the
     * fields, the rows all hang from the left — so the title was the one line
     * that did not line up with anything under it.
     */
    fun title(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.TITLE)
        letterSpacing = TRACK_SNUG
        typeface = DISPLAY_FACE
        gravity = Gravity.START
    }

    /**
     * The console's `ScreenTitle`: the title and one sentence under it saying
     * what the screen is for, in the body face and the dim text colour, with
     * the console's gap to whatever follows. Every destination opens this way
     * so the eye knows where it is; a phone screen that opened with a
     * wordmark and a tracked caps word did not.
     */
    fun screenTitle(context: Context, title: String, lede: String = ""): LinearLayout =
        LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(
                title(context, title),
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
            if (lede.isNotEmpty()) {
                addView(
                    TextView(context).apply {
                        text = lede
                        setTextColor(JarvisTokens.Color.TEXT_DIM)
                        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.BODY)
                        typeface = BODY_FACE
                        setLineSpacing(dp(context, Space.TIGHT).toFloat(), 1f)
                        setPadding(0, dp(context, Space.TIGHT), 0, 0)
                    },
                    LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    )
                )
            }
            setPadding(0, 0, 0, dp(context, Space.SCREEN))
        }

    /**
     * The label recipe, bare: uppercase, the label face, tracked, the
     * `--jv-fs-2xs` step. `Panel.svelte`'s head and `Button.svelte`'s face are
     * this recipe; so is every chrome word on the phone now. It exists because
     * nine screens typed the same idea by hand in mono with a tracking each
     * remembered differently (0.14, 0.16, 0.2, 0.24, 0.3), and mono is for
     * DATA — a heading in the data face reads as a readout.
     *
     * [tracking] is [TRACK_WIDE] for a heading over a block and
     * [TRACK_CHROME] for a word inside a control or a tag; nothing else.
     */
    fun labelText(
        context: Context,
        text: String,
        color: Int = JarvisTokens.Color.TEXT_DIM,
        tracking: Float = TRACK_WIDE,
    ): TextView = TextView(context).apply {
        this.text = text.uppercase()
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
        letterSpacing = tracking
        typeface = LABEL_FACE
    }

    /** Small all-caps label above a field or a block: tracked, dim, the label face. */
    fun label(context: Context, text: String): TextView = labelText(context, text).apply {
        setPadding(0, dp(context, Space.GAP), 0, dp(context, Space.TIGHT))
    }

    /**
     * A readout: data, in the data face. `StatusReadout.svelte`'s word, a
     * count, a duration, a host. Mono, the smallest step, snug tracking,
     * tabular digits so a changing number does not jitter its neighbours.
     * Never a heading — see [labelText].
     */
    fun readout(
        context: Context,
        text: String,
        color: Int = JarvisTokens.Color.TEXT_DIM,
    ): TextView = TextView(context).apply {
        this.text = text
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
        letterSpacing = TRACK_SNUG
        typeface = MONO_FACE
        fontFeatureSettings = TABULAR_DIGITS
    }

    /**
     * `Pill.svelte`: a small status word on a hairline — square-cornered, the
     * label recipe at chrome tracking, its tone in the word AND the colour so
     * the colour is never the only signal. [tone] is one of [TAG_NEUTRAL],
     * [TAG_LIVE], [TAG_OK], [TAG_WARN], [TAG_DANGER].
     */
    fun statusTag(context: Context, text: String, tone: Int = TAG_NEUTRAL): TextView =
        labelText(context, text, tracking = TRACK_CHROME).apply {
            val (ink, edge) = when (tone) {
                TAG_LIVE -> ACCENT to atAlpha(ACCENT, FILL_ALPHA_STRONG)
                TAG_OK -> APPROVE to atAlpha(APPROVE, FILL_ALPHA_STRONG)
                TAG_WARN -> GOLD to atAlpha(GOLD, FILL_ALPHA_STRONG)
                TAG_DANGER -> DENY_TEXT to atAlpha(DENY, FILL_ALPHA_STRONG)
                else -> JarvisTokens.Color.TEXT_DIM to JarvisTokens.Color.LINE_HAIR
            }
            setTextColor(ink)
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.SM).toFloat()
                setColor(Color.TRANSPARENT)
                setStroke(dp(context, Space.HAIRLINE), edge)
            }
            setPadding(dp(context, Space.SNUG), 0, dp(context, Space.SNUG), 0)
            maxLines = 1
        }

    const val TAG_NEUTRAL = 0
    const val TAG_LIVE = 1
    const val TAG_OK = 2
    const val TAG_WARN = 3
    const val TAG_DANGER = 4

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

    /** `--jv-line-hair` as a rule the width of its parent. */
    fun hairline(context: Context): View = View(context).apply {
        setBackgroundColor(JarvisTokens.Color.LINE_HAIR)
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(context, Space.HAIRLINE)
        )
    }

    /**
     * A list as the console draws one: ONE panel, and a hairline between each
     * row and the next (`Panel.svelte` around `Activity.svelte`'s
     * `border-top: 1px solid var(--jv-line-hair)`). Rows carry their own
     * padding and no background.
     *
     * Every row used to be a box of its own — fill, stroke and radius — and a
     * column of eight boxes with a gap between each is a stack of cards, which
     * is the previous direction. Density comes from hairlines over boxes.
     */
    fun rows(context: Context, rows: List<View>): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = panel(context)
        showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
        dividerDrawable = GradientDrawable().apply {
            setSize(0, dp(context, Space.HAIRLINE))
            setColor(JarvisTokens.Color.LINE_HAIR)
        }
        for (row in rows) {
            addView(
                row,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }
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
     * flat panel, the words in the body face, and the action as the screen's
     * one filled control: a held bar is on screen because the one thing to do
     * next is the thing on it, and `Approvals.svelte` fills APPROVE for the
     * same reason. It was a quiet hairline button, which made the fix look
     * optional. Amber rather than red: this is a thing the user can fix in two
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
            primary(context, actionLabel, onAction),
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(context, Space.ROW) }
        )
        addView(body, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
    }

    /**
     * One line of a checklist: a state glyph, a label, and an explanation, as
     * a row for [rows] — no box of its own. The glyph is text rather than an
     * icon so it survives any font and any accessibility scale, and so it
     * copies into a bug report as-is.
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
        setPadding(0, dp(context, Space.ROW), dp(context, Space.GAP), dp(context, Space.ROW))
        // A missing essential is the one row that is not quiet: the warn
        // colour as an inset rule down its left says "held" where every other
        // row says nothing — the held bar's own mark, so it reads as the same
        // idea. Drawn transparent on the others rather than left out, so the
        // rows' text lines up whether or not one of them is held.
        addView(
            View(context).apply {
                setBackgroundColor(if (!satisfied && essential) GOLD else Color.TRANSPARENT)
            },
            LinearLayout.LayoutParams(dp(context, Space.MICRO), ViewGroup.LayoutParams.MATCH_PARENT)
                .apply { rightMargin = dp(context, Space.ROW) }
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
        // `Input.svelte`: space-2 above and below, space-3 at the sides. It
        // was GAP all round, a step deeper than the console's on every edge.
        setPadding(dp(context, Space.ROW), dp(context, Space.STEP), dp(context, Space.ROW), dp(context, Space.STEP))
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
            // `Button.svelte`: space-2 by space-4 — 16 across, 10 down. It
            // was 20 by 12, and a control a step bigger than the console's on
            // every screen is what made the phone feel like a bigger, looser
            // copy rather than the same thing.
            setPadding(
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
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
            // The same geometry as [button]: the fill is what makes it the
            // primary, not a wider box. It was 34 by 12.
            setPadding(
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
            )
            setOnClickListener { onClick() }
        }

    /**
     * A tab in a strip: the label alone, with no box of its own. The strip
     * draws the one underline under whichever tab is current ([ConsoleFrame]),
     * so a tab is not a button that happens to sit in a row.
     *
     * Sized as `TopBar.svelte` sizes its tabs under 720px: the smallest chrome
     * step, TIGHT tracking, and space-1 of padding at the sides — so the
     * underline, which is this control's own width, is the word's width and
     * not the word plus a gap either side. The gap between tabs belongs to the
     * strip. It was chrome tracking with GAP all round at every width, which is
     * what made five labels overflow a handset.
     */
    fun tab(context: Context, label: String, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            setTextColor(JarvisTokens.Color.TEXT_DIM)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
            letterSpacing = TRACK_TIGHT
            typeface = LABEL_FACE
            background = null
            minWidth = 0
            minimumWidth = 0
            setPadding(
                dp(context, Space.TIGHT),
                dp(context, Space.STEP),
                dp(context, Space.TIGHT),
                dp(context, Space.STEP),
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
    ): Button = Button(context).apply {
        // `Select.svelte`, not a button: a VALUE, in the body face at the
        // body step, untracked, at the left, on the field ground with a
        // chevron at the end. It inherited [button]'s label recipe, so
        // "08:00" rendered tracked, centred and in the chrome face — a value
        // dressed as a heading.
        text = labels.getOrNull(selected) ?: "—"
        isAllCaps = false
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.BODY)
        typeface = BODY_FACE
        gravity = Gravity.START or Gravity.CENTER_VERTICAL
        minWidth = 0
        minimumWidth = 0
        background = panel(context, fill = JarvisTokens.Color.FIELD, stroke = JarvisTokens.Color.LINE_SOFT)
        setPadding(dp(context, Space.ROW), dp(context, Space.STEP), dp(context, Space.ROW), dp(context, Space.STEP))
        // The absolute setter, not the relative one: a relative drawable is
        // applied only once the view's layout direction resolves, which a
        // view drawn off a window (a golden) never does, so the chevron
        // silently did not appear in the picture that was meant to prove it.
        setCompoundDrawablesWithIntrinsicBounds(null, null, Chevron(context), null)
        compoundDrawablePadding = dp(context, Space.STEP)
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
     * Consent buttons. [tone] is [APPROVE] or [DENY], and says which half this
     * is; it is not the colour.
     *
     * The yes half is the screen's primary — the accent as a fill with the ink
     * on it, exactly `Approvals.svelte`'s `<Button variant="primary">APPROVE`
     * — because a screen whose only purpose is this answer has one primary
     * action and this is it. It was filled in the OK green, which is a
     * semantic colour: green says "this succeeded", and nothing has happened
     * yet. The no half is a quiet hairline in the danger text colour, so
     * saying yes and saying no are not two equally loud controls and a thumb
     * cannot mistake one for the other by shape. The console's hairline-OK
     * `approve` variant is for the yes of a QUESTION Jarvis asked, not for
     * consent, and no phone screen draws one yet.
     */
    fun consentButton(context: Context, label: String, tone: Int, onClick: () -> Unit): Button =
        Button(context).apply {
            text = label
            isAllCaps = true
            val yes = tone == APPROVE
            setTextColor(if (yes) JarvisTokens.Color.ACCENT_INK else DENY_TEXT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
            letterSpacing = TRACK_CHROME
            typeface = LABEL_FACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(if (yes) ACCENT else Color.TRANSPARENT)
                setStroke(dp(context, Space.HAIRLINE), if (yes) ACCENT else atAlpha(DENY, FILL_ALPHA_STRONG))
            }
            setPadding(dp(context, Space.SCREEN), dp(context, Size.INSET), dp(context, Space.SCREEN), dp(context, Size.INSET))
            // Refuse taps that arrive through another window sitting on top of
            // this one. A consent screen is exactly what a tapjacking overlay
            // wants to sit on.
            filterTouchesWhenObscured = true
            setOnClickListener { onClick() }
        }

    /** A colour at an opacity, without writing either of them down twice. */
    fun atAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or (alpha.coerceIn(0, 255) shl 24)

    /**
     * The stroke on a quiet danger control, and the edge of a toned tag:
     * enough to read the tone, not to shout. `Pill.svelte` mixes its edge at
     * 40 %; this is the phone's one such value.
     */
    private const val FILL_ALPHA_STRONG = 0x73

    /** `font-variant-numeric: tabular-nums`, for a readout whose digits change. */
    private const val TABULAR_DIGITS = "tnum"

    /** What a held bar says above its message. */
    private const val HELD_LABEL = "HELD · ASKS BEFORE IT GOES ON"

    /**
     * The chevron at the end of a [chooser]: two hairline strokes meeting at a
     * point, in the dim text colour, the size of a label glyph. Drawn rather
     * than a glyph because "▾" is a font's decision and the platform's mono
     * face renders it as a filled block.
     */
    private class Chevron(context: Context) : Drawable() {
        private val size = dp(context, Space.ROW)
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = dp(context, Space.HAIRLINE).toFloat()
            color = JarvisTokens.Color.TEXT_DIM
        }

        override fun draw(canvas: Canvas) {
            val b = bounds
            val path = Path().apply {
                moveTo(b.left + b.width() * 0.25f, b.top + b.height() * 0.4f)
                lineTo(b.exactCenterX(), b.top + b.height() * 0.65f)
                lineTo(b.right - b.width() * 0.25f, b.top + b.height() * 0.4f)
            }
            canvas.drawPath(path, paint)
        }

        override fun getIntrinsicWidth(): Int = size
        override fun getIntrinsicHeight(): Int = size
        override fun setAlpha(alpha: Int) { paint.alpha = alpha }
        override fun setColorFilter(colorFilter: ColorFilter?) { paint.colorFilter = colorFilter }

        @Deprecated("Deprecated in Java")
        override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
    }
}
