package ai.jarvis.app.support

import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.fail
import org.junit.Assume
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI

/**
 * Where the REAL jarvis-core harness lives, for the one test that needs a real
 * server: `ConversationE2ETest`.
 *
 * Everything is overridable with an instrumentation argument, so the CI job can
 * choose how it exposes the harness without this file having to be edited:
 *
 * ```
 * ./gradlew :app:connectedDebugAndroidTest \
 *     -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessUrl=http://10.0.2.2:8080 \
 *     -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessToken=<token>
 * ```
 *
 * or, driving the runner directly:
 *
 * ```
 * adb shell am instrument -w \
 *     -e jarvisHarnessUrl http://127.0.0.1:8080 \
 *     ai.jarvis.app.test/androidx.test.runner.AndroidJUnitRunner
 * ```
 *
 * ## The two ways to reach the host from an emulator
 *
 *  * `http://10.0.2.2:8080` — QEMU's alias for the host's loopback. Needs no
 *    setup, and is the default. Requires the debug-only cleartext entry in
 *    src/debug/res/xml/network_security_config.xml.
 *  * `http://127.0.0.1:8080` with `adb reverse tcp:8080 tcp:8080` — needs one
 *    command in the CI job, and is already permitted by the SHIPPING network
 *    security config, so it exercises the app's real transport posture.
 *
 * Either works. The second is slightly closer to the shipping configuration;
 * the first is one less thing for a CI job to get wrong.
 */
object Harness {

    private const val TAG = "JarvisHarness"

    const val ARG_URL = "jarvisHarnessUrl"
    const val ARG_TOKEN = "jarvisHarnessToken"
    const val ARG_PIPELINE = "jarvisHarnessPipeline"
    const val ARG_REQUIRED = "jarvisRequireHarness"

    /**
     * Optional exact text the harness's fake STT is known to return.
     *
     * Absent by default, because this suite does not own the harness's canned
     * responses and a test that asserted them would report an unrelated harness
     * edit as an app regression. Supply it — `-e jarvisExpectedTranscript "turn
     * on the kitchen light"` — when the two are versioned together and the
     * tighter assertion is worth having.
     */
    const val ARG_EXPECTED_TRANSCRIPT = "jarvisExpectedTranscript"

    const val DEFAULT_URL = "http://10.0.2.2:8080"
    const val DEFAULT_TOKEN = "jarvis-test-token"
    const val DEFAULT_PIPELINE = "Jarvis"

    val baseUrl: String get() = argument(ARG_URL) ?: DEFAULT_URL
    val token: String get() = argument(ARG_TOKEN) ?: DEFAULT_TOKEN
    val pipeline: String get() = argument(ARG_PIPELINE) ?: DEFAULT_PIPELINE

    /** The exact transcript to expect, when the caller supplied one. */
    fun expectedTranscript(): String? = argument(ARG_EXPECTED_TRANSCRIPT)

    /**
     * Whether a missing harness is a failure or a skip. Defaults to FAILURE.
     *
     * A test that quietly skips when the thing it tests is absent is a test that
     * reports green on a CI job which forgot to start the server — which is
     * exactly the failure mode this whole exercise exists to eliminate. Pass
     * `-e jarvisRequireHarness false` to run the rest of the suite on a machine
     * where you have not got jarvis-core up.
     */
    val required: Boolean
        get() = argument(ARG_REQUIRED)?.lowercase() != "false"

    /**
     * Fail (or skip) now, with a message that says what to do, rather than
     * letting a connection refusal surface 90 seconds later as "the transcript
     * never rendered".
     */
    fun requireReachable() {
        if (isReachable()) {
            Log.i(TAG, "harness reachable at $baseUrl")
            return
        }
        val explanation = buildString {
            append("The jarvis-core test harness is not reachable at ")
            append(baseUrl)
            append(". This test drives a real voice round trip against the real server, ")
            append("so there is nothing it can prove without one.\n")
            append("  * The CI job must boot jarvis-core with the fake model/voice ")
            append("backends and expose it to the emulator, either on the host ")
            append("(reachable at 10.0.2.2) or through `adb reverse tcp:8080 tcp:8080` ")
            append("(reachable at 127.0.0.1).\n")
            append("  * Override the address with -e $ARG_URL <url>.\n")
            append("  * To run the rest of the suite without a harness, pass ")
            append("-e $ARG_REQUIRED false and this test will skip instead.")
        }
        if (required) {
            fail(explanation)
        } else {
            Log.w(TAG, explanation)
            Assume.assumeTrue("harness not reachable at $baseUrl and not required", false)
        }
    }

    /** A TCP connect, which is all "is anything listening" needs. */
    fun isReachable(timeoutMs: Int = PROBE_TIMEOUT_MS): Boolean {
        val uri = try {
            URI.create(baseUrl)
        } catch (t: Throwable) {
            Log.w(TAG, "harness URL does not parse: $baseUrl", t)
            return false
        }
        val host = uri.host ?: return false
        val port = if (uri.port > 0) uri.port else if (uri.scheme == "https") 443 else 80
        return try {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(host, port), timeoutMs)
                true
            }
        } catch (t: Throwable) {
            Log.i(TAG, "harness probe to $host:$port failed: ${t.javaClass.simpleName}")
            false
        }
    }

    private fun argument(name: String): String? =
        InstrumentationRegistry.getArguments().getString(name)?.trim()?.takeIf { it.isNotEmpty() }

    private const val PROBE_TIMEOUT_MS = 3_000
}
