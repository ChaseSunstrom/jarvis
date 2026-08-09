package ai.jarvis.app.channel

/**
 * PURE LOGIC — no Android imports.
 *
 * Log hygiene for the command channel.
 *
 * `adb logcat` is readable by any app holding READ_LOGS, by anyone with the
 * phone unlocked and a cable, and by a bug report the user mails to somebody.
 * The bearer token in [ai.jarvis.app.config.JarvisConfig.token] is the whole
 * authentication story for this device, so it never reaches a log line — not in
 * a frame dump, not in a URL, not in an exception message from OkHttp.
 *
 * The approach is belt and braces: the channel never logs raw frames in the
 * first place, and everything that does get logged goes through here anyway,
 * because "never" is a property of code as it is today and this file is a
 * property of code as it will be after the next edit.
 */
object Redact {

    const val MASK = "[redacted]"

    /**
     * A token, for the rare log line that has to identify *which* token
     * (e.g. "auth rejected"). Shows the length and a 4-character fingerprint of
     * the first characters — enough to tell "the one I pasted" from "an old
     * one", useless for authenticating.
     */
    fun token(token: String?): String {
        if (token.isNullOrEmpty()) return "(none)"
        val head = token.take(4)
        return "$head…(${token.length} chars)"
    }

    /**
     * Strip anything that looks like a credential out of an arbitrary string
     * before it is logged: `access_token` / `token` / `authorization` JSON
     * fields, and `?token=` style query parameters.
     *
     * Regex on hostile input is a liability, so these are deliberately simple
     * and anchored on the key name rather than on the value's shape.
     */
    fun text(value: String?): String {
        if (value.isNullOrEmpty()) return ""
        var out: String = value
        for (key in SECRET_KEYS) {
            val jsonField = JSON_FIELD.getValue(key)
            val queryParam = QUERY_PARAM.getValue(key)
            out = jsonField.replace(out, "\"$key\":\"$MASK\"")
            out = queryParam.replace(out) { m -> m.groupValues[1] + key + "=" + MASK }
        }
        return out
    }

    /** Redact a whole frame for a debug log. Never call this on a hot path. */
    fun frame(json: String?, maxChars: Int = 512): String {
        val redacted = text(json)
        return if (redacted.length <= maxChars) redacted
        else redacted.take(maxChars) + "…(+${redacted.length - maxChars} chars)"
    }

    private val SECRET_KEYS = listOf("access_token", "token", "authorization", "password", "api_key")

    /** `"access_token": "…"` with any spacing. */
    private val JSON_FIELD: Map<String, Regex> = SECRET_KEYS.associateWith { key ->
        Regex("\"${Regex.escape(key)}\"\\s*:\\s*\"[^\"]*\"", RegexOption.IGNORE_CASE)
    }

    /** `?token=…` / `&token=…` up to the next separator. */
    private val QUERY_PARAM: Map<String, Regex> = SECRET_KEYS.associateWith { key ->
        Regex("([?&])${Regex.escape(key)}=[^&\\s\"]*", RegexOption.IGNORE_CASE)
    }
}
