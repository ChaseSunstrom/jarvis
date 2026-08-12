package ai.jarvis.app.ui

import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.compat.RuntimePermissions
import ai.jarvis.app.crash.JarvisCrashHandler
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.util.Log
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

    /** What [askInPlace] last asked for, so the result can be judged. */
    private var pendingGroup: List<String> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildUi().also { JarvisUi.fitSystemBars(it) })
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
                        // A runtime permission is one tap here. Only rows with
                        // nothing askable left fall through to Settings — this
                        // screen exists because "go and find it in Settings"
                        // is how the grants got missed in the first place.
                        if (!askInPlace(req)) GrapheneCompat.openSettingsFor(this, req)
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

    /**
     * Ask for the runtime permissions behind [req], here, rather than opening
     * a Settings page and hoping.
     *
     * Returns false when there is nothing this screen can ask for — a special
     * access, a grant that is already held, or a group with no runtime
     * permissions at all — and the caller then falls back to Settings.
     *
     * This is the other half of the fix for permissions that were declared and
     * never requested. The dispatcher asks at the moment an action needs one;
     * this is for the person who came here after reading `permission … not
     * granted` in a transcript and wants to fix it before trying again.
     */
    private fun askInPlace(req: GrapheneCompat.Requirement): Boolean {
        val wanted = RuntimePermissions.missing(this, RuntimePermissions.inGroup(req.id))
        if (wanted.isEmpty()) return false
        pendingGroup = wanted
        return try {
            requestPermissions(wanted.toTypedArray(), REQ_GROUP)
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not request ${req.id} in place", t)
            pendingGroup = emptyList()
            false
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_GROUP) return
        val outstanding = RuntimePermissions.missing(this, pendingGroup)
        pendingGroup = emptyList()
        // "Don't ask again" means the dialog will never appear from here again,
        // and a row that does nothing when tapped is worse than one that sends
        // you to Settings. So when the platform is done asking, we take over.
        //
        // AN EMPTY `grantResults` IS NOT AN ANSWER. It means the request was
        // cancelled — the user pressed Back on the dialog, or something else
        // took the foreground — and nothing was decided. The platform reports
        // "no rationale needed" for a permission that has never been answered
        // just as it does for one refused with don't-ask-again, so without this
        // the two are indistinguishable and the FIRST Back on the first
        // Calendar tap threw the user out to the App info screen.
        //
        // `PermissionRequestActivity` guards exactly this, in the same words,
        // for the same reason. This one did not.
        val decided = grantResults.isNotEmpty()
        val exhausted = decided && outstanding.isNotEmpty() && outstanding.none {
            runCatching { shouldShowRequestPermissionRationale(it) }.getOrDefault(false)
        }
        if (exhausted) GrapheneCompat.openAppDetails(this)
        refresh()
    }

    private companion object {
        const val TAG = "JarvisSystemCheck"
        const val REQ_GROUP = 5401
    }
}
