package ai.jarvis.app.automation.tasks

import ai.jarvis.app.automation.policy.ActionTier

/**
 * PURE LOGIC — no Android imports, no org.json.
 *
 * Whether a task the server pushed may be switched on without a human looking
 * at it.
 *
 * ## The problem this solves
 *
 * A task can be authored by the language model. The model runs on the server,
 * reads web pages, and can be wrong or injected. A pushed task is therefore a
 * *proposal*, and the dangerous version of that proposal is not a single bad
 * action — the policy engine already prompts for those — it is a task that
 * looks harmless in the list and contains one CONFIRM step somewhere in a
 * branch, twenty steps down, that the user will approve out of habit when the
 * prompt appears at 3am attached to an automation they never read.
 *
 * So: **a pushed task containing any CONFIRM-tier action is not auto-enabled.**
 * It is stored, listed and shown, and it stays off until a human turns it on
 * in the app.
 *
 * ## What this deliberately is not
 *
 * It is not a second approval gate. Enabling a task never pre-approves
 * anything: every CONFIRM step still prompts, every single time it runs, with
 * its own real parameters. There is no batch approval anywhere in this package
 * and there is no way to add one — [TaskRunner] has no path to the registry
 * other than `dispatch`, which consults the policy engine per call.
 *
 * ## Failing closed
 *
 * An action id this build does not know is treated as CONFIRM. A task
 * referencing `send_email` on a phone with no such action must not be waved
 * through because the tier lookup returned null.
 */
object TaskSafety {

    /**
     * Every action id the task can dispatch, walking into `if` branches and
     * `repeat` bodies. `notify` counts: it dispatches `send_notification`.
     */
    fun collectActionIds(steps: List<StepSpec>): List<String> {
        val out = ArrayList<String>()
        collect(steps, out, 0)
        return out
    }

    private fun collect(steps: List<StepSpec>, into: MutableList<String>, depth: Int) {
        if (depth > TaskLimits.MAX_STEP_DEPTH) return
        for (step in steps) {
            when (step.type) {
                StepType.ACTION -> step.action?.trim()?.takeIf { it.isNotEmpty() }?.let(into::add)
                StepType.NOTIFY -> into.add(NOTIFY_ACTION_ID)
                else -> Unit
            }
            collect(step.then, into, depth + 1)
            collect(step.otherwise, into, depth + 1)
            collect(step.steps, into, depth + 1)
        }
    }

    /**
     * Screen a task against the local action table.
     *
     * @param tierOf the local tier for an action id, or null when this build
     *   has no such action. Wire this to `ActionRegistry[id]?.tier`.
     */
    fun screen(task: TaskDefinition, tierOf: (String) -> ActionTier?): TaskAdmission {
        val ids = collectActionIds(task.steps).distinct()
        val confirm = ArrayList<String>()
        val unknown = ArrayList<String>()

        for (id in ids) {
            val tier = tierOf(id)
            when {
                tier == null -> unknown.add(id)
                tier == ActionTier.CONFIRM -> confirm.add(id)
                else -> Unit
            }
        }

        val blocked = confirm.isNotEmpty() || unknown.isNotEmpty()
        val reason = when {
            confirm.isNotEmpty() && unknown.isNotEmpty() ->
                "contains confirm-tier actions (${confirm.joinToString()}) and " +
                    "actions this build does not have (${unknown.joinToString()})"

            confirm.isNotEmpty() ->
                "contains confirm-tier actions: ${confirm.joinToString()}"

            unknown.isNotEmpty() ->
                "contains actions this build does not have, which are treated as " +
                    "confirm-tier: ${unknown.joinToString()}"

            else -> "contains no confirm-tier actions"
        }

        return TaskAdmission(
            mayAutoEnable = !blocked,
            confirmActions = confirm,
            unknownActions = unknown,
            reason = reason
        )
    }

    /**
     * The effective `enabled` for a task that has just arrived or been edited.
     *
     * The rules, in one place:
     *
     *  * A task the user wrote HERE is whatever they set. They are sitting in
     *    front of it in the editor.
     *  * Anything else that passes screening may arrive enabled — nothing in it
     *    can act without at least a Tier-2 prompt.
     *  * Anything else that fails screening is off until
     *    [TaskDefinition.enabledByUser]. The server cannot set that flag: it is
     *    stripped on import.
     *
     * @param authoredLocally true ONLY when this task was just written in this
     *   app's own task editor. It is deliberately not the same question as
     *   `task.source == LOCAL`: `source` is a field, and a JSON bundle the user
     *   was sent — by mail, by a chat app, by a server writing a file — can
     *   simply claim `"source": "LOCAL"`. Trusting the field would let an
     *   imported task with a `send_sms` step arrive switched on and unscreened,
     *   which is the one outcome this whole object exists to prevent. So
     *   `TaskStore.import` passes false, and only the editor passes true.
     */
    fun effectiveEnabled(
        task: TaskDefinition,
        admission: TaskAdmission,
        authoredLocally: Boolean = task.source == TaskSource.LOCAL
    ): Boolean = when {
        authoredLocally -> task.enabled
        admission.mayAutoEnable -> task.enabled
        else -> task.enabledByUser && task.enabled
    }

    /**
     * True when an edit invalidates a previous human enablement.
     *
     * Steps, triggers and conditions are all part of what was approved. Renaming
     * a task or changing its description is not. If the executable part changed,
     * [TaskDefinition.enabledByUser] is cleared and the user is asked again —
     * otherwise a server could get a task approved as one thing and then quietly
     * make it another.
     */
    fun requiresReconsent(previous: TaskDefinition?, updated: TaskDefinition): Boolean {
        if (previous == null) return false
        if (!previous.enabledByUser) return false
        return previous.steps != updated.steps ||
            previous.triggers != updated.triggers ||
            previous.conditions != updated.conditions
    }

    /** The action a `notify` step dispatches. Kept here so screening sees it. */
    const val NOTIFY_ACTION_ID = "send_notification"
}

/**
 * What screening decided, and enough detail for the task list to say why a task
 * is sitting there switched off.
 */
data class TaskAdmission(
    val mayAutoEnable: Boolean,
    val confirmActions: List<String> = emptyList(),
    val unknownActions: List<String> = emptyList(),
    val reason: String = ""
) {
    /** True when the user has to turn this on by hand. */
    val needsUserEnablement: Boolean get() = !mayAutoEnable
}
