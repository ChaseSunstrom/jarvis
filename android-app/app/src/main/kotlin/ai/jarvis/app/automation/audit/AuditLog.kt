package ai.jarvis.app.automation.audit

import android.content.Context
import android.util.Log
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.Decision
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * One line of the local, append-only, user-viewable audit log.
 *
 * Every dispatch writes exactly one of these — allowed, asked-and-approved,
 * asked-and-denied, denied outright, unsupported or crashed. If it is not in
 * here, it did not run.
 */
data class AuditEntry(
    /** Wall-clock epoch millis. */
    val timestamp: Long,
    val actionId: String,
    /** RAW params. [AuditLog.record] redacts before anything touches disk. */
    val params: JSONObject?,
    /** The tier we actually enforced (max of local and requested). */
    val tier: ActionTier,
    val decision: Decision,
    /** Wire status finally returned: ok | denied | error | unsupported. */
    val status: String,
    val ok: Boolean,
    val error: String? = null,
    /** Who asked: "server", "user", "trigger", … */
    val source: String = "server",
    /** `command_id` from the device_command, when there was one. */
    val commandId: String? = null,
    val durationMs: Long = 0,
    /** Free-text policy explanation, e.g. "raised by server, policy=ASK". */
    val note: String? = null
) {
    /** Serialised form. `params` is redacted here and only here. */
    internal fun toJson(): JSONObject {
        val out = JSONObject()
            .put("ts", timestamp)
            .put("action", actionId)
            .put("params", ParamRedaction.redact(params))
            .put("tier", tier.name)
            .put("decision", decision.name)
            .put("status", status)
            .put("ok", ok)
            .put("source", source)
            .put("duration_ms", durationMs)
        error?.let { out.put("error", Redactor.truncate(it)) }
        commandId?.let { out.put("command_id", it) }
        note?.let { out.put("note", Redactor.truncate(it)) }
        return out
    }

    companion object {
        internal fun fromJson(o: JSONObject): AuditEntry = AuditEntry(
            timestamp = o.optLong("ts"),
            actionId = o.optString("action"),
            params = o.optJSONObject("params"),
            tier = ActionTier.fromName(o.optString("tier")) ?: ActionTier.CONFIRM,
            decision = runCatching { Decision.valueOf(o.optString("decision")) }
                .getOrDefault(Decision.DENY),
            status = o.optString("status"),
            ok = o.optBoolean("ok"),
            error = o.optString("error").ifEmpty { null },
            source = o.optString("source").ifEmpty { "server" },
            commandId = o.optString("command_id").ifEmpty { null },
            durationMs = o.optLong("duration_ms"),
            note = o.optString("note").ifEmpty { null }
        )
    }
}

/**
 * Append-only JSONL audit log in app-private storage
 * (`filesDir/jarvis/audit.jsonl`), capped and rotated in place.
 *
 * Deliberately boring: one line of JSON per action, no database, no index, so
 * the user can read it with any file viewer and so a corrupt line costs one
 * entry rather than the whole history. Writes are serialised by a [Mutex] and
 * always happen off the main thread.
 *
 * Nothing here can fail a dispatch: every operation swallows its own I/O
 * errors after logging them. An audit write must never be the reason an action
 * does or does not run — but note the dispatcher records BEFORE returning, so
 * the ordering "executed then recorded" is preserved.
 */
