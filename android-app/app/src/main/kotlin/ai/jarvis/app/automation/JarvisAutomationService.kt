package ai.jarvis.app.automation

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import ai.jarvis.app.JarvisApp
import ai.jarvis.app.automation.actions.ActionRegistry
import ai.jarvis.app.automation.actions.builtin.Builtins
import ai.jarvis.app.automation.audit.AuditLog
import ai.jarvis.app.automation.notify.NotificationBus
import ai.jarvis.app.automation.policy.PolicyStore
import ai.jarvis.app.automation.tasks.AskJarvisClient
import ai.jarvis.app.automation.tasks.DeviceConditionProbe
import ai.jarvis.app.automation.tasks.DeviceEventSink
import ai.jarvis.app.automation.tasks.EventWaiter
import ai.jarvis.app.automation.tasks.TaskDefinition
import ai.jarvis.app.automation.tasks.TaskEngine
import ai.jarvis.app.automation.tasks.TaskRunner
import ai.jarvis.app.automation.tasks.TaskStore
import ai.jarvis.app.automation.triggers.AutomationServiceStarter
import ai.jarvis.app.automation.triggers.SystemEventBus
import ai.jarvis.app.automation.triggers.SystemEventReceiver
import ai.jarvis.app.automation.triggers.TriggerEvent
import ai.jarvis.app.automation.triggers.TriggerManager
import ai.jarvis.app.config.JarvisConfig
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * The foreground service that owns the automation lifecycle.
 *
 * What it does, in order: build (or reuse) the runtime, register the dynamic
 * broadcast receiver, start the triggers the enabled tasks need, and put up a
 * low-priority notification with a **Pause automations** button wired to the
 * policy master switch.
 *
 * ## Why a foreground service at all
 *
 * Every interesting trigger is registered-only: `SCREEN_ON`, `USER_PRESENT`,
 * `HEADSET_PLUG`, `BATTERY_CHANGED` are not deliverable to manifest receivers,
 * and a `ConnectivityManager.NetworkCallback` or a `LocationListener` dies with
 * its process. A background process on Graphene lives for seconds. So either
 * the automation layer is a foreground service with a notification the user can
 * see and switch off, or it is a background process that does not work — and
 * pretending otherwise would produce automations that fire on a demo and never
 * again.
 *
 * The notification is not a compliance tax. It is the honest statement that
 * this app is currently watching the phone, and the button on it turns that
 * off in one tap.
 *
 * ## Resilience
 *
 * `START_STICKY` with a null-intent restart path, everything unregistered in
 * `onDestroy`, and both kill switches watched live: setting panic or clearing
 * the master switch stops every trigger immediately, from wherever it was
 * changed, because the watch is on the `SharedPreferences` file rather than on
 * a particular `PolicyStore` instance.
 */
class JarvisAutomationService : Service() {

    private lateinit var policy: PolicyStore
    private lateinit var prefs: AutomationPrefs

    private var receiver: SystemEventReceiver? = null
    private var policyWatcher: SharedPreferences.OnSharedPreferenceChangeListener? = null
    private var policyPrefs: SharedPreferences? = null
    private var taskWatcher: ((List<TaskDefinition>) -> Unit)? = null

    private val scope = CoroutineScope(
        SupervisorJob() +
            CoroutineExceptionHandler { _, t -> Log.w(TAG, "automation coroutine failed", t) }
    )

    /**
     * Serialises trigger rebuilds.
     *
     * Two of them can be requested at once — loading the task store fires the
     * change listener while `startTriggers` is already running — and
     * `TriggerManager.start` stops everything before it starts anything. Two
     * interleaved calls would have one clearing the registry the other had just
     * filled, leaving a phone that looks live and observes nothing.
     */
    private val rebuildLock = Mutex()

    /** True while triggers are registered, so a re-entrant start is cheap. */
    @Volatile
    private var live = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        policy = PolicyStore(applicationContext)
        prefs = AutomationPrefs(applicationContext)
        AutomationRuntime.ensure(applicationContext)

        startForegroundNotification()
        watchPolicy()
        watchTasks()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // A null intent means the system restarted us after a kill. That is the
        // normal path on a phone with aggressive memory pressure, and it must
        // rebuild everything rather than assume the previous state survived.
        val reason = intent?.getStringExtra(AutomationServiceStarter.EXTRA_REASON) ?: "restart"
        Log.i(TAG, "onStartCommand ($reason)")

        when (intent?.action) {
            ACTION_PAUSE -> {
                policy.automationEnabled = false
                Log.i(TAG, "automation paused from the notification")
            }

            ACTION_RESUME -> {
                policy.automationEnabled = true
                Log.i(TAG, "automation resumed from the notification")
            }
        }

