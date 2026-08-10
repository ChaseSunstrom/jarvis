package ai.jarvis.app.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Telling jarvis-core and the jarvis-web console apart.
 *
 * This is the decision that used to be made by assumption, and the assumption
 * was why voice worked only inside the management WebView: the console's socket
 * is `/ws` and it relays — swallowing the auth handshake — while jarvis-core's
 * is `/api/websocket` and expects the client to authenticate. Getting it wrong
 * fails twice over, and silently.
 *
 * `android-app/tools/server_endpoint_test.py` mirrors these rules so they can
 * be checked without a JVM; this is the copy the build compiles.
 */
class ServerEndpointTest {

    private val relayBody = """
        {"pipeline":"Jarvis","backend":"core","backendUrl":"http://127.0.0.1:8080",
         "backendUrlVar":"JARVIS_URL","tokenConfigured":true}
    """.trimIndent()

    private val coreBody = """
        {"version":"0.1.0","ha_version":"jarvis-0.1.0","components":["light"]}
    """.trimIndent()

    @Test
    fun `the console is recognised by a key only it has`() {
        assertEquals(ServerKind.RELAY, ServerEndpoint.kindFromProbe(200, relayBody))
    }

    @Test
    fun `jarvis-core is recognised by its own config shape`() {
        assertEquals(ServerKind.CORE, ServerEndpoint.kindFromProbe(200, coreBody))
    }

    @Test
    fun `a route that refused us is jarvis-core, whose copy needs a token`() {
        assertEquals(ServerKind.CORE, ServerEndpoint.kindFromProbe(401, ""))
        assertEquals(ServerKind.CORE, ServerEndpoint.kindFromProbe(403, null))
    }

    @Test
    fun `the body decides, not the status code`() {
        // A reverse proxy that rewrites statuses must not flip the answer.
        assertEquals(ServerKind.RELAY, ServerEndpoint.kindFromProbe(401, relayBody))
        assertEquals(ServerKind.CORE, ServerEndpoint.kindFromProbe(200, coreBody))
    }

    @Test
    fun `something that is not Jarvis leaves the answer unknown`() {
        // Null, not a guess: the caller keeps believing whatever it already did
        // rather than dialling a path on the strength of a captive portal.
        assertNull(ServerEndpoint.kindFromProbe(200, """{"hello":"world"}"""))
        assertNull(ServerEndpoint.kindFromProbe(200, "<html>sign in</html>"))
        assertNull(ServerEndpoint.kindFromProbe(200, ""))
        assertNull(ServerEndpoint.kindFromProbe(404, null))
        // A bare version is too common a field to claim jarvis-core from.
        assertNull(ServerEndpoint.kindFromProbe(200, """{"version":"9"}"""))
    }

    @Test
    fun `the two kinds dial different paths and authenticate differently`() {
        assertEquals("/api/websocket", ServerKind.CORE.wsPath)
        assertEquals("/ws", ServerKind.RELAY.wsPath)
        assertTrue(ServerKind.CORE.clientAuthenticates)
        // The relay eats auth_ok. A client that waits for it there hangs
        // forever, which is the original bug.
        assertFalse(ServerKind.RELAY.clientAuthenticates)
    }

    @Test
    fun `websocket urls carry scheme, port and any proxy path prefix`() {
        assertEquals(
            "ws://192.168.2.10:8080/api/websocket",
            ServerEndpoint.websocketUrl("http://192.168.2.10:8080", ServerKind.CORE)
        )
        assertEquals(
            "wss://jarvis.example.test/ws",
            ServerEndpoint.websocketUrl("https://jarvis.example.test", ServerKind.RELAY)
        )
        assertEquals(
            "ws://box.lan:8199/jarvis/ws",
            ServerEndpoint.websocketUrl("http://box.lan:8199/jarvis", ServerKind.RELAY)
        )
        assertNull(ServerEndpoint.websocketUrl("not a url", ServerKind.CORE))
    }

    @Test
    fun `every kind stays reachable whatever is already known`() {
        assertEquals(listOf(ServerKind.CORE, ServerKind.RELAY), ServerEndpoint.candidates(null))
        for (known in ServerKind.entries) {
            val order = ServerEndpoint.candidates(known)
            assertEquals(known, order.first())
            assertEquals(ServerKind.entries.toSet(), order.toSet())
        }
    }

    @Test
    fun `only the console can show a management page`() {
        assertTrue(ServerEndpoint.servesConsole(ServerKind.RELAY))
        assertFalse(ServerEndpoint.servesConsole(ServerKind.CORE))
        assertFalse(ServerEndpoint.servesConsole(null))
    }

    @Test
    fun `the probe hangs off the base url and refuses a bad one`() {
        assertEquals("http://box.lan:8199/api/config", ServerEndpoint.probeUrl("http://box.lan:8199/"))
        assertNull(ServerEndpoint.probeUrl("nonsense"))
    }
}
