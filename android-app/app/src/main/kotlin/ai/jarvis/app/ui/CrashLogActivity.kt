package ai.jarvis.app.ui

import ai.jarvis.app.crash.CrashRecord
import ai.jarvis.app.crash.JarvisCrashHandler
import android.app.Activity
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.text.format.DateFormat
import android.view.Gravity
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * The crash log, on the phone.
 *
 * The app this replaced crashed on GrapheneOS constantly and every diagnosis
 * needed a laptop, a cable, and a logcat buffer that had usually already rolled
 * over. [JarvisCrashHandler] writes each crash to app storage; this screen
 * turns that file into something a person can read, copy and clear without any
 * of that.
 *
 * Two levels only: a list of headlines, and one full report. There is no
 * upload button and no crash reporting service — the file is local, it is
 * yours, and copying it somewhere is a decision you make one crash at a time.
 */
class CrashLogActivity : Activity() {

    private var showing: CrashRecord? = null
    private lateinit var root: FrameLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }
        setContentView(root.also { JarvisUi.fitSystemBars(it) })
        showList()
    }

    /**
     * Back steps out of a report before it leaves the screen.
     *
     * Still the classic callback rather than an `OnBackInvokedCallback`,
     * because the manifest sets `enableOnBackInvokedCallback="false"` for the
     * whole app — the consent screen depends on the old dispatch semantics.
     */
    @Suppress("DEPRECATION", "MissingSuperCall")
    override fun onBackPressed() {
        if (showing != null) {
            showing = null
            showList()
            return
        }
        super.onBackPressed()
    }

    // --- list ---------------------------------------------------------------

    private fun showList() {
        val records = JarvisCrashHandler.recent(this)
        val col = JarvisUi.column(this, padDp = 20)

        col.addView(JarvisUi.title(this, "CRASH LOGS"))
        col.addView(
            JarvisUi.hint(
                this,
                "Written to ${JarvisCrashHandler.file(this).path}. App-private, excluded " +
                    "from backups, and never sent anywhere."
            )
        )

        if (records.isEmpty()) {
            col.addView(JarvisUi.spacer(this, 24))
            col.addView(
                TextView(this).apply {
                    text = "No crashes recorded."
                    setTextColor(JarvisUi.FAINT)
                    textSize = JarvisUi.Type.BODY
                    gravity = Gravity.CENTER
                    typeface = android.graphics.Typeface.MONOSPACE
                }
            )
        } else {
            col.addView(JarvisUi.label(this, "${records.size} recorded, newest first"))
            for (record in records) {
                col.addView(rowFor(record), matchWidth().apply { topMargin = JarvisUi.dp(this@CrashLogActivity, 8) })
            }
            col.addView(JarvisUi.spacer(this, 16))
            col.addView(
                JarvisUi.ghost(this, "COPY ALL") { copy(records.joinToString("\n\n---\n\n") { it.toText() }) },
                matchWidth()
            )
            col.addView(
                JarvisUi.ghost(this, "CLEAR") { clearAll() },
                matchWidth().apply { topMargin = JarvisUi.dp(this@CrashLogActivity, 8) }
            )
        }
        col.addView(JarvisUi.spacer(this, 24))
        replaceContent(col)
    }

    private fun rowFor(record: CrashRecord): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        val p = JarvisUi.dp(this@CrashLogActivity, 12)
        setPadding(p, p, p, p)
        background = JarvisUi.panel(this@CrashLogActivity)
        addView(
            TextView(this@CrashLogActivity).apply {
                text = record.headline()
                setTextColor(JarvisUi.DENY)
                textSize = JarvisUi.Type.MONO
                typeface = android.graphics.Typeface.MONOSPACE
            }
        )
        addView(
            TextView(this@CrashLogActivity).apply {
                text = "${formatTime(record.timestamp)}  ·  ${record.thread}  ·  " +
                    "v${record.appVersion}  ·  Android ${record.androidVersion}"
                setTextColor(JarvisUi.FAINT)
                textSize = JarvisUi.Type.LABEL
                typeface = android.graphics.Typeface.MONOSPACE
                setPadding(0, JarvisUi.dp(this@CrashLogActivity, 4), 0, 0)
            }
        )
        setOnClickListener { showRecord(record) }
    }

    // --- one report ---------------------------------------------------------

    private fun showRecord(record: CrashRecord) {
        showing = record
        val col = JarvisUi.column(this, padDp = 20)
        col.addView(JarvisUi.title(this, "CRASH"))
        col.addView(
            TextView(this).apply {
                text = formatTime(record.timestamp)
                setTextColor(JarvisUi.DIM)
                textSize = JarvisUi.Type.HINT
                gravity = Gravity.CENTER
                typeface = android.graphics.Typeface.MONOSPACE
            }
        )
        col.addView(JarvisUi.spacer(this, 12))
        col.addView(JarvisUi.mono(this, record.toText()), matchWidth())
        col.addView(JarvisUi.spacer(this, 16))
        col.addView(JarvisUi.ghost(this, "COPY") { copy(record.toText()) }, matchWidth())
        col.addView(
            JarvisUi.ghost(this, "BACK TO LIST") {
                showing = null
                showList()
            },
            matchWidth().apply { topMargin = JarvisUi.dp(this@CrashLogActivity, 8) }
        )
        col.addView(JarvisUi.spacer(this, 24))
        replaceContent(col)
    }

    // --- plumbing -----------------------------------------------------------

    private fun replaceContent(col: LinearLayout) {
        root.removeAllViews()
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
    }

    private fun clearAll() {
        val ok = JarvisCrashHandler.clear(this)
        toast(if (ok) "Crash log cleared" else "Could not clear the crash log")
        showing = null
        showList()
    }

    private fun copy(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        if (cm == null) {
            toast("No clipboard on this device")
            return
        }
        try {
            val clip = ClipData.newPlainText("Jarvis crash", text)
            // Android 13+ shows a preview of whatever is copied, and a system
            // clipboard entry is readable by the OS UI. A stack trace is
            // already redacted (see JarvisCrashHandler.redact) but it is still
            // diagnostic detail about this device; flagging it sensitive keeps
            // it out of the preview toast.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                clip.description.extras = PersistableBundle().apply {
                    putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
                }
            }
            cm.setPrimaryClip(clip)
            toast("Copied ${text.length} characters")
        } catch (t: Throwable) {
            toast("Could not copy: ${t.javaClass.simpleName}")
        }
    }

    private fun formatTime(ts: Long): String = try {
        DateFormat.format("yyyy-MM-dd HH:mm:ss", ts).toString()
    } catch (t: Throwable) {
        ts.toString()
    }

    private fun matchWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
}
