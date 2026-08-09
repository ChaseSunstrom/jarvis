package ai.jarvis.app.automation.tasks

/**
 * PURE LOGIC — no Android imports, no org.json, no I/O.
 *
 * `{{var}}` expansion for task steps. Mirrored by the executable spec at
 * `android-app/tools/task_vars_test.py`:
 *
 *     python3 android-app/tools/task_vars_test.py
 *
 * ## Why this file is a security boundary
 *
 * A step's parameters are templates — `{"body": "Battery is {{battery.level}}%"}`
 * — and the values come from trigger data, from earlier step results, and from
 * the server's language model (`ask_jarvis`). A notification body, a web page
 * and an LLM completion all end up here. Four rules carry that weight:
 *
 *  1. Substitution runs on the LEAVES of an already-parsed structure. A value
 *     containing `","to":"` cannot add a parameter, because nothing is ever
 *     re-parsed as JSON afterwards.
 *  2. Object keys are NOT substituted, ever. A variable may fill a parameter;
 *     it may never name one. See [substituteValue].
 *  3. Output is never re-scanned — substitution is NOT recursive. `{{a}}` where
 *     `a` is the text `"{{secret}}"` produces the literal `{{secret}}`.
 *  4. Every path that resolved is reported in [SubstitutionResult.used], so
 *     [TaskRunner] can see that a step touched a tainted variable and drop that
 *     step's dispatch to `TrustLevel.UNTRUSTED` — which the policy engine can
 *     never auto-allow.
 *
 * None of that makes the text safe. It makes it *inert*: it stays data.
 */
object VariableSubstitution {

    private const val OPEN = "{{"
    private const val CLOSE = "}}"

    /** `a.b.c.d.e.f.g.h` is already absurd; deeper is a probe, not a path. */
    const val MAX_PATH_SEGMENTS = 8

    /** A hostile payload must not be able to build a gigabyte string. */
    const val MAX_OUTPUT_CHARS = 64 * 1024

    /** Depth cap when walking a parsed params structure. */
    const val MAX_WALK_DEPTH = 12

    const val TRUNCATION_MARK = "…[truncated]"

    /**
     * Expand every `{{path}}` in [template], left to right, exactly once.
     *
     * @param onMissing what an unresolvable path becomes.
     */
    fun substitute(
        template: String,
        variables: Map<String, Any?>,
        onMissing: MissingPolicy = MissingPolicy.EMPTY
    ): SubstitutionResult {
        val out = StringBuilder()
        val used = LinkedHashSet<String>()
        val absent = LinkedHashSet<String>()
        var truncated = false

        /** Returns false once the output cap is hit. */
        fun emit(text: String): Boolean {
            if (truncated) return false
            val room = MAX_OUTPUT_CHARS - out.length
            if (text.length <= room) {
                out.append(text)
                return true
            }
            out.append(text, 0, room)
            out.append(TRUNCATION_MARK)
            truncated = true
            return false
        }

        var i = 0
        val n = template.length
        while (i < n) {
            val ch = template[i]

            // A backslash escapes the next character, but only when that
            // character is `{` or `\`. Everywhere else a backslash is ordinary,
            // so Windows-ish paths and regexes survive unharmed.
            if (ch == '\\' && i + 1 < n && (template[i + 1] == '{' || template[i + 1] == '\\')) {
                if (!emit(template[i + 1].toString())) break
                i += 2
                continue
            }

            if (template.startsWith(OPEN, i)) {
                val end = template.indexOf(CLOSE, i + OPEN.length)
                if (end < 0) {
                    // Unterminated. Emit the rest literally rather than guessing.
                    emit(template.substring(i))
                    break
                }
                val path = template.substring(i + OPEN.length, end).trim()
                i = end + CLOSE.length
                if (path.isEmpty()) {
                    if (!emit(OPEN + CLOSE)) break
                    continue
                }
                val value = resolvePath(path, variables)
                if (value === MISSING) {
                    absent.add(path)
                    if (onMissing == MissingPolicy.KEEP && !emit(OPEN + path + CLOSE)) break
                    continue
                }
                used.add(path)
                if (!emit(renderValue(value))) break
                continue
            }

            if (!emit(ch.toString())) break
            i++
        }

        return SubstitutionResult(out.toString(), used, absent, truncated)
    }

    /**
     * Walk a parsed structure and expand every STRING LEAF.
     *
     * Map keys are copied verbatim — keys are NOT substituted, so a variable
     * can fill a parameter but never invent one. Non-string scalars pass
     * through untouched, which keeps a numeric `level` a number.
     */
    fun substituteValue(
        value: Any?,
        variables: Map<String, Any?>,
        onMissing: MissingPolicy = MissingPolicy.EMPTY
    ): ValueSubstitution {
        val used = LinkedHashSet<String>()
        val absent = LinkedHashSet<String>()
        var truncated = false

        fun walk(node: Any?, depth: Int): Any? {
            if (depth > MAX_WALK_DEPTH) return null
            return when (node) {
                is String -> {
                    val r = substitute(node, variables, onMissing)
                    used.addAll(r.used)
                    absent.addAll(r.missing)
                    truncated = truncated || r.truncated
                    r.text
                }

                is Map<*, *> -> {
                    val out = LinkedHashMap<String, Any?>(node.size)
                    for ((k, v) in node) out[k.toString()] = walk(v, depth + 1)
                    out
                }

                is List<*> -> node.map { walk(it, depth + 1) }
                is Array<*> -> node.map { walk(it, depth + 1) }
                else -> node
            }
        }

        return ValueSubstitution(walk(value, 0), used, absent, truncated)
    }

