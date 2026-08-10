package ai.jarvis.app.update

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * Fetching the app's own updates from GitHub Releases.
 *
 * The app is sideloaded, so nothing else is going to update it. CI publishes
 * every build as a prerelease and every hand-published tag as a full release;
 * [ReleaseFeed] decides which of those is worth offering and this brings it
 * down and hands it to the platform installer.
 *
 * **The user still confirms.** Installation goes through [PackageInstaller],
 * which shows the system's own "do you want to install this update?" prompt.
 * That is deliberate and not a step to engineer away: an app that can silently
 * replace itself from the internet is an app that only needs one bad release,
 * or one stolen token, to become something else entirely.
 *
 * `PackageInstaller` rather than an `ACTION_VIEW` on a `content://` URI: the
 * session API needs no FileProvider, never writes the APK anywhere another app
 * can read it, and reports a real result instead of leaving the app guessing
 * whether the installer ever appeared.
 */
class UpdateChecker(
    private val context: Context,
    private val repo: String = DEFAULT_REPO,
) {

    sealed interface Result {
        /** An installable update, already downloaded and handed to the installer. */
        data class Offered(val update: ReleaseFeed.Update) : Result

        /** Checked successfully; this is already the newest build. */
        data object UpToDate : Result

        /** Could not check or could not install. [message] is for the user. */
        data class Failed(val message: String) : Result
    }

    private val http = OkHttpClient.Builder()
        .callTimeout(60, TimeUnit.SECONDS)
        .build()

    /**
     * Ask GitHub what is available. Blocking — call it off the main thread.
     *
     * [installedVersionCode] is what is running now; see
     * `PackageInfo.longVersionCode`.
     */
    fun check(installedVersionCode: Long, allowPrerelease: Boolean): Result {
        val url = "https://api.github.com/repos/$repo/releases?per_page=$PAGE_SIZE"
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/vnd.github+json")
            .header("X-GitHub-Api-Version", "2022-11-28")
            .build()
        val body = try {
            http.newCall(request).execute().use { response ->
                if (response.code == 404) {
                    // A private repo answers 404 rather than 403 to an
                    // unauthenticated client, so say the useful thing instead
                    // of "not found", which sounds like a typo in the name.
                    return Result.Failed(
                        "GitHub returned nothing for $repo. If the repository is " +
                            "private, releases cannot be checked without a token."
                    )
                }
                if (!response.isSuccessful) {
                    return Result.Failed("GitHub returned HTTP ${response.code}.")
                }
                response.body?.string()
            }
        } catch (t: Throwable) {
            Log.w(TAG, "update check failed", t)
            return Result.Failed("Could not reach GitHub: ${t.message ?: "no connection"}")
        } ?: return Result.Failed("GitHub returned an empty response.")

        val update = ReleaseFeed.pick(body, installedVersionCode, allowPrerelease)
            ?: return Result.UpToDate
        return Result.Offered(update)
    }

    /**
     * Download [update] and hand it to the platform installer. Blocking.
     *
     * Streams straight into the install session rather than to a file: the APK
     * never lands anywhere on disk that another app could swap underneath us
     * between the download finishing and the install starting.
     */
    fun install(update: ReleaseFeed.Update): Result {
        // Re-checked here and not only when the feed was parsed. This is the
        // call that actually reaches out, and it is the one place where being
        // wrong means fetching an attacker's bytes.
        if (!ReleaseFeed.isAllowedDownload(update.downloadUrl)) {
            return Result.Failed("Refusing to download from ${update.downloadUrl}")
        }

        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(
            PackageInstaller.SessionParams.MODE_FULL_INSTALL
        )
        if (update.sizeBytes > 0) params.setSize(update.sizeBytes)

        var sessionId = -1
        try {
            sessionId = installer.createSession(params)
            installer.openSession(sessionId).use { session ->
                val request = Request.Builder().url(update.downloadUrl).build()
                http.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        return Result.Failed("Download failed: HTTP ${response.code}")
                    }
                    val source = response.body?.byteStream()
                        ?: return Result.Failed("Download returned no data.")
                    session.openWrite(WRITE_NAME, 0, update.sizeBytes.takeIf { it > 0 } ?: -1)
                        .use { sink ->
                            source.copyTo(sink, DEFAULT_BUFFER_SIZE)
                            session.fsync(sink)
                        }
                }
                session.commit(confirmationIntent().intentSender)
            }
            return Result.Offered(update)
        } catch (t: Throwable) {
            Log.w(TAG, "install failed", t)
            // Abandon explicitly: a half-written session left behind counts
            // against the installer's limits and the next attempt fails for a
            // reason that has nothing to do with the real problem.
            if (sessionId >= 0) runCatching { installer.abandonSession(sessionId) }
            return Result.Failed(installFailureMessage(t))
        }
    }

    /**
     * Where the installer reports its verdict.
     *
     * Broadcast to ourselves. The user sees the system's install prompt either
     * way; this exists so a refusal has somewhere to be logged rather than
     * vanishing.
     */
    private fun confirmationIntent(): PendingIntent = PendingIntent.getBroadcast(
        context,
        0,
        Intent(ACTION_INSTALL_RESULT).setPackage(context.packageName),
        PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun installFailureMessage(t: Throwable): String {
        val detail = t.message ?: t::class.java.simpleName
        return if (detail.contains("INSTALL_FAILED_UPDATE_INCOMPATIBLE", ignoreCase = true) ||
            detail.contains("signatures do not match", ignoreCase = true)
        ) {
            // The one failure with a specific, non-obvious remedy.
            "This build was signed with a different key than the installed app, " +
                "so Android will not update in place. Uninstall Jarvis first, " +
                "then install this build."
        } else {
            "Install failed: $detail"
        }
    }

    companion object {
        private const val TAG = "JarvisUpdate"
        private const val WRITE_NAME = "jarvis.apk"
        private const val PAGE_SIZE = 20

        const val ACTION_INSTALL_RESULT = "ai.jarvis.app.INSTALL_RESULT"

        /** Where this app's releases live. */
        const val DEFAULT_REPO = "ChaseSunstrom/jarvis"
    }
}
