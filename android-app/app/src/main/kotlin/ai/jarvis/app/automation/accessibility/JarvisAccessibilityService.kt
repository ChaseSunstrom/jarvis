package ai.jarvis.app.automation.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityManager
import android.view.accessibility.AccessibilityNodeInfo
import ai.jarvis.app.automation.AutomationBridge
import ai.jarvis.app.automation.actions.ActionEnv
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * The most dangerous component in the app.
 *
 * An enabled `AccessibilityService` sees the full view hierarchy of every app
 * the user opens — message bodies, account numbers, one-time codes — and, with
 * `canPerformGestures`, can tap and type anywhere. Android puts a deliberately
 * scary warning in front of enabling it. That warning is correct.
 *
 * What this class itself does is small and boring, on purpose:
 *
 *  * configures the service,
 *  * publishes a single live reference so [UiAutomator] can reach the system
 *    APIs (`rootInActiveWindow`, `dispatchGesture`, `performGlobalAction`,
 *    `takeScreenshot`), which exist only on an `AccessibilityService` instance,
 *  * tracks which app and activity is in front, and tells [ScreenEvents]
 *    listeners when that changes.
 *
 * It contains no policy and no action dispatch. Nothing here reads screen text
 * and nothing here can start an action: events carry a package name and an
 * activity name and nothing else, so a notification or a web page cannot get a
 * string into the automation path just by being on screen. Policy lives in
 * [UiAutomator] and in `automation/policy`.
 */
class JarvisAccessibilityService : AccessibilityService() {

    /** Last window we saw, so `ui_*` can name its target before it acts. */
    @Volatile
    private var foreground: ScreenChangeEvent = ScreenChangeEvent.UNKNOWN

    /**
     * Last window we saw that was NOT Jarvis' own UI.
     *
     * This is the app a human was looking at when a consent prompt covered
     * their screen, and therefore the only app an approval can possibly have
     * been about. See [ForegroundGuard] for why reading the plain foreground
     * straight after an approval answers the wrong question.
     */
    @Volatile
    private var lastForeign: ScreenChangeEvent = ScreenChangeEvent.UNKNOWN

    /**
     * `elapsedRealtime` when a Jarvis screen was last observed in front, or 0
     * before it ever has been. The consent prompt is a Jarvis activity, so this
     * is the device-local evidence that somebody was actually asked something.
     */
    private val selfInFrontAt = AtomicLong(0L)

    override fun onServiceConnected() {
        super.onServiceConnected()

        // Capabilities (canRetrieveWindowContent, canPerformGestures) come from
        // @xml/jarvis_accessibility_service and are not settable at runtime.
        // Everything else is set here so the source of truth is code that can be
        // reviewed next to the code that relies on it.
        serviceInfo = AccessibilityServiceInfo().apply {
            eventTypes = AccessibilityEvent.TYPES_ALL_MASK
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS or
                AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            notificationTimeout = NOTIFICATION_TIMEOUT_MS
            // packageNames stays null: every app, because "operate any app" is
            // the whole point. The denylist decides what we refuse to act on.
            packageNames = null
        }

        instance = this
        Log.i(TAG, "accessibility service connected")

        // Publish the delegate so the action registry stops answering
        // "unsupported" for ui_*. Never clobber a delegate someone else
        // installed (a test double, for instance).
        if (ActionEnv.uiDelegate == null) {
            ActionEnv.uiDelegate = UiAutomator.shared(applicationContext)
        }
        publishCapabilities()
    }

    /**
     * Tell the server what this phone can do now that the switch is on.
     *
     * `ActionRegistry.capabilities()` filters on LIVE availability, and the
     * capability list is only sent in the register frame. Without this the
     * server was told once, at startup, that this device has no `ui_automation`
     * — and kept believing it forever: the user enables the scariest toggle
     * Android has and the model still never asks for a tap.
     *
     * `AutomationBridge.onCapabilitiesChanged` is a no-op when no channel is
     * attached, so this is safe on a phone with no server.
     */
    private fun publishCapabilities() {
        runCatching {
            AutomationBridge.uiAutomation = object : AutomationBridge.UiAutomationStatus {
                override fun isReady(): Boolean = JarvisAccessibilityService.isRunning()
                override fun supportedActions(): Set<String> =
                    ActionEnv.uiDelegate?.supportedActions.orEmpty()
            }
            AutomationBridge.onCapabilitiesChanged()
        }.onFailure { Log.w(TAG, "could not publish UI-automation capabilities", it) }
    }

