package ai.jarvis.app.automation.actions

import java.net.URI

/**
 * PURE LOGIC — no Android imports. Unit-testable on a plain JVM.
 *
 * `http_request` exists so Jarvis can fetch a public page or poke a public API.
 * It must not become a proxy into the phone's own loopback, the local network,
 * or a cloud metadata endpoint, because the thing choosing the URL is an LLM
 * that reads attacker-controlled text.
 *
 * Two layers, both required:
 *
 *  1. [check] — literal inspection of the URL. Blocks non-HTTP schemes,
 *     embedded credentials, and any host that is *written* as a loopback,
 *     private, link-local, CGNAT, multicast or metadata address, including the
 *     decimal (`http://2130706433`), octal (`0177.0.0.1`) and IPv4-mapped-IPv6
 *     (`[::ffff:127.0.0.1]`) spellings.
 *  2. [isBlockedIp] — re-checked by the caller against every address the host
 *     actually resolves to, which is the only defence against a DNS name that
 *     points at 127.0.0.1.
 *
 * The single exemption is the configured jarvis-core host: that is the server
 * we already trust and talk to over the WebSocket, so it stays reachable even
 * though it lives on the LAN.
 */
object SsrfGuard {

    data class Check(
        val allowed: Boolean,
        val reason: String? = null,
        val scheme: String? = null,
        val host: String? = null,
        val port: Int = -1,
        /** True when the host matched the jarvis-core allowlist. */
        val exempt: Boolean = false,
        /** True when [host] is a name, so the caller must resolve and re-check. */
        val needsDnsCheck: Boolean = false
    )

    /**
     * @param rawUrl the URL as given by the server.
     * @param allowedHosts hosts exempt from the private-range block — normally
     *   just the configured jarvis-core host. Compared case-insensitively.
     */
    fun check(rawUrl: String?, allowedHosts: Set<String> = emptySet()): Check {
        val text = rawUrl?.trim().orEmpty()
        if (text.isEmpty()) return Check(false, "url is required")
        if (text.any { it.isISOControl() }) return Check(false, "url contains control characters")

        val uri = try {
            URI(text)
        } catch (t: Exception) {
            return Check(false, "malformed url")
        }

        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") {
            return Check(false, "only http and https are allowed (got ${scheme ?: "no scheme"})")
        }
        if (uri.userInfo != null) {
            return Check(false, "credentials in the url are not allowed")
        }

        val rawHost = (uri.host ?: return Check(false, "url has no host"))
            .trim()
            .removeSurrounding("[", "]")
            .trimEnd('.')
            .lowercase()
        if (rawHost.isEmpty()) return Check(false, "url has no host")

        val port = if (uri.port > 0) uri.port else if (scheme == "https") 443 else 80

        val exemptSet = allowedHosts.asSequence()
            .map { it.trim().trimEnd('.').lowercase() }
            .filter { it.isNotEmpty() }
            .toSet()
        if (rawHost in exemptSet) {
            return Check(true, null, scheme, rawHost, port, exempt = true, needsDnsCheck = false)
        }

