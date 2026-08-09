package ai.jarvis.app.automation.actions

import android.content.ComponentName
import android.content.Context
import ai.jarvis.app.automation.notify.JarvisNotificationListener
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.ServerUrl

/**
 * The few pieces of app-wide wiring the built-in actions need but must not
 * reach out and construct themselves. Filled in by
 * [ai.jarvis.app.automation.actions.builtin.Builtins.standard] during startup,
 * before the device WebSocket connects.
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
     * Set from the same config the WebSocket client uses (see [refreshFromConfig]),
     * as the bare host — no scheme, no port, no path. It is re-read on every
     * request, so changing the server URL and calling [refreshFromConfig] takes
     * effect immediately.
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

    /**
     * This build's `versionName`, for anything in the automation layer that
     * wants to say which version acted. The register frame gets its own copy
     * through `ChannelConfig.appVersion`; this is not that.
     */
    @Volatile
    var appVersion: String = "dev"

    /**
     * Hosts exempt from [SsrfGuard]'s private-range block.
     *
     * Exactly one entry at most: the configured jarvis-core host, which this
     * device already trusts enough to hold an authenticated WebSocket to. A
     * blank or unset host yields an EMPTY set, which is the safe direction —
     * `http_request` then simply cannot reach the LAN at all.
     */
    fun allowedHttpHosts(): Set<String> {
        val host = jarvisServerHost?.trim()?.trimEnd('.')?.lowercase()
        return if (host.isNullOrEmpty()) emptySet() else setOf(host)
    }

    /**
     * Re-read everything that comes from user configuration or a system grant.
     *
     * Called when the registry is built, and safe to call again after the user
     * edits the server URL or grants/revokes notification access. Every step is
     * independently guarded: a failure leaves that one slot at its previous
     * value rather than taking the app down at startup.
     */
    fun refreshFromConfig(context: Context) {
        val app = context.applicationContext

        runCatching {
            jarvisServerHost = ServerUrl.originOf(JarvisConfig(app).serverUrl)?.host
        }

        runCatching {
            notificationListener = if (JarvisNotificationListener.isEnabled(app)) {
                JarvisNotificationListener.component(app)
            } else {
                null
            }
        }

        runCatching {
            // The PackageInfoFlags overload only exists from API 33; minSdk is 29.
            @Suppress("DEPRECATION")
            val info = app.packageManager.getPackageInfo(app.packageName, 0)
            appVersion = info.versionName ?: appVersion
        }
    }
}
