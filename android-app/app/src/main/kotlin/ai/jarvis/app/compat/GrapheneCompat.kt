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
    const val ID_POST_NOTIFICATIONS = "post_notifications"
    const val ID_ON_SCREEN = "on_screen"
    const val ID_FULL_SCREEN = "full_screen"
    const val ID_EXACT_ALARMS = "exact_alarms"

    /**
     * The action permissions, re-exported from [RuntimePermissions], which is
     * where the decision about which grants share a row is made. Every
     * dangerous permission in that table belongs to exactly one of these — the
     * invariant `tools/runtime_permissions_test.py` enforces, and the reason
     * "Everything is granted" can be believed.
     */
    const val ID_PEOPLE = RuntimePermissions.ID_PEOPLE
    const val ID_CALENDAR = RuntimePermissions.ID_CALENDAR
    const val ID_LOCATION = RuntimePermissions.ID_LOCATION
    const val ID_MEDIA = RuntimePermissions.ID_MEDIA
    const val ID_SENSORS = RuntimePermissions.ID_SENSORS

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
        val postNotifications: Boolean,
        val fullScreenIntents: Boolean,
        val exactAlarms: Boolean,
        /**
         * The action permissions, one flag per [RuntimePermissions] group.
         *
         * Defaulted to true so that a caller constructing a [Status] by hand —
         * a test of the network or on-screen logic, say — does not have to know
         * about grants it is not testing. The real probe in [status] never uses
         * the defaults.
         */
        val people: Boolean = true,
        val calendar: Boolean = true,
        val location: Boolean = true,
        val media: Boolean = true,
        val sensors: Boolean = true,
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
            id = ID_POST_NOTIFICATIONS,
            label = "Show notifications",
            why = "A runtime permission since Android 13, and nothing had ever " +
                "asked for it. Denied, Jarvis cannot show the listening " +
                "notification, the wake-word alert, or a Tier-3 approval — which " +
                "means approvals time out unanswered.",
            satisfied = status.postNotifications,
            essential = true,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_ON_SCREEN,
            label = "Appear on screen",
            why = "Whether saying “Hey Jarvis” can put anything in front of you. " +
                "Needs EITHER “display over other apps” or “full screen " +
                "notifications” — either one is enough, and with neither the wake " +
                "word can only leave a notification for you to tap.",
            // The disjunction is the point, and it is why this entry exists at
            // all. The two below are each optional BECAUSE either satisfies
            // this — which meant that with neither granted, nothing on the
            // checklist was both essential and missing, no banner appeared, and
            // the phone sat in exactly the broken state that was reported with
            // nothing anywhere saying so.
            //
            // The action points at the overlay switch: it is the better of the
            // two — the orb is drawn directly, with no notification in the way —
            // and it doubles as the exemption that lets listening restart after
            // a reboot.
            satisfied = status.canDrawOverlays || status.fullScreenIntents,
            essential = true,
            settingsAction = Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_FULL_SCREEN,
            label = "Full screen notifications",
            why = "What makes a wake word take over the screen instead of waiting " +
                "in the shade for a tap. Android 14 grants it only to calling and " +
                "alarm apps, so it has to be turned on by hand.",
            satisfied = status.fullScreenIntents,
            // Not essential: with "display over other apps" granted, Jarvis
            // draws the orb directly and never needs this path at all.
            essential = false,
            settingsAction = ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
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

        // --- what Jarvis may DO, as opposed to whether it runs at all -------
        //
        // These are the runtime permissions the actions need. They were all
        // declared in the manifest and none of them was ever requested, so
        // every one of them was denied on every device — and this screen said
        // "Everything is granted", because it did not list them.
        //
        // None is essential: Jarvis works perfectly well on a phone where you
        // never want it touching your messages. Each is now requested at the
        // moment an action needs it (see RuntimePermissions), so these rows are
        // the answer to "why did it say permission not granted", not the only
        // way to grant them.
        Requirement(
            id = ID_PEOPLE,
            label = "Contacts, messages & calls",
            why = "Texting or calling somebody by name, reading your messages and " +
                "the call log, hanging up. Without contacts, \"text Sam\" cannot " +
                "become a number; without SMS and phone, the send, the read and " +
                "the call fail after you have already approved them.",
            satisfied = status.people,
            essential = false,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_CALENDAR,
            label = "Calendar",
            why = "Reading what is on today and putting something in the diary.",
            satisfied = status.calendar,
            essential = false,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_LOCATION,
            label = "Location",
            why = "\"Where am I\", navigation, and anything that depends on where " +
                "you are. Location triggers while Jarvis is off screen need the " +
                "background grant too, which Android only offers in Settings.",
            satisfied = status.location,
            essential = false,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_MEDIA,
            label = "Camera & media",
            why = "\"What am I looking at\", and reading a photo or a recording you " +
                "point Jarvis at.",
            satisfied = status.media,
            essential = false,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            needsPackageUri = true,
        ),
        Requirement(
            id = ID_SENSORS,
            label = "Activity & nearby devices",
            why = "Step count, and knowing which car or headset you are connected " +
                "to — which is what the wake-word gate keys off.",
            satisfied = status.sensors,
            essential = false,
            settingsAction = Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
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
        postNotifications = canPostNotifications(context),
        fullScreenIntents = canUseFullScreenIntent(context),
        exactAlarms = canScheduleExactAlarms(context),
        people = RuntimePermissions.groupHeld(context, ID_PEOPLE),
        calendar = RuntimePermissions.groupHeld(context, ID_CALENDAR),
        location = RuntimePermissions.groupHeld(context, ID_LOCATION),
        media = RuntimePermissions.groupHeld(context, ID_MEDIA),
        sensors = RuntimePermissions.groupHeld(context, ID_SENSORS),
    )

    /**
     * Whether Jarvis may show a notification at all.
     *
     * A runtime permission since Android 13 (API 33), and — until this — one
     * nothing in the app had ever asked for. Below 33 it is granted by being
     * declared, so that half of the check is version-gated.
     *
     * `areNotificationsEnabled` is the other half and is not version-gated,
     * because the permission is not the only way to end up silent: the user can
     * switch Jarvis's notifications off in Settings on any Android version, and
     * from 13 the two answers can also disagree in the permission's favour.
     * Both matter for the same reason — `NotificationManager.notify` DOES NOT
     * THROW when the notification will not be shown. It returns normally and
     * posts nothing. Anything that treats a clean return as "the user can see
     * this" is wrong on both paths, which is exactly how [PermissionBridge]
     * came to block a command for 65 seconds waiting on a prompt that had never
     * been on screen.
     */
    @JvmStatic
    fun canPostNotifications(context: Context): Boolean = try {
        val permitted = Build.VERSION.SDK_INT < 33 ||
            hasPermission(context, "android.permission.POST_NOTIFICATIONS")
        permitted && context.getSystemService(NotificationManager::class.java)
            ?.areNotificationsEnabled() == true
    } catch (t: Throwable) {
        Log.w(TAG, "notification permission check failed", t)
        false
    }

    /**
     * Whether a full-screen intent will actually take over the screen.
     *
     * Below 34 the manifest declaration is the whole story. From 34 the
     * platform grants it at install only to calling and alarm apps and asks
     * everyone else to turn it on by hand, silently downgrading
     * `setFullScreenIntent` to a heads-up until they do.
     */
    @JvmStatic
    fun canUseFullScreenIntent(context: Context): Boolean = try {
        if (Build.VERSION.SDK_INT < 34) {
            hasPermission(context, "android.permission.USE_FULL_SCREEN_INTENT")
        } else {
            context.getSystemService(NotificationManager::class.java)
                ?.canUseFullScreenIntent() ?: false
        }
    } catch (t: Throwable) {
        Log.w(TAG, "full-screen intent check failed", t)
        false
    }

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
     * `Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT` is API 34+, written
     * out for the same reason as the constant above.
     *
     * This is the screen behind the reported symptom: on Android 14 the
     * permission is declared in the manifest, held, and *not granted* — the
     * platform reserves the install-time grant for calling and alarm apps.
     * Everything else gets a heads-up notification instead of a takeover, so a
     * wake word "just sits in the notification bar" until it is tapped.
     */
    const val ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT =
        "android.settings.MANAGE_APP_USE_FULL_SCREEN_INTENT"

    /**
     * Declared in AndroidManifest.xml and implemented by the automation module.
     * Named as a string so this module does not import from that one.
     */
    private const val NOTIFICATION_LISTENER_CLASS =
        "ai.jarvis.app.automation.notify.JarvisNotificationListener"
}
