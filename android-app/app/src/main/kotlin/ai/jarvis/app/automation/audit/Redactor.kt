package ai.jarvis.app.automation.audit

/**
 * PURE LOGIC — no Android imports, no org.json. Unit-testable on a plain JVM.
 *
 * Decides which parameter keys are secrets and how long a logged value may be.
 * The audit log is a local file the user can read, but it is still the one
 * place where every parameter Jarvis ever acted on is written down, so
 * anything that smells like a credential is masked before it lands there.
 *
 * Over-redaction is the safe direction: `country_code` losing its value in the
 * log costs nothing, a logged OTP costs a lot.
 */
object Redactor {

    const val MASK = "[redacted]"

    /** Longest string value kept verbatim in the log; the rest is elided. */
    const val MAX_VALUE_CHARS = 256

    /** Longest list/array we enumerate before summarising it. */
    const val MAX_ARRAY_ITEMS = 20

    /**
     * Whole-token matches. Short and generic words go here so that
     * `pin` matches `pin`, `sim_pin` and `pinCode` but not `spinner`.
     */
    private val SECRET_TOKENS = setOf(
        "token", "password", "passwd", "pass", "passphrase", "pin", "otp",
        "code", "secret", "key", "apikey", "auth", "authorization", "credential",
        "credentials", "cookie", "session", "seed", "mnemonic", "cvv", "cvc",
        "ssn", "iban", "account", "bearer", "signature", "sig", "nonce"
    )

    /**
     * Substring matches for words long enough that a false positive is
     * essentially impossible.
     */
    private val SECRET_SUBSTRINGS = listOf(
        "token", "password", "passwd", "secret", "apikey", "credential",
        "privatekey", "accesskey", "authorization", "otpcode", "pincode"
    )

    /** True when a parameter under this key must never be written verbatim. */
    fun isSecretKey(key: String): Boolean {
        val flat = key.lowercase().filter { it.isLetterOrDigit() }
        if (flat.isEmpty()) return false
        if (SECRET_SUBSTRINGS.any { flat.contains(it) }) return true
        return tokenize(key).any { it in SECRET_TOKENS }
    }

    /**
     * Split a key into lowercase words on `_ - . / space` and camelCase
     * boundaries: `apiKey` -> [api, key], `sim_pin` -> [sim, pin].
     */
    fun tokenize(key: String): List<String> {
        val out = ArrayList<String>()
        val current = StringBuilder()
        var prevWasLower = false
        for (ch in key) {
            when {
                !ch.isLetterOrDigit() -> {
                    if (current.isNotEmpty()) { out += current.toString().lowercase(); current.clear() }
                    prevWasLower = false
                }
                ch.isUpperCase() && prevWasLower -> {
                    if (current.isNotEmpty()) { out += current.toString().lowercase(); current.clear() }
                    current.append(ch)
                    prevWasLower = false
                }
                else -> {
                    current.append(ch)
                    prevWasLower = ch.isLowerCase() || ch.isDigit()
                }
            }
        }
        if (current.isNotEmpty()) out += current.toString().lowercase()
        return out
    }

    /** Mask or shorten one string value according to its key. */
    fun redactString(key: String, value: String): String =
        if (isSecretKey(key)) MASK else truncate(value)

    /** Keep long free text (SMS bodies, HTTP payloads) from bloating the log. */
    fun truncate(value: String, max: Int = MAX_VALUE_CHARS): String =
        if (value.length <= max) value
        else value.substring(0, max) + "...(+" + (value.length - max) + " chars)"
}
