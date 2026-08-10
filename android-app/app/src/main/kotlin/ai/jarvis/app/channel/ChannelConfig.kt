package ai.jarvis.app.channel

import android.content.Context
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.ServerEndpoint
import ai.jarvis.app.config.ServerKind
import ai.jarvis.app.config.ServerUrl

/**
 * Everything [JarvisChannel] needs, as an immutable snapshot.
 *
 * A snapshot rather than a live reference, so a settings change mid-connection
 * cannot move the goalposts under a command that is already in flight: the
 * running socket keeps the config it was built with, and the next reconnect
 * picks up the new one. The host pin in particular has to be stable — a pin
 * that can change while a command is being validated is not a pin.
 *
 * Nothing here is ever written from the network.
 */
data class ChannelConfig(
    /** Base URL of jarvis-core, e.g. `http://192.168.2.10:8123`. */
    val serverUrl: String,
    /** Long-lived bearer token. NEVER log this; see [Redact.token]. */
    val token: String,
    /** Stable per-install UUID. Not a hardware id — a reinstall is a new device. */
    val deviceId: String,
    val deviceName: String,
    val appVersion: String,

    /**
     * Hosts the user has explicitly agreed to reach over cleartext despite not
     * being on a private range. Typed by a human in Settings on this device.
     * Populating this from anything the server said would defeat the rule it
     * exists to relax.
     */
    val acknowledgedCleartextHosts: Set<String> = emptySet(),

    /**
     * Which of the two servers is at [serverUrl], if it has been worked out.
     *
     * This channel used to hardcode `/api/websocket`, which is jarvis-core's
     * path and not the console's. So pointing the app at the jarvis-web URL —
     * the one with a web page on it, and the one the app now tells people to
     * use — meant this socket dialled a path that is not there and never
     * connected. Voice worked, because the assist client had already learned to
     * discover the kind; the command channel had not, so `jarvis/device/register`
     * never ran and the console could not see the phone at all. That is the
     * reported symptom: "I have a mobile device connected, but the web app
     * doesn't know about it."
     *
     * Null means "not discovered yet", and [JarvisChannel] then tries the
     * candidates in turn and persists whichever authenticates.
     */
    val serverKind: ServerKind? = null,

    /**
     * Hard ceiling on one command, measured from admission to reply.
     *
     * Generous on purpose: a Tier-3 command spends up to 60 s on the consent
     * screen before its action even starts, and `ApprovalBridge` adds a
     * delivery grace on top. This is the watchdog for a dispatcher that never
     * comes back at all, not a performance budget. On expiry the server gets
     * `status: "error"` — never silence.
     */
    val commandTimeoutMs: Long = DEFAULT_COMMAND_TIMEOUT_MS,

    /** Actions allowed to run at once. See [CommandGate]. */
    val maxConcurrentCommands: Int = CommandGate.DEFAULT_MAX_CONCURRENT,

    /** Application-level ping cadence. jarvis-core answers `ping` with `pong`. */
    val heartbeatIntervalMs: Long = DEFAULT_HEARTBEAT_MS,

    /** No pong within this window and the socket is considered dead. */
    val heartbeatTimeoutMs: Long = DEFAULT_HEARTBEAT_TIMEOUT_MS,

    /** How many `device_event`s to hold while offline before dropping the oldest. */
    val offlineEventQueue: Int = DEFAULT_OFFLINE_EVENT_QUEUE,

    /** Send the action manifest inside the register frame. */
    val sendManifest: Boolean = true
) {

    /**
     * The socket URL for the server we believe is there, or null when
     * [serverUrl] is unusable.
     *
     * Undiscovered falls back to the first candidate rather than to a fixed
     * path, so the "which server is this" question has exactly one answer in
     * this class and [JarvisChannel] rotates through the rest on failure.
     */
    val websocketUrl: String? get() = websocketUrlFor(
        serverKind ?: ServerEndpoint.candidates(null).first()
    )

    /** The socket URL if [kind] is what is at [serverUrl]. */
    fun websocketUrlFor(kind: ServerKind): String? =
        ServerEndpoint.websocketUrl(serverUrl, kind)?.let { collapseIpv6Brackets(it) }

    /**
     * The host every socket is pinned to. Taken from the *configured* URL, not
     * from the socket — comparing the socket against itself proves nothing.
     */
    val pinnedHost: String? get() = LanHost.normalize(ServerUrl.originOf(serverUrl)?.host)

    /** Enough to try: a URL that parses and a token. */
    val isUsable: Boolean
        get() = websocketUrl != null && token.isNotEmpty() && deviceId.isNotEmpty()

    /** Transport verdict for the derived WebSocket URL. */
    fun transportVerdict(): LanHost.Verdict =
        LanHost.checkUrl(websocketUrl ?: serverUrl, acknowledgedCleartextHosts)

    /**
     * Undo the double bracketing an IPv6 literal picks up on its way here.
     *
     * `java.net.URI.getHost()` reports an IPv6 host WITH its square brackets
     * (`[fd00::1]`), and `ServerUrl.websocketUrl` wraps anything containing a
     * colon in brackets again — so `http://[fd00::1]:8123` came out as
     * `ws://[[fd00::1]]:8123/api/websocket`, which `URI.create` rejects as a
     * malformed IPv6 address. The transport check then said "server URL does not
     * parse" and the channel sat in BLOCKED forever: every IPv6-literal server
     * URL was unusable, which is not what `docs/device-channel.md` promises.
     *
     * Only the authority is touched — the part between `://` and the first `/` —
     * so a path prefix that somehow contains brackets is left alone.
     */
    private fun collapseIpv6Brackets(url: String): String {
        val schemeEnd = url.indexOf("://")
        if (schemeEnd < 0) return url
        val authorityStart = schemeEnd + 3
        val pathStart = url.indexOf('/', authorityStart).takeIf { it >= 0 } ?: url.length
        val authority = url.substring(authorityStart, pathStart)
        if (!authority.contains("[[")) return url
        val fixed = authority.replace("[[", "[").replace("]]", "]")
        return url.substring(0, authorityStart) + fixed + url.substring(pathStart)
    }

    /** Safe for a log line: no token, no query string. */
    override fun toString(): String =
        "ChannelConfig(host=$pinnedHost, device=$deviceId/$deviceName, " +
            "version=$appVersion, token=${Redact.token(token)})"

    companion object {
        // The socket path is no longer a constant here: it depends on which of
        // the two servers is at the configured URL, and that is
        // ai.jarvis.app.config.ServerKind's business — see [serverKind].

        const val DEFAULT_COMMAND_TIMEOUT_MS = 180_000L      // 3 min
        const val DEFAULT_HEARTBEAT_MS = 45_000L             // 45 s
        const val DEFAULT_HEARTBEAT_TIMEOUT_MS = 15_000L     // 15 s
        const val DEFAULT_OFFLINE_EVENT_QUEUE = 64

        /**
         * Optional key in the `jarvis_config` prefs: hosts the user has
         * acknowledged for cleartext, comma-separated. Absent by default. The
         * settings screen may write it; nothing else may.
         */
        const val KEY_CLEARTEXT_ACK = "channel_cleartext_ack"

        /**
         * Snapshot the user's configuration.
         *
         * `deviceId` comes from [JarvisConfig], which generates a random UUID on
         * first read and persists it — deliberately not `ANDROID_ID`, not the
         * IMEI, not a MAC. A device identifier that survives a wipe is a
         * tracking identifier, and one that survives a reinstall would let a
         * fresh install inherit an old install's authorisation.
         */
        fun from(context: Context, appVersion: String): ChannelConfig {
            val app = context.applicationContext
            val config = JarvisConfig(app)
            val ack = app.getSharedPreferences(PREFS_FILE, Context.MODE_PRIVATE)
                .getString(KEY_CLEARTEXT_ACK, "")
                .orEmpty()
                .split(',')
                .mapNotNull { LanHost.normalize(it) }
                .toSet()
            return ChannelConfig(
                serverUrl = config.serverUrl,
                token = config.token,
                deviceId = config.deviceId,
                deviceName = config.deviceName,
                appVersion = appVersion,
                acknowledgedCleartextHosts = ack,
                serverKind = config.serverKind,
            )
        }

        /** Mirrors `JarvisConfig.FILE`, which is private to that class. */
        private const val PREFS_FILE = "jarvis_config"
    }
}
