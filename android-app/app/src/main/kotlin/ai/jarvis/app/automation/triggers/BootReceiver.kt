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
        if (!policy.automationEnabled) {
            Log.i(TAG, "not starting after $action: automation is switched off")
            return
        }
        if (!AutomationPrefs(app).startOnBoot) {
            Log.i(TAG, "not starting after $action: start-on-boot is off")
            return
        }

        AutomationServiceStarter.start(app, "boot:$action")

        // "Always on" has to mean across a reboot, or the first restart turns
        // it silently off and the switch in Settings becomes a lie. Gated on
        // its own setting inside ensureRunning, and on the same panic and
        // automation-enabled checks above, which have already run — a killed
        // automation stack should not leave a microphone open behind it.
        WakeWordService.ensureRunning(app)

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
