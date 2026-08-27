package ai.jarvis.app.ui

import android.animation.ValueAnimator
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens
import kotlin.math.max
import kotlin.math.min

/**
 * The console's nav, as one strip that every screen behind it wears.
 *
 * ## Why this exists
 *
 * *"because the buttons on the home screen take you to basically the web app
 * view, why dont you just have a Manage button ... and have the settings for
 * the android app be in that same web view look? so we can dedup the things"*
 *
 * There were three copies of the same navigation: the home screen's grid of six
 * buttons, [ManagementActivity][ai.jarvis.app.ManagementActivity]'s tab strip,
 * and the console's own nav inside the WebView. The home screen's copy is gone
 * — it is one MANAGE button now — and the other two are what they always were,
 * one of them unavoidable: a link tapped inside the WebView is a
 * page-initiated navigation and WebView does not attach `additionalHeaders` to
 * those, so the page's own nav cannot carry the bearer token. The native strip
 * is not decoration; it is the only nav in this app that works.
 *
 * So there is one strip, built here, and both screens that need it use it.
 *
 * ## The look (M51, then M64)
 *
 * `TopBar.svelte` under 720px, exactly: a first row with the brand at the left
 * — the reactor mark and JARVIS — and the status readout at the right; a
 * second row of tabs in the smallest chrome step with TIGHT tracking, which
 * scrolls when it must and fades at the edge it overflows, with ONE accent
 * underline the current tab's own width that SLIDES to the next one over
 * `motion.dur.base`. It was an underline per tab, rebuilt on every switch, so
 * a tab change was one light going off and another coming on rather than the
 * same thing elsewhere; and six labels at chrome tracking, which is what put
 * PHONE behind an invisible scroll.
 *
 * ## Why PHONE is a tab here but not a [ConsoleTab]
 *
 * The phone's own settings sit in this strip beside the console's sections, so
 * that the mobile half and the house's half are one frame with one nav instead
 * of a native screen reached from somewhere else entirely. But it is NOT an
 * entry in [ConsoleTab], because that enum is pinned tab-for-tab against
 * `jarvis-web/src/lib/screens.ts` by `console_parity_test.py` and the browser
 * has no PHONE page — it cannot, since what is on it is Android permissions,
 * the wake word and which server this handset talks to. Adding it to the enum
 * would make the parity spec either wrong or a lie.
 *
 * That is also the honest limit of this dedup. The phone's settings cannot BE a
 * web page; a page in a WebView cannot ask for RECORD_AUDIO, take a battery
 * exemption, or download a wake-word model. What they can share is the frame,
 * the nav and the chrome, which is what somebody means by "the same look".
 */
object ConsoleFrame {

    /** What the phone's own half is called wherever it is offered. */
    const val PHONE_LABEL = ConsoleTab.PHONE_LABEL

    /** The view tag on the row that draws the one underline, for a test to find it by. */
    const val UNDERLINE_TAG = "underline"

    /** The brand word beside the mark. */
    const val BRAND = "JARVIS"

    /** `StatusReadout.svelte`'s tones: how lit the readout's dot is, and what colour its word. */
    enum class Tone { LIVE, WARN, OFF, NEUTRAL }

    /**
     * The strip, with [current] marked.
     *
     * [current] is the [ConsoleTab] being shown, or null on the phone's own
     * screen — which is how PHONE gets marked instead. Selecting the tab you
     * are already on is left to the caller: [ManagementActivity]
     * [ai.jarvis.app.ManagementActivity] re-issues its authenticated
     * navigation (a reload), and the settings screen does nothing. The
     * returned [Strip] is what a screen keeps: [Strip.select] slides the
     * underline to a new tab and [Strip.setStatus] writes the readout.
     */
    fun tabBar(
        activity: Activity,
        current: ConsoleTab?,
        onPhone: Boolean = false,
        onTab: (ConsoleTab) -> Unit,
    ): Strip = Strip(activity, onPhone, onTab).also { it.select(current) }

    /**
     * The brand row alone — the mark, JARVIS, and the readout — for the voice
     * screen, which wears the console's bar (the HUD sits under the same
     * `TopBar` in a browser) but not its tabs: the home screen reaches the
     * console through one MANAGE control on purpose (see `MainActivity`), and
     * `console_parity_test.py` holds it to that.
     */
    fun brand(context: Context): Brand = Brand(context)

