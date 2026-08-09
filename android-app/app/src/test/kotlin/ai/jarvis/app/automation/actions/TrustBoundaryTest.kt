package ai.jarvis.app.automation.actions

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `open_url` used to read only the SCHEME out of [SsrfGuard.check] and throw the
 * verdict away, while sitting at Tier 1 — so a prompt-injected server could
 * silently drive the browser to `http://192.168.1.1/reboot`,
 * `http://localhost:8080/admin` or `http://169.254.169.254/…`. That is a GET
 * that changes something, made from the user's LAN, carrying the user's
 * cookies, with no prompt.
 *
 * The fix is a tier raise rather than a block — the user may legitimately want
 * their router page — so what is pinned here is the predicate that decides it.
 */
class TrustBoundaryTest {

    /** A jarvis-core on the LAN, which is where it normally lives. */
    private val jarvis = setOf("192.168.2.10")

    @Test
    fun `a URL aimed inside the trust boundary is flagged`() {
        for (url in listOf(
            "http://localhost:8080/admin",
            "http://LOCALHOST/",
            "http://127.0.0.1:8123/api/websocket",
            "http://2130706433/",
            "http://0177.0.0.1/",
            "http://192.168.1.1/reboot?confirm=1",
            "http://10.0.0.1/",
            "http://172.16.4.4/",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://100.64.9.9/",
            "http://[::1]/",
            "http://[fd00::1]/",
            "http://[fe80::1]/",
            "http://[::ffff:127.0.0.1]/"
        )) {
            assertTrue(url, SsrfGuard.isInsideTrustBoundary(url))
        }
    }

    @Test
    fun `an ordinary public URL is not flagged and stays tier 1`() {
        for (url in listOf(
            "https://example.com/",
            "http://example.com/path?q=1",
            "https://en.wikipedia.org/wiki/Iron_Man",
            "https://8.8.8.8/"
        )) {
            assertFalse(url, SsrfGuard.isInsideTrustBoundary(url))
        }
    }

    @Test
    fun `the configured jarvis-core host is not treated as a surprise`() {
        // We already hold an authenticated socket to it; prompting for it would
        // be noise, not safety. Without the exemption the same LAN address is
        // exactly what this check is for.
        assertFalse(SsrfGuard.isInsideTrustBoundary("http://192.168.2.10:8123/api", jarvis))
        assertTrue(SsrfGuard.isInsideTrustBoundary("http://192.168.2.10:8123/api", emptySet()))
        // The exemption is one host, not "the LAN".
        assertTrue(SsrfGuard.isInsideTrustBoundary("http://192.168.2.11:8123/api", jarvis))
    }

    @Test
    fun `a malformed url is not turned into a consent prompt`() {
        // No host means nothing to show a human; execute() rejects these.
        for (url in listOf(null, "", "   ", "not a url", "javascript:alert(1)", "file:///etc/hosts")) {
            assertFalse(
                "$url should be rejected outright, not escalated",
                SsrfGuard.isInsideTrustBoundary(url)
            )
        }
    }

    @Test
    fun `credentials in the url are rejected rather than escalated`() {
        // check() refuses these before it records a host, so they fall to
        // execute()'s scheme/host validation and never reach the browser.
        val check = SsrfGuard.check("http://user:pw@example.com/")
        assertFalse(check.allowed)
        assertFalse(SsrfGuard.isInsideTrustBoundary("http://user:pw@example.com/"))
    }

    @Test
    fun `an empty or blank configured host does not become an empty-string allowlist`() {
        ActionEnv.jarvisServerHost = null
        assertTrue(ActionEnv.allowedHttpHosts().isEmpty())
        ActionEnv.jarvisServerHost = "   "
        assertTrue(ActionEnv.allowedHttpHosts().isEmpty())
        ActionEnv.jarvisServerHost = "Jarvis.LAN."
        assertTrue(ActionEnv.allowedHttpHosts().contains("jarvis.lan"))
        assertFalse(SsrfGuard.isInsideTrustBoundary("http://jarvis.lan/", ActionEnv.allowedHttpHosts()))
        ActionEnv.jarvisServerHost = null
    }
}
