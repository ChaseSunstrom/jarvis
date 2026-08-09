package ai.jarvis.app.automation.actions.builtin

import android.content.Context
import ai.jarvis.app.automation.actions.ActionEnv
import ai.jarvis.app.automation.actions.ActionResult
import ai.jarvis.app.automation.actions.JarvisAction
import ai.jarvis.app.automation.actions.SsrfGuard
import ai.jarvis.app.automation.actions.intOr
import ai.jarvis.app.automation.actions.json
import ai.jarvis.app.automation.actions.markUntrusted
import ai.jarvis.app.automation.actions.str
import ai.jarvis.app.automation.policy.ActionTier
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL

/**
 * Outbound HTTP from the phone.
 *
 * This is the action most likely to be pointed somewhere it should not go, so
 * it is wrapped in three layers:
 *
 *  1. [SsrfGuard.check] on the literal URL — scheme allowlist, no embedded
 *     credentials, no loopback/private/link-local/metadata address in any of
 *     its spellings.
 *  2. A DNS re-check: every address the hostname resolves to is run through
 *     [SsrfGuard.isBlockedIp] before the socket is opened.
 *  3. Redirects are NOT followed automatically. Each `Location` is re-checked
 *     from step 1, at most 3 hops, so a public URL cannot bounce us onto
 *     169.254.169.254.
 *
 * The only exemption is the configured jarvis-core host in
 * [ActionEnv.jarvisServerHost] — the server we already trust.
 *
 * Residual risk, stated plainly: between our DNS check and the platform's own
 * resolution there is a rebinding window. Closing it needs connect-by-IP with
 * a Host override, which breaks TLS verification, so it is not done here. The
 * response body is treated as untrusted either way.
 */
object HttpRequest : JarvisAction {
    override val id = "http_request"
    override val tier = ActionTier.NOTIFY
    override val description = "Make an HTTP request to a public URL and return the response."
    override val paramsSchema = mapOf(
        "url" to "string: http(s) URL",
        "method" to "string: GET (default) | POST | PUT | PATCH | DELETE | HEAD",
        "headers" to "object (optional): header name -> value",
        "body" to "string (optional): request body",
        "content_type" to "string (optional): defaults to application/json when a body is given",
        "max_bytes" to "int: truncate the response after this many bytes (default 262144)"
    )
    override val capability = "http"
    override val timeoutMs = 40_000L

    /** The response body is the canonical piece of attacker-controlled text. */
    override val untrustedOutput = true

