package ai.jarvis.app.support

import android.util.Log
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONArray
import org.json.JSONObject
import java.net.InetAddress
import java.util.Collections
import java.util.concurrent.atomic.AtomicInteger

/**
 * A jarvis-core stand-in, in the test process, on loopback.
 *
 * ## Why this and not the CI harness
 *
 * `ConversationE2ETest` talks to the real jarvis-core harness, because the point
 * of that test is a real round trip through the real voice pipeline. The channel
 * tests want the opposite: total control over what the server says and exactly
 * when it says it. "Send a Tier-3 `delete_file` now, then send an identical one
 * again after the first is denied" is a script, not a conversation, and driving
 * it through a real server would mean inventing a remote-control API for the
 * harness and making three tests depend on it.
 *
 * So this speaks the handshake — `auth_required` → `auth` → `auth_ok` →
 * `jarvis/device/register` → `result` — and then does what the test tells it.
 * Everything on the DEVICE side of the socket is production code: the real
 * `JarvisChannel`, the real `CommandGate`, the real `TierGuard`, the real
 * `ActionRegistry`, the real `PolicyEngine`, the real `ApprovalActivity`.
 *
 * ## Loopback, not the emulator alias
 *
 * `127.0.0.1` is already on the shipping cleartext allow-list in
 * res/xml/network_security_config.xml, and `LanHost` classifies it as LOOPBACK,
 * so these tests need no debug-only network exemption and no `adb reverse`. It
 * also means the host pin is exercised for real: the app dials the host it was
 * configured with and refuses anything else.
 *
 * Built on OkHttp's MockWebServer at the same version the app uses, so the two
 * ends of the WebSocket cannot drift apart on framing.
 */
