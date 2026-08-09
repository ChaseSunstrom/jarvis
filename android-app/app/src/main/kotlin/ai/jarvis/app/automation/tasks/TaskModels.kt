package ai.jarvis.app.automation.tasks

/**
 * PURE LOGIC — no Android imports, no org.json.
 *
 * The shape of a task. `TaskJson` turns these into and out of the JSON the
 * server pushes and the store keeps; everything else in the package works on
 * these types, so the parsing lives in exactly one file.
 *
 * A task is Tasker's "task" plus its profile: what starts it, what has to be
 * true, and what it then does.
 */

/** What happens when a task is triggered while it is already running. */
enum class TaskMode {
    /** Ignore the new trigger. The default, and the right answer for most tasks. */
    SINGLE,

    /** Cancel the run in flight and start again from the top. */
    RESTART,

    /** Queue it and run it when the current one finishes. Bounded. */
    QUEUED;

    companion object {
        fun fromName(name: String?): TaskMode = when (name?.trim()?.uppercase()) {
            "RESTART" -> RESTART
            "QUEUED", "QUEUE" -> QUEUED
            else -> SINGLE
        }
    }
}

/** Who wrote this task. Decides whether it may be enabled without a human. */
enum class TaskSource {
    /** Created on the phone by the user. */
    LOCAL,

    /**
     * Pushed by jarvis-core — which means written by the language model, which
     * means it may have been influenced by a web page the model read. See
     * [TaskSafety].
     */
    SERVER;

    companion object {
        fun fromName(name: String?): TaskSource =
            if (name?.trim()?.uppercase() == "SERVER") SERVER else LOCAL
    }
}

/** The kinds of step a task can contain. */
enum class StepType(val wire: String) {
    /** Dispatch an action through the registry. The only way to touch the world. */
    ACTION("action"),

    /** Sleep. */
    WAIT("wait"),

    /** Block until a trigger fires, or time out. */
    WAIT_FOR_EVENT("wait_for_event"),

    /** Branch on a condition. */
    IF("if"),

    /** Loop a fixed number of times, or while a condition holds. */
    REPEAT("repeat"),

    /** Set a variable from a template. */
    SET_VARIABLE("set_variable"),

    /** End the run early, successfully. */
    STOP("stop"),

    /** Ask the server's model something and keep the reply. Reply is tainted. */
    ASK_JARVIS("ask_jarvis"),

    /** Post a local notification. Goes through the registry like any action. */
    NOTIFY("notify");

    companion object {
        private val BY_WIRE = entries.associateBy { it.wire }

        fun fromWire(value: String?): StepType? = BY_WIRE[value?.trim()?.lowercase()]
    }
}

/**
 * What starts a task.
 *
 * [type] is a [ai.jarvis.app.automation.triggers.TriggerIds] value; [params]
 * carries both the trigger's own configuration (a time, a radius) and the
 * match filters (`packages`, `equals`, …). Which is which is decided by
 * [ai.jarvis.app.automation.triggers.TriggerMatch].
 */
data class TriggerSpec(
    val type: String,
    val params: Map<String, Any?> = emptyMap()
)

/**
 * Something that must be true for the task to run.
 *
 * [children] carries the operands of `all` / `any` / `not`; leaf conditions
 * leave it empty. [negate] flips any condition, so `not` is available in two
 * spellings and users get whichever reads better.
 */
data class ConditionSpec(
    val type: String,
    val params: Map<String, Any?> = emptyMap(),
    val negate: Boolean = false,
    val children: List<ConditionSpec> = emptyList()
)

/**
 * One step.
 *
 * The union is a little wide because a step is a tagged union in JSON and
 * Kotlin data classes are the honest way to hold that without a sealed
 * hierarchy that `TaskJson` would then have to reproduce twice.
 */
