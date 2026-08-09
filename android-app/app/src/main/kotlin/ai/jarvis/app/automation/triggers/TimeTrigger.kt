package ai.jarvis.app.automation.triggers

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import org.json.JSONObject
import java.time.ZoneId
import java.util.concurrent.TimeUnit

private const val TAG = "JarvisTriggers"

/**
 * Fires at a wall-clock time, on chosen days.
 *
 * `setExactAndAllowWhileIdle` is the only scheduling API that survives Doze
 * with the accuracy a "wake me at 07:00" automation needs. It is a one-shot, so
 * the trigger re-arms itself after every fire — which is also what makes it
 * correct across DST and timezone changes, since the next fire is recomputed
 * from the current zone rather than extrapolated from the last one.
 *
 * When the user has not granted `SCHEDULE_EXACT_ALARM` (Android 12+ hands it
 * out only on request, and revokes it when an app is unused) this degrades to
 * `setAndAllowWhileIdle`, which the system may delay by minutes. The trigger
 * reports which mode it is in via the `exact` field of its payload, so a task
 * that cares can see the difference instead of quietly drifting.
 */
class ScheduleTrigger(
    context: Context,
    /** Stable per-schedule key; also the `PendingIntent` identity. */
    private val key: String,
    private val spec: ScheduleSpec,
    private val zone: () -> ZoneId = { ZoneId.systemDefault() },
    private val now: () -> Long = System::currentTimeMillis
) : JarvisTrigger {

    override val id = TriggerIds.TIME_SCHEDULE

    private val app = context.applicationContext
    private val alarms = app.getSystemService(AlarmManager::class.java)
    private var callback: ((JSONObject) -> Unit)? = null
    private var armedFor: Long? = null

    override fun isAvailable(ctx: Context): Boolean = alarms != null && spec.isValid()

    override val unavailableReason: String?
        get() = when {
            alarms == null -> "no AlarmManager on this device"
            !spec.isValid() -> "the schedule is not valid"
            else -> null
        }

    override fun start(cb: (JSONObject) -> Unit) {
        stop()
        callback = cb
        AlarmRouter.register(key) { onFired() }
        arm()
    }

    override fun stop() {
        AlarmRouter.unregister(key)
        cancelAlarm()
        callback = null
        armedFor = null
    }

    /** Next fire, or null when this schedule can never fire again. */
    fun nextFireMs(): Long? = ScheduleCalculator.nextFireEpochMs(now(), spec, zone())

    private fun arm() {
        val manager = alarms ?: return
        val next = nextFireMs()
        if (next == null) {
            Log.w(TAG, "schedule $key can never fire; not arming")
            return
        }
        armedFor = next
        val exact = canScheduleExact(manager)
        try {
            if (exact) {
                manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, next, pendingIntent())
            } else {
                manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, next, pendingIntent())
            }
        } catch (t: SecurityException) {
            // The grant can be revoked between the check and the call.
            Log.w(TAG, "exact alarm refused for $key; falling back to inexact", t)
            runCatching {
                manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, next, pendingIntent())
            }
        }
    }

    private fun onFired() {
        val scheduled = armedFor
        val cb = callback
        // Re-arm FIRST. If the callback throws or the task engine is busy, the
        // schedule must still be live for tomorrow.
        arm()
        cb?.invoke(
            JSONObject()
                .put("key", key)
                .put("scheduled_for", scheduled ?: JSONObject.NULL)
                .put("fired_at", now())
                .put("exact", alarms?.let { canScheduleExact(it) } ?: false)
                .apply {
                    spec.minuteOfDay?.let { put("minute_of_day", it) }
                    spec.intervalMinutes?.let { put("interval_minutes", it) }
                }
        )
    }

    private fun canScheduleExact(manager: AlarmManager): Boolean =
        if (Build.VERSION.SDK_INT >= 31) manager.canScheduleExactAlarms() else true

    private fun pendingIntent(): PendingIntent {
        val intent = Intent(app, AlarmReceiver::class.java)
            .setAction(ACTION_PREFIX + key)
            .putExtra(AlarmReceiver.EXTRA_ALARM_KEY, key)
        return PendingIntent.getBroadcast(
            app,
            key.hashCode(),
            intent,
            // IMMUTABLE is required from API 31 and correct everywhere: nothing
            // outside this app may fill in the extras of our alarms.
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun cancelAlarm() {
        val manager = alarms ?: return
        val intent = Intent(app, AlarmReceiver::class.java)
            .setAction(ACTION_PREFIX + key)
            .putExtra(AlarmReceiver.EXTRA_ALARM_KEY, key)
        val pi = PendingIntent.getBroadcast(
            app,
            key.hashCode(),
            intent,
            PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE
        )
        if (pi != null) {
            runCatching { manager.cancel(pi) }
            pi.cancel()
        }
    }

    private companion object {
        const val ACTION_PREFIX = "ai.jarvis.app.automation.ALARM."
    }
}

/**
 * Fires every N minutes.
 *
 * Two implementations behind one trigger, because Android has two answers:
 *
 *  * **15 minutes or more** — `WorkManager` periodic work. Survives reboots and
 *    process death without holding anything, and the system batches it with
 *    other work, which is the whole point at this cadence.
 *  * **Under 15 minutes** — `WorkManager`'s floor is 15 minutes, so a chain of
 *    one-shot exact alarms is used instead. Honest about the cost: this is the
 *    expensive option, and the docs say so.
 *
 * Either way the next fire is computed by [ScheduleCalculator], so an interval
 * stays aligned to the local wall clock rather than drifting by however long
 * the system took to deliver the last one.
 */
class IntervalTrigger(
    context: Context,
    private val key: String,
    private val spec: ScheduleSpec,
    private val requiresNetwork: Boolean = false,
    private val zone: () -> ZoneId = { ZoneId.systemDefault() },
    private val now: () -> Long = System::currentTimeMillis
) : JarvisTrigger {

    override val id = TriggerIds.INTERVAL

    private val app = context.applicationContext
    private var callback: ((JSONObject) -> Unit)? = null
    private var alarmLeg: ScheduleTrigger? = null

    private val minutes: Int get() = spec.intervalMinutes ?: 0
    private val usesWorkManager: Boolean get() = minutes >= MIN_WORK_MANAGER_MINUTES

    override fun isAvailable(ctx: Context): Boolean = spec.isValid() && spec.intervalMinutes != null

    override val unavailableReason: String?
        get() = if (isAvailable(app)) null else "the interval is not valid"

    override fun start(cb: (JSONObject) -> Unit) {
        stop()
        if (!isAvailable(app)) return
        callback = cb
        IntervalRouter.register(key) { emit(it) }

        if (usesWorkManager) {
            enqueueWork()
        } else {
            // Reuse the alarm machinery wholesale rather than writing a second
            // copy of it. The schedule spec IS an interval spec.
            alarmLeg = ScheduleTrigger(app, workName(), spec, zone, now).also { leg ->
                leg.start { payload -> emit(payload) }
            }
        }
    }

    override fun stop() {
        IntervalRouter.unregister(key)
        alarmLeg?.stop()
        alarmLeg = null
        runCatching { WorkManager.getInstance(app).cancelUniqueWork(workName()) }
            .onFailure { Log.d(TAG, "could not cancel $key work", it) }
        callback = null
    }

    private fun emit(payload: JSONObject) {
        callback?.invoke(
            payload
                .put("key", key)
                .put("interval_minutes", minutes)
                .put("mechanism", if (usesWorkManager) "workmanager" else "alarm")
        )
    }

    private fun enqueueWork() {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(if (requiresNetwork) NetworkType.CONNECTED else NetworkType.NOT_REQUIRED)
            .build()
        val request = PeriodicWorkRequestBuilder<IntervalWorker>(minutes.toLong(), TimeUnit.MINUTES)
            .setConstraints(constraints)
            .setInputData(workDataOf(IntervalWorker.KEY_TRIGGER to key))
            .addTag(WORK_TAG)
            .build()
        runCatching {
            WorkManager.getInstance(app).enqueueUniquePeriodicWork(
                workName(),
                // UPDATE so an edited interval takes effect against the
                // existing work rather than waiting out the old period, and
                // without losing the run history a KEEP/REPLACE pair would.
                ExistingPeriodicWorkPolicy.UPDATE,
                request
            )
        }.onFailure { Log.w(TAG, "could not enqueue interval work for $key", it) }
    }

    private fun workName() = "$WORK_TAG:$key"

    companion object {
        /** `WorkManager`'s hard floor. Below it, alarms. */
        const val MIN_WORK_MANAGER_MINUTES = 15
        const val WORK_TAG = "jarvis-interval"
    }
}

/**
 * Where a periodic `WorkManager` run goes. Same shape as [AlarmRouter]: a
 * worker whose trigger has been stopped finds no owner and does nothing.
 */
object IntervalRouter {

    private val owners = java.util.concurrent.ConcurrentHashMap<String, (JSONObject) -> Unit>()

    fun register(key: String, owner: (JSONObject) -> Unit) {
        owners[key] = owner
    }

    fun unregister(key: String) {
        owners.remove(key)
    }

    fun clear() = owners.clear()

    fun deliver(key: String, payload: JSONObject): Boolean {
        val owner = owners[key] ?: return false
        return runCatching { owner(payload) }.isSuccess
    }
}

/**
 * The `WorkManager` half of [IntervalTrigger].
 *
 * Deliberately does almost nothing: it hands the tick to [IntervalRouter] and
 * returns. Work runs on a background thread with a ten-minute budget, and
 * running a whole task inside that budget would put automation execution
 * outside the foreground service that owns it — including outside its
 * notification, which is the user's only sign that automation is live.
 */
class IntervalWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        val key = inputData.getString(KEY_TRIGGER) ?: return Result.success()
        val payload = JSONObject()
            .put("key", key)
            .put("fired_at", System.currentTimeMillis())
        if (!IntervalRouter.deliver(key, payload)) {
            Log.d(TAG, "interval $key ticked with no owner; starting the service")
            AutomationServiceStarter.start(applicationContext, "interval:$key")
        }
        return Result.success()
    }

    companion object {
        const val KEY_TRIGGER = "trigger_key"
    }
}
