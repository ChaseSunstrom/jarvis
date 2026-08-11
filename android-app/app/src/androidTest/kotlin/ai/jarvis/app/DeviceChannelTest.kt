package ai.jarvis.app

import ai.jarvis.app.channel.JarvisChannel
import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.FakeJarvisServer
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/**
 * The device registers with a server and executes a Tier-1 command.
 *
 * The happy path of `docs/device-channel.md`, run for real on a device:
 *
 * ```
 *   connect -> auth_required -> auth{token} -> auth_ok
 *           -> jarvis/device/register{device:{…, actions:[…]}}
 *           -> result{success:true}                       ==> READY
 *   <- device_command{command_id, action:"list_files", tier:1}
 *   -> device_result{command_id, status:"ok", result:{…}}
 * ```
 *
 * Everything on the device side is production code: the real `JarvisChannel`
 * over a real WebSocket, the real `CommandGate`, the real `TierGuard`, the real
 * `ActionRegistry` with the real policy store and the real `UiApprovalGateway`.
 * Only the server is a stand-in, and only because the point of this test is a
 * script the server has to follow exactly (see [FakeJarvisServer]).
 *
 * ## `list_files` is not an arbitrary choice
 *
 * It is Tier 1 AUTO in the local action table, needs no runtime permission,
 * cannot fail for an environmental reason, and its result carries a `count` — so
 * a green result means the action genuinely ran rather than being answered by a
 * stub. It is also confined to `filesDir/jarvis_files` by two independent
 * checks, so a test that runs it on a developer's phone cannot touch anything.
 *
 * ## About the wiring
 *
 * The shipping app now starts a channel of its own —
 * `ai.jarvis.app.channel.DeviceChannelHost`, owned by `JarvisAutomationService`
 * — so `TestHooks.startChannel` stops that one first and takes sole ownership
 * for the duration of the test. Two channels to one server means two
 * registrations and a coin toss over which socket a `device_command` arrives on.
 *
 * That startup path is NOT what this test proves. It proves the protocol, with
 * the socket brought up at a moment the test chooses against a server it
 * scripts. That the app starts one at all is asserted statically, in
 * `android-app/tools/channel_protocol_test.py` — which is where the gap this
 * comment used to describe would have been caught.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class DeviceChannelTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var server: FakeJarvisServer
    private lateinit var channel: JarvisChannel

    @Before
    fun connectTheDevice() {
        server = FakeJarvisServer().start()
        TestHooks.configure(context, server.baseUrl, server.expectedToken)

        // Something on screen, so the screenshots are of an app rather than of a
        // launcher, and so the process is foreground while commands arrive.
        //
        // Muted first. The home screen opens a conversation on every resume now,
        // and the server it would dial is THIS test's fake — which speaks the
        // device channel and not the assist pipeline, so the conversation gets
        // nothing usable and retries against the socket this test is waiting on.
        // Nothing here is about voice; the screen is wanted for its pixels.
        TestHooks.muteMicrophone(context)
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        channel = TestHooks.startChannel(context)
    }

    @After
    fun disconnect() {
        TestHooks.stopChannel(context)
        server.close()
    }

    @Test
    fun theDeviceAuthenticatesAndRegistersItsActionManifest() {
        val register = server.awaitRegistration()

        val device = register.optJSONObject("device")
        assertNotNull("The register frame must carry a `device` object", device)
        assertEquals(
            "The platform must be reported as android",
            "android",
            device!!.optString("platform"),
        )
        assertEquals(
            "The device id must be the persisted per-install UUID",
            TestHooks.deviceId(context),
            device.optString("id"),
        )

        val actions = device.optJSONArray("actions")
        assertNotNull(
            "The manifest rides inside `device` as an additive field; without it " +
                "the server has no tools to offer the model",
            actions,
        )
        assertTrue(
            "The manifest must describe more than nothing; got ${actions!!.length()} actions",
            actions.length() > 0,
        )

        // Every entry must carry a tier. The channel builds its own local tier
        // table from this array, and an entry with no usable tier is treated as
        // CONFIRM — safe, but it means the server would be told this device
        // confirms everything.
        for (i in 0 until actions.length()) {
            val entry = actions.optJSONObject(i) ?: continue
            val id = entry.optString("id")
            assertTrue("Manifest entry $i has no id", id.isNotEmpty())
            val tier = entry.optInt("tier", -1)
            assertTrue(
                "Action \"$id\" has tier $tier; the wire tier must be 1, 2 or 3",
                tier in 1..3,
            )
        }

        Waits.until("the channel to reach READY after registration") {
            channel.status.value.state == JarvisChannel.State.READY
        }

        Screenshots.take("DeviceChannelTest-registered")
    }

    @Test
    fun aTierOneCommandExecutesAndAnswersWithADeviceResult() {
        server.awaitRegistration()
        Waits.until("the channel to reach READY") {
            channel.status.value.state == JarvisChannel.State.READY
        }

        val commandId = "cmd-${UUID.randomUUID()}"
        server.sendDeviceCommand(
            commandId = commandId,
            action = TIER1_ACTION,
            params = JSONObject(),
            tier = 1,
            reason = "an instrumented test asked the device to list its own storage",
        )

        val result = server.awaitDeviceResult(commandId)

        assertEquals(
            "A Tier-1 action must run without asking anybody. A `denied` here means " +
                "the policy store or a kill switch refused it; an `unsupported` means " +
                "the action dispatcher was never installed. Frame: $result",
            "ok",
            result.optString("status"),
        )
        assertEquals(
            "The result must answer the command it was sent for",
            commandId,
            result.optString("command_id"),
        )

        val payload = result.optJSONObject("result")
        assertNotNull("A successful $TIER1_ACTION must return its listing", payload)
        assertTrue(
            "…including a count, which is what shows the action really ran",
            payload!!.has("count"),
        )

        // The audit log is the device's own record, and it is where a user would
        // look. It must agree with what went on the wire.
        val recorded = TestHooks.policyDecisions(context, TIER1_ACTION)
        assertEquals(
            "Exactly one audit line for one command; entries: $recorded",
            1,
            recorded.size,
        )
        val entry = recorded.first()
        assertEquals("The enforced tier must be AUTO", "AUTO", entry.tier)
        assertEquals("A Tier-1 action is allowed, not asked about", "ALLOW", entry.decision)
        assertEquals("…and the recorded status must match the wire", "ok", entry.status)
        assertEquals(
            "…and the line must be joinable back to the command that caused it",
            commandId,
            entry.commandId,
        )
        assertTrue("…and it must be marked as executed", entry.executed)

        Screenshots.take("DeviceChannelTest-tier1-executed")
    }

    @Test
    fun theSameCommandIdIsNeverExecutedTwice() {
        server.awaitRegistration()
        Waits.until("the channel to reach READY") {
            channel.status.value.state == JarvisChannel.State.READY
        }

        val commandId = "cmd-${UUID.randomUUID()}"
        server.sendDeviceCommand(commandId, TIER1_ACTION, tier = 1)
        server.awaitDeviceResult(commandId)

        // A redelivery. The server does this whenever it did not hear an answer,
        // so it has to be safe: `CommandGate` replays the stored reply verbatim
        // and the action must not run a second time.
        server.sendDeviceCommand(commandId, TIER1_ACTION, tier = 1)

        Waits.untilPresent("the replayed device_result for $commandId") {
            server.deviceResults(commandId).takeIf { it.size >= 2 }
        }

        val results = server.deviceResults(commandId)
        assertEquals(
            "The redelivery must be answered, not ignored — silence leaves the " +
                "server waiting forever",
            2,
            results.size,
        )
        assertEquals(
            "…and it must be the SAME answer, replayed",
            results[0].toString(),
            results[1].toString(),
        )

        val recorded = TestHooks.policyDecisions(context, TIER1_ACTION)
        assertEquals(
            "The action itself must have run exactly once, whatever the server sent. " +
                "Audit entries: $recorded",
            1,
            recorded.size,
        )

        Screenshots.take("DeviceChannelTest-redelivery")
    }

    @Test
    fun anUnknownActionIsRefusedWithoutRunningAnything() {
        server.awaitRegistration()
        Waits.until("the channel to reach READY") {
            channel.status.value.state == JarvisChannel.State.READY
        }

        val commandId = "cmd-${UUID.randomUUID()}"
        server.sendDeviceCommand(
            commandId = commandId,
            action = "definitely_not_a_real_action",
            tier = 1,
            reason = "a server that has been told about an action this build does not have",
        )

        val result = server.awaitDeviceResult(commandId)
        assertEquals(
            "An action not in the local table is unsupported, and the device says so " +
                "rather than going quiet. Frame: $result",
            "unsupported",
            result.optString("status"),
        )

        val recorded = TestHooks.policyDecisions(context, "definitely_not_a_real_action")
        assertTrue(
            "An unknown action must still be audited — 'if it is not in here, it did " +
                "not run' only holds if refusals are recorded too",
            recorded.isNotEmpty(),
        )
        assertEquals(
            "An unknown action is treated as CONFIRM, because we cannot tell how " +
                "dangerous it would have been",
            "CONFIRM",
            recorded.last().tier,
        )
        assertEquals("…and denied", "DENY", recorded.last().decision)

        Screenshots.take("DeviceChannelTest-unknown-action")
    }

    private companion object {
        /**
         * Tier 1 AUTO, no runtime permission, confined to `filesDir/jarvis_files`
         * by `PathScope` and `FileSandbox`, and its result carries a `count` —
         * so a green result proves the action ran rather than being stubbed.
         */
        const val TIER1_ACTION = "list_files"
    }
}
