package ai.jarvis.app.crash

import ai.jarvis.app.channel.Redact
import android.content.Context
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter

/**
 * One recorded crash, as it appears on disk and in Settings.
 *
 * Everything here is what the OS or the app itself knows about the failure —
 * no user content is collected deliberately. A stack trace can still quote a
 * message that came from somewhere, so [stack] is truncated rather than
 * unbounded, and the file lives in app-private storage and is excluded from
 * backups along with the rest of `filesDir`.
 *
 * [message] and [stack] are run through [Redact.text] before they are stored.
 * That is not belt-and-braces: this screen's whole purpose is a COPY button, so
 * a crash report is the one diagnostic in the app that is *expected* to leave
 * the device, and an exception thrown out of OkHttp or a JSON parser routinely
 * quotes the frame or the URL that failed. The bearer token is the entire
 * authentication story for this device; it does not travel in a bug report.
 */
data class CrashRecord(
    /** Wall-clock epoch millis at the moment the handler ran. */
    val timestamp: Long,
    /** Name of the thread that died. */
    val thread: String,
    /** Fully-qualified exception class, e.g. `java.lang.NullPointerException`. */
    val exception: String,
    /** `getMessage()`, or empty. */
    val message: String,
    /** The full printed stack trace, truncated to [MAX_STACK_CHARS]. */
    val stack: String,
    val appVersion: String,
    val appVersionCode: Long,
    /** Android release, e.g. "15". */
    val androidVersion: String,
    val sdkInt: Int,
    /** Manufacturer and model. */
    val device: String,
    /** Build fingerprint — how you tell a GrapheneOS build from a stock one. */
    val fingerprint: String,
) {

    /** The one-line summary the list screen shows. */
    fun headline(): String {
        val simple = exception.substringAfterLast('.')
        return if (message.isEmpty()) simple else "$simple: ${message.take(120)}"
    }

    fun toJson(): JSONObject = JSONObject()
        .put("ts", timestamp)
        .put("thread", thread)
        .put("exception", exception)
        .put("message", message)
        .put("stack", stack)
        .put("app_version", appVersion)
        .put("app_version_code", appVersionCode)
        .put("android_version", androidVersion)
        .put("sdk_int", sdkInt)
        .put("device", device)
        .put("fingerprint", fingerprint)

    /** The whole record as plain text, for the clipboard and for a bug report. */
    fun toText(): String = buildString {
        append("Jarvis crash report\n")
        append("time            ").append(java.util.Date(timestamp)).append('\n')
        append("app             ").append(appVersion).append(" (").append(appVersionCode).append(")\n")
        append("android         ").append(androidVersion).append(" (SDK ").append(sdkInt).append(")\n")
        append("device          ").append(device).append('\n')
        append("build           ").append(fingerprint).append('\n')
        append("thread          ").append(thread).append('\n')
        append("exception       ").append(exception).append('\n')
        if (message.isNotEmpty()) append("message         ").append(message).append('\n')
        append('\n')
        append(stack)
    }

    companion object {
        /** Long enough for any real trace, short enough not to fill the disk. */
        const val MAX_STACK_CHARS = 12_000

        fun fromJson(o: JSONObject): CrashRecord = CrashRecord(
            timestamp = o.optLong("ts"),
            thread = o.optString("thread"),
            exception = o.optString("exception").ifEmpty { "UnknownThrowable" },
            message = o.optString("message"),
            stack = o.optString("stack"),
            appVersion = o.optString("app_version"),
            appVersionCode = o.optLong("app_version_code"),
            androidVersion = o.optString("android_version"),
            sdkInt = o.optInt("sdk_int"),
            device = o.optString("device"),
            fingerprint = o.optString("fingerprint"),
        )
    }
}

