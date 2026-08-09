package ai.jarvis.app.automation.tasks

import android.os.SystemClock
import android.util.Log
import ai.jarvis.app.automation.actions.ActionRegistry
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.audit.AuditEntry
import ai.jarvis.app.automation.audit.AuditLog
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.Decision
import ai.jarvis.app.automation.policy.TrustLevel
import ai.jarvis.app.automation.triggers.TriggerEvent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID

/**
 * Runs a task's steps, in order, once.
 *
 * ## The two rules that matter
 *
 * **Everything goes through [ActionRegistry.dispatch].** There is no other way
 * for this class to touch the phone — no direct intent, no direct API call, no
 * "internal" shortcut for steps the task author marked trusted. That is what
 * makes the policy table apply to automations as much as to typed commands, and
 * it is why a CONFIRM step inside a task still shows its own consent prompt with
 * its own real parameters, every single time it runs.
 *
 * **A denial aborts the task.** Not "skip and continue", not "retry" — abort.
 * A task is a sequence someone reasoned about; running steps 4 through 9 after
 * the user refused step 3 executes a plan nobody approved. `continue_on_error`
 * exists for flaky networks and explicitly does not apply to a denial.
 *
 * ## Taint
 *
 * Untrusted text reaches a task from two places: a trigger whose source is
 * untrusted (a notification, a foreground-app change) and the reply to an
 * `ask_jarvis` step. Both are tracked:
 *
 *  * an untrusted TRIGGER makes the whole run untrusted — every action
 *    dispatches with [TrustLevel.UNTRUSTED], no exceptions;
 *  * an untrusted trigger PAYLOAD (a manual run whose data the server supplied)
 *    taints the variables it fills without degrading the whole run;
 *  * an `ask_jarvis` reply taints the variable it is stored in, and any later
 *    step whose parameters mention that variable dispatches untrusted too;
 *  * an ACTION RESULT taints its `store_as` variable whenever the action
 *    declares [ActionRegistry.producesUntrustedOutput] — `http_request`,
 *    `read_clipboard`, `read_file`, `read_calendar`, `read_screen`,
 *    `run_shell`, contact lookups. Those results are text somebody else wrote,
 *    and without this a task could launder a web page into a Tier-1 action's
 *    parameters simply by parking it in a variable first;
 *  * a CONDITION that reads a tainted variable degrades the rest of the run,
 *    because `if {{reply}} contains "yes" then <action>` lets injected text
 *    choose what happens even when the action's own parameters are constants.
 *
 * [ai.jarvis.app.automation.policy.PolicyEngine] turns any ALLOW into an ASK
 * for an untrusted request, so the strongest thing injected text can achieve is
 * a consent prompt that shows the user exactly what it wants to do.
 */
