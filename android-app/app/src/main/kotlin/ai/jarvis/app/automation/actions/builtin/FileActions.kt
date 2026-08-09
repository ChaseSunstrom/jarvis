package ai.jarvis.app.automation.actions.builtin

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Base64
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.PathScope
import ai.jarvis.app.automation.actions.boolOr
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Files and clipboard.
 *
 * Every file action is confined to ONE directory:
 * `filesDir/jarvis_files`. There is no parameter that reaches outside it, no
 * "absolute path" mode, and no SAF picker here. Two independent checks enforce
 * that:
 *
 *  1. [PathScope.normalize] — pure string/segment arithmetic, unit-tested.
 *  2. [FileSandbox.resolve] — canonical-path containment, which is the only
 *     thing that catches a symlink pointing out of the sandbox.
 *
 * Reads are Tier 1, writes Tier 2, deletes Tier 3. Contents that come back out
 * are marked untrusted — a file Jarvis downloaded earlier is not a trusted
 * instruction source.
 */
internal object FileSandbox {

    sealed class Resolved {
        data class Ok(val file: File, val relative: String) : Resolved()
        data class Err(val message: String) : Resolved()
    }

    fun root(ctx: Context): File {
        val dir = File(ctx.filesDir, PathScope.ROOT_DIR_NAME)
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    fun resolve(ctx: Context, raw: String?, allowRoot: Boolean = false): Resolved {
        val normalized = when (val r = PathScope.normalize(raw, allowRoot)) {
            is PathScope.Result.Rejected -> return Resolved.Err(r.reason)
            is PathScope.Result.Allowed -> r.relative
        }
        val root = root(ctx)
        return try {
            val rootCanonical = root.canonicalFile
            val target = if (normalized.isEmpty()) rootCanonical else File(root, normalized).canonicalFile
            val inside = target == rootCanonical ||
                target.path.startsWith(rootCanonical.path + File.separator)
            if (!inside) {
                Resolved.Err("path escapes the sandbox")
            } else {
                Resolved.Ok(target, normalized)
            }
        } catch (e: Exception) {
            Resolved.Err("could not resolve the path: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    const val MAX_WRITE_BYTES = 5 * 1024 * 1024
    const val DEFAULT_READ_BYTES = 256 * 1024
}

/** Tier 2 — writes into app storage. Recoverable, but say so. */
object WriteFile : JarvisAction {
    override val id = "write_file"
    override val tier = ActionTier.NOTIFY
    override val description = "Write a text file inside Jarvis's private storage."
    override val paramsSchema = mapOf(
        "path" to "string: relative path inside Jarvis storage, e.g. notes/todo.txt",
        "content" to "string: file contents",
        "append" to "bool (optional): append instead of replacing",
        "base64" to "bool (optional): content is base64 and should be written as bytes"
    )
    override val capability = "files"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val target = when (val r = FileSandbox.resolve(ctx, params.str("path"))) {
                is FileSandbox.Resolved.Err -> return@withContext ActionResult.error(r.message)
                is FileSandbox.Resolved.Ok -> r
            }
            val content = params.str("content") ?: params.optString("content", "")
            val append = params.boolOr("append", false)
            val asBytes = params.boolOr("base64", false)
            try {
                target.file.parentFile?.mkdirs()
                if (asBytes) {
                    val bytes = try {
                        Base64.decode(content, Base64.DEFAULT)
                    } catch (e: IllegalArgumentException) {
                        return@withContext ActionResult.error("content is not valid base64")
                    }
                    if (bytes.size > FileSandbox.MAX_WRITE_BYTES) {
                        return@withContext ActionResult.error("file too large (max 5 MiB)")
                    }
                    if (append) target.file.appendBytes(bytes) else target.file.writeBytes(bytes)
                } else {
                    if (content.toByteArray().size > FileSandbox.MAX_WRITE_BYTES) {
                        return@withContext ActionResult.error("file too large (max 5 MiB)")
                    }
                    if (append) target.file.appendText(content) else target.file.writeText(content)
                }
                ActionResult.ok(
                    json("path" to target.relative, "bytes" to target.file.length(), "appended" to append)
                )
            } catch (e: Exception) {
                ActionResult.error("write failed: ${e.message ?: e.javaClass.simpleName}")
            }
        }
}

/** Tier 1 — read-only, and only inside the sandbox. */
object ReadFile : JarvisAction {
    override val id = "read_file"
    override val tier = ActionTier.AUTO
    override val description = "Read a file from Jarvis's private storage."

    /** A file Jarvis downloaded earlier is not a trusted instruction source. */
    override val untrustedOutput = true
    override val paramsSchema = mapOf(
        "path" to "string: relative path inside Jarvis storage",
        "max_bytes" to "int: truncate after this many bytes (default 262144)",
        "base64" to "bool (optional): return base64 instead of text"
    )
    override val capability = "files"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val target = when (val r = FileSandbox.resolve(ctx, params.str("path"))) {
                is FileSandbox.Resolved.Err -> return@withContext ActionResult.error(r.message)
                is FileSandbox.Resolved.Ok -> r
            }
            if (!target.file.exists()) return@withContext ActionResult.error("no such file: ${target.relative}")
            if (target.file.isDirectory) return@withContext ActionResult.error("${target.relative} is a directory; use list_files")
            val max = params.intOr("max_bytes", FileSandbox.DEFAULT_READ_BYTES)
                .coerceIn(1, FileSandbox.MAX_WRITE_BYTES)
            return@withContext try {
                val all = target.file.readBytes()
                val bytes = if (all.size > max) all.copyOf(max) else all
                val payload = if (params.boolOr("base64", false)) {
                    json("base64" to Base64.encodeToString(bytes, Base64.NO_WRAP))
                } else {
                    json("content" to String(bytes))
                }
                ActionResult.ok(
                    payload
                        .put("path", target.relative)
                        .put("bytes", target.file.length())
                        .put("truncated", all.size > max)
                        .markUntrusted()
                )
            } catch (e: Exception) {
                ActionResult.error("read failed: ${e.message ?: e.javaClass.simpleName}")
            }
        }
}

/** Tier 1 — directory listing inside the sandbox. */
object ListFiles : JarvisAction {
    override val id = "list_files"
    override val tier = ActionTier.AUTO
    override val description = "List files in Jarvis's private storage."
    override val paramsSchema = mapOf(
        "path" to "string (optional): sub-directory, defaults to the storage root"
    )
    override val capability = "files"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val target = when (val r = FileSandbox.resolve(ctx, params.str("path") ?: "", allowRoot = true)) {
                is FileSandbox.Resolved.Err -> return@withContext ActionResult.error(r.message)
                is FileSandbox.Resolved.Ok -> r
            }
            if (!target.file.exists()) return@withContext ActionResult.error("no such directory: ${target.relative}")
            if (!target.file.isDirectory) return@withContext ActionResult.error("${target.relative} is a file")
            val entries = JSONArray()
            for (child in target.file.listFiles().orEmpty().sortedBy { it.name }) {
                entries.put(
                    json(
                        "name" to child.name,
                        "is_dir" to child.isDirectory,
                        "bytes" to child.length(),
                        "modified" to child.lastModified()
                    )
                )
            }
            ActionResult.ok(
                json("path" to target.relative, "entries" to entries, "count" to entries.length())
            )
        }
}

/** Tier 3 — deletion is not recoverable, so it confirms every time. */
object DeleteFile : JarvisAction {
    override val id = "delete_file"
    override val tier = ActionTier.CONFIRM
    override val description = "Delete a file from Jarvis's private storage."
    override val paramsSchema = mapOf(
        "path" to "string: relative path inside Jarvis storage",
        "recursive" to "bool (optional): also delete a non-empty directory"
    )
    override val capability = "files"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val target = when (val r = FileSandbox.resolve(ctx, params.str("path"))) {
                is FileSandbox.Resolved.Err -> return@withContext ActionResult.error(r.message)
                is FileSandbox.Resolved.Ok -> r
            }
            if (!target.file.exists()) return@withContext ActionResult.error("no such file: ${target.relative}")
            val recursive = params.boolOr("recursive", false)
            if (target.file.isDirectory && !recursive) {
                val empty = target.file.listFiles().orEmpty().isEmpty()
                if (!empty) {
                    return@withContext ActionResult.error(
                        "${target.relative} is not empty; pass recursive=true to delete it anyway"
                    )
                }
            }
            val deleted = try {
                if (target.file.isDirectory && recursive) target.file.deleteRecursively()
                else target.file.delete()
            } catch (e: Exception) {
                return@withContext ActionResult.error("delete failed: ${e.message ?: e.javaClass.simpleName}")
            }
            if (deleted) ActionResult.ok(json("path" to target.relative, "deleted" to true))
            else ActionResult.error("could not delete ${target.relative}")
        }
}

