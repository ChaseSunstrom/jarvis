package ai.jarvis.app.assist

import ai.jarvis.app.config.ServerEndpoint
import ai.jarvis.app.config.ServerKind
import ai.jarvis.app.config.ServerUrl
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Voice pipeline client, speaking the same protocol the browser HUD speaks so
 * the phone and the browser stay in lockstep:
 *
 *  1. auth handshake (auth_required -> auth -> auth_ok)
 *  2. assist_pipeline/pipeline/list -> resolve the named pipeline's id
 *  3. assist_pipeline/run (stt -> tts), streaming mic audio as binary frames
 *     each prefixed with the run's stt_binary_handler_id; a lone id byte ends
 *     the audio.
 *  4. dispatch events: run-start, stt-end, intent-progress (delta),
 *     intent-end (final speech + conversation_id), tts-start, tts-end (url),
 *     run-end, error.
 *
 * **It talks to either server.** Step 1 is not the same on both, and assuming
 * it was is why voice used to work only inside the management WebView. See
 * [ServerKind]: jarvis-core wants `/api/websocket` and the auth frame from this
 * end, while jarvis-web's console relays `/ws` and swallows the handshake
 * entirely. Pointed at the console, the old client dialled a path that was not
 * there — and even reaching it would not have helped, because it only began a
 * turn inside its `auth_ok` branch and that frame never arrives on a relay.
 * The kind is discovered on first connect and remembered by the caller.
 *
 * All callbacks are delivered on the main thread. OkHttp for the socket,
 * org.json for messages — no extra dependencies.
 *
 * This client carries VOICE only. Device commands ride a separate connection
 * owned by the automation module, because the two have different lifetimes and
 * very different blast radii.
 */
