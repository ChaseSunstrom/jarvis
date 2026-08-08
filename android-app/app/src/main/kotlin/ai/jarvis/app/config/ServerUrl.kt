package ai.jarvis.app.config

import java.net.URI

/**
 * Server URL parsing and origin comparison.
 *
 * Deliberately free of Android imports: this is the logic that decides which
 * origins the management WebView is allowed to talk to, so it should be
 * exercisable by a plain JVM unit test rather than only on a device.
 *
 * "Origin" here means the usual scheme/host/port triple, with the default port
 * filled in, compared case-insensitively on scheme and host.
 */
data class Origin(val scheme: String, val host: String, val port: Int) {

    override fun toString(): String = "$scheme://$host:$port"

    /** True when [url] parses to exactly this origin. */
    fun matches(url: String): Boolean = ServerUrl.originOf(url) == this
}

object ServerUrl {

    /** Result of [check] — a normalised URL plus at most one problem to show. */
    data class Check(
        val normalized: String,
        /** Fatal: the URL cannot be used. Null when usable. */
        val error: String? = null,
        /** Usable, but the user should know something. */
        val warning: String? = null,
    ) {
        val isValid: Boolean get() = error == null
    }

    private val DEFAULT_PORTS = mapOf(
        "http" to 80,
        "https" to 443,
        "ws" to 80,
        "wss" to 443,
    )

    /** Local/private suffixes that the platform's cleartext policy tolerates. */
    private val PRIVATE_SUFFIXES = listOf(".local", ".lan", ".home.arpa", ".internal", ".home")

    /** Trim whitespace and trailing slashes. Does not add a scheme. */
    fun normalize(raw: String): String = raw.trim().trimEnd('/')

    /**
     * Parse [raw] into an [Origin], or null if it is not a usable absolute
     * http/https/ws/wss URL. A missing or unparsable host is null, never a
     * guess — callers use this to decide what to let through.
     */
    fun originOf(raw: String): Origin? {
        val text = normalize(raw)
        if (text.isEmpty()) return null
        // URI.create wraps URISyntaxException in IllegalArgumentException, so
        // one catch covers every way a hand-typed URL can be malformed.
        val uri = try {
            URI.create(text)
        } catch (e: IllegalArgumentException) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        val port = if (uri.port >= 0) uri.port else DEFAULT_PORTS[scheme] ?: return null
        val host = uri.host?.lowercase() ?: return null
        if (host.isEmpty()) return null
        return Origin(scheme, host, port)
    }

    /** True when both URLs resolve to the same scheme/host/port. */
    fun sameOrigin(a: String, b: String): Boolean {
        val oa = originOf(a) ?: return false
        val ob = originOf(b) ?: return false
        return oa == ob
    }

    /**
     * The WebSocket URL for a given base URL: http -> ws, https -> wss, with
     * [path] appended. Returns null when [base] is not a usable URL, so a
     * caller can't accidentally dial `null/api/websocket`.
     */
    fun websocketUrl(base: String, path: String = "/api/websocket"): String? {
        val origin = originOf(base) ?: return null
        val scheme = when (origin.scheme) {
            "http", "ws" -> "ws"
            "https", "wss" -> "wss"
            else -> return null
        }
        val authority = hostPort(origin, scheme)
        val suffix = if (path.startsWith("/")) path else "/$path"
        return "$scheme://$authority${pathPrefixOf(base)}$suffix"
    }

    /**
     * True for hosts that only exist on a LAN, a VPN, or the device itself.
     * These are the only places plain HTTP is acceptable, and the only ones
     * res/xml/network_security_config.xml permits cleartext for.
     */
    fun isPrivateHost(host: String): Boolean {
        val h = host.lowercase().trim('[', ']')
        if (h == "localhost" || h.startsWith("127.")) return true
        if (h == "::1") return true
        if (PRIVATE_SUFFIXES.any { h.endsWith(it) }) return true
        // IPv6 unique-local (fc00::/7) and link-local (fe80::/10).
        if (h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe8")) {
            if (h.contains(':')) return true
        }
        val parts = h.split('.')
        val octets = parts.mapNotNull { it.toIntOrNull() }
        if (parts.size == 4 && octets.size == 4 && octets.all { it in 0..255 }) {
            val a = octets[0]
            val b = octets[1]
            return when {
                a == 10 -> true
                a == 192 && b == 168 -> true
                a == 172 && b in 16..31 -> true
                a == 169 && b == 254 -> true
                else -> false
            }
        }
        return false
    }

    /**
     * Validate what the user typed in Settings. Adds a scheme when it is
     * missing (assuming http, since a self-hosted LAN box usually has no cert)
     * and refuses cleartext to anything that isn't a private host — the
     * platform's network security config would refuse the connection anyway,
     * and failing here says why.
     */
    fun check(raw: String): Check {
        val trimmed = normalize(raw)
        if (trimmed.isEmpty()) {
            return Check("", error = "Server URL is required.")
        }

        val hadScheme = trimmed.contains("://")
        val candidate = if (hadScheme) trimmed else "http://$trimmed"
        val origin = originOf(candidate)
            ?: return Check(trimmed, error = "That is not a valid URL. Expected something like http://192.168.2.10:8123")

        if (origin.scheme != "http" && origin.scheme != "https") {
            return Check(trimmed, error = "Use http:// or https:// — got ${origin.scheme}://")
        }

        if (origin.scheme == "http" && !isPrivateHost(origin.host)) {
            return Check(
                candidate,
                error = "Plain HTTP is only allowed to LAN or VPN addresses. " +
                    "Use https:// for ${origin.host}, or reach it over WireGuard."
            )
        }

        val warning = when {
            !hadScheme -> "No scheme given, assuming http:// — edit it if your server uses TLS."
            origin.scheme == "http" ->
                "Cleartext to ${origin.host}. Fine over LAN/WireGuard; the host must be listed " +
                    "in res/xml/network_security_config.xml or the connection is refused."
            else -> null
        }
        return Check(candidate, warning = warning)
    }

    // --- internals ---------------------------------------------------------

    private fun hostPort(origin: Origin, wsScheme: String): String {
        val isDefault = (wsScheme == "ws" && origin.port == 80) ||
            (wsScheme == "wss" && origin.port == 443)
        val host = if (origin.host.contains(':')) "[${origin.host}]" else origin.host
        return if (isDefault) host else "$host:${origin.port}"
    }

    /** Any path the user included in the base URL (e.g. a reverse-proxy prefix). */
    private fun pathPrefixOf(base: String): String {
        val uri = try {
            URI.create(normalize(base))
        } catch (e: IllegalArgumentException) {
            return ""
        }
        return (uri.path ?: "").trimEnd('/')
    }
}
