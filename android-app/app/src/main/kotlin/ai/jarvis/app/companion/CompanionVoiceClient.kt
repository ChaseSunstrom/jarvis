package ai.jarvis.app.companion

import ai.jarvis.app.assist.MicStreamer
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
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Two single-stage voice runs against jarvis-core's pipeline, for the companion
 * surface only.
 *
 * ```
 *  speak(text)  ->  assist_pipeline/run  start_stage=tts  end_stage=tts
 *                   input={"text": ...}          -> tts-end {tts_output.url}
 *
 *  listen()     ->  assist_pipeline/run  start_stage=stt  end_stage=stt
 *                   mic audio as binary frames   -> stt-end {stt_output.text}
 * ```
 *
 * ## Why this is not [ai.jarvis.app.assist.AssistPipelineClient]
 *
 * That client runs `stt -> tts`: whatever you say goes through the conversation
 * agent, which can call tools. That is exactly right for "hey Jarvis, turn the
 * lights off" and exactly wrong here. When Jarvis has asked "shall I upload the
 * photos?", the spoken reply is an **answer to a question**, and it must be
 * transcribed and handed back to the waiting `companion.ask` — not executed. A
 * user saying "no, delete them" to a question should not have that sentence
 * dispatched as a command.
 *
 * Stopping the intent stage from ever running is a property of the frame this
 * class sends (`end_stage: "stt"`), not of a callback anyone has to remember to
 * unregister. The same reasoning applies in the other direction: the speak path
 * is `start_stage: "tts"`, so the text jarvis-core sent us is spoken back and
 * never re-enters the agent.
 *
 * Everything is best effort. A failure calls back with null and the caller
 * falls back to a notification — the message is never dropped because the
 * socket misbehaved.
 */