    /**
     * The first row of the bar: the mark and the word at the left, the
     * readout at the right, on the bar's hairline.
     */
    class Brand(context: Context) : LinearLayout(context) {
        private val readoutDot = StateDot(context).apply { periodMs = JarvisTokens.Motion.Dur.BLINK }
        private val readoutWord = JarvisUi.readout(context, "")
        private val actions = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        init {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(
                JarvisUi.dp(context, JarvisUi.Space.GAP),
                JarvisUi.dp(context, JarvisUi.Space.STEP),
                JarvisUi.dp(context, JarvisUi.Space.GAP),
                0,
            )
            val mark = BrandMark(context)
            addView(
                mark,
                LayoutParams(JarvisUi.dp(context, JarvisUi.Space.SCREEN), JarvisUi.dp(context, JarvisUi.Space.SCREEN))
                    .apply { rightMargin = JarvisUi.dp(context, JarvisUi.Space.ROW) }
            )
            // The word: the body face at the label weight, `--jv-fs-sm`,
            // wide tracking, bright — `.brand` in TopBar.svelte.
            addView(
                TextView(context).apply {
                    text = BRAND
                    setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
                    setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisUi.Type.BODY)
                    letterSpacing = JarvisUi.TRACK_WIDE
                    typeface = JarvisUi.LABEL_FACE
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                },
                LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            )
            val readout = LinearLayout(context).apply {
                orientation = HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                // The one item a screen reader follows — the console gives its
                // link readout `role="status"`.
                JarvisUi.liveRegion(this)
            }
            readout.addView(
                readoutDot,
                LayoutParams(JarvisUi.dp(context, JarvisUi.Space.SNUG), JarvisUi.dp(context, JarvisUi.Space.SNUG))
                    .apply { rightMargin = JarvisUi.dp(context, JarvisUi.Space.SNUG) }
            )
            readout.addView(readoutWord)
            addView(readout, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            addView(actions, LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            JarvisUi.describe(mark, BRAND)
            setStatus("", Tone.OFF)
        }

        /** What the bar says about the screen, and how lit its dot is. */
        fun setStatus(label: String, tone: Tone) {
            readoutWord.text = label
            readoutWord.setTextColor(
                when (tone) {
                    Tone.LIVE -> JarvisTokens.Color.TEXT
                    Tone.WARN -> JarvisTokens.Color.WARN
                    Tone.OFF -> JarvisTokens.Color.TEXT_FAINT
                    Tone.NEUTRAL -> JarvisTokens.Color.TEXT_DIM
                }
            )
            readoutDot.set(
                when (tone) {
                    Tone.LIVE -> StateDot.Tone.LIVE
                    Tone.WARN -> StateDot.Tone.WARN
                    Tone.OFF -> StateDot.Tone.REST
                    Tone.NEUTRAL -> StateDot.Tone.NEUTRAL
                }
            )
            readoutDot.visibility = if (label.isEmpty()) GONE else VISIBLE
        }

        /**
         * A control at the end of the row. The console's bar has none; the
         * phone's console screen needs RELOAD, because a WebView has no
         * pull-to-refresh and every navigation here must carry the bearer.
         */
        fun addAction(view: View) {
            actions.addView(
                view,
                LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                    .apply { leftMargin = JarvisUi.dp(context, JarvisUi.Space.ROW) }
            )
        }
    }

    /**
     * The whole bar: the brand row, the tab row with its one underline, and
     * the hairline it all sits on.
     */
    class Strip internal constructor(
        activity: Activity,
        private val onPhone: Boolean,
        onTab: (ConsoleTab) -> Unit,
    ) : LinearLayout(activity) {

        val brand = Brand(activity)
        private val tabs = LinkedHashMap<ConsoleTab, Button>()
        private val phone: Button
        private val row: TabRow

        init {
            orientation = VERTICAL
            addView(brand, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

            val strip = LinearLayout(activity).apply {
                orientation = HORIZONTAL
                val p = JarvisUi.dp(activity, JarvisUi.Space.GAP)
                setPadding(p, 0, p, 0)
            }
            for (entry in ConsoleTab.entries) {
                val button = JarvisUi.tab(activity, entry.label) { onTab(entry) }
                tabs[entry] = button
                strip.addView(button)
                strip.addView(gap(activity))
            }

            // The console's five scroll. PHONE does NOT.
            //
            // It used to be the sixth button inside this scroller, and six
            // monospace labels do not fit a phone's width — so the one entry that
            // is about THIS HANDSET sat off the right-hand edge, behind a
            // horizontal scroll with no scrollbar, on a strip whose other five
            // items are all reachable. Reported, twice, as the phone's settings
            // simply not being there; and the second report came after a release
            // that had "fixed" it, because what was fixed was the duplicate nav
            // and not the fact that you cannot tap what you cannot see.
            //
            // So it is pinned outside the scroller, always on screen, at the end
            // where a settings affordance belongs. The five that scroll are the
            // console's, which is also the honest visual grouping: they are one
            // thing and this is another.
            phone = JarvisUi.tab(activity, PHONE_LABEL) {
                if (!onPhone) {
                    activity.startActivity(
                        Intent(activity, ai.jarvis.app.SettingsActivity::class.java)
                    )
                }
            }

            val scroller = Scroller(activity).apply {
                // FrameLayout params, not LinearLayout's: HorizontalScrollView IS a
                // FrameLayout, and FrameLayout.onMeasure casts its child's
                // LayoutParams — the wrong type is a ClassCastException on the
                // first measure pass rather than a layout that looks a bit off.
                addView(
                    strip,
                    FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    )
                )
            }

            row = TabRow(activity, scroller).apply {
                val pad = JarvisUi.dp(activity, JarvisUi.Space.GAP)
                setPadding(0, 0, pad, 0)
                tag = UNDERLINE_TAG
                // Weight 0 on the width so the scroller takes what is left rather
                // than pushing PHONE off the edge it was just rescued from.
                addView(
                    scroller,
                    LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                )
                addView(
                    phone,
                    LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                    )
                )
            }
            addView(row, LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

            // The hairline the whole strip sits on, with the underline drawn over
            // it under the current tab: the bar's own edge, not a box per tab.
            addView(
                JarvisUi.hairline(activity),
                LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, JarvisUi.dp(activity, JarvisUi.Space.HAIRLINE))
                    .apply { bottomMargin = JarvisUi.dp(activity, JarvisUi.Space.ROW) }
            )
        }