    private val METHODS = setOf("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
    private const val MAX_REDIRECTS = 3
    private const val DEFAULT_MAX_BYTES = 256 * 1024

    /**
     * A GET reads; a POST/PUT/PATCH/DELETE writes to someone else's system —
     * which is "a web action that submits a form", i.e. Tier 3. Raise only.
     */
    override fun tierFor(params: JSONObject): ActionTier {
        val method = (params.str("method") ?: "GET").uppercase()
        return if (method == "GET" || method == "HEAD") ActionTier.NOTIFY else ActionTier.CONFIRM
    }

    override suspend fun execute(ctx: Context, params: JSONObject): ActionResult =
        withContext(Dispatchers.IO) {
            val method = (params.str("method") ?: "GET").uppercase()
            if (method !in METHODS) {
                return@withContext ActionResult.error("method must be one of $METHODS")
            }
            var url = params.str("url") ?: return@withContext ActionResult.error("url is required")
            val maxBytes = params.intOr("max_bytes", DEFAULT_MAX_BYTES).coerceIn(1, 2 * 1024 * 1024)
            // Normalised and blank-safe: an unset or empty server host must
            // produce an EMPTY allowlist, never a set containing "".
            val allowed = ActionEnv.allowedHttpHosts()

            var hops = 0
            while (hops <= MAX_REDIRECTS) {
                val check = SsrfGuard.check(url, allowed)
                if (!check.allowed) {
                    return@withContext ActionResult.error("blocked: ${check.reason}")
                }
                if (check.needsDnsCheck) {
                    val host = check.host.orEmpty()
                    val addresses = try {
                        InetAddress.getAllByName(host)
                    } catch (e: Exception) {
                        return@withContext ActionResult.error("could not resolve $host")
                    }
                    for (address in addresses) {
                        val ip = address.hostAddress ?: continue
                        if (SsrfGuard.isBlockedIp(ip)) {
                            return@withContext ActionResult.error(
                                "blocked: $host resolves to $ip, which is a private/loopback/metadata address"
                            )
                        }
                    }
                }

                val connection = try {
                    (URL(url).openConnection() as HttpURLConnection)
                } catch (e: Exception) {
                    return@withContext ActionResult.error("could not open $url: ${e.message ?: "unknown"}")
                }

                try {
                    connection.requestMethod = method
                    connection.connectTimeout = 10_000
                    connection.readTimeout = 20_000
                    // Redirects are re-checked by hand; never let the stack follow one.
                    connection.instanceFollowRedirects = false
                    connection.setRequestProperty("Accept-Encoding", "identity")

                    params.optJSONObject("headers")?.let { headers ->
                        val keys = headers.keys()
                        while (keys.hasNext()) {
                            val k = keys.next()
                            val v = headers.opt(k)?.toString() ?: continue
                            if (k.any { it == '\r' || it == '\n' } || v.any { it == '\r' || it == '\n' }) {
                                return@withContext ActionResult.error("header injection attempt rejected")
                            }
                            runCatching { connection.setRequestProperty(k, v) }
                        }
                    }

                    val body = params.str("body")
                    if (body != null && method != "GET" && method != "HEAD") {
                        connection.doOutput = true
                        connection.setRequestProperty(
                            "Content-Type",
                            params.str("content_type") ?: "application/json"
                        )
                        connection.outputStream.use { it.write(body.toByteArray()) }
                    }

                    val status = connection.responseCode
                    if (status in 300..399) {
                        val location = connection.getHeaderField("Location")
                        if (!location.isNullOrBlank()) {
                            // Loop back to the top so the new URL goes through
                            // the full guard again. The `finally` disconnects.
                            url = absolutize(url, location)
                            hops++
                            continue
                        }
                    }

                    val stream: InputStream? =
                        if (status in 200..299) connection.inputStream else connection.errorStream
                    val bytes = stream?.use { readAtMost(it, maxBytes) } ?: ByteArray(0)

                    val responseHeaders = JSONObject()
                    for ((key, values) in connection.headerFields) {
                        if (key == null) continue
                        responseHeaders.put(key.lowercase(), values.joinToString(", "))
                    }

                    return@withContext ActionResult.ok(
                        json(
                            "status" to status,
                            "url" to url,
                            "headers" to responseHeaders,
                            "body" to String(bytes),
                            "bytes" to bytes.size,
                            "truncated" to (bytes.size >= maxBytes),
                            "redirects" to hops
                        ).markUntrusted()
                    )
                } catch (e: Exception) {
                    return@withContext ActionResult.error(
                        "request failed: ${e.message ?: e.javaClass.simpleName}"
                    )
                } finally {
                    runCatching { connection.disconnect() }
                }
            }
            ActionResult.error("too many redirects (more than $MAX_REDIRECTS)")
        }

    /** Resolve a possibly-relative Location header against the current URL. */
    private fun absolutize(base: String, location: String): String = try {
        URL(URL(base), location).toString()
    } catch (e: Exception) {
        location
    }

    private fun readAtMost(stream: InputStream, max: Int): ByteArray {
        val buffer = ByteArray(8 * 1024)
        val out = java.io.ByteArrayOutputStream()
        while (out.size() < max) {
            val n = stream.read(buffer, 0, minOf(buffer.size, max - out.size()))
            if (n <= 0) break
            out.write(buffer, 0, n)
        }
        return out.toByteArray()
    }
}
