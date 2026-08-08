package ai.jarvis.app.automation.actions

/**
 * PURE LOGIC — no Android imports. Unit-testable on a plain JVM.
 *
 * File actions are confined to one directory inside app-private storage
 * (`filesDir/jarvis_files`). This object answers exactly one question: given a
 * caller-supplied relative path, what is the safe relative path inside that
 * directory — or why is it rejected?
 *
 * Traversal is resolved arithmetically (`..` pops a segment; popping past the
 * root is a rejection) rather than by string matching, because "does it contain
 * `..`" is not a security check. On top of this, [FileActions] independently
 * verifies the resolved `canonicalPath` still sits under the root, which is the
 * only way to catch a symlink pointing out of the sandbox.
 */
object PathScope {

    /** Directory name under `filesDir` that every file action is confined to. */
    const val ROOT_DIR_NAME = "jarvis_files"

    private const val MAX_PATH_CHARS = 512
    private const val MAX_SEGMENT_CHARS = 255

    sealed class Result {
        /** Cleaned, root-relative path using `/` separators. "" means the root itself. */
        data class Allowed(val relative: String) : Result()
        data class Rejected(val reason: String) : Result()
    }

    /**
     * Normalise a caller-supplied path.
     *
     * @param allowRoot when true, a path that resolves to the sandbox root
     *   itself is allowed (used by `list_files`); when false it is rejected
     *   (used by read/write/delete, which need an actual file).
     */
    fun normalize(raw: String?, allowRoot: Boolean = false): Result {
        val path = raw?.trim() ?: return Result.Rejected("path is required")
        if (path.isEmpty()) {
            return if (allowRoot) Result.Allowed("") else Result.Rejected("path is required")
        }
        if (path.length > MAX_PATH_CHARS) return Result.Rejected("path too long")
        if (path.indexOf('\u0000') >= 0) return Result.Rejected("path contains a null byte")
        if (path.contains('\\')) return Result.Rejected("backslashes are not allowed in paths")
        if (path.startsWith("~")) return Result.Rejected("home-relative paths are not allowed")

        // Percent-encoded separators and dots are never legitimate here and are
        // the classic way to smuggle traversal past a naive check.
        val lower = path.lowercase()
        if (lower.contains("%2e") || lower.contains("%2f") || lower.contains("%5c")) {
            return Result.Rejected("percent-encoded path segments are not allowed")
        }
        // Absolute paths, UNC paths and anything with a scheme are out.
        if (path.startsWith("/")) return Result.Rejected("absolute paths are not allowed")
        if (path.contains("://")) return Result.Rejected("only plain relative paths are allowed")
        if (Regex("^[A-Za-z]:").containsMatchIn(path)) {
            return Result.Rejected("absolute paths are not allowed")
        }

        val stack = ArrayList<String>()
        for (segment in path.split('/')) {
            when (segment) {
                "", "." -> continue
                ".." -> {
                    if (stack.isEmpty()) return Result.Rejected("path escapes the sandbox")
                    stack.removeAt(stack.size - 1)
                }
                else -> {
                    if (segment.length > MAX_SEGMENT_CHARS) {
                        return Result.Rejected("path segment too long")
                    }
                    stack.add(segment)
                }
            }
        }

        if (stack.isEmpty()) {
            return if (allowRoot) Result.Allowed("") else Result.Rejected("path resolves to the sandbox root")
        }
        return Result.Allowed(stack.joinToString("/"))
    }

    /** Convenience: the cleaned path, or null when rejected. */
    fun normalizedOrNull(raw: String?, allowRoot: Boolean = false): String? =
        (normalize(raw, allowRoot) as? Result.Allowed)?.relative
}