class AuditLog(
    context: Context,
    private val maxEntries: Int = DEFAULT_MAX_ENTRIES
) {

    private val dir = File(context.applicationContext.filesDir, "jarvis")
    private val file = File(dir, FILE_NAME)
    private val mutex = Mutex()

    /** -1 = not counted yet. */
    private var lineCount: Int = -1

    /** The file the settings screen can offer to export. */
    fun file(): File = file

    suspend fun record(entry: AuditEntry) {
        withContext(Dispatchers.IO) {
            mutex.withLock {
                try {
                    if (!dir.exists()) dir.mkdirs()
                    val line = entry.toJson().toString()
                    file.appendText(line + "\n")
                    if (lineCount < 0) lineCount = countLines()
                    lineCount += 1
                    if (lineCount >= maxEntries + ROTATE_SLACK) compactLocked()
                } catch (t: Throwable) {
                    // Never let bookkeeping break the action path.
                    Log.w(TAG, "audit write failed for ${entry.actionId}", t)
                }
            }
        }
    }

    /**
     * Newest first by default — that is what a log screen wants.
     * [limit] caps how many entries are materialised.
     */
    suspend fun read(limit: Int = 200, newestFirst: Boolean = true): List<AuditEntry> =
        withContext(Dispatchers.IO) {
            mutex.withLock {
                try {
                    if (!file.exists()) return@withLock emptyList<AuditEntry>()
                    val all = ArrayList<AuditEntry>()
                    file.forEachLine { raw ->
                        val line = raw.trim()
                        if (line.isNotEmpty()) {
                            runCatching { AuditEntry.fromJson(JSONObject(line)) }
                                .getOrNull()?.let { all.add(it) }
                        }
                    }
                    val ordered = if (newestFirst) all.asReversed() else all
                    if (ordered.size > limit) ordered.take(limit) else ordered.toList()
                } catch (t: Throwable) {
                    Log.w(TAG, "audit read failed", t)
                    emptyList<AuditEntry>()
                }
            }
        }

    /** Same as [read] but already serialised, for a WebView/list adapter. */
    suspend fun readJson(limit: Int = 200): JSONArray {
        val arr = JSONArray()
        for (e in read(limit)) arr.put(e.toJson())
        return arr
    }

    suspend fun count(): Int = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (lineCount < 0) lineCount = countLines()
            lineCount
        }
    }

    /** User-initiated wipe. */
    suspend fun clear() {
        withContext(Dispatchers.IO) {
            mutex.withLock {
                runCatching { if (file.exists()) file.delete() }
                    .onFailure { Log.w(TAG, "audit clear failed", it) }
                lineCount = 0
            }
        }
    }

    // --- internals ----------------------------------------------------------

    private fun countLines(): Int {
        if (!file.exists()) return 0
        var n = 0
        return try {
            file.forEachLine { if (it.isNotBlank()) n++ }
            n
        } catch (t: Throwable) {
            Log.w(TAG, "audit count failed", t)
            0
        }
    }

    /** Keep the newest [maxEntries] lines. Caller holds the mutex. */
    private fun compactLocked() {
        try {
            val lines = file.readLines().filter { it.isNotBlank() }
            val keep = if (lines.size > maxEntries) lines.subList(lines.size - maxEntries, lines.size) else lines
            val tmp = File(dir, "$FILE_NAME.tmp")
            tmp.writeText(keep.joinToString("\n", postfix = "\n"))
            if (tmp.renameTo(file)) {
                lineCount = keep.size
            } else {
                file.writeText(keep.joinToString("\n", postfix = "\n"))
                tmp.delete()
                lineCount = keep.size
            }
        } catch (t: Throwable) {
            Log.w(TAG, "audit rotate failed", t)
        }
    }

    companion object {
        private const val TAG = "JarvisAudit"
        private const val FILE_NAME = "audit.jsonl"
        const val DEFAULT_MAX_ENTRIES = 5000
        private const val ROTATE_SLACK = 250
    }
}

/** Walks a params object and applies [Redactor] to every leaf. */
internal object ParamRedaction {

    fun redact(params: JSONObject?): JSONObject {
        if (params == null) return JSONObject()
        return redactObject(params, 0)
    }

    private fun redactObject(o: JSONObject, depth: Int): JSONObject {
        val out = JSONObject()
        if (depth > MAX_DEPTH) return out.put("_", "[too deep]")
        val keys = o.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            out.put(k, redactValue(k, o.opt(k), depth))
        }
        return out
    }

    private fun redactValue(key: String, value: Any?, depth: Int): Any = when {
        value == null || value === JSONObject.NULL -> JSONObject.NULL
        Redactor.isSecretKey(key) -> Redactor.MASK
        value is JSONObject -> redactObject(value, depth + 1)
        value is JSONArray -> redactArray(key, value, depth + 1)
        value is String -> Redactor.truncate(value)
        else -> value
    }

    private fun redactArray(key: String, arr: JSONArray, depth: Int): JSONArray {
        val out = JSONArray()
        if (depth > MAX_DEPTH) return out
        val n = minOf(arr.length(), Redactor.MAX_ARRAY_ITEMS)
        for (i in 0 until n) out.put(redactValue(key, arr.opt(i), depth))
        if (arr.length() > n) out.put("...(+${arr.length() - n} items)")
        return out
    }

    private const val MAX_DEPTH = 6
}
