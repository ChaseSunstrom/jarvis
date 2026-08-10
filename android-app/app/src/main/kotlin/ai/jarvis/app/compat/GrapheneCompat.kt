package ai.jarvis.app.compat

import android.Manifest
import android.app.ActivityManager
import android.app.AlarmManager
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import android.view.accessibility.AccessibilityManager
import java.net.UnknownHostException
import java.util.concurrent.atomic.AtomicInteger

/**
 * Everything this app has to know about running on GrapheneOS without falling
 * over.
 *
 * GrapheneOS is the target, not an afterthought, and it differs from stock
 * Android in ways that break naive code:
 *
 *  * **Network is revocable.** `INTERNET` is an install-time permission on
 *    stock Android — `checkSelfPermission` returns GRANTED for the life of the
 *    install. GrapheneOS adds a per-app **Network** toggle on top of it.
 *    Whether that toggle is visible to `checkSelfPermission` depends on the
 *    OS version and on how the block is enforced, so the check is a useful
 *    signal but not a reliable one: what is certain is that the sockets fail.
 *    Hence [noteNetworkFailure], which folds what actually happened on the
 *    wire back into the verdict.
 *  * **Nothing is guaranteed to be there.** Every `getSystemService` can return
 *    null, every settings screen can be missing, every read can throw. On a
 *    hardened OS "this always works on my phone" is not evidence.
 *  * **Background execution is tight.** The command channel is a foreground
 *    service and still needs the battery exemption to survive.
 *
 * The rule for this whole file: **no method here ever throws.** A probe that
 * cannot answer returns the pessimistic answer and logs. Crashing while
 * checking whether we are allowed to do something would be an unusually stupid
 * way to fail.
 *
 * The verdict logic ([networkVerdict], [classify], [evaluate]) is pure and is
 * mirrored by `tools/boot_timeline_test.py`.
 */
object GrapheneCompat {

    private const val TAG = "GrapheneCompat"

    // ------------------------------------------------------------------
    // Network
    // ------------------------------------------------------------------

    /** What we currently believe about this app's ability to reach the network. */
    enum class NetworkVerdict {
        /** Nothing says otherwise. Carry on. */
        GRANTED,

        /** Definitely blocked: either the permission is gone or the OS said no. */
        DENIED,

        /**
         * Every connection is failing to resolve. That is what a revoked
         * Network toggle looks like from inside the app — but it is also what a
         * server that is switched off looks like, so we say "probably" and let
         * the user judge.
         */
        SUSPECT,
    }

    /** How a failed connection is classified. */
    enum class Signal { SECURITY, HOST, OTHER }

    /** Consecutive resolve failures before we start suspecting the toggle. */
    const val SUSPECT_THRESHOLD = 3

    private val securityDenials = AtomicInteger(0)
    private val hostFailures = AtomicInteger(0)
    private val successes = AtomicInteger(0)

    /**
     * PURE. Which exception means what.
     *
     * A [SecurityException] anywhere in the cause chain is the OS telling us
     * directly. An [UnknownHostException] is circumstantial: DNS not resolving
     * is what a blocked app sees, and also what a typo sees. Everything else —
     * connection refused, timeouts, TLS problems — is a server-side story and
     * says nothing about permissions.
     */
    @JvmStatic
    fun classify(exceptionClassNames: List<String>): Signal {
        for (name in exceptionClassNames) {
            if (name == "java.lang.SecurityException") return Signal.SECURITY
        }
        for (name in exceptionClassNames) {
            if (name == "java.net.UnknownHostException") return Signal.HOST
        }
        return Signal.OTHER
    }

    /**
     * PURE. The verdict, given the permission check and what the wire has done
     * so far.
     *
     * A `SecurityException` outranks everything: it is the OS refusing this
     * app's socket in as many words, and it is the *only* signal here that
     * cannot be produced by a server being switched off.
     *
     * That it outranks a past success matters, because the toggle it detects is
     * revocable at any moment. The counters live for the life of the process
     * and [noteNetworkSuccess] clears the denials, so "we connected once, then
     * the OS started refusing" reads as DENIED — which is the case the whole
     * class exists for — while "the OS refused, then we connected" reads as
     * GRANTED. Ordering these the other way round would let a connection made
     * before the user revoked Network pin the verdict to GRANTED for the rest
     * of the process, and the banner explaining the outage would never appear.
     *
     * One successful connection still outranks any amount of mere suspicion:
     * the app demonstrably has network.
     */
    @JvmStatic
    fun networkVerdict(
        permissionGranted: Boolean,
        securityDenials: Int,
        hostFailures: Int,
        successes: Int,
    ): NetworkVerdict {
        if (!permissionGranted) return NetworkVerdict.DENIED
        if (securityDenials > 0) return NetworkVerdict.DENIED
        if (successes > 0) return NetworkVerdict.GRANTED
        if (hostFailures >= SUSPECT_THRESHOLD) return NetworkVerdict.SUSPECT
        return NetworkVerdict.GRANTED
    }