        startForegroundNotification()
        applyKillSwitches()
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy: releasing everything")
        teardownTriggers()
        unregisterDynamicReceiver()

        policyWatcher?.let { watcher ->
            runCatching { policyPrefs?.unregisterOnSharedPreferenceChangeListener(watcher) }
        }
        policyWatcher = null
        policyPrefs = null

        taskWatcher?.let { AutomationRuntime.tasks?.removeListener(it) }
        taskWatcher = null

        AutomationRuntime.engine?.cancelAll()
        scope.cancel()
        super.onDestroy()
    }

    // --- the two kill switches ---------------------------------------------

    /**
     * Bring the trigger layer in line with the master switch and the panic flag.
     *
     * Panic or master-off does not merely stop actions from running — the
     * dispatcher would refuse those anyway. It stops the OBSERVING: receivers
     * unregistered, location listener removed, notification allow-list emptied.
     * "Pause automations" that left the phone still watching would be a lie.
     */
    private fun applyKillSwitches() {
        val shouldRun = policy.automationEnabled && !policy.panic
        AutomationRuntime.engine?.let { engine ->
            engine.accepting = shouldRun
            engine.reportTriggerEvents = prefs.reportTriggersToServer
        }

        if (!shouldRun) {
            if (live) {
                Log.i(TAG, "stopping triggers: enabled=${policy.automationEnabled} panic=${policy.panic}")
                teardownTriggers()
            }
            NotificationBus.updateAllowedPackages(emptySet())
            updateNotification()
            return
        }

        if (!live) startTriggers()
        updateNotification()
    }

    private fun startTriggers() {
        registerDynamicReceiver()
        scope.launch {
            val runtime = AutomationRuntime.ensure(applicationContext)
            val tasks = runtime.tasks.all()
            rebuild(runtime, tasks)
        }
    }

    private suspend fun rebuild(runtime: AutomationRuntime.Runtime, tasks: List<TaskDefinition>) {
        rebuildLock.withLock {
            if (!policy.automationEnabled || policy.panic) return
            // Order matters: `TriggerManager.start` stops everything first, and
            // stopping empties the notification listener's allow-list. Refilling
            // it before the rebuild would refill it and then immediately clear
            // it, leaving a phone with notification triggers that never fire.
            runtime.triggers.start(tasks)
            runtime.engine.onTasksChanged()
            live = true
        }
        updateNotification()
        Log.i(
            TAG,
            "automation live: ${tasks.count { it.isRunnable() }} of ${tasks.size} task(s), " +
                "${runtime.triggers.activeIds.size} trigger(s)"
        )
    }

    private fun teardownTriggers() {
        AutomationRuntime.triggers?.stop()
        AutomationRuntime.engine?.cancelAll()
        unregisterDynamicReceiver()
        live = false
    }

    // --- broadcasts ---------------------------------------------------------

    /**
     * The registered-only broadcasts. The manifest copy of this receiver
     * catches the handful that can wake a cold process; this catches the rest,
     * which are only ever delivered to a live registration.
     */
    private fun registerDynamicReceiver() {
        if (receiver != null) return
        val filter = IntentFilter().apply {
            for (action in SystemEventBus.DYNAMIC_ACTIONS) addAction(action)
        }
        // dynamic = true: this is the live registration, and it is the one that
        // delivers. The manifest-declared copy of the same receiver stands down
        // while this exists, or every task would run twice for the broadcasts
        // both registrations match.
        val r = SystemEventReceiver(dynamic = true)
        try {
            // NOT_EXPORTED: these are protected system broadcasts, and nothing
            // else has any business sending them to us.
            ContextCompatRegister.register(this, r, filter)
            receiver = r
        } catch (t: Throwable) {
            Log.w(TAG, "could not register the system event receiver", t)
        }
    }

    private fun unregisterDynamicReceiver() {
        receiver?.let { r ->
            runCatching { unregisterReceiver(r) }
                .onFailure { Log.d(TAG, "receiver was already unregistered", it) }
        }
        receiver = null
    }

    // --- watching for changes ----------------------------------------------

    private fun watchPolicy() {
        val sp = JarvisConfig.Policy.open(applicationContext)
        val watcher = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key == JarvisConfig.Policy.KEY_AUTOMATION_ENABLED ||
                key == JarvisConfig.Policy.KEY_PANIC
            ) {
                Log.i(TAG, "policy switch changed ($key)")
                applyKillSwitches()
            }
        }
        sp.registerOnSharedPreferenceChangeListener(watcher)
        policyPrefs = sp
        policyWatcher = watcher
    }

    private fun watchTasks() {
        val runtime = AutomationRuntime.ensure(applicationContext)
        val watcher: (List<TaskDefinition>) -> Unit = { tasks ->
            scope.launch { rebuild(runtime, tasks) }
        }
        runtime.tasks.addListener(watcher)
        taskWatcher = watcher
    }

    // --- the notification ---------------------------------------------------

    private fun startForegroundNotification() {
        try {
            // `specialUse` is an API 34 type. On 29-33 the platform validates
            // the requested type against the manifest as PARSED BY THAT
            // VERSION, where `specialUse` is an unknown token and drops out —
            // so asking for it there throws. `dataSync` is declared on the same
            // component and is the honest description of the older behaviour.
            val type = if (Build.VERSION.SDK_INT >= 34) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            } else {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            }
            ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(), type)
        } catch (t: Throwable) {
            // Android 14+ can refuse a foreground start from the background.
            // Log it and carry on: the service still runs for as long as the
            // system allows, and the next user-initiated start will succeed.
            Log.w(TAG, "could not enter the foreground", t)
        }
    }

    private fun updateNotification() {
        runCatching {
            val nm = getSystemService(android.app.NotificationManager::class.java)
            nm?.notify(NOTIFICATION_ID, buildNotification())
        }
    }

    private fun buildNotification(): Notification {
        val paused = !policy.automationEnabled || policy.panic
        val runtime = AutomationRuntime.peek()
        val triggerCount = runtime?.triggers?.activeIds?.size ?: 0
        val taskCount = runtime?.tasks?.snapshot()?.count { it.isRunnable() } ?: 0

        val text = when {
            policy.panic -> "Panic is on. Nothing will run until you clear it."
            !policy.automationEnabled -> "Paused. No triggers are registered."
            taskCount == 0 -> "No automations are switched on."
            else -> "$taskCount automation${plural(taskCount)}, " +
                "$triggerCount trigger${plural(triggerCount)} active"
        }

        val builder = NotificationCompat.Builder(this, JarvisApp.CHANNEL_SERVICE)
            .setContentTitle("Jarvis automations")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_recent_history)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(true)
            .setShowWhen(false)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            // Params and task names can be personal; keep them off the keyguard.
            .setVisibility(NotificationCompat.VISIBILITY_SECRET)
            .setContentIntent(openAppIntent())

        // The one control this notification carries. It writes the same master
        // switch the settings screen writes, which the dispatcher consults on
        // every single action — so pausing here pauses everything, including a
        // command that arrives over the socket a millisecond later.
        if (paused && !policy.panic) {
            builder.addAction(
                android.R.drawable.ic_media_play,
                "Resume automations",
                serviceIntent(ACTION_RESUME)
            )
        } else if (!policy.panic) {
            builder.addAction(
                android.R.drawable.ic_media_pause,
                "Pause automations",
                serviceIntent(ACTION_PAUSE)
            )
        }

        return builder.build()
    }

    private fun plural(n: Int) = if (n == 1) "" else "s"

    private fun serviceIntent(action: String): PendingIntent {
        val intent = Intent(this, JarvisAutomationService::class.java).setAction(action)
        return PendingIntent.getService(
            this,
            action.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /**
     * Tapping the notification opens the app. Launched by NAME so this module
     * does not import the UI module — the dependency runs one way, and a build
     * without `MainActivity` degrades to a notification that does nothing when
     * tapped rather than failing to compile.
     */
    private fun openAppIntent(): PendingIntent? {
        val intent = packageManager.getLaunchIntentForPackage(packageName) ?: return null
        return PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    companion object {
        private const val TAG = "JarvisAutomation"
        private const val NOTIFICATION_ID = 4201

        const val ACTION_PAUSE = "ai.jarvis.app.automation.PAUSE"
        const val ACTION_RESUME = "ai.jarvis.app.automation.RESUME"

        /** Start (or nudge) the service. Safe to call repeatedly. */
        fun ensureRunning(context: Context, reason: String = "app") {
            AutomationServiceStarter.start(context, reason)
        }
    }
}

/**
 * `registerReceiver` with the export flag the platform requires from API 33.
 *
 * `ContextCompat.registerReceiver` would do this, but pulling in the whole
 * `androidx.core.content` surface for one call when the branch is three lines
 * is not a trade worth making, and this way the NOT_EXPORTED decision is
 * visible at the call site.
 */
private object ContextCompatRegister {
    fun register(context: Context, receiver: android.content.BroadcastReceiver, filter: IntentFilter) {
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(receiver, filter)
        }
    }
}

/**
 * The process-wide automation objects.
 *
 * Built once and shared, because there is exactly one action registry, one
 * policy store and one audit log per process, and handing three modules their
 * own copies is how a policy change ends up applying to some commands and not
 * others.
 *
 * ## The seams other agents plug into
 *
 * [deviceEvents] and [askJarvis] are the command channel's side of the
 * relationship, and both default to a no-op. That is the important part: with
 * no server attached the phone still runs its automations, still enforces its
 * policy, still writes its audit log. The server makes Jarvis useful; it is not
 * what makes the phone safe.
 */
object AutomationRuntime {

    private var instance: Runtime? = null

    /**
     * Where `device_event` frames go. Set by whoever owns the WebSocket:
     *
     * ```kotlin
     * AutomationRuntime.deviceEvents = myWebSocketClient
     * ```
     */
    @Volatile
    var deviceEvents: DeviceEventSink? = null

    /** Backs the `ask_jarvis` step. Same owner as [deviceEvents]. */
    @Volatile
    var askJarvis: AskJarvisClient? = null

    /** Everything, building it on first use. Thread-safe. */
    @Synchronized
    fun ensure(context: Context): Runtime {
        instance?.let { return it }
        val app = context.applicationContext
        val registry = Builtins.standard(app)
        val audit = AuditLog(app)
        val store = TaskStore(app, tierOf = { id -> registry[id]?.tier })
        val probe = DeviceConditionProbe(app)

        val scope = CoroutineScope(
            SupervisorJob() +
                CoroutineExceptionHandler { _, t -> Log.w("JarvisAutomation", "engine failed", t) }
        )

        // The runner needs the engine (for wait_for_event) and the engine needs
        // the runner. Broken with a lateinit holder rather than by giving one of
        // them a mutable reference to the other.
        val holder = EngineHolder()
        val runner = TaskRunner(
            registry = registry,
            audit = audit,
            probe = probe,
            ask = { askJarvis },
            events = holder
        )
        val engine = TaskEngine(
            scope = scope,
            store = store,
            runner = runner,
            probe = probe,
            deviceEvents = { deviceEvents }
        )
        holder.engine = engine

        val triggers = TriggerManager(app, onEvent = { event -> engine.onTriggerEvent(event) })

        return Runtime(registry, audit, store, engine, triggers, scope).also { instance = it }
    }

    /** The runtime if it exists, without building one. */
    fun peek(): Runtime? = instance

    val registry: ActionRegistry? get() = instance?.registry
    val tasks: TaskStore? get() = instance?.tasks
    val engine: TaskEngine? get() = instance?.engine
    val triggers: TriggerManager? get() = instance?.triggers

    class Runtime(
        val registry: ActionRegistry,
        val audit: AuditLog,
        val tasks: TaskStore,
        val engine: TaskEngine,
        val triggers: TriggerManager,
        val scope: CoroutineScope
    )

    /** Breaks the runner/engine cycle without a mutable field on either. */
    private class EngineHolder : EventWaiter {
        @Volatile
        var engine: TaskEngine? = null

        override suspend fun await(triggerId: String, timeoutMs: Long): TriggerEvent? =
            engine?.await(triggerId, timeoutMs)
    }
}

/**
 * The automation layer's own preferences. Separate from `JarvisConfig` (which
 * holds the server token) and from `PolicyStore` (which holds the user's `never`
 * rules), so that resetting one never clears another.
 */
class AutomationPrefs(context: Context) {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /**
     * Restart the automation service after a reboot.
     *
     * Defaults to true. Automations that stop working after a reboot are worse
     * than no automations, and this is not a permission — the master switch,
     * the panic flag and the per-action policy all still apply, so a phone that
     * boots with automation enabled is a phone that boots into exactly the
     * state the user left it in.
     */
    var startOnBoot: Boolean
        get() = prefs.getBoolean(KEY_START_ON_BOOT, true)
        set(value) = prefs.edit().putBoolean(KEY_START_ON_BOOT, value).apply()

    /** Push a `device_event` for every trigger, not only for finished runs. */
    var reportTriggersToServer: Boolean
        get() = prefs.getBoolean(KEY_REPORT_TRIGGERS, true)
        set(value) = prefs.edit().putBoolean(KEY_REPORT_TRIGGERS, value).apply()

    companion object {
        private const val FILE = "jarvis_automation"
        private const val KEY_START_ON_BOOT = "start_on_boot"
        private const val KEY_REPORT_TRIGGERS = "report_triggers"
    }
}
