package ai.jarvis.app.automation.ui

import ai.jarvis.app.automation.AutomationRuntime
import ai.jarvis.app.automation.audit.AuditEntry
import ai.jarvis.app.automation.audit.AuditLog
import ai.jarvis.app.automation.policy.Decision
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.PersistableBundle
import android.text.format.DateFormat
import android.util.Log
import android.view.Gravity
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * The audit log, on the phone.
 *
 * Settings has promised for a long time that "the audit log records every action
 * this device actually executed, with its tier and how it was authorised", and
 * the button under that sentence toasted "not available in this build" because
 * this class did not exist — the activity was declared in the manifest and
 * launched by name from `ai.jarvis.app.ui.JarvisScreens`, and nothing was behind
 * the name. Every dispatch has been writing `filesDir/jarvis/audit.jsonl` all
 * along with no way to read it short of a cable.
 *
 * Two levels, following `ai.jarvis.app.ui.CrashLogActivity`: a list of lines,
 * and one full entry.
 *
 * Params are redacted by `ParamRedaction.redact` on the way to disk, before
 * anything is written. This screen shows what is in the file and makes no
 * attempt to recover the originals — there is nothing here to recover them
 * from, and that is the point.
 */
class AuditLogActivity : Activity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private lateinit var root: FrameLayout
    private var audit: AuditLog? = null

    /** The entry whose detail is open, or null for the list. */
    private var showing: AuditEntry? = null

    /** Second tap on CLEAR actually clears. */
    private var clearArmed = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }
        setContentView(root.also { JarvisUi.fitSystemBars(it) })

        audit = try {
            AutomationRuntime.ensure(applicationContext).audit
        } catch (t: Throwable) {
            // A log viewer that crashes on the way in is worse than an empty
            // one: this is the screen somebody opens when something has already
            // gone wrong.
            Log.w(TAG, "the automation runtime could not be built", t)
            null
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** See `CrashLogActivity.onBackPressed`: the manifest disables the new API. */
    @Suppress("DEPRECATION", "MissingSuperCall")
    override fun onBackPressed() {
        if (showing != null) {
            showing = null
            refresh()
            return
        }
        super.onBackPressed()
    }

    // --- rendering ----------------------------------------------------------

    private fun refresh() {
        val log = audit
        if (log == null) {
            replaceContent(unavailableColumn())
            return
        }
        val open = showing
        if (open != null) {
            replaceContent(detailColumn(open))
            return
        }
        scope.launch {
            // Already newest-first: that is what a log screen wants.
            val entries = runCatching { log.read(limit = READ_LIMIT) }
                .onFailure { Log.w(TAG, "could not read the audit log", it) }
                .getOrDefault(emptyList())
            val total = runCatching { log.count() }.getOrDefault(entries.size)
            replaceContent(listColumn(entries, total))
        }
    }

    private fun unavailableColumn(): LinearLayout {
        val col = JarvisUi.column(this, padDp = 20)
        col.addView(JarvisUi.title(this, "AUDIT LOG"))
        col.addView(
            JarvisUi.hint(
                this,
                "The automation runtime could not be started on this device, so there is " +
                    "no audit log to read."
            )
        )
        return col
    }

    private fun listColumn(entries: List<AuditEntry>, total: Int): LinearLayout {
        val col = JarvisUi.column(this, padDp = 20)
        col.addView(JarvisUi.title(this, "AUDIT LOG"))
        col.addView(
            JarvisUi.hint(
                this,
                "Every action this device executed, with the tier enforced and how it was " +
                    "authorised. Written to ${audit?.file()?.path}. App-private, excluded " +
                    "from backups, and never sent anywhere. Parameters are redacted before " +
                    "they are written."
            )
        )

        if (entries.isEmpty()) {
            col.addView(JarvisUi.spacer(this, 24))
            col.addView(
                TextView(this).apply {
                    text = "Nothing has run yet."
                    setTextColor(JarvisUi.FAINT)
                    textSize = JarvisUi.Type.BODY
                    gravity = Gravity.CENTER
                    typeface = Typeface.MONOSPACE
                }
            )
            col.addView(JarvisUi.spacer(this, 24))
            return col
        }

        col.addView(
            JarvisUi.label(
                this,
                if (total > entries.size) "${entries.size} of $total, newest first"
                else "$total recorded, newest first"
            )
        )
        for (entry in entries) {
            col.addView(
                rowFor(entry),
                matchWidth().apply { topMargin = JarvisUi.dp(this@AuditLogActivity, 8) }
            )
        }

        col.addView(JarvisUi.spacer(this, 16))
        col.addView(
            JarvisUi.button(this, "COPY ALL") {
                copy(entries.joinToString("\n") { oneLine(it) })
            },
            matchWidth()
        )
        col.addView(
            JarvisUi.button(this, if (clearArmed) "TAP AGAIN TO CLEAR" else "CLEAR") { clearAll() },
            matchWidth().apply { topMargin = JarvisUi.dp(this@AuditLogActivity, 8) }
        )
        col.addView(JarvisUi.spacer(this, 24))
        return col
    }

    private fun rowFor(entry: AuditEntry): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        val p = JarvisUi.dp(this@AuditLogActivity, 12)
        setPadding(p, p, p, p)
        background = JarvisUi.panel(this@AuditLogActivity)
        addView(
            TextView(this@AuditLogActivity).apply {
                text = "${entry.actionId}  ·  ${entry.status}"
                setTextColor(toneFor(entry))
                textSize = JarvisUi.Type.MONO
                typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            }
        )
        addView(
            TextView(this@AuditLogActivity).apply {
                text = "${formatTime(entry.timestamp)}  ·  ${entry.tier.name}  ·  " +
                    "${entry.decision.name}  ·  ${entry.source}  ·  ${entry.durationMs}ms"
                setTextColor(JarvisUi.FAINT)
                textSize = JarvisUi.Type.LABEL
                typeface = Typeface.MONOSPACE
                setPadding(0, JarvisUi.dp(this@AuditLogActivity, 4), 0, 0)
            }
        )
        setOnClickListener {
            showing = entry
            refresh()
        }
    }

    /** Green for what ran, red for what was refused, grey for everything else. */
    private fun toneFor(entry: AuditEntry): Int = when {
        entry.decision == Decision.DENY || entry.status == "denied" -> JarvisUi.DENY
        entry.ok -> JarvisUi.APPROVE
        else -> JarvisUi.FAINT
    }

    private fun detailColumn(entry: AuditEntry): LinearLayout {
        val col = JarvisUi.column(this, padDp = 20)
        col.addView(JarvisUi.title(this, "ENTRY"))
        col.addView(
            TextView(this).apply {
                text = formatTime(entry.timestamp)
                setTextColor(JarvisUi.DIM)
                textSize = JarvisUi.Type.HINT
                gravity = Gravity.CENTER
                typeface = Typeface.MONOSPACE
            }
        )
        col.addView(JarvisUi.spacer(this, 12))
        col.addView(JarvisUi.mono(this, detailText(entry)), matchWidth())
        col.addView(JarvisUi.spacer(this, 16))
        col.addView(JarvisUi.button(this, "COPY") { copy(detailText(entry)) }, matchWidth())
        col.addView(
            JarvisUi.button(this, "BACK TO LIST") {
                showing = null
                refresh()
            },
            matchWidth().apply { topMargin = JarvisUi.dp(this@AuditLogActivity, 8) }
        )
        col.addView(JarvisUi.spacer(this, 24))
        return col
    }

    private fun detailText(entry: AuditEntry): String = buildString {
        append("action:     ${entry.actionId}\n")
        append("when:       ${formatTime(entry.timestamp)}\n")
        append("tier:       ${entry.tier.name}\n")
        append("decision:   ${entry.decision.name}\n")
        append("status:     ${entry.status}\n")
        append("ok:         ${entry.ok}\n")
        append("source:     ${entry.source}\n")
        append("duration:   ${entry.durationMs}ms\n")
        entry.commandId?.let { append("command id: $it\n") }
        entry.note?.let { append("note:       $it\n") }
        entry.error?.let { append("error:      $it\n") }
        append("\nparams (already redacted on write):\n")
        append(
            entry.params?.let { runCatching { it.toString(2) }.getOrElse { _ -> it.toString() } }
                ?: "(none)"
        )
    }

    private fun oneLine(entry: AuditEntry): String =
        "${formatTime(entry.timestamp)}  ${entry.actionId}  ${entry.tier.name}  " +
            "${entry.decision.name}  ${entry.status}  ${entry.durationMs}ms  ${entry.source}"

    // --- actions ------------------------------------------------------------

    private fun clearAll() {
        if (!clearArmed) {
            clearArmed = true
            toast("Tap CLEAR again to wipe the audit log")
            refresh()
            return
        }
        clearArmed = false
        val log = audit ?: return
        scope.launch {
            runCatching { log.clear() }.onFailure { Log.w(TAG, "audit clear failed", it) }
            showing = null
            toast("Audit log cleared")
            refresh()
        }
    }

    private fun copy(text: String) {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        if (cm == null) {
            toast("No clipboard on this device")
            return
        }
        try {
            val clip = ClipData.newPlainText("Jarvis audit log", text)
            // Android 13+ previews whatever is copied, and a system clipboard
            // entry is readable by the OS UI. These lines are already redacted
            // but they are still a record of what this phone did; flagging them
            // sensitive keeps them out of the preview.
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

    private companion object {
        private const val TAG = "JarvisAuditUi"

        /** How many entries to materialise. The file itself is capped too. */
        private const val READ_LIMIT = 200
    }
}
