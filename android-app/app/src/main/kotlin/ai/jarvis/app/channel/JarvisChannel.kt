package ai.jarvis.app.channel

import android.content.Context
import android.os.SystemClock
import android.util.Log
import ai.jarvis.app.automation.AutomationBridge
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONArray
import org.json.JSONObject
import java.util.Random
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * The authenticated command channel between this phone and jarvis-core.
 *
 * One WebSocket to `${serverUrl}/api/websocket`, separate from the voice socket
 * in `assist/AssistPipelineClient` — same protocol, different lifetime and a
 * very different blast radius. Voice is a burst that lives as long as a turn;
 * this one is open for days and can be told to send an SMS.
 *
 * ```
 *  connect ─► auth_required ─► auth{access_token} ─► auth_ok
 *                                                     │
 *                                      jarvis/device/register
 *                                                     │
 *                                              result{ok:true}  ──► READY
 *                                                     │
 *              ◄── device_command ──────────────────  │  ── device_event ──►
 *              ──► device_result ───────────────────  │
 * ```
 *
 * ## What this class refuses to do, whatever the server says
 *
 *  * **Dial a host it was not configured for.** The URL is built from local
 *    config, HTTP redirects are off, and the socket's host is compared against
 *    the configured one at open and again on every `device_command`.
 *  * **Talk in the clear to a public host.** [LanHost.checkUrl] runs before the
 *    socket opens. `http://` reaches RFC1918/link-local/CGNAT/ULA/loopback and
 *    local names, or a host the *user* typed into the acknowledgement list.
 *  * **Lower an action's tier.** The `tier` field is folded in through
 *    [TierGuard.effective], which is `max()`. An action absent from the local
 *    manifest is CONFIRM.
 *  * **Take policy from the wire.** There is no field this class reads that can
 *    grant, remember, or skip a consent prompt. `params` is passed through
 *    untouched to be displayed verbatim; nothing in it is interpreted here.
 *  * **Run the same `command_id` twice**, or run more than one command per
 *    action, or run more than [ChannelConfig.maxConcurrentCommands] at once, or
 *    accept commands faster than the token bucket allows.
 *  * **Hang.** Every accepted command produces exactly one `device_result`,
 *    including on timeout, on a missing dispatcher, and on a crash.
 *
 * Threading: OkHttp delivers callbacks on its own reader thread, and everything
 * that could block — dispatch, approval prompts — is moved onto [scope]. The
 * only work done on the reader thread is parsing and admission.
 */