/**
 * Tier 2 — reading the clipboard can hoover up a password the user just
 * copied, so it asks. Android 10+ only hands the clipboard to an app that has
 * focus (or is the default IME / an accessibility service), which is why this
 * often fails with a clear message rather than data.
 */
object ReadClipboard : JarvisAction {
    override val id = "read_clipboard"
    override val tier = ActionTier.NOTIFY
    override val description = "Read the current clipboard text."

    /** Whatever was last copied, from any app, including a web page. */
    override val untrustedOutput = true
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "clipboard"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val cm = ctx.getSystemService(ClipboardManager::class.java)
            ?: return ActionResult.error("no clipboard service")
        return try {
            if (!cm.hasPrimaryClip()) return ActionResult.ok(json("text" to "", "empty" to true))
            val clip = cm.primaryClip
                ?: return ActionResult.error(
                    "Android 10+ only gives the clipboard to the focused app; bring Jarvis to the foreground and retry"
                )
            val text = (0 until clip.itemCount)
                .mapNotNull { clip.getItemAt(it)?.coerceToText(ctx)?.toString() }
                .joinToString("\n")
            ActionResult.ok(json("text" to text, "empty" to text.isEmpty()).markUntrusted())
        } catch (e: SecurityException) {
            ActionResult.error("the system refused clipboard access to a background app")
        }
    }
}

/** Tier 2 — replaces whatever the user had copied. */
object WriteClipboard : JarvisAction {
    override val id = "write_clipboard"
    override val tier = ActionTier.NOTIFY
    override val description = "Put text on the clipboard."
    override val paramsSchema = mapOf(
        "text" to "string: text to copy",
        "sensitive" to "bool (optional): hide it from the Android 13+ clipboard preview"
    )
    override val capability = "clipboard"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val text = params.str("text") ?: return ActionResult.error("text is required")
        val cm = ctx.getSystemService(ClipboardManager::class.java)
            ?: return ActionResult.error("no clipboard service")
        return try {
            val clip = ClipData.newPlainText("Jarvis", text)
            if (params.boolOr("sensitive", false)) {
                // Honoured from Android 13; ignored harmlessly before that.
                clip.description.extras = android.os.PersistableBundle().apply {
                    putBoolean("android.content.extra.IS_SENSITIVE", true)
                }
            }
            cm.setPrimaryClip(clip)
            ActionResult.ok(json("chars" to text.length))
        } catch (e: Exception) {
            ActionResult.error("clipboard write failed: ${e.message ?: e.javaClass.simpleName}")
        }
    }
}
