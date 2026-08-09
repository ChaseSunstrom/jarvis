package ai.jarvis.app.automation.actions

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId

/**
 * PURE LOGIC — no Android imports. Unit-testable on a plain JVM.
 *
 * The model writes times in whatever shape it feels like. Rather than making
 * every calendar/alarm action re-implement the guesswork, everything funnels
 * through here: epoch millis, epoch seconds, ISO-8601 with or without an
 * offset, a bare date, or a relative offset like `+90m`.
 *
 * Returns null when it cannot tell — callers then reject the parameter instead
 * of silently scheduling something for 1970.
 */
object TimeParse {

    private val RELATIVE = Regex("^\\+?\\s*(\\d{1,6})\\s*(ms|s|sec|secs|m|min|mins|h|hr|hrs|d|day|days)$")

    /**
     * @param value a Number or String from the params object.
     * @param nowMs the current epoch millis (injected so this stays pure).
     * @param zone the zone to assume for values that carry none.
     */
    fun epochMillis(value: Any?, nowMs: Long, zone: ZoneId = ZoneId.systemDefault()): Long? = when (value) {
        null -> null
        is Number -> normalizeEpoch(value.toLong())
        is String -> fromString(value.trim(), nowMs, zone)
        else -> null
    }

    private fun fromString(text: String, nowMs: Long, zone: ZoneId): Long? {
        if (text.isEmpty()) return null

        RELATIVE.find(text.lowercase())?.let { m ->
            val amount = m.groupValues[1].toLongOrNull() ?: return null
            val ms = when (m.groupValues[2]) {
                "ms" -> amount
                "s", "sec", "secs" -> amount * 1_000
                "m", "min", "mins" -> amount * 60_000
                "h", "hr", "hrs" -> amount * 3_600_000
                else -> amount * 86_400_000
            }
            return nowMs + ms
        }

        if (text.all { it.isDigit() }) {
            return text.toLongOrNull()?.let { normalizeEpoch(it) }
        }

        // 2026-08-09T14:30:00Z / +02:00
        runCatching { return OffsetDateTime.parse(text).toInstant().toEpochMilli() }
        runCatching { return Instant.parse(text).toEpochMilli() }
        // 2026-08-09T14:30 (assume the phone's zone)
        runCatching {
            return LocalDateTime.parse(text.replace(' ', 'T')).atZone(zone).toInstant().toEpochMilli()
        }
        // 2026-08-09
        runCatching {
            return LocalDate.parse(text).atStartOfDay(zone).toInstant().toEpochMilli()
        }
        return null
    }

    /** 10-digit values are seconds, 13-digit are millis. Anything absurd is rejected. */
    private fun normalizeEpoch(raw: Long): Long? = when {
        raw <= 0 -> null
        raw < 100_000_000_000L -> raw * 1000  // seconds
        raw < 100_000_000_000_000L -> raw     // millis
        else -> null
    }

    /** Split "HH:MM" (or "H:MM", "HH.MM") into hour/minute, or null. */
    fun hourMinute(text: String?): Pair<Int, Int>? {
        val t = text?.trim()?.replace('.', ':') ?: return null
        val parts = t.split(':')
        if (parts.size != 2) return null
        val h = parts[0].toIntOrNull() ?: return null
        val m = parts[1].take(2).toIntOrNull() ?: return null
        if (h !in 0..23 || m !in 0..59) return null
        return h to m
    }
}
