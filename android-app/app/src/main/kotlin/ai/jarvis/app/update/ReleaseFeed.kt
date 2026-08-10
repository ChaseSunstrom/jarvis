package ai.jarvis.app.update

import org.json.JSONArray
import org.json.JSONObject

/**
 * Deciding whether a GitHub release is an update worth offering.
 *
 * The app is sideloaded — there is no store to push it — so it checks the
 * repository's releases itself. That means taking a JSON document from the
 * internet and turning it into "download this file and ask the user to install
 * it", which is the most dangerous sentence in the app. Everything that decides
 * it lives here, with no Android and no network, so it can be tested.
 *
 * Three rules, and each exists because of a specific way this goes wrong.
 *
 * **The version must actually be newer.** Android refuses to install a package
 * whose `versionCode` is not greater than the installed one, so offering an
 * equal-or-older release produces a prompt that can only fail. The comparison
 * is on the integer, never on the tag text: `v1.10.0` sorts before `v1.9.0` as
 * a string.
 *
 * **The asset must come from the release's own API payload**, over https, on
 * GitHub's own host. A release body is attacker-controllable text if the
 * repository is ever public or a token leaks, and "download whatever URL the
 * JSON said" is how an updater becomes a malware delivery service.
 *
 * **A draft is not a release.** Drafts are visible to anyone with push access
 * and are, by definition, not finished.
 */
object ReleaseFeed {

    /** Hosts a release asset may be downloaded from. */
    private val ALLOWED_HOSTS = setOf("github.com", "api.github.com", "objects.githubusercontent.com")

    /** The APK the release ships, once it has been judged installable. */
    data class Update(
        val versionCode: Long,
        val versionName: String,
        val tag: String,
        val downloadUrl: String,
        val sizeBytes: Long,
        val notes: String,
        val prerelease: Boolean,
    )

    /**
     * The newest installable update in [json], or null.
     *
     * [installedVersionCode] is what is running now. [allowPrerelease] lets the
     * user opt into the per-build prereleases CI publishes; with it off, only
     * full releases are offered.
     */
    fun pick(
        json: String,
        installedVersionCode: Long,
        allowPrerelease: Boolean,
    ): Update? {
        val releases = try {
            JSONArray(json)
        } catch (e: Exception) {
            return null
        }
        var best: Update? = null
        for (i in 0 until releases.length()) {
            val release = releases.optJSONObject(i) ?: continue
            val candidate = parseRelease(release, allowPrerelease) ?: continue
            if (candidate.versionCode <= installedVersionCode) continue
            // Highest code wins, not first-in-list: GitHub orders by creation
            // date, and a patch to an older line can be created most recently.
            if (best == null || candidate.versionCode > best!!.versionCode) best = candidate
        }
        return best
    }

    private fun parseRelease(release: JSONObject, allowPrerelease: Boolean): Update? {
        if (release.optBoolean("draft", false)) return null
        val prerelease = release.optBoolean("prerelease", false)
        if (prerelease && !allowPrerelease) return null

        val tag = release.optString("tag_name").trim()
        if (tag.isEmpty()) return null

        val assets = release.optJSONArray("assets") ?: return null
        for (i in 0 until assets.length()) {
            val asset = assets.optJSONObject(i) ?: continue
            val name = asset.optString("name")
            if (!name.endsWith(".apk", ignoreCase = true)) continue
            val url = asset.optString("browser_download_url")
            if (!isAllowedDownload(url)) continue
            val code = versionCodeOf(tag, asset.optString("label")) ?: continue
            return Update(
                versionCode = code,
                versionName = versionNameOf(tag),
                tag = tag,
                downloadUrl = url,
                sizeBytes = asset.optLong("size", 0L),
                notes = release.optString("body").take(MAX_NOTES),
                prerelease = prerelease,
            )
        }
        return null
    }

    /**
     * True for a URL this app will fetch an APK from.
     *
     * Scheme and host are both checked, and the host is matched exactly rather
     * than by suffix: `github.com.evil.test` ends with nothing useful but
     * `endsWith("github.com")` would have said yes to `evilgithub.com`.
     */
    fun isAllowedDownload(url: String): Boolean {
        val text = url.trim()
        if (!text.startsWith("https://")) return false
        if (text.any { it.isISOControl() }) return false
        val authority = text.removePrefix("https://").substringBefore('/')
        // Credentials in the authority would move the real host after an '@'.
        if (authority.contains('@')) return false
        val host = authority.substringBefore(':').lowercase()
        return host in ALLOWED_HOSTS
    }

    /**
     * The versionCode a release advertises.
     *
     * CI puts it in the tag as `v<name>+<code>` — the code is what Android
     * compares and the tag is the only place a client can read it without
     * downloading the whole APK first. An asset label is accepted as a
     * fallback so a hand-made release can still be offered.
     */
    fun versionCodeOf(tag: String, label: String?): Long? {
        codeAfterPlus(tag)?.let { return it }
        return codeAfterPlus(label ?: "")
    }

    private fun codeAfterPlus(text: String): Long? {
        val plus = text.lastIndexOf('+')
        if (plus < 0 || plus == text.lastIndex) return null
        val digits = text.substring(plus + 1).trim()
        if (digits.isEmpty() || !digits.all { it.isDigit() }) return null
        return digits.toLongOrNull()?.takeIf { it > 0 }
    }

    /** The human-facing version: the tag without its `v` prefix or `+code`. */
    fun versionNameOf(tag: String): String =
        tag.removePrefix("v").substringBefore('+').ifEmpty { tag }

    private const val MAX_NOTES = 2000
}