/**
 * A global uncaught-exception handler that writes the crash somewhere the user
 * can read it **on the phone**, then hands the exception straight back to
 * whoever had the job before.
 *
 * This exists because of GrapheneOS. The app it replaces crashed there
 * constantly, and every diagnosis needed a laptop, a USB cable and a logcat
 * buffer that had usually already rolled over. Writing the trace to app storage
 * turns "it just closes" into a screen the user can read and paste.
 *
 * Three properties matter, in this order:
 *
 *  1. **It is installed first.** [install] is the first statement in
 *     `JarvisApp.onCreate`, before notification channels, before any config
 *     read — otherwise the crashes most worth catching (the ones during
 *     startup) are exactly the ones it misses.
 *  2. **It never becomes the crash.** Every step is wrapped. A handler that
 *     throws while handling replaces a diagnosable failure with a mysterious
 *     one, and on a hardened OS the difference between those is a week.
 *  3. **It always delegates.** The previous handler — normally the platform's,
 *     which is what actually kills the process and writes the tombstone — is
 *     always called afterwards. Swallowing the exception would leave a process
 *     alive with a dead thread and no window, which is worse than a crash.
 *
 * Storage is a rotating JSONL file, `filesDir/jarvis/crashes.jsonl`, one JSON
 * object per line, newest last. Same shape as the audit log, for the same
 * reason: any file viewer can read it and a corrupt line costs one entry.
 */
object JarvisCrashHandler : Thread.UncaughtExceptionHandler {

    private const val TAG = "JarvisCrash"

    /** Directory under `filesDir`, shared with the audit log. */
    const val DIR_NAME = "jarvis"

    /** One JSON object per line, newest last. */
    const val FILE_NAME = "crashes.jsonl"

    /** Older entries are dropped once the file passes this many lines. */
    const val MAX_RECORDS = 50

    /** Hard ceiling on the file, whatever the line count says. */
    const val MAX_FILE_BYTES = 512L * 1024L

    @Volatile
    private var previous: Thread.UncaughtExceptionHandler? = null

    @Volatile
    private var appContext: Context? = null

    @Volatile
    private var appVersion: String = "?"

    @Volatile
    private var appVersionCode: Long = 0

    @Volatile
    private var installed = false

    /**
     * Install the handler. Idempotent; call it as the first thing in
     * `Application.onCreate`.
     *
     * Returns true if this call installed it. Never throws: if it cannot be
     * installed, the app runs exactly as it would have without it.
     */
    @JvmStatic
    fun install(context: Context): Boolean {
        if (installed) return false
        return try {
            appContext = context.applicationContext
            readVersion(context)
            previous = Thread.getDefaultUncaughtExceptionHandler()
            Thread.setDefaultUncaughtExceptionHandler(this)
            installed = true
            true
        } catch (t: Throwable) {
            Log.w(TAG, "could not install crash handler", t)
            false
        }
    }

    override fun uncaughtException(thread: Thread, throwable: Throwable) {
        // Record first, delegate second, and never let the recording be the
        // reason the delegate does not run.
        try {
            appContext?.let { write(it, record(thread, throwable)) }
        } catch (t: Throwable) {
            Log.w(TAG, "failed to record crash", t)
        } finally {
            val next = previous
            if (next != null) {
                next.uncaughtException(thread, throwable)
            } else {
                // No previous handler is unusual but possible. Do not return
                // normally from here: a process with a dead main thread and no
                // handler is a frozen app, which is worse than a crash.
                Log.e(TAG, "uncaught, no delegate", throwable)
                android.os.Process.killProcess(android.os.Process.myPid())
            }
        }
    }

    // --- reading ------------------------------------------------------------

    /**
     * Recorded crashes, newest first, at most [limit]. A missing file, an
     * unreadable one, or a half-written last line all yield what could be read
     * — this is a diagnostic screen and it must open even when things are bad.
     */
    @JvmStatic
    fun recent(context: Context, limit: Int = MAX_RECORDS): List<CrashRecord> = try {
        val f = file(context)
        if (!f.isFile) {
            emptyList()
        } else {
            f.readLines()
                .asReversed()
                .asSequence()
                .mapNotNull { line ->
                    val trimmed = line.trim()
                    if (trimmed.isEmpty()) null
                    else runCatching { CrashRecord.fromJson(JSONObject(trimmed)) }.getOrNull()
                }
                .take(limit)
                .toList()
        }
    } catch (t: Throwable) {
        Log.w(TAG, "could not read crash log", t)
        emptyList()
    }

    /** True when there is at least one recorded crash. */
    @JvmStatic
    fun hasRecords(context: Context): Boolean =
        try { file(context).length() > 0L } catch (t: Throwable) { false }

    /** Delete every recorded crash. Returns true if the log is gone afterwards. */
    @JvmStatic
    fun clear(context: Context): Boolean = try {
        val f = file(context)
        !f.exists() || f.delete()
    } catch (t: Throwable) {
        Log.w(TAG, "could not clear crash log", t)
        false
    }

