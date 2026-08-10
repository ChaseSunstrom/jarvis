package ai.jarvis.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which GitHub release, if any, is worth installing.
 *
 * This turns a document fetched over the internet into "download this file and
 * ask the user to install it", so the refusals matter more than the successes.
 * `android-app/tools/release_feed_test.py` mirrors the rules.
 */
class ReleaseFeedTest {

    private fun release(
        tag: String,
        prerelease: Boolean = false,
        draft: Boolean = false,
        url: String = "https://github.com/o/r/releases/download/$tag/jarvis-release.apk",
        name: String = "jarvis-release.apk",
        label: String = "",
    ) = """
        {"tag_name":"$tag","draft":$draft,"prerelease":$prerelease,"body":"notes",
         "assets":[{"name":"$name","label":"$label","size":123,
                    "browser_download_url":"$url"}]}
    """.trimIndent()

    private fun feed(vararg releases: String) = releases.joinToString(",", "[", "]")

    @Test
    fun `a newer build is offered`() {
        val update = ReleaseFeed.pick(feed(release("v1.2.0+42")), 41, allowPrerelease = true)
        assertEquals(42L, update?.versionCode)
        assertEquals("1.2.0", update?.versionName)
    }

    @Test
    fun `the same or an older build is not offered`() {
        // Android refuses these installs, so offering one is a prompt that can
        // only fail.
        assertNull(ReleaseFeed.pick(feed(release("v1.2.0+42")), 42, true))
        assertNull(ReleaseFeed.pick(feed(release("v1.2.0+42")), 99, true))
    }

    @Test
    fun `versions compare as integers, not as tag text`() {
        // As strings "v1.9.0+9" sorts after "v1.10.0+10".
        val update = ReleaseFeed.pick(feed(release("v1.9.0+9"), release("v1.10.0+10")), 8, true)
        assertEquals(10L, update?.versionCode)
    }

    @Test
    fun `the highest code wins whatever order the feed is in`() {
        val update = ReleaseFeed.pick(feed(release("v1.11.0+11"), release("v1.10.0+10")), 1, true)
        assertEquals(11L, update?.versionCode)
    }

    @Test
    fun `a draft is never offered`() {
        assertNull(ReleaseFeed.pick(feed(release("v2.0.0+50", draft = true)), 1, true))
    }

    @Test
    fun `a prerelease needs opting in`() {
        val one = feed(release("v2.0.0+50", prerelease = true))
        assertNull(ReleaseFeed.pick(one, 1, allowPrerelease = false))
        assertEquals(50L, ReleaseFeed.pick(one, 1, allowPrerelease = true)?.versionCode)
    }

    @Test
    fun `a release whose version cannot be read is skipped, not guessed at`() {
        assertNull(ReleaseFeed.pick(feed(release("v2.0.0")), 1, true))
        assertNull(ReleaseFeed.pick(feed(release("v2.0.0+")), 1, true))
        assertNull(ReleaseFeed.pick(feed(release("v2.0.0+abc")), 1, true))
        // 0 is not a usable versionCode.
        assertNull(ReleaseFeed.pick(feed(release("v2.0.0+0")), 1, true))
    }

    @Test
    fun `an asset label can carry the code when the tag does not`() {
        val update = ReleaseFeed.pick(feed(release("nightly", label = "build+77")), 1, true)
        assertEquals(77L, update?.versionCode)
    }

    @Test
    fun `an asset hosted anywhere but GitHub is refused`() {
        // Even though it is the newest thing in the feed.
        assertNull(
            ReleaseFeed.pick(feed(release("v9.9.9+999", url = "https://evil.test/a.apk")), 1, true)
        )
    }

    @Test
    fun `a non-apk asset is not an update`() {
        assertNull(ReleaseFeed.pick(feed(release("v3.0.0+60", name = "notes.txt")), 1, true))
    }

    @Test
    fun `garbage in, nothing out`() {
        for (bad in listOf("", "not json", "{}", "[1,2,3]", "null")) {
            assertNull("expected null for $bad", ReleaseFeed.pick(bad, 1, true))
        }
    }

    @Test
    fun `download urls must be https on a GitHub host, matched exactly`() {
        assertTrue(ReleaseFeed.isAllowedDownload("https://github.com/o/r/a.apk"))
        assertTrue(ReleaseFeed.isAllowedDownload("https://objects.githubusercontent.com/x"))

        assertFalse(ReleaseFeed.isAllowedDownload("http://github.com/o/r/a.apk"))
        // Suffix matching would let these through.
        assertFalse(ReleaseFeed.isAllowedDownload("https://evilgithub.com/a.apk"))
        assertFalse(ReleaseFeed.isAllowedDownload("https://github.com.evil.test/a.apk"))
        // Credentials move the real host after the '@'.
        assertFalse(ReleaseFeed.isAllowedDownload("https://github.com@evil.test/a.apk"))
        assertFalse(ReleaseFeed.isAllowedDownload("https://github.com/a\napk"))
        assertFalse(ReleaseFeed.isAllowedDownload(""))
    }
}
