package ai.jarvis.app.tasks

/**
 * Long work, as the phone knows about it — the bookkeeping half.
 *
 * jarvis-core keeps the registry (`jarvis-core/jarvis/tasks.py`) and fires
 * `jarvis_task_added` / `_updated` / `_removed` on every move. This turns that
 * stream into a list of rows, a headline and a percentage, and knows nothing
 * about views, sockets or JSON.
 *
 * It is deliberately the same shape as the console's `$lib/tasks.ts`, down to
 * the three bar modes and the linger times, because a phone and a browser
 * disagreeing about what Jarvis is doing is worse than either being slightly
 * wrong. `android-app/tools/task_board_test.py` is the executable mirror.
 *
 * ## The one rule
 *
 * **Never invent a number.** jarvis-core sends `fraction` and deliberately
 * sends null when a percentage would be a guess — a task with no steps, or one
 * still discovering them. In Kotlin the trap is not `Number(null)` but
 * `optDouble`, which answers `NaN`, and `getDouble` with a default, which
 * answers whatever default you passed. Both turn "no number" into a bar that
 * sits still for the whole run, indistinguishable from a task that never
 * started. So the fraction is a nullable Double, and "no fraction" is drawn as
 * an indeterminate bar rather than as 0%.
 *
 * The corollary catches the other half: a task that FAILED at 2 steps of 5
 * keeps its 40%. How far it got is the only interesting fact about it.
 */
class TaskBoard(private val clock: () -> Long = { System.currentTimeMillis() }) {

    enum class Status { QUEUED, RUNNING, BLOCKED, DONE, ERROR, CANCELLED;

        val finished: Boolean get() = this == DONE || this == ERROR || this == CANCELLED

        companion object {
            fun of(raw: String?): Status =
                Status.entries.firstOrNull { it.name.equals(raw, ignoreCase = true) }
                    ?: Status.QUEUED
        }
    }

    /**
     * What a bar should do.
     *
     * DETERMINATE — a real fraction to fill to.
     * INDETERMINATE — something IS happening and how far along is unknowable.
     * NONE — not started, waiting on a person, or over. Nothing to animate.
     */
    enum class Bar { DETERMINATE, INDETERMINATE, NONE }

    data class Row(
        val id: String,
        val title: String,
        val kind: String = "background",
        val status: Status = Status.QUEUED,
        /** Null means "do not draw a number" — never 0. */
        val fraction: Double? = null,
        val detail: String = "",
        val result: String = "",
        val error: String = "",
        val doneSteps: Int = 0,
        val totalSteps: Int = 0,
        /** Seconds since the epoch, as jarvis-core sends them. */
        val created: Double = 0.0,
        val updated: Double = 0.0,
    ) {
        val finished: Boolean get() = status.finished

        val bar: Bar
            get() = when {
                fraction != null -> Bar.DETERMINATE
                // A crawling bar under a task that stopped an hour ago is a lie
                // about the present tense.
                finished -> Bar.NONE
                // BLOCKED is waiting on a PERSON. A moving bar over it says
                // "working", which is how an approval prompt goes unnoticed.
                status == Status.BLOCKED -> Bar.NONE
                status == Status.RUNNING -> Bar.INDETERMINATE
                else -> Bar.NONE
            }

        /**
         * 0..100 for a determinate bar; 0 for every other mode.
         *
         * `Math.round`, not `toInt()`. The console rounds (`Math.round` in JS)
         * and truncating here would put the phone a percent below the browser
         * on the same task — a difference nobody would chase and everybody
         * could see. Fractions are non-negative, so Java's `floor(x + 0.5)`
         * and JavaScript's rounding agree exactly.
         */
        val percent: Int
            get() = fraction?.let { Math.round(it.coerceIn(0.0, 1.0) * 100).toInt() } ?: 0

        /** One line under the title: what is happening, or why it stopped. */
        val says: String
            get() = when (status) {
                Status.ERROR -> error.ifEmpty { "it failed, and said no more than that" }
                Status.CANCELLED -> detail.ifEmpty { "cancelled" }
                Status.DONE -> result.ifEmpty { detail.ifEmpty { "finished" } }
                Status.BLOCKED -> detail.ifEmpty { "waiting for you" }
                Status.QUEUED -> detail.ifEmpty { "queued" }
                Status.RUNNING -> detail.ifEmpty { "working" }
            }

        val label: String
            get() = when (status) {
                Status.RUNNING -> "RUNNING"
                Status.BLOCKED -> "WAITING"
                Status.QUEUED -> "QUEUED"
                Status.DONE -> "DONE"
                Status.ERROR -> "FAILED"
                Status.CANCELLED -> "CANCELLED"
            }

        /** "3 of 8", or empty when a count would be noise. */
        val steps: String get() = if (totalSteps <= 0) "" else "$doneSteps of $totalSteps"
    }

