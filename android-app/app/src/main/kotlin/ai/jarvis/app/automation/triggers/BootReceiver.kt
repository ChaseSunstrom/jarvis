package ai.jarvis.app.automation.triggers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import ai.jarvis.app.assist.WakeWordService
import ai.jarvis.app.automation.AutomationPrefs
import ai.jarvis.app.automation.policy.PolicyStore

/**
 * Brings the automation layer back after a reboot or an app update.
 *
 * Three checks before anything starts, in order of how badly the user wants
 * them respected:
 *
 *  1. **Panic.** If the user hit panic before rebooting, rebooting does not
 *     clear it. Only a human clears panic.
 *  2. **Master switch.** Off means off, across reboots.
 *  3. **Start on boot.** The user's own preference for this behaviour.
 *
 * Alarms and `WorkManager` jobs do not survive a reboot, so the service
 * re-arms every time trigger when it starts — see
 * [ai.jarvis.app.automation.JarvisAutomationService].
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_MY_PACKAGE_REPLACED &&
            action != Intent.ACTION_LOCKED_BOOT_COMPLETED
        ) {
            return
        }

        val app = context.applicationContext
        val policy = PolicyStore(app)
        if (policy.panic) {
            Log.i(TAG, "not starting after $action: panic is set")
            return
        }
        // The wake listener first, and NOT behind the automation checks below.
        //
        // It used to sit after them, which meant that someone who had turned
        // the automation layer off — or never turned start-on-boot on — lost
        // "Hey Jarvis" across every reboot for reasons that have nothing to do
        // with it. They are separate features with separate switches; only
        // panic, which means stop everything, is shared.
        //
        // `ensureRunning` will very likely NOT be able to start it from here:
        // a foreground service typed `microphone` cannot be started from the
        // background, and BOOT_COMPLETED is explicitly not an exemption for the
        // while-in-use types. That refusal is the whole reason always-on
        // listening quietly stopped working after a restart. The call is still
        // right — with battery-optimisation exemption or "display over other
        // apps" granted it succeeds — and when it cannot, it leaves a
        // notification that starts it in one tap instead of a log line.
        WakeWordService.ensureRunning(app)

        // Reminders next, and also NOT behind the automation checks. An
        // AlarmManager alarm does not survive a reboot, so without this
        // "remind me tomorrow morning" is silently cancelled by a phone that
        // restarted overnight — the failure a user notices least and forgives
        // least. It is the user's own reminder, not an automation they may
        // have switched off, so only panic suppresses it.
        runCatching { ReminderReceiver.rearmAll(app) }
            .onFailure { Log.w(TAG, "could not re-arm reminders after $action", it) }

        if (!policy.automationEnabled) {
            Log.i(TAG, "not starting after $action: automation is switched off")
            return
        }
        if (!AutomationPrefs(app).startOnBoot) {
            Log.i(TAG, "not starting after $action: start-on-boot is off")
            return
        }

        AutomationServiceStarter.start(app, "boot:$action")

        // Let a task fire on boot itself. The real BOOT_COMPLETED broadcast is
        // long finished by the time the service has built its triggers, so a
        // synthetic copy is parked on the bus for the BootCompletedTrigger to
        // pick up when it starts.
        if (action != Intent.ACTION_MY_PACKAGE_REPLACED) {
            SystemEventBus.publish(app, Intent(ACTION_SYNTHETIC_BOOT))
        }
    }

    companion object {
        private const val TAG = "JarvisBoot"

        /**
         * Stands in for `BOOT_COMPLETED` on the internal bus. App-private and
         * never broadcast to the system, so nothing outside this process can
         * forge a boot.
         */
        const val ACTION_SYNTHETIC_BOOT = "ai.jarvis.app.automation.SYNTHETIC_BOOT"
    }
}