class TaskRunner(
    private val registry: ActionRegistry,
    private val audit: AuditLog,
    private val probe: ConditionProbe,
    private val ask: () -> AskJarvisClient?,
    private val events: EventWaiter,
    private val now: () -> Long = System::currentTimeMillis
) {

    /**
     * Execute [task]. Cancellation of the calling coroutine cancels the run:
     * the step in flight is abandoned and the result is [TaskStatus.CANCELLED].
     */
    suspend fun run(task: TaskDefinition, trigger: TriggerEvent?): TaskRunResult {
        val runId = UUID.randomUUID().toString().take(8)
        val startedAt = now()
        val startedUptime = SystemClock.elapsedRealtime()

        val state = RunState(
            task = task,
            runId = runId,
            // An untrusted trigger poisons the entire run, not just the steps
            // that mention its data. A task started by a notification cannot
            // auto-approve anything, whatever its steps look like.
            runTrust = if (trigger?.untrusted == true) TrustLevel.UNTRUSTED else TrustLevel.TRUSTED,
            deadlineUptime = startedUptime + TaskLimits.MAX_RUN_MS
        )

        trigger?.let { event ->
            state.variables["trigger"] = event.data + mapOf("_event" to event.triggerId)
            for ((key, value) in event.data) state.variables.putIfAbsent(key, value)
            // `dataTainted` is the weaker half of `untrusted`: a manual run whose
            // payload the SERVER supplied carries model-authored text, but the
            // trigger itself (a human tap, or an authenticated frame) is not a
            // reason to degrade a run that never interpolates that text.
            if (event.dataTainted) {
                state.tainted.add("trigger")
                state.tainted.addAll(event.data.keys)
            }
        }
        state.variables["task"] = mapOf("id" to task.id, "name" to task.name, "run_id" to runId)

        val outcome = try {
            executeAll(task.steps, state, depth = 0)
        } catch (t: CancellationException) {
            // NonCancellable, or nothing is written at all: `AuditLog.record`
            // suspends on Dispatchers.IO, and a suspend in an already-cancelled
            // coroutine throws before it does any work. Without this, a RESTART
            // that cancels a run in flight silently discards the audit lines for
            // every step that had already executed — and "every executed action
            // is written to the audit log" would be false exactly when it
            // matters most.
            withContext(NonCancellable) {
                recordRun(state, TaskStatus.CANCELLED, startedAt, "cancelled")
            }
            return state.result(TaskStatus.CANCELLED, startedAt, now(), "cancelled")
        } catch (t: Throwable) {
            Log.w(TAG, "task ${task.id} crashed", t)
            val message = "${t.javaClass.simpleName}: ${t.message ?: "task failed"}"
            recordRun(state, TaskStatus.ERROR, startedAt, message)
            return state.result(TaskStatus.ERROR, startedAt, now(), message)
        }

        recordRun(state, outcome.status, startedAt, outcome.message)
        return state.result(outcome.status, startedAt, now(), outcome.message)
    }

    // --- the loop -----------------------------------------------------------

    private suspend fun executeAll(steps: List<StepSpec>, state: RunState, depth: Int): StepFlow {
        if (depth > TaskLimits.MAX_STEP_DEPTH) {
            return StepFlow(TaskStatus.ERROR, "steps nested deeper than ${TaskLimits.MAX_STEP_DEPTH}")
        }
        for (step in steps) {
            if (SystemClock.elapsedRealtime() > state.deadlineUptime) {
                return StepFlow(TaskStatus.ERROR, "task exceeded its ${TaskLimits.MAX_RUN_MS} ms budget")
            }
            if (state.stepCount >= TaskLimits.MAX_STEPS_PER_RUN) {
                return StepFlow(TaskStatus.ERROR, "task executed too many steps")
            }
            val flow = executeOne(step, state, depth)
            if (flow.status != TaskStatus.OK) return flow
        }
        return StepFlow(TaskStatus.OK, null)
    }

    private suspend fun executeOne(step: StepSpec, state: RunState, depth: Int): StepFlow {
        state.stepCount++
        val index = state.stepCount
        val startedUptime = SystemClock.elapsedRealtime()

        // A per-step guard, available on ANY step type. Cheaper to read than
        // wrapping half a task in an `if`.
        step.condition?.let { guard ->
            if (step.type != StepType.IF && step.type != StepType.REPEAT) {
                state.noteConditionTaint(guard)
                val outcome = ConditionEvaluator.evaluate(guard, state.context())
                if (!outcome.passed) {
                    state.record(StepOutcome(index, step.type, TaskStatus.OK, skipped = true, note = outcome.reason))
                    recordStep(state, step, index, "skipped", outcome.reason, 0)
                    return StepFlow(TaskStatus.OK, null)
                }
            }
        }

        return when (step.type) {
            StepType.ACTION -> runAction(step, state, index, startedUptime)
            StepType.NOTIFY -> runNotify(step, state, index, startedUptime)
            StepType.WAIT -> runWait(step, state, index)
            StepType.WAIT_FOR_EVENT -> runWaitForEvent(step, state, index)
            StepType.SET_VARIABLE -> runSetVariable(step, state, index)
            StepType.IF -> runIf(step, state, index, depth)
            StepType.REPEAT -> runRepeat(step, state, index, depth)
            StepType.ASK_JARVIS -> runAskJarvis(step, state, index)
            StepType.STOP -> runStop(step, state, index)
        }
    }

    // --- steps --------------------------------------------------------------

    private suspend fun runAction(
        step: StepSpec,
        state: RunState,
        index: Int,
        startedUptime: Long
    ): StepFlow {
        val actionId = step.action?.trim()
        if (actionId.isNullOrEmpty()) {
            return failStep(state, step, index, "action step has no action id", startedUptime)
        }
        return dispatch(actionId, step, state, index, startedUptime)
    }

    /**
     * `notify` is sugar for `send_notification`, and it goes through the
     * registry like anything else. It is not a back door: if the user set that
     * action to `never`, a `notify` step is denied and the task aborts.
     */
    private suspend fun runNotify(
        step: StepSpec,
        state: RunState,
        index: Int,
        startedUptime: Long
    ): StepFlow = dispatch(TaskSafety.NOTIFY_ACTION_ID, step, state, index, startedUptime)

    private suspend fun dispatch(
        actionId: String,
        step: StepSpec,
        state: RunState,
        index: Int,
        startedUptime: Long
    ): StepFlow {
        val substituted = VariableSubstitution.substituteValue(step.params, state.variables)
        val params = TaskJson.mapToJson(substituted.value as? Map<String, Any?> ?: emptyMap())

        // The whole taint calculation, in one expression so it cannot drift.
        val touchedTainted = substituted.rootsUsed.any { it in state.tainted }
        val trust = if (state.runTrust == TrustLevel.UNTRUSTED || touchedTainted) {
            TrustLevel.UNTRUSTED
        } else {
            TrustLevel.TRUSTED
        }

        val reason = buildString {
            append("automation \"")
            append(state.task.name)
            append("\"")
            step.label?.let { append(" — ").append(it) }
            if (trust == TrustLevel.UNTRUSTED) {
                append(" (started from untrusted content — read the parameters carefully)")
            }
        }

        val result = registry.dispatch(
            actionId = actionId,
            params = params,
            // A task may declare a tier. Through max() it can only RAISE the
            // local one, so a task cannot make an action cheaper than the table
            // says it is.
            requestedTier = ActionTier.fromName(step.params["tier"]?.toString()),
            reason = reason,
            commandId = "${state.runId}#$index",
            trust = trust,
            source = "task:${state.task.id}"
        )

        val durationMs = SystemClock.elapsedRealtime() - startedUptime
        step.storeAs?.let { name ->
            // Two independent reasons to taint the result. The second is the one
            // that is easy to forget and expensive to get wrong: `read_calendar`,
            // `http_request`, `read_clipboard`, `read_file` and `read_screen` all
            // return text written by somebody else, so parking one in a variable
            // and interpolating it into the NEXT step's parameters would
            // otherwise launder untrusted content into a TRUSTED dispatch — and
            // a Tier-1 action would then run on it with no prompt at all.
            // `producesUntrustedOutput` answers true for an unknown id too.
            val resultTainted = trust == TrustLevel.UNTRUSTED ||
                registry.producesUntrustedOutput(actionId)
            state.setVariable(name, resultToVariable(result), tainted = resultTainted)
        }

        state.record(
            StepOutcome(
                index = index,
                type = step.type,
                status = statusFor(result),
                actionId = actionId,
                note = result.error,
                trust = trust,
                durationMs = durationMs
            )
        )
        recordStep(state, step, index, result.status.wire, "action=$actionId trust=$trust", durationMs)

        return when {
            result.status == ActionResult.Status.DENIED ->
                // Abort. Not skip. See the class docs.
                StepFlow(TaskStatus.DENIED, "step $index ($actionId) was denied: ${result.error}")

            result.ok -> StepFlow(TaskStatus.OK, null)

            step.continueOnError -> StepFlow(TaskStatus.OK, null)

            else -> StepFlow(TaskStatus.ERROR, "step $index ($actionId) failed: ${result.error}")
        }
    }

    private suspend fun runWait(step: StepSpec, state: RunState, index: Int): StepFlow {
        val requested = state.number(step, "ms", "milliseconds")
            ?: state.number(step, "seconds")?.times(1000)
            ?: state.number(step, "minutes")?.times(60_000)
            ?: 0.0
        val ms = requested.toLong().coerceIn(0L, TaskLimits.MAX_WAIT_MS)
        val remaining = state.deadlineUptime - SystemClock.elapsedRealtime()
        val actual = ms.coerceAtMost(remaining.coerceAtLeast(0))
        delay(actual)
        state.record(StepOutcome(index, step.type, TaskStatus.OK, note = "${actual} ms"))
        recordStep(state, step, index, "ok", "waited $actual ms", actual)
        return StepFlow(TaskStatus.OK, null)
    }

    /**
     * Block until a trigger fires. The classic use is "start navigation, then
     * wait until the car bluetooth disconnects".
     *
     * A timeout is not an error by default — a task that waited for something
     * that did not happen has simply finished waiting — unless the step sets
     * `"required": true`.
     */
    private suspend fun runWaitForEvent(step: StepSpec, state: RunState, index: Int): StepFlow {
        val wanted = (step.params["event"] ?: step.params["trigger"])?.toString()?.trim()
        if (wanted.isNullOrEmpty()) {
            return failStep(state, step, index, "wait_for_event has no event id", SystemClock.elapsedRealtime())
        }
        val timeout = TaskLimits.clampStepTimeout(step.timeoutMs)
        val budget = (state.deadlineUptime - SystemClock.elapsedRealtime()).coerceAtLeast(0)
        val event = events.await(wanted, timeout.coerceAtMost(budget))

        if (event != null) {
            state.setVariable(
                step.storeAs ?: "event",
                event.data,
                tainted = event.untrusted
            )
            // An untrusted event arriving mid-run degrades the rest of the run.
            if (event.untrusted) state.runTrust = TrustLevel.UNTRUSTED
        }

        val required = step.params["required"] == true
        val status = if (event != null || !required) TaskStatus.OK else TaskStatus.ERROR
        val note = if (event != null) "got $wanted" else "timed out waiting for $wanted"
        state.record(StepOutcome(index, step.type, status, note = note))
        recordStep(state, step, index, if (status == TaskStatus.OK) "ok" else "error", note, timeout)
        return if (status == TaskStatus.OK) StepFlow(TaskStatus.OK, null) else StepFlow(status, note)
    }

    private fun runSetVariable(step: StepSpec, state: RunState, index: Int): StepFlow {
        val name = (step.params["name"] ?: step.storeAs)?.toString()?.trim()
        if (name.isNullOrEmpty()) {
            state.record(StepOutcome(index, step.type, TaskStatus.ERROR, note = "no variable name"))
            return StepFlow(TaskStatus.ERROR, "set_variable has no name")
        }
        val substituted = VariableSubstitution.substituteValue(step.params["value"], state.variables)
        // Taint is contagious: a variable computed from a tainted one is tainted.
        val tainted = substituted.rootsUsed.any { it in state.tainted }
        state.setVariable(name, substituted.value, tainted)
        state.record(StepOutcome(index, step.type, TaskStatus.OK, note = "$name set"))
        recordStep(state, step, index, "ok", "set $name (tainted=$tainted)", 0)
        return StepFlow(TaskStatus.OK, null)
    }

    private suspend fun runIf(step: StepSpec, state: RunState, index: Int, depth: Int): StepFlow {
        val condition = step.condition
        val outcome = if (condition == null) {
            ConditionOutcome(false, "if step has no condition")
        } else {
            state.noteConditionTaint(condition)
            ConditionEvaluator.evaluate(condition, state.context())
        }
        state.record(StepOutcome(index, step.type, TaskStatus.OK, note = outcome.reason))
        recordStep(state, step, index, "ok", "if: ${outcome.reason}", 0)
        val branch = if (outcome.passed) step.then else step.otherwise
        return executeAll(branch, state, depth + 1)
    }

    private suspend fun runRepeat(step: StepSpec, state: RunState, index: Int, depth: Int): StepFlow {
        val body = step.steps
        if (body.isEmpty()) {
            state.record(StepOutcome(index, step.type, TaskStatus.OK, note = "empty body"))
            return StepFlow(TaskStatus.OK, null)
        }
        val fixedCount = step.count?.coerceIn(0, TaskLimits.MAX_REPEAT_ITERATIONS)
        var iterations = 0

        while (true) {
            if (SystemClock.elapsedRealtime() > state.deadlineUptime) {
                return StepFlow(TaskStatus.ERROR, "repeat at step $index ran out of budget")
            }
            if (fixedCount != null) {
                if (iterations >= fixedCount) break
            } else {
                val condition = step.condition
                    ?: return StepFlow(TaskStatus.ERROR, "repeat needs either count or a condition")
                state.noteConditionTaint(condition)
                if (!ConditionEvaluator.evaluate(condition, state.context()).passed) break
            }
            if (iterations >= TaskLimits.MAX_REPEAT_ITERATIONS) {
                return StepFlow(TaskStatus.ERROR, "repeat at step $index hit the iteration cap")
            }
            state.variables["repeat_index"] = iterations
            val flow = executeAll(body, state, depth + 1)
            if (flow.status != TaskStatus.OK) return flow
            iterations++
        }

        state.record(StepOutcome(index, step.type, TaskStatus.OK, note = "$iterations iteration(s)"))
        recordStep(state, step, index, "ok", "repeat x$iterations", 0)
        return StepFlow(TaskStatus.OK, null)
    }

    private suspend fun runAskJarvis(step: StepSpec, state: RunState, index: Int): StepFlow {
        val client = ask()
        val startedUptime = SystemClock.elapsedRealtime()
        if (client == null || !client.isConnected) {
            return failStep(state, step, index, "not connected to jarvis-core", startedUptime)
        }
        val promptTemplate = (step.params["prompt"] ?: step.params["text"])?.toString()
        if (promptTemplate.isNullOrBlank()) {
            return failStep(state, step, index, "ask_jarvis has no prompt", startedUptime)
        }
        val prompt = VariableSubstitution.substitute(promptTemplate, state.variables).text
        val timeout = TaskLimits.clampStepTimeout(step.timeoutMs)

        val reply = withTimeoutOrNull(timeout) {
            runCatching { client.ask(prompt, timeout) }.getOrNull()
        }
        val duration = SystemClock.elapsedRealtime() - startedUptime

        if (reply == null) {
            state.record(StepOutcome(index, step.type, TaskStatus.ERROR, note = "no reply", durationMs = duration))
            recordStep(state, step, index, "error", "ask_jarvis got no reply", duration)
            return if (step.continueOnError) StepFlow(TaskStatus.OK, null)
            else StepFlow(TaskStatus.ERROR, "ask_jarvis at step $index got no reply")
        }

        // ALWAYS tainted. The reply is model output, and the model reads the web.
        state.setVariable(step.storeAs ?: "reply", reply, tainted = true)
        state.record(
            StepOutcome(index, step.type, TaskStatus.OK, note = "${reply.length} chars", durationMs = duration)
        )
        recordStep(state, step, index, "ok", "ask_jarvis replied (tainted)", duration)
        return StepFlow(TaskStatus.OK, null)
    }

    private fun runStop(step: StepSpec, state: RunState, index: Int): StepFlow {
        val note = step.params["reason"]?.toString() ?: "stop step"
        state.record(StepOutcome(index, step.type, TaskStatus.OK, note = note))
        recordStep(state, step, index, "ok", "stopped: $note", 0)
        return StepFlow(TaskStatus.STOPPED, note)
    }

    // --- plumbing -----------------------------------------------------------

    private fun failStep(
        state: RunState,
        step: StepSpec,
        index: Int,
        message: String,
        startedUptime: Long
    ): StepFlow {
        val duration = SystemClock.elapsedRealtime() - startedUptime
        state.record(StepOutcome(index, step.type, TaskStatus.ERROR, note = message, durationMs = duration))
        recordStep(state, step, index, "error", message, duration)
        return if (step.continueOnError) StepFlow(TaskStatus.OK, null) else StepFlow(TaskStatus.ERROR, message)
    }

    private fun resultToVariable(result: ActionResult): Any? {
        val data = result.data ?: return result.ok
        return TaskJson.jsonToMap(data)
    }

    private fun statusFor(result: ActionResult): TaskStatus = when (result.status) {
        ActionResult.Status.OK -> TaskStatus.OK
        ActionResult.Status.DENIED -> TaskStatus.DENIED
        else -> TaskStatus.ERROR
    }

    /**
     * One audit line per step.
     *
     * Action steps produce two lines: this one, and the authoritative one the
     * dispatcher writes for the action itself. That is on purpose — the pair
     * gives a contiguous trace of the task alongside the record of what
     * actually ran, and the dispatcher's line stays the one to trust.
     *
     * Capped per run so a 1000-iteration loop cannot flush the log.
     */
    private fun recordStep(
        state: RunState,
        step: StepSpec,
        index: Int,
        status: String,
        note: String?,
        durationMs: Long
    ) {
        if (state.auditedSteps > TaskLimits.MAX_AUDIT_STEPS_PER_RUN) return
        state.auditedSteps++
        val suffix = if (state.auditedSteps == TaskLimits.MAX_AUDIT_STEPS_PER_RUN) {
            " (further steps in this run are not logged individually)"
        } else {
            ""
        }
        state.pendingAudit.add(
            AuditEntry(
                timestamp = now(),
                actionId = "task.step.${step.type.wire}",
                params = null,
                tier = ActionTier.AUTO,
                decision = Decision.ALLOW,
                status = status,
                ok = status == "ok" || status == "skipped",
                error = null,
                source = "task:${state.task.id}",
                commandId = "${state.runId}#$index",
                durationMs = durationMs,
                note = "${state.task.name} step $index${step.label?.let { " ($it)" } ?: ""}: " +
                    "${note ?: step.type.wire}$suffix"
            )
        )
    }

    private suspend fun recordRun(
        state: RunState,
        status: TaskStatus,
        startedAt: Long,
        message: String?
    ) {
        // Step entries are buffered and flushed here so a run appears in the
        // log as one contiguous block rather than interleaved with whatever
        // else the phone was doing.
        for (entry in state.pendingAudit) audit.record(entry)
        state.pendingAudit.clear()
        audit.record(
            AuditEntry(
                timestamp = now(),
                actionId = "task.run",
                params = null,
                tier = ActionTier.AUTO,
                decision = Decision.ALLOW,
                status = status.wire,
                ok = status == TaskStatus.OK || status == TaskStatus.STOPPED,
                error = message.takeIf { status != TaskStatus.OK && status != TaskStatus.STOPPED },
                source = "task:${state.task.id}",
                commandId = state.runId,
                durationMs = now() - startedAt,
                note = "${state.task.name}: ${state.stepCount} step(s), trust=${state.runTrust}" +
                    (message?.let { ", $it" } ?: "")
            )
        )
    }

    /** Internal control-flow signal. Not the public result. */
    private data class StepFlow(val status: TaskStatus, val message: String?)

    /** Everything one run needs to carry. Not shared between runs. */
    private inner class RunState(
        val task: TaskDefinition,
        val runId: String,
        var runTrust: TrustLevel,
        val deadlineUptime: Long
    ) {
        val variables = LinkedHashMap<String, Any?>()
        val tainted = LinkedHashSet<String>()
        val steps = ArrayList<StepOutcome>()
        val pendingAudit = ArrayList<AuditEntry>()
        var stepCount = 0
        var auditedSteps = 0

        fun setVariable(name: String, value: Any?, tainted: Boolean) {
            val key = name.trim().substringBefore('.')
            if (key.isEmpty()) return
            variables[key] = value
            if (tainted) this.tainted.add(key) else this.tainted.remove(key)
        }

        /**
         * Control flow is a channel too.
         *
         * `{"type":"if","condition":{"type":"variable","name":"reply",
         * "op":"contains","value":"yes"},"then":[{"type":"action", …}]}` lets an
         * `ask_jarvis` reply — or a notification body parked in a variable —
         * decide WHICH action runs, while that action's own parameters are
         * constants and would otherwise dispatch TRUSTED. Taint has to follow
         * the branch, not only the interpolation, so a condition that reads a
         * tainted variable degrades the rest of the run.
         *
         * Degrading the whole run rather than one branch is deliberate: the
         * decision has already been made by the time the branch is chosen, and
         * anything after the `if` is downstream of it.
         */
        fun noteConditionTaint(spec: ConditionSpec) {
            if (runTrust == TrustLevel.UNTRUSTED) return
            if (tainted.isEmpty()) return
            val roots = ConditionEvaluator.variableRoots(spec)
            if (roots.any { it in tainted }) {
                runTrust = TrustLevel.UNTRUSTED
                Log.i(TAG, "run $runId degraded to UNTRUSTED: a condition read a tainted variable")
            }
        }

        fun context(): ConditionContext = probe.sample().withVariables(variables)

        fun number(step: StepSpec, vararg keys: String): Double? {
            for (key in keys) {
                when (val v = step.params[key]) {
                    is Number -> return v.toDouble()
                    is String -> {
                        val expanded = VariableSubstitution.substitute(v, variables).text
                        expanded.trim().toDoubleOrNull()?.let { return it }
                    }

                    else -> Unit
                }
            }
            return null
        }

        fun record(outcome: StepOutcome) {
            steps.add(outcome)
        }

        fun result(status: TaskStatus, startedAt: Long, finishedAt: Long, message: String?) =
            TaskRunResult(
                taskId = task.id,
                taskName = task.name,
                runId = runId,
                status = status,
                steps = steps.toList(),
                variables = variables.toMap(),
                trust = runTrust,
                startedAtMs = startedAt,
                finishedAtMs = finishedAt,
                message = message
            )
    }

    companion object {
        private const val TAG = "JarvisTasks"
    }
}

