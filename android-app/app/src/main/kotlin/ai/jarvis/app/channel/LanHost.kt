package ai.jarvis.app.channel

import java.net.URI
import java.util.Locale

/**
 * PURE LOGIC — no Android imports, no org.json, no I/O.
 *
 * Decides whether a host is somewhere only *you* can reach (LAN, WireGuard, the
 * device itself) and therefore whether plain `http://` / `ws://` to it is
 * acceptable. Mirrored by `android-app/tools/channel_protocol_test.py`, which
 * is the executable spec — change one, change both.
 *
 * The direction of failure matters. Classifying a host as LAN *permits*
 * cleartext, so every ambiguous case resolves to [HostClass.PUBLIC] or
 * [HostClass.INVALID] and the connection is forced onto TLS. A host that is
 * genuinely on your LAN but written in a way this parser refuses costs the user
 * one edit in Settings; a public host that sneaks past this parser costs them
 * their bearer token.
 *
 * `ai.jarvis.app.config.ServerUrl.isPrivateHost` answers a similar question for
 * the Settings screen and the WebView. This one is deliberately separate and
 * stricter: it guards the socket that carries device commands, it is the copy
 * that has a cross-language test, and defence in depth is worth one duplicated
 * table of prefixes.
 */
enum class HostClass(
    /** True when the host can only be reached from a private network. */
    val lan: Boolean,
    val explanation: String
) {
    LOOPBACK(true, "the device itself"),
    PRIVATE_V4(true, "RFC1918 private address"),
    LINK_LOCAL_V4(true, "RFC3927 link-local address"),
    CGNAT_V4(true, "RFC6598 shared address space (WireGuard/Tailscale mesh)"),
    UNIQUE_LOCAL_V6(true, "RFC4193 unique-local address"),
    LINK_LOCAL_V6(true, "RFC4291 link-local address"),
    PRIVATE_NAME(true, "a name that only resolves on a local network"),
    PUBLIC(false, "a publicly routable host"),
    INVALID(false, "not a usable host");

    val isLan: Boolean get() = lan
}

object LanHost {

    /**
     * DNS suffixes reserved for, or conventionally used by, local networks.
     *
     * These are a *convention*, not a routing property: nothing stops a public
     * resolver answering for `evil.internal`. They are accepted because the
     * whole point of this app is reaching a box the user runs at home, and that
     * box is usually `jarvis.local`. If you do not trust your resolver, use an
     * IP literal or HTTPS.
     */
    private val PRIVATE_SUFFIXES = listOf(
        ".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet",
        ".localdomain", ".private"
    )

    /** Schemes this app will ever dial. */
    private val CLEARTEXT_SCHEMES = setOf("http", "ws")
    private val TLS_SCHEMES = setOf("https", "wss")

    /** True when [host] is only reachable from a private network. */
    fun isLanHost(host: String?): Boolean = classify(host).isLan

    /**
     * Classify a bare host: no scheme, no port, no path. Accepts IPv6 in
     * brackets and with a `%zone` suffix, since that is how they arrive from a
     * URL.
     */
    fun classify(host: String?): HostClass {
        val h = normalize(host) ?: return HostClass.INVALID
        if (h == "localhost") return HostClass.LOOPBACK

        classifyIpv6(h)?.let { return it }
        classifyIpv4(h)?.let { return it }

        // A name. Anything with a private suffix, or a single label with no
        // dot at all (`jarvis`, resolvable only by the local resolver).
        if (PRIVATE_SUFFIXES.any { h.endsWith(it) }) return HostClass.PRIVATE_NAME
        if (!h.contains('.')) return HostClass.PRIVATE_NAME
        return HostClass.PUBLIC
    }

