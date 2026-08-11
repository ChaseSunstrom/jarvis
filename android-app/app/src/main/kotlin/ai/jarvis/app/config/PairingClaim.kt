package ai.jarvis.app.config

import ai.jarvis.app.channel.LanHost
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The second half of pairing: turn a scanned code into a real token.
 *
 * [PairingPayload] decides whether a scanned square is addressed to us and
 * whether its address is one this app is allowed to dial. This does the
 * exchange — `POST <url>/api/pair/claim` — and hands back the token the server
 * minted, which is the only moment that value exists anywhere.
 *
 * ## Why a code and not a token in the QR
 *
 * A QR on a screen can be photographed from across a room, ends up in whatever
 * screenshot or shared window captured it, and a token in one stays valid
 * indefinitely. The code is single use and lives five minutes, so a photograph
 * of the console is worthless very shortly after it is taken. See
 * `jarvis-core/jarvis/api/pairing.py`.
 *
 * ## What this refuses to do
 *
 *  * **Follow a redirect.** The claim carries no credential, but the ANSWER is
 *    one, and a 30x would move the request — and the code — to a host the user
 *    never scanned. Off at the client, like every other socket in this app.
 *  * **Talk in the clear to a public host.** [LanHost.checkUrl] again, on the
 *    URL from the QR. The typed field obeys that rule; a printed one must not
 *    be the way around it.
 *  * **Log anything it received.** Not the code, not the token, not the body.
 */
object PairingClaim {

    private const val TAG = "JarvisPairing"
    private val JSON = "application/json; charset=utf-8".toMediaType()

    /** A token, or a sentence explaining why not. */
    sealed interface Result {
        data class Ok(val url: String, val token: String, val name: String) : Result
        data class Failed(val message: String) : Result
    }

    /**
     * Exchange [payload] for a token. Blocking; call from a worker thread.
     *
     * @param deviceName what the server should call this phone. Shown in the
     *   console's device list and in the token list, so a person can revoke the
     *   right one later.
     * @param acknowledgedCleartextHosts hosts the USER has agreed to reach over
     *   plain HTTP despite not being private. Passed through rather than
     *   consulted from config here so this stays testable and so the QR cannot
     *   add to that list.
     */
    fun claim(
        payload: PairingPayload,
        deviceName: String,
        acknowledgedCleartextHosts: Set<String> = emptySet(),
        clientFactory: () -> OkHttpClient = ::defaultClient,
    ): Result {
        val verdict = LanHost.checkUrl(payload.url, acknowledgedCleartextHosts)
        if (!verdict.allowed) {
            return Result.Failed(verdict.reason)
        }
        val body = JSONObject()
            .put("code", payload.code)
            .put("name", deviceName)
            .toString()
            .toRequestBody(JSON)
        val request = Request.Builder()
            .url("${payload.url.trimEnd('/')}/api/pair/claim")
            .post(body)
            .build()

        return try {
            clientFactory().newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return Result.Failed(explain(response.code, text))
                }
                val json = try {
                    JSONObject(text)
                } catch (t: Throwable) {
                    return Result.Failed("The server's answer was not readable.")
                }
                val token = json.optString("token")
                if (token.isEmpty()) {
                    return Result.Failed("The server did not send a token.")
                }
                Result.Ok(
                    url = payload.url,
                    token = token,
                    name = json.optString("name").ifEmpty { deviceName },
                )
            }
        } catch (t: Throwable) {
            // The exception type only. Its message can contain the URL, and a
            // URL in a log line is the start of the pattern this file avoids.
            Log.w(TAG, "pairing failed: ${t.javaClass.simpleName}")
            Result.Failed("Could not reach that Jarvis server (${t.javaClass.simpleName}).")
        }
    }

    /** What a refusal means, in a sentence somebody can act on. */
    private fun explain(code: Int, body: String): String = when (code) {
        403 -> detailOf(body)
            ?: "That pairing code has expired or has already been used. Show a new one."
        404 ->
            "That server has no pairing endpoint. Update Jarvis on your server, or type the " +
                "token by hand."
        else -> "The server answered $code."
    }

    /** The server's own explanation, when it sent one. */
    private fun detailOf(body: String): String? = try {
        JSONObject(body).optString("detail").takeIf { it.isNotEmpty() }
    } catch (t: Throwable) {
        null
    }

    private fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        // The answer to this request is a credential. A redirect must not be
        // able to decide who receives the code, or who supplies the token.
        .followRedirects(false)
        .followSslRedirects(false)
        .build()
}