/** How a run, or a step, ended. */
enum class TaskStatus(val wire: String) {
    OK("ok"),

    /** A `stop` step. Deliberate, and a success. */
    STOPPED("stopped"),

    /** Policy said no. The task aborted; nothing after the denial ran. */
    DENIED("denied"),

    ERROR("error"),

    /** The mode is RESTART and a newer trigger took over, or the service stopped. */
    CANCELLED("cancelled"),

    /** SINGLE mode, already running. Nothing happened. */
    SKIPPED("skipped")
}

/** What one step did. */
data class StepOutcome(
    val index: Int,
    val type: StepType,
    val status: TaskStatus,
    val actionId: String? = null,
    val skipped: Boolean = false,
    val note: String? = null,
    val trust: TrustLevel = TrustLevel.TRUSTED,
    val durationMs: Long = 0
)

/** What one run did. Structured so the UI and the server get the same story. */
data class TaskRunResult(
    val taskId: String,
    val taskName: String,
    val runId: String,
    val status: TaskStatus,
    val steps: List<StepOutcome> = emptyList(),
    val variables: Map<String, Any?> = emptyMap(),
    val trust: TrustLevel = TrustLevel.TRUSTED,
    val startedAtMs: Long = 0,
    val finishedAtMs: Long = 0,
    val message: String? = null
) {
    val durationMs: Long get() = (finishedAtMs - startedAtMs).coerceAtLeast(0)

    /** Compact form for a `device_event`, with no variable VALUES in it. */
    fun toEventData(): Map<String, Any?> = mapOf(
        "task_id" to taskId,
        "task_name" to taskName,
        "run_id" to runId,
        "status" to status.wire,
        "steps" to steps.size,
        "duration_ms" to durationMs,
        "trust" to trust.name.lowercase(),
        "message" to message
    )
}

/** Samples the world for [ConditionEvaluator]. Implemented over the platform. */
interface ConditionProbe {
    fun sample(): ConditionContext
}

/** Lets a `wait_for_event` step block on a trigger. Implemented by `TaskEngine`. */
interface EventWaiter {
    suspend fun await(triggerId: String, timeoutMs: Long): TriggerEvent?
}