class FakeJarvisServer(
    /** The token the device must present. Anything else gets `auth_invalid`. */
    var expectedToken: String = DEFAULT_TOKEN,
) : AutoCloseable {

    private val server = MockWebServer()

    /** Every frame the device sent, in arrival order, across all connections. */
    private val received: MutableList<JSONObject> =
        Collections.synchronizedList(mutableListOf<JSONObject>())

    @Volatile
    private var socket: WebSocket? = null

    private val connectionCount = AtomicInteger(0)

    /** Set false to leave a registration unacknowledged, so the device stays un-READY. */
    @Volatile
    var acknowledgeRegistration: Boolean = true

    /** Frames the server has sent, for a test that wants to assert its own script. */
    private val sent: MutableList<JSONObject> =
        Collections.synchronizedList(mutableListOf<JSONObject>())

    // --- lifecycle ----------------------------------------------------------

    fun start(): FakeJarvisServer {
        server.dispatcher = object : Dispatcher() {
            // Every request is a WebSocket upgrade, whatever the path. A queue
            // dispatcher would 404 the device's second connection attempt, and
            // reconnects are normal: the channel reconnects on any socket loss.
            override fun dispatch(request: RecordedRequest): MockResponse =
                MockResponse().withWebSocketUpgrade(listener)
        }
        // Bound to the literal loopback address, not "localhost", so the URL the
        // app is configured with and the host OkHttp reports back are the same
        // string and JarvisChannel's host pin compares like for like.
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        Log.i(TAG, "fake jarvis-core listening on $baseUrl")
        return this
    }

    /** What to put in `JarvisConfig.serverUrl`. */
    val baseUrl: String get() = "http://127.0.0.1:${server.port}"

    /** How many times the device has opened a socket to us. */
    val connections: Int get() = connectionCount.get()

    override fun close() {
        runCatching { socket?.close(1000, "test over") }
        socket = null
        runCatching { server.shutdown() }
    }

    // --- the handshake ------------------------------------------------------

    private val listener = object : WebSocketListener() {

        override fun onOpen(webSocket: WebSocket, response: Response) {
            socket = webSocket
            connectionCount.incrementAndGet()
            // jarvis-core speaks first, exactly as the real server does.
            send(webSocket, JSONObject().put("type", "auth_required"))
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val frame = try {
                JSONObject(text)
            } catch (t: Throwable) {
                Log.w(TAG, "device sent an unparsable frame: ${text.take(200)}")
                return
            }
            received.add(frame)
            Log.i(TAG, "device -> server: ${redact(frame)}")

            when (frame.optString("type")) {
                "auth" -> {
                    val presented = frame.optString("access_token")
                    if (presented == expectedToken) {
                        send(webSocket, JSONObject().put("type", "auth_ok"))
                    } else {
                        send(webSocket, JSONObject().put("type", "auth_invalid"))
                    }
                }

                "jarvis/device/register" -> {
                    if (!acknowledgeRegistration) return
                    send(
                        webSocket,
                        JSONObject()
                            // Must be a JSON integer: JarvisChannel deliberately
                            // refuses a `result` whose id is the STRING "1", so
                            // a stray frame cannot be mistaken for the reply to
                            // a request this device actually made.
                            .put("id", frame.optInt("id", -1))
                            .put("type", "result")
                            .put("success", true)
                            .put("result", JSONObject())
                    )
                }

                "ping" -> send(
                    webSocket,
                    JSONObject().put("id", frame.optInt("id", -1)).put("type", "pong")
                )
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.i(TAG, "socket failed on the server side: ${t.javaClass.simpleName}: ${t.message}")
            if (socket === webSocket) socket = null
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (socket === webSocket) socket = null
        }
    }

    // --- sending ------------------------------------------------------------

    /** Push a raw frame. Fails the test if there is no live socket. */
    fun send(frame: JSONObject) {
        val live = socket ?: error(
            "the fake server has no live socket; the device has not connected " +
                "(or has disconnected). Frames sent so far: ${sent.size}"
        )
        send(live, frame)
    }

    private fun send(webSocket: WebSocket, frame: JSONObject) {
        sent.add(frame)
        Log.i(TAG, "server -> device: ${redact(frame)}")
        webSocket.send(frame.toString())
    }

    /**
     * Send a `device_command`.
     *
     * @param tier the wire tier, 1|2|3. Advisory: the device folds it in through
     *   `TierGuard.effective`, which is `max()`, so a test that sends 1 for a
     *   Tier-3 action is testing that the device refuses to be talked down.
     */
    fun sendDeviceCommand(
        commandId: String,
        action: String,
        params: JSONObject = JSONObject(),
        tier: Int? = null,
        reason: String = "an instrumented test asked for it",
    ) {
        val frame = JSONObject()
            .put("type", "device_command")
            .put("command_id", commandId)
            .put("action", action)
            .put("params", params)
            .put("reason", reason)
        if (tier != null) frame.put("tier", tier)
        send(frame)
    }

    /** Send a `jarvis_message`. Defaults describe a question with options. */
    fun sendCompanionMessage(
        messageId: String,
        text: String,
        options: List<String> = emptyList(),
        kind: String = "ask",
        mode: String = "ask",
        importance: String = "normal",
        timeoutSeconds: Int = 120,
    ) {
        val frame = JSONObject()
            .put("type", "jarvis_message")
            .put("message_id", messageId)
            .put("kind", kind)
            .put("mode", mode)
            .put("text", text)
            .put("importance", importance)
            .put("timeout_s", timeoutSeconds)
        if (options.isNotEmpty()) {
            frame.put("options", JSONArray().apply { options.forEach { put(it) } })
        }
        send(frame)
    }

    // --- receiving ----------------------------------------------------------

    /** A snapshot of every frame the device has sent. */
    fun allFrames(): List<JSONObject> = synchronized(received) { received.toList() }

    /** Frames of one `type`, in arrival order. */
    fun frames(type: String): List<JSONObject> =
        allFrames().filter { it.optString("type") == type }

    /** `device_result` frames for one `command_id`. */
    fun deviceResults(commandId: String): List<JSONObject> =
        frames("device_result").filter { it.optString("command_id") == commandId }

    /** `jarvis_message_result` frames for one `message_id`. */
    fun messageResults(messageId: String): List<JSONObject> =
        frames("jarvis_message_result").filter { it.optString("message_id") == messageId }

    /**
     * Wait until the device registers, and return the register frame.
     *
     * Proves the whole handshake in one call: a register frame can only be sent
     * on a socket that has already authenticated.
     */
    fun awaitRegistration(timeoutMs: Long = Waits.NETWORK_TIMEOUT_MS): JSONObject =
        Waits.untilPresent(
            "the device to authenticate and send jarvis/device/register to $baseUrl",
            timeoutMs,
        ) {
            frames("jarvis/device/register").lastOrNull()
        }

    /** Wait for the single `device_result` answering [commandId]. */
    fun awaitDeviceResult(
        commandId: String,
        timeoutMs: Long = Waits.NETWORK_TIMEOUT_MS,
    ): JSONObject = Waits.untilPresent(
        "a device_result for command $commandId",
        timeoutMs,
    ) {
        deviceResults(commandId).firstOrNull()
    }

    /** Wait for a `jarvis_message_result` answering [messageId]. */
    fun awaitMessageResult(
        messageId: String,
        timeoutMs: Long = Waits.NETWORK_TIMEOUT_MS,
    ): JSONObject = Waits.untilPresent(
        "a jarvis_message_result for message $messageId",
        timeoutMs,
    ) {
        messageResults(messageId).firstOrNull()
    }

    /** Wait until at least [count] results have arrived for [messageId]. */
    fun awaitMessageResultCount(
        messageId: String,
        count: Int,
        timeoutMs: Long = Waits.NETWORK_TIMEOUT_MS,
    ): List<JSONObject> = Waits.untilPresent(
        "$count jarvis_message_result frame(s) for message $messageId",
        timeoutMs,
    ) {
        messageResults(messageId).takeIf { it.size >= count }
    }

    // --- logging ------------------------------------------------------------

    /**
     * A frame safe to put in logcat: the access token never appears, even in a
     * test. A CI log is an artefact that outlives the run.
     */
    private fun redact(frame: JSONObject): String {
        if (!frame.has("access_token")) return frame.toString().take(MAX_LOG_CHARS)
        val copy = JSONObject(frame.toString())
        copy.put("access_token", "<redacted ${frame.optString("access_token").length} chars>")
        return copy.toString().take(MAX_LOG_CHARS)
    }

    companion object {
        private const val TAG = "JarvisFakeServer"
        private const val MAX_LOG_CHARS = 1_200

        /** Arbitrary, but long enough to look like the real thing in a log. */
        const val DEFAULT_TOKEN = "instrumented-test-token-0123456789"
    }
}
