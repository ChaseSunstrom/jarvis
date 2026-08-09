package ai.jarvis.app.automation.actions.builtin

import android.content.Context
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.longOr
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.util.concurrent.TimeUnit

/**
 * Shell, via Shizuku only. Tier 3, always.
 *
 * There is no root here and none is claimed. Shizuku runs a service with the
 * ADB shell's identity (uid 2000) after the user has explicitly started it, so
 * this gets `adb shell`-level reach and nothing more — `pm`, `am`, `settings`,
 * `dumpsys`, `svc wifi`, and so on.
 *
 * Shizuku is an OPTIONAL dependency: the whole API is reached by reflection, so
 * the app builds and runs identically on a phone that has never heard of it,
 * and this action simply reports itself unsupported.
 */
object RunShell : JarvisAction {
    override val id = "run_shell"
    override val tier = ActionTier.CONFIRM
    override val description =
        "Run a shell command with ADB-level privileges through Shizuku (no root)."
    override val paramsSchema = mapOf(
        "command" to "string: the command line, run with sh -c",
        "args" to "array of strings (optional): exec form, used instead of command",
        "timeout_ms" to "int: kill the command after this long (default 30000, max 60000)"
    )
    override val capability = "shell"
    override val timeoutMs = 70_000L

    /** Command output quotes logs, files and other apps' data verbatim. */
    override val untrustedOutput = true

    override val unsupportedReason: String
        get() = "Shizuku is not available on this phone. Install and start Shizuku " +
            "(https://shizuku.rikka.app), grant Jarvis its permission, and retry. " +
            "Jarvis never asks for root."

    override fun isAvailable(ctx: Context): Boolean = Shizuku.isPresent()

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            if (!Shizuku.isPresent()) return@withContext ActionResult.unsupported(unsupportedReason)
            if (!Shizuku.pingBinder()) {
                return@withContext ActionResult.error(
                    "Shizuku is installed but its service is not running; start it and retry"
                )
            }
            if (!Shizuku.hasPermission()) {
                return@withContext ActionResult.error(
                    "Shizuku permission not granted to Jarvis; grant it in the Shizuku app"
                )
            }

            val argv: Array<String> = params.optJSONArray("args")?.let { arr ->
                Array(arr.length()) { i -> arr.optString(i) }
            } ?: params.str("command")?.let { arrayOf("sh", "-c", it) }
            ?: return@withContext ActionResult.error("command or args is required")
            if (argv.isEmpty() || argv.any { it.isEmpty() }) {
                return@withContext ActionResult.error("empty command")
            }

            val waitMs = params.longOr("timeout_ms", 30_000L).coerceIn(1_000L, 60_000L)
            val process = try {
                Shizuku.newProcess(argv)
            } catch (t: Throwable) {
                return@withContext ActionResult.error(
                    "Shizuku refused to start the process: ${t.message ?: t.javaClass.simpleName}"
                )
            } ?: return@withContext ActionResult.error("Shizuku returned no process")

            // Both pipes are drained on their own daemon threads, and the
            // deadline is enforced by waitFor rather than by how long a read
            // happens to take.
            //
            // Reading stdout to EOF on this thread first would have made
            // `timeout_ms` unenforceable for every command that holds its
            // stdout open — `logcat`, `top`, a tail — because a blocking
            // InputStream.read does not answer to coroutine cancellation
            // either, so the dispatcher's own withTimeout could not have
            // rescued it. destroyForcibly() closes the pipes, which is what
            // unblocks the readers.
            return@withContext try {
                val stdout = Drain(process.inputStream)
                val stderr = Drain(process.errorStream)
                stdout.start()
                stderr.start()

                val finished = process.waitFor(waitMs, TimeUnit.MILLISECONDS)
                if (!finished) {
                    runCatching { process.destroyForcibly() }
                    stdout.join(DRAIN_JOIN_MS)
                    stderr.join(DRAIN_JOIN_MS)
                    return@withContext ActionResult.error(
                        "command timed out after $waitMs ms and was killed"
                    )
                }
                stdout.join(DRAIN_JOIN_MS)
                stderr.join(DRAIN_JOIN_MS)
                ActionResult.ok(
                    json(
                        "exit_code" to process.exitValue(),
                        "stdout" to stdout.text(),
                        "stderr" to stderr.text(),
                        "truncated" to (stdout.truncated || stderr.truncated),
                        "argv" to JSONArray(argv.toList()),
                        "via" to "shizuku (adb shell privileges, not root)"
                    ).markUntrusted()
                )
            } catch (t: Throwable) {
                runCatching { process.destroyForcibly() }
                ActionResult.error("shell failed: ${t.message ?: t.javaClass.simpleName}")
            }
        }
}

