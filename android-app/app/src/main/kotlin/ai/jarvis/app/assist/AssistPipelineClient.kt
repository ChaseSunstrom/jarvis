package ai.jarvis.app.assist

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
 * Voice pipeline client for jarvis-core's WebSocket API at `/api/websocket` —
 * the same protocol the browser HUD speaks, so the phone and the browser stay
 * in lockstep:
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
         * The wake word was heard, and the run has moved on to speech.
         *
         * Only ever fired for a [StartStage.WAKE_WORD] run. Default so the two
         * existing push-to-talk callers need no change.
         */
        fun onWakeWord(name: String) {}
    }

    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var ws: WebSocket? = null
    private val nextId = AtomicInteger(1)

    private var pipelineName = "Jarvis"
    private var pipelineId: String? = null
    private var listRequestId = -1
    private var authed = false

    @Volatile var sttBinaryHandlerId: Int? = null
        private set
    private var conversationId: String? = null
    private var pendingRunAfterList = false

    fun connect(pipelineName: String) {
        this.pipelineName = pipelineName
        val url = serverUrl
            .replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://")
            .trimEnd('/') + "/api/websocket"
        val req = Request.Builder().url(url).build()
        ws = http.newWebSocket(req, this)
    }

    /** Begin a turn: resolve the pipeline (once) then run stt->tts. */
    fun startTurn() {
        if (!authed) return
        if (pipelineId == null && listRequestId < 0) {
            pendingRunAfterList = true
            listPipelines()
        } else {
            runPipeline()
        }
    }

    fun sendAudio(pcm: ByteArray, len: Int) {
        val id = sttBinaryHandlerId ?: return
        val frame = ByteArray(len + 1)
        frame[0] = id.toByte()
        System.arraycopy(pcm, 0, frame, 1, len)
        // ByteString.of(vararg Byte) exists in both okio 2 and 3; frame is
        // already exact-size so no offset variant is needed.
        ws?.send(ByteString.of(*frame))
    }

    /** Signal end-of-audio (lone handler-id byte). */
    fun endAudio() {
        val id = sttBinaryHandlerId ?: return
        ws?.send(ByteString.of(id.toByte()))
        sttBinaryHandlerId = null
    }

    fun close() {
        try { ws?.close(1000, null) } catch (_: Exception) {}
        ws = null
    }

    // --- WebSocketListener -------------------------------------------------

    override fun onMessage(webSocket: WebSocket, text: String) {
        val msg = try { JSONObject(text) } catch (e: Exception) { return }
        when (msg.optString("type")) {
            "auth_required" -> webSocket.send(
                JSONObject().put("type", "auth").put("access_token", token).toString()
            )
            "auth_ok" -> {
                authed = true
                startTurn()
            }
            "auth_invalid" -> post { callbacks.onError("auth failed: check the token") }
            "result" -> if (msg.optInt("id") == listRequestId) handlePipelineList(msg)
            "event" -> handleEvent(msg.optJSONObject("event") ?: return)
        }
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        Log.w(TAG, "ws failure", t)
        post { callbacks.onError("connection error: ${t.message ?: "unreachable"}") }
    }

    // --- protocol ----------------------------------------------------------

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
        val id = nextId.getAndIncrement()
        val run = JSONObject()
            .put("id", id)
            .put("type", "assist_pipeline/run")
            .put("start_stage", startStage.wire)
            .put("end_stage", "tts")
            .put("input", JSONObject().put("sample_rate", 16000))
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
        when (event.optString("type")) {
            "run-start" -> {
                val handler = data?.optJSONObject("runner_data")
                    ?.optInt("stt_binary_handler_id", -1) ?: -1
                if (handler >= 0) sttBinaryHandlerId = handler
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
                    ?.let { conversationId = it }
                val speech = output?.optJSONObject("response")
                    ?.optJSONObject("speech")?.optJSONObject("plain")
                    ?.optString("speech").orEmpty()
                if (speech.isNotEmpty()) post { callbacks.onResponseFinal(speech) }
            }
            "tts-start" -> post { callbacks.onState(State.SPEAKING) }
            "tts-end" -> {
                val url = data?.optJSONObject("tts_output")?.optString("url").orEmpty()
                if (url.isNotEmpty()) {
                    val resolved = absolute(url)
                    if (resolved == null) {
                        Log.w(TAG, "refusing an off-origin tts url")
                        post { callbacks.onError("refused a TTS URL that is not on your server") }
                    } else {
                        post { callbacks.onTtsUrl(resolved) }
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
        ServerUrl.resolveOnServer(serverUrl, pathOrUrl)

    private fun post(block: () -> Unit) = main.post(block)

    companion object {
        private const val TAG = "JarvisPipeline"
    }
}
