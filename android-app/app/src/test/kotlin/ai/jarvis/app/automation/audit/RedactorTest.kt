package ai.jarvis.app.automation.audit

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The audit log is the one place every parameter Jarvis ever acted on is
 * written down. Over-redaction is fine; a logged OTP is not.
 */
class RedactorTest {

    @Test
    fun `obvious secrets are recognised`() {
        for (key in listOf(
            "token", "access_token", "refreshToken", "password", "passwd", "pass",
            "pin", "sim_pin", "pinCode", "otp", "code", "api_key", "apiKey",
            "secret", "client_secret", "authorization", "cookie", "session",
            "credential", "credentials", "cvv", "mnemonic", "seed", "PRIVATE_KEY"
        )) {
            assertTrue("$key should be treated as a secret", Redactor.isSecretKey(key))
        }
    }

    @Test
    fun `ordinary parameters are left alone`() {
        for (key in listOf(
            "number", "body", "text", "title", "url", "path", "level", "stream",
            "package", "destination", "latitude", "spinner", "keyword", "encoding",
            "days_ahead", "duration_ms", "calendar_id", "view_id", "method"
        )) {
            assertFalse("$key should not be treated as a secret", Redactor.isSecretKey(key))
        }
    }

    @Test
    fun `keys are split on separators and camelCase`() {
        assertEquals(listOf("api", "key"), Redactor.tokenize("apiKey"))
        assertEquals(listOf("sim", "pin"), Redactor.tokenize("sim_pin"))
        assertEquals(listOf("one", "two", "three"), Redactor.tokenize("one-two.three"))
        // Acronym runs are not split — they do not need to be, because the
        // flattened-substring pass catches things like "APIKey" anyway.
        assertEquals(listOf("httpurl"), Redactor.tokenize("HTTPUrl"))
        assertTrue(Redactor.isSecretKey("APIKey"))
        assertEquals(emptyList<String>(), Redactor.tokenize("___"))
    }

    @Test
    fun `secret values are masked and long values are truncated`() {
        assertEquals(Redactor.MASK, Redactor.redactString("password", "hunter2"))
        assertEquals("hello", Redactor.redactString("body", "hello"))

        val long = "a".repeat(300)
        val truncated = Redactor.redactString("body", long)
        assertTrue(truncated.startsWith("a".repeat(Redactor.MAX_VALUE_CHARS)))
        assertTrue(truncated.endsWith("...(+44 chars)"))
        assertEquals(Redactor.MAX_VALUE_CHARS + "...(+44 chars)".length, truncated.length)

        assertEquals("short", Redactor.truncate("short"))
    }

    @Test
    fun `empty and punctuation-only keys are not secrets`() {
        assertFalse(Redactor.isSecretKey(""))
        assertFalse(Redactor.isSecretKey("___"))
    }
}