        /**
         * Light [current] — or PHONE, on the phone's own screen — and slide
         * the underline there. Animated when the strip is already on screen;
         * placed outright before its first layout, and under reduced motion.
         */
        fun select(current: ConsoleTab?) {
            val target = if (onPhone) phone else current?.let { tabs[it] }
            for ((_, button) in tabs) {
                button.setTextColor(
                    if (button === target) JarvisTokens.Color.TEXT_BRIGHT else JarvisTokens.Color.TEXT_DIM
                )
            }
            phone.setTextColor(if (onPhone) JarvisTokens.Color.TEXT_BRIGHT else JarvisTokens.Color.TEXT_DIM)
            row.moveTo(target)
        }

        /** What the bar says about the screen. */
        fun setStatus(label: String, tone: Tone) = brand.setStatus(label, tone)

        private fun gap(context: Context): View = View(context).apply {
            layoutParams = LayoutParams(JarvisUi.dp(context, JarvisUi.Space.SECTION), 1)
        }
    }

    /**
     * The scrolling half of the tab row. No scrollbar; the edge it overflows
     * fades to the ground colour instead — `TopBar.svelte`'s `scrollbar-width:
     * none` with the fade a reader's eye needs to know there is more.
     */
    private class Scroller(context: Context) : HorizontalScrollView(context) {
        init {
            isHorizontalScrollBarEnabled = false
            isHorizontalFadingEdgeEnabled = true
            setFadingEdgeLength(JarvisUi.dp(context, JarvisUi.Space.SECTION))
            // Fills the width when the tabs fit and scrolls when they do not.
            isFillViewport = true
        }

        /** The fade is drawn in the ground colour, as a solid ramp rather than a composited layer. */
        override fun getSolidColor(): Int = JarvisTokens.Color.BG
    }

    /**
     * The tab row, which draws the ONE underline.
     *
     * Measured rather than drawn per tab, as `TopBar.svelte` measures its
     * `.ind` against the current anchor: one rule that moves is what makes a
     * tab change read as "the same thing, elsewhere" instead of one light
     * going off and another coming on. Drawn by this row rather than by a
     * View of its own so that it can sit under a tab inside the scroller and
     * under PHONE outside it with the same geometry, follow the scroller as
     * it scrolls, and be clipped at the scroller's edge rather than drawn over
     * PHONE when the current tab is half off screen.
     */
    private class TabRow(context: Context, private val scroller: Scroller) : LinearLayout(context) {
        private val paint = Paint().apply { color = JarvisTokens.Color.ACCENT }
        private val thickness = JarvisUi.dp(context, JarvisUi.Space.MICRO).toFloat()

        /** Where the underline is now, in this row's coordinates. */
        private var indLeft = 0f
        private var indWidth = 0f
        private var placed = false
        private var target: Button? = null
        private var slide: ValueAnimator? = null

        init {
            orientation = HORIZONTAL
            setWillNotDraw(false)
            // The underline follows the scroller; without this it would stay
            // where the tab WAS while the tab moved under a thumb.
            scroller.setOnScrollChangeListener { _, _, _, _, _ -> invalidate() }
            addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
                // The fonts, the width and the labels can all change every
                // tab's width after the first placement (`refit()` on the
                // web); place again against what is actually laid out.
                val t = target ?: return@addOnLayoutChangeListener
                if (slide == null) place(t, animate = false)
            }
        }

