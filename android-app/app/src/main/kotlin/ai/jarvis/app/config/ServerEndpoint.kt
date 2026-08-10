package ai.jarvis.app.config

import org.json.JSONObject

/**
 * Which of the two Jarvis servers is at the configured URL, and how to talk to it.
 *
 * There are two, they listen on different ports, and the natural thing for a
 * person to type into "Jarvis URL" is the one with the web page on it:
 *
 *  * **jarvis-core** (:8080) is the API. Its socket is `/api/websocket` and the
 *    client authenticates by sending `{"type":"auth","access_token":...}` in
 *    response to `auth_required`.
 *  * **jarvis-web** (:8199) is the console. Its socket is `/ws`, and it is a
 *    *relay*: the Node server holds the admin token, does the handshake with
 *    jarvis-core itself, and **swallows** the `auth_required`/`auth_ok` frames
 *    so the browser never sees them or the token.
 *
 * Before this existed the app assumed jarvis-core unconditionally, which broke
 * in two independent ways against the console URL: it dialled a path that is
 * not there, and — even had it connected — it only starts a turn inside its
 * `auth_ok` branch, and on the relay that frame never arrives. The symptom was
 * that voice worked in the management WebView (a web page talking to its own
 * relay) and nowhere else, with an inert orb and no error, because a pipeline
 * that never reaches LISTENING gates the microphone off.
 *
 * So the kind is *discovered* rather than assumed. This file is the pure half —
 * no Android, no network — so the discrimination rule can be tested on the JVM
 * and mirrored in `android-app/tools/server_endpoint_test.py`.
 */
enum class ServerKind(
    /** Where the WebSocket lives, appended to the base URL. */
    val wsPath: String,
    /** True when this end must send the auth frame itself. */
    val clientAuthenticates: Boolean,
) {
    /** jarvis-core, spoken to directly. */
    CORE("/api/websocket", true),

    /** jarvis-web's relay, which authenticates on our behalf. */
    RELAY("/ws", false),
}

object ServerEndpoint {

    /**
     * The unauthenticated endpoint whose answer tells the two servers apart.
     *
     * jarvis-web serves this as public client configuration. jarvis-core has a
     * route at the same path but requires a bearer token and answers something
     * structurally different, which is what [kindFromProbe] keys on.
     */
    fun probeUrl(base: String): String? {
        val normalized = ServerUrl.normalize(base)
        if (ServerUrl.originOf(normalized) == null) return null
        return "$normalized/api/config"
    }

    /**
     * Read the probe's answer. Null means "cannot tell" — the caller should keep
     * whatever it already believed rather than guess.
     *
     * Keys, not status codes, decide it. A 401 from jarvis-core and a 200 from
     * jarvis-web is the common case, but a reverse proxy that rewrites status
     * codes, or a future jarvis-core that opens `/api/config` up, would make a
     * status-based rule quietly wrong. `backendUrlVar` is jarvis-web's alone:
     * it is the *name of the environment variable* the relay read its backend
     * URL from, which is a thing only the relay has.
     */
    fun kindFromProbe(status: Int, body: String?): ServerKind? {
        if (body != null && body.isNotBlank()) {
            val json = try {
                JSONObject(body)
            } catch (e: Exception) {
                null
            }
            if (json != null) {
                if (json.has("backendUrlVar") || json.has("tokenConfigured")) return ServerKind.RELAY
                // jarvis-core answers the Home Assistant config shape. `version`
                // alone is too common to key on; `components` is the giveaway.
                if (json.has("components") || json.has("ha_version")) return ServerKind.CORE
            }
        }
        // An authenticated-only route that refused us is jarvis-core: the relay's
        // copy is public and would have answered.
        if (status == 401 || status == 403) return ServerKind.CORE
        return null
    }

    /** The WebSocket URL for [base], given which server is there. */
    fun websocketUrl(base: String, kind: ServerKind): String? =
        ServerUrl.websocketUrl(base, kind.wsPath)

    /**
     * What to try, in order, when the kind is not known yet.
     *
     * CORE first: it is the endpoint that needs the token, so trying it first
     * means a misconfigured token fails loudly against the server that checks
     * it rather than silently succeeding against a relay that does not.
     */
    fun candidates(known: ServerKind?): List<ServerKind> = when (known) {
        null -> listOf(ServerKind.CORE, ServerKind.RELAY)
        else -> listOf(known) + ServerKind.entries.filter { it != known }
    }

    /**
     * Whether a management WebView can show a console at this URL.
     *
     * Only the relay serves one. Pointed at jarvis-core the WebView would load
     * the API's JSON index, which looks like a broken page rather than like a
     * URL pointing at the wrong one of two servers.
     */
    fun servesConsole(kind: ServerKind?): Boolean = kind == ServerKind.RELAY
}
