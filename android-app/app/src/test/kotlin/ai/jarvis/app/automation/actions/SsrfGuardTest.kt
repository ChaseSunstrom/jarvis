package ai.jarvis.app.automation.actions

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `http_request` is the action most likely to be aimed somewhere it should not
 * go, because the thing choosing the URL reads attacker-controlled text.
 */
class SsrfGuardTest {

    private val jarvis = setOf("jarvis.lan")

    @Test
    fun `public hosts are allowed but must still be resolved and re-checked`() {
        val check = SsrfGuard.check("https://example.com/search?q=1")
        assertTrue(check.allowed)
        assertTrue(check.needsDnsCheck)
        assertFalse(check.exempt)
        assertEquals("example.com", check.host)
        assertEquals(443, check.port)
        assertEquals("https", check.scheme)
    }

    @Test
    fun `loopback is blocked in every spelling`() {
        for (url in listOf(
            "http://localhost/",
            "http://LOCALHOST./",
            "http://sub.localhost/",
            "http://127.0.0.1/",
            "http://127.1.2.3:8123/api",
            "http://2130706433/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://ip6-localhost/"
        )) {
            assertFalse("$url should be blocked", SsrfGuard.check(url).allowed)
        }
    }

    @Test
    fun `private link-local and metadata addresses are blocked`() {
        for (url in listOf(
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://instance-data/",
            "http://10.1.2.3/",
            "http://192.168.1.1/",
            "http://172.16.0.5/",
            "http://172.31.255.255/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://[fe80::1]/",
            "http://[fd00:ec2::254]/",
            "http://[fc00::1]/"
        )) {
            assertFalse("$url should be blocked", SsrfGuard.check(url).allowed)
        }
    }

    @Test
    fun `public ranges next to private ones stay allowed`() {
        for (url in listOf(
            "https://8.8.8.8/",
            "https://172.32.0.1/",   // just outside 172.16/12
            "https://172.15.0.1/",   // just below
            "https://100.63.255.255/", // just below CGNAT
            "https://11.0.0.1/"      // just above 10/8
        )) {
            assertTrue("$url should be allowed", SsrfGuard.check(url).allowed)
        }
    }

    @Test
    fun `only http and https, and never with embedded credentials`() {
        assertFalse(SsrfGuard.check("ftp://example.com/x").allowed)
        assertFalse(SsrfGuard.check("file:///etc/passwd").allowed)
        assertFalse(SsrfGuard.check("javascript:alert(1)").allowed)
        assertFalse(SsrfGuard.check("intent://scan/#Intent;scheme=zxing;end").allowed)
        assertFalse(SsrfGuard.check("content://settings/secure").allowed)
        assertFalse(SsrfGuard.check("example.com/no-scheme").allowed)
        assertFalse(SsrfGuard.check("http://user:pass@example.com/").allowed)
        assertFalse(SsrfGuard.check("").allowed)
        assertFalse(SsrfGuard.check(null).allowed)
        assertFalse(SsrfGuard.check("http://exa\nmple.com/").allowed)
    }

    @Test
    fun `the configured jarvis server is the one exemption`() {
        val check = SsrfGuard.check("http://jarvis.lan:8123/api/websocket", jarvis)
        assertTrue(check.allowed)
        assertTrue(check.exempt)
        assertFalse(check.needsDnsCheck)

        // A LAN address is reachable only when it IS the configured server.
        assertFalse(SsrfGuard.check("http://192.168.1.50:8123/", jarvis).allowed)
        assertTrue(SsrfGuard.check("http://192.168.1.50:8123/", setOf("192.168.1.50")).allowed)

        // The exemption is exact — no suffix games.
        assertFalse(SsrfGuard.check("http://evil-jarvis.lan/", jarvis).allowed)
        assertFalse(SsrfGuard.check("http://jarvis.lan.evil.com/", jarvis).allowed)
    }

    @Test
    fun `ipv4 is parsed in the spellings a resolver would accept`() {
        assertEquals(2130706433L, SsrfGuard.parseIpv4("127.0.0.1"))
        assertEquals(2130706433L, SsrfGuard.parseIpv4("2130706433"))
        assertEquals(2130706433L, SsrfGuard.parseIpv4("0x7f.0.0.1"))
        assertEquals(2130706433L, SsrfGuard.parseIpv4("0177.0.0.1"))
        assertEquals(2130706433L, SsrfGuard.parseIpv4("127.1"))
        assertEquals(0L, SsrfGuard.parseIpv4("0.0.0.0"))
        assertNull(SsrfGuard.parseIpv4("example.com"))
        assertNull(SsrfGuard.parseIpv4("1.2.3.4.5"))
        assertNull(SsrfGuard.parseIpv4("256.1.1.1"))
        assertNull(SsrfGuard.parseIpv4(""))
    }

    @Test
    fun `isBlockedIp is what the DNS re-check calls`() {
        for (ip in listOf(
            "127.0.0.1", "127.53.1.9", "10.0.0.1", "192.168.0.1", "172.20.1.1",
            "169.254.169.254", "0.0.0.0", "224.0.0.1", "255.255.255.255",
            "::1", "fe80::abcd", "fd00::1", "::ffff:10.0.0.1"
        )) {
            assertTrue("$ip should be blocked", SsrfGuard.isBlockedIp(ip))
        }
        for (ip in listOf("8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1111")) {
            assertFalse("$ip should be allowed", SsrfGuard.isBlockedIp(ip))
        }
    }

    @Test
    fun `unparseable ipv6 fails closed`() {
        assertTrue(SsrfGuard.isBlockedIp("::1::2"))
        assertTrue(SsrfGuard.isBlockedIp(":::"))
        assertNull(SsrfGuard.expandIpv6("gg::1"))
    }

    @Test
    fun `ipv6 expansion places groups correctly`() {
        assertEquals(
            listOf(0, 0, 0, 0, 0, 0, 0, 1),
            SsrfGuard.expandIpv6("::1")?.toList()
        )
        assertEquals(
            listOf(0xfe80, 0, 0, 0, 0, 0, 0, 1),
            SsrfGuard.expandIpv6("fe80::1")?.toList()
        )
        assertEquals(
            listOf(0, 0, 0, 0, 0, 0xffff, 0x7f00, 0x0001),
            SsrfGuard.expandIpv6("::ffff:127.0.0.1")?.toList()
        )
    }
}
