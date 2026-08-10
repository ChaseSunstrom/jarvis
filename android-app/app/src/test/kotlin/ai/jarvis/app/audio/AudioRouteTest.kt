package ai.jarvis.app.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The earpiece routing rules. Mirrored by `tools/audio_route_test.py`; if you
 * change a rule here, change it there.
 */
class AudioRouteTest {

    // --- opt-in ------------------------------------------------------------

    @Test
    fun `a headset does nothing until the user turns headset mode on`() {
        for (kind in HeadsetKind.values()) {
            val route = AudioRoute(kind = kind, headsetModeEnabled = false)
            assertFalse(
                "$kind captured through the headset without the user opting in",
                route.capturesThroughHeadset
            )
            assertFalse("$kind offered warm-link without opt-in", route.warmLinkEligible)
        }
    }

    @Test
    fun `plugging in headphones never silently moves the microphone`() {
        // The output-only devices: Jarvis will play through them, but the mic
        // must stay on the phone because they have none.
        for (kind in listOf(HeadsetKind.WIRED_HEADPHONES, HeadsetKind.BLUETOOTH_A2DP)) {
            val route = AudioRoute(kind = kind, headsetModeEnabled = true)
            assertFalse("$kind claimed a microphone", route.capturesThroughHeadset)
            assertFalse(kind.hasMic)
            assertTrue("$kind should still be an output", kind.isExternalOutput)
        }
    }

    // --- the echo loop -----------------------------------------------------

    @Test
    fun `an ear-worn headset gets echo cancellation`() {
        for (kind in listOf(
            HeadsetKind.BLUETOOTH_SCO,
            HeadsetKind.BLE_HEADSET,
            HeadsetKind.WIRED_HEADSET,
            HeadsetKind.USB_HEADSET
        )) {
            val route = AudioRoute(kind = kind, headsetModeEnabled = true)
            assertTrue("$kind should have an echo loop", route.hasEchoLoop)
            val profile = CaptureProfile.forRoute(route)
            assertTrue(
                "$kind must capture through VOICE_COMMUNICATION or Jarvis hears itself",
                profile.useVoiceCommunication
            )
            assertTrue(profile.requestCommunicationDevice)
        }
    }

    @Test
    fun `the phone microphone keeps the accuracy-preserving source`() {
        val profile = CaptureProfile.forRoute(AudioRoute())
        assertFalse(
            "the phone mic has no echo loop, so it must not pay the AEC accuracy cost",
            profile.useVoiceCommunication
        )
        assertFalse(profile.requestCommunicationDevice)
    }

    @Test
    fun `headphones with the user opted in still use the raw source`() {
        // Output-only device: playback moves, capture does not, so there is no
        // shared device and no loop.
        val route = AudioRoute(kind = HeadsetKind.WIRED_HEADPHONES, headsetModeEnabled = true)
        val profile = CaptureProfile.forRoute(route)
        assertFalse(profile.useVoiceCommunication)
        assertFalse(profile.requestCommunicationDevice)
    }

    // --- the SCO link ------------------------------------------------------

    @Test
    fun `a bluetooth headset whose call profile is unavailable falls back to the phone`() {
        val route = AudioRoute(
            kind = HeadsetKind.BLUETOOTH_SCO,
            headsetModeEnabled = true,
            scoAvailable = false
        )
        assertFalse(
            "capturing over an unavailable SCO link returns silence",
            route.capturesThroughHeadset
        )
        assertFalse(route.warmLinkEligible)
        assertFalse(CaptureProfile.forRoute(route).useVoiceCommunication)
    }

    @Test
    fun `wired headsets do not depend on an SCO link`() {
        val route = AudioRoute(
            kind = HeadsetKind.WIRED_HEADSET,
            headsetModeEnabled = true,
            scoAvailable = false
        )
        assertTrue(
            "a cable has no SCO link to be unavailable",
            route.capturesThroughHeadset
        )
    }

    // --- warm link ---------------------------------------------------------

    @Test
    fun `warm-link is offered only where echo cancellation is active`() {
        for (kind in HeadsetKind.values()) {
            val route = AudioRoute(kind = kind, headsetModeEnabled = true)
            assertEquals(
                "warm-link without AEC is a feedback loop, not a feature ($kind)",
                route.hasEchoLoop,
                route.warmLinkEligible
            )
        }
    }

    // --- every branch is reachable and distinct ----------------------------

    @Test
    fun `each capture profile branch is reachable and explains itself`() {
        val phone = CaptureProfile.forRoute(AudioRoute())
        // SCO needs its link up, or this is still the phone's own microphone.
        val worn = CaptureProfile.forRoute(
            AudioRoute(
                kind = HeadsetKind.BLUETOOTH_SCO,
                headsetModeEnabled = true,
                scoAvailable = true,
            )
        )
        val wired = CaptureProfile.forRoute(
            AudioRoute(kind = HeadsetKind.WIRED_HEADSET, headsetModeEnabled = true)
        )

        val reasons = listOf(phone, worn, wired).map { it.reason }
        for (r in reasons) assertTrue("a reason should be human-readable", r.length > 20)

        // Two branches, not three. Every headset that can capture is ear-worn
        // (see the invariant below), so the two headsets share a profile and
        // the phone gets its own.
        assertEquals("phone and headset must differ", 2, reasons.toSet().size)
        assertEquals(worn.reason, wired.reason)
        assertFalse("the phone mic has no echo loop to cancel", phone.useVoiceCommunication)
        assertTrue("an ear-worn headset needs AEC", worn.useVoiceCommunication)
    }

    @Test
    fun `every headset that can capture is also ear-worn`() {
        // This is what lets `forRoute` have two branches instead of three. A
        // headset with a mic that is NOT in the ear — the cable-clip case
        // `isEarWorn` describes — would need the raw source kept rather than
        // AEC applied, and there is no longer a branch that does that.
        //
        // So if this fails, do not relax it: put the third branch back in
        // CaptureProfile.forRoute, where a comment is waiting.
        for (kind in HeadsetKind.entries) {
            if (!kind.hasMic) continue
            assertTrue(
                "$kind can capture but is not ear-worn; CaptureProfile.forRoute " +
                    "has no branch for that any more",
                kind.isEarWorn
            )
        }
    }
}
