package ai.jarvis.app.config

import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The phone's half of `/api/voice/speaker` — enrolling a voice, and asking what
 * the gate thinks of one.
 *
 * ## Why enrolment happens on the phone
 *
 * The microphone is here. The console can show the state of the profile and
 * delete it, but the person enrolling has to say five phrases into something,
 * and on a self-hosted assistant the thing they are holding is the phone.
 *
 * ## What is sent
 *
 * Raw 16 kHz mono 16-bit PCM, one sample per request, to a server that already
 * holds this device's bearer token. Nothing is written to disk on the way — the
 * samples exist in memory for the length of the request and are dropped.
 *
 * The response never contains the voiceprint. It carries counts, scores and
 * timestamps, which is what the enrolment screen draws; the vectors stay on the
 * server, because "is somebody enrolled" must not also answer "what do they
 * sound like".
 *
 * Synchronous by design, and every call must be made off the main thread. The
 * enrolment screen has one thing in flight at a time and a spinner in front of
 * it; a callback API here would buy nothing but a chance to leak an activity.
 */
class VoiceIdentityClient(
    private val serverUrl: String,
    private val token: String,
) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // Embedding a sample is CPU-bound pure Python on the server and takes
        // a few hundred milliseconds; the default read timeout is fine, but it
        // is stated here so nobody "optimises" it down to something that fails
        // on a Pi.
        .readTimeout(30, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    /** What the enrolment screen draws itself from. */
    data class Status(
        val enrolled: Boolean,
        val samples: Int,
        val minSamples: Int,
        val maxSamples: Int,
        val mode: String,
        val active: Boolean,
        val prompts: List<String>,
        val threshold: Double,
        val worstSelfScore: Double?,
        val suggestedThreshold: Double,
    ) {
        /** True once there are enough samples for the profile to verify at all. */
        val usable: Boolean get() = samples >= minSamples

        companion object {
            fun from(json: JSONObject): Status {
                val prompts = ArrayList<String>()
                (json.optJSONArray("prompts") ?: JSONArray()).let { array ->
                    for (index in 0 until array.length()) {
                        array.optString(index).takeIf { it.isNotBlank() }?.let(prompts::add)
                    }
                }
                return Status(
                    enrolled = json.optBoolean("enrolled", false),
                    samples = json.optInt("samples", 0),
                    minSamples = json.optInt("min_samples", 3),
                    maxSamples = json.optInt("max_samples", 20),
                    mode = json.optString("mode", "off"),
                    active = json.optBoolean("active", false),
                    prompts = prompts,
                    threshold = json.optDouble("threshold", 0.0),
                    worstSelfScore = json.optDouble("worst_self_score").takeIf { !it.isNaN() },
                    suggestedThreshold = json.optDouble("suggested_threshold", 0.0),
                )
            }
        }
    }

    /** Either an answer or a sentence to show the user. Never both, never neither. */
    sealed class Result<out T> {
        data class Ok<T>(val value: T) : Result<T>()
        data class Failed(val message: String) : Result<Nothing>()
    }

    fun status(): Result<Status> = get("/api/voice/speaker").map(Status::from)

    /**
     * Add one enrolment sample.
     *
     * One per request rather than five in a batch, because the useful feedback
     * is per sample: "that one was too quiet, say it again" between phrases,
     * rather than a single failure for the whole set at the end.
     */
    fun enrol(pcm: ByteArray): Result<Status> =
        post("/api/voice/speaker/enrol", pcm).map(Status::from)

    /**
     * Score a sample without enrolling it, and report whether it would have
     * been refused. This is how the owner checks the gate will let them in
     * before they turn enforcement on.
     */
    fun verify(pcm: ByteArray): Result<JSONObject> = post("/api/voice/speaker/verify", pcm)

    fun forget(): Result<Status> = delete("/api/voice/speaker").map(Status::from)

    // --- transport ----------------------------------------------------------
    private fun <T> Result<JSONObject>.map(transform: (JSONObject) -> T): Result<T> =
        when (this) {
            is Result.Ok -> Result.Ok(transform(value))
            is Result.Failed -> this
        }

    private fun get(path: String) = call(request(path).get().build())

    private fun delete(path: String) = call(request(path).delete().build())

    private fun post(path: String, body: ByteArray) = call(
        request(path).post(body.toRequestBody(PCM)).build()
    )

    private fun request(path: String): Request.Builder {
        val base = ServerUrl.normalize(serverUrl)
        return Request.Builder()
            .url(base + path)
            .header("Authorization", "Bearer $token")
    }

    private fun call(request: Request): Result<JSONObject> {
        return try {
            http.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    // The server's `detail` is written for a person to act on
                    // ("that sample has no measurable pitch — it is too quiet")
                    // so it is shown rather than replaced with a status code.
                    return Result.Failed(detailOf(text) ?: "server said ${response.code}")
                }
                Result.Ok(JSONObject(text))
            }
        } catch (t: Throwable) {
            Log.w(TAG, "voice identity request failed", t)
            Result.Failed(t.message ?: t.javaClass.simpleName)
        }
    }

    private fun detailOf(body: String): String? = try {
        JSONObject(body).optString("detail").takeIf { it.isNotBlank() }
    } catch (t: Throwable) {
        null
    }

    private companion object {
        const val TAG = "JarvisVoiceId"
        val PCM = "application/octet-stream".toMediaType()
    }
}