    /** Lower-case, strip IPv6 brackets and any `%zone`, reject empty/garbage. */
    fun normalize(host: String?): String? {
        // Locale.ROOT, not the device locale. In a Turkish locale the default
        // `lowercase()` maps 'I' to 'ı', so "JARVIS.LOCAL" and OkHttp's own
        // (Locale.US) "jarvis.local" would stop comparing equal and the
        // per-command host pin would refuse a perfectly legitimate server.
        var h = host?.trim()?.lowercase(Locale.ROOT) ?: return null
        if (h.isEmpty()) return null
        if (h.startsWith("[") && h.endsWith("]")) h = h.substring(1, h.length - 1)
        h = h.substringBefore('%')
        h = h.trimEnd('.') // a fully-qualified "jarvis.lan." is the same host
        if (h.isEmpty()) return null
        // No spaces, no credentials, no path fragments — this must be a bare host.
        if (h.any { it.isWhitespace() } || h.contains('/') || h.contains('@')) return null
        return h
    }

    // --- IPv4 ---------------------------------------------------------------

    /**
     * Returns null when [h] is not an IPv4 literal *attempt* (so the caller
     * falls through to name handling), and [HostClass.INVALID] when it looks
     * like one but does not parse.
     *
     * Leading zeros are rejected outright: `0177.0.0.1` is 127.0.0.1 to
     * `inet_aton` and 177.0.0.1 to a naive parser, and a classifier that can be
     * made to disagree with the resolver is a bypass waiting to happen.
     */
    fun classifyIpv4(h: String): HostClass? {
        val parts = h.split('.')
        if (parts.size != 4) return null
        if (!parts.all { it.isNotEmpty() && it.all { c -> c in '0'..'9' } }) return null

        val octets = IntArray(4)
        for (i in 0..3) {
            val p = parts[i]
            if (p.length > 3) return HostClass.INVALID
            if (p.length > 1 && p[0] == '0') return HostClass.INVALID // octal-looking
            val v = p.toIntOrNull() ?: return HostClass.INVALID
            if (v > 255) return HostClass.INVALID
            octets[i] = v
        }

        val a = octets[0]
        val b = octets[1]
        return when {
            a == 0 -> HostClass.INVALID                 // 0.0.0.0/8, "this network"
            a == 127 -> HostClass.LOOPBACK              // 127.0.0.0/8
            a == 10 -> HostClass.PRIVATE_V4             // 10.0.0.0/8
            a == 172 && b in 16..31 -> HostClass.PRIVATE_V4   // 172.16.0.0/12
            a == 192 && b == 168 -> HostClass.PRIVATE_V4      // 192.168.0.0/16
            a == 169 && b == 254 -> HostClass.LINK_LOCAL_V4   // 169.254.0.0/16
            a == 100 && b in 64..127 -> HostClass.CGNAT_V4    // 100.64.0.0/10
            else -> HostClass.PUBLIC
        }
    }

    // --- IPv6 ---------------------------------------------------------------

    /** Null when [h] is not an IPv6 literal attempt. */
    fun classifyIpv6(h: String): HostClass? {
        if (!h.contains(':')) return null

        // ::, ::1, and the long-hand 0:0:0:0:0:0:0:1. The `endsWith` guard
        // matters: without it "1::" (= 0001::) would also reduce to "1".
        val bare = h.replace(":", "")
        if (bare.isEmpty()) return HostClass.INVALID           // "::" — unspecified
        if (bare.all { it == '0' }) return HostClass.INVALID
        if (h.endsWith(":1") && bare.trimStart('0') == "1") return HostClass.LOOPBACK

        // A dotted tail decides ONLY for the v4-mapped/compatible prefixes,
        // `::a.b.c.d` and `::ffff:a.b.c.d`, where those last 32 bits really are
        // an IPv4 address.
        //
        // Any other prefix is an ordinary IPv6 address that merely happens to be
        // written with a dotted tail, and reading the tail there is a cleartext
        // bypass: `2001:4860:4860::10.0.0.1` is globally routable, but the tail
        // `10.0.0.1` says "RFC1918", so `http://[2001:4860:4860::10.0.0.1]:8123`
        // used to classify as LAN and carry the bearer token in the clear.
        val tail = h.substringAfterLast(':')
        if (tail.contains('.')) {
            val prefix = h.substring(0, h.length - tail.length)
            if (isV4EmbeddingPrefix(prefix)) return classifyIpv4(tail) ?: HostClass.INVALID
            // else fall through: classify it as the IPv6 address it actually is.
        }

        val first = h.substringBefore(':')
        if (first.isEmpty()) return HostClass.PUBLIC           // starts with "::"
        if (first.length > 4) return HostClass.INVALID
        val value = first.toIntOrNull(16) ?: return HostClass.INVALID
        return when (value) {
            in 0xfc00..0xfdff -> HostClass.UNIQUE_LOCAL_V6     // fc00::/7
            in 0xfe80..0xfebf -> HostClass.LINK_LOCAL_V6       // fe80::/10
            else -> HostClass.PUBLIC
        }
    }

