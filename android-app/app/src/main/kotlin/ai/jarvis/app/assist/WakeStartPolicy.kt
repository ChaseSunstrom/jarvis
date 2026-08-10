package ai.jarvis.app.assist

/**
 * Whether the always-on listener can legally be started right now, and what to
 * do when it cannot.
 *
 * This exists because of a platform rule that is easy to miss and impossible to
 * observe: **a foreground service typed `microphone` cannot be started while the
 * app is in the background.** `BOOT_COMPLETED` is an exemption from the general
 * background-start restriction, but it is explicitly *not* an exemption for the
 * while-in-use types (microphone, camera, location). So the receiver that runs
 * after a reboot — the one thing standing between "always on" and "always on
 * until you next restart your phone" — calls `startForegroundService`, the
 * platform throws `ForegroundServiceStartNotAllowedException`, the old code
 * logged a warning nobody has a cable to read, and the listener was simply not
 * running. From the user's side the symptom is exactly what was reported: the
 * wake word works, then one day it does not, and opening the app fixes it.
 *
 * Three of the documented exemptions are reachable for an app like this:
 *
 *  * the start comes from a **foreground Activity** — always allowed, which is
 *    why every one-tap repair in this app routes through
 *    [ai.jarvis.app.ListenTrampolineActivity] rather than calling the service;
 *  * the app is **exempt from battery optimizations** — the user's own decision,
 *    offered in Settings;
 *  * the app holds **SYSTEM_ALERT_WINDOW** ("display over other apps") — which
 *    the Siri-style overlay needs anyway, so the one grant buys both.
 *
 * Kept as plain arithmetic over booleans so the decision is testable on the JVM
 * and mirrored in `android-app/tools/wake_start_policy_test.py`. Nothing here
 * touches Android.
 */
object WakeStartPolicy {

    /** What a caller should do about a requested start. */
    enum class Route {
        /** The user has not asked for always-on listening. Do nothing at all. */
        OFF,

        /**
         * Wanted, but RECORD_AUDIO is not granted. Starting the service would
         * only produce a foreground notification over a mic that cannot open,
         * so the honest move is to ask for the permission — which needs an
         * Activity, not a service.
         */
        NEEDS_MIC_PERMISSION,

        /** Start it now; the platform will allow it. */
        DIRECT,

        /**
         * Wanted and possible, but not from here: the platform will refuse a
         * background start of a microphone service. Put it one tap away instead
         * of failing silently.
         */
        NEEDS_A_TAP,
    }

    /**
     * First SDK level that refuses a background foreground-service start with
     * `ForegroundServiceStartNotAllowedException` (Android 12).
     *
     * Android 11 already restricted *what the mic returns* for a service
     * started from the background, but the start itself succeeded, so a phone
     * on 29/30 gets the direct route and the silence watchdog
     * ([MicSilenceWatch]) catches the muted case if it happens.
     */
    const val FIRST_RESTRICTED_SDK = 31

    /**
     * @param enabled the user's "Listen for Hey Jarvis" switch
     * @param hasMicPermission RECORD_AUDIO is granted
     * @param fromForeground the caller is a resumed Activity (or something the
     *   platform treats as one, such as a running foreground service of ours)
     * @param sdkInt `Build.VERSION.SDK_INT`
     * @param ignoringBatteryOptimizations `PowerManager.isIgnoringBatteryOptimizations`
     * @param canDrawOverlays `Settings.canDrawOverlays`
     */
    fun route(
        enabled: Boolean,
        hasMicPermission: Boolean,
        fromForeground: Boolean,
        sdkInt: Int,
        ignoringBatteryOptimizations: Boolean,
        canDrawOverlays: Boolean,
    ): Route {
        if (!enabled) return Route.OFF
        // Before the start, not after: a service that comes up and immediately
        // finds no permission has already shown a notification saying Jarvis is
        // listening, which would be a lie.
        if (!hasMicPermission) return Route.NEEDS_MIC_PERMISSION
        if (fromForeground) return Route.DIRECT
        if (sdkInt < FIRST_RESTRICTED_SDK) return Route.DIRECT
        if (ignoringBatteryOptimizations || canDrawOverlays) return Route.DIRECT
        return Route.NEEDS_A_TAP
    }

    /**
     * The sentence shown on the "tap to start listening" notification.
     *
     * Written for someone who has just rebooted their phone and does not know
     * that Android has rules about microphones, so it says what happened and
     * what the tap will do — not an error code.
     */
    fun explain(route: Route): String? = when (route) {
        Route.OFF, Route.DIRECT -> null
        Route.NEEDS_MIC_PERMISSION ->
            "Tap to grant the microphone permission and start listening."
        Route.NEEDS_A_TAP ->
            "Android will not let Jarvis open the microphone on its own after a " +
                "restart. Tap to start listening."
    }
}
