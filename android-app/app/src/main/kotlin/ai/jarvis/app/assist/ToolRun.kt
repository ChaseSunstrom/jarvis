package ai.jarvis.app.assist

/**
 * What Jarvis is doing, while it does it — the bookkeeping half.
 *
 * jarvis-core fires `jarvis_tool_started` and `jarvis_tool_finished` around
 * every tool call a turn makes, carrying the call's position in the round and
 * how many the model asked for. This turns that stream into a list of rows and
 * a percentage, and knows nothing about views, sockets or JSON.
 *
 * It is deliberately the same shape as the console's `ToolActivity.svelte`,
 * down to the key and the argument summary, because a phone and a browser
 * disagreeing about what a turn just did is worse than either of them being
 * slightly wrong. `android-app/tools/tool_run_test.py` is the executable mirror.
 *
 * **The progress is the model's own count.** `index` and `total` come from the
 * call list the model asked for; nothing here advances a bar on a timer. A bar
 * that moved by itself would be a decoration that lies during exactly the
 * seconds somebody is watching it.
 */
class ToolRun {

    enum class State { RUNNING, OK, FAILED }

    data class Row(
        val key: String,
        val name: String,
        /** One line of arguments, already shortened. May be empty. */
        val summary: String,
        val index: Int,
        val total: Int,
        val state: State,
        /** Why it failed, when it did. */
        val error: String? = null,
        val durationMs: Int = 0,
    )

    private val entries = ArrayList<Row>()

    /** In the order the model asked for them, not the order they finished. */
    val rows: List<Row> get() = entries.toList()

    val isEmpty: Boolean get() = entries.isEmpty()

    /** How many calls have stopped running, successfully or not. */
    val done: Int get() = entries.count { it.state != State.RUNNING }

    /**
     * How many there are to do.
     *
     * The larger of what the model announced and what has actually arrived: a
     * second round of calls adds rows beyond the first round's `total`, and a
     * denominator smaller than the numerator would read as "5 / 4".
     */
    val total: Int
        get() = if (entries.isEmpty()) 0
        else maxOf(entries.maxOf { it.total }, entries.size)

    val percent: Int get() = if (total == 0) 0 else (done * 100) / total

    val running: Boolean get() = entries.any { it.state == State.RUNNING }

    val failed: Int get() = entries.count { it.state == State.FAILED }

    /** A tool call began. Repeats of the same key replace, never duplicate. */
    fun started(name: String, round: Int, index: Int, total: Int, summary: String) {
        put(
            Row(
                key = keyOf(name, round, index),
                name = name,
                summary = summary,
                index = index,
                total = maxOf(total, 1),
                state = State.RUNNING,
            )
        )
    }

    /**
     * A tool call ended.
     *
     * A finish with no matching start still lands: events can be missed when a
     * socket subscribes mid-turn, and a row that says what happened is better
     * than no row at all.
     */
    fun finished(
        name: String,
        round: Int,
        index: Int,
        total: Int,
        ok: Boolean,
        error: String?,
        durationMs: Int,
    ) {
        val key = keyOf(name, round, index)
        val existing = entries.firstOrNull { it.key == key }
        put(
            Row(
                key = key,
                name = name,
                summary = existing?.summary.orEmpty(),
                index = index,
                total = maxOf(total, existing?.total ?: 1, 1),
                state = if (ok) State.OK else State.FAILED,
                error = error?.takeIf { it.isNotEmpty() },
                durationMs = durationMs,
            )
        )
    }

    /**
     * The last [limit] rows, still in the model's order.
     *
     * A phone screen is not a console: a turn that calls nine tools would push
     * the orb off the top. The LAST few rather than the first, because the
     * interesting one is whatever is happening now — and the header still
     * counts every call, so the cap is visible rather than a quiet lie.
     */
    fun visible(limit: Int): List<Row> =
        if (limit <= 0 || entries.size <= limit) rows else entries.takeLast(limit)

    fun clear() = entries.clear()

    /**
     * How long the rows should stay up once nothing is running.
     *
     * A failed row is an answer to read, not a progress report to glance at,
     * so it is given three times as long. Same numbers as the console.
     */
    fun holdMs(): Long = if (failed > 0) FAILED_HOLD_MS else DONE_HOLD_MS

    private fun put(row: Row) {
        val at = entries.indexOfFirst { it.key == row.key }
        if (at >= 0) entries[at] = row else entries.add(row)
        entries.sortBy { it.index }
    }

    companion object {
        const val EVENT_STARTED = "jarvis_tool_started"
        const val EVENT_FINISHED = "jarvis_tool_finished"

        const val DONE_HOLD_MS = 4_000L
        const val FAILED_HOLD_MS = 12_000L

        /** Longest an argument value may be before it is cut. */
        const val VALUE_CHARS = 40

        /** How many arguments fit on one line before the rest are dropped. */
        const val MAX_PARTS = 3

        /**
         * Identity of a call within a turn.
         *
         * The round matters: a model that calls `get_state` in round 1 and again
         * in round 2 made two calls, and collapsing them onto one row would show
         * the second overwriting the first's result.
         */
        fun keyOf(name: String, round: Int, index: Int): String = "$round:$index:$name"

        /**
         * One line of arguments for a row that only has one line.
         *
         * Ordered as given — the caller preserves the order jarvis-core sent,
         * which is the order the model wrote them — because the first argument
         * of a tool call is almost always the interesting one (the entity, the
         * area, the query) and re-sorting would bury it.
         */
        fun summarise(arguments: List<Pair<String, String>>): String {
            val parts = ArrayList<String>(MAX_PARTS)
            for ((key, value) in arguments) {
                if (value.isEmpty() || value == "null") continue
                val short =
                    if (value.length > VALUE_CHARS) value.take(VALUE_CHARS - 1) + "…" else value
                parts.add("$key: $short")
                if (parts.size >= MAX_PARTS) break
            }
            return parts.joinToString(" · ")
        }
    }
}