    /** The log file. Exposed so Settings can offer to share or delete it. */
    @JvmStatic
    fun file(context: Context): File =
        File(File(context.applicationContext.filesDir, DIR_NAME), FILE_NAME)

    // --- writing ------------------------------------------------------------

    /**
     * Build the record. Every field is defensive: this runs on a thread that is
     * already dying, in a process that may be out of memory.
     */
    internal fun record(thread: Thread, throwable: Throwable): CrashRecord {
        val stack = try {
            val sw = StringWriter()
            PrintWriter(sw).use { throwable.printStackTrace(it) }
            // Truncate first, then redact: the mask is applied to exactly the
            // text that will be stored, and the regex work stays bounded by
            // MAX_STACK_CHARS however large the trace was.
            redact(sw.toString().take(CrashRecord.MAX_STACK_CHARS))
        } catch (t: Throwable) {
            "(stack trace unavailable: ${t.javaClass.name})"
        }
        return CrashRecord(
            timestamp = System.currentTimeMillis(),
            thread = safe { thread.name } ?: "?",
            exception = safe { throwable.javaClass.name } ?: "java.lang.Throwable",
            message = redact(safe { throwable.message } ?: ""),
            stack = stack,
            appVersion = appVersion,
            appVersionCode = appVersionCode,
            androidVersion = safe { Build.VERSION.RELEASE } ?: "?",
            sdkInt = Build.VERSION.SDK_INT,
            device = "${safe { Build.MANUFACTURER } ?: "?"} ${safe { Build.MODEL } ?: "?"}",
            fingerprint = safe { Build.FINGERPRINT } ?: "?",
        )
    }

    /** Append one record and rotate. Swallows its own failures by design. */
    internal fun write(context: Context, record: CrashRecord) {
        val f = file(context)
        val dir = f.parentFile
        if (dir != null && !dir.exists() && !dir.mkdirs()) {
            Log.w(TAG, "could not create ${dir.path}")
            return
        }
        // One line, no embedded newlines: the stack trace's own newlines are
        // escaped by JSONObject, which is exactly why the format is JSONL.
        f.appendText(record.toJson().toString() + "\n")
        rotate(f)
    }

    /**
     * Keep the newest [MAX_RECORDS] lines, and stay under [MAX_FILE_BYTES]
     * whatever they contain. Rewrite in place via a temp file so a kill halfway
     * through leaves either the old log or the new one, never a shredded mix.
     */
    private fun rotate(f: File) {
        try {
            if (f.length() <= MAX_FILE_BYTES) {
                val lines = f.readLines()
                if (lines.size <= MAX_RECORDS) return
                writeAtomic(f, lines.takeLast(MAX_RECORDS))
                return
            }
            var kept = f.readLines().takeLast(MAX_RECORDS)
            while (kept.size > 1 && kept.sumOf { it.length + 1 } > MAX_FILE_BYTES) {
                kept = kept.drop(1)
            }
            writeAtomic(f, kept)
        } catch (t: Throwable) {
            Log.w(TAG, "crash log rotation failed", t)
        }
    }

    private fun writeAtomic(f: File, lines: List<String>) {
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(lines.joinToString("\n", postfix = "\n"))
        if (!tmp.renameTo(f)) {
            f.writeText(lines.joinToString("\n", postfix = "\n"))
            tmp.delete()
        }
    }

    private fun readVersion(context: Context) {
        try {
            val pm = context.packageManager ?: return
            @Suppress("DEPRECATION")
            val info = pm.getPackageInfo(context.packageName, 0)
            appVersion = info.versionName ?: "?"
            appVersionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                info.longVersionCode
            } else {
                @Suppress("DEPRECATION")
                info.versionCode.toLong()
            }
        } catch (t: Throwable) {
            // A package manager that cannot describe our own package is not
            // worth crashing over; the record just says "?".
            Log.w(TAG, "could not read package info", t)
        }
    }

    /**
     * Mask anything credential-shaped before it is written down.
     *
     * Runs on a thread that is already dying, so it cannot be allowed to throw:
     * a regex that somehow blows up must cost the *text*, not the record.
     */
    private fun redact(value: String): String = try {
        Redact.text(value)
    } catch (t: Throwable) {
        "(redaction failed: ${t.javaClass.name})"
    }

    /** Evaluate a platform getter that is typed non-null but is not. */
    private inline fun <T> safe(block: () -> T?): T? = try {
        block()
    } catch (t: Throwable) {
        null
    }
}
