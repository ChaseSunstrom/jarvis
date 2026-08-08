package ai.jarvis.app.automation.actions

import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONArray
import org.json.JSONObject

/**
 * Small shared helpers for action bodies. Nothing here makes a policy
 * decision; it only keeps the built-ins short enough to read.
 */

/** Trimmed string param, or null when absent/blank/JSON-null. */
fun JSONObject.str(key: String): String? {
    if (!has(key) || isNull(key)) return null
    val v = optString(key, "").trim()
    return v.ifEmpty { null }
}

/** Int param with a fallback; tolerates numeric strings. */
fun JSONObject.intOr(key: String, fallback: Int): Int =
    if (!has(key) || isNull(key)) fallback else optInt(key, fallback)

/** Long param with a fallback. */
fun JSONObject.longOr(key: String, fallback: Long): Long =
    if (!has(key) || isNull(key)) fallback else optLong(key, fallback)

/** Bool param with a fallback; tolerates "true"/"false" strings. */
fun JSONObject.boolOr(key: String, fallback: Boolean): Boolean =
    if (!has(key) || isNull(key)) fallback else optBoolean(key, fallback)

/** Clamp helper for the many 0..100 percentage params. */
fun Int.clampPercent(): Int = coerceIn(0, 100)

/** Runtime permission check. Always used before touching a guarded API. */
fun Context.granted(permission: String): Boolean =
    checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

/** Builds a JSONArray from anything iterable. */
fun Iterable<*>.toJsonArray(): JSONArray {
    val arr = JSONArray()
    for (item in this) arr.put(item)
    return arr
}