    /**
     * Walk `a.b.0.c` through nested maps and lists.
     *
     * Returns [MISSING] when the path does not exist, indexes into a scalar, or
     * uses a negative index. Never throws: a bad path is an empty value, not a
     * crashed automation.
     */
    fun resolvePath(path: String, variables: Map<String, Any?>): Any? {
        val segments = path.split('.')
        if (segments.isEmpty() || segments.size > MAX_PATH_SEGMENTS) return MISSING
        if (segments.any { it.isEmpty() }) return MISSING

        var current: Any? = variables
        for (segment in segments) {
            when (current) {
                is Map<*, *> -> {
                    if (!current.containsKey(segment)) return MISSING
                    current = current[segment]
                }

                is List<*> -> {
                    val index = segment.toIndexOrNull() ?: return MISSING
                    if (index >= current.size) return MISSING
                    current = current[index]
                }

                is Array<*> -> {
                    val index = segment.toIndexOrNull() ?: return MISSING
                    if (index >= current.size) return MISSING
                    current = current[index]
                }

                // Indexing into a string, a number or null is a miss.
                else -> return MISSING
            }
        }
        return current
    }

    /** Digits only, so `-1` and `+1` are misses rather than surprise indexes. */
    private fun String.toIndexOrNull(): Int? {
        if (isEmpty() || any { !it.isDigit() }) return null
        return toIntOrNull()
    }

    /**
     * A value as text. Structures become compact JSON so they are at least
     * inspectable in a notification — but they are still only text.
     */
    fun renderValue(value: Any?): String = when (value) {
        null -> ""
        is String -> value
        is Boolean -> if (value) "true" else "false"
        is Double -> if (value == Math.floor(value) && !value.isInfinite() && !value.isNaN()) {
            value.toLong().toString()
        } else {
            value.toString()
        }

        is Float -> renderValue(value.toDouble())
        is Number -> value.toString()
        is Map<*, *>, is List<*>, is Array<*> -> writeJson(value)
        else -> value.toString()
    }

    // --- a minimal JSON writer, so this file needs no org.json --------------

    private fun writeJson(value: Any?): String = StringBuilder().also { write(it, value, 0) }.toString()

    private fun write(sb: StringBuilder, value: Any?, depth: Int) {
        if (depth > MAX_WALK_DEPTH) {
            sb.append("null")
            return
        }
        when (value) {
            null -> sb.append("null")
            is Boolean -> sb.append(if (value) "true" else "false")
            is Double, is Float -> sb.append(renderValue(value))
            is Number -> sb.append(value.toString())
            is String -> writeString(sb, value)
            is Map<*, *> -> {
                sb.append('{')
                var first = true
                for ((k, v) in value) {
                    if (!first) sb.append(',')
                    first = false
                    writeString(sb, k.toString())
                    sb.append(':')
                    write(sb, v, depth + 1)
                }
                sb.append('}')
            }

            is List<*> -> {
                sb.append('[')
                value.forEachIndexed { index, item ->
                    if (index > 0) sb.append(',')
                    write(sb, item, depth + 1)
                }
                sb.append(']')
            }

            is Array<*> -> write(sb, value.toList(), depth)
            else -> writeString(sb, value.toString())
        }
    }

    private fun writeString(sb: StringBuilder, text: String) {
        sb.append('"')
        for (c in text) {
            when {
                c == '"' -> sb.append("\\\"")
                c == '\\' -> sb.append("\\\\")
                c == '\n' -> sb.append("\\n")
                c == '\r' -> sb.append("\\r")
                c == '\t' -> sb.append("\\t")
                c < ' ' -> sb.append(String.format("\\u%04x", c.code))
                else -> sb.append(c)
            }
        }
        sb.append('"')
    }

    /** Sentinel for "this path did not resolve". Never leaks to a caller. */
    val MISSING: Any = Any()
}

/** What an unresolvable `{{path}}` becomes. */
enum class MissingPolicy {
    /** Renders as the empty string. The default: a template is not a demand. */
    EMPTY,

    /** Left verbatim, braces and all. Useful when showing a task to the user. */
    KEEP
}

/**
 * One expanded template.
 *
 * @param used every path that resolved to a value, including a present-but-null
 *   one. The task runner intersects [rootsUsed] with its tainted set.
 * @param missing every path that did not resolve, for the step's audit note.
 * @param truncated true when the output hit [VariableSubstitution.MAX_OUTPUT_CHARS].
 */
data class SubstitutionResult(
    val text: String,
    val used: Set<String> = emptySet(),
    val missing: Set<String> = emptySet(),
    val truncated: Boolean = false
) {
    /** First segment of every resolved path — the variable names actually read. */
    val rootsUsed: Set<String>
        get() = used.mapTo(LinkedHashSet()) { it.substringBefore('.') }
}

/** [VariableSubstitution.substituteValue]'s result: a new structure plus the tally. */
data class ValueSubstitution(
    val value: Any?,
    val used: Set<String> = emptySet(),
    val missing: Set<String> = emptySet(),
    val truncated: Boolean = false
) {
    val rootsUsed: Set<String>
        get() = used.mapTo(LinkedHashSet()) { it.substringBefore('.') }
}
