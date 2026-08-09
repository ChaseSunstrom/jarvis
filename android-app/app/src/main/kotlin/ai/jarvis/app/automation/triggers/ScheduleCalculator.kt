package ai.jarvis.app.automation.triggers

import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset

/**
 * PURE LOGIC — no Android imports, no org.json, no I/O, no ambient clock.
 *
 * Turns a `{time, days_of_week, interval}` schedule into the next moment it
 * should fire. Mirrored line-for-line by the executable spec at
 * `android-app/tools/schedule_calc_test.py`; change a rule here and change it
 * there, then run:
 *
 *     python3 android-app/tools/schedule_calc_test.py
 *
 * ## Why everything is in "local millis"
 *
 * [nextFireLocalMs] works on the wall clock rendered as if it were UTC. Local
 * 2026-03-29 07:00 is the same number whatever the offset is that day, so
 * day-of-week arithmetic, midnight wrapping and interval alignment contain no
 * timezone at all and can be checked with plain integers.
 *
 * Exactly one conversion happens, in [nextFireEpochMs], and that is the only
 * place daylight saving is visible:
 *
 *  * **Spring forward** — 02:30 does not exist on the jump day.
 *    `ZonedDateTime` resolves the gap forward, so the alarm fires at 03:30.
 *  * **Fall back** — 01:30 happens twice. `ZonedDateTime` picks the FIRST.
 *    If that instant has already passed (we are living through the repeat) the
 *    candidate is not in the future, so the loop asks for the next one. The
 *    schedule therefore fires once on that day, never twice.
 *
 * Interval schedules are aligned to the local wall clock too, so "every 30
 * minutes" stays on :00 and :30 across a DST change. One interval on the jump
 * day is consequently short or long in real time. That is deliberate: users
 * think in wall clock.
 */
object ScheduleCalculator {

    const val MINUTE_MS = 60_000L
    const val HOUR_MS = 60 * MINUTE_MS
    const val DAY_MS = 24 * HOUR_MS

    const val MONDAY = 1
    const val SUNDAY = 7

    /** ISO weekday sets the task JSON accepts as `"weekdays"` / `"weekend"`. */
    val WEEKDAYS: Set<Int> = setOf(1, 2, 3, 4, 5)
    val WEEKEND: Set<Int> = setOf(6, 7)

    /** A schedule that cannot fire within a week is a bug, not a schedule. */
    private const val MAX_LOOKAHEAD_DAYS = 8
    private const val MAX_INTERVAL_STEPS = 4096
    const val MAX_INTERVAL_MINUTES = 7 * 24 * 60

    // --- the pure core ------------------------------------------------------

    /** Whole local days since the epoch. Floor division, so negatives work. */
    fun dayIndex(localMs: Long): Long = Math.floorDiv(localMs, DAY_MS)

    /** ISO day of week, 1=Mon..7=Sun. 1970-01-01 was a Thursday, hence the +3. */
    fun isoWeekday(localMs: Long): Int =
        (Math.floorMod(dayIndex(localMs) + 3, 7L) + 1L).toInt()

    /**
     * Next fire strictly AFTER [nowLocalMs], in local millis.
     *
     * Null when the spec can never fire: invalid fields, or a day filter whose
     * every entry was out of range.
     */
    fun nextFireLocalMs(nowLocalMs: Long, spec: ScheduleSpec): Long? {
        if (!spec.isValid()) return null
        val days = spec.normalizedDays()
        // A day filter that survived as the empty set must NOT quietly become
        // "every day" — that would fire an automation the user restricted.
        if (spec.daysOfWeek.isNotEmpty() && days.isEmpty()) return null

        return if (spec.intervalMinutes != null) {
            nextInterval(nowLocalMs, spec, days)
        } else {
            nextTimeOfDay(nowLocalMs, spec, days)
        }
    }

