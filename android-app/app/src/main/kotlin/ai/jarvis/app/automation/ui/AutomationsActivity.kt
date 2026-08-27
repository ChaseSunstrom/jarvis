package ai.jarvis.app.automation.ui

import ai.jarvis.app.automation.AutomationRuntime
import ai.jarvis.app.automation.JarvisAutomationService
import ai.jarvis.app.automation.policy.PolicyStore
import ai.jarvis.app.automation.tasks.TaskAdmission
import ai.jarvis.app.automation.tasks.TaskDefinition
import ai.jarvis.app.automation.tasks.TaskRunResult
import ai.jarvis.app.ui.JarvisUi
import android.app.Activity
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * The automations screen — the list of tasks this phone will run by itself.
 *
 * `AndroidManifest.xml` has declared this activity for a long time and
 * `ai.jarvis.app.ui.JarvisScreens` has launched it by name, but no class with
 * this name existed, so both entry points (the home screen's AUTOMATIONS button
 * and Settings') resolved to a toast. That is also why nothing could ever put a
 * task in the store: `TaskStore.upsert` has no other caller in shipping code.
 *
 * Read-only, deliberately, for a first cut. There is no rule editor here. What
 * it does provide is the two things the automation layer cannot work without
 * and had no route to:
 *
 *  * **Consent.** `TaskStore.setEnabledByUser` is the only path that may set a
 *    task's consent flag, and it exists solely for this screen. A pushed task
 *    containing a CONFIRM-tier action arrives switched off and stays off until
 *    a human turns it on *here*, having read the reason underneath it.
 *  * **Visibility.** Which tasks exist, which are running, and whether the
 *    master switch or the panic flag is holding everything down.
 *
 * Two levels, following `ai.jarvis.app.ui.CrashLogActivity`: a list, and one
 * task. Back steps out of the detail before it leaves the screen.
 */
class AutomationsActivity : Activity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val main = Handler(Looper.getMainLooper())

    private lateinit var root: FrameLayout
    private var runtime: AutomationRuntime.Runtime? = null
    private var policy: PolicyStore? = null

    /** Id of the task whose detail is open, or null for the list. */
    private var showing: String? = null

    /** Set by RUN NOW / the run listener, shown under the header. */
    private var notice: String? = null

    /** Both fire from background coroutines; both re-render on the main thread. */
    private val taskListener: (List<TaskDefinition>) -> Unit = { main.post { refresh() } }
    private val runListener: (TaskRunResult) -> Unit = { result ->
        main.post {
            notice = "${result.taskName}: ${result.status.wire}" +
                (result.message?.let { " — $it" } ?: "")
            refresh()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        root = FrameLayout(this).apply { setBackgroundColor(JarvisUi.BG) }
        setContentView(root.also { JarvisUi.fitSystemBars(it) })

        // Building the runtime is what fills AutomationBridge.dispatcher and
        // ActionEnv; it is safe to call from anywhere and idempotent. Wrapped
        // because a screen that cannot show the task list must still open and
        // say so rather than crash the app from a nav button.
        runtime = try {
            AutomationRuntime.ensure(applicationContext)
        } catch (t: Throwable) {
            Log.w(TAG, "the automation runtime could not be built", t)
            null
        }
        policy = try {
            PolicyStore(applicationContext)
        } catch (t: Throwable) {
            Log.w(TAG, "the policy store could not be opened", t)
            null
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        // ensure() constructs the TriggerManager but never starts it — triggers
        // are only live inside the running foreground service. Without this the
        // "N triggers active" line reads 0 on a phone whose automations are
        // simply not running yet, which reads as "none configured".
        runCatching { JarvisAutomationService.ensureRunning(this, "automations-screen") }
            .onFailure { Log.w(TAG, "could not start the automation service", it) }
        runtime?.let {
            it.tasks.addListener(taskListener)
            it.engine.addRunListener(runListener)
        }
        refresh()
    }

    override fun onPause() {
        runtime?.let {
            it.tasks.removeListener(taskListener)
            it.engine.removeRunListener(runListener)
        }
        super.onPause()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /**
     * Back steps out of a task before it leaves the screen.
     *
     * The classic callback rather than an `OnBackInvokedCallback`: the manifest
     * sets `enableOnBackInvokedCallback="false"` for the whole app.
     */
    @Suppress("DEPRECATION", "MissingSuperCall")
    override fun onBackPressed() {
        if (showing != null) {
            showing = null
            refresh()
            return
        }
        super.onBackPressed()
    }

    // --- rendering ----------------------------------------------------------

    private fun refresh() {
        val rt = runtime
        if (rt == null) {
            replaceContent(unavailableColumn())
            return
        }
        scope.launch {
            val tasks = try {
                rt.tasks.all()
            } catch (t: Throwable) {
                Log.w(TAG, "could not read the task store", t)
                emptyList()
            }
            val admissions = HashMap<String, TaskAdmission>()
            for (task in tasks) {
                runCatching { rt.tasks.admissionFor(task.id) }.getOrNull()
                    ?.let { admissions[task.id] = it }
            }
            val open = showing?.let { id -> tasks.firstOrNull { it.id == id } }
            replaceContent(
                if (open != null) detailColumn(open, admissions[open.id])
                else listColumn(tasks, admissions)
            )
        }
    }

    private fun unavailableColumn(): LinearLayout {
        val col = JarvisUi.column(this, padDp = JarvisUi.Space.SCREEN)
        col.addView(JarvisUi.screenTitle(this, "Phone tasks", "The tasks this phone runs by itself, and the consent each one has."))
        col.addView(
            JarvisUi.hint(
                this,
                "The automation runtime could not be started on this device. Nothing " +
                    "will run automatically until it can; the crash log may say why."
            )
        )
        return col
    }

    private fun listColumn(
        tasks: List<TaskDefinition>,
        admissions: Map<String, TaskAdmission>,
    ): LinearLayout {
        val rt = runtime
        val col = JarvisUi.column(this, padDp = JarvisUi.Space.SCREEN)
        col.addView(JarvisUi.screenTitle(this, "Phone tasks", "The tasks this phone runs by itself, and the consent each one has."))

        val store = policy
        val live = store?.automationLive ?: false
        val state = when {
            store == null -> "policy unavailable"
            store.panic -> "PANIC — everything is stopped"
            !store.automationEnabled -> "paused"
            else -> "live"
        }
        val running = rt?.engine?.runningTaskIds?.size ?: 0
        val activeTriggers = rt?.triggers?.activeIds?.size ?: 0
        col.addView(
            JarvisUi.hint(
                this,
                "${tasks.size} task(s), ${tasks.count { it.isRunnable() }} runnable, " +
                    "$running running now. $activeTriggers trigger(s) active — that is 0 " +
                    "whenever the automation service is not running, which is not the same " +
                    "as no triggers configured. Automation is $state."
            )
        )

        notice?.let { col.addView(JarvisUi.hint(this, it)) }

        if (store != null && !store.panic) {
            col.addView(
                JarvisUi.button(this, if (live) "PAUSE AUTOMATIONS" else "RESUME AUTOMATIONS") {
                    store.automationEnabled = !store.automationEnabled
                    runCatching {
                        JarvisAutomationService.ensureRunning(this, "automations-screen")
                    }
                    refresh()
                },
                matchWidth().apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.GAP) }
            )
        }

        // The kill switch, which nothing could set.
        //
        // `panic` outranks the master switch and every remembered "always
        // allow": PolicyEngine returns DENY on it before it looks at anything
        // else, the boot receiver refuses to restart triggers under it, and
        // this very screen already knew how to render "PANIC — everything is
        // stopped". Four readers, no writer. The state was unreachable, and so
        // was the way out of it — which is why CLEAR is on the same button
        // rather than somewhere else.
        if (store != null) {
            col.addView(
                JarvisUi.button(this, if (store.panic) "CLEAR PANIC" else "PANIC") {
                    val turningOn = !store.panic
                    JarvisAutomationService.panic(this, turningOn)
                    notice = if (turningOn) {
                        "Panic is on. Nothing runs — no command from the server, no " +
                            "trigger, no task — until you clear it. Triggers are " +
                            "unregistered too, so the phone is not watching either."
                    } else {
                        "Panic cleared."
                    }
                    refresh()
                },
                matchWidth().apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.STEP) }
            )
        }

        if (tasks.isEmpty()) {
            col.addView(JarvisUi.spacer(this, JarvisUi.Space.WIDE))
            col.addView(
                TextView(this).apply {
                    text = "No automations yet."
                    setTextColor(JarvisUi.FAINT)
                    textSize = JarvisUi.Type.BODY
                    gravity = Gravity.CENTER
                    typeface = Typeface.MONOSPACE
                }
            )
            col.addView(
                JarvisUi.hint(
                    this,
                    "Tasks arrive from the house over the device channel — its " +
                        "import_tasks action, which you confirm on this phone. One containing " +
                        "an action that needs confirming arrives switched off, and this is " +
                        "where you turn it on."
                )
            )
        } else {
            col.addView(JarvisUi.label(this, "TASKS"))
            for (task in tasks) {
                col.addView(
                    rowFor(task, admissions[task.id]),
                    matchWidth().apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.STEP) }
                )
            }
        }

        col.addView(JarvisUi.spacer(this, JarvisUi.Space.WIDE))
        return col
    }

    private fun rowFor(task: TaskDefinition, admission: TaskAdmission?): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val p = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.GAP)
            setPadding(p, p, p, p)
            background = JarvisUi.panel(this@AutomationsActivity)

            val isRunning = runtime?.engine?.runningTaskIds?.contains(task.id) == true
            addView(
                TextView(this@AutomationsActivity).apply {
                    text = task.name + if (isRunning) "  · RUNNING" else ""
                    setTextColor(if (task.enabled) JarvisUi.APPROVE else JarvisUi.FAINT)
                    textSize = JarvisUi.Type.BODY
                    typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
                }
            )
            task.description?.takeIf { it.isNotBlank() }?.let { text ->
                addView(
                    TextView(this@AutomationsActivity).apply {
                        this.text = text
                        setTextColor(JarvisUi.DIM)
                        textSize = JarvisUi.Type.HINT
                        setPadding(0, JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.TIGHT), 0, 0)
                    }
                )
            }
            addView(
                TextView(this@AutomationsActivity).apply {
                    text = "${task.source.name.lowercase()} · " +
                        "on ${task.triggerTypes().joinToString(", ").ifEmpty { "nothing" }} · " +
                        "does ${task.actionIds().joinToString(", ").ifEmpty { "nothing" }}"
                    setTextColor(JarvisUi.FAINT)
                    textSize = JarvisUi.Type.LABEL
                    typeface = Typeface.MONOSPACE
                    setPadding(0, JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.TIGHT), 0, 0)
                }
            )

            // Why it is off. Shown verbatim so the user is turning it on with
            // the reason in front of them, not despite it.
            val heldOff = admission?.takeIf { it.needsUserEnablement }?.reason.orEmpty()
            if (heldOff.isNotBlank()) {
                addView(
                    TextView(this@AutomationsActivity).apply {
                        text = heldOff
                        setTextColor(JarvisUi.DENY_TEXT)
                        textSize = JarvisUi.Type.LABEL
                        setPadding(0, JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.SNUG), 0, 0)
                    }
                )
            }

            addView(
                JarvisUi.button(this@AutomationsActivity, if (task.enabled) "ON" else "OFF") {
                    toggleEnabled(task)
                },
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.STEP) }
            )

            setOnClickListener {
                showing = task.id
                refresh()
            }
        }

    private fun detailColumn(task: TaskDefinition, admission: TaskAdmission?): LinearLayout {
        val col = JarvisUi.column(this, padDp = JarvisUi.Space.SCREEN)
        col.addView(JarvisUi.screenTitle(this, "Task"))
        col.addView(
            TextView(this).apply {
                text = task.name
                setTextColor(JarvisUi.ACCENT)
                textSize = JarvisUi.Type.FIELD
                gravity = Gravity.CENTER
                typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            }
        )

        val heldOff = admission?.takeIf { it.needsUserEnablement }?.reason.orEmpty()
        val isRunning = runtime?.engine?.runningTaskIds?.contains(task.id) == true
        col.addView(JarvisUi.label(this, "STATE"))
        col.addView(
            JarvisUi.mono(
                this,
                buildString {
                    append("enabled:       ${task.enabled}\n")
                    append("by the user:   ${task.enabledByUser}\n")
                    append("source:        ${task.source.name}\n")
                    append("mode:          ${task.mode.name}\n")
                    append("runnable:      ${task.isRunnable()}\n")
                    append("running now:   $isRunning")
                    if (heldOff.isNotBlank()) append("\nheld off:      $heldOff")
                }
            ),
            matchWidth()
        )

        col.addView(JarvisUi.label(this, "TRIGGERS"))
        col.addView(
            JarvisUi.mono(
                this,
                task.triggers.joinToString("\n") { "${it.type}  ${it.params}" }
                    .ifEmpty { "(none — this task can only be run by hand)" }
            ),
            matchWidth()
        )

        if (task.conditions.isNotEmpty()) {
            col.addView(JarvisUi.label(this, "CONDITIONS"))
            col.addView(
                JarvisUi.mono(this, task.conditions.joinToString("\n") { it.toString() }),
                matchWidth()
            )
        }

        col.addView(JarvisUi.label(this, "STEPS"))
        col.addView(
            JarvisUi.mono(
                this,
                task.steps.mapIndexed { i, step ->
                    val what = step.action ?: step.type.name.lowercase()
                    "${i + 1}. ${step.label ?: what}"
                }.joinToString("\n").ifEmpty { "(none)" }
            ),
            matchWidth()
        )

        col.addView(JarvisUi.spacer(this, JarvisUi.Space.SECTION))
        col.addView(
            JarvisUi.button(this, if (task.enabled) "SWITCH OFF" else "SWITCH ON") {
                toggleEnabled(task)
            },
            matchWidth()
        )
        // TaskEngine.runNow returns false without running when the task is
        // switched off — force only skips the CONDITIONS, never the enabled
        // check — so offering RUN NOW there would be a button that does
        // nothing.
        if (task.enabled) {
            col.addView(
                JarvisUi.button(this, "RUN NOW") { runNow(task) },
                matchWidth().apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.STEP) }
            )
            col.addView(
                JarvisUi.button(this, "CANCEL RUN") {
                    val cancelled = runtime?.engine?.cancel(task.id) ?: false
                    toast(if (cancelled) "Cancelled" else "That task is not running")
                },
                matchWidth().apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.STEP) }
            )
        }
        col.addView(
            JarvisUi.button(this, "BACK TO LIST") {
                showing = null
                refresh()
            },
            matchWidth().apply { topMargin = JarvisUi.dp(this@AutomationsActivity, JarvisUi.Space.STEP) }
        )
        col.addView(JarvisUi.spacer(this, JarvisUi.Space.WIDE))
        return col
    }

    // --- actions ------------------------------------------------------------

    /**
     * The user turned a task on or off.
     *
     * `setEnabledByUser` is the only path in the whole app that may set the
     * consent flag, and nothing on the command path can reach it.
     */
    private fun toggleEnabled(task: TaskDefinition) {
        val rt = runtime ?: return
        scope.launch {
            val updated = runCatching { rt.tasks.setEnabledByUser(task.id, !task.enabled) }
                .onFailure { Log.w(TAG, "could not switch ${task.id}", it) }
                .getOrNull()
            notice = when {
                updated == null -> "Could not change ${task.name}."
                updated.enabled -> "${updated.name} is on."
                else -> "${updated.name} is off."
            }
            runCatching { rt.engine.onTasksChanged() }
            refresh()
        }
    }

    private fun runNow(task: TaskDefinition) {
        val rt = runtime ?: return
        scope.launch {
            // dataTrusted = true: this is a local tap on this phone. force is
            // never passed — a task's own conditions are part of what the user
            // approved, so "run it anyway" is a separate, deliberate thing.
            val started = runCatching { rt.engine.runNow(task.id, dataTrusted = true) }
                .onFailure { Log.w(TAG, "could not run ${task.id}", it) }
                .getOrDefault(false)
            if (!started) {
                notice = "${task.name} did not start: it is switched off, its conditions " +
                    "are not met, or automation is paused."
                refresh()
            }
        }
    }

    // --- plumbing -----------------------------------------------------------

    private fun replaceContent(col: LinearLayout) {
        root.removeAllViews()
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(
                col,
                ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )
            )
        }
        root.addView(
            scroll,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
    }

    private fun matchWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT
    )

    private fun toast(message: String) =
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private companion object {
        private const val TAG = "JarvisAutomationsUi"
    }
}
