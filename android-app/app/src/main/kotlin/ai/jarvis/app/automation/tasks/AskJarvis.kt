package ai.jarvis.app.automation.tasks

/**
 * The `ask_jarvis` seam: send a prompt to the server's model, get text back.
 *
 * Implemented by whatever owns the command channel to jarvis-core, and
 * registered on `AutomationRuntime.askJarvis`. When nothing is registered, an
 * `ask_jarvis` step fails cleanly and the task stops — it does not silently
 * carry on with an empty answer, because a step that says "ask what to text
 * Sam" and then proceeds with nothing is worse than a task that stopped.
 *
 * ## The reply is tainted, always
 *
 * The answer comes from a language model that reads web pages. It is exactly
 * the kind of text the whole policy model exists to contain, so [TaskRunner]
 * marks the variable it is stored in as tainted, and any later step whose
 * parameters mention that variable dispatches as
 * `TrustLevel.UNTRUSTED` — which can never be auto-allowed.
 *
 * The practical effect: "ask Jarvis what to say, then send it as an SMS" is a
 * legitimate task and it will work. It will simply show the user the actual
 * text, every time, before it goes anywhere.
 */
interface AskJarvisClient {

    /** True when the command channel is up. */
    val isConnected: Boolean

    /**
     * Send [prompt] and return the reply text, or null on timeout/failure.
     *
     * Must not throw for an expected failure. Implementations should honour
     * cancellation: a cancelled task run must not leave a request in flight
     * that later completes into nothing.
     */
    suspend fun ask(prompt: String, timeoutMs: Long): String?
}

/**
 * What the device pushes back up as `device_event`.
 *
 * Implemented by the command-channel owner and registered on
 * `AutomationRuntime.deviceEvents`. When nothing is registered, trigger events
 * still drive local tasks — the phone does not need the server to run an
 * automation, which is the point of doing policy on the device.
 */
interface DeviceEventSink {

    val isConnected: Boolean

    /**
     * Send one `{"type":"device_event","event":…,"data":{…}}` frame.
     *
     * @return true when it went out. A false is not an error worth failing a
     *   task over: the automation already ran locally.
     */
    fun sendEvent(event: String, data: Map<String, Any?>): Boolean
}

/** Fallbacks so the automation layer runs standalone, with no server attached. */
object NoDeviceLink : DeviceEventSink, AskJarvisClient {
    override val isConnected: Boolean get() = false
    override fun sendEvent(event: String, data: Map<String, Any?>): Boolean = false
    override suspend fun ask(prompt: String, timeoutMs: Long): String? = null
}