    /** The live verdict for this process. Never throws. */
    @JvmStatic
    fun networkVerdict(context: Context): NetworkVerdict = networkVerdict(
        permissionGranted = hasPermission(context, Manifest.permission.INTERNET),
        securityDenials = securityDenials.get(),
        hostFailures = hostFailures.get(),
        successes = successes.get(),
    )

    /**
     * Can this app reach the network?
     *
     * True unless we have positive evidence otherwise. Callers should still
     * handle failure — this answers "should I warn the user", not "will this
     * request succeed".
     */
    @JvmStatic
    fun hasNetworkPermission(context: Context): Boolean =
        networkVerdict(context) != NetworkVerdict.DENIED

    /**
     * Record a failed connection attempt. Call this from every network error
     * path; it is what turns "requests keep failing" into a banner that names
     * the actual cause.
     */
    @JvmStatic
    fun noteNetworkFailure(throwable: Throwable?) {
        when (classify(causeChain(throwable))) {
            Signal.SECURITY -> securityDenials.incrementAndGet()
            Signal.HOST -> hostFailures.incrementAndGet()
            Signal.OTHER -> Unit
        }
    }

    /** Record a successful connection. Clears any accumulated suspicion. */
    @JvmStatic
    fun noteNetworkSuccess() {
        successes.incrementAndGet()
        hostFailures.set(0)
        securityDenials.set(0)
    }

    /** Forget everything observed so far — e.g. after the user changed the toggle. */
    @JvmStatic
    fun resetNetworkObservations() {
        securityDenials.set(0)
        hostFailures.set(0)
        successes.set(0)
    }

    /** Exception class names from the throwable down its cause chain. */
    private fun causeChain(throwable: Throwable?): List<String> {
        val names = ArrayList<String>(4)
        var t = throwable
        var guard = 0
        while (t != null && guard < 12) {
            names.add(t.javaClass.name)
            if (t is UnknownHostException) names.add("java.net.UnknownHostException")
            if (t is SecurityException) names.add("java.lang.SecurityException")
            t = t.cause
            guard++
        }
        return names
    }

    /**
     * The banner to show, or null when there is nothing to say. The text names
     * the exact path through GrapheneOS's settings, because "check your network
     * permissions" helps nobody.
     */
    @JvmStatic
    fun networkBanner(context: Context): String? = when (networkVerdict(context)) {
        NetworkVerdict.GRANTED -> null
        NetworkVerdict.DENIED ->
            "Network permission denied — Settings → Apps → Jarvis → " +
                "Permissions → Network"
        NetworkVerdict.SUSPECT ->
            "Cannot reach your server. If this is GrapheneOS, check " +
                "Settings → Apps → Jarvis → Permissions → Network"
    }

    // ------------------------------------------------------------------
    // Battery
    // ------------------------------------------------------------------

    /**
     * True when the OS has put this app in the restricted background bucket.
     * The automation foreground service does not survive that for long.
     */
    @JvmStatic
    fun isRestrictedBattery(context: Context): Boolean = try {
        val am = context.getSystemService(ActivityManager::class.java)
        am?.isBackgroundRestricted ?: false
    } catch (t: Throwable) {
        Log.w(TAG, "background-restricted check failed", t)
        false
    }

    /** True when this app is exempt from doze/battery optimisation. */
    @JvmStatic
    fun isIgnoringBatteryOptimizations(context: Context): Boolean = try {
        val pm = context.getSystemService(PowerManager::class.java)
        pm?.isIgnoringBatteryOptimizations(context.packageName) ?: false
    } catch (t: Throwable) {
        Log.w(TAG, "battery optimisation check failed", t)
        false
    }

    // ------------------------------------------------------------------
    // Individual probes
    // ------------------------------------------------------------------

    /** Null-safe, exception-safe `checkSelfPermission`. */
    @JvmStatic
    fun hasPermission(context: Context, permission: String): Boolean = try {
        context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
    } catch (t: Throwable) {
        Log.w(TAG, "permission check failed for $permission", t)
        false
    }

    /** Does this app currently hold the device assistant role? */
    @JvmStatic
    fun hasAssistantRole(context: Context): Boolean = try {
        val rm = context.getSystemService(RoleManager::class.java)
        rm != null && rm.isRoleAvailable(RoleManager.ROLE_ASSISTANT) &&
            rm.isRoleHeld(RoleManager.ROLE_ASSISTANT)
    } catch (t: Throwable) {
        // The role clears on every reinstall, and on some builds the service is
        // simply absent. Neither is worth an exception reaching the UI.
        Log.w(TAG, "assistant role check failed", t)
        false
    }