class AssistPipelineClient(
    private val serverUrl: String,
    private val token: String,
    private val callbacks: Callbacks,
    /**
     * Where runs from this client begin. Defaults to the push-to-talk stage,
     * which is what both existing callers want.
     */
    private val startStage: StartStage = StartStage.STT,
    /**
     * What to say, for a [StartStage.INTENT] run. Ignored by the other stages,
     * which get their input from the microphone.
     */
    private val inputText: String? = null,
    /**
     * The kind of server last known to be at [serverUrl], or null to discover
     * it. Passing what was learned last time skips a failed connect.
     */
    private var serverKind: ServerKind? = null,
    /**
     * Called when discovery settles which server is there, so the caller can
     * remember it. Not a callback on [Callbacks] because it is configuration
     * bookkeeping rather than something the UI reacts to.
     */
    private val onKindResolved: (ServerKind) -> Unit = {},
    /**
     * The conversation this run continues, or null to start a new one.
     *
     * **This parameter is the fix for a documented feature that did not work.**
     * `conversationId` was a `private var` with no constructor parameter and no
     * setter: it could be *learned* from an `intent-end` event and could never
     * be *given*, so nothing could seed a conversation. Every client this app
     * built started from nothing, which broke the thread in three places at
     * once — a text turn after a voice turn (`JarvisConversation.speakToServer`
     * builds a second client), the wake orb and the assist card on one phone
     * (each builds its own `JarvisConversation`), and every cross-device
     * `conversation_id` the server sent, which `docs/cross-device.md` promises
     * lands back in the conversation the other device started.
     *
     * The store is [ConversationRegistry]; this is the way in.
     */
    conversationId: String? = null,
    /**
     * Called on the main thread when the server issues or confirms a
     * conversation id, so the caller can persist it. Defaulted, because a
     * one-shot run that does not care is a legitimate caller.
     */
    private val onConversationId: (String) -> Unit = {},
) : WebSocketListener() {

    enum class State { IDLE, LISTENING, THINKING, SPEAKING }

    /**
     * Where a run begins.
     *
     * `STT` is a turn the user has already asked for — a button, the assist
     * gesture — so the audio that follows is speech by definition. `WAKE_WORD`
     * puts openWakeWord in front of it, which is what always-on listening needs:
     * the phone streams continuously and jarvis-core decides when a name was
     * said, so no recognisable audio has to be interpreted on the device.
     */
    enum class StartStage(val wire: String) {
        WAKE_WORD("wake_word"),
        STT("stt"),

        /**
         * Skip transcription: the phone already did it, and sends the sentence.
         *
         * Used when [LocalTranscriber] handled the audio, which is the whole
         * point of on-device speech to text — the utterance never leaves the
         * device, only what it said does.
         */
        INTENT("intent"),
    }

    interface Callbacks {
        fun onState(state: State)
        fun onTranscript(text: String)
        fun onResponseDelta(delta: String)
        fun onResponseFinal(text: String)
        fun onTtsUrl(absoluteUrl: String)
        fun onRunEnd()
        fun onError(message: String)

        /**
         * A sentence of the reply, synthesised while the model writes the next
         * (M60's `tts-chunk`): play it now, in order. Defaulted — a caller that
         * does not chunk still gets the whole reply on [onTtsEnd].
         */
        fun onTtsChunk(absoluteUrl: String, index: Int) {}

        /**
         * `tts-end`, with what M60 added: the whole reply as before, plus the
         * part the chunks did not cover and how many chunks there were. The
         * default keeps the old contract — the whole reply, once.
         */
        fun onTtsEnd(absoluteUrl: String, remainderUrl: String?, chunks: Int) {
            onTtsUrl(absoluteUrl)
        }

        /** Any bus event this device subscribed to, as it arrived. Feeds [ActivityRows]. */
        fun onBusEvent(type: String, data: JSONObject) {}

        /**
         * The wake word was heard, and the run has moved on to speech.
         *
         * Only ever fired for a [StartStage.WAKE_WORD] run. Default so the two
         * existing push-to-talk callers need no change.
         */
        fun onWakeWord(name: String) {}

        /**
         * A tool call started, and then finished.
         *
         * Bus events rather than pipeline events — jarvis-core fires them from
         * the agent loop, so they arrive on the same socket but in a different
         * envelope. Defaulted: a caller that draws no activity panel wants
         * neither, and the subscription costs one frame at the start of a turn.
         */
        fun onToolStarted(
            name: String,
            round: Int,
            index: Int,
            total: Int,
            arguments: List<Pair<String, String>>,
        ) {
        }

        fun onToolFinished(
            name: String,
            round: Int,
            index: Int,
            total: Int,
            ok: Boolean,
            error: String?,
            durationMs: Int,
        ) {
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var ws: WebSocket? = null
    private val nextId = AtomicInteger(1)

    /** Results awaited by id for [request]; a socket that closes forgets them. */
    private val pending = HashMap<Int, (JSONObject?) -> Unit>()

    private var pipelineName = "Jarvis"
    private var pipelineId: String? = null
    private var listRequestId = -1
    private var authed = false
    /** True once this connection has asked to hear about tool calls. */
    private var subscribed = false

    /** Server kinds still to try for this connection, in order. */
    private var attempts: List<ServerKind> = emptyList()
    private var attempt = 0
    /** True once this dial got its upgrade answer, so the watchdog stands down. */
    private var opened = false
    private val currentKind: ServerKind? get() = attempts.getOrNull(attempt)

    @Volatile var sttBinaryHandlerId: Int? = null
        private set

    /**
     * True once this turn's run has opened. Distinguishes "not yet" from
     * "already over", which want opposite treatment for a late capture buffer.
     */
    @Volatile private var runStarted = false

    /** Audio captured before the run could accept it. See [sendAudio]. */
    private val prebuffer = ArrayDeque<ByteArray>()
    private var prebufferedBytes = 0
    /**
     * The thread this client's runs belong to.
     *
     * Public and settable, so a caller that learns of a conversation after
     * construction — a `jarvis_message` arriving mid-turn, a handoff from
     * another device — can join it without tearing the socket down. Seeded from
     * the constructor parameter of the same name.
     */
    @Volatile
    var conversationId: String? = conversationId

    private var pendingRunAfterList = false

    fun connect(pipelineName: String) {
        this.pipelineName = pipelineName
        attempts = ServerEndpoint.candidates(serverKind)
        attempt = 0
        dial()
    }

    /**
     * Open the socket for the current candidate.
     *
     * Built through [ServerUrl.websocketUrl] rather than by string replacement,
     * so a base URL with a reverse-proxy path prefix — or without a scheme —
     * cannot produce a URL that is subtly wrong instead of obviously invalid.
     */
    private fun dial() {
        val kind = attempts.getOrNull(attempt) ?: run {
            post { callbacks.onError(unreachableMessage()) }
            return
        }
        val url = ServerEndpoint.websocketUrl(serverUrl, kind) ?: run {
            post { callbacks.onError("That server address is not usable: $serverUrl") }
            return
        }
        Log.i(TAG, "connecting to $kind at $url")
        authed = false
        opened = false
        // A new socket has none of the old one's subscriptions.
        subscribed = false
        ws = http.newWebSocket(
            Request.Builder()
                .url(url)
                // Presented on the upgrade so jarvis-web's relay passes it
                // through to jarvis-core instead of injecting its own admin
                // token. jarvis-core ignores it here and asks over the socket;
                // either way the handshake below is the same.
                .header("Authorization", "Bearer $token")
                .build(),
            this,
        )
        // `readTimeout(0)` is required — a voice socket is idle for minutes at a
        // time — but it also applies to the upgrade response, so a server that
        // accepts the TCP connection and then says nothing would stall here
        // forever and never let the other candidate be tried. jarvis-web does
        // exactly that for a path its relay does not handle: the upgrade
        // listener returns without answering and the socket is left open.
        main.removeCallbacks(handshakeWatchdog)
        main.postDelayed(handshakeWatchdog, HANDSHAKE_TIMEOUT_MS)
    }

    /** Fires when a dial neither opened nor failed in time. */
    private val handshakeWatchdog = Runnable {
        if (opened) return@Runnable
        Log.w(TAG, "no upgrade answer from ${currentKind}; moving on")
        if (!tryNextCandidate()) callbacks.onError(unreachableMessage())
    }

    /**
     * Move to the next candidate, or give up.
     *
     * Returns true when another attempt was started, so the failure handler
     * knows to stay quiet: reporting "connection error" for a probe that was
     * always going to fail is how a working setup looks broken.
     */
    private fun tryNextCandidate(): Boolean {
        if (authed || attempt >= attempts.lastIndex) return false
        attempt++
        try { ws?.cancel() } catch (_: Exception) {}
        dial()
        return true
    }

    /**
     * True when [webSocket] is the dial currently in flight.
     *
     * A socket that has been abandoned for the next candidate can still deliver
     * a late `onFailure`, and acting on it would report "can't reach Jarvis"
     * over the top of an attempt that is about to succeed — or, worse, resolve
     * the server kind from the wrong socket.
     */
    private fun isCurrent(webSocket: WebSocket): Boolean = webSocket === ws

    private fun unreachableMessage(): String {
        val origin = ServerUrl.originOf(serverUrl)?.toString() ?: serverUrl
        return "Can't reach Jarvis at $origin. Check the address in Settings, " +
            "and that the phone is on the same network or VPN."
    }

    /**
     * Ask to be told about tool calls, once per connection.
     *
     * Bus subscriptions, not pipeline events: jarvis-core fires these from the
     * agent loop around each tool call, so they are the only way to know what a
     * turn is actually touching while it touches it. Sent before the run so no
     * call in the first round is missed.
     *
     * Failure is not handled and does not need to be. An older jarvis-core
     * answers with an error result and fires nothing, which is exactly the
     * behaviour before this existed: the panel stays empty.
     */
    private fun subscribeToToolCalls() {
        if (subscribed) return
        subscribed = true
        // The strip's whole vocabulary (M61), not only the two tool events:
        // `ActivityRows.EVENTS` is the contract the console reads too.
        for (event in ActivityRows.EVENTS.keys) {
            ws?.send(
                JSONObject()
                    .put("id", nextId.getAndIncrement())
                    .put("type", "subscribe_events")
                    .put("event_type", event)
                    .toString()
            )
        }
    }

    /** Begin a turn: resolve the pipeline (once) then run stt->tts. */
    fun startTurn() {
        if (!authed) return
        subscribeToToolCalls()
        if (pipelineId == null && listRequestId < 0) {
            pendingRunAfterList = true
            listPipelines()
        } else {
            runPipeline()
        }
    }

    /**
     * Stream one capture buffer — or keep it until the run can accept it.
     *
     * This used to be `val id = sttBinaryHandlerId ?: return`, and that single
     * `?: return` is why Jarvis could not hear the beginning of anything.
     *
     * [JarvisConversation.start] opens the microphone in the same breath as it
     * dials the socket, but `sttBinaryHandlerId` does not exist until the
     * server sends `run-start` — which is four round trips away: the WebSocket
     * upgrade, the auth handshake, `assist_pipeline/pipeline/list`, and then
     * `assist_pipeline/run`. On a quiet LAN that is a few hundred milliseconds.
     * Against a cold jarvis-core loading models, or when [ServerEndpoint] tries
     * the wrong candidate first and waits out its handshake watchdog, it is
     * seconds. Every frame captured in that window was dropped on the floor.
     *
     * After a wake word the user is ALREADY speaking — "Hey Jarvis, turn on the
     * lights" is one breath — so what was discarded was the front of the
     * command. The symptom is not silence, which somebody would have debugged:
     * it is transcripts that are subtly, consistently wrong, missing their
     * first word or two. "The STT doesn't work that well."
     *
     * So audio captured too early is kept and sent the moment the run opens.
     * Bounded by [MAX_PREBUFFER_BYTES] and dropped oldest-first, because the
     * useful part of a delayed start is the most recent audio, and an unbounded
     * queue against a server that never answers is a memory leak on a phone.
     */
    fun sendAudio(pcm: ByteArray, len: Int) {
        if (len <= 0) return
        val id = sttBinaryHandlerId
        if (id == null) {
            // After `endAudio` the turn is over and its handler id is gone;
            // holding that audio would prepend the tail of one utterance to the
            // start of the next.
            if (!runStarted) hold(pcm, len)
            return
        }
        ws?.send(frameOf(id, pcm, len))
    }

    /** Keep a copy of audio the run is not ready for yet. */
    private fun hold(pcm: ByteArray, len: Int) {
        if (startStage == StartStage.INTENT) return  // this run carries text
        synchronized(prebuffer) {
            prebuffer.addLast(pcm.copyOf(len))
            prebufferedBytes += len
            while (prebufferedBytes > MAX_PREBUFFER_BYTES && prebuffer.isNotEmpty()) {
                prebufferedBytes -= prebuffer.removeFirst().size
            }
        }
    }

    /**
     * Send everything captured before the run opened, oldest first.
     *
     * Called from the `run-start` handler, before anything else can enqueue a
     * later frame, so the utterance stays in order.
     */
    private fun flushPrebuffer(id: Int) {
        val held = synchronized(prebuffer) {
            val copy = prebuffer.toList()
            prebuffer.clear()
            prebufferedBytes = 0
            copy
        }
        if (held.isEmpty()) return
        val socket = ws ?: return
        var bytes = 0
        for (chunk in held) {
            socket.send(frameOf(id, chunk, chunk.size))
            bytes += chunk.size
        }
        Log.i(TAG, "sent ${bytes / BYTES_PER_SECOND.toFloat()}s of audio captured before the run opened")
    }

    /** One binary frame: the run's handler id, then the PCM. */
    private fun frameOf(id: Int, pcm: ByteArray, len: Int): ByteString {
        val frame = ByteArray(len + 1)
        frame[0] = id.toByte()
        System.arraycopy(pcm, 0, frame, 1, len)
        // ByteString.of(vararg Byte) exists in both okio 2 and 3; frame is
        // already exact-size so no offset variant is needed.
        return ByteString.of(*frame)
    }

    /** Signal end-of-audio (lone handler-id byte). */
    fun endAudio() {
        val id = sttBinaryHandlerId ?: return
        ws?.send(ByteString.of(id.toByte()))
        sttBinaryHandlerId = null
    }

    /** Drop anything held. Called when a conversation ends. */
    private fun clearPrebuffer() {
        synchronized(prebuffer) {
            prebuffer.clear()
            prebufferedBytes = 0
        }
    }

    fun close() {
        clearPrebuffer()
        main.removeCallbacks(handshakeWatchdog)
        main.removeCallbacks(assumeAuthed)
        try { ws?.close(1000, null) } catch (_: Exception) {}
        ws = null
    }

    // --- WebSocketListener -------------------------------------------------

    override fun onOpen(webSocket: WebSocket, response: Response) {
        if (!isCurrent(webSocket)) return
        opened = true
        main.removeCallbacks(handshakeWatchdog)
        val kind = currentKind ?: return
        Log.i(TAG, "connected to $kind")
        onKindResolved(kind)
        serverKind = kind
        // Normally the server now asks: jarvis-core always does, and jarvis-web
        // does too once it is passing our token through. But an OLDER jarvis-web
        // authenticates on our behalf and swallows the handshake, so nothing
        // would ever arrive and the turn would never start — the original bug.
        // Rather than pin the app to a matching server version, wait briefly and
        // assume we are already authenticated if asked for nothing.
        main.postDelayed(assumeAuthed, HANDSHAKE_QUIET_MS)
    }

    /**
     * Start anyway when the server never asked us to authenticate.
     *
     * Only reachable against a jarvis-web old enough to still inject its own
     * token. [startTurn] is idempotent via [authed], so a late `auth_required`
     * after this has fired cannot start a second run.
     */
    private val assumeAuthed = Runnable {
        if (authed || ws == null) return@Runnable
        Log.i(TAG, "no auth handshake asked for; assuming the relay authenticated us")
        authed = true
        startTurn()
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        if (!isCurrent(webSocket)) return
        val msg = try { JSONObject(text) } catch (e: Exception) { return }
        when (msg.optString("type")) {
            "auth_required" -> {
                main.removeCallbacks(assumeAuthed)
                webSocket.send(
                    JSONObject().put("type", "auth").put("access_token", token).toString()
                )
            }
            "auth_ok" -> {
                main.removeCallbacks(assumeAuthed)
                if (!authed) {
                    authed = true
                    startTurn()
                }
            }
            "auth_invalid" -> post { callbacks.onError("auth failed: check the token") }
            "result" -> {
                val id = msg.optInt("id")
                if (id == listRequestId) {
                    handlePipelineList(msg)
                } else {
                    val waiting = synchronized(pending) { pending.remove(id) }
                    if (waiting != null) {
                        val result = msg.optJSONObject("result")
                        post { waiting(result) }
                    }
                }
            }
            "event" -> handleEvent(msg.optJSONObject("event") ?: return)
        }
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        if (!isCurrent(webSocket)) return
        main.removeCallbacks(handshakeWatchdog)
        Log.w(TAG, "ws failure (${currentKind}, http ${response?.code})", t)
        // A refused upgrade is the expected answer from the *other* server, so
        // move on quietly rather than reporting a failure the next attempt is
        // about to disprove.
        if (tryNextCandidate()) return
        post { callbacks.onError(unreachableMessage()) }
    }

    // --- protocol ----------------------------------------------------------

    /**
     * One command to jarvis-core, its result handed back on the main thread
     * (M61): what the knowledge graph reads its notes and memory with. False,
     * and nothing sent, before the socket has authenticated — the caller asks
     * again at the next turn, which is when it wants the answer anyway.
     */
    fun request(type: String, onResult: (JSONObject?) -> Unit): Boolean {
        if (!authed) return false
        val id = nextId.getAndIncrement()
        synchronized(pending) { pending[id] = onResult }
        ws?.send(JSONObject().put("id", id).put("type", type).toString())
        return true
    }

    private fun listPipelines() {
        listRequestId = nextId.getAndIncrement()
        ws?.send(
            JSONObject()
                .put("id", listRequestId)
                .put("type", "assist_pipeline/pipeline/list")
                .toString()
        )
    }

    private fun handlePipelineList(msg: JSONObject) {
        val result = msg.optJSONObject("result")
        val arr = result?.optJSONArray("pipelines")
        var chosen: String? = null
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val p = arr.optJSONObject(i) ?: continue
                if (p.optString("name") == pipelineName) chosen = p.optString("id")
            }
        }
        pipelineId = chosen ?: result?.optString("preferred_pipeline")
        listRequestId = -1
        if (pendingRunAfterList) {
            pendingRunAfterList = false
            runPipeline()
        }
    }

    private fun runPipeline() {
        sttBinaryHandlerId = null
        runStarted = false
        val id = nextId.getAndIncrement()
        val run = JSONObject()
            .put("id", id)
            .put("type", "assist_pipeline/run")
            .put("start_stage", startStage.wire)
            .put("end_stage", "tts")
            .put(
                "input",
                if (startStage == StartStage.INTENT) {
                    // `audio_derived` is the honest label on this frame: every
                    // INTENT-start run this app makes carries text that came
                    // out of a microphone on this phone, never off a keyboard.
                    //
                    // It matters because the speaker gate runs on the server,
                    // on SOUND. Words alone cannot be checked, so a server that
                    // is enforcing refuses this rather than letting on-device
                    // transcription walk past the gate. The console's typed
                    // chat does not set it and is unaffected — a person at a
                    // keyboard is authenticated by the token they typed it
                    // with.
                    //
                    // A client could lie by omitting it, and one holding the
                    // token can already send any transcript it likes; this
                    // closes the accident of two settings cancelling each
                    // other, not an attack.
                    JSONObject()
                        .put("text", inputText.orEmpty())
                        .put("audio_derived", true)
                } else {
                    JSONObject().put("sample_rate", 16000)
                },
            )
        pipelineId?.let { run.put("pipeline", it) }
        conversationId?.let { run.put("conversation_id", it) }
        ws?.send(run.toString())
        // A wake-word run is not listening for a command yet — it is waiting to
        // be addressed, and saying LISTENING here would arm the caller's VAD and
        // its inactivity timer against audio that is meant to be ignored.
        if (startStage == StartStage.STT) post { callbacks.onState(State.LISTENING) }
    }

    private fun handleEvent(event: JSONObject) {
        val data = event.optJSONObject("data")
        // Two shapes arrive down this one channel. A pipeline event names itself
        // with `type`; a bus event — which is what a tool call is — names itself
        // with `event_type`. Checking the bus key first is what keeps a future
        // bus event called "run-end" from being mistaken for the pipeline's.
        val busType = event.optString("event_type")
        if (busType.isNotEmpty()) {
            handleBusEvent(busType, data)
            return
        }
        when (event.optString("type")) {
            "run-start" -> {
                val handler = data?.optJSONObject("runner_data")
                    ?.optInt("stt_binary_handler_id", -1) ?: -1
                if (handler >= 0) {
                    // Order matters: the held audio goes out before the id is
                    // published, so a capture thread calling sendAudio cannot
                    // slip a later frame in front of the start of the utterance.
                    flushPrebuffer(handler)
                    sttBinaryHandlerId = handler
                    runStarted = true
                }
            }
            "wake_word-end" -> {
                // openWakeWord heard the name. The run continues straight into
                // STT on the same socket, so from here a wake run behaves
                // exactly like a push-to-talk one — which is why LISTENING is
                // announced here rather than at run time.
                val name = data?.optJSONObject("wake_word_output")
                    ?.optString("wake_word_id").orEmpty()
                post {
                    callbacks.onWakeWord(name)
                    callbacks.onState(State.LISTENING)
                }
            }
            "stt-end" -> {
                val txt = data?.optJSONObject("stt_output")?.optString("text").orEmpty()
                post {
                    callbacks.onTranscript(txt)
                    callbacks.onState(State.THINKING)
                }
            }
            "intent-progress" -> {
                val delta = data?.optJSONObject("chat_log_delta")?.optString("content").orEmpty()
                if (delta.isNotEmpty()) post { callbacks.onResponseDelta(delta) }
            }
            "intent-end" -> {
                val output = data?.optJSONObject("intent_output")
                output?.optString("conversation_id")?.takeIf { it.isNotEmpty() }
                    ?.let {
                        conversationId = it
                        // Told to the caller as well as kept here. This client
                        // lives for one connection; the thread outlives it, and
                        // for the life of the app nothing outside this class
                        // could ever hear about it — which is why every new
                        // client started a fresh conversation.
                        post { onConversationId(it) }
                    }
                val speech = output?.optJSONObject("response")
                    ?.optJSONObject("speech")?.optJSONObject("plain")
                    ?.optString("speech").orEmpty()
                if (speech.isNotEmpty()) post { callbacks.onResponseFinal(speech) }
            }
            "tts-start" -> post { callbacks.onState(State.SPEAKING) }
            "tts-chunk" -> {
                val url = data?.optJSONObject("tts_output")?.optString("url").orEmpty()
                if (url.isNotEmpty()) {
                    val resolved = absolute(url)
                    if (resolved == null) {
                        Log.w(TAG, "refusing an off-origin tts chunk url")
                    } else {
                        val index = data?.optInt("index", 0) ?: 0
                        post {
                            callbacks.onState(State.SPEAKING)
                            callbacks.onTtsChunk(resolved, index)
                        }
                    }
                }
            }
            "tts-end" -> {
                val output = data?.optJSONObject("tts_output")
                val url = output?.optString("url").orEmpty()
                if (url.isNotEmpty()) {
                    val resolved = absolute(url)
                    if (resolved == null) {
                        Log.w(TAG, "refusing an off-origin tts url")
                        post { callbacks.onError("refused a TTS URL that is not on your server") }
                    } else {
                        // The remainder is on the same origin or it is not played.
                        val remainder = output?.optString("remainder_url").orEmpty()
                            .takeIf { it.isNotEmpty() && it != "null" }?.let { absolute(it) }
                        val chunks = output?.optInt("chunks", 0) ?: 0
                        post { callbacks.onTtsEnd(resolved, remainder, chunks) }
                    }
                }
            }
            "run-end" -> post { callbacks.onRunEnd() }
            "error" -> {
                val code = data?.optString("code").orEmpty()
                val message = data?.optString("message").orEmpty()
                post { callbacks.onError("$code: $message") }
            }
        }
    }

    /** What the agent loop is doing, as it does it. See [ToolRun]. */
    private fun handleBusEvent(type: String, data: JSONObject?) {
        if (data == null) return
        // Every subscribed event, as it came: the activity strip's feed (M61).
        post { callbacks.onBusEvent(type, data) }
        when (type) {
            ToolRun.EVENT_STARTED -> {
                val name = data.optString("name").ifEmpty { "tool" }
                post {
                    callbacks.onToolStarted(
                        name,
                        data.optInt("round", 1),
                        data.optInt("index", 0),
                        data.optInt("total", 1),
                        flatten(data.optJSONObject("arguments")),
                    )
                }
            }
            ToolRun.EVENT_FINISHED -> {
                val name = data.optString("name").ifEmpty { "tool" }
                post {
                    callbacks.onToolFinished(
                        name,
                        data.optInt("round", 1),
                        data.optInt("index", 0),
                        data.optInt("total", 1),
                        data.optBoolean("ok", true),
                        data.optString("error").takeIf { it.isNotEmpty() && it != "null" },
                        data.optInt("duration_ms", 0),
                    )
                }
            }
        }
    }

    /**
     * A tool call's arguments as ordered `key to value` pairs.
     *
     * Order matters and is preserved: the first argument of a tool call is
     * almost always the interesting one — the entity, the area, the query — and
     * only the first few fit on a row. Nested values are re-serialised rather
     * than walked, because the row has one line either way.
     */
    private fun flatten(args: JSONObject?): List<Pair<String, String>> {
        if (args == null) return emptyList()
        val out = ArrayList<Pair<String, String>>(args.length())
        val keys = args.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            val value = args.opt(key)
            if (value == null || value === JSONObject.NULL) continue
            out.add(key to value.toString())
        }
        return out
    }

    /**
     * Resolve a URL the server handed back, or null to refuse it.
     *
     * This used to be `if (startsWith("http")) it else serverUrl + it`, which
     * meant the server could name ANY host and the phone would then fetch it
     * with the bearer token attached (see [TtsPlayer]). The server is exactly
     * the component the threat model says may be prompt-injected, so the URL is
     * pinned to the configured origin instead.
     */
    private fun absolute(pathOrUrl: String): String? =
        ServerEndpoint.mediaUrl(serverUrl, currentKind ?: serverKind, pathOrUrl)

    private fun post(block: () -> Unit) = main.post(block)

    companion object {
        private const val TAG = "JarvisPipeline"

        /**
         * How long one candidate gets to answer the upgrade before the other is
         * tried. Generous enough for a sleepy LAN box, short enough that a user
         * who typed the wrong one of two URLs is not left holding a button.
         */
        private const val HANDSHAKE_TIMEOUT_MS = 6_000L

        /**
         * How long to wait for the server to ask us to authenticate before
         * concluding it never will. Only an older jarvis-web does that.
         */
        private const val HANDSHAKE_QUIET_MS = 2_000L

        /** 16 kHz mono PCM16 — what [MicStreamer] captures. */
        const val BYTES_PER_SECOND = 16_000 * 2

        /**
         * How much audio to keep while the run is still opening.
         *
         * Six seconds, which is deliberately more than a fast LAN needs: the
         * case that matters is the slow one — a cold server, or a candidate
         * rotation that waits out [HANDSHAKE_TIMEOUT_MS] before trying the
         * other path. Roughly 190 KB, dropped oldest-first, and only ever held
         * between opening the microphone and the run accepting audio.
         */
        const val MAX_PREBUFFER_BYTES = 6 * BYTES_PER_SECOND
    }
}