private const val MAX_OUTPUT_CHARS = 64 * 1024

/** How long to wait for a drain thread after the process is done or killed. */
private const val DRAIN_JOIN_MS = 2_000L

/**
 * Reads one of the process's pipes on a daemon thread, keeping at most
 * [MAX_OUTPUT_CHARS] characters.
 *
 * Bounded on purpose: the old code accumulated the whole stream before
 * truncating, so `run_shell` with `cat /dev/urandom` or a large `dumpsys`
 * was an out-of-memory kill rather than a truncated result. Once the cap is
 * reached it keeps draining (so the child never blocks on a full pipe) but
 * stops keeping anything.
 *
 * Every touch of the buffer is inside `synchronized(buffer)`, so [text] is
 * safe to call even when the drain thread is still running — which happens
 * whenever the child forked something that inherited the pipe and is still
 * holding it open. Once per 8 KiB chunk, the lock costs nothing.
 */
private class Drain(private val stream: InputStream) {
    private val buffer = StringBuilder()
    private var overflowed = false
    private val thread = Thread {
        runCatching {
            stream.bufferedReader().use { reader ->
                val chunk = CharArray(8 * 1024)
                while (true) {
                    val n = reader.read(chunk)
                    if (n < 0) break
                    synchronized(buffer) {
                        val room = MAX_OUTPUT_CHARS - buffer.length
                        if (room > 0) buffer.append(chunk, 0, minOf(n, room))
                        if (n > room) overflowed = true
                    }
                }
            }
        }
    }

    fun start() {
        thread.isDaemon = true
        thread.start()
    }

    fun join(millis: Long) {
        runCatching { thread.join(millis) }
    }

    /** True when output was dropped, or the pipe is still open. */
    val truncated: Boolean
        get() = synchronized(buffer) { overflowed } || thread.isAlive

    /** What has been captured so far. */
    fun text(): String = synchronized(buffer) { buffer.toString() }
}

/**
 * Reflective wrapper around `rikka.shizuku.Shizuku`.
 *
 * Reflection rather than a compile-time dependency for two reasons: Shizuku is
 * genuinely optional, and `newProcess` is a restricted API that is not part of
 * the published surface. Everything fails closed — any reflective error is
 * reported as "not available" rather than swallowed as success.
 */
internal object Shizuku {

    private const val CLASS_NAME = "rikka.shizuku.Shizuku"

    private val clazz: Class<*>? by lazy {
        try {
            Class.forName(CLASS_NAME)
        } catch (t: Throwable) {
            null
        }
    }

    fun isPresent(): Boolean = clazz != null

    fun pingBinder(): Boolean = try {
        clazz?.getMethod("pingBinder")?.invoke(null) as? Boolean ?: false
    } catch (t: Throwable) {
        false
    }

    fun hasPermission(): Boolean = try {
        // 0 == PackageManager.PERMISSION_GRANTED
        (clazz?.getMethod("checkSelfPermission")?.invoke(null) as? Int) == 0
    } catch (t: Throwable) {
        false
    }

    /** `Shizuku.newProcess(String[] cmd, String[] env, String dir)` — @RestrictedApi. */
    fun newProcess(argv: Array<String>): Process? {
        val c = clazz ?: return null
        val method = c.getDeclaredMethod(
            "newProcess",
            Array<String>::class.java,
            Array<String>::class.java,
            String::class.java
        )
        method.isAccessible = true
        return method.invoke(null, argv, null, null) as? Process
    }
}