    /** Smallest point on the `anchor + n*step` grid that is at least [value]. */
    private fun alignUp(value: Long, anchor: Long, step: Long): Long {
        val delta = value - anchor
        // Ceiling division that behaves for negatives.
        val n = -Math.floorDiv(-delta, step)
        return anchor + n * step
    }

    private fun nextInterval(nowLocalMs: Long, spec: ScheduleSpec, days: Set<Int>): Long? {
        val step = spec.intervalMinutes!! * MINUTE_MS
        val anchorGiven = spec.anchorLocalMs != null
        val anchor = spec.anchorLocalMs ?: 0L

        var candidate: Long
        if (anchorGiven && anchor > nowLocalMs) {
            // An explicit anchor in the future is a start time, so the grid is
            // not extended backwards past it. The implicit anchor (0 = local
            // midnight on 1970-01-01) is only a phase reference, never a start
            // time — a device whose clock is set before 1970 must still fire.
            candidate = anchor
        } else {
            candidate = alignUp(nowLocalMs + 1, anchor, step)
            if (candidate <= nowLocalMs) candidate += step // exactly on the grid
        }

        if (days.isEmpty()) return candidate

        // Skip whole days rather than stepping through them, so a 5-minute
        // interval restricted to Monday does not loop 288 times per skipped day.
        repeat(MAX_INTERVAL_STEPS) {
            if (isoWeekday(candidate) in days) return candidate
            val nextDayStart = (dayIndex(candidate) + 1) * DAY_MS
            candidate = alignUp(nextDayStart, anchor, step)
            if (candidate <= nowLocalMs) candidate = alignUp(nowLocalMs + 1, anchor, step)
        }
        return null
    }

    private fun nextTimeOfDay(nowLocalMs: Long, spec: ScheduleSpec, days: Set<Int>): Long? {
        val today = dayIndex(nowLocalMs)
        for (offset in 0 until MAX_LOOKAHEAD_DAYS) {
            val candidate = (today + offset) * DAY_MS + spec.minuteOfDay!! * MINUTE_MS
            if (days.isNotEmpty() && isoWeekday(candidate) !in days) continue
            // Strictly future: firing "now" would re-fire the alarm that just
            // woke us, and the trigger would loop.
            if (candidate > nowLocalMs) return candidate
        }
        return null
    }

    // --- the one conversion -------------------------------------------------

    /**
     * Next fire as a real epoch instant, ready for `AlarmManager`.
     *
     * The retry loop is the DST guard: when the zone maps a candidate onto an
     * instant that is not actually in the future — a fall-back repeat, or a gap
     * resolved differently by some other platform — we ask the core for the one
     * after it rather than scheduling in the past or firing twice.
     */
    fun nextFireEpochMs(
        nowEpochMs: Long,
        spec: ScheduleSpec,
        zone: ZoneId = ZoneId.systemDefault()
    ): Long? {
        var cursor = toLocalMs(nowEpochMs, zone)
        repeat(MAX_LOOKAHEAD_DAYS + 2) {
            val candidateLocal = nextFireLocalMs(cursor, spec) ?: return null
            val candidateEpoch = toEpochMs(candidateLocal, zone)
            if (candidateEpoch > nowEpochMs) return candidateEpoch
            cursor = candidateLocal
        }
        return null
    }

