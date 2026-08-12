package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.ReminderStore
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.TimeParse
import ai.jarvis.app.automation.actions.granted
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.triggers.ReminderReceiver
import android.Manifest
import android.content.Context
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * "Remind me to take the bins out at six."
 *
 * The phone could already set an alarm and write a calendar event, and neither
 * is this — see [ReminderStore] for why. A reminder is one notification, at one
 * time, carrying the words the user said, and then it is gone.
 *
 * All three are Tier 2. Setting one is a state change on this phone that the
 * user will find out about when it fires, which is exactly what NOTIFY means;
 * none of them reaches another person or spends anything.
 */

/** Tier 2 — a notification, later. */
object SetReminder : JarvisAction {
    override val id = "set_reminder"
    override val tier = ActionTier.NOTIFY
    override val description = "Remind the user about something at a given time."
    override val paramsSchema = mapOf(
        "text" to "string: what to remind them about, in their own words",
        "when" to "string: '+15m', '+2h', 'HH:MM' today or tomorrow, or an ISO timestamp",
    )
    override val capability = "reminders"
    override val requiredPermissions =
        listOf(Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.SCHEDULE_EXACT_ALARM)

    /** Nothing sooner than this: a reminder that fires as you finish asking. */
    private const val MIN_LEAD_MS = 5_000L

    /** A year. Past this, `AlarmManager` is the wrong tool and so is memory. */
    private const val MAX_LEAD_MS = 366L * 24 * 60 * 60 * 1000

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !ctx.granted(Manifest.permission.POST_NOTIFICATIONS)
        ) {
            return ActionResult.missingPermission(Manifest.permission.POST_NOTIFICATIONS)
        }
        val text = params.str("text")
            ?: return ActionResult.error("text is required — what should the reminder say?")
        if (text.length > 500) return ActionResult.error("reminder text is too long (max 500)")

        val now = System.currentTimeMillis()
        val due = TimeParse.epochMillis(params.opt("when") ?: params.opt("time"), now)
            ?: return ActionResult.error(
                "could not read the time — try '+15m', '18:00', or an ISO timestamp"
            )
        if (due - now < MIN_LEAD_MS) {
            // Silently firing something already due would look like it worked
            // and then never arrive.
            return ActionResult.error("that time has already passed")
        }
        if (due - now > MAX_LEAD_MS) {
            return ActionResult.error("that is more than a year away")
        }

        val reminder = ReminderStore.Reminder(UUID.randomUUID().toString().take(12), text, due)
        val store = ReminderStore(ctx)
        if (!store.add(reminder)) {
            return ActionResult.error(
                "there are already ${ReminderStore.MAX_REMINDERS} reminders set; " +
                    "cancel some before adding more"
            )
        }
        val exact = ReminderReceiver.arm(ctx, reminder)
        return ActionResult.ok(
            json(
                "id" to reminder.id,
                "text" to text,
                "due_at_ms" to due,
                "in_seconds" to ((due - now) / 1000),
                // Reported rather than hidden: "remind me in five minutes"
                // arriving in twenty is a different product, and the model
                // should be able to say so.
                "exact" to exact,
            )
        )
    }
}

/** Tier 2 — read-only, but it is a list of the user's own plans. */
object ListReminders : JarvisAction {
    override val id = "list_reminders"
    override val tier = ActionTier.NOTIFY
    override val description = "List reminders that have not fired yet."
    override val paramsSchema = emptyMap<String, String>()
    override val capability = "reminders"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val now = System.currentTimeMillis()
        val items = JSONArray()
        for (reminder in ReminderStore(ctx).pending(now)) {
            items.put(
                json(
                    "id" to reminder.id,
                    "text" to reminder.text,
                    "due_at_ms" to reminder.dueAtMs,
                    "in_seconds" to ((reminder.dueAtMs - now) / 1000),
                )
            )
        }
        return ActionResult.ok(json("reminders" to items, "count" to items.length()))
    }
}

/** Tier 2 — cancels something the user asked for, so they should hear about it. */
object CancelReminder : JarvisAction {
    override val id = "cancel_reminder"
    override val tier = ActionTier.NOTIFY
    override val description = "Cancel a reminder by its id, or all of them."
    override val paramsSchema = mapOf(
        "id" to "string: the reminder id from set_reminder or list_reminders",
        "all" to "bool (optional): cancel every pending reminder",
    )
    override val capability = "reminders"

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
        val store = ReminderStore(ctx)
        if (params.optBoolean("all", false)) {
            val pending = store.pending()
            pending.forEach { ReminderReceiver.cancel(ctx, it) }
            store.clear()
            return ActionResult.ok(json("cancelled" to pending.size))
        }
        val id = params.str("id")
            ?: return ActionResult.error("give an id, or all: true")
        val removed = store.remove(id)
            ?: return ActionResult.error("no reminder with id '$id'")
        ReminderReceiver.cancel(ctx, removed)
        return ActionResult.ok(json("cancelled" to 1, "text" to removed.text))
    }
}