class CompanionVoiceClient(
    private val serverUrl: String,
    private val token: String,
    /**
     * Which server is at [serverUrl], if the channel has worked it out.
     *
     * This class used to append `/api/websocket` unconditionally, which is
     * jarvis-core's path. Against the console URL that is a 404 and the spoken
     * question silently became a notification instead — a fallback so graceful
     * that the bug behind it was invisible. There is no discovery loop here
     * because this is a one-shot client; it uses what the command channel
     * already learned, and null still resolves to the more likely candidate
     * rather than to a fixed guess.
     */
    private val serverKind: ServerKind? = null,
) : WebSocketListener() {

    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .connectTimeout(15, TimeUnit.SECONDS)
        // The host pin, at the transport: a 30x on the upgrade must not move
        // this socket — and the bearer token — to another host.
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    private val nextId = AtomicInteger(1)
    private val finished = AtomicBoolean(false)

    /**
     * Which server kinds to try, in order.
     *
     * A one-shot client cannot run the channel's discovery loop, but it can
     * afford a second dial. Trying only the first candidate meant that with the
     * kind not yet discovered, a wrong first guess was indistinguishable from a
     * dead server: one 404 and the spoken question became a notification.
     */
    private val candidates: List<ServerKind> = ServerEndpoint.candidates(serverKind)
    private var attempt = 0

    private var ws: WebSocket? = null
    private var mic: MicStreamer? = null

    @Volatile private var runId = -1
    @Volatile private var binaryHandlerId: Int? = null
    @Volatile private var job: Job? = null

    private sealed class Job {
        data class Speak(val text: String, val onUrl: (String?) -> Unit) : Job()
        data class Listen(
            val onLevel: (Float) -> Unit,
            val onText: (String?) -> Unit,
        ) : Job()
    }

    /**
     * Have the server synthesise [text] and hand back the audio URL, which
     * [ai.jarvis.app.assist.TtsPlayer] can play. `null` means it did not work.
     */
    fun speak(text: String, onUrl: (String?) -> Unit) {
        job = Job.Speak(text, onUrl)
        connect()
    }

    /**
     * Capture from the mic and hand back the transcript. `null` means nothing
     * was recognised or the run failed. Call [endAudio] when the user is done
     * speaking, or [close] to abandon it.
     */
    fun listen(onLevel: (Float) -> Unit, onText: (String?) -> Unit) {
        job = Job.Listen(onLevel, onText)
        connect()
    }

    /** Tell the server the user has stopped talking. */
    fun endAudio() {
        val id = binaryHandlerId ?: return
        binaryHandlerId = null
        mic?.stop()
        mic = null
        try {
            ws?.send(ByteString.of(id.toByte()))
        } catch (t: Throwable) {
            Log.w(TAG, "could not signal end-of-audio", t)
        }
    }

    /** Abandon whatever is in flight. Idempotent; never reports a result. */
    fun close() {
        finished.set(true)
        mic?.stop()
        mic = null
        try {
            ws?.close(1000, null)
        } catch (t: Throwable) {
            // Closing a socket that is already gone is not an error worth
            // surfacing to a user staring at a question.
            Log.d(TAG, "socket already closed", t)
        }
        ws = null
    }

    /** The kind this attempt is dialling. */
    private fun currentKind(): ServerKind = candidates.getOrElse(attempt) { candidates.first() }

    private fun connect() {
        val base = ServerUrl.normalize(serverUrl)
        if (base.isEmpty() || token.isEmpty()) {
            deliver(null)
            return
        }
        val url = ServerEndpoint.websocketUrl(base, currentKind())
        if (url == null) {
            deliver(null)
            return
        }
        ws = try {
            http.newWebSocket(
                Request.Builder()
                    .url(url)
                    // Presented on the upgrade, exactly as JarvisChannel and
                    // AssistPipelineClient do. This was the one WebSocket in the
                    // app that did not, and against the console — which is the
                    // URL a person types, and the first candidate — the relay
                    // answered the handshake with 401 before a single frame was
                    // exchanged. The in-band `auth_required` reply below never
                    // got the chance to run, so the token this class does hold
                    // was never presented anywhere.
                    //
                    // The symptom was not an error. Every proactive line and
                    // every spoken answer fell back to a notification, which is
                    // the designed behaviour for "no surface can speak" — so
                    // the failure looked like a policy decision and only showed
                    // up as a retry loop in logcat.
                    .header("Authorization", "Bearer $token")
                    .build(),
                this,
            )
        } catch (t: Throwable) {
            Log.w(TAG, "could not open the companion voice socket", t)
            deliver(null)
            null
        }
    }

    // --- WebSocketListener --------------------------------------------------

    override fun onMessage(webSocket: WebSocket, text: String) {
        if (text.length > MAX_FRAME_CHARS) return
        val msg = try {
            JSONObject(text)
        } catch (t: Throwable) {
            return
        }
        when (msg.optString("type")) {
            "auth_required" -> webSocket.send(
                // The only frame carrying the token, and only in reply to a
                // server that asked for it.
                JSONObject().put("type", "auth").put("access_token", token).toString()
            )
            "auth_ok" -> startRun(webSocket)
            "auth_invalid" -> {
                Log.w(TAG, "the server rejected the token")
                deliver(null)
            }
            "event" -> onEvent(msg.optJSONObject("event") ?: return)
            "result" -> if (msg.optInt("id", -1) == runId && !isSuccess(msg)) deliver(null)
        }
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        val code = response?.code
        // A 4xx on the *upgrade* is the endpoint saying it did not want this
        // handshake — wrong path, or a relay that will not take our token — and
        // the useful response is to try the other server kind rather than to
        // report "no voice". Anything else (no route to host, TLS, a timeout)
        // is about the network and dialling the same box again would only
        // repeat it.
        if (code != null && code in 400..499 && attempt + 1 < candidates.size && !finished.get()) {
            val next = candidates[attempt + 1]
            Log.i(TAG, "companion voice: HTTP $code as ${currentKind()}, retrying as $next")
            attempt += 1
            ws = null
            connect()
            return
        }
        Log.w(TAG, "companion voice socket failed (HTTP ${code ?: "-"})", t)
        deliver(null)
    }

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
        // A close before a result is a failure, not a silent success.
        deliver(null)
    }

    // --- the run ------------------------------------------------------------

    private fun startRun(webSocket: WebSocket) {
        val current = job ?: return
        runId = nextId.getAndIncrement()
        val run = JSONObject()
            .put("id", runId)
            .put("type", "assist_pipeline/run")

        when (current) {
            is Job.Speak -> {
                run.put("start_stage", "tts")
                    .put("end_stage", "tts")
                    .put("input", JSONObject().put("text", current.text))
            }
            is Job.Listen -> {
                // end_stage "stt": the transcript comes back as text and the
                // conversation agent is never reached. See the class docs.
                run.put("start_stage", "stt")
                    .put("end_stage", "stt")
                    .put("input", JSONObject().put("sample_rate", SAMPLE_RATE))
            }
        }
        webSocket.send(run.toString())
    }

    private fun onEvent(event: JSONObject) {
        val data = event.optJSONObject("data")
        when (event.optString("type")) {
            "run-start" -> {
                val handler = data?.optJSONObject("runner_data")
                    ?.optInt("stt_binary_handler_id", -1) ?: -1
                if (handler >= 0) {
                    binaryHandlerId = handler
                    (job as? Job.Listen)?.let { startMic(it) }
                }
            }
            "stt-end" -> {
                val text = data?.optJSONObject("stt_output")?.optString("text").orEmpty()
                deliver(text.trim().takeIf { it.isNotEmpty() })
            }
            "tts-end" -> {
                val url = data?.optJSONObject("tts_output")?.optString("url").orEmpty()
                // The token rides in a header on the fetch, so the URL has to
                // be on the configured origin or the credential leaks — and it
                // has to go through the relay's media proxy when the relay is
                // what we are talking to. jarvis-core answers with one of its
                // own paths (`/api/tts_proxy/…`), which the console does not
                // serve; fetching it there is a 404, and a 404 on the audio of
                // a spoken reply is silence. ServerEndpoint.mediaUrl knows both
                // shapes, and it re-checks the origin either way.
                deliver(ServerEndpoint.mediaUrl(serverUrl, currentKind(), url))
            }
            "error" -> {
                Log.w(TAG, "pipeline error: ${data?.optString("message").orEmpty()}")
                deliver(null)
            }
            "run-end" -> deliver(null)
        }
    }

    private fun startMic(listen: Job.Listen) {
        if (mic != null) return
        mic = MicStreamer(
            onPcm = { buf, len -> sendAudio(buf, len) },
            onLevel = { level -> main.post { listen.onLevel(level) } },
        ).also {
            try {
                it.start()
            } catch (t: Throwable) {
                // No permission, no mic, a device in a call: all of them mean
                // "cannot take a spoken answer", not "crash the question".
                Log.w(TAG, "could not start the mic", t)
                deliver(null)
            }
        }
    }

    private fun sendAudio(pcm: ByteArray, len: Int) {
        val id = binaryHandlerId ?: return
        val frame = ByteArray(len + 1)
        frame[0] = id.toByte()
        System.arraycopy(pcm, 0, frame, 1, len)
        try {
            ws?.send(ByteString.of(*frame))
        } catch (t: Throwable) {
            Log.d(TAG, "dropping an audio frame", t)
        }
    }

    /** Report the one result, exactly once, on the main thread. */
    private fun deliver(value: String?) {
        if (!finished.compareAndSet(false, true)) return
        val current = job
        job = null
        mic?.stop()
        mic = null
        main.post {
            when (current) {
                is Job.Speak -> current.onUrl(value)
                is Job.Listen -> current.onText(value)
                null -> Unit
            }
        }
        try {
            ws?.close(1000, null)
        } catch (t: Throwable) {
            Log.d(TAG, "socket already closed", t)
        }
        ws = null
    }

    private fun isSuccess(msg: JSONObject): Boolean =
        !msg.has("success") || msg.optBoolean("success", false)

    companion object {
        private const val TAG = "JarvisCompanionVoice"
        private const val SAMPLE_RATE = 16000
        private const val MAX_FRAME_CHARS = 512 * 1024
    }
}
