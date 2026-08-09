package ai.jarvis.app.automation.actions

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** File actions may only ever touch `filesDir/jarvis_files`. This proves the arithmetic half. */
class PathScopeTest {

    private fun allowed(path: String?, allowRoot: Boolean = false): String? =
        (PathScope.normalize(path, allowRoot) as? PathScope.Result.Allowed)?.relative

    private fun rejected(path: String?, allowRoot: Boolean = false): String? =
        (PathScope.normalize(path, allowRoot) as? PathScope.Result.Rejected)?.reason

    @Test
    fun `plain relative paths are kept`() {
        assertEquals("notes.txt", allowed("notes.txt"))
        assertEquals("a/b/c.json", allowed("a/b/c.json"))
        assertEquals("a/b.txt", allowed("./a/./b.txt"))
        assertEquals("b.txt", allowed("a/../b.txt"))
        assertEquals("a/c.txt", allowed("a/b/../c.txt"))
        assertEquals(".hidden", allowed(".hidden"))
        assertEquals("with space.txt", allowed("with space.txt"))
        assertEquals("a/b.txt", allowed("a//b.txt"))
    }

    @Test
    fun `traversal out of the sandbox is rejected`() {
        for (bad in listOf(
            "../secrets",
            "../../etc/passwd",
            "a/../../b",
            "a/b/../../../c",
            "..",
            "a/..//../b"
        )) {
            assertTrue("$bad should be rejected", rejected(bad) != null)
        }
        assertEquals("path escapes the sandbox", rejected("../x"))
    }

    @Test
    fun `absolute and scheme paths are rejected`() {
        assertEquals("absolute paths are not allowed", rejected("/data/data/other/db"))
        assertEquals("absolute paths are not allowed", rejected("C:/Windows"))
        assertTrue(rejected("file:///etc/passwd") != null)
        assertTrue(rejected("content://media/external/images") != null)
        assertTrue(rejected("~/.ssh/id_rsa") != null)
    }

    @Test
    fun `encoded and exotic separators are rejected`() {
        assertEquals("percent-encoded path segments are not allowed", rejected("%2e%2e/secrets"))
        assertEquals("percent-encoded path segments are not allowed", rejected("a/%2E%2E/b"))
        assertEquals("percent-encoded path segments are not allowed", rejected("a%2fb"))
        assertEquals("backslashes are not allowed in paths", rejected("..\\windows"))
        assertEquals("path contains a null byte", rejected("ok.txt\u0000.png"))
    }

    @Test
    fun `the root itself is only allowed for listing`() {
        assertNull(allowed(""))
        assertNull(allowed("."))
        assertNull(allowed("a/.."))
        assertEquals("", allowed("", allowRoot = true))
        assertEquals("", allowed(".", allowRoot = true))
        assertEquals("", allowed("a/..", allowRoot = true))
    }

    @Test
    fun `absurd sizes are rejected`() {
        assertEquals("path too long", rejected("x".repeat(513)))
        assertEquals("path segment too long", rejected("a/" + "x".repeat(256)))
        assertEquals("path is required", rejected(null))
        assertEquals("path is required", rejected("   "))
    }

    @Test
    fun `normalizedOrNull is the convenience form`() {
        assertEquals("a/b.txt", PathScope.normalizedOrNull("a/./b.txt"))
        assertNull(PathScope.normalizedOrNull("../b.txt"))
    }
}
