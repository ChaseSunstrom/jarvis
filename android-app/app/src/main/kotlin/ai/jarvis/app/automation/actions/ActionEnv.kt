package ai.jarvis.app.automation.actions

import android.content.ComponentName

/**
 * The few pieces of app-wide wiring the built-in actions need but must not
 * reach out and construct themselves. Set once during app startup, before the
 * device WebSocket connects.
 *
 * Everything is `@Volatile` and nullable so an action degrades to a clear
 * "not configured / not available" error instead of crashing.
 */
object ActionEnv {

    /**
     * Host of the configured jarvis-core server, e.g. `10.0.7.2` or
     * `jarvis.lan`. This is the ONLY host `http_request` may reach that would
     * otherwise be blocked by [SsrfGuard] as private/loopback/link-local.
     *
     * Set it from the same config the WebSocket client uses, and set it to the
     * bare host — no scheme, no port, no path.
     */
    @Volatile
    var jarvisServerHost: String? = null

    /** Registered by the accessibility agent once its service connects. */
    @Volatile
    var uiDelegate: UiAutomationDelegate? = null

    /**
     * Component of the app's `NotificationListenerService`, when it exists and
     * the user has granted notification access. Media actions use it to talk to
     * `MediaSessionManager` for precise control; without it they fall back to
     * broadcasting media key events, which needs no special access.
     */
    @Volatile
    var notificationListener: ComponentName? = null

    /** Reported in the register message; also stamped into shell results. */
    @Volatile
    var appVersion: String = "dev"
}
