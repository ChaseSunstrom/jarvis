package ai.jarvis.app.assist

import ai.jarvis.app.config.ServerUrl
import android.content.Context
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Model weights, fetched from the user's own Jarvis and kept on the phone.
 *
 * The weights are not in the APK — megabytes for a feature most installs never
 * enable — and they are deliberately not fetched from GitHub or Hugging Face
 * either. A phone that downloads a wake-word model from a public host tells a
 * third party, and every network in between, that this device is setting up a
 * private voice assistant and which phrase it listens for. It would also be
 * the one place in the app that talks to something other than the configured
 * server, in the code path that runs on a fresh, unconfigured install.
 *
 * So jarvis-core mirrors them: it fetches once on a machine that already
 * reaches the internet, checks the bytes against a digest pinned in its own
 * source, and serves them at `/api/models/<name>`. This end downloads over the
 * origin it already trusts, with the token it already holds, and verifies the
 * digest again — because "the server said so" is exactly the assumption this
 * project does not make anywhere else.
 */
object ModelStore {

    private const val TAG = "JarvisModels"
    private const val DIGEST_HEADER = "X-Jarvis-SHA256"

    /** Where the weights live: app-private, excluded from backup. */
    fun directory(context: Context): File =
        File(context.filesDir, "models").apply { mkdirs() }

    fun isDownloaded(context: Context, names: List<String>): Boolean {
        val dir = directory(context)
        return names.all { File(dir, it).let { f -> f.isFile && f.length() > 0 } }
    }

    /** How many bytes are on disk, for a Settings line that means something. */
    fun bytesOnDisk(context: Context): Long =
        directory(context).listFiles()?.sumOf { it.length() } ?: 0L

    fun deleteAll(context: Context) {
        directory(context).listFiles()?.forEach { runCatching { it.delete() } }
    }

    /**
     * Download every missing model. Blocking; call from a worker thread.
     *
     * @return null on success, or a sentence explaining what went wrong — one
     *   the Settings screen can show verbatim, because "download failed" is not
     *   something anyone can act on.
     */
    fun download(
        context: Context,
        serverUrl: String,
        token: String,
        names: List<String>,
        onProgress: (String, Int, Int) -> Unit = { _, _, _ -> },
    ): String? {
        val base = ServerUrl.normalize(serverUrl)
        if (base.isEmpty() || token.isEmpty()) {
            return "Set the server URL and token first."
        }
        val client = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            // A model is a couple of megabytes over a LAN, but a phone on a bad
            // link should be allowed to finish rather than restart forever.
            .readTimeout(180, TimeUnit.SECONDS)
            // The host pin, at the transport: a redirect must not move this
            // download — or the bearer token — to another host.
            .followRedirects(false)
            .followSslRedirects(false)
            .build()

        val dir = directory(context)
        names.forEachIndexed { index, name ->
            val target = File(dir, name)
            if (target.isFile && target.length() > 0) return@forEachIndexed
            onProgress(name, index + 1, names.size)

            val request = Request.Builder()
                .url("$base/api/models/$name")
                .header("Authorization", "Bearer $token")
                .build()
            val partial = File(dir, "$name.part")
            try {
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        return "The server answered ${response.code} for $name."
                    }
                    val body = response.body ?: return "The server sent nothing for $name."
                    partial.outputStream().use { out -> body.byteStream().copyTo(out) }

                    // Verify, even though it came from our own server over an
                    // authenticated connection. A truncated model does not fail
                    // loudly — it fails as a wake word that never fires, which
                    // is indistinguishable from the feature being off.
                    val expected = response.header(DIGEST_HEADER)
                    val actual = sha256(partial)
                    if (expected != null && !expected.equals(actual, ignoreCase = true)) {
                        partial.delete()
                        return "$name did not match its checksum. Nothing was saved."
                    }
                    if (!partial.renameTo(target)) {
                        partial.delete()
                        return "Could not save $name."
                    }
                }
            } catch (t: Throwable) {
                partial.delete()
                Log.w(TAG, "could not download $name", t)
                return "Could not reach the server for $name: ${t.javaClass.simpleName}."
            }
        }
        return null
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { stream ->
            val buffer = ByteArray(1 shl 16)
            while (true) {
                val read = stream.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
