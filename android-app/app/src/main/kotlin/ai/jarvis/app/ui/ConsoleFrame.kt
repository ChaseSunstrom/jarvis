package ai.jarvis.app.ui

import android.app.Activity
import android.content.Intent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import ai.jarvis.app.ui.theme.JarvisTokens

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
 * ## The look (M51)
 *
 * Reactor II's tabs: uppercase labels on a hairline, and ONE accent underline
 * under whichever is current — not a lit button among dim ones. The strip is
 * what the browser's bar draws above the same pages, so a person moving from
 * the phone to the console sees the same idea in the same place.
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

    /**
     * The strip, with [current] marked.
     *
     * [current] is the [ConsoleTab] being shown, or null on the phone's own
     * screen — which is how PHONE gets marked instead. Selecting the tab you
     * are already on is left to the caller: [ManagementActivity]
     * [ai.jarvis.app.ManagementActivity] re-issues its authenticated
     * navigation (a reload), and the settings screen does nothing.
     */
    fun tabBar(
        activity: Activity,
        current: ConsoleTab?,
        onPhone: Boolean = false,
        onTab: (ConsoleTab) -> Unit,
    ): ViewGroup {
        val strip = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            val p = JarvisUi.dp(activity, JarvisUi.Space.GAP)
            setPadding(p, 0, p, 0)
        }

        val buttons = mutableListOf<Pair<Button, Boolean>>()
        for (entry in ConsoleTab.entries) {
            val button = JarvisUi.tab(activity, entry.label) { onTab(entry) }
            buttons += button to (!onPhone && entry == current)
            strip.addView(withUnderline(activity, button, !onPhone && entry == current))
            strip.addView(gap(activity))
        }

        // The console's four scroll. PHONE does NOT.
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
        // where a settings affordance belongs. The four that scroll are the
        // console's, which is also the honest visual grouping: they are one
        // thing and this is another.
        val phone = JarvisUi.tab(activity, PHONE_LABEL) {
            if (!onPhone) {
                activity.startActivity(
                    Intent(activity, ai.jarvis.app.SettingsActivity::class.java)
                )
            }
        }
        buttons += phone to onPhone

        for ((button, here) in buttons) {
            button.setTextColor(
                if (here) JarvisTokens.Color.TEXT_BRIGHT else JarvisTokens.Color.TEXT_DIM
            )
        }

        val scroller = HorizontalScrollView(activity).apply {
            isHorizontalScrollBarEnabled = false
            // Fills the width when the tabs fit and scrolls when they do not.
            isFillViewport = true
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

        val bar = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            val pad = JarvisUi.dp(activity, JarvisUi.Space.GAP)
            setPadding(0, 0, pad, 0)
            // Weight 0 on the width so the scroller takes what is left rather
            // than pushing PHONE off the edge it was just rescued from.
            addView(
                scroller,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            )
            addView(
                withUnderline(activity, phone, onPhone),
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }

        // The hairline the whole strip sits on, with the underline drawn over
        // it under the current tab: the bar's own edge, not a box per tab.
        return LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            addView(
                bar,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
            addView(
                View(activity).apply { setBackgroundColor(JarvisTokens.Color.LINE_HAIR) },
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    JarvisUi.dp(activity, JarvisUi.Space.HAIRLINE)
                ).apply { bottomMargin = JarvisUi.dp(activity, JarvisUi.Space.ROW) }
            )
        }
    }

    /**
     * A tab over its underline. The rule is drawn for every tab so the strip
     * does not reflow when the current one changes — it is transparent under
     * the others and the accent under this one.
     */
    private fun withUnderline(activity: Activity, tab: Button, here: Boolean): View =
        LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            addView(
                tab,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
            val underline = View(activity).apply {
                setBackgroundColor(if (here) JarvisTokens.Color.ACCENT else android.graphics.Color.TRANSPARENT)
                tag = if (here) UNDERLINE_TAG else null
            }
            addView(
                underline,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    JarvisUi.dp(activity, JarvisUi.Space.MICRO)
                )
            )
        }

    private fun gap(activity: Activity): View = View(activity).apply {
        layoutParams = LinearLayout.LayoutParams(JarvisUi.dp(activity, JarvisUi.Space.STEP), 1)
    }

    /** The view tag on the one lit underline, for a test to find it by. */
    const val UNDERLINE_TAG = "underline"
}