data class StepSpec(
    val type: StepType,
    /** For ACTION: the action id. Ignored otherwise. */
    val action: String? = null,
    /** Templates. Substituted through `VariableSubstitution` before use. */
    val params: Map<String, Any?> = emptyMap(),
    /** Variable to keep the result in. */
    val storeAs: String? = null,
    /** Per-step cap. Null means [TaskLimits.DEFAULT_STEP_TIMEOUT_MS]. */
    val timeoutMs: Long? = null,
    /** For IF, REPEAT-while, and as a per-step guard on any step. */
    val condition: ConditionSpec? = null,
    /** IF: the true branch. */
    val then: List<StepSpec> = emptyList(),
    /** IF: the false branch. */
    val otherwise: List<StepSpec> = emptyList(),
    /** REPEAT: the body. */
    val steps: List<StepSpec> = emptyList(),
    /** REPEAT: fixed iteration count. Null with a [condition] means while-loop. */
    val count: Int? = null,
    /**
     * Keep going when this step errors.
     *
     * Deliberately has NO effect on a policy denial: a denied step always
     * aborts the task. "Continue on error" is for a flaky network, not for
     * working around the user saying no.
     */
    val continueOnError: Boolean = false,
    /** Human label for the audit log and the task editor. */
    val label: String? = null
)

/**
 * A whole task.
 *
 * [enabled] is the effective switch. For a [TaskSource.SERVER] task it is only
 * ever true if [TaskSafety] cleared it or the user turned it on by hand —
 * see [enabledByUser].
 */
data class TaskDefinition(
    val id: String,
    val name: String,
    val enabled: Boolean = false,
    val triggers: List<TriggerSpec> = emptyList(),
    val conditions: List<ConditionSpec> = emptyList(),
    val steps: List<StepSpec> = emptyList(),
    val mode: TaskMode = TaskMode.SINGLE,
    val source: TaskSource = TaskSource.LOCAL,
    /**
     * True once a human turned this on in the app.
     *
     * The one flag the server cannot write. A pushed task containing a
     * CONFIRM-tier action stays off until this is set locally, and it is
     * cleared whenever the task's steps change — re-consent is required for
     * a task that is no longer the one that was approved.
     */
    val enabledByUser: Boolean = false,
    val description: String? = null,
    val createdAtMs: Long = 0L,
    val updatedAtMs: Long = 0L
) {
    /** Every action id this task can dispatch, including inside branches. */
    fun actionIds(): List<String> = TaskSafety.collectActionIds(steps)

    /** Trigger types this task listens for. */
    fun triggerTypes(): Set<String> = triggers.mapTo(LinkedHashSet()) { it.type }

    /** Packages named by any notification trigger, for the listener's allow-list. */
    fun notificationPackages(): Set<String> {
        val out = LinkedHashSet<String>()
        for (trigger in triggers) {
            if (trigger.type != "notification_posted") continue
            val raw = trigger.params["packages"] ?: trigger.params["package"] ?: continue
            when (raw) {
                is String -> out.add(raw.trim())
                is List<*> -> raw.forEach { item -> item?.toString()?.trim()?.let { out.add(it) } }
                else -> Unit
            }
        }
        out.remove("")
        return out
    }

    fun isRunnable(): Boolean = enabled && steps.isNotEmpty() && triggers.isNotEmpty()
}

/**
 * The caps. Every one of them exists because the alternative is a task that
 * pins the CPU, holds the foreground service open, or fills the audit log.
 */
object TaskLimits {
    const val DEFAULT_STEP_TIMEOUT_MS = 30_000L
    const val MAX_STEP_TIMEOUT_MS = 5 * 60_000L

    /** A single `wait` step. Longer than this wants a time trigger, not a sleep. */
    const val MAX_WAIT_MS = 10 * 60_000L

    /** Whole-run budget, including waits. */
    const val MAX_RUN_MS = 30 * 60_000L

    const val MAX_REPEAT_ITERATIONS = 1000
    const val MAX_STEPS_PER_RUN = 5000

    /** Nesting of if/repeat bodies. */
    const val MAX_STEP_DEPTH = 8

    /** Queued runs held per task before the oldest is dropped. */
    const val MAX_QUEUE_DEPTH = 8

    /** Audit lines a single run may write before they are summarised. */
    const val MAX_AUDIT_STEPS_PER_RUN = 200

    fun clampStepTimeout(requested: Long?): Long =
        (requested ?: DEFAULT_STEP_TIMEOUT_MS).coerceIn(100L, MAX_STEP_TIMEOUT_MS)
}