    /** Real instant -> wall clock rendered as if it were UTC. */
    fun toLocalMs(epochMs: Long, zone: ZoneId = ZoneId.systemDefault()): Long {
        val local = LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMs), zone)
        return local.toEpochSecond(ZoneOffset.UTC) * 1000L + local.nano / 1_000_000L
    }

    /**
     * Wall clock -> real instant. `atZone` shifts a nonexistent local time
     * forward out of a spring-forward gap and picks the earlier of two
     * candidates during a fall-back overlap.
     */
    fun toEpochMs(localMs: Long, zone: ZoneId = ZoneId.systemDefault()): Long {
        val seconds = Math.floorDiv(localMs, 1000L)
        val nanos = (Math.floorMod(localMs, 1000L) * 1_000_000L).toInt()
        return LocalDateTime.ofEpochSecond(seconds, nanos, ZoneOffset.UTC)
            .atZone(zone)
            .toInstant()
            .toEpochMilli()
    }

    /** Local wall-clock minute of day for a real instant, for time-window conditions. */
    fun minuteOfDay(epochMs: Long, zone: ZoneId = ZoneId.systemDefault()): Int {
        val localMs = toLocalMs(epochMs, zone)
        return ((localMs - dayIndex(localMs) * DAY_MS) / MINUTE_MS).toInt()
    }

    /** ISO weekday for a real instant. */
    fun weekdayOf(epochMs: Long, zone: ZoneId = ZoneId.systemDefault()): Int =
        isoWeekday(toLocalMs(epochMs, zone))

    /** `"HH:MM"` -> minute of day, or null. Accepts `7:5` as well as `07:05`. */
    fun parseTimeOfDay(text: String?): Int? {
        val t = text?.trim() ?: return null
        val parts = t.split(":")
        if (parts.size != 2) return null
        val h = parts[0].trim().toIntOrNull() ?: return null
        val m = parts[1].trim().toIntOrNull() ?: return null
        if (h !in 0..23 || m !in 0..59) return null
        return h * 60 + m
    }

    /**
     * Day names / numbers -> ISO weekday numbers. Understands `mon`, `monday`,
     * `1`, and the aliases `weekdays`, `weekend`, `daily`/`every_day` (which
     * expand to the empty set, meaning no filter).
     */
    fun parseDays(tokens: List<String>): Set<Int> {
        val out = LinkedHashSet<Int>()
        for (raw in tokens) {
            when (val token = raw.trim().lowercase()) {
                "weekdays", "weekday" -> out.addAll(WEEKDAYS)
                "weekend", "weekends" -> out.addAll(WEEKEND)
                "daily", "every_day", "everyday", "all" -> return emptySet()
                else -> {
                    val number = token.toIntOrNull()
                    if (number != null) {
                        if (number in MONDAY..SUNDAY) out.add(number)
                    } else {
                        DAY_NAMES[token.take(3)]?.let { out.add(it) }
                    }
                }
            }
        }
        return out
    }

    private val DAY_NAMES = mapOf(
        "mon" to 1, "tue" to 2, "wed" to 3, "thu" to 4,
        "fri" to 5, "sat" to 6, "sun" to 7
    )
}

/**
 * A time trigger's schedule.
 *
 * @param minuteOfDay 0..1439, local wall clock. Null in interval mode.
 * @param daysOfWeek ISO 1=Mon..7=Sun. Empty means every day.
 * @param intervalMinutes when set, fires every N minutes instead of at a time.
 * @param anchorLocalMs grid origin for interval mode, in LOCAL millis. Null
 *   defaults to 0 — local midnight on 1970-01-01 — so any interval that
 *   divides a day lands on tidy wall-clock times.
 */
data class ScheduleSpec(
    val minuteOfDay: Int? = null,
    val daysOfWeek: Set<Int> = emptySet(),
    val intervalMinutes: Int? = null,
    val anchorLocalMs: Long? = null
) {
    fun normalizedDays(): Set<Int> =
        daysOfWeek.filterTo(LinkedHashSet()) { it in ScheduleCalculator.MONDAY..ScheduleCalculator.SUNDAY }

    fun isValid(): Boolean = when {
        intervalMinutes != null -> intervalMinutes in 1..ScheduleCalculator.MAX_INTERVAL_MINUTES
        minuteOfDay != null -> minuteOfDay in 0..1439
        else -> false
    }

    /**
     * True when this needs a sub-15-minute cadence, which `WorkManager` cannot
     * express. [IntervalTrigger] uses an alarm chain for these instead.
     */
    fun needsExactAlarm(): Boolean =
        minuteOfDay != null || (intervalMinutes != null && intervalMinutes < 15)
}
