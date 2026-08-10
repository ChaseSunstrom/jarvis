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
 *    *relay* to jarvis-core. For the browser — which cannot set headers on a
 *    WebSocket — it injects the server-held admin token and swallows the
 *    handshake, which is why that token never reaches the page. For a client
 *    that presents its own bearer token, it passes the token through and
 *    jarvis-core validates it, so the handshake looks exactly like talking to
 *    jarvis-core directly.
 *
 * That second mode is what makes one URL enough. The app presents its token on
 * the upgrade, so the two kinds now differ ONLY in the path, and pointing the
 * app at the console works for voice as well as for the management page.
 *
 * Before this the app assumed jarvis-core unconditionally, which broke in two
 * independent ways against the console URL: it dialled a path that is not
 * there, and — even had it connected — it only started a turn inside its
 * `auth_ok` branch, which the relay was eating. The symptom was that voice
 * worked in the management WebView (a web page talking to its own relay) and
 * nowhere else, with an inert orb and no error, because a pipeline that never
 * reaches LISTENING gates the microphone off.
 *
 * The kind is still *discovered* rather than assumed, because the path differs.
 * This file is the pure half — no Android, no network — so the rule can be
 * tested on the JVM and mirrored in `android-app/tools/server_endpoint_test.py`.
 */
enum class ServerKind(
    /** Where the WebSocket lives, appended to the base URL. */
    val wsPath: String,
) {
    /** jarvis-core, spoken to directly. */
    CORE("/api/websocket"),

    /**
     * jarvis-web's relay.
     *
     * It used to authenticate on the client's behalf unconditionally, which is
     * why this end once had to skip the handshake here. It no longer does: a
     * client that presents a bearer token is passed straight through to
     * jarvis-core, which validates it. So the handshake is now the same on both
     * kinds and only the path differs — which is the whole point, because it
     * means one URL works whichever server is behind it.
     */
    RELAY("/ws"),
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
     * RELAY first. Both endpoints now authenticate identically, so the order is
     * about which is more likely: the console is the URL a person types,
     * because it is the one with a web page on it.
     */
    fun candidates(known: ServerKind?): List<ServerKind> = when (known) {
        null -> listOf(ServerKind.RELAY, ServerKind.CORE)
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