class JarvisChannel(
    context: Context,
    /**
     * Re-read on every reconnect, so editing the server URL in Settings takes
     * effect on the next attempt without restarting the service. A live socket
     * keeps the snapshot it was opened with.
     */
    private val configProvider: () -> ChannelConfig,
    /** Injected for tests; production reads [AutomationBridge.dispatcher]. */
    private val dispatcherProvider: () -> AutomationBridge.ActionDispatcher? =
        { AutomationBridge.dispatcher },
    /** Monotonic milliseconds. Never wall clock — see [TokenBucket]. */
    private val clock: () -> Long = { SystemClock.elapsedRealtime() }
) : AutomationBridge.ChannelHandle, AutomationBridge.DeviceEventSubscriber {

    enum class State {
        STOPPED,
        /** No network; the loop is parked until one appears. */
        OFFLINE,
        /** Refusing to dial: no config, or the transport policy said no. */
        BLOCKED,
        CONNECTING,
        AUTHENTICATING,
        REGISTERING,
        /** Authenticated and registered. Commands are accepted only in this state. */
        READY,
        BACKING_OFF
    }

    /** Snapshot for the settings screen. Contains no secret. */
    data class Status(
        val state: State = State.STOPPED,
        val host: String? = null,
        val actionCount: Int = 0,
        val lastError: String? = null,
        val nextRetryMs: Long = 0L
    )

    private val appContext = context.applicationContext

    /**
     * Recreated by [start] when a previous [stop] cancelled it, so a service
     * that is stopped and started again gets a working channel instead of a
     * silently dead one.
     */
    @Volatile
    private var scope: CoroutineScope = newScope()

    private fun newScope(): CoroutineScope =
        CoroutineScope(SupervisorJob() + Dispatchers.IO + CoroutineName("jarvis-channel"))

    private val http: OkHttpClient = OkHttpClient.Builder()
        // Protocol-level keepalive. Catches a silently dead NAT binding faster
        // than the application ping, and costs two frames a minute.
        .pingInterval(20, TimeUnit.SECONDS)
        // A WebSocket has no read deadline; the heartbeat is the liveness test.
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .connectTimeout(15, TimeUnit.SECONDS)
        // THE HOST PIN, enforced at the transport. Without this a 30x on the
        // upgrade request could move the socket to a host the user never
        // configured, and every check below would be inspecting the wrong URL.
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(true)
        .build()

    private val watcher = NetworkWatcher(appContext)
    private val gate = CommandGate()
    private val backoff = Backoff()
    private val random = Random()
    private val nextRequestId = AtomicInteger(1)
    private val running = AtomicBoolean(false)

    /** Conflated: one pending "try again now" ticket at most. */
    private val retryTicket = Channel<Unit>(Channel.CONFLATED)

    private val inbound = TokenBucket.forCommands(clock())
    private val outbound = TokenBucket.forEvents(clock())

    private val eventQueue = ArrayDeque<JSONObject>()

    /**
     * Request id -> the coroutine waiting for its `result` frame.
     *
     * Only used for frames THIS device initiated (registration, ping,
     * `conversation/process`). Nothing the server pushes creates an entry, so a
     * hostile server cannot make the phone wait on something it never asked
     * for, and cannot answer a request that does not exist.
     */
    private val pending = ConcurrentHashMap<Int, CompletableDeferred<JSONObject>>()

    @Volatile
    private var session: Session? = null

    @Volatile
    private var config: ChannelConfig = configProvider()

    /**
     * `action id -> tier`, built from the dispatcher's own manifest at each
     * registration. The channel's local tier table. Never contains anything the
     * server sent.
     */
    @Volatile
    private var tierTable: Map<String, WireTier> = emptyMap()

    private val _status = MutableStateFlow(Status())
    val status: StateFlow<Status> get() = _status

    override val isConnected: Boolean get() = session?.authed == true
    override val isRegistered: Boolean get() = session?.registered == true

    // --- lifecycle ----------------------------------------------------------

    /**
     * Start connecting and keep trying until [stop].
     *
     * Also claims the [AutomationBridge] channel slot and subscribes to trigger
     * events, so callers only wire one thing.
     */
    fun start(subscribeToBridgeEvents: Boolean = true) {
        if (!running.compareAndSet(false, true)) return
        if (!scope.isActive) scope = newScope()
        AutomationBridge.channel = this
        // Pass false when trigger events already reach this channel another way
        // — e.g. `AutomationRuntime.deviceEvents = DeviceLink(channel)`. Wiring
        // both paths would send every event to the server twice.
        if (subscribeToBridgeEvents) AutomationBridge.subscribe(this)
        watcher.start()
        scope.launch {
            // A network appearing is a reason to stop waiting out a backoff.
            // Edge-triggered: only a CHANGE issues a ticket, so the loop is not
            // woken by the value it already knows about.
            var seen = watcher.generation.value
            while (isActive) {
                seen = watcher.generation.first { it != seen }
                retryTicket.trySend(Unit)
            }
        }
        scope.launch { connectLoop() }
        Log.i(TAG, "channel started")
    }

    /**
     * Close the socket and stop reconnecting. Idempotent.
     *
     * Cancelling [scope] cancels every in-flight command, and cancelling a
     * command cancels its consent prompt — so nothing that was waiting on a
     * human tap proceeds. The dedupe history goes with it: the server never got
     * a `device_result` for those, so a redelivery after the next [start] is
     * allowed to run from scratch.
     */
    fun stop() {
        if (!running.compareAndSet(true, false)) return
        Log.i(TAG, "channel stopping")
        session?.close(NORMAL_CLOSE, "shutting down")
        session = null
        if (AutomationBridge.channel === this) AutomationBridge.channel = null
        AutomationBridge.unsubscribe(this)
        watcher.stop()
        gate.clearAll()
        failPendingRequests("the channel was stopped")
        synchronized(eventQueue) { eventQueue.clear() }
        setState(State.STOPPED)
        scope.cancel("channel stopped")
    }

    /** Ask the loop to retry now instead of finishing its backoff. */
    fun wake() {
        retryTicket.trySend(Unit)
    }

    // --- the reconnect loop -------------------------------------------------

    private suspend fun connectLoop() {
        while (running.get() && scope.isActive) {
            val cfg = try {
                configProvider()
            } catch (t: Throwable) {
                // Reading settings must never be able to kill the loop; without
                // it the phone stays offline until the service is restarted.
                Log.e(TAG, "could not read the channel configuration", t)
                blocked("could not read the settings on this device")
                waitBeforeRetry(RECHECK_CONFIG_MS)
                continue
            }
            config = cfg

            if (!cfg.isUsable) {
                blocked("no server URL or token configured yet")
                waitBeforeRetry(RECHECK_CONFIG_MS)
                continue
            }

            val verdict = cfg.transportVerdict()
            if (!verdict.allowed) {
                // Not a transient failure — it will be wrong again in a second.
                backoff.penalise()
                blocked(verdict.reason)
                waitBeforeRetry(backoff.next(random.nextDouble()))
                continue
            }
            if (verdict.cleartext) {
                Log.i(TAG, "cleartext permitted: ${verdict.reason}")
            }

            if (!watcher.online.value) {
                setState(State.OFFLINE)
                watcher.awaitOnline()
                if (!running.get()) break
            }

            val url = cfg.websocketUrl
            if (url == null) {
                blocked("the server URL does not parse into a WebSocket URL")
                waitBeforeRetry(RECHECK_CONFIG_MS)
                continue
            }
            val current = Session(cfg)
            session = current
            setState(State.CONNECTING)
            Log.i(TAG, "connecting to ${cfg.pinnedHost} ($cfg)")

            val socket = try {
                http.newWebSocket(Request.Builder().url(url).build(), current)
            } catch (t: Throwable) {
                Log.w(TAG, "could not open the socket", t)
                current.finish("could not open the socket: ${t.javaClass.simpleName}")
                null
            }
            current.attach(socket)

            val outcome = current.finished.await()
            teardown(current)
            if (!running.get()) break

            if (outcome.penalise) backoff.penalise()
            val delayMs = backoff.next(random.nextDouble())
            setState(State.BACKING_OFF, outcome.reason, nextRetryMs = delayMs)
            Log.i(TAG, "reconnecting in ${delayMs}ms after: ${outcome.reason}")
            waitBeforeRetry(delayMs)
        }
        setState(State.STOPPED)
    }

    /** Sleep, but wake early on a network change or an explicit [wake]. */
    private suspend fun waitBeforeRetry(delayMs: Long) {
        while (retryTicket.tryReceive().isSuccess) {
            // Drain tickets issued while we were connected.
        }
        withTimeoutOrNull(delayMs) { retryTicket.receive() }
    }

    private fun teardown(current: Session) {
        current.heartbeat?.cancel()
        current.close(NORMAL_CLOSE, "reconnecting")
        if (session === current) session = null
        failPendingRequests("the socket closed before the server answered")
        // NOTE: in-flight bookkeeping is deliberately NOT cleared here.
        //
        // Losing the socket does not stop a command — the coroutine running it
        // lives in [scope], which survives reconnects, and it may be sitting on
        // a consent prompt the user is reading right now. Clearing the gate
        // would let the redelivery that follows the reconnect start a SECOND
        // copy of the same `sms.send`. The command stays "running" until its
        // coroutine finishes and answers, and the answer is then replayed on
        // whatever socket is live by then.
    }

    // --- registration -------------------------------------------------------

    private fun sendRegister(current: Session) {
        val dispatcher = dispatcherProvider()
        val manifest: JSONArray? = if (dispatcher == null) null else try {
            dispatcher.manifest()
        } catch (t: Throwable) {
            Log.w(TAG, "the action manifest could not be built", t)
            null
        }
        val capabilities: List<String> = if (dispatcher == null) emptyList() else try {
            dispatcher.capabilities()
        } catch (t: Throwable) {
            Log.w(TAG, "the capability list could not be built", t)
            emptyList()
        }

        // The channel's own tier table, from OUR manifest, never from the server.
        tierTable = ChannelFrames.tierTable(manifest)

        val cfg = current.cfg
        val requestId = nextRequestId.getAndIncrement()
        current.registerId = requestId
        setState(State.REGISTERING)

        val frame = ChannelFrames.register(
            requestId = requestId,
            deviceId = cfg.deviceId,
            deviceName = cfg.deviceName,
            capabilities = capabilities,
            appVersion = cfg.appVersion,
            actions = if (cfg.sendManifest) manifest else null
        )
        Log.i(
            TAG,
            "registering ${cfg.deviceId} as \"${cfg.deviceName}\" with " +
                "${capabilities.size} capabilities and ${tierTable.size} actions"
        )
        if (!current.send(frame)) current.finish("could not send the registration frame")
    }

    private fun onRegistered(current: Session) {
        current.registered = true
        backoff.reset()
        inbound.reset(clock())
        setState(State.READY)
        Log.i(TAG, "registered; channel is ready")
        startHeartbeat(current)
        flushEvents(current)
    }

    // --- outbound requests --------------------------------------------------

    /**
     * Send a request frame and wait for its `result`.
     *
     * For the few things the *phone* asks the server, such as
     * `conversation/process` behind an `ask_jarvis` task step. Returns null if
     * the channel is not ready, the send fails, or nothing comes back in time —
     * never throws for an expected failure, so a caller can treat null as "no
     * answer" without a try block.
     *
     * The reply is server output. For `conversation/process` that means LLM
     * text, which may have been shaped by a web page the model read. Treat it
     * as **data**: display it, store it, put it in a notification — but do not
     * let it choose an action without a fresh human approval.
     */
    suspend fun request(
        type: String,
        payload: JSONObject = JSONObject(),
        timeoutMs: Long = DEFAULT_REQUEST_TIMEOUT_MS
    ): JSONObject? {
        val current = session?.takeIf { it.registered } ?: return null
        val id = nextRequestId.getAndIncrement()
        val frame = JSONObject().put("id", id).put("type", type)
        try {
            for (key in payload.keys()) frame.put(key, payload.get(key))
        } catch (t: Throwable) {
            Log.w(TAG, "could not build a $type request", t)
            return null
        }

        val answer = CompletableDeferred<JSONObject>()
        pending[id] = answer
        return try {
            if (!current.send(frame)) null
            else withTimeoutOrNull(timeoutMs) { answer.await() }
        } finally {
            pending.remove(id)
        }
    }

    /**
     * Answer every waiting request with a synthetic failure.
     *
     * Completing rather than cancelling: a caller that loses its socket should
     * get a null back and carry on, not have `CancellationException` thrown
     * through a task run that was otherwise fine.
     */
    private fun failPendingRequests(why: String) {
        if (pending.isEmpty()) return
        val stub = JSONObject()
            .put("type", ChannelFrames.TYPE_RESULT)
            .put("success", false)
            .put("error", JSONObject().put("code", "disconnected").put("message", why))
        val ids = pending.keys.toList()
        for (id in ids) pending.remove(id)?.complete(stub)
    }

    override fun requestReregister() {
        val current = session ?: return
        if (!current.authed) return
        scope.launch { sendRegister(current) }
    }

    // --- heartbeat ----------------------------------------------------------

    private fun startHeartbeat(current: Session) {
        current.heartbeat?.cancel()
        current.heartbeat = scope.launch {
            val cfg = current.cfg
            while (isActive && current.registered) {
                delay(cfg.heartbeatIntervalMs)
                if (!current.registered) return@launch
                val id = nextRequestId.getAndIncrement()
                current.pingSentAt = clock()
                if (!current.send(ChannelFrames.ping(id))) {
                    current.finish("the socket refused a ping")
                    return@launch
                }
                delay(cfg.heartbeatTimeoutMs)
                if (current.lastPongAt < current.pingSentAt) {
                    current.finish("no pong within ${cfg.heartbeatTimeoutMs}ms")
                    return@launch
                }
            }
        }
    }

    // --- inbound ------------------------------------------------------------

    private fun onText(current: Session, text: String) {
        if (session !== current) return
        if (text.length > MAX_FRAME_CHARS) {
            Log.w(TAG, "dropping an oversized frame (${text.length} chars)")
            return
        }
        val msg = try {
            JSONObject(text)
        } catch (t: Throwable) {
            Log.w(TAG, "dropping an unparsable frame")
            return
        }

        when (msg.optString("type")) {
            ChannelFrames.TYPE_AUTH_REQUIRED -> {
                setState(State.AUTHENTICATING)
                // The ONLY place the token is transmitted, and it never appears
                // in a URL, a header, or a log line.
                if (!current.send(ChannelFrames.auth(current.cfg.token))) {
                    current.finish("could not send the auth frame")
                }
            }

            ChannelFrames.TYPE_AUTH_OK -> {
                current.authed = true
                // Off the reader thread: building the manifest asks every action
                // whether it is available right now, which can touch the package
                // manager. Reading the socket must not wait for that.
                scope.launch { sendRegister(current) }
            }

            ChannelFrames.TYPE_AUTH_INVALID -> {
                // Retrying in a second cannot fix a rejected token, and
                // hammering an auth endpoint is how you end up rate-limited or
                // locked out. Sit down for a while and tell the UI why.
                Log.w(TAG, "auth rejected for token ${Redact.token(current.cfg.token)}")
                current.finish(
                    "the server rejected the access token — check it in Settings",
                    penalise = true
                )
            }

            ChannelFrames.TYPE_PONG -> current.lastPongAt = clock()

            ChannelFrames.TYPE_RESULT -> onResult(current, msg)

            ChannelFrames.TYPE_DEVICE_COMMAND -> onDeviceCommand(current, msg)

            else -> Log.d(TAG, "ignoring frame type ${msg.optString("type")}")
        }
    }

    private fun onResult(current: Session, msg: JSONObject) {
        val id = msg.optInt("id", -1)
        // A reply to something this device asked for. Only ids we allocated are
        // in the map, so an unsolicited `result` finds nothing and is ignored.
        pending.remove(id)?.let {
            it.complete(msg)
            return
        }
        if (id != current.registerId) {
            if (!ChannelFrames.isSuccess(msg)) {
                Log.d(TAG, "server refused request $id: ${ChannelFrames.errorOf(msg)}")
            }
            return
        }
        if (ChannelFrames.isSuccess(msg)) {
            onRegistered(current)
        } else {
            val why = ChannelFrames.errorOf(msg)
            Log.w(TAG, "registration refused: $why")
            current.finish("registration refused: $why")
        }
    }

    private fun onDeviceCommand(current: Session, msg: JSONObject) {
        // Commands are accepted ONLY on a socket that authenticated and
        // registered. Anything earlier is either a server bug or somebody
        // talking to us before we know who they are.
        if (!current.registered) {
            Log.w(TAG, "device_command before registration; ignoring")
            return
        }
        // The pin, re-checked per command rather than once at open: it costs a
        // string compare, and a check that runs once is a check that stops
        // running the moment somebody adds a reconnect path around it.
        //
        // Compared as normalised strings, not as resolved addresses. That is
        // fail-closed: an IPv6 literal written long-hand in Settings
        // (`fd00:0:0:0:0:0:0:1`) will not match OkHttp's compressed form and the
        // channel refuses to run commands, loudly, with both values in the log.
        // Refusing a legitimate server is recoverable by editing one field;
        // accepting a host we did not configure is not.
        val socketHost = LanHost.normalize(current.socket?.request()?.url?.host)
        val pinned = current.cfg.pinnedHost
        if (socketHost == null || pinned == null || socketHost != pinned) {
            Log.e(TAG, "host pin violated: socket=$socketHost configured=$pinned")
            current.finish("the socket is not on the configured host", penalise = true)
            return
        }

        val command = ChannelFrames.parseCommand(msg)
        if (command == null) {
            // No command_id means no way to answer. Nothing to do but say so.
            Log.w(TAG, "malformed device_command (missing command_id or action)")
            return
        }

        if (!inbound.tryAcquire(clock())) {
            val wait = inbound.waitMs(clock())
            Log.w(
                TAG,
                "rate limit hit; dropping ${command.action} (${command.commandId}); " +
                    "capacity ${inbound.capacity}/burst, ${inbound.refillPerSecond}/s, retry in ${wait}ms"
            )
            current.send(
                ChannelFrames.deviceResult(
                    command.commandId,
                    ChannelFrames.STATUS_ERROR,
                    "rate limited by the device; retry in ${wait}ms"
                )
            )
            return
        }

        when (val admission = gate.admit(command.commandId, command.action)) {
            is CommandGate.Admission.Accepted -> execute(current, command)

            is CommandGate.Admission.AlreadyAnswered -> {
                Log.i(TAG, "replaying the stored reply for ${command.commandId}")
                current.sendRaw(admission.reply)
            }

            is CommandGate.Admission.StillRunning ->
                Log.i(TAG, "${command.commandId} is already running; it will answer once")

            is CommandGate.Admission.ActionBusy -> current.send(
                ChannelFrames.deviceResult(
                    command.commandId,
                    ChannelFrames.STATUS_ERROR,
                    "${admission.actionId} is already running on this device"
                )
            )

            is CommandGate.Admission.AtCapacity -> current.send(
                ChannelFrames.deviceResult(
                    command.commandId,
                    ChannelFrames.STATUS_ERROR,
                    "the device is already running ${admission.running} commands"
                )
            )

            is CommandGate.Admission.Malformed ->
                Log.w(TAG, "unanswerable device_command: ${admission.why}")
        }
    }

    /**
     * Run one admitted command and answer exactly once.
     *
     * Every exit path from here sends a `device_result`. A command that is
     * admitted and then silently dropped leaves the server waiting forever,
     * which is worse than a refusal: the model has no way to tell "the phone
     * said no" from "the phone is thinking".
     */
    private fun execute(current: Session, command: ChannelFrames.Command) {
        scope.launch {
            val cfg = current.cfg
            var replied = false
            try {
                // RULE: max(local, incoming). The dispatcher then does it again
                // against the real table. Neither one can lower anything.
                val tier = TierGuard.forAction(tierTable, command.action, command.requestedTier)
                if (TierGuard.isDowngradeAttempt(tierTable, command.action, command.requestedTier)) {
                    Log.w(
                        TAG,
                        "the server asked for tier ${command.requestedTier?.wire} on " +
                            "${command.action}; enforcing ${tier.wire}"
                    )
                }
                if (!tierTable.containsKey(command.action)) {
                    Log.w(TAG, "${command.action} is not in the local manifest; treating it as tier 3")
                }

                val dispatcher = dispatcherProvider()
                val body: JSONObject = if (dispatcher == null) {
                    JSONObject()
                        .put("status", ChannelFrames.STATUS_UNSUPPORTED)
                        .put("error", "automation is not running on this device")
                } else {
                    // Called on the captured reference, not through the bridge
                    // slot, so a dispatcher swapped out mid-command cannot turn
                    // into a bogus "timed out".
                    val answer = withTimeoutOrNull(cfg.commandTimeoutMs) {
                        try {
                            if (dispatcher is AutomationBridge.CommandAwareDispatcher) {
                                dispatcher.dispatch(
                                    command.action, command.params, tier.label,
                                    command.reason, command.commandId
                                )
                            } else {
                                dispatcher.dispatch(
                                    command.action, command.params, tier.label, command.reason
                                )
                            }
                        } catch (ce: CancellationException) {
                            throw ce
                        } catch (t: Throwable) {
                            Log.w(TAG, "dispatch of ${command.action} threw", t)
                            JSONObject()
                                .put("status", ChannelFrames.STATUS_ERROR)
                                .put(
                                    "error",
                                    "${t.javaClass.simpleName}: ${t.message ?: "dispatch failed"}"
                                )
                        }
                    }
                    answer ?: JSONObject()
                        .put("status", ChannelFrames.STATUS_ERROR)
                        .put(
                            "error",
                            "${command.action} did not finish within ${cfg.commandTimeoutMs}ms; " +
                                "nothing further will run for this command"
                        )
                }

                reply(command.commandId, ChannelFrames.deviceResult(command.commandId, body))
                replied = true
            } catch (ce: CancellationException) {
                // Shutdown or socket loss. The server got nothing, so forget the
                // command entirely and let a redelivery run it.
                gate.abandon(command.commandId)
                replied = true
                throw ce
            } catch (t: Throwable) {
                Log.e(TAG, "unhandled failure running ${command.action}", t)
                reply(
                    command.commandId,
                    ChannelFrames.deviceResult(
                        command.commandId,
                        ChannelFrames.STATUS_ERROR,
                        "the device failed to run the command: ${t.javaClass.simpleName}"
                    )
                )
                replied = true
            } finally {
                // Belt and braces: a path that somehow escaped without replying
                // must still free the slot, or that action id is wedged forever.
                if (!replied) gate.abandon(command.commandId)
            }
        }
    }

    /**
     * Record the reply, then send it on whatever socket is live now.
     *
     * Order matters. If the send fails because the socket died mid-command, the
     * reply is already in the dedupe history, so the redelivery that follows
     * the reconnect is answered from cache instead of executing the action a
     * second time. A `command_id` is issued by the server and outlives one
     * socket, which is exactly why the answer is not tied to one either.
     */
    private fun reply(commandId: String, frame: JSONObject) {
        val text = frame.toString()
        gate.complete(commandId, text) { id ->
            ChannelFrames.deviceResult(
                id,
                frame.optString("status", ChannelFrames.STATUS_ERROR),
                "the result was too large to keep for replay; it was delivered once"
            ).toString()
        }
        val live = session?.takeIf { it.registered }
        if (live == null || !live.sendRaw(text)) {
            Log.w(TAG, "could not deliver the result for $commandId; it will be replayed on redelivery")
        }
    }

    // --- outbound events ----------------------------------------------------

    override fun onDeviceEvent(event: String, data: JSONObject, untrusted: Boolean) {
        sendEvent(event, data, untrusted)
    }

    override fun sendEvent(event: String, data: JSONObject, untrusted: Boolean): Boolean {
        if (event.isBlank()) return false
        if (!outbound.tryAcquire(clock())) {
            Log.w(TAG, "outbound event rate limit hit; dropping $event")
            return false
        }
        val frame = ChannelFrames.deviceEvent(event, data, untrusted)
        val current = session
        if (current != null && current.registered && current.sendRaw(frame.toString())) return true

        synchronized(eventQueue) {
            var dropped = 0
            while (eventQueue.size >= config.offlineEventQueue) {
                eventQueue.removeFirst()
                dropped++
            }
            if (dropped > 0) Log.w(TAG, "offline event queue full; dropped $dropped oldest")
            eventQueue.addLast(frame)
        }
        return true
    }

    private fun flushEvents(current: Session) {
        val pending = synchronized(eventQueue) {
            if (eventQueue.isEmpty()) return
            val copy = eventQueue.toList()
            eventQueue.clear()
            copy
        }
        Log.i(TAG, "flushing ${pending.size} queued events")
        for (frame in pending) {
            if (!current.sendRaw(frame.toString())) {
                // Socket died mid-flush: keep what is left rather than losing it.
                synchronized(eventQueue) { eventQueue.addLast(frame) }
                break
            }
        }
    }

    // --- status -------------------------------------------------------------

    override fun describe(): String {
        val s = _status.value
        val where = s.host ?: "(no server configured)"
        return buildString {
            append(s.state.name.lowercase().replace('_', ' '))
            append(" · ")
            append(where)
            if (s.actionCount > 0) append(" · ${s.actionCount} actions")
            s.lastError?.let { append(" · ").append(it) }
        }
    }

    private fun blocked(reason: String) {
        if (_status.value.state != State.BLOCKED || _status.value.lastError != reason) {
            Log.w(TAG, "not connecting: $reason")
        }
        setState(State.BLOCKED, reason)
    }

    private fun setState(state: State, error: String? = null, nextRetryMs: Long = 0L) {
        _status.value = Status(
            state = state,
            host = config.pinnedHost,
            actionCount = tierTable.size,
            lastError = error ?: if (state == State.READY) null else _status.value.lastError,
            nextRetryMs = nextRetryMs
        )
    }

    // --- one socket ---------------------------------------------------------

    /** Why a socket ended, and whether it is worth retrying soon. */
    private data class Outcome(val reason: String, val penalise: Boolean)

    /**
     * One connection attempt. Every callback checks `session === this` before
     * touching shared state, so a straggling callback from a socket we already
     * gave up on cannot resurrect it or corrupt the next one.
     */
    private inner class Session(val cfg: ChannelConfig) : WebSocketListener() {

        val finished = CompletableDeferred<Outcome>()

        @Volatile
        var socket: WebSocket? = null

        @Volatile
        var authed = false

        @Volatile
        var registered = false

        @Volatile
        var registerId = -1

        @Volatile
        var pingSentAt = 0L

        @Volatile
        var lastPongAt = 0L

        @Volatile
        var heartbeat: Job? = null

        fun attach(ws: WebSocket?) {
            socket = ws
            if (ws == null) finish("the socket could not be created")
        }

        fun send(frame: JSONObject): Boolean = sendRaw(frame.toString())

        fun sendRaw(text: String): Boolean = try {
            socket?.send(text) == true
        } catch (t: Throwable) {
            Log.w(TAG, "send failed", t)
            false
        }

        fun close(code: Int, reason: String) {
            heartbeat?.cancel()
            try {
                socket?.close(code, reason)
            } catch (t: Throwable) {
                Log.d(TAG, "close failed", t)
            }
        }

        fun finish(reason: String, penalise: Boolean = false) {
            if (finished.isCompleted) return
            registered = false
            authed = false
            // A fixed short reason: the close frame's reason is capped at 123
            // UTF-8 bytes and OkHttp throws on a longer one. The real reason
            // travels in the Outcome, where nothing truncates it.
            close(NORMAL_CLOSE, "client closing")
            finished.complete(Outcome(reason, penalise))
        }

        override fun onOpen(webSocket: WebSocket, response: Response) {
            // The pin, at the transport layer, on the FINAL request — redirects
            // are disabled, so this should equal the URL we built, and if it
            // ever does not, that is exactly the case worth refusing.
            val actual = LanHost.normalize(response.request.url.host)
            val expected = cfg.pinnedHost
            if (actual == null || expected == null || actual != expected) {
                Log.e(TAG, "refusing a socket to $actual; configured host is $expected")
                finish("the server redirected us to $actual", penalise = true)
                return
            }
            Log.i(TAG, "socket open to $actual; waiting for auth_required")
            setState(State.AUTHENTICATING)
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            onText(this, text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            // The command channel is text-only. Audio rides the voice socket.
            Log.d(TAG, "ignoring ${bytes.size} binary bytes on the command channel")
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            finish("the server closed the socket ($code${if (reason.isEmpty()) "" else ": $reason"})")
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            finish("the socket closed ($code)")
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            val code = response?.code
            // 401/403 on the upgrade is a token problem, not a network blip.
            val fatal = code == 401 || code == 403
            val detail = when {
                code != null -> "HTTP $code"
                else -> t.message?.let { Redact.text(it) } ?: t.javaClass.simpleName
            }
            Log.w(TAG, "socket failed: $detail")
            finish("connection failed: $detail", penalise = fatal)
        }
    }

    companion object {
        private const val TAG = "JarvisChannel"

        private const val NORMAL_CLOSE = 1000

        /**
         * Largest inbound text frame we will parse. A `device_command` is a few
         * hundred bytes; anything at this size is a bug or an attempt to make
         * the phone allocate.
         */
        const val MAX_FRAME_CHARS = 512 * 1024

        /** How often to re-check an unconfigured install for a server URL. */
        private const val RECHECK_CONFIG_MS = 30_000L

        /** Default ceiling for a phone-initiated request. */
        const val DEFAULT_REQUEST_TIMEOUT_MS = 30_000L
    }
}
