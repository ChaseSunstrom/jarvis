package ai.jarvis.app.automation.actions

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

/** Calendar and alarm params arrive in whatever shape the model felt like. */
class TimeParseTest {

    private val now = 1_775_000_000_000L // fixed "current" epoch millis
    private val utc = ZoneId.of("UTC")

    @Test
    fun `epoch seconds and millis are both understood`() {
        assertEquals(1_700_000_000_000L, TimeParse.epochMillis(1_700_000_000L, now))
        assertEquals(1_700_000_000_000L, TimeParse.epochMillis(1_700_000_000_000L, now))
        assertEquals(1_700_000_000_000L, TimeParse.epochMillis("1700000000", now))
        assertNull(TimeParse.epochMillis(0, now))
        assertNull(TimeParse.epochMillis(-5, now))
        assertNull(TimeParse.epochMillis(Long.MAX_VALUE, now))
    }

    @Test
    fun `iso-8601 is understood with and without an offset`() {
        val expected = Instant.parse("2026-08-09T14:30:00Z").toEpochMilli()
        assertEquals(expected, TimeParse.epochMillis("2026-08-09T14:30:00Z", now, utc))
        assertEquals(expected, TimeParse.epochMillis("2026-08-09T16:30:00+02:00", now, utc))
        assertEquals(expected, TimeParse.epochMillis("2026-08-09T14:30:00", now, utc))
        assertEquals(expected, TimeParse.epochMillis("2026-08-09 14:30:00", now, utc))
        assertEquals(
            Instant.parse("2026-08-09T00:00:00Z").toEpochMilli(),
            TimeParse.epochMillis("2026-08-09", now, utc)
        )
    }

    @Test
    fun `relative offsets are resolved against the injected now`() {
        assertEquals(now + 90 * 60_000L, TimeParse.epochMillis("+90m", now))
        assertEquals(now + 2 * 3_600_000L, TimeParse.epochMillis("+2h", now))
        assertEquals(now + 3 * 86_400_000L, TimeParse.epochMillis("3 days", now))
        assertEquals(now + 45_000L, TimeParse.epochMillis("+45s", now))
    }

    @Test
    fun `nonsense is rejected rather than guessed`() {
        assertNull(TimeParse.epochMillis(null, now))
        assertNull(TimeParse.epochMillis("", now))
        assertNull(TimeParse.epochMillis("tomorrow afternoon", now))
        assertNull(TimeParse.epochMillis("2026-13-45", now))
        assertNull(TimeParse.epochMillis(listOf(1, 2), now))
    }

    @Test
    fun `hour and minute parsing rejects impossible clock times`() {
        assertEquals(7 to 5, TimeParse.hourMinute("07:05"))
        assertEquals(7 to 30, TimeParse.hourMinute("7.30"))
        assertEquals(23 to 59, TimeParse.hourMinute("23:59"))
        assertEquals(0 to 0, TimeParse.hourMinute("0:00"))
        assertNull(TimeParse.hourMinute("24:00"))
        assertNull(TimeParse.hourMinute("12:60"))
        assertNull(TimeParse.hourMinute("noon"))
        assertNull(TimeParse.hourMinute(null))
        assertNull(TimeParse.hourMinute("12"))
    }
}