    /**
     * True for the two prefixes whose trailing dotted quad is an IPv4 address:
     * `::` (IPv4-compatible, deprecated) and `::ffff:` (IPv4-mapped), in either
     * the compressed or the long-hand `0:0:0:0:0:ffff:` spelling.
     *
     * [prefix] is everything up to and including the colon before the quad.
     */
    private fun isV4EmbeddingPrefix(prefix: String): Boolean {
        if (!prefix.endsWith(":")) return false
        val compressed = prefix.startsWith("::")
        val groups = prefix.split(':').filter { it.isNotEmpty() }
        // Compressed: "::" (no groups) or "::ffff:" (one). Long-hand: exactly the
        // five zero groups plus the ffff marker.
        if (compressed && groups.size > 1) return false
        if (!compressed && groups.size != 6) return false
        for ((i, group) in groups.withIndex()) {
            if (group.length > 4) return false
            val value = group.toIntOrNull(16) ?: return false
            if (value == 0) continue
            // The only non-zero group allowed is a trailing ffff.
            if (value != 0xffff || i != groups.size - 1) return false
        }
        return true
    }

    // --- transport policy ---------------------------------------------------

    /** The answer to "may I dial this URL?", with a reason fit for a log line. */
    data class Verdict(
        val allowed: Boolean,
        val reason: String,
        val host: String?,
        val hostClass: HostClass,
        val cleartext: Boolean
    )

    /**
     * The rule the socket enforces before it opens:
     *
     *  * `https://` / `wss://` — allowed to any host. TLS is the point.
     *  * `http://` / `ws://` — allowed ONLY to a [HostClass.isLan] host, or to
     *    a host the user has explicitly acknowledged in Settings.
     *  * anything else — refused.
     *
     * [acknowledgedCleartextHosts] is the user's own list, typed on this device.
     * It is never populated from the network, and in particular never from the
     * server: a server that could add itself to it would have defeated the rule.
     */
    fun checkUrl(url: String?, acknowledgedCleartextHosts: Set<String> = emptySet()): Verdict {
        val raw = url?.trim().orEmpty()
        if (raw.isEmpty()) {
            return Verdict(false, "no server URL configured", null, HostClass.INVALID, false)
        }
        val uri = try {
            URI.create(raw)
        } catch (e: IllegalArgumentException) {
            return Verdict(false, "server URL does not parse", null, HostClass.INVALID, false)
        }
        val scheme = uri.scheme?.lowercase(Locale.ROOT)
            ?: return Verdict(false, "server URL has no scheme", null, HostClass.INVALID, false)
        val host = normalize(uri.host)
            ?: return Verdict(false, "server URL has no usable host", null, HostClass.INVALID, false)
        val cls = classify(host)

        if (scheme in TLS_SCHEMES) {
            return Verdict(true, "TLS to $host", host, cls, cleartext = false)
        }
        if (scheme !in CLEARTEXT_SCHEMES) {
            return Verdict(false, "unsupported scheme $scheme://", host, cls, cleartext = false)
        }
        if (cls.isLan) {
            return Verdict(true, "cleartext to $host (${cls.explanation})", host, cls, cleartext = true)
        }
        if (acknowledgedCleartextHosts.any { normalize(it) == host }) {
            return Verdict(
                true,
                "cleartext to $host — allowed only because the user acknowledged it",
                host, cls, cleartext = true
            )
        }
        return Verdict(
            false,
            "refusing cleartext to $host (${cls.explanation}); use https:// or reach it over WireGuard",
            host, cls, cleartext = true
        )
    }
}
