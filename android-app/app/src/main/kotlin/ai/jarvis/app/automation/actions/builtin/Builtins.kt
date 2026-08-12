package ai.jarvis.app.automation.actions.builtin

import android.content.Context
import ai.jarvis.app.automation.AutomationBridge
import ai.jarvis.app.automation.actions.ActionEnv
import ai.jarvis.app.automation.actions.ActionRegistry
import ai.jarvis.app.automation.actions.ApprovalGateway
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.UiApprovalGateway
import ai.jarvis.app.automation.actions.asBridgeDispatcher
import ai.jarvis.app.automation.audit.AuditLog
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.automation.policy.PolicyProvider
import ai.jarvis.app.automation.policy.PolicyStore

/**
 * The local action table — the authority on what this phone can do and how
 * dangerous each of those things is. The server's `tier` field can raise these
 * numbers for a single command; nothing can lower them.
 *
 * Adding an action is a two-line change here plus a row in `docs/actions.md`.
 * Adding one at the wrong tier is a security bug, so the table is deliberately
 * in one place where it can be reviewed as a whole.
 */
object Builtins {

    /** Every built-in action, in the order the manifest will list them. */
    fun all(): List<JarvisAction> = buildList {
        // Device and system
        add(GetDeviceState)
        add(SetVolume)
        add(SetRingerMode)
        add(ToggleDnd)
        add(SetBrightness)
        add(ToggleTorch)
        add(VibrateAction)
        add(GetLocation)
        add(GetSensors)

        // Apps and intents
        add(LaunchApp)
        add(OpenUrl)
        add(ShareText)
        add(StartNavigation)
        add(DialNumber)
        add(OpenSettingsPanel)
        add(ListInstalledApps)
        add(KillApp)

        // Media
        addAll(MediaActions.all)

        // Comms
        add(SendSms)
        add(PlaceCall)
        add(ReadContacts)
        add(SendNotification)

        // Calendar and clock
        add(ReadCalendar)
        add(CreateCalendarEvent)
        add(SetAlarm)
        add(SetTimer)
        add(SetReminder)
        add(ListReminders)
        add(CancelReminder)

        // Files and clipboard
        add(ReadFile)
        add(WriteFile)
        add(ListFiles)
        add(DeleteFile)
        add(ReadClipboard)
        add(WriteClipboard)

        // Network
        add(HttpRequest)

        // Shell
        add(RunShell)

        // Delegated to the accessibility service
        addAll(UiActions.all)
    }

    /**
     * The local tier of one action id, straight from the table above, or null
     * when this build has never heard of the id.
     *
     * Static — it is the declared tier, not the per-invocation one that
     * `tierFor(params)` may raise. Used by [PolicyStore] so it can refuse to
     * store an `allow_always` for a Tier-3 action without being told the tier.
     */
    fun tierOf(actionId: String): ActionTier? = TIERS[actionId]

    private val TIERS: Map<String, ActionTier> by lazy {
        all().associate { it.id to it.tier }
    }

    /** Registry wired to explicit collaborators — the form tests use. */
    fun registry(
        context: Context,
        policy: PolicyProvider,
        audit: AuditLog,
        approvals: ApprovalGateway
    ): ActionRegistry = ActionRegistry(context, policy, audit, approvals).registerAll(all())

    /**
     * Registry wired the way the app runs it: SharedPreferences policy store,
     * JSONL audit log, and the full-screen consent UI.
     *
     * Build this once at startup (`AutomationRuntime.ensure` does) and hand the
     * same instance to the WebSocket client and the settings screen. Three
     * pieces of wiring happen here rather than being left to a caller who might
     * forget, because forgetting any of them is silent:
     *
     *  1. [ActionEnv.refreshFromConfig] — the jarvis-core host that
     *     `http_request` is allowed to reach, the notification-listener
     *     component the media actions prefer, the app version.
     *  2. The policy store is given the action table, so its Tier-3 guard works
     *     without the caller supplying a tier.
     *  3. [AutomationBridge.dispatcher] is filled in. `JarvisChannel` reads that
     *     slot for every `device_command`; with it empty every command from the
     *     server is answered `unsupported` and the phone does nothing at all.
     */
    fun standard(context: Context, approvals: ApprovalGateway? = null): ActionRegistry {
        val appContext = context.applicationContext
        ActionEnv.refreshFromConfig(appContext)
        return registry(
            appContext,
            PolicyStore(appContext, ::tierOf),
            AuditLog(appContext),
            approvals ?: UiApprovalGateway(appContext)
        ).also { AutomationBridge.dispatcher = it.asBridgeDispatcher() }
    }
}
