package ai.jarvis.app.automation.tasks

import android.util.Log
import ai.jarvis.app.automation.notify.NotificationBus
import ai.jarvis.app.automation.triggers.TriggerEvent
import ai.jarvis.app.automation.triggers.TriggerIds
import ai.jarvis.app.automation.triggers.TriggerMatch
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.ConcurrentHashMap

/**
 * Routes trigger events to tasks and owns their concurrency.
 *
 * The trigger layer knows nothing about tasks; the runner knows nothing about
 * triggers. This is the join, and it is deliberately the only place that knows
 * both — which means the question "can a notification start something?" has one
 * answer, in one file, rather than being spread over fifteen trigger classes.
 *
 * ## What happens to an event
 *
 * 1. Published to [eventStream], so any `wait_for_event` step blocked on it wakes.
 * 2. Matched against every enabled task's trigger specs ([TriggerMatch]).
 * 3. For each match: conditions are evaluated once, against a single sample of
 *    the world, so two conditions in the same task cannot disagree about the
 *    battery level.
 * 4. Started, restarted, queued or skipped according to [TaskMode].
 *
 * The event's trust travels with it the whole way and ends up on the run — see
 * [TaskRunner].
 */
class TaskEngine(
    private val scope: CoroutineScope,
    private val store: TaskStore,
    private val runner: TaskRunner,
    /** Samples the world once per event, for the task-level conditions. */
    private val probe: ConditionProbe,
    /** Where a finished run and every trigger event get reported. */
    private val deviceEvents: () -> DeviceEventSink?,
    private val now: () -> Long = System::currentTimeMillis
) : EventWaiter {

    private val eventStream = MutableSharedFlow<TriggerEvent>(
        replay = 0,
        extraBufferCapacity = 64,
        // A slow `wait_for_event` must not stall the trigger that is publishing.
        onBufferOverflow = BufferOverflow.DROP_OLDEST
    )

    private val running = ConcurrentHashMap<String, Job>()
    private val queues = ConcurrentHashMap<String, ArrayDeque<TriggerEvent>>()
    private val queueLock = Mutex()

    private val listeners = java.util.concurrent.CopyOnWriteArrayList<(TaskRunResult) -> Unit>()

    /** Set false by the service on panic or when the master switch goes off. */
    @Volatile
    var accepting: Boolean = true

    /**
     * Forward every trigger event to the server, not only finished runs.
     *
     * On by default because the server's model is more useful when it knows the
     * phone just got in the car — but it is a real privacy dial (it includes
     * fenced notification payloads for the packages a task named), so
     * `AutomationPrefs.reportTriggersToServer` turns it off without disabling
     * the automations themselves.
     */
    @Volatile
    var reportTriggerEvents: Boolean = true

    // --- the front door -----------------------------------------------------

    /**
     * One trigger fired. Never throws and never blocks the caller: triggers are
     * called from broadcast receivers and platform callbacks.
     */
    fun onTriggerEvent(event: TriggerEvent) {
        if (!accepting) {
            Log.d(TAG, "ignoring ${event.triggerId}: automation is paused")
            return
        }
        scope.launch {
            // Wake anything waiting for this event first, so a `wait_for_event`
            // in a running task sees it even if no task is triggered by it.
            eventStream.emit(event)
            reportToServer(event)
            dispatchToTasks(event)
        }
    }

    private fun reportToServer(event: TriggerEvent) {
        if (!reportTriggerEvents) return
        val sink = deviceEvents() ?: return
        if (!sink.isConnected) return
        runCatching { sink.sendEvent(event.triggerId, event.data) }
            .onFailure { Log.d(TAG, "could not report ${event.triggerId}", it) }
    }

    private suspend fun dispatchToTasks(event: TriggerEvent) {
        val tasks = store.enabled().filter { task ->
            task.triggers.any { TriggerMatch.matches(it.type, it.params, event) }
        }
        if (tasks.isEmpty()) return

        // One sample of the world for this event, shared by every task and
        // every condition it contains. Re-reading per condition would let
        // `battery_above: 30` and `battery_below: 80` disagree about the same
        // battery, which is the kind of bug nobody ever reproduces.
        val context = try {
            probe.sample()
        } catch (t: Throwable) {
            Log.w(TAG, "could not sample device state; conditions will fail closed", t)
            null
        }

        for (task in tasks) {
            if (task.conditions.isNotEmpty()) {
                if (context == null) {
                    Log.i(TAG, "task ${task.id} not run: device state unavailable")
                    continue
                }
                val outcome = ConditionEvaluator.evaluateAll(task.conditions, context)
                if (!outcome.passed) {
                    Log.i(TAG, "task ${task.id} not run: ${outcome.reason}")
                    continue
                }
            }
            start(task, event)
        }
    }

    // --- modes --------------------------------------------------------------

    /**
     * Serialises the mode decision PER TASK.
     *
     * `onTriggerEvent` launches a coroutine per event, so two broadcasts a
     * millisecond apart genuinely race here. Without this, both would read
     * `running[task.id]` as idle and a `SINGLE` task would run twice — the one
     * mode whose entire contract is that it does not — and a `RESTART` task
     * would cancel one job, launch two, and leave the one that lost the write to
     * `running` executing with nothing tracking it.
     *
     * Per task rather than global: a `RESTART` that joins a cancelled run must
     * not hold up an unrelated task's trigger.
     */
    private val startLocks = ConcurrentHashMap<String, Mutex>()

    private suspend fun start(task: TaskDefinition, event: TriggerEvent?) =
        startLocks.getOrPut(task.id) { Mutex() }.withLock { startLocked(task, event) }

    private suspend fun startLocked(task: TaskDefinition, event: TriggerEvent?) {
        when (task.mode) {
            TaskMode.SINGLE -> {
                if (running[task.id]?.isActive == true) {
                    Log.i(TAG, "task ${task.id} is already running (SINGLE); skipping")
                    return
                }
                launchRun(task, event)
            }

            TaskMode.RESTART -> {
                running[task.id]?.let { job ->
                    job.cancel(CancellationException("restarted by a newer trigger"))
                    job.join()
                }
                launchRun(task, event)
            }

            TaskMode.QUEUED -> {
                if (running[task.id]?.isActive == true) {
                    queueLock.withLock {
                        val queue = queues.getOrPut(task.id) { ArrayDeque() }
                        while (queue.size >= TaskLimits.MAX_QUEUE_DEPTH) queue.removeFirst()
                        if (event != null) queue.addLast(event)
                    }
                    return
                }
                launchRun(task, event)
            }
        }
    }

    private fun launchRun(task: TaskDefinition, event: TriggerEvent?) {
        val job = scope.launch {
            val result = try {
                runner.run(task, event)
            } catch (t: CancellationException) {
                TaskRunResult(task.id, task.name, "-", TaskStatus.CANCELLED, message = "cancelled")
            } catch (t: Throwable) {
                Log.w(TAG, "task ${task.id} escaped the runner", t)
                TaskRunResult(task.id, task.name, "-", TaskStatus.ERROR, message = t.message)
            }
            publish(result)
            drainQueue(task)
        }
        running[task.id] = job
        job.invokeOnCompletion { running.remove(task.id, job) }
    }

    private suspend fun drainQueue(task: TaskDefinition) {
        // Non-QUEUED modes return before taking anything, which is also what
        // keeps this deadlock-free: RESTART is the only mode that joins a job
        // while holding the start lock, and a RESTART task never gets here.
        if (task.mode != TaskMode.QUEUED) return
        // Same lock as `start`, and taken before `queueLock` in both places, so
        // draining the queue cannot race a fresh trigger into a second run.
        startLocks.getOrPut(task.id) { Mutex() }.withLock {
            val next = queueLock.withLock { queues[task.id]?.removeFirstOrNull() } ?: return
            // Re-read the task: it may have been disabled or edited while we ran.
            val fresh = store.get(task.id) ?: return
            if (!fresh.isRunnable() || !accepting) return
            if (running[task.id]?.isActive == true) return
            launchRun(fresh, next)
        }
    }

    private fun publish(result: TaskRunResult) {
        for (listener in listeners) runCatching { listener(result) }
        val sink = deviceEvents()
        if (sink != null && sink.isConnected) {
            runCatching { sink.sendEvent("task_run", result.toEventData()) }
        }
        if (result.status == TaskStatus.DENIED) {
            Log.i(TAG, "task ${result.taskId} aborted: ${result.message}")
        }
    }

    // --- manual runs --------------------------------------------------------

    /**
     * Run one task now, because the user tapped it or the server asked.
     *
     * Even a server-requested run is only a *run*: every step still goes
     * through the policy table, and a disabled task stays disabled. "Run this
     * now" is not a way to execute something the user switched off.
     *
     * The task's own CONDITIONS are enforced here too. They are part of what the
     * user approved — `TaskSafety.requiresReconsent` treats editing them exactly
     * like editing a step — so "only when I am at home", "only on weekdays" and
     * "only while charging" must hold for a run the server asked for as much as
     * for one a trigger started. Without that, `runNow` would be a way to
     * execute a restricted task outside its restrictions.
     *
     * @param dataTrusted see [ai.jarvis.app.automation.triggers.ManualTriggers.fire].
     *   Only a local tap may pass true.
     * @param force skips the conditions. Reserved for a deliberate local
     *   override — a "run it anyway" button the user pressed while looking at
     *   the reason it would not run. Nothing on the command path may pass true.
     */
    suspend fun runNow(
        taskId: String,
        data: Map<String, Any?> = emptyMap(),
        dataTrusted: Boolean = false,
        force: Boolean = false
    ): Boolean {
        if (!accepting) return false
        val task = store.get(taskId) ?: return false
        if (!task.enabled) {
            Log.i(TAG, "refusing to run $taskId: it is switched off")
            return false
        }
        if (!force && task.conditions.isNotEmpty()) {
            val context = try {
                probe.sample()
            } catch (t: Throwable) {
                Log.w(TAG, "could not sample device state; $taskId not run", t)
                null
            } ?: return false
            val outcome = ConditionEvaluator.evaluateAll(task.conditions, context)
            if (!outcome.passed) {
                Log.i(TAG, "refusing to run $taskId: ${outcome.reason}")
                return false
            }
        }
        start(
            task,
            TriggerEvent(
                triggerId = TriggerIds.MANUAL,
                data = data + mapOf("id" to taskId),
                atMs = now(),
                dataTainted = !dataTrusted && data.isNotEmpty()
            )
        )
        return true
    }

    /** Cancel a run in flight. */
    fun cancel(taskId: String): Boolean {
        val job = running[taskId] ?: return false
        job.cancel(CancellationException("cancelled by the user"))
        return true
    }

    fun cancelAll() {
        for ((_, job) in running) job.cancel(CancellationException("automation stopped"))
        running.clear()
        queues.clear()
    }

    val runningTaskIds: Set<String>
        get() = running.entries.filter { it.value.isActive }.mapTo(LinkedHashSet()) { it.key }

    // --- EventWaiter --------------------------------------------------------

    override suspend fun await(triggerId: String, timeoutMs: Long): TriggerEvent? =
        withTimeoutOrNull(timeoutMs) {
            eventStream.first { it.triggerId == triggerId }
        }

    // --- run notifications for the UI ---------------------------------------

    fun addRunListener(listener: (TaskRunResult) -> Unit) {
        listeners.add(listener)
    }

    fun removeRunListener(listener: (TaskRunResult) -> Unit) {
        listeners.remove(listener)
    }

    // --- keeping the world in sync with the task list -----------------------

    /**
     * Recompute everything that depends on which tasks exist.
     *
     * Right now that is one thing, and it is important: the notification
     * listener's allow-list. Notification access lets this app read every
     * message on the phone, so nothing is reported unless a task named that
     * package — and when the last such task is deleted, the allow-list empties
     * and the listener goes back to discarding everything it sees.
     */
    suspend fun onTasksChanged() {
        val packages = LinkedHashSet<String>()
        for (task in store.enabled()) packages.addAll(task.notificationPackages())
        NotificationBus.updateAllowedPackages(packages)
        Log.i(TAG, "notification allow-list: ${packages.size} package(s)")
    }

    companion object {
        private const val TAG = "JarvisTasks"
    }
}
