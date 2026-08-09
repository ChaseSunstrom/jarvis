package ai.jarvis.app.channel

import ai.jarvis.app.BuildConfig
import ai.jarvis.app.automation.AutomationRuntime
import android.content.Context
import android.util.Log

/**
 * The one place in the shipping app that owns a [JarvisChannel].
 *
 * ## Why this file exists
 *
 * The device channel is the transport that carries `device_command` from
 * jarvis-core to this phone and `device_result` back. Without it the phone is
 * not answering "unsupported" to the server — it is not being asked anything at
 * all, because there is no socket to ask it on. Every one of the 48 registered
 * actions was unreachable in a shipping build for exactly this reason: nothing
 * constructed `JarvisChannel`. Its only callers were the debug-only
 * `TestHooks.startChannel` and the instrumented suite, which is precisely how a
 * green `DeviceChannelTest` and a dead device channel coexisted — the test
 * started the channel itself, so it proved the protocol and nothing about
 * startup.
 *
 * ## The wiring, and the one trap in it
 *
 * [AutomationRuntime] leaves two slots for whoever owns the socket,
 * `deviceEvents` and `askJarvis`, both defaulting to no-ops so the phone still
 * runs its automations, enforces its policy and writes its audit log with no
 * server attached. [DeviceLink] fills both.
 *
 * Because it does, the channel is started with `subscribeToBridgeEvents =
 * false`. `JarvisChannel.start` otherwise ALSO subscribes itself to
 * `AutomationBridge.publishEvent`, and every trigger event would reach the
 * server twice — once through the bridge and once through the link. This is
 * called out at `AutomationBridge.publishEvent` and on [DeviceLink]; it is the
 * kind of thing that looks like a server bug for a week.
 *
 * ## Lifetime
 *
 * Owned by `JarvisAutomationService`, which is the process's long-lived
 * component: the channel has to outlive any Activity, and a socket held by a
 * background process on GrapheneOS lives for seconds. An unconfigured phone is
 * fine to start — the channel's connect loop parks in BLOCKED and re-reads the
 * configuration on a timer, so setting a server URL in Settings brings it up
 * without restarting anything.
 */
object DeviceChannelHost {

    private const val TAG = "JarvisChannelHost"

    @Volatile
    private var channel: JarvisChannel? = null

    /** The live channel, for the settings screen's status readout. */
    fun channel(): JarvisChannel? = channel

    /** True once [start] has built a channel that has not been [stop]ped. */
    val isStarted: Boolean get() = channel != null

    /**
     * Build the channel, plug it into the automation seams, and start it.
     *
     * Idempotent and safe to call from any component's startup path.
     */
    @Synchronized
    fun start(context: Context) {
        if (channel != null) return
        val app = context.applicationContext
        val built = try {
            JarvisChannel(
                context = app,
                configProvider = { ChannelConfig.from(app, BuildConfig.VERSION_NAME) },
            )
        } catch (t: Throwable) {
            // A phone that cannot open its command channel must still run its
            // local automations rather than take the service down with it.
            Log.e(TAG, "could not build the device channel", t)
            return
        }
        val link = DeviceLink(built)
        AutomationRuntime.deviceEvents = link
        AutomationRuntime.askJarvis = link
        channel = built
        try {
            // false: DeviceLink is the event path. See the class KDoc.
            built.start(subscribeToBridgeEvents = false)
        } catch (t: Throwable) {
            Log.e(TAG, "could not start the device channel", t)
            stop()
            return
        }
        Log.i(TAG, "device channel started")
    }

    /** Close the socket and clear the seams. Safe when there is no channel. */
    @Synchronized
    fun stop() {
        val existing = channel ?: return
        channel = null
        AutomationRuntime.deviceEvents = null
        AutomationRuntime.askJarvis = null
        runCatching { existing.stop() }
            .onFailure { Log.w(TAG, "device channel stop failed", it) }
    }

    /**
     * The configuration changed (a new server URL or token). Ask the connect
     * loop to retry now rather than finish its backoff; it re-reads the
     * configuration on every attempt.
     */
    fun configChanged() {
        channel?.wake()
    }
}