        fun moveTo(button: Button?) {
            target = button
            if (button == null) {
                placed = false
                invalidate()
                return
            }
            if (button.width == 0) {
                // Not laid out yet: the layout listener places it. Nothing to
                // slide from, so nothing to animate.
                return
            }
            place(button, animate = placed)
        }

        /**
         * The underline's left edge in this row's coordinates, for [button].
         * Its width is the button's own. No pair is allocated: this is read
         * on every frame while the strip scrolls, and lint's DrawAllocation
         * is right that a draw should not make garbage.
         */
        private fun leftOf(button: Button): Float =
            if (button.parent === scroller.getChildAt(0)) {
                (scroller.left + button.left - scroller.scrollX).toFloat()
            } else {
                button.left.toFloat()
            }

        private fun place(button: Button, animate: Boolean) {
            val toLeft = leftOf(button)
            val toWidth = button.width.toFloat()
            reveal(button)
            slide?.cancel()
            slide = null
            if (!animate || JarvisUi.reducedMotion(context) || !ValueAnimator.areAnimatorsEnabled()) {
                indLeft = toLeft
                indWidth = toWidth
                placed = true
                invalidate()
                return
            }
            val fromLeft = indLeft
            val fromWidth = indWidth
            slide = ValueAnimator.ofFloat(0f, 1f).apply {
                duration = JarvisTokens.Motion.Dur.BASE.toLong()
                interpolator = JarvisUi.EASE_OUT
                addUpdateListener { a ->
                    val t = a.animatedValue as Float
                    indLeft = fromLeft + (toLeft - fromLeft) * t
                    indWidth = fromWidth + (toWidth - fromWidth) * t
                    invalidate()
                }
                addListener(object : android.animation.AnimatorListenerAdapter() {
                    override fun onAnimationEnd(animation: android.animation.Animator) {
                        slide = null
                    }
                })
                start()
            }
            placed = true
        }

        /**
         * Scroll the current tab into view, as the console does: a lit tab off
         * the edge of the strip says "you are somewhere" and not where.
         */
        private fun reveal(button: Button) {
            if (button.parent !== scroller.getChildAt(0)) return
            val margin = JarvisUi.dp(context, JarvisUi.Space.SECTION)
            val visibleLeft = scroller.scrollX
            val visibleRight = scroller.scrollX + scroller.width
            val to = when {
                button.left - margin < visibleLeft -> max(0, button.left - margin)
                button.right + margin > visibleRight -> button.right + margin - scroller.width
                else -> return
            }
            if (JarvisUi.reducedMotion(context)) scroller.scrollTo(to, 0) else scroller.smoothScrollTo(to, 0)
        }

        override fun dispatchDraw(canvas: Canvas) {
            super.dispatchDraw(canvas)
            val t = target ?: return
            if (!placed) return
            // While the underline follows a scrolling tab, read the live
            // position rather than the placed one, and clip it to the
            // scroller's edge so it never runs under PHONE.
            var l = indLeft
            var w = indWidth
            if (slide == null && t.parent === scroller.getChildAt(0)) {
                l = leftOf(t)
                w = t.width.toFloat()
            }
            var right = l + w
            if (t.parent === scroller.getChildAt(0)) {
                l = max(l, scroller.left.toFloat())
                right = min(right, scroller.right.toFloat())
            }
            if (right <= l) return
            val bottom = height.toFloat()
            canvas.drawRect(l, bottom - thickness, right, bottom, paint)
        }
    }

    /**
     * The reactor's mark: a dashed ring, a ring, a dot, in the accent — the
     * `<svg class="mark">` beside the word in `TopBar.svelte`, at the same
     * proportions (radii 8, 3.5 and 1.2 of an 18-unit box).
     */
    private class BrandMark(context: Context) : View(context) {
        private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = JarvisTokens.Color.ACCENT
            strokeWidth = JarvisUi.dp(context, JarvisUi.Space.HAIRLINE).toFloat()
        }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = JarvisTokens.Color.ACCENT }

        override fun onDraw(canvas: Canvas) {
            val unit = min(width, height) / MARK_BOX
            val cx = width / 2f
            val cy = height / 2f
            stroke.pathEffect = DashPathEffect(floatArrayOf(MARK_DASH * unit, MARK_GAP * unit), 0f)
            canvas.drawCircle(cx, cy, MARK_RING * unit, stroke)
            stroke.pathEffect = null
            canvas.drawCircle(cx, cy, MARK_INNER * unit, stroke)
            canvas.drawCircle(cx, cy, MARK_DOT * unit, fill)
        }

        private companion object {
            const val MARK_BOX = 18f
            const val MARK_RING = 8f
            const val MARK_INNER = 3.5f
            const val MARK_DOT = 1.2f
            const val MARK_DASH = 6f
            const val MARK_GAP = 3f
        }
    }
}
