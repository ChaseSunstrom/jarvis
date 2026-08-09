package ai.jarvis.app.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [ServerUrl] decides which origins the management WebView may talk to and
 * whether a URL the user typed is allowed to be cleartext. Both of those are
 * security decisions, so they live in a class with no Android imports and get
 * tested here on a plain JVM.
 */
class ServerUrlTest {

    @Test
    fun normalizeTrimsWhitespaceAndTrailingSlashes() {
        assertEquals("http://a.local:81", ServerUrl.normalize("  http://a.local:81///  "))
        assertEquals("", ServerUrl.normalize("   "))
    }

    @Test
    fun originFillsDefaultPorts() {
        assertEquals(Origin("http", "jarvis.local", 80), ServerUrl.originOf("http://jarvis.local"))
        assertEquals(Origin("https", "jarvis.local", 443), ServerUrl.originOf("https://jarvis.local"))
        assertEquals(
            Origin("http", "192.168.2.10", 8123),
            ServerUrl.originOf("http://192.168.2.10:8123")
        )
    }

    @Test
    fun originLowercasesSchemeAndHost() {
        assertEquals(Origin("http", "jarvis.local", 80), ServerUrl.originOf("HTTP://Jarvis.Local"))
    }

    @Test
    fun originRejectsJunk() {
        assertNull(ServerUrl.originOf(""))
        assertNull(ServerUrl.originOf("jarvis.local:8123"))  // no scheme
        assertNull(ServerUrl.originOf("not a url"))
        assertNull(ServerUrl.originOf("javascript:alert(1)"))
        assertNull(ServerUrl.originOf("file:///etc/passwd"))
    }

    @Test
    fun sameOriginDistinguishesSchemeHostAndPort() {
        assertTrue(ServerUrl.sameOrigin("http://h:8123/a", "http://h:8123/b?c=1"))
        assertFalse(ServerUrl.sameOrigin("http://h:8123", "https://h:8123"))
        assertFalse(ServerUrl.sameOrigin("http://h:8123", "http://h:8124"))
        assertFalse(ServerUrl.sameOrigin("http://h:8123", "http://evil.example:8123"))
        // An unparsable URL is never "the same origin" as anything.
        assertFalse(ServerUrl.sameOrigin("http://h", "garbage"))
    }

    @Test
    fun originMatchesHelperAgrees() {
        val origin = ServerUrl.originOf("http://192.168.2.10:8123")!!
        assertTrue(origin.matches("http://192.168.2.10:8123/ui/index.html"))
        assertFalse(origin.matches("http://192.168.2.11:8123/"))
    }

    @Test
    fun privateHostsAreRecognised() {
        listOf(
            "localhost", "127.0.0.1", "10.1.2.3", "192.168.2.10",
            "172.16.0.1", "172.31.255.254", "169.254.1.1",
            "jarvis.local", "box.jarvis.lan", "srv.home.arpa", "::1", "fd00::1",
        ).forEach { assertTrue("expected private: $it", ServerUrl.isPrivateHost(it)) }
    }

    @Test
    fun publicHostsAreNotPrivate() {
        listOf(
            "example.com", "8.8.8.8", "172.32.0.1", "172.15.0.1",
            "193.168.2.10", "evil.example.org",
        ).forEach { assertFalse("expected public: $it", ServerUrl.isPrivateHost(it)) }
    }

    @Test
    fun checkRejectsEmpty() {
        assertFalse(ServerUrl.check("   ").isValid)
    }

    @Test
    fun checkAssumesHttpForBareLanAddress() {
        val result = ServerUrl.check("192.168.2.10:8123")
        assertTrue(result.error ?: "", result.isValid)
        assertEquals("http://192.168.2.10:8123", result.normalized)
        assertTrue(result.warning!!.contains("assuming http"))
    }

    @Test
    fun checkRefusesCleartextToPublicHosts() {
        val result = ServerUrl.check("http://example.com")
        assertFalse(result.isValid)
        assertTrue(result.error!!.contains("LAN"))
    }

    @Test
    fun checkAcceptsHttpsAnywhereWithoutWarning() {
        val result = ServerUrl.check("https://jarvis.example.com/")
        assertTrue(result.isValid)
        assertEquals("https://jarvis.example.com", result.normalized)
        assertNull(result.warning)
    }

    @Test
    fun checkWarnsAboutCleartextOnLan() {
        val result = ServerUrl.check("http://192.168.2.10:8123")
        assertTrue(result.isValid)
        assertTrue(result.warning!!.contains("Cleartext"))
    }

    @Test
    fun checkRejectsNonHttpSchemes() {
        assertFalse(ServerUrl.check("ftp://jarvis.local").isValid)
        assertFalse(ServerUrl.check("javascript:alert(1)").isValid)
    }