    private val entries = LinkedHashMap<String, Row>()

    /** Newest first, which is the order every surface wants. */
    val rows: List<Row>
        get() = entries.values.sortedWith(compareByDescending<Row> { it.created }.thenBy { it.id })

    val isEmpty: Boolean get() = visible().isEmpty()

    /**
     * A task appeared or moved.
     *
     * An update for a task never seen is an INSERT, not a no-op: work that
     * began before the phone connected, and work that a filter had excluded
     * until this very update, would otherwise be lost.
     *
     * A frame older than the one held is dropped. One socket delivers in order,
     * but a `jarvis/tasks/list` response in flight while an event fires lands
     * after it and would otherwise reinstate the older row.
     */
    fun upsert(row: Row) {
        val held = entries[row.id]
        if (held != null && held.updated > row.updated) return
        entries[row.id] = row
    }

    fun remove(id: String): Boolean = entries.remove(id) != null

    fun clear() = entries.clear()

    /** Replace everything, for the one listing sent on connect. */
    fun replaceAll(fresh: List<Row>) {
        val held = entries.toMap()
        entries.clear()
        for (row in fresh) {
            val old = held[row.id]
            entries[row.id] = if (old != null && old.updated > row.updated) old else row
        }
    }

    /**
     * What to draw right now: everything live, plus what has just ended.
     *
     * A job you were watching vanishing at the instant it succeeds is the one
     * frame you actually wanted to see, so a finished task lingers. A failure
     * stays far longer, because that one is not a progress report — it is an
     * answer. Same numbers as the console.
     */
    fun visible(nowMs: Long = clock()): List<Row> {
        val all = rows
        val kept = all.filter { row ->
            if (!row.finished) true else (nowMs - (row.updated * 1000).toLong()) < lingerFor(row)
        }
        // Live work first: a finished job floating above a running one buries
        // the thing somebody opened the overlay to read.
        return kept.filterNot { it.finished } + kept.filter { it.finished }
    }

    val running: Boolean get() = entries.values.any { it.status == Status.RUNNING }

    val waiting: Boolean get() = entries.values.any { it.status == Status.BLOCKED }

    /**
     * "2 running · 1 waiting on you".
     *
     * Waiting is counted apart from running deliberately: they are different
     * things to do about it, and folding them together is how an approval sits
     * unnoticed behind a spinner.
     */
    fun headline(nowMs: Long = clock()): String {
        val live = visible(nowMs)
        val parts = ArrayList<String>(4)
        live.count { it.status == Status.RUNNING }.takeIf { it > 0 }?.let { parts.add("$it running") }
        live.count { it.status == Status.BLOCKED }.takeIf { it > 0 }
            ?.let { parts.add("$it waiting on you") }
        live.count { it.status == Status.QUEUED }.takeIf { it > 0 }?.let { parts.add("$it queued") }
        live.count { it.status == Status.ERROR }.takeIf { it > 0 }?.let { parts.add("$it failed") }
        return parts.joinToString(" · ")
    }

    /**
     * When the surface next needs to redraw because a task has aged out.
     *
     * One timer at the next expiry rather than a tick every second for ever
     * behind an empty overlay — which on a phone is a battery cost, not merely
     * an untidiness. Null means nothing is waiting to expire.
     */
    fun nextExpiryMs(nowMs: Long = clock()): Long? =
        entries.values.mapNotNull { row ->
            val linger = lingerFor(row)
            if (linger == 0L) null
            else (linger - (nowMs - (row.updated * 1000).toLong())).takeIf { it > 0 }
        }.minOrNull()

    private fun lingerFor(row: Row): Long = when {
        !row.finished -> 0L
        row.status == Status.ERROR -> FAILED_LINGER_MS
        else -> DONE_LINGER_MS
    }

    companion object {
        const val EVENT_ADDED = "jarvis_task_added"
        const val EVENT_UPDATED = "jarvis_task_updated"
        const val EVENT_REMOVED = "jarvis_task_removed"

        val EVENTS = listOf(EVENT_ADDED, EVENT_UPDATED, EVENT_REMOVED)

        const val DONE_LINGER_MS = 8_000L
        const val FAILED_LINGER_MS = 30_000L

        /** Rows an overlay may show before it is a screen rather than a chip. */
        const val OVERLAY_ROWS = 3
    }
}
