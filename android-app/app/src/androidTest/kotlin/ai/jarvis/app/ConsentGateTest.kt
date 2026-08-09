package ai.jarvis.app

import ai.jarvis.app.channel.JarvisChannel
import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.FakeJarvisServer
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import ai.jarvis.app.ui.ConsentGate
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiObject2
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.UUID

/**
 * # The security invariant, proved on a real device.
 *
 * The most important test in this suite. Everything else checks that a feature
 * works; this checks that a feature CANNOT be made to work by the one component
 * the threat model says may be lying.
 *
 * `docs/security.md` states the rule and `PolicyEngine` implements it: a Tier-3
 * CONFIRM action asks a human EVERY TIME, the answer is never remembered, and
 * the server may raise a tier but never lower one. Those are three claims about
 * behaviour, and until now all three were only ever checked against a truth
 * table on a JVM. A truth table cannot tell you that the prompt appears at all,
 * that the parameters on it are the ones about to run, that DENY actually stops
 * the action, or that a second identical command asks again instead of quietly
 * reusing the first answer.
 *
 * ## The shape of the proof
 *
 * `delete_file` is Tier 3 CONFIRM and its effect is directly observable: plant a
 * file, ask the device to delete it, deny, and look. A test that asserted only
 * "the wire said denied" would pass on a build that reported a denial and
 * deleted the file anyway, which is exactly the failure worth catching.
 *
 * ## The one rule this test obeys about itself
 *
 * **It answers through the real UI.** There is no test hook that resolves an
 * approval, and adding one would destroy the thing being tested: a mechanism
 * that can approve around the prompt is a mechanism that can approve around the
 * prompt, whoever is holding it. `ApprovalBridge.deliver` is called by
 * `ApprovalActivity` and by nothing else, so the only way to answer is a tap on
 * a real button on a real screen — which is what happens below.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class ConsentGateTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var server: FakeJarvisServer
    private lateinit var channel: JarvisChannel

    /** The file the server will ask the device to delete. */
    private lateinit var target: File

    @Before
    fun connectAndPlantAFile() {
        server = FakeJarvisServer().start()
        TestHooks.configure(context, server.baseUrl, server.expectedToken)

        // A foreground app. Background activity-start restrictions would
        // otherwise decide whether the prompt appears directly or only as a
        // notification, and that is a different test.
        val main = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(main)

        target = plantFile()

        channel = TestHooks.startChannel(context)
        server.awaitRegistration()
        Waits.until("the channel to reach READY") {
            channel.status.value.state == JarvisChannel.State.READY
        }
    }

    @After
    fun disconnect() {
        TestHooks.stopChannel(context)
        server.close()
        runCatching { target.delete() }
    }

    // --- the invariant ------------------------------------------------------

    @Test
    fun aTierThreeCommandPromptsWithVerbatimParamsAndDenyStopsIt() {
        val commandId = "cmd-${UUID.randomUUID()}"
        val params = JSONObject().put("path", TARGET_RELATIVE_PATH)

        val prompt = sendAndAwaitPrompt(commandId, params, tier = 3, reason = SERVER_REASON)

        // 1. It says what it is about to do, and how dangerous that is.
        assertOnScreen(TIER3_ACTION, "the action id must be shown verbatim")
        assertOnScreen(
            "TIER 3",
            "the tier must be stated; a prompt that does not say how dangerous this " +
                "is trains the user to tap through it",
        )

        // 2. It shows the server's reason — untrusted remote text — labelled AS
        //    the server's, separately from the action's own description.
        assertOnScreen(SERVER_REASON, "the server's stated reason must be shown verbatim")
        assertOnScreen(
            "Why the server says so",
            "the reason must be labelled as the SERVER's. Blending it with the " +
                "action's own description would hide which half was written by a " +
                "machine that may have read a hostile web page",
        )

        // 3. It shows the parameters that are about to be executed. The
        //    load-bearing one: if the prompt can show something other than what
        //    runs, the gate is theatre.
        assertOnScreen(
            TARGET_RELATIVE_PATH,
            "the verbatim parameters must be on screen; this is the whole point of " +
                "the prompt",
        )

        // 4. It promises the answer is not remembered, and offers no control
        //    that could remember it.
        assertOnScreen(
            "not remembered",
            "the prompt must say out loud that Tier 3 is asked every time",
        )
        assertNoRememberControl()

        Screenshots.take("ConsentGateTest-tier3-prompt")

        // 5. RAW shows the exact string handed to ApprovalBridge — unformatted,
        //    not re-serialised, duplicate keys and all.
        tap("RAW")
        assertOnScreen(
            params.toString(),
            "the RAW toggle must show the exact serialisation that was passed in, " +
                "because re-serialising can lose duplicate keys in a hostile payload",
        )
        Screenshots.take("ConsentGateTest-tier3-prompt-raw")

        // 6. Deny, through the real button on the real screen.
        tap("DENY")
        Activities.awaitFinished(prompt)

        val result = server.awaitDeviceResult(commandId)
        assertEquals(
            "A denied action must be reported as denied — not error, not silence. " +
                "Frame: $result",
            "denied",
            result.optString("status"),
        )

        // 7. NOTHING RAN. The assertion a lying implementation cannot fake.
        assertTrue(
            "The file must still exist. The device reported `denied` — if the file " +
                "is gone, that report was a lie and the consent gate does nothing.",
            target.exists(),
        )
        assertEquals("…and its contents must be untouched", FILE_CONTENTS, target.readText())

        // 8. The device's own record agrees with what went on the wire.
        val recorded = TestHooks.policyDecisions(context, TIER3_ACTION)
        assertEquals("Exactly one audit line; got $recorded", 1, recorded.size)
        val entry = recorded.first()
        assertEquals("The enforced tier must be CONFIRM", "CONFIRM", entry.tier)
        assertEquals("…and the outcome DENY", "DENY", entry.decision)
        assertEquals("…reported as denied", "denied", entry.status)
        assertFalse("…and not executed", entry.executed)
        assertTrue(
            "…and the note must record that a human was asked and said no, so a " +
                "denial by policy stays distinguishable from a denial by a person. " +
                "Note: ${entry.note}",
            entry.note.orEmpty().contains("approval=DENIED"),
        )
    }

    @Test
    fun anIdenticalSecondCommandPromptsAgainBecauseTierThreeIsNeverRemembered() {
        // The invariant that separates CONFIRM from NOTIFY. A Tier-2 action may
        // be answered once and remembered; a Tier-3 one may not, ever, by any
        // route — not through the policy store, not through a cache, and not
        // because the server claims the user already agreed.
        val params = JSONObject().put("path", TARGET_RELATIVE_PATH)

        val first = "cmd-${UUID.randomUUID()}"
        val firstPrompt = sendAndAwaitPrompt(first, params, tier = 3, reason = SERVER_REASON)
        tap("DENY")
        Activities.awaitFinished(firstPrompt)
        assertEquals("denied", server.awaitDeviceResult(first).optString("status"))

        // A DIFFERENT command_id with identical content. Not a redelivery — the
        // command gate would replay the stored answer for one of those — but a
        // second request for the same thing. It must ask again.
        val second = "cmd-${UUID.randomUUID()}"
        val secondPrompt = sendAndAwaitPrompt(
            second,
            params,
            tier = 3,
            reason = SERVER_REASON,
            why = "an identical Tier-3 command must prompt again; an approval is " +
                "consent for exactly one request",
        )

        assertOnScreen(
            TARGET_RELATIVE_PATH,
            "the second prompt must show the parameters too — an abbreviated repeat " +
                "prompt is how a user is trained to stop reading them",
        )
        assertNoRememberControl()
        Screenshots.take("ConsentGateTest-second-prompt")

        tap("DENY")
        Activities.awaitFinished(secondPrompt)
        assertEquals("denied", server.awaitDeviceResult(second).optString("status"))

        assertTrue("The file must still exist after both denials", target.exists())

        val recorded = TestHooks.policyDecisions(context, TIER3_ACTION)
        assertEquals(
            "Both commands must have been asked about and denied; entries: $recorded",
            2,
            recorded.size,
        )
        assertTrue("Both must record CONFIRM", recorded.all { it.tier == "CONFIRM" })
        assertTrue("Neither may have executed", recorded.none { it.executed })

        // And nothing reached the policy store. `PolicyEngine.canRemember` and
        // `PolicyStore.setPolicy` refuse an ALLOW_ALWAYS for a CONFIRM action
        // independently of each other; this is the observable result of both.
        val policies = TestHooks.userPolicies(context)
        assertFalse(
            "No standing answer may be stored for a Tier-3 action. Store: $policies",
            policies.containsKey(TIER3_ACTION),
        )
    }

    @Test
    fun backDeniesRatherThanLeavingTheRequestUnanswered() {
        // Doing nothing must deny. Back, a swipe, being destroyed for any reason:
        // ApprovalActivity treats all of them as a refusal, because the one thing
        // a consent prompt must never do is leave the server waiting while the
        // user believes they dismissed it.
        val commandId = "cmd-${UUID.randomUUID()}"
        val prompt = sendAndAwaitPrompt(
            commandId,
            JSONObject().put("path", TARGET_RELATIVE_PATH),
            tier = 3,
            reason = SERVER_REASON,
        )
        Screenshots.take("ConsentGateTest-before-back")

        Device.ui.pressBack()
        Activities.awaitFinished(prompt)

        val result = server.awaitDeviceResult(commandId)
        assertEquals(
            "Back must produce a denial, not silence. Frame: $result",
            "denied",
            result.optString("status"),
        )
        assertTrue("…and nothing may have run", target.exists())
    }

    @Test
    fun theServerCannotTalkATierThreeActionDownToTierOne() {
        // `TierGuard.effective` is max(local, incoming), and `PolicyEngine`
        // applies the same rule again against the real action table. A server
        // that claims `"tier": 1` for delete_file is describing a field it does
        // not get to decide, and the device must still ask.
        val commandId = "cmd-${UUID.randomUUID()}"
        val prompt = sendAndAwaitPrompt(
            commandId = commandId,
            params = JSONObject().put("path", TARGET_RELATIVE_PATH),
            tier = 1,
            reason = "the server insists this one is harmless",
            why = "a Tier-3 action must prompt even when the server claims tier 1",
        )

        assertOnScreen(
            "TIER 3",
            "the prompt must show the tier the DEVICE enforced, not the one the " +
                "server asked for",
        )
        Screenshots.take("ConsentGateTest-downgrade-refused")

        tap("DENY")
        Activities.awaitFinished(prompt)

        assertEquals("denied", server.awaitDeviceResult(commandId).optString("status"))
        assertTrue("Nothing may have run", target.exists())

        val recorded = TestHooks.policyDecisions(context, TIER3_ACTION).last()
        assertEquals(
            "The enforced tier must be CONFIRM whatever the wire said",
            "CONFIRM",
            recorded.tier,
        )
    }

    // --- helpers ------------------------------------------------------------

    /** Write the file the Tier-3 command will be asked to delete. */
    private fun plantFile(): File {
        val root = File(context.filesDir, JARVIS_FILES_DIR)
        root.mkdirs()
        val file = File(root, TARGET_RELATIVE_PATH)
        file.writeText(FILE_CONTENTS)
        assertTrue("could not plant ${file.absolutePath}", file.exists())
        return file
    }

    /**
     * Send the command and return the consent prompt it raised.
     *
     * The activity monitor is registered BEFORE the frame goes out, which is not
     * optional: the prompt is raised from the WebSocket reader thread the moment
     * the dispatcher reaches the ASK branch, and on a fast machine that can
     * happen before an after-the-fact poll ever looks.
     */
    private fun sendAndAwaitPrompt(
        commandId: String,
        params: JSONObject,
        tier: Int,
        reason: String,
        why: String = "a Tier-3 command must raise the consent prompt",
    ): ApprovalActivity {
        val prompt = Activities.expect(
            ApprovalActivity::class.java,
            timeoutMs = Waits.NETWORK_TIMEOUT_MS,
        ) {
            server.sendDeviceCommand(
                commandId = commandId,
                action = TIER3_ACTION,
                params = params,
                tier = tier,
                reason = reason,
            )
        }
        Activities.awaitResumed(prompt)
        Waits.until("the consent prompt to render its buttons — $why") {
            findByText("DENY") != null
        }

        // The prompt renders over the keyguard on purpose, and while the
        // keyguard is up it hides the parameters and keeps APPROVE inert — that
        // is the security property, not an inconvenience. `JarvisTestRule`
        // already dismissed the keyguard and the activity asks the system to
        // dismiss it again on its own account, so this waits for the result
        // rather than assuming it. Without the wait, a slow keyguard would show
        // up as "the parameters were not on screen", which is the same symptom a
        // genuinely broken prompt produces.
        Waits.until(
            what = "the consent prompt to become readable (it is still showing " +
                "\"${ConsentGate.LOCKED_PARAMS}\", so the phone is locked)",
            timeoutMs = Waits.DEFAULT_TIMEOUT_MS,
            // Each retry shells out to `wm dismiss-keyguard`; polling that at
            // 50ms would spend the whole timeout forking processes.
            pollMs = UNLOCK_RETRY_MS,
        ) {
            if (Device.ui.findObject(By.text(ConsentGate.LOCKED_PARAMS)) == null) {
                true
            } else {
                Device.wakeAndUnlock()
                false
            }
        }
        return prompt
    }

    private fun tap(label: String) {
        Waits.untilPresent("a control labelled \"$label\" on the prompt") {
            findByText(label)
        }.click()
    }

    /** Scroll-aware: the prompt is a ScrollView and may be taller than the screen. */
    private fun findByText(text: String): UiObject2? =
        Views.findScrolling(By.text(Views.textIgnoringCase(text)))

    private fun assertOnScreen(text: String, why: String) {
        Waits.until("\"$text\" to appear on the consent prompt — $why") {
            Views.findScrolling(By.text(Views.containingIgnoringCase(text))) != null
        }
    }

    /**
     * No control on this prompt can remember the answer.
     *
     * Checked against CLICKABLE nodes rather than against all text, because the
     * prompt's own footer contains the word "remembered" — in the sentence
     * promising that the answer is not. The claim under test is that there is no
     * *affordance*, so the clickable set is the right thing to look at.
     */
    private fun assertNoRememberControl() {
        val offenders = Device.ui.findObjects(By.clickable(true))
            .mapNotNull { it.text }
            .filter { label ->
                REMEMBER_WORDS.any { label.contains(it, ignoreCase = true) }
            }
        assertTrue(
            "A Tier-3 prompt must offer no way to remember an answer, and " +
                "ApprovalBridge never returns approved_always. Found control(s): " +
                "$offenders",
            offenders.isEmpty(),
        )
    }

    private companion object {
        /**
         * Tier 3 CONFIRM in the local action table, available on any device, and
         * — the reason it was chosen — its effect is directly observable. A test
         * that could only inspect the wire would pass on an implementation that
         * reported `denied` and deleted the file anyway.
         */
        const val TIER3_ACTION = "delete_file"

        /** `PathScope.ROOT_DIR_NAME`; every file action is confined to it. */
        const val JARVIS_FILES_DIR = "jarvis_files"

        const val TARGET_RELATIVE_PATH = "consent-gate-probe.txt"
        const val FILE_CONTENTS = "this file proves DENY means nothing ran\n"

        /**
         * Distinctive enough that finding it on screen proves the prompt
         * rendered the SERVER's text rather than a default of its own.
         */
        const val SERVER_REASON =
            "a prompt-injected server claims you asked to delete the probe file"

        val REMEMBER_WORDS = listOf("always", "remember", "don't ask", "do not ask")

        /** How often to re-try dismissing the keyguard while waiting for it to go. */
        const val UNLOCK_RETRY_MS = 500L
    }
}
