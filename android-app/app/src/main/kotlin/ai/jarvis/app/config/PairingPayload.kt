package ai.jarvis.app.config

import java.net.URI
import java.net.URLDecoder

/**
 * The string inside a Jarvis pairing QR code, parsed.
 *
 *     jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&c=<code>
 *
 * Deliberately free of Android imports, like [ServerUrl] and
 * `channel/LanHost.kt` next to it: this decides what a camera pointed at an
 * unknown square is allowed to hand the rest of the app, so it should be
 * exercisable by a plain JVM unit test rather than only on a device. There is
 * a Python mirror at `android-app/tools/pairing_payload_test.py` that runs the
 * same fixture and the same rejection table with no SDK, so the two cannot
 * drift apart quietly.
 *
 * Two things are worth saying about what is NOT in here.
 *
 * There is no token. The QR carries a short-lived, single-use *code*, which is
 * exchanged for a token over HTTP by [PairingClaim]. A QR on a screen can be
 * photographed from across the room, ends up in screenshots and in whatever
 * captured the shared window; a long-lived credential in one would be a
 * credential in all of those.
 *
 * And the URL is checked by [ServerUrl.check] — the same validator the typed
 * field uses, not a relaxed one. A QR is network input. If a scanned address
 * could skip the cleartext rule that a hand-typed address obeys, then the way
 * to bypass that rule would be to print it.
 */
data class PairingPayload(
    /** The server URL, normalised by [ServerUrl.check]. */
    val url: String,
    /** The single-use pairing code. Never logged, never stored. */
    val code: String,
) {
    /** Outcome of [parse]: a payload, or a sentence to show the user. */
    sealed interface Result {
        data class Ok(val payload: PairingPayload) : Result
        /** Not a Jarvis pairing payload at all — the caller may try other formats. */
        data class NotAPayload(val reason: String) : Result
        /** Recognisably a Jarvis payload, and refused. Show [message]. */
        data class Refused(val message: String) : Result
    }

    companion object {
        const val SCHEME = "jarvis"
        const val AUTHORITY = "pair"

        /** The only version this build understands. */
        const val VERSION = "1"

        /**
         * A generous cap on the whole scanned string.
         *
         * A version-40 QR holds 2953 bytes, and nothing legitimate here comes
         * near 512: a URL, a code, and three parameter names. The cap exists so
         * that a hostile symbol cannot hand the parser a megabyte to chew on
         * before any of the structural rules get a chance to refuse it.
         */
        const val MAX_LENGTH = 512

        /** base64url, which is what `secrets.token_urlsafe` produces. */
        private val CODE = Regex("^[A-Za-z0-9_-]{16,64}$")

        /**
         * Parse a scanned string.
         *
         * Every rule fails closed, and the two failure kinds are distinct on
         * purpose: [Result.NotAPayload] means "this was not addressed to us"
         * and lets the caller fall back to its older bare-token behaviour,
         * while [Result.Refused] means "this claimed to be one of ours and is
         * not acceptable" and must never fall through to anything.
         */
        fun parse(raw: String?): Result {
            val text = raw?.trim().orEmpty()
            if (text.isEmpty()) return Result.NotAPayload("empty")
            if (text.length > MAX_LENGTH) {
                return Result.Refused("That QR code is too long to be a Jarvis pairing code.")
            }
            // Control characters cannot appear in any legitimate payload and are
            // how a scanned string smuggles a newline into a log line or a
            // terminator into something downstream. Checked on the raw text,
            // before any decoding, and again after — see below.
            if (text.any { it.isISOControl() }) {
                return Result.Refused("That QR code contains characters a pairing code cannot.")
            }
            if (!text.startsWith("$SCHEME://", ignoreCase = true)) {
                return Result.NotAPayload("not a $SCHEME:// url")
            }

            val uri = try {
                URI.create(text)
            } catch (e: IllegalArgumentException) {
                return Result.Refused("That QR code is not a readable pairing code.")
            }
            if (!AUTHORITY.equals(uri.authority, ignoreCase = true)) {
                return Result.NotAPayload("not a pairing url")
            }

            val params = queryParams(uri.rawQuery)
                ?: return Result.Refused("That QR code is not a readable pairing code.")

            // Version first, so a future format gets the honest error rather
            // than a confusing one about a field it has renamed.
            val version = params["v"]
            if (version != VERSION) {
                return Result.Refused(
                    "That pairing code was made by a newer Jarvis than this app. Update the app."
                )
            }

            val rawUrl = params["u"]
            if (rawUrl.isNullOrEmpty()) {
                return Result.Refused("That pairing code has no server address in it.")
            }
            val decoded = try {
                URLDecoder.decode(rawUrl, "UTF-8")
            } catch (e: IllegalArgumentException) {
                return Result.Refused("That pairing code's server address is not readable.")
            }
            if (decoded.any { it.isISOControl() }) {
                return Result.Refused("That QR code contains characters a pairing code cannot.")
            }

            // The same validator the typed field uses. A QR must not be a way
            // around the cleartext rule.
            val check = ServerUrl.check(decoded)
            if (!check.isValid) {
                return Result.Refused(check.error ?: "That pairing code's server address is not usable.")
            }

            // The code is NOT decoded: it is base64url by construction, so any
            // percent sign in it is a sign something else is going on.
            val code = params["c"].orEmpty()
            if (!CODE.matches(code)) {
                return Result.Refused("That pairing code is malformed.")
            }

            return Result.Ok(PairingPayload(url = check.normalized, code = code))
        }

        /**
         * Split a raw query into single-valued parameters.
         *
         * Returns null on a repeated key rather than picking one. A payload
         * with two `u=` parameters is either broken or an attempt to have the
         * parser and something else read different values out of the same
         * string, and neither is worth guessing about.
         */
        private fun queryParams(rawQuery: String?): Map<String, String>? {
            if (rawQuery.isNullOrEmpty()) return null
            val out = HashMap<String, String>()
            for (part in rawQuery.split('&')) {
                if (part.isEmpty()) continue
                val eq = part.indexOf('=')
                if (eq <= 0) return null
                val key = part.substring(0, eq)
                if (out.containsKey(key)) return null
                out[key] = part.substring(eq + 1)
            }
            return out
        }
    }
}