    /**
     * …and tell it again when the switch goes off. The status object stays in
     * place and simply answers `isReady() == false`, which is what the
     * re-registration needs to read.
     */
    private fun retractCapabilities() {
        runCatching { AutomationBridge.onCapabilitiesChanged() }
            .onFailure { Log.w(TAG, "could not retract UI-automation capabilities", it) }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // onInterrupt drops the reference; a live event proves the service is
        // running, so this is how it comes back without the user re-toggling
        // the switch in Settings.
        if (instance !== this) instance = this

        val e = event ?: return
        when (e.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> Unit
            else -> return
        }

        val pkg = e.packageName?.toString()?.takeIf { it.isNotBlank() }
        // For TYPE_WINDOW_STATE_CHANGED the class name is the activity (or the
        // dialog/window class). Deliberately no text, no content description:
        // this event is metadata, not content.
        val cls = e.className?.toString()?.takeIf { it.isNotBlank() }
        if (pkg == null && cls == null) return

        val previous = foreground
        val next = ScreenChangeEvent(
            packageName = pkg ?: previous.packageName,
            activity = if (e.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) cls
            else previous.activity,
            timestamp = System.currentTimeMillis()
        )
        // Before the de-duplication below: a repeat of the same window is still
        // fresh evidence of what is in front, and the consent-evidence check
        // reads a timestamp, not a transition.
        note(next)
        if (next.packageName == previous.packageName && next.activity == previous.activity) return
        foreground = next
        ScreenEvents.emit(next)
    }

    /**
     * "Stop whatever you are doing." Android sends this on things like a
     * screen-off; it is not a teardown, but it is a good moment to abandon any
     * gesture or wait in flight and to fail closed until the next event proves
     * the service is alive again (see [onAccessibilityEvent]).
     */
    override fun onInterrupt() {
        Log.i(TAG, "accessibility service interrupted; abandoning in-flight work")
        UiAutomator.abortInFlight()
        instance = null
    }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        UiAutomator.abortInFlight()
        retractCapabilities()
        Log.i(TAG, "accessibility service unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        UiAutomator.abortInFlight()
        retractCapabilities()
        Log.i(TAG, "accessibility service destroyed")
        super.onDestroy()
    }

    // --- what UiAutomator needs ---------------------------------------------

    /** The active window's root, or null when there is nothing to read. */
    fun activeRoot(): AccessibilityNodeInfo? = try {
        rootInActiveWindow
    } catch (t: Throwable) {
        Log.w(TAG, "rootInActiveWindow failed", t)
        null
    }

    /**
     * Which app and screen is in front right now. Prefers the live window over
     * the cached event, because the event stream lags during transitions and
     * acting on a stale target is exactly the mistake worth avoiding.
     */
    fun currentScreen(): ScreenChangeEvent {
        val live = try {
            rootInActiveWindow?.packageName?.toString()
        } catch (t: Throwable) {
            null
        }
        val cached = foreground
        val now = when {
            live.isNullOrBlank() -> cached
            live == cached.packageName -> cached
            // Package changed under us; the cached activity belongs to the old
            // app, so drop it rather than report a mismatched pair.
            else -> ScreenChangeEvent(live, null, System.currentTimeMillis())
        }
        // Every read is also an observation. The event stream is the primary
        // source, but a consent prompt that opens and closes between two events
        // must still leave its trace, and this is the call that happens while
        // it is on screen.
        note(now)
        return now
    }

    /**
     * The last app in front that was not Jarvis, or [ScreenChangeEvent.UNKNOWN].
     * This is what an approval given under a Jarvis consent prompt was about.
     */
    fun lastForeignScreen(): ScreenChangeEvent = lastForeign

    /**
     * How long ago a Jarvis screen was last in front, in milliseconds, or
     * [Long.MAX_VALUE] if one never has been. Read by [UiAutomator] to check
     * that a consent prompt really did just happen.
     */
    fun msSinceSelfInFront(): Long {
        val at = selfInFrontAt.get()
        return if (at <= 0L) Long.MAX_VALUE else SystemClock.elapsedRealtime() - at
    }

    /** Record what a foreground observation tells us. Cheap; called often. */
    private fun note(screen: ScreenChangeEvent) {
        when {
            ForegroundGuard.isSelf(screen.packageName) ->
                selfInFrontAt.set(SystemClock.elapsedRealtime())

            !screen.packageName.isNullOrBlank() -> {
                val previous = lastForeign
                // A package-only observation (the live-root fallback in
                // [currentScreen]) must not erase the window class we learned
                // from the event stream: the denylist's keyguard and
                // security-settings rules match on that class, so losing it
                // quietly weakens the gate.
                lastForeign = if (screen.activity == null &&
                    previous.activity != null &&
                    previous.packageName == screen.packageName
                ) {
                    screen.copy(activity = previous.activity)
                } else {
                    screen
                }
            }
        }
    }

