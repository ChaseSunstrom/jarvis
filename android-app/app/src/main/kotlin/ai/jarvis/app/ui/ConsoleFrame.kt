package ai.jarvis.app.ui

import android.app.Activity
import android.content.Intent
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout

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
 * ## Why PHONE is a tab here but not a [ConsoleTab]
 *
 * The phone's own settings sit in this strip beside the console's sections, so
 * that the mobile half and the house's half are one frame with one nav instead
 * of a native screen reached from somewhere else entirely. But it is NOT an
 * entry in [ConsoleTab], because that enum is pinned tab-for-tab against
 * `jarvis-web/src/routes/+layout.svelte` by `console_parity_test.py` and the
 * browser has no PHONE page — it cannot, since what is on it is Android
 * permissions, the wake word and which server this handset talks to. Adding it
 * to the enum would make the parity spec either wrong or a lie.
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
            val p = JarvisUi.dp(activity, 12)
            setPadding(p, 0, p, JarvisUi.dp(activity, 10))
        }

        val buttons = mutableListOf<Pair<Button, Boolean>>()
        for (entry in ConsoleTab.entries) {
            val button = JarvisUi.ghost(activity, entry.label) { onTab(entry) }
            buttons += button to (!onPhone && entry == current)
            strip.addView(button)
            strip.addView(gap(activity))
        }
        // Last, because it is the one entry that is this phone rather than the
        // house — and the order the console's own nav has no opinion about.
        val phone = JarvisUi.ghost(activity, PHONE_LABEL) {
            if (!onPhone) {
                activity.startActivity(
                    Intent(activity, ai.jarvis.app.SettingsActivity::class.java)
                )
            }
        }
        buttons += phone to onPhone
        strip.addView(phone)

        for ((button, here) in buttons) {
            button.setTextColor(if (here) JarvisUi.ACCENT else JarvisUi.DIM)
            button.alpha = if (here) 1f else 0.75f
        }

        // Scrolls, because six monospace labels do not fit a phone's width and
        // the alternative is a nav that wraps into two ragged lines.
        return HorizontalScrollView(activity).apply {
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
    }

    private fun gap(activity: Activity): View = View(activity).apply {
        layoutParams = LinearLayout.LayoutParams(JarvisUi.dp(activity, 8), 1)
    }
}