    /** Is the Jarvis accessibility service switched on? */
    @JvmStatic
    fun hasAccessibilityService(context: Context): Boolean = try {
        val am = context.getSystemService(AccessibilityManager::class.java)
        val enabled = am?.getEnabledAccessibilityServiceList(
            android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_ALL_MASK
        ).orEmpty()
        enabled.any { it?.resolveInfo?.serviceInfo?.packageName == context.packageName }
    } catch (t: Throwable) {
        Log.w(TAG, "accessibility check failed", t)
        false
    }

    /** Has the user granted notification-listener access to Jarvis? */
    @JvmStatic
    fun hasNotificationListener(context: Context): Boolean = try {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm != null && nm.isNotificationListenerAccessGranted(
            ComponentName(context.packageName, NOTIFICATION_LISTENER_CLASS)
        )
    } catch (t: Throwable) {
        Log.w(TAG, "notification listener check failed", t)
        false
    }

    /** May this app schedule exact alarms? Always true below API 31. */
    @JvmStatic
    fun canScheduleExactAlarms(context: Context): Boolean = try {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            true
        } else {
            context.getSystemService(AlarmManager::class.java)
                ?.canScheduleExactAlarms() ?: false
        }
    } catch (t: Throwable) {
        Log.w(TAG, "exact alarm check failed", t)
        false
    }

    // ------------------------------------------------------------------
    // The requirements checklist
    // ------------------------------------------------------------------

    /** One line of the checklist. */
    data class Requirement(
        /** Stable id; the UI and the tests key off this, not off the label. */
        val id: String,
        /** Short name, as the user would look for it in system settings. */
        val label: String,
        /** What breaks without it — always concrete, never "for full functionality". */
        val why: String,
        val satisfied: Boolean,
        /**
         * True when the app is not usable without it. Optional requirements are
         * shown too, greyed rather than alarming, so the checklist explains the
         * whole surface instead of nagging about features nobody enabled.
         */
        val essential: Boolean,
        /** `Settings.ACTION_*` to open. */
        val settingsAction: String,
        /** Whether that screen wants a `package:` URI. */
        val needsPackageUri: Boolean,
    )

    /** Requirement ids. */
    const val ID_NETWORK = "network"
    const val ID_MICROPHONE = "microphone"
    const val ID_ASSISTANT = "assistant"
    const val ID_ACCESSIBILITY = "accessibility"
    const val ID_NOTIFICATIONS = "notifications"
    const val ID_BATTERY = "battery"
    const val ID_OVERLAY = "overlay"
    const val ID_EXACT_ALARMS = "exact_alarms"

    /** Everything [evaluate] needs, so the verdicts can be tested without a device. */
    data class Status(
        val network: Boolean,
        val microphone: Boolean,
        val assistant: Boolean,
        val accessibility: Boolean,
        val notificationListener: Boolean,
        val batteryExempt: Boolean,
        val batteryRestricted: Boolean,
        val canDrawOverlays: Boolean,
        val exactAlarms: Boolean,
    )

    /**
     * PURE. The checklist for a given [Status], in the order the user should
     * work through it. Order is fixed: network first, because nothing works
     * without it and it is the single most common GrapheneOS surprise.
     */
    @JvmStatic
    fun evaluate(status: Status): List<Requirement> = listOf(
        Requirement(
            id = ID_NETWORK,
            label = "Network",
            why = "Nothing reaches your server without it. GrapheneOS can revoke " +
                "this even though Android normally cannot.",
            satisfied = status.network,
            essential = true,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_MICROPHONE,
            label = "Microphone",
            why = "Required to speak to Jarvis at all.",
            satisfied = status.microphone,
            essential = true,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_ASSISTANT,
            label = "Assistant role",
            why = "Lets the assist gesture and the power-button hold open Jarvis. " +
                "This clears on every reinstall.",
            satisfied = status.assistant,
            essential = false,
            settingsAction = Settings.ACTION_VOICE_INPUT_SETTINGS,
            needsPackageUri = false,
        ),
        Requirement(
            id = ID_ACCESSIBILITY,
            label = "Accessibility",
            why = "Only needed for UI automation. Every action it performs is " +
                "Tier 3 and asks you first.",
            satisfied = status.accessibility,
            essential = false,
            settingsAction = Settings.ACTION_ACCESSIBILITY_SETTINGS,
            needsPackageUri = false,
        ),
        Requirement(
            id = ID_NOTIFICATIONS,
            label = "Notification access",
            why = "Only needed for notification triggers. Notification text is " +
                "treated as untrusted data.",
            satisfied = status.notificationListener,
            essential = false,
            settingsAction = Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS,
            needsPackageUri = false,
        ),
        Requirement(
            id = ID_BATTERY,
            label = "Unrestricted battery",
            why = "The automation service and the wake word are killed without it.",
            satisfied = status.batteryExempt && !status.batteryRestricted,
            essential = true,
            settingsAction = Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_OVERLAY,
            label = "Display over other apps",
            why = "Lets the wake word draw Jarvis over whatever you are using, and " +
                "lets listening restart by itself after a reboot.",
            satisfied = status.canDrawOverlays,
            // Not essential: unrestricted battery already covers restarting on
            // its own, and without this a wake word still arrives — as a
            // notification to tap rather than as an orb on screen.
            essential = false,
            settingsAction = Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_EXACT_ALARMS,
            label = "Exact alarms",
            why = "Time-based automations fire late or not at all without it.",
            satisfied = status.exactAlarms,
            essential = false,
            settingsAction = ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
            needsPackageUri = true,
        ),
    )

    /** Probe the device. Every probe is individually exception-safe. */
    @JvmStatic
    fun status(context: Context): Status = Status(
        network = hasNetworkPermission(context),
        microphone = hasPermission(context, Manifest.permission.RECORD_AUDIO),
        assistant = hasAssistantRole(context),
        accessibility = hasAccessibilityService(context),
        notificationListener = hasNotificationListener(context),
        batteryExempt = isIgnoringBatteryOptimizations(context),
        batteryRestricted = isRestrictedBattery(context),
        canDrawOverlays = canDrawOverlays(context),
        exactAlarms = canScheduleExactAlarms(context),
    )

    /**
     * "Display over other apps".
     *
     * Two jobs, which is why it earns a place on the checklist: it is what lets
     * the wake word put the orb over whatever app is in front, and it is one of
     * the two documented exemptions that let a microphone-typed foreground
     * service start from the background — so without it or the battery
     * exemption, always-on listening does not survive a reboot.
     */
    @JvmStatic
    fun canDrawOverlays(context: Context): Boolean = try {
        Settings.canDrawOverlays(context)
    } catch (t: Throwable) {
        Log.w(TAG, "overlay permission check failed", t)
        false
    }

    /** The live checklist: one screen that says exactly what is missing. */
    @JvmStatic
    fun requirements(context: Context): List<Requirement> = evaluate(status(context))

    /** The essential requirements that are not met, in checklist order. */
    @JvmStatic
    fun missingEssentials(context: Context): List<Requirement> =
        requirements(context).filter { it.essential && !it.satisfied }

    /**
     * The Intent that takes the user to the screen where [requirement] is
     * granted. `FLAG_ACTIVITY_NEW_TASK` is set so this works from a service or
     * a notification as well as from an Activity.
     */
    @JvmStatic
    fun intentFor(context: Context, requirement: Requirement): Intent =
        Intent(requirement.settingsAction).apply {
            if (requirement.needsPackageUri) {
                data = Uri.fromParts("package", context.packageName, null)
            }
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }

    /**
     * Open the settings screen for [requirement], falling back to this app's
     * details page and then giving up quietly. A missing settings screen is a
     * fact about the ROM, not a reason to crash.
     */
    @JvmStatic
    fun openSettingsFor(context: Context, requirement: Requirement): Boolean {
        if (startSafely(context, intentFor(context, requirement))) return true
        return startSafely(context, appDetailsIntent(context))
    }

    /** This app's page in system settings — the universal fallback. */
    @JvmStatic
    fun appDetailsIntent(context: Context): Intent =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = Uri.fromParts("package", context.packageName, null)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }

    /** Open this app's settings page. Used by the network banner's button. */
    @JvmStatic
    fun openAppDetails(context: Context): Boolean =
        startSafely(context, appDetailsIntent(context))

    private fun startSafely(context: Context, intent: Intent): Boolean = try {
        context.startActivity(intent)
        true
    } catch (e: ActivityNotFoundException) {
        Log.w(TAG, "no activity for ${intent.action}", e)
        false
    } catch (t: Throwable) {
        Log.w(TAG, "could not start ${intent.action}", t)
        false
    }

    /**
     * `Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM` is API 31+. The string is
     * stable and harmless on older releases (the intent simply does not
     * resolve, and [openSettingsFor] falls back), so it is written out here
     * rather than guarded at every call site.
     */
    const val ACTION_REQUEST_SCHEDULE_EXACT_ALARM =
        "android.settings.REQUEST_SCHEDULE_EXACT_ALARM"

    /**
     * Declared in AndroidManifest.xml and implemented by the automation module.
     * Named as a string so this module does not import from that one.
     */
    private const val NOTIFICATION_LISTENER_CLASS =
        "ai.jarvis.app.automation.notify.JarvisNotificationListener"
}