    companion object {
        private const val TAG = "JarvisA11y"
        private const val NOTIFICATION_TIMEOUT_MS = 100L

        /**
         * The live service, or null when it is not running.
         *
         * A static reference to a Service is normally a leak; here it is the
         * only way for the action layer to reach APIs that exist only on the
         * instance, and it is cleared in [onDestroy], [onUnbind] and
         * [onInterrupt]. Every read must handle null — see [requireService].
         */
        @Volatile
        var instance: JarvisAccessibilityService? = null
            private set

        /**
         * The sentence the model and the user get when the service is off. It
         * names the exact switch to flip; a bare "unsupported" is useless to
         * both.
         */
        const val NOT_ENABLED_ERROR: String =
            "the Jarvis accessibility service is not enabled — turn it on in " +
                "Settings > Accessibility > Installed apps > Jarvis " +
                "(Settings.ACTION_ACCESSIBILITY_SETTINGS), then retry"

        /** True when the service is connected and usable right now. */
        fun isRunning(): Boolean = instance != null

        /** Live service or null. Callers report [NOT_ENABLED_ERROR] on null. */
        fun requireService(): JarvisAccessibilityService? = instance

        /**
         * Deep link to the accessibility settings screen. There is no API to
         * scroll a user to one specific service, so this lands on the list.
         */
        fun settingsIntent(): Intent =
            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        /**
         * Whether the user has granted the service, independent of whether it
         * happens to be bound this instant. Used by the settings screen; the
         * action path uses [isRunning] because a granted-but-dead service still
         * cannot tap anything.
         */
        fun isGranted(context: Context): Boolean = try {
            val manager = context.getSystemService(AccessibilityManager::class.java)
            val wanted = JarvisAccessibilityService::class.java.name
            val enabled = manager
                ?.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
                ?: emptyList()
            enabled.any { info ->
                val service = info.resolveInfo?.serviceInfo
                val byComponent = service != null &&
                    service.packageName == context.packageName &&
                    service.name == wanted
                // `id` is "package/class" and is the more reliable of the two on
                // some OEM builds, so accept either.
                byComponent || (info.id?.contains(wanted) == true)
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not read the enabled accessibility services", t)
            false
        }
    }
}

/**
 * A change of foreground app or screen. Metadata only — no text, no content
 * description, no window title. A trigger may fire on "the banking app came to
 * the front"; it may not smuggle the contents of that app into a rule.
 */
data class ScreenChangeEvent(
    val packageName: String?,
    /** Activity or window class name, when the event carried one. */
    val activity: String?,
    val timestamp: Long
) {
    val isKnown: Boolean get() = !packageName.isNullOrBlank()

    companion object {
        val UNKNOWN = ScreenChangeEvent(null, null, 0L)
    }
}

/**
 * Fan-out for [ScreenChangeEvent], for whoever owns triggers.
 *
 * Deliberately a plain listener list rather than a Flow: this is called from the
 * accessibility callback thread, which must not block, and the emitting side
 * must never be able to hang because a subscriber is slow. A listener that
 * throws is dropped from the notification, not retried.
 */
object ScreenEvents {

    private const val TAG = "JarvisA11yEvents"

    fun interface Listener {
        /** Called on the accessibility thread. Return fast; do not block. */
        fun onScreenChanged(event: ScreenChangeEvent)
    }

    private val listeners = CopyOnWriteArrayList<Listener>()
    private val counter = AtomicLong(0)

    @Volatile
    private var last: ScreenChangeEvent = ScreenChangeEvent.UNKNOWN

    /** Most recent change seen, or [ScreenChangeEvent.UNKNOWN] before the first. */
    fun current(): ScreenChangeEvent = last

    /** How many changes have been emitted since boot. Handy in the debug screen. */
    fun changeCount(): Long = counter.get()

    fun addListener(listener: Listener) {
        listeners.addIfAbsent(listener)
    }

    fun removeListener(listener: Listener) {
        listeners.remove(listener)
    }

    internal fun emit(event: ScreenChangeEvent) {
        last = event
        counter.incrementAndGet()
        for (l in listeners) {
            try {
                l.onScreenChanged(event)
            } catch (t: Throwable) {
                Log.w(TAG, "screen-change listener threw", t)
            }
        }
    }
}
