package ai.jarvis.app.ui

import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.crash.JarvisCrashHandler
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * One screen that says exactly what is missing.
 *
 * On GrapheneOS an app can be installed, launched, and completely inert because
 * a toggle the user has never heard of is off. The old Home-Assistant fork's
 * answer to that was a spinner that never stopped. This screen's answer is a
 * list: every permission and special access Jarvis can use, whether it has it,
 * what breaks without it, and a button that opens the exact settings page.
 *
 * The checklist itself is [GrapheneCompat.requirements]; this activity only
 * renders it. It re-probes in [onResume], because the user leaves to change a
 * setting and comes straight back — a stale checklist would be worse than none.
 */
class SystemCheckActivity : Activity() {

    private lateinit var listColumn: LinearLayout
    private lateinit var summary: TextView
    private lateinit var bannerSlot: FrameLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildUi())
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun buildUi(): ViewGroup {
        val root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }
        val col = JarvisUi.column(this, padDp = 20)

        col.addView(JarvisUi.title(this, "SYSTEM CHECK"))

        summary = JarvisUi.hint(this, "")
        col.addView(summary)

        bannerSlot = FrameLayout(this)
        col.addView(
            bannerSlot,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@SystemCheckActivity, 12) }
        )

        col.addView(JarvisUi.label(this, "Requirements"))
        listColumn = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(
            listColumn,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )

        col.addView(JarvisUi.label(this, "Diagnostics"))
        col.addView(
            JarvisUi.hint(
                this,
                "If Jarvis has closed unexpectedly, the stack trace is on this device — " +
                    "no cable, no laptop, no logcat needed."
            )
        )
        col.addView(
            JarvisUi.ghost(this, "CRASH LOGS") {
                startActivity(Intent(this, CrashLogActivity::class.java))
            },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@SystemCheckActivity, 8) }
        )
        col.addView(
            JarvisUi.ghost(this, "APP INFO") { GrapheneCompat.openAppDetails(this) },
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = JarvisUi.dp(this@SystemCheckActivity, 8) }
        )
        col.addView(JarvisUi.spacer(this, 24))

        val scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(
                col,
                ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }
        root.addView(
            scroll,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        return root
    }

    private fun refresh() {
        val requirements = GrapheneCompat.requirements(this)
        val missing = requirements.count { it.essential && !it.satisfied }
        val optional = requirements.count { !it.essential && !it.satisfied }

        summary.text = when {
            missing > 0 && optional > 0 ->
                "$missing required item(s) missing, $optional optional item(s) off."
            missing > 0 -> "$missing required item(s) missing."
            optional > 0 -> "Everything required is granted. $optional optional item(s) are off."
            else -> "Everything is granted."
        }

        bannerSlot.removeAllViews()
        GrapheneCompat.networkBanner(this)?.let { text ->
            bannerSlot.addView(
                JarvisUi.banner(this, text, "OPEN PERMISSIONS") {
                    GrapheneCompat.openAppDetails(this)
                },
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }

        listColumn.removeAllViews()
        for (req in requirements) {
            listColumn.addView(
                JarvisUi.checkRow(
                    context = this,
                    satisfied = req.satisfied,
                    essential = req.essential,
                    label = req.label,
                    why = req.why,
                    onClick = {
                        // Sending the user off to change this toggle makes
                        // everything observed on the wire so far stale; keeping
                        // it would leave a fixed problem still on screen.
                        if (req.id == GrapheneCompat.ID_NETWORK) {
                            GrapheneCompat.resetNetworkObservations()
                        }
                        GrapheneCompat.openSettingsFor(this, req)
                    },
                ),
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = JarvisUi.dp(this@SystemCheckActivity, 8) }
            )
        }

        if (JarvisCrashHandler.hasRecords(this)) {
            listColumn.addView(
                JarvisUi.hint(this, "There are recorded crashes on this device — see Diagnostics.")
            )
        }
    }
}