        if (isBlockedHostName(rawHost)) {
            return Check(false, "host $rawHost is blocked (loopback/metadata name)", scheme, rawHost, port)
        }
        if (isIpLiteral(rawHost)) {
            if (isBlockedIp(rawHost)) {
                return Check(false, "address $rawHost is blocked (private/loopback/link-local/metadata)", scheme, rawHost, port)
            }
            return Check(true, null, scheme, rawHost, port, exempt = false, needsDnsCheck = false)
        }
        // A name we do not recognise: allowed only after the caller resolves it
        // and runs every resulting address through isBlockedIp.
        return Check(true, null, scheme, rawHost, port, exempt = false, needsDnsCheck = true)
    }

    /**
     * True when [rawUrl] names a real host that [check] refuses — loopback, the
     * LAN, link-local, CGNAT, a cloud metadata endpoint.
     *
     * Separate from [check] because two callers want two different answers
     * about the same fact. `http_request` refuses to make the request at all.
     * `open_url` hands the URL to the browser, which is a different act with a
     * different risk: the browser is on the user's LAN and carries the user's
     * cookies, so a private target is not forbidden, it is Tier 3 — shown to
     * the user with the URL in front of them.
     *
     * A malformed URL, a missing scheme or embedded credentials give no host,
     * so this is false for them: they are rejected on their own merits rather
     * than turned into a pointless consent prompt.
     */
    fun isInsideTrustBoundary(rawUrl: String?, allowedHosts: Set<String> = emptySet()): Boolean {
        val result = check(rawUrl, allowedHosts)
        return !result.allowed && result.host != null
    }

    /** Names that never legitimately appear in an LLM-chosen URL. */
    fun isBlockedHostName(host: String): Boolean {
        val h = host.trimEnd('.').lowercase()
        if (h == "localhost" || h.endsWith(".localhost")) return true
        if (h == "ip6-localhost" || h == "ip6-loopback") return true
        return h in METADATA_NAMES
    }

    private val METADATA_NAMES = setOf(
        "metadata", "metadata.google.internal", "metadata.goog",
        "instance-data", "instance-data.ec2.internal",
        "metadata.azure.com", "169.254.169.254.nip.io"
    )

    /** True when the string is written as an IPv4 or IPv6 literal in any spelling. */
    fun isIpLiteral(host: String): Boolean =
        parseIpv4(host) != null || looksLikeIpv6(host)

    /**
     * True when this address must never be contacted. Call it on the literal
     * host AND on every address the host resolves to.
     */
    fun isBlockedIp(address: String): Boolean {
        val v4 = parseIpv4(address)
        if (v4 != null) return isBlockedIpv4(v4)
        if (looksLikeIpv6(address)) return isBlockedIpv6(address)
        // Not an address at all — not this function's business.
        return false
    }

    // --- IPv4 ---------------------------------------------------------------

    /**
     * Accepts dotted-quad plus the legacy spellings `inet_aton` still honours
     * and that libc-backed resolvers therefore accept: a bare 32-bit decimal,
     * octal segments (`0177`) and hex segments (`0x7f`). Returns the address as
     * an unsigned 32-bit value, or null when it is not IPv4 at all.
     */
    fun parseIpv4(host: String): Long? {
        val h = host.trim().trimEnd('.')
        if (h.isEmpty()) return null
        val parts = h.split('.')
        if (parts.size > 4) return null
        val values = ArrayList<Long>(parts.size)
        for (p in parts) {
            val v = parseIpv4Part(p) ?: return null
            values.add(v)
        }
        return when (values.size) {
            1 -> values[0].takeIf { it <= 0xFFFFFFFFL }
            2 -> if (values[0] <= 0xFF && values[1] <= 0xFFFFFF) (values[0] shl 24) or values[1] else null
            3 -> if (values[0] <= 0xFF && values[1] <= 0xFF && values[2] <= 0xFFFF) {
                (values[0] shl 24) or (values[1] shl 16) or values[2]
            } else null
            4 -> if (values.all { it <= 0xFF }) {
                (values[0] shl 24) or (values[1] shl 16) or (values[2] shl 8) or values[3]
            } else null
            else -> null
        }
    }

    private fun parseIpv4Part(part: String): Long? {
        // Long.toLong() throws on anything wider than 64 bits, and the callers
        // bound every value, so the only job of this length cap is to stop
        // pathological input from being parsed at all.
        if (part.isEmpty() || part.length > 20) return null
        return try {
            when {
                part.startsWith("0x") || part.startsWith("0X") ->
                    part.substring(2).ifEmpty { return null }.toLong(16)
                part.length > 1 && part[0] == '0' -> part.substring(1).toLong(8)
                else -> part.toLong(10)
            }.takeIf { it >= 0 }
        } catch (t: NumberFormatException) {
            null
        }
    }

    private fun isBlockedIpv4(addr: Long): Boolean {
        fun inNet(network: String, prefix: Int): Boolean {
            val net = parseIpv4(network) ?: return false
            val mask = if (prefix == 0) 0L else (0xFFFFFFFFL shl (32 - prefix)) and 0xFFFFFFFFL
            return (addr and mask) == (net and mask)
        }
        return inNet("0.0.0.0", 8) ||        // "this network"
            inNet("10.0.0.0", 8) ||          // RFC1918
            inNet("100.64.0.0", 10) ||       // CGNAT
            inNet("127.0.0.0", 8) ||         // loopback
            inNet("169.254.0.0", 16) ||      // link-local, incl. 169.254.169.254 metadata
            inNet("172.16.0.0", 12) ||       // RFC1918
            inNet("192.0.0.0", 24) ||        // IETF protocol assignments
            inNet("192.0.2.0", 24) ||        // TEST-NET-1
            inNet("192.168.0.0", 16) ||      // RFC1918
            inNet("198.18.0.0", 15) ||       // benchmarking
            inNet("224.0.0.0", 4) ||         // multicast
            inNet("240.0.0.0", 4)            // reserved + 255.255.255.255
    }

    // --- IPv6 ---------------------------------------------------------------

    private fun looksLikeIpv6(host: String): Boolean {
        val h = host.removeSurrounding("[", "]")
        if (!h.contains(':')) return false
        // Only hex digits, colons, dots (v4-mapped) and a zone id.
        val body = h.substringBefore('%')
        return body.isNotEmpty() && body.all { it == ':' || it == '.' || it.isDigit() || it.lowercaseChar() in 'a'..'f' }
    }

    /** Expand to 8 groups; null when it is not parseable as IPv6. */
    fun expandIpv6(host: String): IntArray? {
        var h = host.removeSurrounding("[", "]").substringBefore('%').lowercase()
        if (h.isEmpty()) return null

        // Trailing IPv4 form: ::ffff:127.0.0.1
        var tail: IntArray? = null
        val lastColon = h.lastIndexOf(':')
        if (h.contains('.')) {
            if (lastColon < 0) return null
            val v4 = parseIpv4(h.substring(lastColon + 1)) ?: return null
            tail = intArrayOf(((v4 shr 16) and 0xFFFF).toInt(), (v4 and 0xFFFF).toInt())
            h = h.substring(0, lastColon + 1) + "0:0"
        }

        val doubleColon = h.indexOf("::")
        val groups: MutableList<Int> = ArrayList(8)
        fun parseSide(side: String): List<Int>? {
            if (side.isEmpty()) return emptyList()
            val out = ArrayList<Int>()
            for (g in side.split(':')) {
                if (g.isEmpty() || g.length > 4) return null
                out += g.toIntOrNull(16) ?: return null
            }
            return out
        }
        if (doubleColon >= 0) {
            if (h.indexOf("::", doubleColon + 1) >= 0) return null
            val left = parseSide(h.substring(0, doubleColon)) ?: return null
            val right = parseSide(h.substring(doubleColon + 2)) ?: return null
            val fill = 8 - left.size - right.size
            if (fill < 0) return null
            groups += left
            repeat(fill) { groups += 0 }
            groups += right
        } else {
            val all = parseSide(h) ?: return null
            if (all.size != 8) return null
            groups += all
        }
        if (groups.size != 8) return null
        if (tail != null) {
            groups[6] = tail[0]
            groups[7] = tail[1]
        }
        return groups.toIntArray()
    }

    private fun isBlockedIpv6(host: String): Boolean {
        val g = expandIpv6(host) ?: return true // unparseable v6 => fail closed
        // ::  (unspecified) and ::1 (loopback)
        if (g.take(7).all { it == 0 } && (g[7] == 0 || g[7] == 1)) return true
        // IPv4-mapped ::ffff:a.b.c.d and IPv4-compatible ::a.b.c.d
        if (g.take(5).all { it == 0 } && (g[5] == 0xffff || g[5] == 0)) {
            val v4 = (g[6].toLong() shl 16) or g[7].toLong()
            if (isBlockedIpv4(v4)) return true
        }
        val first = g[0]
        if ((first and 0xFFC0) == 0xFE80) return true // fe80::/10 link-local
        if ((first and 0xFE00) == 0xFC00) return true // fc00::/7 unique local
        if ((first and 0xFF00) == 0xFF00) return true // ff00::/8 multicast
        if (first == 0x0064 && g[1] == 0xff9b) return true // 64:ff9b::/96 NAT64
        // fd00:ec2::254 (EC2 IMDSv2 over IPv6) is inside fc00::/7 already.
        return false
    }
}