    @Test
    fun websocketUrlUpgradesScheme() {
        assertEquals(
            "ws://192.168.2.10:8123/api/websocket",
            ServerUrl.websocketUrl("http://192.168.2.10:8123")
        )
        assertEquals(
            "wss://jarvis.example.com/api/websocket",
            ServerUrl.websocketUrl("https://jarvis.example.com")
        )
        assertEquals(
            "ws://jarvis.local/api/websocket",
            ServerUrl.websocketUrl("http://jarvis.local:80")
        )
    }

    @Test
    fun websocketUrlKeepsAReverseProxyPathPrefix() {
        assertEquals(
            "wss://proxy.example.com/jarvis/api/websocket",
            ServerUrl.websocketUrl("https://proxy.example.com/jarvis/")
        )
    }

    @Test
    fun websocketUrlIsNullForJunk() {
        assertNull(ServerUrl.websocketUrl("nonsense"))
        assertNull(ServerUrl.websocketUrl(""))
    }

    // --- resolveOnServer ----------------------------------------------------
    //
    // Regression cover for the token-exfiltration path: the pipeline's
    // `tts_output.url` used to be accepted verbatim whenever it started with
    // "http", and TtsPlayer then fetched it with `Authorization: Bearer <token>`
    // attached. A prompt-injected or compromised server could therefore name any
    // host and be handed the long-lived token.

    private val server = "http://192.168.2.10:8123"

    @Test
    fun resolveOnServerKeepsAbsolutePathsOnTheConfiguredOrigin() {
        assertEquals(
            "http://192.168.2.10:8123/api/tts_proxy/x.mp3",
            ServerUrl.resolveOnServer(server, "/api/tts_proxy/x.mp3")
        )
        assertEquals(
            "http://192.168.2.10:8123/api/tts_proxy/x.mp3",
            ServerUrl.resolveOnServer(server, "http://192.168.2.10:8123/api/tts_proxy/x.mp3")
        )
    }

    @Test
    fun resolveOnServerKeepsAReverseProxyPathPrefix() {
        assertEquals(
            "https://proxy.example.com/jarvis/api/tts_proxy/x.mp3",
            ServerUrl.resolveOnServer("https://proxy.example.com/jarvis/", "/api/tts_proxy/x.mp3")
        )
    }

    @Test
    fun resolveOnServerRefusesAnyOtherOrigin() {
        // The whole point: a different host, port or scheme is refused, and so
        // is a host that merely looks like ours.
        assertNull(ServerUrl.resolveOnServer(server, "http://evil.example/x.mp3"))
        assertNull(ServerUrl.resolveOnServer(server, "https://192.168.2.10:8123/x.mp3"))
        assertNull(ServerUrl.resolveOnServer(server, "http://192.168.2.10:9999/x.mp3"))
        assertNull(ServerUrl.resolveOnServer(server, "http://192.168.2.100:8123/x.mp3"))
        assertNull(ServerUrl.resolveOnServer("http://jarvis.lan", "http://evil-jarvis.lan/x.mp3"))
        assertNull(ServerUrl.resolveOnServer("http://jarvis.lan", "http://jarvis.lan.evil.com/x"))
    }

    @Test
    fun resolveOnServerRefusesSchemeRelativeUrls() {
        // `//evil.example/x.mp3` has no scheme, so a naive "does it start with
        // http" test treats it as relative and concatenates it onto the base.
        assertNull(ServerUrl.resolveOnServer(server, "//evil.example/x.mp3"))
    }

    @Test
    fun resolveOnServerRefusesNonHttpSchemesAndCredentials() {
        assertNull(ServerUrl.resolveOnServer(server, "file:///data/data/ai.jarvis.app/x"))
        assertNull(ServerUrl.resolveOnServer(server, "content://media/external/audio/1"))
        assertNull(ServerUrl.resolveOnServer(server, "javascript:alert(1)"))
        assertNull(ServerUrl.resolveOnServer(server, "http://user:pass@192.168.2.10:8123/x.mp3"))
    }

    @Test
    fun resolveOnServerRefusesJunkAndRelativePaths() {
        assertNull(ServerUrl.resolveOnServer(server, null))
        assertNull(ServerUrl.resolveOnServer(server, "   "))
        assertNull(ServerUrl.resolveOnServer(server, "x.mp3"))
        assertNull(ServerUrl.resolveOnServer(server, "/api/x\n.mp3"))
        assertNull(ServerUrl.resolveOnServer("", "/api/x.mp3"))
        assertNull(ServerUrl.resolveOnServer("nonsense", "/api/x.mp3"))
    }

    @Test
    fun resolveOnServerIsCaseInsensitiveOnTheOrigin() {
        assertEquals(
            "HTTP://JARVIS.LOCAL:8123/x.mp3",
            ServerUrl.resolveOnServer("http://jarvis.local:8123", "HTTP://JARVIS.LOCAL:8123/x.mp3")
        )
    }
}
